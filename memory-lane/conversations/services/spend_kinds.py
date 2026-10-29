"""Spend by kind of turn, for the status page and /api/usage/kinds/ (Justin, #interfaces, 28 Oct: "introspect
token usage by kind of turn").

A turn's kind is what woke it, as its wake says (mood_view.WAKE_REASON, wake_said): a mention with the agent's
full tools, or one that may only look; or a look nobody asked for -- at new posts (consider), at the agents'
own talk (crosstalk), at a long quiet (quiet), at someone signing in (arrival). A screened moment never woke
the agent at all: the runner's small screen let it pass, and left a dot (by="screen") with what that cost.

Dollars are what was paid by the call -- OpenRouter's, and any vendor's but the Claude plan's, which is
paid in a share of its rolling windows instead (services/plan.py). Each model response is counted as the
prompts page counts it (usage.calls, helpers' calls included), and its output is split three ways -- thinking, words,
tool calls -- by the characters each of its rows holds. Where the harness kept its thinking (Pi), the split is
by those characters; where it didn't (Claude Code keeps only a signature), words and tool calls are taken at
CHARS_PER_TOKEN and thinking is the rest. Estimates, and labelled so.
"""

from collections import defaultdict
from datetime import timedelta

from django.db.models import TextField
from django.db.models.functions import Cast
from django.utils import timezone

from conversations.models import Message
from conversations.services import usage
from conversations.services.mood_view import WAKE_REACH, WAKE_REASON, WAKE_SENDER, WAKE_TAIL, wake_said

CHARS_PER_TOKEN = 4
# In the order the table shows them: (key, what the page calls it).
KINDS = (('mention-full', 'mention, full tools'), ('mention-look', 'mention, look-only'),
         ('consider', 'unasked look at new posts'), ('crosstalk', "look at the agents' own talk"),
         ('quiet', 'look at a long quiet'), ('arrival', 'someone signed in'),
         ('other', 'some other wake'), ('unknown', 'wake not recorded'), ('screened', 'screened, never woken'))
REASONS = {'consider', 'crosstalk', 'quiet', 'arrival'}
SCREENED = '<silent by="screen">'
QUIET_SOURCE = 'ingest-mood-runner'  # views_runner's own rows


def kind_of(head, tail):
    """A wake's kind, from its opening (the reason) and its end (the tools it gave)."""
    reason = WAKE_REASON.search(head or '')
    reason = reason.group(1) if reason else ''
    if reason:
        return reason if reason in REASONS else 'other'
    return 'mention-look' if wake_said(tail or '') == 'look' else 'mention-full'


def _wakes(sessions, since):
    """session id (str) -> [(when, kind)], oldest first. A wake is the poller's prompt in the session: filed as
    the poller's (Pi echoes it; the runner posts it for Claude Code), or -- brought in from a transcript whose
    prompt opens with the harness's own reminders -- as the container's person's, but holding a <mood-wake."""
    found = defaultdict(list)
    near = {'session_id__in': sessions, 'created_at__gte': since - timedelta(seconds=WAKE_REACH)}
    rows = (Message.objects.filter(**near, sender_id=WAKE_SENDER)
            | Message.objects.filter(**near, sender__thinkingentity__is_biological_human=True))
    rows = (rows.annotate(text=Cast('content', TextField())).filter(text__contains='<mood-wake')
            .order_by('created_at').values_list('session_id', 'created_at', 'text'))
    for session, at, text in rows.iterator(chunk_size=200):
        found[str(session)].append((at, kind_of(text[text.find('<mood-wake'):][:300], text[-WAKE_TAIL:])))
    return found


def split_output(out, chars):
    """A response's output tokens as {'thinking', 'words', 'tools'}, from the characters each kind of row holds."""
    if chars.get('thinking'):
        total = sum(chars.values()) or 1
        parts = {k: out * chars.get(k, 0) / total for k in ('thinking', 'words', 'tools')}
    else:
        words = chars.get('words', 0) / CHARS_PER_TOKEN
        tools = chars.get('tools', 0) / CHARS_PER_TOKEN
        if words + tools > out and words + tools:
            scale = out / (words + tools)
            words, tools = words * scale, tools * scale
        parts = {'thinking': out - words - tools, 'words': words, 'tools': tools}
    return parts


def spend_by_kind(hours=24, mood=None, now=None):
    """{"hours", "mood", "moods", "kinds": [{"kind", "label", "turns", "calls", "ctx", "cached"?, "usd", "out",
    "thinking", "words", "tools", "unpriced"}], "total": {...}}."""
    now = now or timezone.now()
    since = now - timedelta(hours=hours)
    calls = usage.calls(since, mood=mood, chars=True)  # the prompts page's own count: the two agree
    wakes = _wakes({c['turn'] for c in calls}, since)
    rows = {k: {'kind': k, 'label': label, 'turns': set(), 'calls': 0, 'ctx': 0, 'usd': 0.0, 'out': 0,
                'thinking': 0.0, 'words': 0.0, 'tools': 0.0, 'unpriced': 0} for k, label in KINDS}
    for c in calls:
        before = [kind for at, kind in wakes.get(c['turn'], ()) if at <= c['at']]
        row = rows[before[-1] if before else 'unknown']
        row['turns'].add(c['turn'])
        row['calls'] += 1
        row['ctx'] += c['ctx']
        row['out'] += c['out']
        if c['harness'] == 'anthropic':
            pass  # the plan's, paid in a share of its windows, not dollars (Justin, 28 Oct; services/plan.py)
        elif c['usd'] is None:
            row['unpriced'] += 1
        else:
            row['usd'] += c['usd']
        for k, v in split_output(c['out'], c['chars']).items():
            row[k] += v
    # The runner's dots (views_runner.api_quiet): no session, and the screen's own by its opening.
    screened = Message.objects.filter(created_at__gte=since, session_id=None, source_file=QUIET_SOURCE)
    if mood:
        screened = screened.filter(mood__slug=mood)
    screened = [usd for content, usd in screened.values_list('content', 'cost_usd')
                if isinstance(content, str) and content.startswith(SCREENED)]
    for i, usd in enumerate(screened):
        rows['screened']['turns'].add(i)  # each screen its own moment: no session, no calls of the agent's
        if usd is None:
            rows['screened']['unpriced'] += 1
        else:
            rows['screened']['usd'] += usd

    kinds = []
    for k, _ in KINDS:
        row = rows[k]
        row['turns'] = len(row['turns'])
        if not row['turns'] and k in ('other', 'unknown'):
            continue
        for key in ('thinking', 'words', 'tools'):
            row[key] = int(round(row[key]))
        row['usd'] = round(row['usd'], 2)
        kinds.append(row)
    total = {key: sum(r[key] for r in kinds) for key in ('turns', 'calls', 'ctx', 'out', 'thinking', 'words',
                                                         'tools', 'unpriced')}
    total['usd'] = round(sum(r['usd'] for r in kinds), 2)
    moods = sorted(set(Message.objects.filter(created_at__gte=since, sender_id=WAKE_SENDER)
                       .exclude(mood=None).values_list('mood__slug', flat=True)))
    return {'hours': hours, 'mood': mood or '', 'moods': moods, 'kinds': kinds, 'total': total}
