"""2FA, access logs, lockout, privacy notice, canonical host — synthetic accounts only."""
import json
import time
import unittest
from unittest.mock import patch

import totp
import test_security as _ts
from test_security import portal


class TotpUnitTests(unittest.TestCase):
    # RFC 6238 Appendix B test vector (SHA-1, secret "12345678901234567890").
    SECRET = 'GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ'

    def test_rfc6238_vector(self):
        self.assertEqual(totp.code_at(self.SECRET, 59 // 30), '287082')
        self.assertEqual(totp.code_at(self.SECRET, 1111111109 // 30), '081804')

    def test_window_and_replay(self):
        now = 1111111109
        code = totp.code_at(self.SECRET, now // 30)
        counter = totp.verify(self.SECRET, code, now=now)
        self.assertEqual(counter, now // 30)
        self.assertIsNone(totp.verify(self.SECRET, code, last_counter=counter, now=now))
        self.assertIsNone(totp.verify(self.SECRET, '000000', now=now))
        self.assertIsNone(totp.verify(self.SECRET, 'abcdef', now=now))
        self.assertEqual(totp.verify(self.SECRET, totp.code_at(self.SECRET, now // 30 - 1), now=now), now // 30 - 1)

    def test_secret_and_uri(self):
        secret = totp.new_secret()
        self.assertEqual(len(secret), 32)
        self.assertIn('otpauth://totp/', totp.provisioning_uri(secret, 'a@example.test'))


class DomainComplianceTests(unittest.TestCase):
    """Borrows SecurityTests fixtures (accounts, projects, CSRF helpers) without re-running its tests."""
    setUpClass = classmethod(_ts.SecurityTests.setUpClass.__func__)
    setUp, sign_in, token, post = _ts.SecurityTests.setUp, _ts.SecurityTests.sign_in, _ts.SecurityTests.token, _ts.SecurityTests.post

    def enroll(self, email='owner@example.test'):
        self.sign_in(email)
        self.post('/portal/settings/2fa/start')
        with self.client.session_transaction() as sess:
            secret = sess['totp_enroll']
        self.post('/portal/settings/2fa/confirm', {'code': totp.code_at(secret, int(time.time() // 30))})
        return secret

    def test_enrolled_account_needs_code_and_codes_cannot_replay(self):
        secret = self.enroll()
        self.assertEqual(portal.sb('GET', 'accounts', params='?email=eq.owner@example.test')[0]['totp_secret'], secret)
        self.post('/logout')
        response = self.post('/login', {'email': 'owner@example.test', 'password': 'A-long-test-password!'})
        self.assertEqual(response.headers['Location'], '/login/2fa')
        self.assertEqual(self.client.get('/portal').status_code, 302)  # not signed in yet (also drops the pending step)
        self.post('/login', {'email': 'owner@example.test', 'password': 'A-long-test-password!'})
        # The enrolment code was already used; the same step must be rejected.
        used = totp.code_at(secret, int(time.time() // 30))
        self.assertEqual(self.post('/login/2fa', {'code': used}).status_code, 401)
        nxt = totp.code_at(secret, int(time.time() // 30) + 1)
        self.assertEqual(self.post('/login/2fa', {'code': nxt}).headers['Location'], '/portal')
        self.assertEqual(self.client.get('/portal').status_code, 200)

    def test_required_2fa_forces_enrolment_first(self):
        with patch.object(portal, 'REQUIRE_2FA', True):
            response = self.post('/login', {'email': 'owner@example.test', 'password': 'A-long-test-password!'})
            self.assertEqual(response.headers['Location'], '/portal/settings/2fa')
            self.assertEqual(self.client.get('/portal/wardy').headers['Location'], '/portal/settings/2fa')
            self.assertEqual(self.client.get('/portal/settings/2fa').status_code, 200)

    def test_per_account_lockout_after_failures(self):
        portal.limiter.enabled = True
        portal.limiter.reset()
        try:
            codes = [self.post('/login', {'email': 'owner@example.test', 'password': 'wrong-password'}).status_code
                     for _ in range(6)]
            self.assertEqual(codes[:5], [401] * 5)
            self.assertEqual(codes[5], 429)
            self.assertIn('15분', self.post('/login', {'email': 'owner@example.test', 'password': 'x'}).get_data(as_text=True))
        finally:
            portal.limiter.enabled = False

    def test_access_log_records_ip_target_and_action(self):
        self.client.get('/portal/wardy?days=all', environ_base={'REMOTE_ADDR': '198.51.100.7'})
        rows = portal.sb('GET', 'audit_log', params='?action=eq.view')
        row = next(r for r in rows if r['target_table'] == 'portal_wardy')
        self.assertEqual((row['researcher'], row['ip']), ('owner@example.test', '198.51.100.7'))
        self.assertEqual(json.loads(row['detail'])['days'], 'all')
        self.post('/login', {'email': 'owner@example.test', 'password': 'nope-nope-nope'})
        self.assertTrue(portal.sb('GET', 'audit_log', params='?action=eq.login_failure'))

    def test_contact_requires_consent_and_privacy_page_exists(self):
        self.assertEqual(self.post('/contact', {'message': 'synthetic inquiry'}).status_code, 400)
        self.assertFalse(portal.sb('GET', 'contact_messages'))
        page = self.client.get('/privacy').get_data(as_text=True)
        for text in ('국외 이전', 'Supabase', '접속기록', '118'):
            self.assertIn(text, page)
        self.assertIn('privacy_consent', self.client.get('/contact').get_data(as_text=True))

    def test_canonical_host_redirect_spares_api_and_healthz(self):
        with patch.object(portal, 'CANONICAL_HOST', 'psynr.cau.ac.kr'):
            r = self.client.get('/team?x=1', base_url='https://caupsynr.onrender.com')
            self.assertEqual((r.status_code, r.headers['Location']), (301, 'https://psynr.cau.ac.kr/team?x=1'))
            self.assertEqual(self.client.get('/healthz', base_url='https://caupsynr.onrender.com').status_code, 200)
            api = self.client.post('/api/wardy/events', json={'state': 'open', 'user_name': 'x'},
                                   headers={'X-API-Key': portal.APP_API_KEY}, base_url='https://caupsynr.onrender.com')
            self.assertEqual(api.status_code, 201)
            self.assertEqual(self.client.get('/team', base_url='https://psynr.cau.ac.kr').status_code, 200)

    def test_portal_ip_allowlist_when_configured(self):
        import ipaddress
        with patch.object(portal, '_PORTAL_ALLOWED', [ipaddress.ip_network('165.194.0.0/16')]):
            self.assertEqual(self.client.get('/portal', environ_base={'REMOTE_ADDR': '203.0.113.9'}).status_code, 403)
            self.assertEqual(self.client.get('/portal', environ_base={'REMOTE_ADDR': '165.194.10.1'}).status_code, 200)
            self.assertEqual(self.client.get('/', environ_base={'REMOTE_ADDR': '203.0.113.9'}).status_code, 200)


    def test_options_and_unused_methods_are_refused(self):
        for path in ('/', '/login', '/api/wardy/events'):
            response = self.client.open(path, method='OPTIONS')
            self.assertEqual(response.status_code, 405, path)
            self.assertNotIn('OPTIONS', response.headers.get('Allow', ''))
        self.assertEqual(self.client.open('/', method='TRACE').status_code, 405)

    def test_participant_download_requires_a_reason(self):
        downloads = lambda: [r for r in portal.sb('GET', 'audit_log', params='?action=eq.download')]
        gate = self.client.get('/portal/wardy/export?project_id=own')
        self.assertEqual(gate.status_code, 200)
        self.assertIn('다운로드 사유', gate.get_data(as_text=True))
        self.assertNotIn('attachment', gate.headers.get('Content-Disposition', ''))
        self.assertIn('name="project_id" value="own"', gate.get_data(as_text=True))
        self.assertEqual(downloads(), [])
        # "기타" without a note is not a reason.
        retry = self.client.get('/portal/wardy/export?project_id=own&reason=other&reason_note=+')
        self.assertIn('직접 적어', retry.get_data(as_text=True))
        self.assertEqual(downloads(), [])
        ok = self.client.get('/portal/wardy/export?project_id=own&reason=report')
        self.assertIn('attachment', ok.headers['Content-Disposition'])
        detail = json.loads(downloads()[0]['detail'])
        self.assertEqual(detail['reason'], 'IRB·연구 보고')
        self.assertEqual(detail['project_id'], 'own')
        self.client.get('/portal/wardy/export?project_id=own&reason=other&reason_note=%EA%B8%B0%EA%B4%80+%EC%A0%9C%EC%B6%9C')
        self.assertIn('기타: 기관 제출', [json.loads(r['detail'])['reason'] for r in downloads()])
        # The gate never opens data the account cannot reach.
        self.assertEqual(self.client.get('/portal/projects/other/export').status_code, 403)

    def test_access_log_summary_flags_review_items(self):
        rows = [{'action': 'login_failure', 'target_id': 'x@example.test', 'ip': '203.0.113.5',
                 'created_at': '2026-11-01T01:00:00+00:00'} for _ in range(6)]
        rows += [
            {'action': 'download', 'researcher': 'a@example.test', 'ip': '198.51.100.1',
             'created_at': '2026-11-01T16:30:00+00:00', 'detail': '{"project_id": "p"}'},          # 01:30 KST, no reason
            {'action': 'download', 'researcher': 'a@example.test', 'ip': '198.51.100.2',
             'created_at': '2026-11-01T03:00:00+00:00', 'detail': '{"reason": "연구 분석"}'},       # 12:00 KST
            {'action': 'view', 'researcher': 'b@example.test', 'ip': '198.51.100.3',
             'created_at': '2026-11-01T05:00:00+00:00'},
            {'action': 'account_add', 'researcher': 'admin@example.test', 'target_id': 'new@example.test',
             'created_at': '2026-11-01T05:00:00+00:00'},
        ]
        s = portal.summarize_access_log(rows)
        self.assertEqual(s['login_fail_accounts'], [('x@example.test', 6)])
        self.assertEqual(s['login_fail_ips'], [('203.0.113.5', 6)])
        self.assertEqual(s['downloads'], [('a@example.test', 2)])
        self.assertEqual(len(s['downloads_without_reason']), 1)
        self.assertEqual([r['created_at'] for r in s['off_hours']], ['2026-11-01T16:30:00+00:00'])
        self.assertEqual([r['action'] for r in s['privilege_changes']], ['account_add'])
        self.assertEqual(s['ips_per_account'][0], ('a@example.test', ['198.51.100.1', '198.51.100.2']))

    def test_monthly_access_review_is_admin_only_and_recorded(self):
        self.assertEqual(self.client.get('/portal/audit/review').status_code, 403)
        self.sign_in('admin@example.test')
        self.assertIn('점검하기', self.client.get('/portal/audit').get_data(as_text=True))
        page = self.client.get('/portal/audit/review')
        self.assertEqual(page.status_code, 200)
        self.assertIn('점검 기록 없음', page.get_data(as_text=True))
        self.assertEqual(self.post('/portal/audit/review', {'note': '이상 없음'}).status_code, 302)
        row = portal.sb('GET', 'audit_log', params='?action=eq.access_log_review')[0]
        self.assertEqual((row['researcher'], json.loads(row['detail'])['note']), ('admin@example.test', '이상 없음'))
        self.assertNotIn('점검하기', self.client.get('/portal/audit').get_data(as_text=True))
        self.assertIn('0일 전 점검', self.client.get('/portal/audit/review').get_data(as_text=True))

if __name__ == '__main__':
    unittest.main()
