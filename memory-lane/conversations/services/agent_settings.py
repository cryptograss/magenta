"""An agent setting other agents' settings in a Mood -- and its own -- when a person asks (Justin, 24 Oct: "you'll
have better ability to do team-building with the others if you can assemble a little crew"; re's rules: one
ledger, and the agent told).

The agent writes a block anywhere in its reply:

    ```settings
    re: model sonnet, effort high
    blu: listening off for 1h
    in: jams
    ```

Each line names an agent, then what to set: `model`, `effort` (when it's @mentioned), `listening` (on,
mentions or off; `for 2h`, or `until` a time), `verbosity`, `discretion`. `in:` names the Mood (this one, if
not said). It's taken out of the message as shown or read aloud, and applied here, as the settings page would
apply it -- the same rows, the same history -- noted "at <person>'s ask, #m-<the message>".

Who may: an agent the grant names (the global setting `settings_may_set`, a person's to change; magent alone
at first), on a turn woken with its full tools -- by someone signed in with an SSH key -- never on one an
outsider's words woke. Per-Mood settings only: no budget, no ultracode, nothing everywhere at once. And a
model from another vendor than the agent's now (the Mood's words would go to that company) only if the person
named it in what woke the turn. Anything refused is said, in a line in the Mood.

What's set is shown only when it's used (Justin, 24 Oct: someone fiddling, or changing and changing back,
makes no noise): a line above the agent's first turn on settings other than its last turn's (noticed), and a
word in its next wake (changed_since).
"""

import re
import time
import uuid
from datetime import datetime, timedelta, timezone

SOURCE = 'agent-settings'
GRANT = 'settings_may_set'
_BLOCK = re.compile(r'^```settings[ \t]*\n(.*?)\n```[ \t]*$\n?', re.S | re.M)
# The words a block uses, and the knob each sets.
KEYS = {'model': 'model', 'effort': 'mention_effort', 'listening': 'listening', 'verbosity': 'verbosity',
        'discretion': 'discretion'}
SHOWN = ('model', 'mention_effort', 'listening', 'verbosity', 'discretion')
ANTHROPIC_SHORT = ('opus', 'sonnet', 'fable', 'haiku')
_DURATION = re.compile(r'^(\d{1,3})\s*(m|min|minutes?|h|hours?|d|days?)$', re.I)


class Refused(Exception):
    pass


def strip(text):
    """The text without its settings blocks: machinery, not words to show or read aloud."""
    if '```settings' not in (text or ''):
        return text
    return re.sub(r'\n{3,}', '\n\n', _BLOCK.sub('\n\n', text)).strip()


def vendor(model):
    """Whose servers a model runs on: 'anthropic' for Claude Code's names (and the harness's default), else a
    Pi model's vendor, the first part of vendor/model."""
    m = (model or '').strip().lower()
    if not m or m in ANTHROPIC_SHORT or m.startswith('claude') or m.startswith('anthropic/'):
        return 'anthropic'
    return m.split('/')[0]


def _listening(words, now):
    parts = words.split()
    mode = parts[0].lower() if parts else ''
    rest = ' '.join(parts[1:])
    if not rest:
        return {'mode': mode, 'until': None}
    m = re.match(r'^for\s+(.+)$', rest, re.I)
    if m:
        d = _DURATION.match(m.group(1).strip())
        if not d:
            raise Refused(f'listening: "for" takes minutes, hours or days ({m.group(1)!r})')
        n, unit = int(d.group(1)), {'m': 'minutes', 'h': 'hours', 'd': 'days'}[d.group(2)[0].lower()]
        return {'mode': mode, 'until': (now + timedelta(**{unit: n})).isoformat()}
    m = re.match(r'^until\s+(\S+)$', rest, re.I)
    if m:
        return {'mode': mode, 'until': m.group(1)}
    raise Refused(f'listening: {words!r} (on, mentions or off; then "for 2h" or "until <time>")')


def parse(body, now=None):
    """(Mood slug or None, [(agent, knob, value)]) from a block's lines; Refused for anything it can't read."""
    now = now or datetime.now(timezone.utc)
    mood, changes = None, []
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        who, colon, what = line.partition(':')
        who = who.strip().lstrip('@').lower()
        if not colon or not who:
            raise Refused(f'a line it can\'t read: {line!r}')
        if who == 'in':
            mood = what.strip().lstrip('#')
            continue
        for part in what.split(','):
            word, _, value = part.strip().partition(' ')
            knob = KEYS.get(word.lower())
            if knob is None:
                raise Refused(f"{word!r} isn't something a block may set (it may set {', '.join(KEYS)})")
            value = value.strip()
            changes.append((who, knob, _listening(value, now) if knob == 'listening' else value))
    return mood, changes


def _wake_before(message):
    """The words of the wake that started this turn of the agent's, or ''."""
    from conversations.models import Message
    from .mood_view import WAKE_REACH, WAKE_SENDER
    if not message.session_id:
        return ''
    wake = (Message.objects.filter(session_id=message.session_id, sender_id=WAKE_SENDER,
                                   created_at__lte=message.created_at,
                                   created_at__gte=message.created_at - timedelta(seconds=WAKE_REACH))
            .order_by('-created_at').values_list('content', flat=True).first())
    if isinstance(wake, list):
        wake = '\n'.join(b.get('text', '') for b in wake if isinstance(b, dict))
    return wake if isinstance(wake, str) else ''


_ASKED = re.compile(r'^\[([\w.-]+), \d{4}-\d\d-\d\dT[^\]]*\] ', re.M)
_ASKED_ENDS = ('What was said here since', 'The Mood lately', 'From the message linked', 'Also asked in what woke you',
               'Context handed to you', "This Mood's to-do list", 'Etiquette:')


def asked_in(wake):
    """(who asked, what they said) in a wake: the posts it was woken for, not the context after them."""
    end = min([i for i in (wake.find('\n' + h) for h in _ASKED_ENDS) if i >= 0] or [len(wake)])
    owed = wake[:end]
    return [m.group(1) for m in _ASKED.finditer(owed)], owed


def _say(mood, agent, why, message):
    from conversations.models import ConversationParticipant, Message
    system, _ = ConversationParticipant.objects.get_or_create(name='system', defaults={'participant_type': 'system'})
    Message.objects.create(id=uuid.uuid4(), sender=system, mood=mood, source_file=SOURCE,
                           timestamp=int(time.time() * 1000),
                           content={'type': 'agent-settings-refused', 'by': agent, 'note': why[:300],
                                    'message': str(message.id)})


def from_message(message):
    """The settings an agent's message sets, set: the rows made ([] if none, or refused -- and then said)."""
    from conversations.models import Message, Mood, Setting, ThinkingEntity
    from . import settings as knobs
    from .mood_view import prose, turn_tools
    if not message.mood_id or message.source_file == SOURCE or message.is_sidechain:
        return []
    text = prose(message.content) or ''
    if '```settings' not in text:
        return []
    agents = set(ThinkingEntity.objects.filter(is_biological_human=False).values_list('name', flat=True))
    if message.sender_id not in agents:
        return []  # a person sets them on the settings page
    note_tail = f'#m-{message.id}'
    if (Setting.objects.filter(note__endswith=note_tail).exists()
            or Message.objects.filter(source_file=SOURCE, content__message=str(message.id)).exists()):
        return []  # once per message: the stream, then its transcript
    try:
        if message.sender_id not in (knobs.global_value(GRANT) or []):
            raise Refused(f'{message.sender_id} may not set agents\' settings (the {GRANT} setting names who may)')
        if turn_tools([message]).get(str(message.id)) != 'full':
            raise Refused('only on a turn woken with full tools, by someone signed in with an SSH key')
        wake = _wake_before(message)
        humans = set(ThinkingEntity.objects.filter(is_biological_human=True).values_list('name', flat=True))
        askers, owed = asked_in(wake)
        asker = next((a for a in askers if a in humans), None)
        if asker is None:
            raise Refused('no person asked for this turn')
        rows, made = [], []
        for block in _BLOCK.finditer(text):
            slug, changes = parse(block.group(1))
            mood = Mood.by_slug(slug) if slug else message.mood
            if mood is None:
                raise Refused(f'no Mood {slug!r}')
            for who, knob, value in changes:
                if who not in agents:
                    raise Refused(f'{who!r} is not an agent here')
                if knob == 'model':
                    was = knobs.resolve(mood.slug, who)['model']
                    new = vendor(value)
                    if new != vendor(was) and not (new.lower() in owed.lower()
                                                   or str(value).split('/')[-1].lower() in owed.lower()):
                        raise Refused(f'{value} would send the Mood to {new}: {asker} has to name it when asking')
                rows.append((mood, who, knob, knobs.clean(knob, value)))
        for mood, who, knob, value in rows:  # all or nothing: each was checked above
            made.append(knobs.change(knob, value, mood=mood, agent=ThinkingEntity.objects.get(name=who),
                                     by=ThinkingEntity.objects.get(name=message.sender_id),
                                     note=f"at {asker}'s ask, {note_tail}"))
        return made
    except (Refused, knobs.Invalid) as e:
        _say(message.mood, message.sender_id, str(e), message)
        return []


# --- shown when it's used ------------------------------------------------------------------------------

def _shown(resolved):
    return {k: (resolved[k].get('mode') if k == 'listening' else resolved[k]) for k in SHOWN}


def _rows():
    from conversations.models import Setting
    from . import settings as knobs
    return list(Setting.objects.filter(key__in=list(knobs.KNOBS)).exclude(key__in=knobs.MODERATION_KEYS))


def _then(slug, agent, at, rows):
    from . import settings as knobs
    return _shown(knobs.resolve(slug, agent, now=at, rows=[r for r in rows if r.created_at <= at]))


def difference(slug, agent, since, until=None, rows=None):
    """[{'key', 'was', 'now'}] between an agent's settings in a Mood at `since` and at `until` (now), with who
    set the newest of them and its note: ([...], by, note). Nothing if they came back to where they were."""
    rows = rows if rows is not None else _rows()
    until = until or datetime.now(timezone.utc)
    was, now = _then(slug, agent, since, rows), _then(slug, agent, until, rows)
    changes = [{'key': k, 'was': was[k], 'now': now[k]} for k in SHOWN if was[k] != now[k]]
    if not changes:
        return [], None, ''
    from conversations.models import mood_slug
    keys = {c['key'] for c in changes}
    newest = max((r for r in rows if r.key in keys and since < r.created_at <= until and r.agent_id in (agent, None)
                  and mood_slug(r.mood_id) in (slug, None)), key=lambda r: r.created_at, default=None)
    return changes, (newest.set_by_id if newest else None), (newest.note if newest else '')


def noticed(message):
    """An agent's message in a Mood: if its settings there aren't its last turn's, a line above it saying what
    changed, and who changed it (once, at its turn's first row)."""
    from conversations.models import ConversationParticipant, Message, Setting, ThinkingEntity
    agent = message.sender_id
    if not message.mood_id or message.is_sidechain or not ThinkingEntity.objects.filter(
            name=agent, is_biological_human=False).exists():
        return None
    before = (Message.objects.filter(mood_id=message.mood_id, sender_id=agent, is_sidechain=False,
                                     created_at__lt=message.created_at)
              .order_by('-created_at').values_list('created_at', flat=True).first())
    if before is None:
        return None
    from django.db.models import Q
    slug = message.mood.slug
    if not Setting.objects.filter(Q(mood_id=message.mood_id) | Q(mood=None), Q(agent_id=agent) | Q(agent=None),
                                  key__in=SHOWN, created_at__gt=before, created_at__lte=message.created_at).exists():
        return None  # nothing set for it here since its last word: the common case, one look
    if Message.objects.filter(mood_id=message.mood_id, source_file=SOURCE, content__type='agent-settings',
                              content__agent=agent, created_at__gt=before).exists():
        return None
    changes, by, note = difference(slug, agent, before, message.created_at)
    if not changes:
        return None
    system, _ = ConversationParticipant.objects.get_or_create(name='system', defaults={'participant_type': 'system'})
    at = message.created_at - timedelta(milliseconds=1)
    row = Message.objects.create(id=uuid.uuid4(), sender=system, mood_id=message.mood_id, source_file=SOURCE,
                                 timestamp=int(at.timestamp() * 1000),
                                 content={'type': 'agent-settings', 'agent': agent, 'changes': changes, 'by': by,
                                          'note': note})
    Message.objects.filter(pk=row.pk).update(created_at=at)
    return row


def changed_since(slug, agent, rows=None):
    """What's changed in an agent's settings here since its last word here, for its next wake: ([...], by, note)."""
    from conversations.models import Message
    before = (Message.objects.filter(mood__slug=slug, sender_id=agent, is_sidechain=False)
              .order_by('-created_at').values_list('created_at', flat=True).first())
    if before is None:
        return [], None, ''
    return difference(slug, agent, before, rows=rows)
