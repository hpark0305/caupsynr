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


if __name__ == '__main__':
    unittest.main()
