"""What the agents spent, for the prompts page's usage section and /api/usage/ (Justin, #usage, 28 Oct).

What he asked for: graphs for each emergent, coloured by model, Mood or harness; for each Mood, by
emergent; overall usage and cost; and the most expensive turns of the past day, one tab for Anthropic
and one for OpenRouter.

One call is one model response, read from the record's usage fields: a response stored as several rows
(its thinking, its words, a tool call) repeats the same usage on each, and is counted once
(mood_view.how_payload's rule). Its cost is what its harness said it cost, when it said (Pi does, and on
OpenRouter that is OpenRouter's own bill, poller/pi_harness.EventsPoster.bill); otherwise the price
list's (mood_view.call_usd). A Claude Code turn on a subscription is paid for by the plan, so its cost
is what the same work would cost through the API: 'list', not 'paid'.

A turn is one wake: the runner starts a new session for each (resumed or forked from the last), so a
session id is a turn.

The calls are summed into slices -- one per hour (or day), agent, Mood, model and harness -- and the
page groups those slices any way it's asked, so changing a chart's colouring asks nothing again.
"""

from collections import defaultdict
from datetime import timedelta, timezone as tz

from django.utils import timezone

from conversations.models import Message
from conversations.services.mood_view import call_usd, model_label

HARNESSES = ('anthropic', 'openrouter', 'other')  # who was paid: one tab each
RANGES = {24: 'hour', 72: 'hour', 168: 'day', 720: 'day'}  # hours looked back: what one bar is
TOP_TURNS = 15
FIELDS = ('id', 'session_id', 'sender_id', 'mood__slug', 'created_at', 'model_backend', 'client_version',
          'input_tokens', 'output_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens', 'cost_usd')


def harness_of(client_version):
    """Who was paid for a call. Claude Code ('2.1.311', or no version, as the transcript watcher writes
    it): Anthropic. Pi says which vendor it called ('pi 1.1.0 · openrouter', pi_harness.EventsPoster);
    a Pi row from before it said is OpenRouter's, as every Pi call to 28 Oct was."""
    client = client_version or ''
    if not client.startswith('pi'):
        return 'anthropic'
    vendor = client.split(' · ', 1)[1] if ' · ' in client else 'openrouter'
    return vendor if vendor in HARNESSES else 'other'



def calls(since, until=None):
    """Each model response from `since`, once: dicts with its turn, agent, Mood, model, harness, tokens
    and cost."""
    rows = (Message.objects.filter(created_at__gte=since, input_tokens__isnull=False,
                                   sender__thinkingentity__is_biological_human=False)
            .exclude(model_backend__startswith='<'))
    if until:
        rows = rows.filter(created_at__lt=until)
    seen = set()
    for row in rows.order_by('created_at').values_list(*FIELDS).iterator(chunk_size=2000):
        (mid, session, agent, mood, at, model, client, fresh, out, read, written, paid) = row
        key = (session, fresh, out, read, written)
        if key in seen:
            continue
        seen.add(key)
        usd = paid if paid is not None else call_usd(model, fresh, out, read, written)
        yield {'id': str(mid), 'turn': str(session or mid), 'agent': agent, 'mood': mood or '', 'at': at,
               'model': model_label(model) or (model or ''), 'harness': harness_of(client),
               'ctx': (fresh or 0) + (read or 0) + (written or 0), 'cached': read or 0, 'out': out or 0,
               'usd': usd, 'paid': paid is not None}


def bucket_of(at, unit):
    at = at.astimezone(tz.utc)
    return at.replace(minute=0, second=0, microsecond=0, **({'hour': 0} if unit == 'day' else {}))


def usage_state(hours=24, now=None):
    """{"hours", "unit", "buckets", "slices", "agents", "moods", "models", "totals", "top"}.

    slices: [[bucket index, agent, mood, model, harness, calls, ctx, cached, out, usd, unpriced calls]].
    top: the most expensive turns of the past day by harness, {"anthropic": [...], "openrouter": [...]}."""
    hours = hours if hours in RANGES else 24
    unit = RANGES[hours]
    now = now or timezone.now()
    since = now - timedelta(hours=hours)
    step = timedelta(days=1) if unit == 'day' else timedelta(hours=1)
    first = bucket_of(since, unit)
    buckets, at = [], first
    while at <= now:
        buckets.append(at)
        at += step
    index = {b: i for i, b in enumerate(buckets)}

    slices = defaultdict(lambda: [0, 0, 0, 0, 0.0, 0])
    turns = {}
    day_ago = now - timedelta(hours=24)
    totals = {h: {'calls': 0, 'ctx': 0, 'cached': 0, 'out': 0, 'usd': 0.0, 'paid_usd': 0.0, 'unpriced': 0}
              for h in HARNESSES}
    for call in calls(since):
        s = slices[(index.get(bucket_of(call['at'], unit), 0), call['agent'], call['mood'], call['model'],
                    call['harness'])]
        s[0] += 1
        s[1] += call['ctx']
        s[2] += call['cached']
        s[3] += call['out']
        t = totals[call['harness']]
        t['calls'] += 1
        t['ctx'] += call['ctx']
        t['cached'] += call['cached']
        t['out'] += call['out']
        if call['usd'] is None:
            s[5] += 1
            t['unpriced'] += 1
        else:
            s[4] += call['usd']
            t['usd'] += call['usd']
            if call['paid']:
                t['paid_usd'] += call['usd']
        if call['at'] >= day_ago:
            turn = turns.setdefault(call['turn'], {
                'turn': call['turn'], 'first': call['id'], 'at': call['at'].isoformat(), 'agent': call['agent'],
                'mood': call['mood'], 'harness': call['harness'], 'models': [], 'calls': 0, 'ctx': 0,
                'cached': 0, 'out': 0, 'max_ctx': 0, 'usd': 0.0, 'paid': False, 'unpriced': 0})
            if call['model'] and call['model'] not in turn['models']:
                turn['models'].append(call['model'])
            turn['calls'] += 1
            turn['ctx'] += call['ctx']
            turn['cached'] += call['cached']
            turn['out'] += call['out']
            turn['max_ctx'] = max(turn['max_ctx'], call['ctx'])
            if call['usd'] is None:
                turn['unpriced'] += 1
            else:
                turn['usd'] += call['usd']
                turn['paid'] = turn['paid'] or call['paid']

    top = {h: sorted((t for t in turns.values() if t['harness'] == h), key=lambda t: -t['usd'])[:TOP_TURNS]
           for h in HARNESSES}
    for listed in top.values():
        for t in listed:
            t['usd'] = round(t['usd'], 4)

    def ranked(position):
        weight = defaultdict(int)
        for key, s in slices.items():
            weight[key[position]] += s[1] + s[3]
        return [name for name, _ in sorted(weight.items(), key=lambda kv: -kv[1])]

    return {
        'hours': hours, 'unit': unit, 'now': now.isoformat(),
        'buckets': [b.isoformat() for b in buckets],
        'slices': [[k[0], k[1], k[2], k[3], k[4], *s[:4], round(s[4], 5), s[5]] for k, s in slices.items()],
        # Each dimension's names, most tokens first: the order a chart's colours are given in.
        'agents': ranked(1), 'moods': ranked(2), 'models': ranked(3),
        'totals': {h: {**t, 'usd': round(t['usd'], 2), 'paid_usd': round(t['paid_usd'], 2)}
                   for h, t in totals.items()},
        'top': top,
    }
