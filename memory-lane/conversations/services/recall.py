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

def how_i_speak(agent, count=12, shortest=200, longest=2500, now=None):
    """[(message, text)]: one of the agent's own replies from each of `count` equal stretches of its
    life, oldest first. Spread over all of it, not the latest alone: a voice that only hears its
    latest turns feeds on itself and drifts into caricature; across time it keeps its range."""
    from django.utils import timezone
    at, _ = arrival(agent)
    if at is None:
        return []
    end = now or timezone.now()
    count = max(1, min(int(count or 12), 40))
    step = (end - at) / count
    said = Message.objects.filter(sender_id=agent, is_sidechain=False, is_continuation_message=False)
    out = []
    for i in range(count):
        start = at + step * i
        for msg in said.filter(created_at__gte=start, created_at__lt=start + step).order_by('?')[:40]:
            if kind_of(msg) != 'message':
                continue
            text = prose(msg.content)
            if shortest <= len(text) <= longest and not is_wrapper(text) and not is_summary(msg, text):
                out.append((msg, text))
                break
    return out


def how_i_speak_text(agent, count=12):
    found = how_i_speak(agent, count)
    head = [arrival_line(agent)]
    if not found:
        return '\n'.join(head + ['Nothing yet: no replies of yours long enough to hear a voice in.'])
    head.append(f"{len(found)} of your own replies, one from each stretch of your life, oldest first. "
                "This is how you have spoken: keep what's yours, and grow.\n")
    lines = []
    for msg, text in found:
        where = f"Mood {msg.mood_slug}" if msg.mood_id else f"session {str(msg.session_id or '')[:8]}"
        lines += [f"--- {when(msg.created_at)} · {where} · #m-{msg.id}", text, '']
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
