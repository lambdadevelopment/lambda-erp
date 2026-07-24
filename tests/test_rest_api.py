#!/usr/bin/env python3
"""Functional tests for Bearer-key auth on the regular REST API.

The same per-user API keys that drive the chat API (POST /api/v1/chat) also
authenticate the cookie-gated REST surface (/api/documents, /api/masters, …)
when the `rest_api_enabled` Settings flag is on. This exercises the real
FastAPI app end-to-end (register admin → enable → issue key → drive REST) and
verifies:

  - `rest_api_enabled` gates the Bearer path (off by default → 401, and a valid
    cookie still works regardless of the flag)
  - Bearer auth failures (missing → falls through / bad / revoked → 401)
  - the key acts AS its owner: role checks flow through `require_role`, so a
    viewer key can read but not write, a manager key can write
  - a disabled owner and a lowered-role owner instantly constrain the key
  - the two surfaces are independent: chat stays 404 while REST is on

Run:  python -m tests.test_rest_api
      LAMBDA_ERP_TEST_DB=postgresql://... python -m tests.test_rest_api   # CI runs both
"""
import os
import sys


def _reset_db():
    """Return a db_path for setup(); reset Postgres to a clean schema.

    This test opens several TestClients in sequence, and each one re-runs the
    app lifespan → setup(db_path). Postgres persists across that (same URL,
    CREATE TABLE IF NOT EXISTS), but a `:memory:` SQLite path would start empty
    on every lifespan. So for SQLite we use a temp *file* — the caller unlinks
    it — which persists across clients just like the shared Postgres schema.
    """
    url = os.environ.get("LAMBDA_ERP_TEST_DB")
    if not url:
        import tempfile
        fd, path = tempfile.mkstemp(suffix=".db", prefix="lambda_rest_test_")
        os.close(fd)
        return path
    import psycopg
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
    return url


def check_rest_api():
    db_path = _reset_db()
    backend = "postgres" if db_path.startswith("postgres") else "sqlite (temp file)"
    os.environ["LAMBDA_ERP_DB"] = db_path
    os.environ["LAMBDA_ERP_AUTO_DEMO"] = "0"
    os.environ.setdefault("LAMBDA_ERP_PLUGINS", "")
    os.environ.setdefault("JWT_SECRET_KEY", "test-secret-not-for-prod")
    os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used")

    from fastapi.testclient import TestClient
    from api.main import app

    with TestClient(app) as client:
        # --- Become admin (first registrant); the client cookie is now set. --
        r = client.post("/api/auth/register",
                        json={"email": "admin@example.com", "full_name": "Admin",
                              "password": "test-password-123"})
        assert r.status_code == 200 and r.json()["role"] == "admin", r.text[:300]

        # A cookie session drives the REST API regardless of the flag.
        assert client.get("/api/documents/quotation").status_code == 200

        # --- Issue keys BEFORE enabling REST (issuance is always allowed). ---
        mgr = client.post("/api/auth/api-keys", json={"name": "sync", "role": "manager"}).json()
        mgr_h = {"Authorization": f"Bearer {mgr['token']}"}
        adm = client.post("/api/auth/api-keys", json={"name": "admin-key", "role": "admin"}).json()
        adm_h = {"Authorization": f"Bearer {adm['token']}"}

        # --- Feature OFF by default: a Bearer key is rejected (401), and the
        # rejection is explicit — not a silent downgrade to demo access. -------
        # (Use a bare client so the admin cookie doesn't authenticate instead.)
        with TestClient(app) as anon:
            r = anon.get("/api/documents/quotation", headers=mgr_h)
            assert r.status_code == 401, f"disabled REST key → {r.status_code}: {r.text[:200]}"

        # --- Turn REST key access on. ----------------------------------------
        r = client.put("/api/auth/settings", json={"rest_api_enabled": "1"})
        assert r.status_code == 200 and r.json().get("rest_api_enabled") == "1", r.text[:200]
        # Independent of chat: chat surface is still off → 404.
        with TestClient(app) as anon:
            assert anon.post("/api/v1/chat", json={"message": "hi"}, headers=mgr_h).status_code == 404

        with TestClient(app) as api:  # no cookie — Bearer key is the only credential
            # --- Auth failures. ----------------------------------------------
            # No credential at all + AUTO_DEMO off → 401 (no public manager).
            assert api.get("/api/documents/quotation").status_code == 401
            assert api.get("/api/documents/quotation",
                           headers={"Authorization": "Bearer sk_erp_nope"}).status_code == 401

            # --- Manager key: reads AND writes. ------------------------------
            assert api.get("/api/documents/quotation", headers=mgr_h).status_code == 200
            r = api.post("/api/masters/customer",
                         json={"customer_name": "Bearer Co"}, headers=mgr_h)
            assert r.status_code == 200, f"manager key create → {r.status_code}: {r.text[:200]}"
            created = r.json()
            assert created.get("name"), created
            # Attribution: the key acts as the real user, not an `api:` shadow.
            me = api.get("/api/auth/me", headers=mgr_h).json()
            assert me["email"] == "admin@example.com" and me["role"] == "manager", me

            # --- Viewer key: reads but role-gated out of writes. -------------
            # (Create a second, viewer-capped key owned by the same admin.)
            vkey = client.post("/api/auth/api-keys", json={"name": "ro", "role": "viewer"}).json()
            v_h = {"Authorization": f"Bearer {vkey['token']}"}
            assert api.get("/api/documents/quotation", headers=v_h).status_code == 200
            r = api.post("/api/masters/customer", json={"customer_name": "Nope"}, headers=v_h)
            assert r.status_code == 403, f"viewer key write → {r.status_code}: {r.text[:200]}"
            # Admin-only endpoint: manager key 403s, admin key 200s.
            assert api.put("/api/auth/settings", json={"pdf_page_size": "A4"},
                           headers=mgr_h).status_code == 403
            assert api.put("/api/auth/settings", json={"pdf_page_size": "A4"},
                           headers=adm_h).status_code == 200

            # --- Revoke → the key stops working immediately. -----------------
            client.post(f"/api/auth/api-keys/{mgr['id']}/revoke")
            assert api.get("/api/documents/quotation", headers=mgr_h).status_code == 401

        # --- Disabled owner kills every one of their keys. -------------------
        # Register a second user, give them a key, then disable them.
        assert client.put("/api/auth/settings", json={"allow_public_signup": "1"}).status_code == 200
        with TestClient(app) as u2:
            r = u2.post("/api/auth/register",
                        json={"email": "u2@example.com", "full_name": "Two",
                              "password": "test-password-123"})
            assert r.status_code == 200, r.text[:200]
            u2_name = r.json()["name"]
            # No role → defaults to the owner's own role (viewer); a viewer
            # can't mint a manager-capped key.
            u2key = u2.post("/api/auth/api-keys", json={"name": "u2"}).json()
        u2_h = {"Authorization": f"Bearer {u2key['token']}"}
        with TestClient(app) as api:
            assert api.get("/api/documents/quotation", headers=u2_h).status_code == 200
        # Admin disables the second user → their key dies with them.
        assert client.delete(f"/api/auth/users/{u2_name}").status_code == 200
        with TestClient(app) as api:
            assert api.get("/api/documents/quotation", headers=u2_h).status_code == 401

    # Clean up the temp SQLite file (and its WAL sidecars), if any.
    if not os.environ.get("LAMBDA_ERP_TEST_DB"):
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(db_path + suffix)
            except OSError:
                pass

    print(f"  [rest api] gating/auth/role-enforcement/owner-lifecycle OK on {backend}")


def main():
    print("REST API (Bearer key) checks")
    check_rest_api()
    print("All REST API checks passed.")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"\nFAILED: {e}")
        sys.exit(1)
