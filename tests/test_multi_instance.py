#!/usr/bin/env python3
"""Several app processes on one database: numbering, stale edits, chat turns,
startup migrations.

Each check that matters across processes runs in genuinely separate Python
processes against the same database (a temp SQLite file, or Postgres when
LAMBDA_ERP_TEST_DB is set), because a per-process counter or lock looks fine
from inside one process.

Run:  python -m tests.test_multi_instance
      LAMBDA_ERP_TEST_DB=postgresql://... python -m tests.test_multi_instance
"""
import os
import subprocess
import sys
import tempfile
import unittest

from tests import test_document_requirements as fixtures
from api import services
from lambda_erp.database import setup
from lambda_erp.exceptions import DocumentStatusError
from lambda_erp.utils import new_name

_WORKER = r"""
import json, sys
from lambda_erp.database import setup
from lambda_erp.utils import new_name
from api.routers.masters import _generate_master_name
db = setup(sys.argv[1])
names = []
for i in range(int(sys.argv[2])):
    # Alternate the document path and the master path on one shared prefix,
    # the way Lead is both a document and a master.
    names.append(new_name("RACE") if i % 2 else _generate_master_name(db, "Customer", "RACE", 4))
print(json.dumps(names))
"""


def _shared_db_url(tmp):
    """A database separate processes can share; reset to empty."""
    url = os.environ.get("LAMBDA_ERP_TEST_DB")
    if not url:
        return os.path.join(tmp, "shared.db")
    import psycopg
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
    return url


def _run_workers(script, args_list):
    env = {**os.environ, "PYTHONPATH": os.getcwd()}
    procs = [subprocess.Popen([sys.executable, "-c", script, *args], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
             for args in args_list]
    results = []
    for proc in procs:
        out, err = proc.communicate(timeout=300)
        if proc.returncode != 0:
            raise AssertionError(f"worker failed:\n{err[-2000:]}")
        results.append(out)
    return results


class SeparateProcesses(unittest.TestCase):
    def test_concurrent_processes_never_share_a_number(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            url = _shared_db_url(tmp)
            setup(url).close()  # schema exists before the workers race
            outputs = _run_workers(_WORKER, [(url, "40")] * 4)
            names = [name for out in outputs for name in json.loads(out)]
            numbers = [int(name.rsplit("-", 1)[1]) for name in names]
            self.assertEqual(len(numbers), 160)
            self.assertEqual(len(set(numbers)), 160, "two processes handed out the same number")

    def test_processes_booting_together_apply_migrations_once(self):
        boot = "import sys\nfrom lambda_erp.database import setup\nsetup(sys.argv[1]).close()\n"
        with tempfile.TemporaryDirectory() as tmp:
            url = _shared_db_url(tmp)
            if not url.startswith("postgres"):
                self.skipTest("SQLite is single-process; the startup lock is Postgres-only")
            _run_workers(boot, [(url,)] * 4)  # each would raise on a failed required migration
            db = setup(url)
            versions = [r["version"] for r in db.sql('SELECT version FROM "_SchemaMigrations"')]
            self.assertEqual(len(versions), len(set(versions)))
            self.assertEqual(max(versions), db.MIGRATIONS[-1][0])
            db.close()


class OneDatabase(unittest.TestCase):
    setUp = fixtures.RequirementsTest.setUp
    tearDown = fixtures.RequirementsTest.tearDown

    quotation = {"company": "Test Co", "customer": "C",
                 "items": [{"item_code": "S", "qty": 1, "rate": 10}]}

    def test_series_is_seeded_from_existing_names_only(self):
        self.db.insert("Customer", {"name": "SEED-0041", "customer_name": "Old"})
        # Not "SEED-<digits>": must not move the series.
        self.db.insert("Customer", {"name": "SEED-IMP-0999", "customer_name": "Import"})
        self.db.insert("Customer", {"name": "SEED-ITEM-0777", "customer_name": "Child-like"})
        self.assertEqual(new_name("SEED"), "SEED-0042")
        self.assertEqual(new_name("SEED"), "SEED-0043")

    def test_document_skips_a_name_created_explicitly_ahead_of_the_series(self):
        first = services.create_document("quotation", self.quotation)["name"]
        number = int(first.rsplit("-", 1)[1])
        taken = f"QTN-{number + 1:04d}"
        services.create_document("quotation", {**self.quotation, "name": taken})
        following = services.create_document("quotation", self.quotation)["name"]
        self.assertEqual(following, f"QTN-{number + 2:04d}")

    def test_master_and_document_paths_share_one_series(self):
        from api.routers.masters import _generate_master_name
        # Five digits: the padding no longer keeps the two paths apart.
        self.db.insert("Customer", {"name": "DUAL-10000", "customer_name": "Seed"})
        from_master = _generate_master_name(self.db, "Customer", "DUAL", 3)
        from_document = new_name("DUAL")
        self.assertEqual((from_master, from_document), ("DUAL-10001", "DUAL-10002"))

    def test_stale_copy_cannot_overwrite_a_newer_save(self):
        doc = services.create_document("quotation", self.quotation)
        loaded = services.load_document("quotation", doc["name"])
        # The client's own round trip works.
        saved = services.update_document("quotation", doc["name"],
                                         {**loaded, "remarks": "first", "_validation": None})
        # A second client still holding the first copy is refused...
        with self.assertRaisesRegex(DocumentStatusError, "changed since it was loaded"):
            services.update_document("quotation", doc["name"], {**loaded, "remarks": "stale"})
        self.assertEqual(services.load_document("quotation", doc["name"])["remarks"], "first")
        # ...and works again from the fresh copy; omitting `modified` keeps
        # last-write-wins for callers that never send it.
        services.update_document("quotation", doc["name"], {**saved, "remarks": "second"})
        services.update_document("quotation", doc["name"], {"remarks": "third"})
        self.assertEqual(services.load_document("quotation", doc["name"])["remarks"], "third")

    def test_one_chat_turn_per_session(self):
        from api.chat import (claim_session_turn, create_session, release_session_turn,
                              session_turn_running)
        sid = create_session(user_id="U")["id"]
        token = claim_session_turn(sid)
        self.assertTrue(token)
        self.assertIsNone(claim_session_turn(sid), "a second turn must wait")
        self.assertTrue(session_turn_running(sid))
        release_session_turn(sid, "not-the-owner")
        self.assertTrue(session_turn_running(sid), "only the owner releases")
        release_session_turn(sid, token)
        self.assertFalse(session_turn_running(sid))
        # A claim left behind by a dead process expires.
        self.db.sql('UPDATE "Chat Session" SET active_turn = ?, active_turn_until = ? WHERE id = ?',
                    ["dead", "2000-01-01T00:00:00+00:00", sid])
        self.db.conn.commit()
        self.assertFalse(session_turn_running(sid))
        self.assertTrue(claim_session_turn(sid))


if __name__ == "__main__":
    result = unittest.main(exit=False, verbosity=2).result
    sys.exit(0 if result.wasSuccessful() else 1)
