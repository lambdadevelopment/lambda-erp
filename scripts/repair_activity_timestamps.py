"""Review/apply the known CRM duplicated-clock repair, without app startup.

Default: read-only JSON report. --apply REPORT applies exactly the reviewed rows
atomically, with compare-and-swap checks. No schema migration or automatic repair.
Run from the repository: python -m scripts.repair_activity_timestamps --help
"""
import argparse
import json
import os
from pathlib import Path
import sqlite3

from lambda_erp.timestamps import repair_duplicated_time, timestamp_epoch


def connect(database, *, writable=False):
    if database.startswith(('postgres://', 'postgresql://')):
        import psycopg
        conn = psycopg.connect(database)
        if not writable:
            conn.execute('SET TRANSACTION READ ONLY')
        return conn, '%s'
    uri = Path(database).expanduser().resolve().as_uri()
    conn = sqlite3.connect(uri + ('?mode=rw' if writable else '?mode=ro'), uri=True)
    conn.create_function('erp_timestamp_epoch', 1, timestamp_epoch, deterministic=True)
    return conn, '?'


def inspect(conn):
    repairs, unknown = [], []
    for name, old in conn.execute('SELECT name, occurred_at FROM "Activity" ORDER BY name'):
        proposed = repair_duplicated_time(old)
        if proposed:
            repairs.append({'name': name, 'old': old, 'new': proposed})
        elif timestamp_epoch(old) is None:
            unknown.append(name)
    return {'kind': 'activity-duplicated-clock-v1',
            'assumption': 'Original first clock reading was UTC; appended clock was save time.',
            'repairs': repairs, 'unrepairable_names': unknown}


def apply(conn, placeholder, report):
    if report.get('kind') != 'activity-duplicated-clock-v1':
        raise ValueError('Not an activity timestamp repair report')
    seen = set()
    try:
        for row in report['repairs']:
            if row['name'] in seen or not row['new'] or repair_duplicated_time(row['old']) != row['new']:
                raise ValueError('Duplicate or unsupported repair in report')
            seen.add(row['name'])
            cursor = conn.execute(
                f'UPDATE "Activity" SET occurred_at = {placeholder} '
                f'WHERE name = {placeholder} AND occurred_at = {placeholder}',
                (row['new'], row['name'], row['old']),
            )
            if cursor.rowcount != 1:
                raise ValueError(f"Record changed or missing: {row['name']}; entire repair rolled back")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return len(seen)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', default=os.environ.get('LAMBDA_ERP_DB'),
                        help='DB path/URL (defaults to LAMBDA_ERP_DB; use environment for credentials)')
    parser.add_argument('--apply', type=Path, metavar='REVIEWED_REPORT.json')
    args = parser.parse_args()
    if not args.database:
        parser.error('Set LAMBDA_ERP_DB or supply --database')
    conn, placeholder = connect(args.database, writable=bool(args.apply))
    try:
        if args.apply:
            count = apply(conn, placeholder, json.loads(args.apply.read_text()))
            print(json.dumps({'repaired': count}))
        else:
            print(json.dumps(inspect(conn), indent=2, ensure_ascii=False))
    finally:
        conn.close()


if __name__ == '__main__':
    main()
