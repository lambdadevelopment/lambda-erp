"""Exact recent-change windows through services, REST, chat and MCP.

Run as a module; LAMBDA_ERP_TEST_DB optionally selects a disposable PostgreSQL DB.
No external model calls or live data. The test database's public schema is reset.
"""
import json
import os
from datetime import datetime, timezone
from unittest.mock import patch


def check_time_filters():
    from tests.test_master_registry import _reset_db
    path = _reset_db()
    os.environ.update(LAMBDA_ERP_DB=path, LAMBDA_ERP_AUTO_DEMO='0',
                      LAMBDA_ERP_PLUGINS='', OPENAI_API_KEY='test-unused',
                      JWT_SECRET_KEY='test-time-filters')
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from lambda_erp.database import setup, Database
    from lambda_erp.model import Document
    from lambda_erp.exceptions import ValidationError
    from lambda_erp.timestamps import timestamp_epoch, normalize_timestamp
    from api import services, chat
    from api.pdf_profiles import DisabledPDF
    from api.routers import documents, masters, mcp
    from api.time_filters import resolve_time_filter
    from scripts.repair_activity_timestamps import inspect, apply

    with patch.object(Database, 'MIGRATIONS', Database.MIGRATIONS[:30]):
        old_db = setup(path)
        old_db.insert('Customer', {'name': 'pre-migration', 'customer_name': 'Historical'})
        assert 'creation' not in old_db._get_table_columns('Customer')
    db = setup(path)
    assert db.get_value('Customer', 'pre-migration', 'creation') is None
    assert db.get_value('Customer', 'pre-migration', 'modified') is None
    assert db.sql('SELECT version FROM "_SchemaMigrations" WHERE version = 31')
    db.delete('Customer', 'pre-migration')
    db.conn.execute('''CREATE TABLE "Activity" (
        name TEXT PRIMARY KEY, type TEXT, notes TEXT, occurred_at TEXT,
        creation TEXT, modified TEXT, docstatus INTEGER DEFAULT 0,
        discarded INTEGER DEFAULT 0)''')
    db.conn.commit()

    class Activity(Document):
        DOCTYPE = 'Activity'
        PREFIX = 'ACT'
        CHILD_TABLES = {}
        def validate(self):
            # Reproduce the existing deployment controller's space separator bug.
            if 'T' not in str(self.occurred_at):
                self.occurred_at = str(self.occurred_at) + 'T10:05:17.879639+00:00'

    services.register_doctype('Activity', Activity, pdf_profile=DisabledPDF('Test fixture'))
    db._ensure_list_indexes()
    window = {'field': 'occurred_at', 'since': '2026-09-14T17:51:00Z', 'until': '2026-09-15T17:51:00Z'}
    malformed = '2026-09-15 10:05:15T10:05:17.879639+00:00'
    values = [
        ('before', '2026-09-14 17:50:59'),
        ('lower', '2026-09-14 17:51:00'),
        ('same-a', '2026-09-15T10:00:00Z'),
        ('same-b', '2026-09-15T12:00:00+02:00'),
        ('upper', '2026-09-15T17:51:00Z'),
        ('invalid', malformed), ('missing', None),
    ]
    for name, stamp in values:
        db.insert('Activity', {'name': name, 'type': 'note', 'notes': 'Scheduled outreach',
                              'occurred_at': stamp, 'creation': stamp, 'modified': stamp})
    db.insert('Activity', {'name': 'discarded', 'type': 'note', 'occurred_at': malformed, 'discarded': 1})
    page = services.list_document_page('activity', time_filter=window, limit=2, fields=['name'])
    assert [r['name'] for r in page['rows']] == ['same-b', 'same-a'], page
    assert page['total'] == 3 and page['has_more'] and page['next_offset'] == 2
    assert page['coverage'] == {'complete': False, 'unknown_time_rows': 2}
    assert page['warnings'] and set(page['rows'][0]) == {'name'}
    if db.dialect == 'sqlite':
        plan = db.sql('EXPLAIN QUERY PLAN SELECT name FROM "Activity" WHERE erp_timestamp_epoch(occurred_at) >= ? AND erp_timestamp_epoch(occurred_at) < ?', [0, 1])
        assert any('ix_time_' in row['detail'] for row in plan), plan
    else:
        assert db.sql("SELECT indexdef FROM pg_indexes WHERE tablename = 'Activity' AND indexdef LIKE '%erp_timestamp_epoch(occurred_at)%'")
    second = services.list_document_page('activity', time_filter=page['time_filter'], offset=2, limit=2)
    assert [r['name'] for r in second['rows']] == ['lower'] and not second['has_more']
    assert services.adjacent_documents('activity', 'same-a', time_filter=window) == {'prev': 'same-b', 'next': 'lower'}
    assert services.adjacent_documents('activity', 'before', time_filter=window) == {'prev': None, 'next': None}
    assert services.count_documents('activity', {'type': ['in', ['call', 'email', 'note']]}, time_filter=window) == 3
    assert services.count_documents('activity', {'type': ['in', []]}, time_filter=window) == 0
    assert services.count_documents('activity', {'type': ['not in', ['note']]}, time_filter=window) == 0
    fixed = resolve_time_filter(db, 'Activity', {'field': 'occurred_at', 'last_hours': 24},
                                clock=datetime(2026, 9, 15, 17, 51, tzinfo=timezone.utc))
    assert fixed['since'] == '2026-09-14T17:51:00+00:00'
    for value in [None, '', malformed, '2026-02-30T00:00:00Z', '2026-09-15T24:00:00Z',
                  '2026-09-15T10:00:60Z', '2026-09-15T10:00:00+02:99', '2026-09-15',
                  '2026-09-15T10:00:00+24:00', '2026-09-15T10:00:00+16:00']:
        actual = db.sql('SELECT erp_timestamp_epoch(?) AS value', [value])[0]['value']
        assert actual is None and timestamp_epoch(value) is None, (value, actual)
    for value in [v for _, v in values[:5]] + ['2026-09-15T10:00:00.123456-05:00']:
        actual = db.sql('SELECT erp_timestamp_epoch(?) AS value', [value])[0]['value']
        assert abs(actual - timestamp_epoch(value)) < 0.000001, (value, actual)

    invalid = [dict(window, field='missing'), dict(window, field='name'),
               dict(window, last_hours=24), dict(window, since='2026-09-14 00:00:00'),
               dict(window, until=window['since']), {'field': 'occurred_at'},
               dict(window, surprise=True)]
    invalid += [{'field': 'occurred_at', 'last_hours': h} for h in [0, -1, True, '24', float('inf'), 9000]]
    for bad in invalid:
        assert 'error' in chat._handle_list_documents({'doctype': 'activity', 'time_filter': bad}), bad
    assert 'error' in chat._handle_list_documents({'doctype': 'activity', 'time_filter': window, 'order': 'DESC; SELECT 1'})
    assert 'error' in chat._handle_list_documents({'doctype': 'activity', 'time_filter': window, 'filters': {'from_date': '2026-09-14'}})
    assert isinstance(chat._handle_list_documents({'doctype': 'activity'}), list)
    assert chat._handle_list_documents({'doctype': 'activity', 'time_filter': window})['total'] == 3
    assert mcp._call('list_documents', {'doctype': 'activity', 'time_filter': window}, {'role': 'viewer'})['total'] == 3
    schemas = {t['name']: t for t in mcp._tools('viewer')}
    assert 'time_filter' in schemas['list_documents']['inputSchema']['properties']
    assert 'time_filter' in schemas['search_masters']['inputSchema']['properties']

    # New writes must survive the deployment controller without a double clock.
    saved = Activity({'name': 'new-valid', 'occurred_at': '2026-09-15 10:05:15'}).save()
    assert saved.occurred_at == normalize_timestamp('2026-09-15T10:05:15Z')
    try:
        Activity({'name': 'new-invalid', 'occurred_at': malformed}).save()
        raise AssertionError('Malformed new timestamp saved')
    except ValidationError:
        pass
    with patch.object(Activity, 'validate', lambda self: setattr(self, 'occurred_at', malformed)):
        try:
            Activity({'name': 'bad-hook', 'occurred_at': '2026-09-15 10:05:15'}).save()
            raise AssertionError('Malformed hook timestamp saved')
        except ValidationError:
            pass
    assert not db.exists('Activity', 'bad-hook')

    # Master timestamps: migration must not manufacture historical events.
    for name, stamp in values[:5]:
        db.insert('Customer', {'name': name, 'customer_name': name, 'creation': stamp, 'modified': stamp})
    db.conn.execute('INSERT INTO "Customer" (name, customer_name) VALUES (?, ?)', ['old-unknown', 'Old'])
    db.conn.commit()
    master_window = dict(window, field='modified')
    mp = services.list_master_records('customer', time_filter=master_window, fields=['name'], limit=2, order='desc')
    assert [r['name'] for r in mp['rows']] == ['same-b', 'same-a'] and mp['total'] == 3
    assert mp['coverage']['unknown_time_rows'] == 1
    db.set_value('Customer', 'old-unknown', 'customer_name', 'Changed')
    assert db.get_value('Customer', 'old-unknown', 'creation') is None
    assert timestamp_epoch(db.get_value('Customer', 'old-unknown', 'modified')) is not None
    db.insert_many('Customer', [{'name': 'bulk', 'customer_name': 'Bulk'}])
    assert timestamp_epoch(db.get_value('Customer', 'bulk', 'creation')) is not None
    assert isinstance(chat._handle_search_masters({'master_type': 'customer'}), list)
    args = {'master_type': 'customer', 'time_filter': master_window, 'limit': 2, 'order': 'desc'}
    assert chat._handle_search_masters(args)['total'] == 3
    assert mcp._call('search_masters', args, {'role': 'viewer'})['total'] == 3
    assert 'error' in chat._handle_search_masters(dict(args, limit='bad'))

    # Real REST routers and authenticated dependency boundary, without app bootstrap.
    app = FastAPI()
    app.include_router(documents.router, prefix='/api')
    app.include_router(masters.router, prefix='/api')
    app.dependency_overrides[documents._viewer.dependency] = lambda: {'role': 'viewer'}
    app.dependency_overrides[masters._viewer.dependency] = lambda: {'role': 'viewer'}
    with TestClient(app) as client:
        response = client.get('/api/documents/activity', params={'time_filter': json.dumps(window), 'limit': 2})
        assert response.status_code == 200, response.text
        assert response.json()['total'] == 4  # includes normalized new-valid
        for bad in ['null', '[]', 'bad', '{}']:
            assert client.get('/api/documents/activity', params={'time_filter': bad}).status_code == 400
        response = client.get('/api/masters/customer', params={'time_filter': json.dumps(master_window), 'order': 'desc', 'limit': 2})
        assert response.status_code == 200 and response.json()['total'] == 3, response.text
        response = client.get('/api/masters/customer/same-a/adjacent', params={'time_filter': json.dumps(master_window), 'order': 'desc'})
        assert response.status_code == 200 and response.json() == {'prev': 'same-b', 'next': 'lower'}, response.text

    from tests.test_tool_permissions import _run
    result, _ = _run([('list_documents', {'doctype': 'activity', 'time_filter': window})], 'viewer')
    assert result[0]['total'] == 4
    assert 'Recent changes and outreach' in chat.build_system_prompt()

    # Repair: preview changes nothing; stale reports roll the entire batch back.
    raw = db.conn._raw if hasattr(db.conn, '_raw') else db.conn
    report = inspect(raw)
    assert db.get_value('Activity', 'invalid', 'occurred_at') == malformed
    assert len(report['repairs']) == 2
    stale = json.loads(json.dumps(report))
    stale['repairs'][-1]['name'] = 'missing-record'
    try:
        apply(raw, '%s' if db.dialect == 'postgres' else '?', stale)
        raise AssertionError('Stale repair accepted')
    except ValueError:
        pass
    assert db.get_value('Activity', 'invalid', 'occurred_at') == malformed
    before = db.get_value('Activity', 'invalid', 'modified')
    assert apply(raw, '%s' if db.dialect == 'postgres' else '?', report) == 2
    assert db.get_value('Activity', 'invalid', 'modified') == before
    assert timestamp_epoch(db.get_value('Activity', 'invalid', 'occurred_at')) is not None
    print(f'PASS exact windows, coverage, timestamps, REST/chat/MCP, repair ({db.dialect})')


if __name__ == '__main__':
    check_time_filters()
