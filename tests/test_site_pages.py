"""Public-site renewal: news / participate / join pages and their portal management — synthetic data only."""
import unittest

import test_security as _ts
from test_security import portal


class SitePagesTests(unittest.TestCase):
    """Borrows SecurityTests fixtures (accounts, CSRF helpers) without re-running its tests."""
    setUpClass = classmethod(_ts.SecurityTests.setUpClass.__func__)
    setUp, sign_in, token, post = _ts.SecurityTests.setUp, _ts.SecurityTests.sign_in, _ts.SecurityTests.token, _ts.SecurityTests.post

    STUDY = {'title': 'Synthetic recruiting study', 'summary': 'Test summary', 'eligibility': 'Adults',
             'irb_no': 'TEST-IRB-001', 'contact': 'lab@example.test', 'status': 'recruiting', 'published': '1'}

    def test_public_pages_render(self):
        self.post('/logout')
        for path in ('/', '/research', '/team', '/news', '/participate', '/join'):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)
        self.assertIn('현재 모집 중인 연구가 없습니다', self.client.get('/participate').get_data(as_text=True))

    def test_every_theme_lists_its_publications(self):
        themes = portal._themes_with_pubs()
        self.assertEqual([t['key'] for t in themes], [t['key'] for t in portal.RESEARCH_THEMES])
        for t in themes:
            self.assertGreater(t['pub_count'], 0, t['key'])
        page = self.client.get('/research').get_data(as_text=True)
        for t in portal.RESEARCH_THEMES:
            self.assertIn(f'id="theme-{t["key"]}"', page)

    def test_site_management_is_admin_only(self):
        self.assertEqual(self.client.get('/portal/site').status_code, 403)
        self.assertEqual(self.post('/portal/site/studies/new', self.STUDY).status_code, 403)
        self.assertEqual(portal.sb('GET', 'site_studies'), [])

    def test_study_requires_irb_number_and_shows_publicly(self):
        self.sign_in('admin@example.test')
        self.assertEqual(self.client.get('/portal/site').status_code, 200)
        self.post('/portal/site/studies/new', dict(self.STUDY, irb_no=''))
        self.assertEqual(portal.sb('GET', 'site_studies'), [])
        self.post('/portal/site/studies/new', dict(self.STUDY, link='javascript:alert(1)'))
        rows = portal.sb('GET', 'site_studies')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['link'], '')  # only https links are stored
        page = self.client.get('/participate').get_data(as_text=True)
        self.assertIn('Synthetic recruiting study', page)
        self.assertIn('TEST-IRB-001', page)
        self.assertIn('Synthetic recruiting study', self.client.get('/').get_data(as_text=True))
        # Closing the study removes it from the home strip and the open list.
        self.post(f'/portal/site/studies/{rows[0]["id"]}/edit', dict(self.STUDY, status='closed'))
        self.assertNotIn('Now recruiting', self.client.get('/').get_data(as_text=True))
        self.assertIn('최근 마감된 연구', self.client.get('/participate').get_data(as_text=True))

    def test_unpublished_items_stay_private(self):
        self.sign_in('admin@example.test')
        self.post('/portal/site/studies/new', dict(self.STUDY, published=''))
        self.post('/portal/site/projects/new', {'title': 'Hidden project', 'status': 'ongoing'})
        self.post('/portal/site/projects/new', {'title': 'Public project', 'status': 'completed',
                                                'theme': 'disaster', 'published': '1'})
        self.assertNotIn('Synthetic recruiting study', self.client.get('/participate').get_data(as_text=True))
        research = self.client.get('/research').get_data(as_text=True)
        self.assertNotIn('Hidden project', research)
        self.assertIn('Public project', research)

    def test_project_delete(self):
        self.sign_in('admin@example.test')
        self.post('/portal/site/projects/new', {'title': 'Temp project', 'published': '1'})
        pid = portal.sb('GET', 'site_projects')[0]['id']
        self.post(f'/portal/site/projects/{pid}/delete')
        self.assertEqual(portal.sb('GET', 'site_projects'), [])

    def test_news_merges_notices_and_activities(self):
        portal.sb('POST', 'news', data={'title': 'Synthetic notice', 'content': 'x',
                                        'researcher_email': 'admin@example.test', 'published': 1})
        portal.sb('POST', 'news', data={'title': 'Draft notice', 'content': 'x',
                                        'researcher_email': 'admin@example.test', 'published': 0})
        page = self.client.get('/news').get_data(as_text=True)
        self.assertIn('Synthetic notice', page)
        self.assertNotIn('Draft notice', page)

    def test_canonical_points_to_each_page(self):
        page = self.client.get('/join').get_data(as_text=True)
        self.assertIn('rel="canonical" href="http://localhost/join"', page)


if __name__ == '__main__':
    unittest.main()
