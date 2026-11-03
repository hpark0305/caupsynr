"""Summaries for the Wardy research dashboard, computed from event rows.

Pure functions (no Flask, no database) so they can be unit-tested with
synthetic rows. "Gameplay" events are what the study measures; exception,
error, cheat and test rows are app diagnostics and are reported separately.
"""
import re
from collections import Counter, OrderedDict, defaultdict
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
DIAGNOSTIC_STATES = ('exception', 'error', 'cheat', 'test')

# Outcome states that close one play attempt started by '게임 시작'.
START = '게임 시작'
OUTCOMES = OrderedDict([
    ('게임 완료(성공)', 'success'),
    ('게임 완료(실패)', 'fail'),
    ('중도포기', 'quit'),
    ('타임오버', 'timeout'),
])
# Badge tone per state for templates; unknown states fall back to 'neutral'.
STATE_TONE = {
    START: 'info', '게임 완료(성공)': 'good', '게임 완료(실패)': 'warn',
    '중도포기': 'bad', '타임오버': 'bad', '로그인': 'neutral', '모듈 선택': 'neutral',
    '레벨 선택': 'neutral', '앱 전환': 'muted', '앱 복귀': 'muted', '앱 종료': 'muted',
}
_LEVEL = re.compile(r'^Module(\d+)(?:Level(\d+))?$')


def parse_time(value):
    """Parse received_at / event_time strings to aware UTC datetimes (None if unknown)."""
    if not value:
        return None
    text = str(value).strip().replace(' ', 'T')
    if text.endswith('Z'):
        text = text[:-1] + '+00:00'
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def level_key(game_level):
    """(module, level) for 'Module2Level3'; None for anything else (e.g. stack traces)."""
    match = _LEVEL.match(str(game_level or '').strip())
    if not match:
        return None
    return int(match.group(1)), int(match.group(2) or 0)


def level_label(key):
    module, level = key
    return f'모듈 {module} · 레벨 {level}' if level else f'모듈 {module}'


def _rate(part, whole):
    return round(100 * part / whole) if whole else None


def summarize(rows, now=None, days=30):
    """Dashboard numbers for gameplay rows (diagnostic rows must be excluded by the caller)."""
    now = now or datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)
    users, active_week = set(), set()
    counts = Counter()
    last_received = None
    daily = Counter()
    levels = defaultdict(Counter)
    people = {}
    for row in rows:
        name = (row.get('user_name') or '').strip() or '(이름 없음)'
        state = row.get('state') or ''
        received = parse_time(row.get('received_at'))
        when = received or parse_time(row.get('event_time'))
        users.add(name)
        counts[state] += 1
        if received and (last_received is None or received > last_received):
            last_received = received
        if when:
            if when >= week_ago:
                active_week.add(name)
            daily[when.astimezone(KST).date()] += 1
        person = people.setdefault(name, {'user_name': name, 'events': 0, 'starts': 0,
                                          'outcomes': Counter(), 'last': None, 'first': None,
                                          'cleared': set()})
        person['events'] += 1
        if when:
            person['last'] = when if person['last'] is None or when > person['last'] else person['last']
            person['first'] = when if person['first'] is None or when < person['first'] else person['first']
        key = level_key(row.get('game_level'))
        if state == START:
            person['starts'] += 1
            if key:
                levels[key]['start'] += 1
        elif state in OUTCOMES:
            person['outcomes'][OUTCOMES[state]] += 1
            if key:
                levels[key][OUTCOMES[state]] += 1
                if state == '게임 완료(성공)':
                    person['cleared'].add(key)

    starts, success = counts[START], counts['게임 완료(성공)']
    today = now.astimezone(KST).date()
    series = [(today - timedelta(days=offset), 0) for offset in range(days - 1, -1, -1)]
    series = [(day, daily.get(day, 0)) for day, _ in series]

    level_rows = []
    for key in sorted(levels):
        c = levels[key]
        finished = sum(c[o] for o in OUTCOMES.values())
        level_rows.append({'label': level_label(key), 'start': c['start'],
                           **{o: c[o] for o in OUTCOMES.values()},
                           'success_rate': _rate(c['success'], c['start']),
                           'unfinished': max(0, c['start'] - finished)})

    participants = []
    for person in people.values():
        best = max(person['cleared']) if person['cleared'] else None
        participants.append({
            'user_name': person['user_name'], 'events': person['events'], 'starts': person['starts'],
            'success': person['outcomes']['success'], 'fail': person['outcomes']['fail'],
            'quit': person['outcomes']['quit'], 'timeout': person['outcomes']['timeout'],
            'success_rate': _rate(person['outcomes']['success'], person['starts']),
            'best_level': level_label(best) if best else '—',
            'levels_cleared': len(person['cleared']),
            'first': person['first'], 'last': person['last'],
            'active_week': person['user_name'] in active_week,
        })
    participants.sort(key=lambda p: p['last'] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)

    return {
        'participants_total': len(users),
        'participants_week': len(active_week),
        'events_total': sum(counts.values()),
        'starts': starts,
        'success': success,
        'success_rate': _rate(success, starts),
        'outcome_totals': {key: counts[state] for state, key in OUTCOMES.items()},
        'last_received': last_received,
        'freshness': freshness(last_received, now),
        'daily': [{'date': d.isoformat(), 'label': f'{d.month}/{d.day}', 'count': n} for d, n in series],
        'levels': level_rows,
        'participants': participants,
        'state_counts': counts.most_common(),
    }


def freshness(last, now=None, warn_hours=48, stale_days=7):
    """Human-readable age of the newest row plus a tone for the badge."""
    now = now or datetime.now(timezone.utc)
    if not last:
        return {'tone': 'muted', 'text': '수신 기록 없음'}
    age = now - last
    hours = age.total_seconds() / 3600
    if hours < 1:
        text = f'{max(1, int(age.total_seconds() // 60))}분 전'
    elif hours < 48:
        text = f'{int(hours)}시간 전'
    else:
        text = f'{int(hours // 24)}일 전'
    tone = 'good' if hours < warn_hours else ('warn' if age < timedelta(days=stale_days) else 'bad')
    return {'tone': tone, 'text': text, 'at': last.astimezone(KST).strftime('%Y-%m-%d %H:%M')}


def diagnostics(rows, limit=20):
    """Group app error/exception rows by their first stack-trace line."""
    groups = OrderedDict()
    for row in rows:
        detail = str(row.get('game_level') or '').strip() or str(row.get('data') or '').strip()
        head = detail.splitlines()[0][:160] if detail else '(내용 없음)'
        key = (row.get('state') or '', head)
        group = groups.setdefault(key, {'state': key[0], 'message': head, 'count': 0,
                                        'users': set(), 'last': None})
        group['count'] += 1
        group['users'].add(row.get('user_name') or '')
        when = parse_time(row.get('received_at'))
        if when and (group['last'] is None or when > group['last']):
            group['last'] = when
    result = sorted(groups.values(), key=lambda g: g['count'], reverse=True)[:limit]
    for g in result:
        g['users'] = len(g['users'])
        g['last'] = g['last'].astimezone(KST).strftime('%Y-%m-%d %H:%M') if g['last'] else ''
    return result
