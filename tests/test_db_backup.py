"""Offline tests for db_backup.py: synthetic rows, no network, no database writes."""
import io
import json
import unittest
from urllib.parse import urlsplit

from db_backup import BackupError, RestReader, verify_archive, write_archive, PAGE


class FakeResponse:
    def __init__(self, body, status=200, headers=None):
        self._body, self.status_code, self.headers = body, status, headers or {}

    def json(self):
        return self._body


class FakeSession:
    """Serves an OpenAPI document and paged rows; records every call."""

    def __init__(self, tables, mutate_after=None):
        self.tables, self.calls, self.mutate_after = tables, [], mutate_after

    def get(self, url, params=None, headers=None, **kwargs):
        self.calls.append(('GET', url, dict(params or {})))
        path = urlsplit(url).path.rsplit('/rest/v1', 1)[1]
        if path == '/':
            return FakeResponse({'definitions': {
                name: {'properties': {col: {'description': 'Note:\nThis is a Primary Key.<pk/>' if col == pk else ''}
                                      for col in ('id', 'email', 'value') if col in (pk, 'value')}}
                for name, (pk, _) in self.tables.items()}})
        table = path.strip('/')
        pk, rows = self.tables[table]
        if params.get('limit') == '0':
            if self.mutate_after == table and any(c[2].get('offset') for c in self.calls if table in c[1]):
                rows = rows + [{pk: 'late', 'value': 0}]
            return FakeResponse([], headers={'Content-Range': f'*/{len(rows)}'})
        offset, limit = int(params['offset']), int(params['limit'])
        return FakeResponse(sorted(rows, key=lambda r: str(r[pk]))[offset:offset + limit])

    def post(self, *a, **k):
        raise AssertionError('backup must never write')
    put = patch = delete = post


class DbBackupTests(unittest.TestCase):
    def reader(self, session):
        return RestReader('https://example.test', 'test-key-not-real', session)

    def test_roundtrip_reads_every_row_with_get_only(self):
        tables = {
            'wardy_events': ('id', [{'id': f'{i:05d}', 'value': i} for i in range(PAGE * 2 + 3)]),
            'accounts': ('email', [{'email': 'a@example.test', 'value': 1}]),
            'portal_auth_sessions': ('id', [{'id': 's', 'value': 1}]),
            'empty': ('id', []),
        }
        session, out = FakeSession(tables), io.BytesIO()
        summary = write_archive(self.reader(session), out)
        self.assertEqual(summary['wardy_events']['rows'], PAGE * 2 + 3)
        self.assertNotIn('portal_auth_sessions', summary)
        self.assertEqual(summary['empty']['rows'], 0)
        self.assertTrue(all(method == 'GET' for method, *_ in session.calls))
        self.assertTrue(any(c[2].get('order') == 'email.asc' for c in session.calls))
        out.seek(0)
        self.assertEqual(verify_archive(out), {'accounts': 1, 'empty': 0, 'wardy_events': PAGE * 2 + 3})

    def test_table_changing_mid_backup_fails(self):
        rows = [{'id': f'{i:05d}', 'value': i} for i in range(PAGE + 1)]
        session = FakeSession({'t': ('id', rows)}, mutate_after='t')
        with self.assertRaises(BackupError):
            write_archive(self.reader(session), io.BytesIO())

    def test_tampered_archive_fails_verification(self):
        out = io.BytesIO()
        write_archive(self.reader(FakeSession({'t': ('id', [{'id': '1', 'value': 1}])})), out)
        data = out.getvalue().replace(b'"value": 1', b'"value": 2')
        with self.assertRaises(BackupError):
            verify_archive(io.BytesIO(data))

    def test_http_only_and_http_errors_are_rejected(self):
        with self.assertRaises(BackupError):
            RestReader('http://example.test', 'k', FakeSession({}))

        class Failing(FakeSession):
            def get(self, *a, **k):
                return FakeResponse({}, status=401)
        with self.assertRaises(BackupError):
            write_archive(self.reader(Failing({})), io.BytesIO())


if __name__ == '__main__':
    unittest.main()
