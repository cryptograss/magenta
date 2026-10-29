"""The Claude plan's rolling windows, as Claude Code reports them (Justin, #usage, 28 Oct).

A Claude Code turn on the plan is paid for by the plan, not in dollars: each one uses up a share of a 5-hour
window and a weekly one. Claude Code's stream says how much with a `rate_limit_event`, whose
`rate_limit_info` names a window (rateLimitType: 'five_hour', 'seven_day', ...), the fraction of it used
(utilization, 0-1, past 1 when usage runs past the cap), when it resets (resetsAt, epoch seconds) and its
status ('allowed', 'allowed_warning', 'rejected'). The runner hands each turn's newest on (mood_poller's
run_turn_once), and a window is kept when it changes, so the prompts page can draw the meter over time.

Read defensively: the event is Claude Code's, and this was written from its own description of the fields,
not a captured run. Whatever came is kept whole in `info`; a window with no utilization is no window.
"""

from datetime import datetime, timezone as tz

from django.utils import timezone

from conversations.models import PlanWindow

NAMES = {'five_hour': '5-hour', 'seven_day': 'week', 'seven_day_opus': 'week, Opus', 'seven_day_sonnet': 'week, Sonnet'}


def windows_in(info):
    """[{'window', 'utilization', 'resets_at', 'status'}] a rate_limit_info says: its own, and any it lists."""
    if not isinstance(info, dict):
        return []
    listed = info.get('windows')
    if isinstance(listed, dict):
        listed = [{'rateLimitType': k, **v} for k, v in listed.items() if isinstance(v, dict)]
    found = []
    for w in [info, *(listed if isinstance(listed, list) else [])]:
        if not isinstance(w, dict):
            continue
        name = w.get('rateLimitType') or w.get('type') or w.get('window')
        used = w.get('utilization')
        if not isinstance(name, str) or not isinstance(used, (int, float)):
            continue
        resets = w.get('resetsAt')
        resets = datetime.fromtimestamp(resets, tz.utc) if isinstance(resets, (int, float)) else None
        found.append({'window': name[:40], 'utilization': float(used), 'resets_at': resets,
                      'status': str(w.get('status') or '')[:40]})
    return list({w['window']: w for w in found}.values())  # one each: the last said


def record(info, agent=''):
    """Keep each window the info names, when it differs from the newest kept for that window."""
    kept = []
    for w in windows_in(info):
        last = PlanWindow.objects.filter(window=w['window']).order_by('-at').first()
        if last and (round(last.utilization, 4), last.resets_at, last.status) == (round(w['utilization'], 4),
                                                                                   w['resets_at'], w['status']):
            continue
        kept.append(PlanWindow.objects.create(agent=agent[:60], info=info, **w))
    return kept


def plan_state(since, now=None):
    """{"windows": {name: {"label", "now": {utilization, resets_at, status, at} or None, "points": [[iso, used]]}}}:
    each window's newest reading, and its readings from `since` -- with the one before, so a line starts at the
    window's left edge."""
    now = now or timezone.now()
    out = {}
    for name in PlanWindow.objects.order_by().values_list('window', flat=True).distinct():
        rows = list(PlanWindow.objects.filter(window=name, at__gte=since).order_by('at'))
        before = PlanWindow.objects.filter(window=name, at__lt=since).order_by('-at').first()
        newest = rows[-1] if rows else before
        if newest is None:
            continue
        points = ([[since.isoformat(), before.utilization]] if before else []) + \
            [[r.at.isoformat(), r.utilization] for r in rows]
        # A window past its reset has started over: from then on it reads 0 until a turn says otherwise.
        current = {'utilization': newest.utilization, 'status': newest.status, 'at': newest.at.isoformat(),
                   'resets_at': newest.resets_at.isoformat() if newest.resets_at else None}
        if newest.resets_at and newest.resets_at <= now:
            current.update(utilization=0.0, status='reset')
            points.append([newest.resets_at.isoformat(), 0.0])
        out[name] = {'label': NAMES.get(name, name.replace('_', ' ')), 'now': current, 'points': points}
    return {'windows': out}

