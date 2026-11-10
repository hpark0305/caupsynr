"""NeuroBreeze session-log ingest (2026-11-10): no-device sessions, idempotent resend, legacy payloads."""
import os
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

IMPORT_DIR = tempfile.TemporaryDirectory(prefix='cau-nb-import-')
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

URL = '/api/neurobreeze/meditation'


def session_payload(**overrides):
    body = {
        'session_uuid': str(uuid.uuid4()),
        'user_name': 'IT-0001',
        'mode': 'no_device',
        'program': 'breathing',
        'breath_sec': 4,
        'start_time': '2026-11-10T21:30:05+09:00',
        'end_time': '2026-11-10T21:40:07+09:00',
        'planned_sec': 600,
        'duration_sec': 602,
        'completed': True,
        'end_reason': 'completed',
        'device_lost_mid_session': False,
        'eeg_sample_count': 0,
        'left_alpha_indicator': 0.0,
        'right_alpha_indicator': 0.0,
        'alpha_indicator': 0.0,
        'start_alpha_indicator_avg': 0.0,
        'end_alpha_indicator_avg': 0.0,
        'stability_score': 0,
        'app_version': '1.2.0',
        'platform': 'IPhonePlayer',
    }
    body.update(overrides)
    return body


class NeuroBreezeSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cau-nb-test-')
        self.addCleanup(self.temp.cleanup)
        portal.DB_PATH = str(Path(self.temp.name) / 'test.db')
        portal.DATA_DIR = portal.PORTAL_FILES_DIR = str(Path(self.temp.name) / 'files')
        portal.app.config.update(TESTING=True)
        portal.limiter.enabled = False
        with patch.dict(os.environ, {'SEED_ADMIN_EMAIL': '', 'SEED_ADMIN_PASSWORD': ''}):
            portal.init_db()
        network = patch('requests.sessions.Session.request', side_effect=AssertionError('Network forbidden'))
        network.start()
        self.addCleanup(network.stop)
        self.client = portal.app.test_client()
        self.headers = {'X-API-Key': portal.APP_API_KEY}

    def rows(self):
        with sqlite3.connect(portal.DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute('SELECT * FROM neurobreeze_meditation')]

    def test_no_device_session_saved_without_eeg_values(self):
        body = session_payload()
        res = self.client.post(URL, json=body, headers=self.headers)
        self.assertEqual(res.status_code, 201, res.get_data(as_text=True))
        [row] = self.rows()
        self.assertEqual(row['id'], body['session_uuid'])
        self.assertEqual((row['mode'], row['program'], row['duration_sec'], row['end_reason']),
                         ('no_device', 'breathing', 602, 'completed'))
        self.assertEqual(row['completed'], 1)
        self.assertEqual(row['breath_mode'], '4초')
        for col in ('alpha_indicator', 'left_alpha_indicator', 'start_alpha_indicator_avg', 'stability_score'):
            self.assertIsNone(row[col], col)

    def test_resend_same_session_keeps_one_row(self):
        body = session_payload()
        for _ in range(3):
            self.assertEqual(self.client.post(URL, json=body, headers=self.headers).status_code, 201)
        self.assertEqual(len(self.rows()), 1)

    def test_eeg_session_keeps_summary(self):
        body = session_payload(mode='eeg', eeg_sample_count=290, alpha_indicator=41.5,
                               left_alpha_indicator=40.0, right_alpha_indicator=43.0, stability_score=2)
        self.assertEqual(self.client.post(URL, json=body, headers=self.headers).status_code, 201)
        [row] = self.rows()
        self.assertEqual((row['mode'], row['eeg_sample_count'], row['alpha_indicator'], row['stability_score']),
                         ('eeg', 290, 41.5, 2))

    def test_unknown_enum_and_out_of_range_values_become_null(self):
        body = session_payload(mode='fake', program='x', end_reason='hack', duration_sec=999999, completed='yes')
        self.assertEqual(self.client.post(URL, json=body, headers=self.headers).status_code, 201)
        [row] = self.rows()
        for col in ('mode', 'program', 'end_reason', 'duration_sec', 'completed'):
            self.assertIsNone(row[col], col)

    def test_legacy_payload_still_saved(self):
        legacy = {'user_name': 'legacy', 'AlphaIndicatorValue': 12.5, '호흡모드': '4-7-8'}
        for _ in range(2):
            self.assertEqual(self.client.post(URL, json=legacy, headers=self.headers).status_code, 201)
        rows = self.rows()
        self.assertEqual(len(rows), 2)  # no session_uuid → no dedup, as before
        self.assertEqual((rows[0]['alpha_indicator'], rows[0]['breath_mode'], rows[0]['mode']), (12.5, '4-7-8', None))

    def test_auth_and_invalid_payloads(self):
        self.assertEqual(self.client.post(URL, json=session_payload()).status_code, 401)
        for bad in ([1], ['x'], [session_payload()] * 501):
            self.assertEqual(self.client.post(URL, json=bad, headers=self.headers).status_code, 400)
        res = self.client.post(URL, json=session_payload(user_name=''), headers=self.headers)
        self.assertEqual(res.get_json()['invalid'], 1)


if __name__ == '__main__':
    unittest.main()
