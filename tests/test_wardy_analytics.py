"""Unit tests for the Wardy dashboard summaries (synthetic rows only)."""
import unittest
from datetime import datetime, timedelta, timezone

import wardy_analytics as wa

NOW = datetime(2026, 11, 3, 3, 0, tzinfo=timezone.utc)


def ev(user, state, level='Module1Level1', hours_ago=1):
    t = (NOW - timedelta(hours=hours_ago)).strftime('%Y-%m-%dT%H:%M:%S+00:00')
    return {'user_name': user, 'state': state, 'game_level': level, 'received_at': t, 'event_time': t}


class WardyAnalyticsTests(unittest.TestCase):
    def test_kpis_levels_and_participants(self):
        rows = [ev('A', '게임 시작'), ev('A', '게임 완료(성공)'),
                ev('A', '게임 시작', 'Module1Level2'), ev('A', '중도포기', 'Module1Level2'),
                ev('B', '게임 시작', hours_ago=24 * 10), ev('B', '타임오버', hours_ago=24 * 10),
                ev('C', '로그인', level='')]
        s = wa.summarize(rows, now=NOW, days=30)
        self.assertEqual((s['participants_total'], s['participants_week']), (3, 2))
        self.assertEqual((s['starts'], s['success'], s['success_rate']), (3, 1, 33))
        self.assertEqual(s['outcome_totals'], {'success': 1, 'fail': 0, 'quit': 1, 'timeout': 1})
        lv1 = s['levels'][0]
        self.assertEqual((lv1['label'], lv1['start'], lv1['success'], lv1['timeout'], lv1['success_rate']),
                         ('모듈 1 · 레벨 1', 2, 1, 1, 50))
        a = next(p for p in s['participants'] if p['user_name'] == 'A')
        self.assertEqual((a['starts'], a['success'], a['best_level']), (2, 1, '모듈 1 · 레벨 1'))
        self.assertEqual(s['participants'][0]['user_name'], 'A')  # most recent first
        self.assertEqual(len(s['daily']), 30)
        self.assertEqual(sum(d['count'] for d in s['daily']), 7)

    def test_stack_traces_never_become_levels(self):
        self.assertIsNone(wa.level_key('Ngarden.Mission.AudioManager+Player (at <000>:0)\nUnity...'))
        self.assertEqual(wa.level_key('Module3Level3'), (3, 3))
        self.assertEqual(wa.level_key('Module2'), (2, 0))

    def test_freshness_tones(self):
        self.assertEqual(wa.freshness(None, NOW)['tone'], 'muted')
        self.assertEqual(wa.freshness(NOW - timedelta(hours=3), NOW)['tone'], 'good')
        self.assertEqual(wa.freshness(NOW - timedelta(days=3), NOW)['tone'], 'warn')
        stale = wa.freshness(NOW - timedelta(days=15), NOW)
        self.assertEqual((stale['tone'], stale['text']), ('bad', '15일 전'))

    def test_diagnostics_grouped_by_first_line(self):
        rows = [{'state': 'exception', 'game_level': 'NullRef at X\nframe 2', 'user_name': 'A', 'received_at': '2026-11-01T00:00:00Z'},
                {'state': 'exception', 'game_level': 'NullRef at X\nframe 9', 'user_name': 'B', 'received_at': '2026-11-02T00:00:00Z'},
                {'state': 'error', 'game_level': '', 'data': {'m': 'x'}, 'user_name': 'A', 'received_at': None}]
        groups = wa.diagnostics(rows)
        self.assertEqual((groups[0]['message'], groups[0]['count'], groups[0]['users']), ('NullRef at X', 2, 2))
        self.assertEqual(len(groups), 2)


if __name__ == '__main__':
    unittest.main()
