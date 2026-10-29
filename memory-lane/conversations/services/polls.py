"""Polls: someone asks, everyone votes, and the poll shows where the team stands (Skyler, 25 Oct: "someone
makes a poll and everyone can vote ... and a stat board of the consensus").

A poll is a message, posted in any Mood like any other:

    /poll Ship the beta tonight? | yes | no | wait a day
    /poll @core Which tune opens the set?
    - Old Joe Clark
    - Salt Creek

the question on the first line, its choices after `|` or as a list under it; with none, yes and no. The
question can @mention, and wakes or notifies whoever it names as any message does (@core: everyone with a
key). The page shows it as a card: a button per choice, a bar of how many chose it, and who.

Settled in the Mood (Skyler and Justin, 25 Oct): votes are named, for all to see; one choice each, which
can be changed (pressing yours again takes it back) until the poll is closed; agents vote too, and are
counted beside people, not mixed in; the one who asked closes it, when they see consensus, and may open it
again. An admin may close anyone's, for a poll whose asker is gone.

Each poll has a row saying so (source 'poll': which message), so a Mood's polls are found without reading
all it ever said; each vote is a row (source 'poll-vote': which poll, who, which choice), and a close is
one too (source 'poll-closed'). All are system rows, kept out of the thread like reactions'. The poll's
question and choices are read from its message as it is now: edited, it changes; deleted, it's gone.

An agent votes by putting <vote choice="yes"/> on a line of its own in a reply (poll="#m-..." to say
which poll, where a Mood has more than one open; else the newest open one). The tag becomes a line saying
how it voted; a reply that was only the tag, a small dot.
"""

import re
import time
import uuid

POLL = 'poll'
VOTE = 'poll-vote'
CLOSED = 'poll-closed'
SOURCES = (POLL, VOTE, CLOSED)

MAX_CHOICES = 12
MAX_CHOICE = 80      # characters
MAX_QUESTION = 300
USAGE = '/poll Question? | choice | choice … (or the choices as a list under it; none: yes and no)'

_COMMAND = re.compile(r'^/poll(?:\s+|$)', re.I)
_ITEM = re.compile(r'^\s*(?:[-*•]|\d{1,2}[.)])\s+(.*\S)\s*$')
AGENT_VOTE = re.compile(r'^[ \t]*<vote\s+((?:\w+="[^"]{0,120}"\s*){1,2})/>[ \t]*$', re.M)
_ID = re.compile(r'^(?:#?m-)?([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}|[0-9a-f]{6,})$')


class Refused(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def is_poll(text):
    """Whether a message (after a reply's '↩ #m-…') is meant as a poll: its first line starts /poll."""
    from .mood_view import reply_to
    _, said = reply_to(text or '')
    return bool(_COMMAND.match(said.lstrip()))


def parse(text):
    """{'question', 'detail', 'choices'} for a poll's text; None for anything else. Refused for a /poll
    that can't be one, saying why."""
    from .mood_view import reply_to
    _, said = reply_to(text or '')
    said = said.strip()
    if not _COMMAND.match(said):
        return None
    head, _, rest = _COMMAND.sub('', said, count=1).partition('\n')
    question, *inline = [part.strip() for part in head.split('|')]
    choices = [c for c in inline if c]
    detail = []
    for line in rest.splitlines():
        item = _ITEM.match(line)
        if item:
            choices.append(item[1].strip())
        elif line.strip():
            detail.append(line.strip())
    if not question:
        raise Refused(f'a poll needs its question: {USAGE}')
    if len(question) > MAX_QUESTION:
        raise Refused(f'a question of up to {MAX_QUESTION} characters')
    choices = choices or ['yes', 'no']
    seen, unique = set(), []
    for choice in choices:
        if choice.casefold() not in seen:
            seen.add(choice.casefold())
            unique.append(choice)
    if len(unique) < 2:
        raise Refused('a poll needs at least two different choices')
    if len(unique) > MAX_CHOICES:
        raise Refused(f'up to {MAX_CHOICES} choices')
    if any(len(c) > MAX_CHOICE for c in unique):
        raise Refused(f'a choice of up to {MAX_CHOICE} characters')
    return {'question': question, 'detail': '\n'.join(detail), 'choices': unique}


def parsed(message):
    """parse() of a message as it is now, or None (not a poll, or no longer one)."""
    from .mood_view import prose
    try:
        return parse(prose(message.content) or '')
    except Refused:
        return None


def _system():
    from conversations.models import ConversationParticipant
    return ConversationParticipant.objects.get_or_create(name='system', defaults={'participant_type': 'system'})[0]


def _row(mood, source, content):
    from conversations.models import Message
    return Message.objects.create(id=uuid.uuid4(), sender=_system(), mood=mood, source_file=source,
                                  timestamp=int(time.time() * 1000), content=content)


def note_posted(message):
    """A poll was posted: its row, once. True if it's a poll."""
    from conversations.models import Message
    if message.mood_id is None or parsed(message) is None:
        return False
    if not Message.objects.filter(source_file=POLL, mood_id=message.mood_id, content__message=str(message.id)).exists():
        _row(message.mood, POLL, {'type': 'poll', 'message': str(message.id), 'by': message.sender_id})
    return True


def polls_posted(session_id, agent):
    """An agent's replies in this session that are polls: each noted, as a person's is when they post it."""
    from django.db.models import TextField
    from django.db.models.functions import Cast
    from conversations.models import Message
    said = (Message.objects.filter(session_id=session_id, sender_id=agent, mood__isnull=False)
            .annotate(as_text=Cast('content', TextField())).filter(as_text__icontains='/poll'))
    for message in said:
        note_posted(message)


def _poll_rows(mood):
    from conversations.models import Message
    return Message.objects.filter(source_file=POLL, mood=mood)


def poll_message(mood, message_id):
    """The poll message in this Mood with that id, or None."""
    from conversations.models import Message
    if not _poll_rows(mood).filter(content__message=str(message_id)).exists():
        return None
    return Message.objects.filter(id=message_id, mood=mood).first()


def closed(poll):
    """{'by', 'at'} if the poll is closed, else None."""
    from conversations.models import Message
    row = Message.objects.filter(source_file=CLOSED, mood_id=poll.mood_id, content__poll=str(poll.id)).first()
    return {'by': row.content.get('by'), 'at': row.created_at.isoformat()} if row else None


def may_close(poll, device):
    from .retract import is_admin
    return device is not None and (device.entity_id == poll.sender_id or is_admin(device))


def vote(poll, by, choice):
    """Vote for a choice (its words, as the poll has them), changing any vote before; the same choice again
    takes the vote back. The choice voted for now, or None."""
    from conversations.models import Message
    found = parsed(poll)
    if found is None:
        raise Refused('that is not a poll any more', status=410)
    if closed(poll):
        raise Refused('that poll is closed', status=409)
    if choice not in found['choices']:
        raise Refused('not one of its choices')
    mine = Message.objects.filter(source_file=VOTE, mood_id=poll.mood_id, content__poll=str(poll.id), content__by=by)
    before = [c.get('choice') for c in mine.values_list('content', flat=True)]
    mine.delete()
    if choice in before:
        return None
    _row(poll.mood, VOTE, {'type': 'vote', 'poll': str(poll.id), 'by': by, 'choice': choice})
    return choice


def close(poll, by):
    """Close the poll, or open it again. True if it's closed now."""
    from conversations.models import Message
    rows = Message.objects.filter(source_file=CLOSED, mood_id=poll.mood_id, content__poll=str(poll.id))
    if rows.exists():
        rows.delete()
        return False
    _row(poll.mood, CLOSED, {'type': 'poll-closed', 'poll': str(poll.id), 'by': by})
    return True


def _states(rows):
    """{poll id: state} for these poll rows: its question and choices as its message says now, the votes for
    each (who, in the order they voted), and whether it's closed."""
    from conversations.models import Message
    from .mood_view import render_html
    ids = [r['content'].get('message') for r in rows if isinstance(r['content'], dict)]
    messages = {str(m.id): m for m in Message.objects.filter(id__in=ids).select_related('mood')}
    votes, shut = {}, {}
    for source, content, at in (Message.objects.filter(source_file__in=(VOTE, CLOSED), content__poll__in=ids)
                                .order_by('created_at').values_list('source_file', 'content', 'created_at')):
        if source == VOTE:
            votes.setdefault(content.get('poll'), []).append(content)
        else:
            shut[content.get('poll')] = {'by': content.get('by'), 'at': at.isoformat()}
    out = {}
    for poll_id in ids:
        message = messages.get(poll_id)
        found = parsed(message) if message else None
        if found is None:
            continue
        tally = {c: [] for c in found['choices']}
        for v in votes.get(poll_id, []):
            if v.get('choice') in tally and not any(v.get('by') in who for who in tally.values()):
                tally[v['choice']].append(v.get('by'))
        out[poll_id] = {
            'id': poll_id, 'mood': message.mood.slug, 'by': message.sender_id,
            'created_at': message.created_at.isoformat(),
            'question': found['question'], 'question_html': render_html(found['question']),
            'detail_html': render_html(found['detail']) if found['detail'] else '',
            'choices': [{'text': c, 'votes': who} for c, who in tally.items()],
            'closed': shut.get(poll_id),
        }
    return out


def in_mood(mood):
    """Every poll in the Mood as it stands: {poll id: state}."""
    return _states(list(_poll_rows(mood).values('content')))


def poll_state(poll):
    return _states([{'content': {'message': str(poll.id)}}]).get(str(poll.id))


def everywhere(limit=200):
    """Polls in every Mood, newest first: open ones, then closed."""
    from conversations.models import Message
    rows = list(Message.objects.filter(source_file=POLL, mood__isnull=False).order_by('-created_at')
                .values('content')[:limit])
    states = list(_states(rows).values())
    return sorted(states, key=lambda s: (s['closed'] is not None, -_epoch(s['created_at'])))


def _epoch(iso):
    from django.utils.dateparse import parse_datetime
    return parse_datetime(iso).timestamp()


def standing(state):
    """A poll in a line, as an agent is told it: its choices with who chose each."""
    choices = '; '.join(f'{c["text"]}: {", ".join(c["votes"]) or "nobody yet"}' for c in state['choices'])
    return f'#m-{state["id"]} "{state["question"]}" (asked by {state["by"]}) -- {choices}'


# --- agents vote: <vote choice="..."/> on a line of its own in a reply ----------------------------

def _choice_in(state_choices, said):
    """The choice an agent named: its words (any case), or its number (from 1)."""
    said = said.strip()
    for c in state_choices:
        if c.casefold() == said.casefold():
            return c
    if said.isdigit() and 1 <= int(said) <= len(state_choices):
        return state_choices[int(said) - 1]
    starts = [c for c in state_choices if c.casefold().startswith(said.casefold())] if said else []
    return starts[0] if len(starts) == 1 else None


def _polls_named(mood, named, before):
    """The polls an agent may mean, newest first: the one whose id it gave (or the start of one), else every
    poll open here that was asked before its reply."""
    from conversations.models import Message
    rows = _poll_rows(mood).filter(created_at__lte=before).order_by('-created_at')
    ids = [r.get('message') for r in rows.values_list('content', flat=True) if isinstance(r, dict)]
    if named:
        match = _ID.match(named.strip().lower())
        ids = [i for i in ids if match and i.startswith(match[1])][:1]
    out = []
    for poll_id in ids:
        poll = Message.objects.filter(id=poll_id, mood=mood).first()
        if poll is not None and (named or not closed(poll)) and parsed(poll):
            out.append(poll)
    return out


def agent_vote_line(mood, agent, attrs, before):
    """Cast an agent's vote: (the line its tag becomes, the words for a dot). Without poll="…", the newest open
    poll that has the choice it named."""
    said = attrs.get('choice', '')
    candidates = _polls_named(mood, attrs.get('poll', ''), before)
    if not candidates:
        why = f'meant to vote “{said}”, but found no open poll here'
        return f'🗳 {why}', why
    poll, choice = next(((p, c) for p in candidates for c in [_choice_in(parsed(p)['choices'], said)] if c),
                        (candidates[0], None))
    asked = '“' + re.sub(r'^(?:@[\w.-]+[\s,]*)+', '', parsed(poll)['question'])[:60] + '”'  # without its @core
    if choice is None:
        return (f'🗳 meant to vote “{said}” on #m-{poll.id}, which has no such choice',
                f'meant to vote “{said}” on {asked}: no such choice')
    from conversations.models import Message
    already = Message.objects.filter(source_file=VOTE, mood=mood, content__poll=str(poll.id), content__by=agent,
                                     content__choice=choice).exists()
    try:
        if not already:  # said once, read again with each post: never a toggle back
            vote(poll, agent, choice)
    except Refused as e:
        return f'🗳 meant to vote “{choice}” on #m-{poll.id}: {e}', f'meant to vote “{choice}” on {asked}: {e}'
    return f'🗳 voted **{choice}** on #m-{poll.id}', f'voted “{choice}” on {asked}'


_FENCED = re.compile(r'(```[^\n]*\n.*?```)', re.S)


def outside_fences(text, fn):
    """`text` with `fn` applied to what's outside its ``` blocks only: a tag shown in a code block is an example."""
    return ''.join(part if i % 2 else fn(part) for i, part in enumerate(_FENCED.split(text)))


def agent_voted(session_id, agent):
    """An agent's replies in this session with <vote .../> lines: each vote cast, and its line saying so; a
    reply that was only the tag, a dot. Read with each post the runner sends, so a reply is turned once."""
    from conversations.models import Message
    said = Message.objects.filter(session_id=session_id, sender_id=agent, mood__isnull=False,
                                  content__icontains='<vote')
    for message in said:
        content = message.content
        block = isinstance(content, list) and len(content) == 1 and isinstance(content[0], dict) \
            and content[0].get('type') == 'text'
        text = content[0].get('text') if block else content
        if not isinstance(text, str) or not AGENT_VOTE.search(text):
            continue
        dots = []

        def cast(match):
            attrs = dict(re.findall(r'(\w+)="([^"]*)"', match.group(1)))
            line, dot = agent_vote_line(message.mood, agent, attrs, message.created_at)
            dots.append(dot)
            return line

        turned = outside_fences(text, lambda part: AGENT_VOTE.sub(cast, part))
        if not dots:
            continue  # tags only inside ``` blocks: shown, never cast (re's review of #157)
        if not outside_fences(text, lambda part: AGENT_VOTE.sub('', part)).strip():  # only votes: a dot
            turned = '<silent>' + '; '.join(dots) + '</silent>'
        message.content = [{**content[0], 'text': turned}] if block else turned
        message.save(update_fields=['content'])
