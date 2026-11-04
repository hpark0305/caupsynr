"""Offline security regression tests: synthetic accounts/data, no production env."""
import io
import json
import os
import re
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path
from html.parser import HTMLParser
from unittest.mock import patch, MagicMock
from urllib.parse import parse_qs, urlsplit
from contextlib import contextmanager

IMPORT_DIR = tempfile.TemporaryDirectory(prefix='cau-security-import-')
with patch.dict(os.environ, {
    'APP_API_KEY': 'test-api-key-not-for-deployment',
    'MINDMATE_PEPPER': 'test-pepper-not-for-deployment',
    'SECRET_KEY': 'test-session-secret-not-for-deployment-12345',
    'SUPABASE_URL': '', 'SUPABASE_KEY': '',
    'PORTAL_DB_PATH': str(Path(IMPORT_DIR.name) / 'portal.db'),
    'DATA_DIR': str(Path(IMPORT_DIR.name) / 'files'),
    'SEED_ADMIN_EMAIL': '', 'SEED_ADMIN_PASSWORD': '',
    'RATELIMIT_STORAGE_URI': 'memory://',
}), patch('dotenv.load_dotenv'), patch('requests.sessions.Session.request', side_effect=AssertionError('Network forbidden')):
    import app as portal

from security_utils import SafeCSVWriter, validate_archive, validate_image, neutralize_excel_formulas, validate_production_settings, password_policy_error


class SecurityTests(unittest.TestCase):
    def test_production_never_uses_local_account_fallback(self):
        for missing in ('SUPABASE_URL', 'SUPABASE_KEY'):
            env = {'RENDER': 'true', 'SUPABASE_URL': 'https://example.test', 'SUPABASE_KEY': 'test'}
            env.pop(missing)
            with self.assertRaises(SystemExit):
                validate_production_settings(env)

    def test_production_cannot_disable_secure_cookie(self):
        with self.assertRaises(SystemExit):
            validate_production_settings({'RENDER': 'true', 'SUPABASE_URL': 'https://example.test',
                                          'SUPABASE_KEY': 'test', 'SESSION_COOKIE_SECURE': 'false'})
        validate_production_settings({'RENDER': 'true', 'SUPABASE_URL': 'https://example.test',
                                      'SUPABASE_KEY': 'test'})
        validate_production_settings({'SESSION_COOKIE_SECURE': 'false'})

    @classmethod
    def setUpClass(cls):
        cls.password_hash = portal.generate_password_hash('A-long-test-password!')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cau-security-test-')
        self.addCleanup(self.temp.cleanup)
        portal.DB_PATH = str(Path(self.temp.name) / 'test.db')
        portal.DATA_DIR = portal.PORTAL_FILES_DIR = str(Path(self.temp.name) / 'files')
        portal.GALLERY_FOLDER = str(Path(self.temp.name) / 'gallery')
        portal.GALLERY_DATA_FILE = str(Path(self.temp.name) / 'gallery.json')
        portal.ADMIN_EMAILS = {'admin@example.test'}
        portal.app.config.update(TESTING=True, WTF_CSRF_ENABLED=True)
        portal.limiter.enabled = False
        with patch.dict(os.environ, {'SEED_ADMIN_EMAIL': '', 'SEED_ADMIN_PASSWORD': ''}):
            portal.init_db()
        self.network = patch('requests.sessions.Session.request', side_effect=AssertionError('Network forbidden'))
        self.network.start()
        self.addCleanup(self.network.stop)
        for email in ('owner@example.test', 'other@example.test', 'viewer@example.test', 'editor@example.test', 'admin@example.test'):
            portal._set_account(email, self.password_hash, already_hashed=True)
        for pid, email in [('own', 'owner@example.test'), ('other', 'other@example.test')]:
            portal.sb('POST', 'projects', data={'id': pid, 'name': pid, 'researcher_email': email})
            portal.sb('POST', 'project_participants', data={'id': pid+'-p', 'project_id': pid, 'code': pid+'-code'})
            portal.sb('POST', 'measurements', data={'id': pid+'-m', 'project_id': pid, 'participant_id': pid+'-p', 'data': {}})
            portal.sb('POST', 'project_variables', data={'id': pid+'-v', 'project_id': pid, 'name': 'score'})
            portal.sb('POST', 'tasks', data={'id': pid+'-t', 'project_id': pid, 'title': pid+'-task', 'created_by': email})
            portal.sb('POST', 'wardy_events', data={'id': pid+'-w', 'project_id': pid, 'user_name': pid+'-user', 'state': 'open'})
        for role in ('viewer', 'editor'):
            portal.sb('POST', 'project_collaborators', data={'project_id': 'own', 'researcher_email': role+'@example.test', 'role': role})
        self.client = portal.app.test_client()
        self.sign_in()

    def sign_in(self, email='owner@example.test'):
        sid = str(portal._uuid.uuid4())
        now = int(portal.time.time())
        portal.sb('POST', 'portal_auth_sessions', data={'id': sid, 'researcher_email': email, 'created_at': now, 'last_seen': now})
        with self.client.session_transaction() as sess:
            sess.clear()
            sess['researcher'] = email
            sess['auth_stamp'] = portal._auth_stamp(portal._get_account(email))
            sess['auth_session_id'] = sid

    @contextmanager
    def remote_files(self):
        objects = {}
        store = MagicMock()
        store.put.side_effect = lambda key, value, **kw: objects.__setitem__(key, value)
        store.get.side_effect = objects.get
        store.list.side_effect = lambda prefix: [{'id': key, 'name': key[len(prefix)+1:]} for key in objects if key.startswith(prefix + '/')]
        store.delete.side_effect = lambda key: objects.pop(key, None)
        local_sb = portal.sb
        def db(*args, **kwargs):
            with patch.object(portal, '_USE_SUPABASE', False):
                return local_sb(*args, **kwargs)
        with patch.object(portal, '_USE_SUPABASE', True), patch.object(portal, '_file_store', return_value=store), patch.object(portal, 'sb', side_effect=db):
            yield store, objects

    def test_remote_upload_survives_empty_local_directory(self):
        with self.remote_files() as (store, objects):
            response = self.post('/portal/files/upload', {'file': (io.BytesIO(b'code,score\na,1'), 'data.csv'), 'project_id': 'own'})
            self.assertEqual(response.status_code, 302)
            row = portal.sb('GET', 'portal_files')[0]
            self.assertTrue(row['filename'].startswith('storage/'))
            self.assertEqual(list(Path(portal.DATA_DIR).rglob('*')), [])
            response = self.client.get('/portal/files/' + row['id'] + '/download')
            self.assertEqual(response.data, b'code,score\na,1')
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.sign_in('other@example.test')
        with self.remote_files() as (store, objects):
            self.assertEqual(self.client.get('/portal/files/' + row['id'] + '/download').status_code, 302)
            store.get.assert_not_called()

    def test_remote_failure_leaves_no_database_metadata_or_import(self):
        before = len(portal.sb('GET', 'measurements'))
        with self.remote_files() as (store, objects):
            store.put.side_effect = portal.StorageUnavailable('unavailable')
            for path in ('/portal/files/upload', '/portal/projects/own/upload'):
                response = self.post(path, {'file': (io.BytesIO(b'code,score\na,1'), 'data.csv')})
                self.assertEqual(response.status_code, 503)
            self.assertEqual(portal.sb('GET', 'portal_files'), [])
            self.assertEqual(len(portal.sb('GET', 'measurements')), before)

    def test_import_original_is_durable_and_downloadable(self):
        with self.remote_files() as (store, objects):
            response = self.post('/portal/projects/own/upload', {'file': (io.BytesIO(b'code,score\na,1'), 'data.csv')})
            self.assertEqual(response.status_code, 302)
            row = portal.sb('GET', 'portal_files')[0]
            self.assertEqual(self.client.get('/portal/files/' + row['id'] + '/download').data, b'code,score\na,1')

    def test_metadata_failure_keeps_remote_original_without_false_success(self):
        local_sb = portal.sb
        def fail_metadata(method, table, **kwargs):
            if method == 'POST' and table == 'portal_files': return []
            with patch.object(portal, '_USE_SUPABASE', False):
                return local_sb(method, table, **kwargs)
        with self.remote_files() as (store, objects), patch.object(portal, 'sb', side_effect=fail_metadata):
            response = self.post('/portal/files/upload', {'file': (io.BytesIO(b'code,score'), 'data.csv')})
            self.assertEqual(response.status_code, 503)
            self.assertEqual(len(objects), 1)
            store.delete.assert_not_called()

    def test_site_metadata_remote_roundtrip_preserves_local_original(self):
        original = [{'key': 'old', 'title': 'Original'}]
        portal.save_events(original)
        with self.remote_files() as (store, objects):
            self.assertEqual(portal.load_events(), original)
            portal.save_events(original + [{'key': 'new', 'title': 'New'}])
            self.assertEqual(len(portal.load_events()), 2)
            self.assertEqual(json.loads(Path(portal.GALLERY_DATA_FILE).read_text()), original)
            store.get.side_effect = portal.StorageUnavailable('offline')
            with self.assertRaises(portal.StorageUnavailable): portal.load_events()

    def test_gallery_upload_and_public_photo_cannot_expose_private_files(self):
        from PIL import Image
        photo = io.BytesIO()
        Image.new('RGB', (2, 2)).save(photo, format='PNG')
        photo.seek(0)
        self.sign_in('admin@example.test')
        portal.save_events([{'key': 'test', 'title': 'Test', 'cover': None}])
        with self.remote_files() as (store, objects):
            self.assertEqual(self.post('/gallery/test/upload', {'photo': (photo, 'photo.png')}).status_code, 302)
            name = portal.event_photos('test')[0]
            anonymous = portal.app.test_client()
            self.assertEqual(anonymous.get('/gallery/test/photo/' + name).status_code, 200)
            store.get.reset_mock()
            self.assertEqual(anonymous.get('/gallery/test/photo/private.csv').status_code, 404)
            self.assertFalse(any(call.args[0].startswith('portal/') for call in store.get.call_args_list))
            self.assertEqual(anonymous.get('/gallery/missing/photo/' + name).status_code, 404)

    def test_remote_delete_failure_keeps_file_metadata(self):
        with self.remote_files() as (store, objects):
            self.post('/portal/files/upload', {'file': (io.BytesIO(b'x'), 'data.csv')})
            row = portal.sb('GET', 'portal_files')[0]
            store.delete.side_effect = portal.StorageUnavailable('offline')
            self.assertEqual(self.post('/portal/files/' + row['id'] + '/delete').status_code, 503)
            self.assertEqual(len(portal.sb('GET', 'portal_files')), 1)

    def test_remote_file_delete_still_requires_project_owner(self):
        with self.remote_files() as (store, objects):
            self.sign_in('editor@example.test')
            self.post('/portal/files/upload', {'file': (io.BytesIO(b'x'), 'data.csv'), 'project_id': 'own'})
            row = portal.sb('GET', 'portal_files')[0]
            self.assertEqual(self.post('/portal/files/' + row['id'] + '/delete').status_code, 403)
            store.delete.assert_not_called()

    def test_legacy_project_original_download_unchanged(self):
        folder = Path(portal.DATA_DIR) / 'project_own'
        folder.mkdir(parents=True)
        (folder / 'legacy.csv').write_bytes(b'original data')
        portal.sb('POST', 'portal_files', data={'id': 'legacy', 'project_id': 'own', 'filename': 'legacy.csv',
                  'original_name': 'legacy.csv', 'researcher_email': 'owner@example.test'})
        with self.remote_files() as (store, objects):
            response = self.client.get('/portal/files/legacy/download')
            self.assertEqual(response.data, b'original data')
            response.close()
            store.get.assert_not_called()

    def test_bundled_gallery_delete_is_persistent_hide_not_original_removal(self):
        folder = Path(portal.GALLERY_FOLDER) / 'test'
        folder.mkdir(parents=True)
        (folder / 'old.png').write_bytes(b'original')
        portal.save_events([{'key': 'test', 'title': 'Test', 'cover': 'old.png'}])
        self.sign_in('admin@example.test')
        with self.remote_files() as (store, objects):
            self.assertEqual(self.post('/gallery/test/delete/old.png').status_code, 302)
            self.assertEqual((folder / 'old.png').read_bytes(), b'original')
            self.assertEqual(portal.event_photos('test'), [])
            self.assertEqual(self.client.get('/gallery/test/photo/old.png').status_code, 404)

    def test_logout_rejects_replayed_cookie(self):
        self.token()
        cookie = self.client.get_cookie('session').value
        self.assertEqual(self.post('/logout').status_code, 302)
        self.client.set_cookie('session', cookie)
        self.assertEqual(self.client.get('/portal').status_code, 302)

    def test_tampered_cookie_rejected(self):
        cookie = self.client.get_cookie('session').value
        self.client.set_cookie('session', 'tampered'+cookie)
        self.assertEqual(self.client.get('/portal').status_code, 302)

    def test_session_store_failure_is_not_authorization(self):
        with sqlite3.connect(portal.DB_PATH) as conn:
            conn.execute('DROP TABLE portal_auth_sessions')
        self.assertEqual(self.client.get('/portal').status_code, 503)

    def test_contact_rate_limit(self):
        portal.limiter.enabled = True
        portal.limiter.reset()
        try:
            statuses = [self.post('/contact', {'message': 'test', 'privacy_consent': 'yes'}).status_code for _ in range(6)]
            self.assertEqual(statuses[:5], [200]*5)
            self.assertEqual(statuses[-1], 429)
        finally:
            portal.limiter.enabled = False

    def test_login_rate_limit(self):
        portal.limiter.enabled = True
        portal.limiter.reset()
        try:
            statuses = [self.post('/login', {'email': 'missing@example.test', 'password': 'wrong'}).status_code for _ in range(11)]
            self.assertEqual(statuses[-1], 429)
        finally:
            portal.limiter.enabled = False

    def test_redirect_does_not_accept_external_target(self):
        result = self.post('/login?next=https://attacker.invalid', {'email': 'owner@example.test', 'password': 'A-long-test-password!'})
        self.assertEqual(result.headers['Location'], '/portal')

    def test_mindmate_batch_is_bounded(self):
        for data in ('[]', '{"dataCount":999999999}', '{"dataCount":"bad"}'):
            response = self.client.post('/api/mindmate/followup', json={
                'order': 'updateData_daily_json', 'PhoneNumber': '01000000000', 'jsonData': data,
            }, headers={'X-API-Key': portal.APP_API_KEY})
            self.assertEqual(response.status_code, 400)

    def test_contact_does_not_claim_success_when_storage_fails(self):
        token = self.token()
        with patch.object(portal, 'sb', return_value=[]):
            response = self.client.post('/contact', data={'message': 'synthetic inquiry', 'privacy_consent': 'yes'},
                                        headers={'X-CSRFToken': token})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('문의가 접수됐습니다.', response.get_data(as_text=True))
        self.assertIn('문의를 저장하지 못했습니다.', response.get_data(as_text=True))

    def test_contact_success_requires_saved_row(self):
        response = self.post('/contact', {'message': 'synthetic inquiry', 'privacy_consent': 'yes'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('문의가 접수됐습니다.', response.get_data(as_text=True))
        self.assertEqual(len(portal.sb('GET', 'contact_messages')), 1)

    def mindmate_send(self, payload, encoding='json'):
        return self.client.post('/api/mindmate/followup', **{encoding: payload},
                                headers={'X-API-Key': portal.APP_API_KEY})

    def test_mindmate_rejects_invalid_phone_before_database_access(self):
        for phone in ('abc', '---', '', '1' * 65, None, True, 1.5,
                      ['01000000000'], {'phone': '01000000000'}):
            with self.subTest(phone=phone), patch.object(portal, 'sb') as database:
                response = self.mindmate_send({'order': 'appOpen', 'PhoneNumber': phone})
                self.assertEqual(response.status_code, 400)
                database.assert_not_called()

    def test_mindmate_rejection_log_never_contains_the_number(self):
        with self.assertLogs(portal.app.logger, level='WARNING') as logs:
            self.mindmate_send({'order': 'appOpen', 'PhoneNumber': 'no-digits-here'})
        self.assertNotIn('no-digits-here', '\n'.join(logs.output))

    @staticmethod
    def legacy_key(phone_number):
        """participant_key derivation deployed before 2026-10-19 (kept for continuity)."""
        import hashlib
        import hmac
        digits = re.sub(r"\D", "", str(phone_number or ""))
        return hmac.new(portal.MINDMATE_PEPPER.encode(), digits.encode(), hashlib.sha256).hexdigest()

    def test_mindmate_phone_hash_mapping_is_unchanged(self):
        import hashlib
        import hmac
        expected = hmac.new(portal.MINDMATE_PEPPER.encode(), b'01000000000', hashlib.sha256).hexdigest()
        for phone in ('01000000000', '010-0000-0000', '(010) 0000 0000'):
            self.assertEqual(portal._participant_key(phone), expected)

    def test_mindmate_key_matches_legacy_derivation_for_relay_formats(self):
        # Formats a Google Apps Script / Unity relay can produce. Each must be
        # accepted and hash exactly as before 2026-10-19 so rows stay linked.
        for phone in ('01000000000', '010-0000-0000', '010.0000.0000', '+82 10-0000-0000',
                      '010–0000–0000', '010 0000 0000', ' 01000000000 ', '010/0000/0000',
                      '０１０００００００００', 1000000000,
                      # SystemInfo.deviceUniqueIdentifier shapes (per the Mind Mate privacy policy)
                      '6F9619FF-8B86-D011-B42D-00C04FC964FF', 'a3f9c2e81b7d4e05b6c1d2e3f4a5b6c7'):
            with self.subTest(phone=phone):
                self.assertEqual(portal._participant_key(phone), self.legacy_key(phone))
                response = self.mindmate_send({'order': 'appOpen', 'PhoneNumber': phone})
                self.assertEqual(response.status_code, 201, response.get_json())

    def test_mindmate_form_encoded_relay_accepted(self):
        # GAS UrlFetchApp with an object payload sends application/x-www-form-urlencoded.
        response = self.mindmate_send({'order': 'writeData_constant', 'PhoneNumber': '010-0000-0000',
                                       'Date': '261019', 'StartTime': '09:00:00', 'EndTime': '09:10:00',
                                       'MusicName': 'synthetic', 'MusicType': '1',
                                       'UserName': 'PII_MARKER', 'Department': 'PII_MARKER'}, 'data')
        self.assertEqual(response.status_code, 201, response.get_json())

    def test_mindmate_all_orders_accept_form_and_json_without_extra_pii(self):
        cases = [
            ('writeData_constant', {'Date': '2026-10-19', 'StartTime': '09:00', 'MusicName': 'test'}, 'mindmate_music_log'),
            ('updateData_daily', {'Date': '2026-10-19', 'Servey_Daily_1': '3'}, 'mindmate_daily_survey'),
            ('updateData_daily_json', {'jsonData': json.dumps({'dataCount': 1, 'date': '2026-10-20',
                'UserName': 'PII_MARKER', 'metadata': {'PhoneNumber': 'PII_MARKER'}})}, 'mindmate_daily_survey'),
            ('appOpen', {}, 'mindmate_app_open'),
        ]
        for encoding in ('data', 'json'):
            for order, fields, table in cases:
                with self.subTest(encoding=encoding, order=order):
                    payload = dict(fields, order=order, PhoneNumber='01000000000',
                                   UserName='PII_MARKER', Department='PII_MARKER',
                                   metadata=json.dumps({'UserName': 'PII_MARKER'}))
                    response = self.mindmate_send(payload, encoding)
                    self.assertEqual(response.status_code, 201, response.get_json())
                    rows = portal.sb('GET', table)
                    self.assertTrue(rows)
                    for row in rows:
                        self.assertNotIn('PII_MARKER', json.dumps(row))
                        self.assertNotIn('01000000000', json.dumps(row))
                    self.assertEqual(rows[-1]['participant_key'], portal._participant_key('01000000000'))

    def test_mindmate_nested_pii_metadata_is_not_retained(self):
        response = self.mindmate_send({'order': 'appOpen', 'PhoneNumber': '01000000000',
                                      'metadata': {'UserName': 'PII_MARKER', 'PhoneNumber': 'PII_MARKER'}})
        self.assertEqual(response.status_code, 201)
        rows = portal.sb('GET', 'mindmate_app_open')
        self.assertNotIn('PII_MARKER', json.dumps(rows))

    def test_mindmate_rejects_structured_values_in_known_fields(self):
        cases = [
            {'order': ['appOpen']},
            {'order': 'writeData_constant', 'Date': {'UserName': 'PII_MARKER'}, 'StartTime': '09:00'},
            {'order': 'updateData_daily', 'Date': '2026-10-19', 'Servey_Daily_1': ['PII_MARKER']},
            {'order': 'updateData_daily_json', 'jsonData': {'date': '2026-10-19'}},
            {'order': 'updateData_daily_json', 'jsonData': json.dumps({'date': {'UserName': 'PII_MARKER'}})},
            {'order': 'updateData_daily_json', 'jsonData': '{bad'},
        ]
        for fields in cases:
            with self.subTest(fields=fields), patch.object(portal, 'sb') as database:
                response = self.mindmate_send(dict(fields, PhoneNumber='01000000000'))
                self.assertEqual(response.status_code, 400)
                database.assert_not_called()

    def test_mindmate_partial_sync_preserves_answers_and_deduplicates(self):
        base = {'PhoneNumber': '01000000000', 'Date': '2026-10-19'}
        for _ in range(2):
            response = self.mindmate_send(dict(base, order='updateData_daily', Servey_Daily_1='4'))
            self.assertEqual(response.status_code, 201)
        response = self.mindmate_send(dict(base, order='updateData_daily_json', jsonData=json.dumps({
            'dataCount': 1, 'date': '2026-10-19', 'isFiveDays': 'true'})))
        self.assertEqual(response.status_code, 201)
        rows = portal.sb('GET', 'mindmate_daily_survey')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['servey_daily_1'], '4')
        self.assertEqual(rows[0]['is_five_days'], 'true')

    def seed_private_response(self):
        self.mindmate_send({'PhoneNumber': '01000000000', 'Date': '2026-10-19',
                            'order': 'updateData_daily', 'Servey_Daily_1': 'PRIVATE_RESPONSE_MARKER'})
        row = portal.sb('GET', 'mindmate_daily_survey')[0]
        portal.sb('PATCH', 'mindmate_daily_survey', params='?id=eq.' + row['id'],
                  data={'raw': json.dumps({'UserName': 'LEGACY_IDENTITY_MARKER'})})
        self.assertIn('LEGACY_IDENTITY_MARKER', str(portal.sb('GET', 'mindmate_daily_survey')[0]['raw']))
        self.sign_in('admin@example.test')
        return row['id']

    def test_mindmate_summaries_do_not_preload_sensitive_answers(self):
        self.seed_private_response()
        for path in ('/portal/mindmate', '/portal/mindmate?tab=live', '/portal/mindmate/live/data'):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertIn('noindex', response.headers['X-Robots-Tag'])
            self.assertNotIn(b'PRIVATE_RESPONSE_MARKER', response.data)
            self.assertNotIn(b'LEGACY_IDENTITY_MARKER', response.data)
        self.assertFalse(portal.sb('GET', 'audit_log', params='?action=eq.view_mindmate_response'))

    def test_mindmate_detail_records_viewer_target_and_time_without_answer(self):
        rid = self.seed_private_response()
        response = self.post('/portal/mindmate/detail/survey/' + rid, json={})
        self.assertEqual(response.status_code, 200)
        self.assertIn('PRIVATE_RESPONSE_MARKER', response.get_data(as_text=True))
        self.assertNotIn('LEGACY_IDENTITY_MARKER', response.get_data(as_text=True))
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        row = portal.sb('GET', 'audit_log', params='?action=eq.view_mindmate_response')[0]
        self.assertEqual(row['researcher'], 'admin@example.test')
        self.assertEqual(row['target_table'], 'mindmate_daily_survey')
        self.assertEqual(row['target_id'], rid)
        self.assertTrue(row['created_at'])
        self.assertIn('참여자-', row['detail'])
        self.assertNotIn('PRIVATE_RESPONSE_MARKER', json.dumps(row))

    def test_mindmate_detail_requires_csrf_and_admin_and_valid_record(self):
        rid = self.seed_private_response()
        path = '/portal/mindmate/detail/survey/' + rid
        self.assertEqual(self.client.get(path).status_code, 405)
        self.assertEqual(self.client.post(path, json={}).status_code, 400)
        for role in ('owner', 'viewer', 'editor', 'other'):
            self.sign_in(role + '@example.test')
            self.assertEqual(self.post(path, json={}).status_code, 403)
        self.sign_in('admin@example.test')
        for path in ('/portal/mindmate/detail/accounts/' + rid, '/portal/mindmate/detail/survey/missing'):
            self.assertEqual(self.post(path, json={}).status_code, 404)
        self.assertFalse(portal.sb('GET', 'audit_log', params='?action=eq.view_mindmate_response'))

    def test_mindmate_detail_fails_closed_when_audit_write_fails(self):
        rid = self.seed_private_response()
        actual = portal.sb
        def failed_log(method, table, **kwargs):
            return [] if method == 'POST' and table == 'audit_log' else actual(method, table, **kwargs)
        with patch.object(portal, 'sb', side_effect=failed_log):
            response = self.post('/portal/mindmate/detail/survey/' + rid, json={})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(b'PRIVATE_RESPONSE_MARKER', response.data)

    def test_mindmate_reopened_response_is_audited_again(self):
        rid = self.seed_private_response()
        for _ in range(2):
            self.assertEqual(self.post('/portal/mindmate/detail/survey/' + rid, json={}).status_code, 200)
        logs = portal.sb('GET', 'audit_log', params='?action=eq.view_mindmate_response')
        self.assertEqual(len(logs), 2)

    def test_mindmate_export_is_audited_and_excludes_legacy_raw(self):
        self.seed_private_response()
        response = self.client.get('/portal/mindmate/export')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'PRIVATE_RESPONSE_MARKER', response.data)
        self.assertNotIn(b'LEGACY_IDENTITY_MARKER', response.data)
        self.assertEqual(len(portal.sb('GET', 'audit_log', params='?action=eq.export_mindmate')), 1)
        with patch.object(portal, 'audit', return_value=False):
            self.assertEqual(self.client.get('/portal/mindmate/export').status_code, 503)

    def test_mindmate_live_music_hides_details_until_audited_read(self):
        self.mindmate_send({'PhoneNumber': '01000000000', 'Date': '2026-10-19', 'StartTime': '09:00',
                            'order': 'writeData_constant', 'MusicName': 'PRIVATE_MUSIC_MARKER'})
        self.sign_in('admin@example.test')
        self.assertNotIn(b'PRIVATE_MUSIC_MARKER', self.client.get('/portal/mindmate?tab=music').data)
        events = self.client.get('/portal/mindmate/live/data').json['events']
        self.assertEqual(events[0]['type'], 'music')
        self.assertNotIn('PRIVATE_MUSIC_MARKER', json.dumps(events))
        response = self.post('/portal/mindmate/detail/music/' + events[0]['id'], json={})
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'PRIVATE_MUSIC_MARKER', response.data)

    def test_idle_and_absolute_session_timeout(self):
        for field, age in [('last_seen', 1801), ('created_at', 28801)]:
            self.sign_in()
            with self.client.session_transaction() as sess:
                sid = sess['auth_session_id']
            portal.sb('PATCH', 'portal_auth_sessions', data={field: int(portal.time.time())-age}, params='?id=eq.'+sid)
            self.assertEqual(self.client.get('/portal').status_code, 302)

    def token(self):
        page = self.client.get('/login').get_data(as_text=True)
        return re.search(r"csrfMeta.content = '([^']+)'", page)[1]

    def post(self, url, data=None, json=None):
        return self.client.post(url, data=data, json=json, headers={'X-CSRFToken': self.token()})

    def test_login_rotates_session_and_has_secure_cookie(self):
        with self.client.session_transaction() as sess:
            sess['old_marker'] = 'discard'
        response = self.post('/login', {'email': 'owner@example.test', 'password': 'A-long-test-password!'})
        self.assertEqual(response.status_code, 302)
        cookie = response.headers['Set-Cookie']
        for value in ('Secure', 'HttpOnly', 'SameSite=Lax'):
            self.assertIn(value, cookie)
        with self.client.session_transaction() as sess:
            self.assertNotIn('old_marker', sess)
            self.assertIn('auth_stamp', sess)

    def test_forged_old_session_rejected(self):
        with self.client.session_transaction() as sess:
            sess.pop('auth_stamp')
        self.assertEqual(self.client.get('/portal').status_code, 302)

    def test_password_change_revokes_existing_session(self):
        portal.sb('PATCH', 'accounts', data={'password': 'changed-hash'}, params='?email=eq.owner%40example.test')
        self.assertEqual(self.client.get('/portal').status_code, 302)

    def test_password_recovery_does_not_issue_token(self):
        response = self.post('/reset-password', {'email': 'other@example.test'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(portal.sb('GET', 'reset_tokens'), [])
        self.assertEqual(self.client.get('/reset-password/previously-issued-token').status_code, 404)

    def test_csrf_on_json_mutations_and_logout(self):
        for url in ('/portal/tasks/own-t/status', '/portal/projects/own/data/cell', '/logout'):
            self.assertEqual(self.client.post(url, json={'status': 'done'}).status_code, 400)
        self.assertEqual(self.client.get('/logout').status_code, 405)
        self.assertEqual(self.post('/logout').status_code, 302)

    def test_own_mutations_still_work(self):
        self.assertEqual(self.post('/portal/tasks/own-t/status', json={'status': 'done'}).status_code, 200)
        self.assertEqual(self.post('/portal/projects/own/data/cell', json={'measurement_id': 'own-m', 'column': 'score', 'value': 7}).status_code, 200)
        self.assertEqual(portal.sb('GET', 'measurements', params='?id=eq.own-m')[0]['data']['score'], 7)

    def test_foreign_tasks_cannot_be_changed(self):
        for suffix in ('status', 'edit', 'delete'):
            response = self.post('/portal/tasks/other-t/'+suffix, json={'status': 'done'})
            self.assertEqual(response.status_code, 403)
        self.assertEqual(portal.sb('GET', 'tasks', params='?id=eq.other-t')[0]['status'], 'todo')

    def test_nested_ids_cannot_cross_projects(self):
        for route in ('participants/other-p/edit', 'participants/other-p/delete',
                      'measurements/other-m/edit', 'measurements/other-m/delete',
                      'variables/other-v/edit', 'variables/other-v/delete'):
            self.assertEqual(self.post('/portal/projects/own/'+route).status_code, 404, route)
        self.assertEqual(self.client.get('/portal/projects/own/participants/other-p').status_code, 404)

    def test_hidden_participant_and_file_ids_checked(self):
        self.assertEqual(self.post('/portal/projects/own/measurements/add', {'participant_id': 'other-p'}).status_code, 404)
        portal.sb('POST', 'portal_files', data={'id': 'other-f', 'project_id': 'other', 'filename': 'other.csv', 'original_name': 'other.csv', 'researcher_email': 'other@example.test'})
        self.assertEqual(self.client.get('/portal/projects/own/data?file_id=other-f').status_code, 404)

    def test_viewer_read_allowed_write_denied(self):
        self.sign_in('viewer@example.test')
        self.assertEqual(self.client.get('/portal/projects/own').status_code, 200)
        self.assertEqual(self.post('/portal/projects/own/edit', {'name': 'changed'}).status_code, 403)

    def test_editor_cannot_delete_project_or_app_rows(self):
        self.sign_in('editor@example.test')
        self.assertEqual(self.post('/portal/projects/own/delete').status_code, 403)
        self.post('/portal/wardy/delete', {'ids': 'own-w'})
        self.assertTrue(portal.sb('GET', 'wardy_events', params='?id=eq.own-w'))

    def test_merge_requires_source_ownership(self):
        self.assertEqual(self.post('/portal/merge/preview', {'project1_id': 'own', 'project2_id': 'other'}).status_code, 403)
        self.assertEqual(self.post('/portal/merge/execute', {'p1_id': 'own', 'p2_id': 'other'}).status_code, 403)
        self.assertEqual(len(portal.sb('GET', 'projects')), 2)

    def test_unscoped_views_do_not_leak_other_projects(self):
        for url in ('/portal', '/portal/wardy', '/portal/wardy/export', '/portal/all/participants'):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, url)
            self.assertNotIn('other-user', response.get_data(as_text=True))
            self.assertNotIn('other-code', response.get_data(as_text=True))
        for url in ('/portal/wardy', '/portal/neurobreeze'):
            self.assertEqual(self.client.get(url+'?project_id=other').status_code, 403)

    def test_collaborator_can_see_scoped_activity(self):
        self.sign_in('viewer@example.test')
        body = self.client.get('/portal/wardy').get_data(as_text=True)
        self.assertIn('own-user', body)
        self.assertNotIn('other-user', body)

    def test_admin_actions_fail_closed(self):
        for url in ('/portal/contacts', '/portal/audit', '/portal/settings/backup', '/portal/drive', '/portal/mindmate', '/portal/mindmate/live/data'):
            self.assertEqual(self.client.get(url).status_code, 403, url)
        self.assertEqual(self.post('/portal/settings/add-account', {'email': 'evil@example.test', 'password': 'very-long-password'}).status_code, 403)
        self.sign_in('admin@example.test')
        self.assertEqual(self.post('/portal/settings/restore').status_code, 403)

    def test_query_values_cannot_inject_filters(self):
        bad = 'owner@example.test&email=neq.x#fragment'
        with patch.object(portal, '_USE_SUPABASE', True), patch.object(portal._requests, 'get') as get:
            get.return_value.json.return_value = []
            portal._get_account(bad)
            query = parse_qs(urlsplit(get.call_args.args[0]).query)
            self.assertEqual(query['email'], ['eq.'+bad])
            # Only paging/ordering keys may be added; the value cannot add a filter.
            self.assertEqual(set(query), {'email', 'select', 'order', 'limit', 'offset'})
            self.assertEqual(query['order'], ['email.asc'])
        self.assertIsNone(portal._get_account(bad))

    def test_sql_values_parameterized_and_columns_validated(self):
        self.assertEqual(portal.sb('GET', 'projects', params='?id=eq.'+portal._q("' OR 1=1 --")), [])
        with self.assertRaises(ValueError):
            portal.sb('POST', 'projects', data={'name) VALUES (1); --': 'x'})

    def test_api_key_not_rendered(self):
        for url in ('/portal/wardy', '/portal/neurobreeze', '/api/docs'):
            self.assertNotIn(portal.APP_API_KEY, self.client.get(url).get_data(as_text=True))

    def test_api_auth_and_invalid_payload(self):
        url = '/api/wardy/events'
        self.assertEqual(self.client.post(url, json={}).status_code, 401)
        for payload in ([], [None], 'string', [1], [{'state': 'open'}]*501):
            self.assertEqual(self.client.post(url, json=payload, headers={'X-API-Key': portal.APP_API_KEY}).status_code, 400)
        response = self.client.post(url, json={'state': 'open', 'user_name': 'test', 'project_id': 'own'}, headers={'X-API-Key': portal.APP_API_KEY})
        self.assertEqual(response.status_code, 201)

    def test_no_default_supabase_seed(self):
        with patch.object(portal, '_USE_SUPABASE', True), patch.dict(os.environ, {'PORTAL_EMAIL': '', 'PORTAL_PASSWORD': ''}), patch.object(portal, '_set_account') as create:
            portal._seed_supabase_account()
            create.assert_not_called()

    def test_headers_methods_and_unnecessary_files(self):
        response = self.client.get('/portal')
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(response.headers['X-Frame-Options'], 'DENY')
        for path in ('/.env', '/.git/config', '/portal.db', '/app.py', '/static/', '/static/../app.py'):
            self.assertEqual(self.client.get(path).status_code, 404, path)
        for method in ('TRACE', 'PUT', 'DELETE'):
            self.assertEqual(self.client.open('/', method=method).status_code, 405)

    def test_csp_limits_script_and_data_destinations(self):
        policy = self.client.get('/').headers['Content-Security-Policy']
        directives = dict(d.strip().split(' ', 1) for d in policy.split(';'))
        self.assertEqual(directives['connect-src'], "'self'")
        self.assertEqual(directives['frame-ancestors'], "'none'")
        self.assertEqual(directives['object-src'], "'none'")
        self.assertNotIn('*', policy)
        self.assertEqual(set(directives['script-src'].split()),
                         {"'self'", "'unsafe-inline'", 'https://cdn.sheetjs.com'})

    def test_templates_load_no_third_party_script_except_sheetjs(self):
        for template in Path(portal.app.root_path, 'templates').glob('*.html'):
            for src in re.findall(r'<script[^>]+src="(https?://[^"]+)"', template.read_text(encoding='utf-8')):
                self.assertTrue(src.startswith('https://cdn.sheetjs.com/'), (template.name, src))

    def test_vendored_scripts_are_served(self):
        for name in ('chart-4.4.1.umd.min.js', 'gsap-3.12.5.min.js'):
            response = self.client.get('/static/vendor/' + name)
            self.assertEqual(response.status_code, 200, name)
            response.close()

    def test_rate_limit_key_uses_cloudflare_header_only_on_render(self):
        headers = {'CF-Connecting-IP': '203.0.113.7', 'X-Forwarded-For': '198.51.100.1'}
        with portal.app.test_request_context('/', headers=headers, environ_base={'REMOTE_ADDR': '10.0.0.1'}):
            with patch.object(portal, '_ON_RENDER', True):
                self.assertEqual(portal.client_ip(), '203.0.113.7')
            with patch.object(portal, '_ON_RENDER', False):
                self.assertEqual(portal.client_ip(), '10.0.0.1')
        with portal.app.test_request_context('/', headers={'X-Forwarded-For': '198.51.100.1'},
                                             environ_base={'REMOTE_ADDR': '10.0.0.1'}):
            with patch.object(portal, '_ON_RENDER', True):
                self.assertEqual(portal.client_ip(), '10.0.0.1')

    def test_login_hashes_even_for_unknown_accounts(self):
        with patch.object(portal, 'check_password_hash', wraps=portal.check_password_hash) as checker:
            self.post('/login', {'email': 'nobody@example.test', 'password': 'whatever-password'})
        checker.assert_called_once()

    def test_robots_and_healthz(self):
        robots = self.client.get('/robots.txt')
        self.assertEqual(robots.status_code, 200)
        self.assertIn('Disallow: /portal', robots.get_data(as_text=True))
        with patch.object(portal, 'sb', side_effect=AssertionError('no database')):
            health = self.client.get('/healthz')
        self.assertEqual(health.get_json(), {'ok': True})

    def test_supabase_get_reads_past_the_1000_row_cap(self):
        rows = [{'id': f'{i:05d}'} for i in range(2345)]
        calls = []

        def fake_get(url, **kwargs):
            q = parse_qs(urlsplit(url).query)
            calls.append(q)
            limit = min(int(q['limit'][0]), 1000)  # server-side Max rows
            offset = int(q['offset'][0])
            response = MagicMock()
            response.json.return_value = rows[offset:offset + limit]
            return response
        with patch.object(portal, '_USE_SUPABASE', True), patch.object(portal._requests, 'get', side_effect=fake_get):
            self.assertEqual(len(portal.sb('GET', 'mindmate_music_log', params='?select=*&order=date.desc')), 2345)
            self.assertTrue(all(c['order'] == ['date.desc'] for c in calls))
            calls.clear()
            self.assertEqual(len(portal.sb('GET', 'wardy_events', params='?select=*&limit=1500')), 1500)
            self.assertEqual([c['limit'][0] for c in calls], ['1000', '500'])
            calls.clear()
            self.assertEqual(len(portal.sb('GET', 'wardy_events', params='?select=*&id=eq.00007')), 2345)
            self.assertEqual(calls[0]['order'], ['id.asc'])

    def test_unassigned_app_rows_visible_and_deletable_only_by_admin(self):
        portal.sb('POST', 'wardy_events', data={'id': 'relay-w', 'project_id': None,
                                                 'user_name': 'relay-user', 'state': 'open'})
        self.assertNotIn('relay-user', self.client.get('/portal/wardy').get_data(as_text=True))
        self.post('/portal/wardy/delete', {'ids': 'relay-w'})
        self.assertTrue(portal.sb('GET', 'wardy_events', params='?id=eq.relay-w'))
        self.sign_in('admin@example.test')
        body = self.client.get('/portal/wardy').get_data(as_text=True)
        self.assertIn('relay-user', body)
        self.assertNotIn('other-user', body)
        self.assertIn('relay-user', self.client.get('/portal/wardy/export').get_data(as_text=True))

    def test_wardy_dashboard_separates_diagnostics_and_hides_stack_traces(self):
        portal.sb('POST', 'wardy_events', data={'id': 'g1', 'project_id': 'own', 'user_name': 'own-user',
                                                 'state': '게임 시작', 'game_level': 'Module1Level1'})
        portal.sb('POST', 'wardy_events', data={'id': 'g2', 'project_id': 'own', 'user_name': 'own-user',
                                                 'state': '게임 완료(성공)', 'game_level': 'Module1Level1'})
        portal.sb('POST', 'wardy_events', data={'id': 'x1', 'project_id': 'own', 'user_name': 'own-user',
                                                 'state': 'exception', 'game_level': 'STACK_TRACE_MARKER at Foo'})
        play = self.client.get('/portal/wardy?days=all').get_data(as_text=True)
        self.assertIn('모듈 1 · 레벨 1', play)
        self.assertNotIn('STACK_TRACE_MARKER', play)
        self.assertNotIn('other-user', play)
        diag = self.client.get('/portal/wardy?view=diagnostics&days=all').get_data(as_text=True)
        self.assertIn('STACK_TRACE_MARKER', diag)

    def test_portal_home_shows_ingestion_status_without_public_footer(self):
        body = self.client.get('/portal').get_data(as_text=True)
        self.assertIn('데이터 수집 상태', body)
        self.assertIn('is-portal', body)
        self.assertNotIn('Mind Mate 설문', body)  # Mind Mate status is admin-only
        self.sign_in('admin@example.test')
        self.assertIn('Mind Mate 설문', self.client.get('/portal').get_data(as_text=True))

    def test_sqlite_fallback_filter_operators(self):
        for i, state in enumerate(['a', 'b', 'c(1)']):
            portal.sb('POST', 'wardy_events', data={'id': f'f{i}', 'user_name': f'Name{i}', 'state': state,
                                                     'received_at': f'2026-11-0{i + 1}T00:00:00'})
        ids = lambda q: sorted(r['id'] for r in portal.sb('GET', 'wardy_events', params=q) if r['id'].startswith('f'))
        self.assertEqual(ids('?state=in.(a,"c(1)")'), ['f0', 'f2'])
        self.assertEqual(ids('?state=not.in.(a,"c(1)")&user_name=ilike.name%25'), ['f1'])
        self.assertEqual(ids('?received_at=gte.2026-11-02T00:00:00'), ['f1', 'f2'])

    def test_per_app_key_accepted_only_for_its_endpoint(self):
        payload = {'state': 'open', 'user_name': 'synthetic'}
        with patch.dict(os.environ, {'WARDY_API_KEY': 'wardy-only-key-for-tests'}):
            ok = self.client.post('/api/wardy/events', json=payload, headers={'X-API-Key': 'wardy-only-key-for-tests'})
            self.assertEqual(ok.status_code, 201)
            other = self.client.post('/api/neurobreeze/eeg', json={'user_name': 'x'},
                                     headers={'X-API-Key': 'wardy-only-key-for-tests'})
            self.assertEqual(other.status_code, 401)
            shared = self.client.post('/api/wardy/events', json=payload, headers={'X-API-Key': portal.APP_API_KEY})
            self.assertEqual(shared.status_code, 201)
            with patch.object(portal, '_SHARED_KEY_DISABLED', True):
                self.assertEqual(self.client.post('/api/wardy/events', json=payload,
                                                  headers={'X-API-Key': portal.APP_API_KEY}).status_code, 401)

    def test_path_traversal_and_symlinks_blocked(self):
        root = Path(self.temp.name) / 'root'
        root.mkdir()
        (root/'escape').symlink_to('/private/tmp')
        with portal.app.test_request_context():
            for name in ('../private', '/etc/passwd', 'escape/anything'):
                with self.assertRaises(portal.HTTPException):
                    portal._stored_path(str(root), name)

    def test_upload_executable_and_fake_image_rejected(self):
        self.assertEqual(self.post('/portal/files/upload', {'file': (io.BytesIO(b'evil'), 'evil.html')}).status_code, 400)
        self.sign_in('admin@example.test')
        with patch.object(portal, 'load_events', return_value=[{'key': 'test'}]):
            self.assertEqual(self.post('/gallery/test/upload', {'photo': (io.BytesIO(b'<script>alert(1)</script>'), 'image.png')}).status_code, 400)

    def test_valid_image_and_archive_bomb(self):
        from PIL import Image
        image = io.BytesIO()
        Image.new('RGB', (2, 2)).save(image, format='PNG')
        image.seek(0)
        validate_image(image, '.png')
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
            z.writestr('large.xml', b'A'*1_000_000)
        archive.seek(0)
        with self.assertRaises(ValueError):
            validate_archive(archive)

    def test_normal_spreadsheet_import_remains_usable(self):
        from openpyxl import Workbook
        workbook = Workbook()
        workbook.active.append(['code', 'score'])
        workbook.active.append(['new-code', 7])
        stream = io.BytesIO()
        workbook.save(stream)
        stream.seek(0)
        response = self.post('/portal/projects/own/upload', {'file': (stream, 'normal.xlsx')})
        self.assertEqual(response.status_code, 302)
        files = portal.sb('GET', 'portal_files', params='?project_id=eq.own')
        self.assertEqual(len(files), 1)
        import json
        self.assertEqual(len(json.loads(files[0]['column_schema'])), 2)

    def test_api_error_body_has_no_internal_details(self):
        with patch.object(portal, '_USE_SUPABASE', True), patch.object(portal._requests, 'get', side_effect=RuntimeError('PRIVATE-SERVER-URL')):
            response = self.client.get('/portal')
            self.assertNotIn('PRIVATE-SERVER-URL', response.get_data(as_text=True))

    def test_csv_and_excel_formulas_are_text(self):
        output = io.StringIO()
        SafeCSVWriter(output).writerow(['=HYPERLINK("bad")', '@SUM(1)', -2, 'ordinary'])
        self.assertIn("'=HYPERLINK", output.getvalue())
        self.assertIn("'@SUM", output.getvalue())
        from openpyxl import Workbook, load_workbook
        workbook = Workbook()
        workbook.active.append(['=1+1', 4])
        neutralize_excel_formulas(workbook)
        buf = io.BytesIO()
        workbook.save(buf)
        buf.seek(0)
        self.assertEqual(load_workbook(buf).active['A1'].data_type, 's')

    def test_all_templates_compile(self):
        for name in portal.app.jinja_env.list_templates():
            portal.app.jinja_env.get_template(name)

    def test_public_app_description_is_escaped(self):
        with patch.object(portal, 'load_extra_apps', return_value=[]), patch.object(portal, 'APPS', [{**portal.APPS[0], 'desc_kr': '<script>alert(1)</script>'}]):
            body = self.client.get('/apps/'+portal.APPS[0]['key']).get_data(as_text=True)
            self.assertNotIn('<script>alert(1)</script>', body)
            self.assertIn('&lt;script&gt;', body)

    def test_drive_outside_configured_folders_rejected(self):
        svc = MagicMock()
        svc.files.return_value.get.return_value.execute.return_value = {'name': 'private', 'mimeType': 'text/plain', 'parents': ['unrelated']}
        with patch.object(portal.drive_sync, '_service', return_value=svc):
            with self.assertRaises(PermissionError):
                portal.drive_sync.get_file('id', ['allowed'])
        svc.files.return_value.get_media.assert_not_called()

    def test_every_mutating_route_requires_csrf_or_api_key(self):
        checked = 0
        for rule in portal.app.url_map.iter_rules():
            if 'POST' not in rule.methods:
                continue
            path = re.sub(r'<(?:[^:<>]+:)?[^<>]+>', 'audit-nonexistent', rule.rule)
            response = self.client.post(path, json={})
            self.assertEqual(response.status_code, 401 if path.startswith('/api/') else 400, path)
            checked += 1
        self.assertGreater(checked, 40)

    def test_session_claim_tampering_cannot_impersonate_admin(self):
        cookie = self.client.get_cookie('session').value
        serializer = portal.app.session_interface.get_signing_serializer(portal.app)
        data = serializer.loads(cookie)
        data['researcher'] = 'admin@example.test'
        # Even a validly signed claim must match the server's session owner.
        self.client.set_cookie('session', serializer.dumps(data))
        self.assertEqual(self.client.get('/portal/contacts').status_code, 302)

    def test_failed_login_does_not_disclose_account_existence(self):
        for email in ('owner@example.test', 'absent@example.test'):
            body = self.post('/login', {'email': email, 'password': 'incorrect'}).get_data(as_text=True)
            self.assertIn('이메일 또는 비밀번호가 올바르지 않습니다.', body)
            self.assertNotIn('Traceback', body)

    def test_new_password_invalidates_all_old_sessions(self):
        first = self.client.get_cookie('session').value
        self.sign_in()
        second = self.client.get_cookie('session').value
        portal._set_account('owner@example.test', 'Cedar-river-lantern-47!')
        for old in (first, second):
            self.client.set_cookie('session', old)
            self.assertEqual(self.client.get('/portal').status_code, 302)

    def test_new_password_policy_rejects_obvious_weak_values(self):
        for value in ('short', 'a'*12, 'abcd'*4, 'Password123456!', 'owner-flower-cloud', 'x'*129, 'cedar\x00riverlantern'):
            self.assertIsNotNone(password_policy_error(value, 'owner@example.test'), value)
        for value in ('Cedar-river-lantern-47!', '노을 진 강가에서 조용히 걷는 오후'):
            self.assertIsNone(password_policy_error(value, 'owner@example.test'))

    def test_password_change_removes_all_server_sessions(self):
        self.sign_in()
        response = self.post('/portal/settings/password', {'current': 'A-long-test-password!',
            'new_pw': 'Cedar-river-lantern-47!', 'confirm': 'Cedar-river-lantern-47!'})
        self.assertEqual(response.location, '/login')
        self.assertEqual(portal.sb('GET', 'portal_auth_sessions', params='?researcher_email=eq.owner%40example.test'), [])
        self.assertTrue(portal.check_password_hash(portal._get_account('owner@example.test'), 'Cedar-river-lantern-47!'))

    def test_weak_password_change_preserves_existing_credentials(self):
        stored = portal._get_account('owner@example.test')
        for value in ('Password123456!', 'a'*12):
            response = self.post('/portal/settings/password', {'current': 'A-long-test-password!', 'new_pw': value, 'confirm': value})
            self.assertEqual(response.location, '/portal/settings')
            self.assertEqual(portal._get_account('owner@example.test'), stored)

    def test_failed_account_write_is_not_reported_as_success(self):
        with portal.app.test_request_context(), patch.object(portal, 'sb', return_value=[]):
            with self.assertRaises(portal.HTTPException) as raised:
                portal._set_account('new@example.test', 'Cedar-river-lantern-47!')
            self.assertEqual(raised.exception.code, 503)

    def test_account_creation_enforces_password_policy(self):
        self.sign_in('admin@example.test')
        self.post('/portal/settings/add-account', {'email': 'new@example.test', 'password': 'Password123456!'})
        self.assertIsNone(portal._get_account('new@example.test'))
        self.post('/portal/settings/add-account', {'email': 'new@example.test', 'password': 'Cedar-river-lantern-47!'})
        self.assertTrue(portal.check_password_hash(portal._get_account('new@example.test'), 'Cedar-river-lantern-47!'))

    def test_password_change_rejects_reusing_current_password(self):
        value = 'Cedar-river-lantern-47!'
        portal._set_account('owner@example.test', value)
        self.sign_in()
        stored = portal._get_account('owner@example.test')
        response = self.post('/portal/settings/password', {'current': value, 'new_pw': value, 'confirm': value})
        self.assertEqual(response.location, '/portal/settings')
        self.assertEqual(portal._get_account('owner@example.test'), stored)

    def test_password_change_attempts_are_rate_limited(self):
        portal.limiter.enabled = True
        portal.limiter.reset()
        try:
            responses = [self.post('/portal/settings/password', {'current': 'incorrect'}) for _ in range(11)]
            self.assertEqual(responses[-1].status_code, 429)
        finally:
            portal.limiter.enabled = False

    def test_settings_only_show_available_authorized_actions(self):
        body = self.client.get('/portal/settings').get_data(as_text=True)
        self.assertNotIn('action="/portal/settings/add-account"', body)
        self.assertNotIn('href="/portal/settings/backup"', body)
        self.assertNotIn('action="/portal/settings/restore"', body)
        self.sign_in('admin@example.test')
        body = self.client.get('/portal/settings').get_data(as_text=True)
        self.assertIn('action="/portal/settings/add-account"', body)
        self.assertIn('type="password" name="password"', body)
        with portal.app.test_request_context():
            body = portal.render_template('portal_settings.html', researcher='admin@example.test', is_admin=True, use_supabase=True)
        self.assertIn('운영 데이터는 Supabase', body)
        self.assertNotIn('href="/portal/settings/backup"', body)
        self.assertNotIn('action="/portal/settings/restore"', body)

    def test_stored_text_is_data_not_executable_handler_code(self):
        attack = "');globalThis.securityProbe=1;//\"<svg onload=alert(1)>"
        portal.sb('PATCH', 'measurements', data={'notes': attack, 'phase': attack, 'data': {'score': 3}}, params='?id=eq.own-m')
        portal.sb('PATCH', 'project_variables', data={'name': attack, 'label': attack, 'unit': attack}, params='?id=eq.own-v')
        elements = []
        class Elements(HTMLParser):
            def handle_starttag(self, tag, attrs):
                elements.append((tag, dict(attrs)))
        response = self.client.get('/portal/projects/own')
        self.assertEqual(response.status_code, 200)
        Elements().feed(response.get_data(as_text=True))
        args = [json.loads(a['data-edit-args']) for _, a in elements if 'data-edit-args' in a]
        self.assertTrue(any(a[:3] == ['own-m', attack, attack] for a in args))
        self.assertTrue(any(a[:3] == ['own-v', attack, attack] for a in args))
        for tag, attrs in elements:
            for key, value in attrs.items():
                if key.startswith('on'):
                    self.assertNotIn('securityProbe', value)
                    self.assertNotIn('alert(1)', value)


if __name__ == '__main__':
    unittest.main()


class AppPagesTests(unittest.TestCase):
    """Public app intro pages and the unified app count."""

    def setUp(self):
        self.client = portal.app.test_client()

    def test_new_app_pages_render_intro_sections(self):
        for key, marker in (('wardy', '모듈과 단계별 레벨'), ('neurobreeze', 'KCT0010473'), ('mindmate', '일일 체크인')):
            with self.subTest(key=key):
                body = self.client.get('/apps/' + key).get_data(as_text=True)
                for text in ('주요 기능', '이렇게 사용합니다', '데이터는 이렇게 다룹니다', marker):
                    self.assertIn(text, body)
                self.assertIn('/privacy/' + key, body)

    def test_legacy_app_pages_unchanged(self):
        body = self.client.get('/apps/pls').get_data(as_text=True)
        self.assertIn('한국어 소개', body)
        self.assertNotIn('주요 기능', body)

    def test_app_count_is_six_everywhere(self):
        self.assertEqual(len(portal.APPS), 6)
        home = self.client.get('/').get_data(as_text=True)
        self.assertNotIn('Mental Health Apps', home)
        self.assertIn('Apps Developed', home)
        self.assertNotIn('Mental-Health Apps Released', home)


class PortalHomeQueryBudgetTests(unittest.TestCase):
    """2026-11-04: the portal home exceeded gunicorn's 30 s timeout in production.

    Simulates Supabase with 6,261 unassigned Wardy rows and asserts the admin
    home page counts rows server-side instead of paging every row over HTTP.
    """

    def test_admin_home_uses_counts_not_row_downloads(self):
        from urllib.parse import parse_qs, urlsplit
        pw_hash = portal.generate_password_hash('A-long-test-password!')
        calls = []
        now = int(portal.time.time())

        def fake_get(url, headers=None, timeout=None):
            parts = urlsplit(url)
            table = parts.path.rsplit('/', 1)[1]
            q = parse_qs(parts.query)
            calls.append((table, parts.query, (headers or {}).get('Prefer')))
            resp = MagicMock(status_code=200, headers={})
            resp.raise_for_status.return_value = None
            if (headers or {}).get('Prefer') == 'count=exact':
                resp.headers = {'Content-Range': '0-0/6261' if table == 'wardy_events' else '*/0'}
                resp.json.return_value = []
            elif table == 'accounts':
                resp.json.return_value = [{'password': pw_hash}]
            elif table == 'portal_auth_sessions':
                resp.json.return_value = [{'created_at': now, 'last_seen': now}]
            elif table == 'wardy_events' and q.get('project_id') == ['is.null']:
                limit = int(q['limit'][0])
                resp.json.return_value = [{'id': str(i), 'received_at': '2026-11-03T07:09:33'} for i in range(min(limit, 1000))]
            else:
                resp.json.return_value = []
            return resp

        client = portal.app.test_client()
        with patch.object(portal, '_USE_SUPABASE', True), \
             patch.object(portal, 'ADMIN_EMAILS', {'admin@example.test'}), \
             patch.object(portal._requests, 'get', side_effect=fake_get), \
             patch.object(portal._requests, 'patch', return_value=MagicMock(status_code=204)), \
             patch.object(portal._requests, 'post', return_value=MagicMock(status_code=201, json=MagicMock(return_value=[{}]))):
            with client.session_transaction() as sess:
                sess['researcher'] = 'admin@example.test'
                sess['auth_stamp'] = portal._auth_stamp(pw_hash)
                sess['auth_session_id'] = 'sid'
            response = client.get('/portal')
        self.assertEqual(response.status_code, 200)
        self.assertIn('6261', response.get_data(as_text=True))
        # No paged download of the unassigned table: every wardy_events GET is a count or asks for <= 10 rows.
        def small(query):
            limit = parse_qs(query).get('limit', ['1000'])[0]
            return int(limit) <= 10
        bulk = [c for c in calls if c[0] == 'wardy_events' and c[2] != 'count=exact' and not small(c[1])]
        self.assertEqual(bulk, [], bulk[:3])
        self.assertLessEqual(len(calls), 40, len(calls))
