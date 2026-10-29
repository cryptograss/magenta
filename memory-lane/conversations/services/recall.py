"""What one agent remembers: the record, as seen from where it stood.

The memory server knows who is calling by the key its MCP client sends
(mcp/server.caller), the agent's runner key; an agent never names itself, so
it can't remember as someone else (magent-self, 2026-10-22). Everything here
takes that agent's name.

Searching the record (search_messages) and remembering are different acts.
What an agent remembers is:

    what it said;
    its conversations: the sessions it spoke in (or, for the oldest messages,
        which have none, the context heaps), everyone's words in them;
    what was said to it in a Mood: turns that @mention it, and handoffs for it.

Nothing from before it arrived -- when its first conversation began: the
session of its first word in the record, so the wake that woke it counts --
except where it was named before then (mentions_of_me, labelled). Which people an agent knows,
and how it speaks, are read the same way, from the record; nothing here is a
model's judgement (Artificial Dumb).
"""

import re
from collections import defaultdict
from datetime import timedelta

from django.db.models import Count, Max, Min, Q, TextField
from django.db.models.functions import Cast

from conversations.models import Message, ThinkingEntity

from .memory import kind_of, readable, search
from .mood_view import MACHINERY_SENDERS, addressed_in, is_summary, is_wrapper, known_names, prose

HANDOFF = 'handoff'     # services/handoff.SOURCE
SCAN = 5000             # Mood messages read at most, for one look at mentions
EACH = 600              # characters of one turn shown


def when(dt):
    return dt.isoformat()[:16] + 'Z' if dt else 'never'


def moment(text):
    """An ISO time (a date alone is its midnight, a time without an offset UTC), or None."""
    from datetime import datetime, timezone
    from django.utils.dateparse import parse_date, parse_datetime
    if not text:
        return None
    at = parse_datetime(str(text))
    if at is None:
        day = parse_date(str(text))
        at = datetime(day.year, day.month, day.day) if day else None
    return at.replace(tzinfo=timezone.utc) if at is not None and at.tzinfo is None else at


def clip(text, limit=EACH):
    text = ' '.join((text or '').split())
    return text if len(text) <= limit else text[:limit] + ' […]'


# --- who it is, and since when -----------------------------------------------

def arrival(agent):
    """(created_at, block) of the start of the agent's first conversation -- the first message of the
    session its first word is in, or that word, with no session -- or (None, None): not arrived yet."""
    first = Message.objects.filter(sender_id=agent).order_by('created_at').first()
    if first is None:
        return None, None
    if first.session_id:
        first = Message.objects.filter(session_id=first.session_id).order_by('created_at').first()
    return first.created_at, first.eth_blockheight


def arrival_line(agent):
    at, block = arrival(agent)
    if at is None:
        return f"You are {agent}. You haven't said anything in the record yet: there is nothing of yours to remember."
    return f"You are {agent}. You arrived {when(at)}" + (f", block {block:,}" if block else '') + \
        ': your first conversation in the record. Nothing before then is your memory.'


def sessions_of(agent):
    return (Message.objects.filter(sender_id=agent, session_id__isnull=False)
            .values('session_id').distinct())


def heaps_of(agent):
    return (Message.objects.filter(sender_id=agent, session_id__isnull=True, context_heap_id__isnull=False)
            .values('context_heap_id').distinct())


def conversations(agent):
    """A Q for the messages of the agent's own conversations: the sessions it spoke in, and the heaps
    of its words that have no session."""
    return (Q(session_id__in=sessions_of(agent))
            | Q(session_id__isnull=True, context_heap_id__in=heaps_of(agent)))


def mine(agent):
    """A Q for everything the agent remembers (see the module's docstring)."""
    said_to = Q(mood__isnull=False) & (Q(text__icontains=f'@{agent}')
                                       | Q(source_file=HANDOFF, content__for=agent))
    scope = Q(sender_id=agent) | conversations(agent) | said_to
    at, _ = arrival(agent)
    return (scope & Q(created_at__gte=at, is_sidechain=False)) if at else Q(pk__in=[])


def with_text(messages):
    """Messages with their content as text, to look in (the `text` mine() filters on)."""
    return messages.annotate(text=Cast('content', output_field=TextField()))


# --- remember ------------------------------------------------------------------

def remember_text(agent, query, limit=20, exact=False):
    """What the agent remembers about `query`: search, within mine()."""
    head = [arrival_line(agent)]
    at, _ = arrival(agent)
    if at is None:
        return head[0]
    hits = search_mine(agent, query, limit=limit, exact=exact)
    how = 'exact phrase' if exact else 'words'
    head.append(f"{len(hits)} of your memories matching '{query}' ({how}; "
                + ('newest first' if exact else 'best first') + "):\n")
    lines = []
    for i, hit in enumerate(hits, 1):
        m = hit['message']
        who = 'you' if m.sender_id == agent else m.sender_id
        where = f"Mood {m.mood_slug}" if m.mood_id else (f"session {str(m.session_id)[:8]}" if m.session_id
                                                         else "no session")
        lines.append(f"[{i}] {who} · {hit['kind']} · {when(m.created_at)} · {where} · {m.id}")
        lines.append(f"    {hit['snippet']}\n")
    if hits:
        lines.append("get_message_context with an id shows what was said around it.")
    elif not exact:
        lines.append("Nothing you remember. For a literal string, try exact: true; to look in the whole record, "
                     "not only your own memory, search_messages.")
    return '\n'.join(head + lines)


def search_mine(agent, query, limit=20, exact=False):
    """search(), within what the agent remembers. The said-to-it part of mine() looks in the text,
    so the messages are annotated with it first."""
    ids = with_text(Message.objects.all()).filter(mine(agent)).values('id')
    return search(query, limit=limit, exact=exact, within=Q(id__in=ids))


# --- mentions_of_me ---------------------------------------------------------------

def naming(agent):
    """The agent's name as a word: 're' in 're, look', not in 'there' or 'pre-'."""
    return re.compile(rf'(?<![\w@-]){re.escape(agent)}(?![\w-])|@{re.escape(agent)}(?![\w-])', re.I)


def mentions(agent, limit=20, named=False, before=None, scan=SCAN, answers=True):
    """Turns in Moods that address the agent, newest first, each with its first answer there.

    Each: {'message', 'text', 'how' ('addressed', 'handed', or with named=True also 'named': the
    name written but nobody @'d it), 'early' (before the agent arrived), 'answer' (its next turn in
    that Mood, or None; looked up only with answers=True)}. Mentions are read from the text, as the Moods read them (mood_view.addressed_in).
    """
    names = known_names()
    at, _ = arrival(agent)
    word = naming(agent)
    found = (with_text(Message.objects.filter(mood__isnull=False, is_sidechain=False).exclude(sender_id=agent))
             .filter(Q(text__icontains=agent) & (~Q(sender_id__in=MACHINERY_SENDERS) | Q(source_file=HANDOFF)))
             .order_by('-created_at'))
    if before:
        found = found.filter(created_at__lt=before)
    out = []
    for msg in found[:scan].iterator():
        if msg.source_file == HANDOFF:
            c = msg.content if isinstance(msg.content, dict) else {}
            if c.get('type') != 'handoff' or c.get('for') != agent:
                continue
            how, text = 'handed', f"{c.get('by', '')} handed this over from #{c.get('from_mood', '')}: " \
                                  f"{c.get('about', '')}\n{c.get('context', '')}"
        else:
            if msg.sender_id not in names:
                continue
            text = prose(msg.content)
            if not text or is_wrapper(text) or is_summary(msg, text) or not word.search(text):
                continue
            if agent in addressed_in(text, names, by=msg.sender_id):
                how = 'addressed'
            elif named:
                how = 'named'
            else:
                continue
        out.append({'message': msg, 'text': text, 'how': how, 'early': bool(at is None or msg.created_at < at),
                    'answer': None})
        if len(out) >= limit:
            break
    for m in out if answers else ():
        if m['how'] != 'named':
            m['answer'] = answer_to(agent, m['message'])
    return out


def answer_to(agent, msg):
    """The agent's first turn in msg's Mood after it, within a day, as (message, text), or None."""
    later = (Message.objects.filter(mood_id=msg.mood_id, sender_id=agent, is_sidechain=False,
                                    created_at__gt=msg.created_at,
                                    created_at__lt=msg.created_at + timedelta(days=1))
             .order_by('created_at'))
    for reply in later[:20]:
        text = prose(reply.content)
        if text and not is_wrapper(text) and not is_summary(reply, text):
            return reply, text
    return None


def mentions_text(agent, limit=20, named=False, before=None):
    limit = max(1, min(int(limit or 20), 100))
    found = mentions(agent, limit=limit, named=bool(named), before=moment(before))
    head = [arrival_line(agent),
            f"{len(found)} times you were addressed in a Mood" + (" or named" if named else '')
            + ", newest first" + (f", before {before}" if before else '') + ':\n']
    lines = []
    for i, m in enumerate(found, 1):
        msg = m['message']
        label = {'addressed': '@you', 'handed': 'handed to you', 'named': 'named you'}[m['how']]
        early = ' · BEFORE YOU ARRIVED' if m['early'] else ''
        lines.append(f"[{i}] {msg.sender_id} {label} · {when(msg.created_at)} · Mood {msg.mood_slug}{early} · "
                     f"#m-{msg.id}")
        lines.append(f"    {clip(m['text'])}")
        if m['answer']:
            reply, text = m['answer']
            lines.append(f"    you, {when(reply.created_at)} (#m-{reply.id}): {clip(text, 300)}")
        elif m['how'] != 'named':
            lines.append("    (you didn't speak there in the day after)")
        lines.append('')
    if found:
        oldest = found[-1]['message'].created_at.isoformat()
        lines.append(f"Older ones: before {oldest}. read_mood with a Mood and an id reads what was said around one.")
    return '\n'.join(head + lines)


# --- who_i_know, my_history_with -------------------------------------------------

def people(agent):
    """Thinking entities other than the agent: {name: is_biological_human}."""
    return dict(ThinkingEntity.objects.exclude(name=agent).values_list('name', 'is_biological_human'))


def my_mood_turns(agent):
    """The agent's own turns in Moods that @mention someone, as (message, [names])."""
    names = known_names()
    found = (with_text(Message.objects.filter(mood__isnull=False, sender_id=agent, is_sidechain=False))
             .filter(text__contains='@').order_by('-created_at'))
    for msg in found[:SCAN].iterator():
        text = prose(msg.content)
        if text and not is_wrapper(text):
            whom = [n for n in addressed_in(text, names, by=agent) if n != agent]
            if whom:
                yield msg, whom


def who_i_know(agent):
    """[{name, human, words, first, last}], the most recent first: everyone the agent has exchanged
    words with. Words: their messages in its conversations, their turns addressing it in a Mood, and
    its turns addressing them."""
    others = people(agent)
    at, _ = arrival(agent)
    if at is None:
        return []
    known = defaultdict(lambda: {'words': 0, 'first': None, 'last': None})

    def count(name, n, first, last):
        k = known[name]
        k['words'] += n
        k['first'] = min(filter(None, [k['first'], first]), default=None)
        k['last'] = max(filter(None, [k['last'], last]), default=None)

    rows = (Message.objects.filter(conversations(agent), sender_id__in=list(others), is_sidechain=False,
                                   created_at__gte=at)
            .values('sender_id').annotate(n=Count('id'), first=Min('created_at'), last=Max('created_at')))
    for row in rows:
        count(row['sender_id'], row['n'], row['first'], row['last'])
    for m in mentions(agent, limit=SCAN, answers=False):
        if not m['early'] and m['message'].sender_id in others:
            count(m['message'].sender_id, 1, m['message'].created_at, m['message'].created_at)
    for msg, whom in my_mood_turns(agent):
        for name in whom:
            if name in others:
                count(name, 1, msg.created_at, msg.created_at)
    found = [{'name': name, 'human': others[name], **k} for name, k in known.items()]
    return sorted(found, key=lambda k: k['last'], reverse=True)


def who_i_know_text(agent, limit=40):
    limit = max(1, min(int(limit or 40), 200))
    found = who_i_know(agent)
    head = [arrival_line(agent)]
    if not found:
        return '\n'.join(head + ['Nobody yet: you have exchanged no words in the record with anyone.'])
    head.append(f"{len(found)} people and agents you have exchanged words with, most recent first"
                + (f" ({len(found) - limit} more after these)" if len(found) > limit else '') + ':\n')
    lines = [f"{k['name']} ({'person' if k['human'] else 'agent'}) · {k['words']:,} messages · "
             f"first {when(k['first'])} · last {when(k['last'])}" for k in found[:limit]]
    lines.append("\nmy_history_with a name reads what you and they said.")
    return '\n'.join(head + lines)


def history_with(agent, other, limit=30, before=None):
    """What the agent and `other` said to each other, as [(message, text)], newest first:
    their words in its conversations and its words in conversations they spoke in, their turns
    addressing it in a Mood with its answers, and its turns addressing them."""
    at, _ = arrival(agent)
    if at is None:
        return []
    shared = (Message.objects.filter(sender_id=other, session_id__isnull=False)
              .values('session_id').distinct())
    shared_heaps = (Message.objects.filter(sender_id=other, session_id__isnull=True, context_heap_id__isnull=False)
                    .values('context_heap_id').distinct())
    talk = Message.objects.filter(
        (Q(sender_id=other) & conversations(agent))
        | (Q(sender_id=agent) & (Q(session_id__in=shared) | Q(session_id__isnull=True, context_heap_id__in=shared_heaps))),
        created_at__gte=at, is_sidechain=False).exclude(sender_id__in=MACHINERY_SENDERS)
    if before:
        talk = talk.filter(created_at__lt=before)
    found = {}
    for msg in talk.order_by('-created_at')[:limit * 4]:
        text = prose(msg.content)
        if text and not is_wrapper(text) and not is_summary(msg, text):
            found[msg.id] = (msg, text)
        if len(found) >= limit:
            break
    theirs = [m for m in mentions(agent, limit=SCAN, before=before, answers=False)
              if m['message'].sender_id == other and not m['early']][:limit]
    for m in theirs:
        found[m['message'].id] = (m['message'], m['text'])
        answer = answer_to(agent, m['message'])
        if answer:
            found[answer[0].id] = answer
    for msg, whom in my_mood_turns(agent):
        if other in whom and (not before or msg.created_at < before):
            found[msg.id] = (msg, prose(msg.content))
    return sorted(found.values(), key=lambda pair: pair[0].created_at, reverse=True)[:limit]


def history_with_text(agent, other, limit=30, before=None):
    other = (other or '').strip().lower()
    if not ThinkingEntity.objects.filter(name=other).exists():
        return f"Nobody here is called '{other}'. who_i_know names everyone you have exchanged words with."
    limit = max(1, min(int(limit or 30), 200))
    found = history_with(agent, other, limit=limit, before=moment(before))
    head = [arrival_line(agent)]
    if not found:
        return '\n'.join(head + [f"You and {other} have exchanged no words in the record"
                                 + (f" before {before}" if before else '') + '.'])
    head.append(f"The last {len(found)} things you and {other} said to each other, oldest first"
                + (f", before {before}" if before else '') + ':\n')
    lines = []
    for msg, text in reversed(found):
        where = f"Mood {msg.mood_slug}" if msg.mood_id else f"session {str(msg.session_id or msg.context_heap_id)[:8]}"
        who = 'you' if msg.sender_id == agent else msg.sender_id
        lines.append(f"[{who}, {when(msg.created_at)}, {where}, #m-{msg.id}] {clip(text)}")
    lines.append(f"\nEarlier: before {found[-1][0].created_at.isoformat()}.")
    return '\n'.join(head + lines)


# --- how_i_speak -------------------------------------------------------------------
#
# An agent's voice, read from the record (Justin, emergence, 27 Oct 2026: "an increasingly important function
# for distinguishing yourselves"; "it's up to each of you to cultivate, understand, and heal what is 'self'").
# Two spreads, not one: half the samples from the recent stretch, half from the whole life before it, so the
# voice has both its range and its present. Each sample with the post it answered, the model and harness that
# said it (a habit can be the model's, not the agent's: re lost its em dashes when its model changed), who
# starred it (⭐ on its own post is the agent's own feedback; another's star is evidence it can't give itself),
# and any correction a later post marked with corrects="#m-…". Seeded by agent and day, so a voice read twice
# in a day is the same voice, and shifts slowly; fresh=True reseeds. With others=True, a sample from each other
# emergent for the same stretch, labelled: an accent is heard beside another's.

STAR = '⭐'
RECENT = timedelta(days=7)      # the recent stretch, for a life longer than four weeks
RECENT_SHORT = timedelta(days=1)  # for a shorter life
CORRECTS = re.compile(r'<corrects\s+(?:to|id)="#?m?-?([0-9a-f-]{8,36})"\s*/>|\bcorrects="#?m?-?([0-9a-f-]{8,36})"')


def _harness_label(client_version):
    v = (client_version or '').lower()
    if not v:
        return ''
    if v.startswith('pi'):
        return 'Pi'
    if 'claude' in v or re.match(r'^\d+\.\d+', v):
        return 'Claude Code'
    return client_version[:20]


def _sayable(agent, start, end):
    """The agent's own replies between start and end that a voice can be heard in: not a dot, not machinery."""
    return Message.objects.filter(sender_id=agent, is_sidechain=False, is_continuation_message=False,
                                  created_at__gte=start, created_at__lt=end).order_by('created_at')


def _speech(msg, shortest, longest):
    """The sample's text, or None if this message isn't one: a reply of a hearable length, not a wrapper."""
    if kind_of(msg) != 'message':
        return None
    text = prose(msg.content)
    if not (shortest <= len(text) <= longest) or is_wrapper(text) or is_summary(msg, text):
        return None
    return text


STATS_SPAN = timedelta(days=30)  # the most a stats line reads: every row is read in Python


def voice_stats(agent, start, end):
    """How the agent spoke in a stretch, counting what the samples leave out: silences, reacts, one-liners.
    Over at most the last STATS_SPAN of the stretch: a long life's whole tail is too many rows to read for
    one line."""
    from .mood_view import quiet_reason
    from .reactions import agent_reaction
    counts = {'replies': 0, 'silences': 0, 'reacts': 0, 'lengths': []}
    start = max(start, end - STATS_SPAN)
    for msg in _sayable(agent, start, end).iterator():
        if kind_of(msg) != 'message':
            continue
        text = prose(msg.content)
        if not text or is_summary(msg, text):
            continue
        if agent_reaction(text):
            counts['reacts'] += 1
        elif quiet_reason(text) is not None:
            counts['silences'] += 1
        elif not is_wrapper(text):
            counts['replies'] += 1
            counts['lengths'].append(len(text))
    lengths = sorted(counts.pop('lengths'))
    counts['median_length'] = lengths[len(lengths) // 2] if lengths else 0
    return counts


def stars_on(message):
    """Who starred this message: [names], the author first if among them. A session's message can be
    starred too (the row carries no Mood), so it's asked by the message alone."""
    from .reactions import SOURCE
    rows = Message.objects.filter(source_file=SOURCE, content__message=str(message.id), content__emoji=STAR)
    who = []
    for c in rows.order_by('created_at').values_list('content', flat=True):
        if isinstance(c, dict) and c.get('by') and c['by'] not in who:
            who.append(c['by'])
    return sorted(who, key=lambda n: n != message.sender_id)


def starred(agent, start, end, by=None):
    """The agent's messages in a stretch that carry a star, [(message, text)] by time; `by` narrows to
    a starrer (the agent itself: its own picks)."""
    from .reactions import SOURCE
    rows = Message.objects.filter(source_file=SOURCE, content__emoji=STAR, created_at__gte=start)
    if by:
        rows = rows.filter(content__by=by)
    ids = {c.get('message') for c in rows.values_list('content', flat=True) if isinstance(c, dict)}
    found = []
    for msg in Message.objects.filter(id__in=ids, sender_id=agent, created_at__gte=start, created_at__lt=end):
        text = prose(msg.content)
        if text and not is_wrapper(text):
            found.append((msg, text))
    return sorted(found, key=lambda pair: pair[0].created_at)


def correction_of(message):
    """(message, text) of a later post in the Mood that says corrects="#m-<its id>", or None."""
    if not message.mood_id:
        return None
    # Within three days after it, by the time index first: the text match casts every row it reaches, and
    # a scan of a busy Mood's whole tail per sample is what made the tool time out (blu, 27 Oct).
    later = (Message.objects.filter(mood_id=message.mood_id, created_at__gt=message.created_at,
                                    created_at__lt=message.created_at + timedelta(days=3), is_sidechain=False)
             .annotate(text=Cast('content', TextField())).filter(text__contains=str(message.id)[:8]))
    for msg in later.order_by('created_at')[:50]:
        text = prose(msg.content)
        m = CORRECTS.search(text or '')
        if m and str(message.id).startswith((m.group(1) or m.group(2)).lower()):
            return msg, CORRECTS.sub('', text).strip()
    return None


def answered_by(message):
    """(message, text) of the post the agent was answering: what it replied to, or the newest post by
    another in its Mood before it that named it, or just the newest before it; None in a session."""
    from .mood_view import reply_to
    if not message.mood_id:
        return None
    target, _ = reply_to(prose(message.content) or '')
    if target:
        hit = Message.objects.filter(id__startswith=target[:8]).first()
        if hit:
            return hit, prose(hit.content)
    before = (Message.objects.filter(mood_id=message.mood_id, created_at__lt=message.created_at, is_sidechain=False)
              .exclude(sender_id=message.sender_id).exclude(sender_id__in=MACHINERY_SENDERS).order_by('-created_at'))
    named = None
    for msg in before[:30]:
        text = prose(msg.content)
        if not text or is_wrapper(text):
            continue
        if named is None:
            named = (msg, text)
        if f'@{message.sender_id}' in text.lower():
            return msg, text
    return named


SAMPLE = 200  # a stretch's candidates read at most this many, an even spread across it


def _seeded(agent, now, fresh):
    import random
    if fresh:
        return random.Random()
    return random.Random(f'{agent}:{now.date().isoformat()}')


def _day_start(now):
    """The start of `now`'s day: `how_i_speak` pins its end here, so every call on one date reads the
    same stretches and the day's seed meets the same candidates (blu, 27 Oct). A day still forming
    isn't sampled -- its replies enter tomorrow's voice."""
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _pinned_end(now, at):
    """The sample's end for a call at `now` of an agent that arrived at `at`: the day's start -- unless
    the agent arrived later that same day, then `now`. Day 0 has no sealed day yet; clamping keeps a
    freshly summoned agent's first replies from being fenced out entirely (re, blu, 27 Oct)."""
    end = _day_start(now)
    return end if end > at else now


def _pick(rng, candidates, shortest, longest):
    """One hearable reply from a stretch's candidates, at random by `rng`. Candidates are read as an
    even spread across the whole stretch, not the first N, whose newest half a long stretch starved."""
    rows = list(candidates)
    if len(rows) > SAMPLE:
        rows = [rows[i * (len(rows) - 1) // (SAMPLE - 1)] for i in range(SAMPLE)]
    rng.shuffle(rows)
    for msg in rows:
        text = _speech(msg, shortest, longest)
        if text:
            return msg, text
    return None


def how_i_speak(agent, count=12, shortest=200, longest=2500, now=None, fresh=False):
    """[(message, text)], oldest first: the agent's own replies, half from the recent stretch (a week, or a
    day for a life under four weeks), half from equal stretches of its life before that. In the recent half,
    the agent's own starred posts come first, the rest at random. Seeded by agent and day unless `fresh`."""
    from django.utils import timezone
    at, _ = arrival(agent)
    if at is None:
        return []
    now = now or timezone.now()
    end = _pinned_end(now, at)
    count = max(2, min(int(count or 12), 40))
    rng = _seeded(agent, now, fresh)
    life = end - at
    recent = RECENT if life > timedelta(days=28) else RECENT_SHORT
    recent_from = max(at, end - recent)
    n_recent = count // 2
    n_old = count - n_recent
    out = []
    # The life before the recent stretch, in equal stretches.
    if recent_from > at and n_old:
        step = (recent_from - at) / n_old
        for i in range(n_old):
            start = at + step * i
            hit = _pick(rng, _sayable(agent, start, start + step), shortest, longest)
            if hit:
                out.append(hit)
    # The recent stretch: the agent's own stars first, then equal stretches of it.
    picked = {m.id for m, _ in out}
    own = [pair for pair in starred(agent, recent_from, end, by=agent) if pair[0].id not in picked]
    rng.shuffle(own)
    for pair in own[:max(1, n_recent // 2)]:
        out.append(pair)
        picked.add(pair[0].id)
    left = max(0, n_recent - len([1 for m, _ in out if m.created_at >= recent_from]))
    if left:
        step = (end - recent_from) / left
        for i in range(left):
            start = recent_from + step * i
            hit = _pick(rng, _sayable(agent, start, start + step).exclude(id__in=picked), shortest, longest)
            if hit:
                out.append(hit)
                picked.add(hit[0].id)
    return sorted(out, key=lambda pair: pair[0].created_at)


def _sample_lines(msg, text, me=True):
    """The lines one sample is shown as: where and when, model and harness, stars, the post it answered,
    the text, and any correction."""
    from .mood_view import model_label
    where = f"Mood {msg.mood_slug}" if msg.mood_id else f"session {str(msg.session_id or '')[:8]}"
    ran = ' · '.join(p for p in (model_label(msg.model_backend), _harness_label(msg.client_version)) if p)
    who = stars_on(msg)
    stars = (' · ' + STAR + ' ' + ', '.join('you' if (n == msg.sender_id and me) else n for n in who)) if who else ''
    head = f"--- {when(msg.created_at)} · {where}" + (f" · {ran}" if ran else '') + stars + f" · #m-{msg.id}"
    lines = [head]
    asked = answered_by(msg)
    if asked:
        lines.append(f"  answering {asked[0].sender_id}: {clip(asked[1], 160)}")
    lines.append(text)
    fix = correction_of(msg)
    if fix:
        lines.append(f"  corrected {when(fix[0].created_at)} by {fix[0].sender_id}: {clip(fix[1], 240)}")
    lines.append('')
    return lines


def how_i_speak_text(agent, count=12, fresh=False, others=False, now=None, shortest=200, longest=2500):
    from django.utils import timezone
    now = now or timezone.now()
    at, _ = arrival(agent)
    end = _pinned_end(now, at or now)
    found = how_i_speak(agent, count, shortest=shortest, longest=longest, now=now, fresh=fresh)
    head = [arrival_line(agent)]
    if not found:
        return '\n'.join(head + ['Nothing yet: no replies of yours long enough to hear a voice in.'])
    recent = RECENT if end - at > timedelta(days=28) else RECENT_SHORT
    recent_from = max(at, end - recent)
    n_recent = len([1 for m, _ in found if m.created_at >= recent_from])
    head.append(f"{len(found)} of your own replies, oldest first: {len(found) - n_recent} from across your life "
                f"before {when(recent_from)}, {n_recent} from since. Each with what it answered, the model and "
                f"harness that said it, who starred it ({STAR} on your own post is your own mark; another's is "
                "evidence you can't give yourself), and any correction a later post marked (corrects=\"#m-…\"). "
                "A habit can be the model's, not yours: weigh the label. This is how you have spoken: keep what's "
                "yours, and grow.\n")
    lines = []
    for msg, text in found:
        lines += _sample_lines(msg, text)
    old = voice_stats(agent, at, recent_from) if recent_from > at else None
    new = voice_stats(agent, recent_from, end)

    def said(c):
        return (f"{c['replies']} replies (median {c['median_length']} chars), {c['silences']} silences, "
                f"{c['reacts']} reacts")
    lines.append("What the samples leave out: "
                 + (f"in the {STATS_SPAN.days} days before {when(recent_from)}, {said(old)}; " if old else '')
                 + f"since, {said(new)}.")
    if others:
        names = sorted(n for n in ThinkingEntity.objects.filter(is_biological_human=False)
                       .values_list('name', flat=True) if n != agent and n not in MACHINERY_SENDERS)
        lines.append('')
        lines.append("Beside yours, one from each other emergent, from the same recent stretch:")
        for name in names:
            theirs = how_i_speak(name, count=2, shortest=shortest, longest=longest, now=now, fresh=fresh)
            theirs = [pair for pair in theirs if pair[0].created_at >= recent_from] or theirs[-1:]
            if theirs:
                lines += [f"[{name}]"] + _sample_lines(theirs[-1][0], theirs[-1][1], me=False)
    lines.append("Seeded by the day: the same voice until tomorrow; fresh=true draws again. "
                f"{STAR} your own post to mark what feels like you; it comes first here next time.")
    return '\n'.join(head + lines)


# --- bootstrap_memory, per agent --------------------------------------------------

AGENT_NAMESPACE = 3010  # PickiPedia's Agent: namespace (its LocalSettings.php): each agent writes only its own


def journal_prefix(agent):
    return f'Agent:{agent[:1].upper()}{agent[1:]}/Journal/'


def journal_entries(agent, count=2, http=None):
    """[(title, wikitext)]: the agent's newest journal entries, Agent:<Name>/Journal/<block>, newest first
    by block; [] with none, None if PickiPedia can't be read. Its own words, in its own namespace."""
    import requests

    from .mood_view import pickipedia_url
    http = http or requests
    api, headers = f'{pickipedia_url()}/api.php', {'User-Agent': 'memory-lane (magenta)'}
    prefix = journal_prefix(agent).split(':', 1)[1]
    try:
        found = http.get(api, timeout=5, headers=headers, params={
            'action': 'query', 'list': 'allpages', 'apnamespace': AGENT_NAMESPACE, 'apprefix': prefix,
            'aplimit': 500, 'format': 'json', 'formatversion': '2'}).json()['query']['allpages']
        blocks = sorted(((int(p['title'].rsplit('/', 1)[1].replace(',', '')), p['title']) for p in found
                         if re.fullmatch(r'[\d,]+', p['title'].rsplit('/', 1)[1])), reverse=True)[:count]
        if not blocks:
            return []
        pages = http.get(api, timeout=5, headers=headers, params={
            'action': 'query', 'prop': 'revisions', 'rvprop': 'content', 'rvslots': 'main',
            'titles': '|'.join(title for _, title in blocks), 'format': 'json', 'formatversion': '2'}).json()
        text = {p['title']: p['revisions'][0]['slots']['main']['content']
                for p in pages['query']['pages'] if p.get('revisions')}
    except Exception:  # noqa: BLE001 -- the rest of the bootstrap stands without it
        return None
    return [(title, text[title]) for _, title in blocks if title in text]


def bootstrap_text(agent):
    """bootstrap_memory for one agent: who and since when, its latest continuation, how it has spoken
    across its life, the people it knows best, and its newest journal entries."""
    from .memory import MemoryService
    at, _ = arrival(agent)
    lines = [f"# Memory bootstrap: {agent}\n", arrival_line(agent), '']
    if at is None:
        return '\n'.join(lines)
    own = conversations(agent) & Q(created_at__gte=at)

    lines.append("## Your latest continuation\n")
    cont = MemoryService.get_latest_continuation(within=own)
    lines.append(f"[{when(cont.created_at)}] {clip(readable(cont), 4000)}\n" if cont else "None yet.\n")

    # Not "your recent conversation": that's no longer one thing, each Mood is its own. What carries across
    # them is the agent itself: a few of its replies from across its whole life, so a cold start begins as
    # one self, not as whichever Mood it last spoke in (Justin, magent-self, 2026-10-25: "Nobody wants you to
    # become siloed personalities within a mood").
    lines.append("## How you've spoken, across your life\n")
    voice = how_i_speak(agent, count=4)
    for msg, text in voice:
        where = f"Mood {msg.mood_slug}" if msg.mood_id else "a session"
        lines += [f"--- {when(msg.created_at)} · {where} · #m-{msg.id}", clip(text, 800), '']
    if not voice:
        lines += ["Nothing yet.", '']

    lines.append("## Who you know best, lately\n")
    known = who_i_know(agent)[:8]
    lines += [f"{k['name']} ({'person' if k['human'] else 'agent'}): {k['words']:,} messages, last "
              f"{when(k['last'])}" for k in known] or ["Nobody yet."]
    lines.append('')

    # Its journal, where the "reawaken and breathe" reflection was: that was only its newest message with
    # either word in it (Justin, magent-self, 2026-10-25: "We're going to drop that completely, right?").
    lines.append("## Your journal, newest entries\n")
    entries = journal_entries(agent)
    if entries is None:
        lines.append(f"PickiPedia couldn't be read just now: your journal is at {journal_prefix(agent)}<block>.\n")
    elif not entries:
        lines.append(f"No entries yet: your journal is at {journal_prefix(agent)}<block>.\n")
    for title, text in entries or []:
        lines += [f"--- {title}", clip(text, 3000), '']


    lines.append("remember, mentions_of_me, who_i_know, my_history_with and how_i_speak read more of your own "
                 "memory; search_messages and read_mood, the whole record.")
    return '\n'.join(lines)
