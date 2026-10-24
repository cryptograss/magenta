"""Knobs: how each agent carries itself in each Mood.

A casual Mood and a focused one want different things from an agent --
how readily it speaks up, how hard it thinks, what it should keep in mind
there. So every knob can be set for one agent in one Mood, for one agent
everywhere, for every agent in one Mood, or for all; the most specific
setting wins:

    (mood, agent) > (mood, every agent) > (every Mood, agent) > (all) > default

Settings are rows (models.Setting), only ever added: the newest row for a
slot is in force, the rest are its history -- who changed what, when, and
why. The runner reads the resolved values every few seconds, in the pulse.

Moderation keys (the scram, bans) are set only by an admin's signed
request (views_admin.py), never through the settings page.
"""

import re
from datetime import datetime, timezone

EFFORTS = ('low', 'medium', 'high', 'xhigh', 'max')
LISTENING = ('on', 'mentions', 'off')
DISCRETIONS = ('reserved', 'normal', 'chatty')
VERBOSITIES = ('brief', 'normal', 'thorough')

# key: (default, help). Validation per key is in clean().
KNOBS = {
    'listening': ({'mode': 'on', 'until': None},
                  "on: answers mentions and may speak up unasked. mentions: answers mentions only. "
                  "off: wakes for nothing; mentions wait until it's back on. With an 'until', it goes "
                  "back on by itself then, and catches up on what it missed."),
    'consider_after': (10, 'Seconds of quiet (and nobody typing) before it considers new posts.'),
    'idle_after': (3000, 'Seconds of silence before its first unprompted look; doubles after each silent '
                         'one. 0: never.'),
    'considers_per_hour': (6, 'At most this many full considerations an hour, here.'),
    'mention_effort': ('high', 'How hard it thinks when someone @mentions it.'),
    'consider_effort': ('medium', 'How hard it thinks when it decides whether to speak up.'),
    'model': ('', "Which model it runs on here: 'opus', 'sonnet', 'fable', 'haiku', or a full name; on Pi, "
                  "vendor/model (anthropic/claude-sonnet-5-5, openrouter/moonshotai/kimi-k3). "
                  "Empty: its harness's default."),
    'catch_up_tokens': (10_000, "How much of what was said here since it last spoke a wake reads word for word, "
                                "in tokens (about 4 characters each); what's older is summarized. A post that links "
                                "a message has it read from that message on."),
    'discretion': ('normal', "How readily it speaks up unasked here. reserved: only when something changes a "
                             "decision or is plainly for it. normal: when it would genuinely help; mostly quiet. "
                             "chatty: joins in as a teammate would, small talk and kind words included."),
    'verbosity': ('normal', 'How long its replies run here: brief, normal or thorough.'),
    'ultracode': (False, "Its full-tools mention wakes here run with Claude Code's ultracode on: standing "
                         "multi-agent workflows, at any effort. Thorough, and costly."),
}
GLOBAL_KNOBS = {
    'consider_usd_per_day': (10.0, 'Dollars a day across screens and considerations (mentions not counted).'),
    'voice_usd_per_day': (10.0, 'Dollars a day for voice: messages read aloud and memos transcribed (ElevenLabs).'),
    'voice': ('', "The voice messages are read in unless one directs otherwise: a name from /api/voice/voices/. "
                  "Empty: the first ElevenLabs offers."),
    'narrator_voice': ('', "The narrator's voice (services/voice.py): who speaks next, and who's thinking, "
                           "when Moods are read as they come. Empty: given one at its first word, nobody else's."),
    # How Tidings ranks its links (services/tiding_links.rank): (1 + upvotes + weight x comments), over
    # (1 + age in units) to the gravity (Justin, 22 Oct: HN's, on our clock; "pretty dynamic").
    'tidings_age_hours': (12.0, 'Tidings ranking: the hours one unit of a link\'s age is. Fewer: links fade faster.'),
    'tidings_gravity': (1.5, 'Tidings ranking: how hard age pulls a link down. More: it fades faster.'),
    'tidings_comment_weight': (0.5, 'Tidings ranking: what one comment counts for, beside one upvote (1).'),
    # Who may set agents' per-Mood settings from a reply (services/agent_settings.py), when a person asks: a
    # grant, so adding an agent is a value a person changes, not code (Justin and re, 24 Oct). magent at first.
    'settings_may_set': (['magent'], "The agents that may set other agents' settings in a Mood (and their own), "
                                     "with a ```settings block, when someone signed in with an SSH key asks."),
}
# Links pinned to the top of Tidings by someone signed in with an SSH key: their ids, last pinned first.
# Set from Tidings itself (tiding_links.pin), not the settings page.
TIDINGS_PINNED = 'tidings_pinned'
MODERATION_KEYS = ('scram', 'banned')
# About one person or agent, wherever they are: set with `agent` naming them
# (human or not) and no Mood. Changed from the voices list on the Moods page
# (views_auth.api_speaker_voice), not the settings page.
PERSON_KEYS = {
    'speaker_voice': ('', "The voice their messages are read aloud in (services/voice.py): a voice's id or name. "
                          "Empty until chosen, or given at their first reading."),
    # A person's look, old-school BBS (Justin, 21 Oct): their name written their way, and their icon's colour.
    'name_art': ('', f"Their name as they'd have it written: at most {30} characters, every letter of their name "
                     "in it, in order (art_fits). Empty: their name as it is."),
    'icon_colour': ('', 'The colour of the little person beside their name, as #rrggbb. Empty: the page\'s own.'),
    # Each character of name_art in one of ANSI's sixteen colours, as a BBS had them (Justin, 21 Oct): a code
    # for each, 0-f (CGA's order: black, blue, green, cyan, red, magenta, brown, grey, then the bright eight),
    # or '.' for the page's own.
    'name_colours': ('', "A colour for each character of their name as written: 0-f, ANSI's sixteen, or '.' for none."),
    # And behind each, as a BBS's cell had its background (iCE colour: all sixteen) (Justin, 21 Oct: "had more colors").
    'name_backs': ('', "A background for each character of their name as written: 0-f, or '.' for none."),
}
NAME_ART_MAX = 30


def art_fits(art, name):
    """Whether `art` writes `name` -- their PickiPedia name, the one people know them by (Justin, 21 Oct) --
    every character of it there, in order, any case (its spaces aside); on one line of printable
    characters, at most NAME_ART_MAX."""
    if not art or len(art) > NAME_ART_MAX or not art.isprintable():
        return False
    letters = iter(art.casefold())
    return all(any(c == ch for c in letters) for ch in name.casefold() if not ch.isspace())


def art_name(person):
    """The name a person's art has to write: their PickiPedia name, else their name here."""
    from .wiki_auth import names
    return names().get(person) or person
# A Mood's own state, not how an agent carries itself there: set for the
# Mood alone (no agent), from the Mood's page, and kept apart from the knobs.
MOOD_KEYS = {
    'archived': (False, 'Out of the Moods list, into "Archived"; still readable, and still answers mentions.'),
    'pinned': (False, 'At the top of the Moods list, whatever was said where lately.'),
}


class Invalid(ValueError):
    pass


def default(key):
    return (KNOBS.get(key) or GLOBAL_KNOBS.get(key) or MOOD_KEYS.get(key) or PERSON_KEYS.get(key) or (None,))[0]


def clean(key, value):
    """The value as stored, or Invalid."""
    if key == 'listening':
        if isinstance(value, str):
            value = {'mode': value, 'until': None}
        if not isinstance(value, dict) or value.get('mode') not in LISTENING:
            raise Invalid(f"listening: one of {', '.join(LISTENING)}")
        until = value.get('until')
        if until:
            try:
                parsed = datetime.fromisoformat(str(until).replace('Z', '+00:00'))
            except ValueError:
                raise Invalid('listening.until: an ISO date and time')
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            until = parsed.isoformat()
        return {'mode': value['mode'], 'until': until or None}
    if key in ('consider_after', 'idle_after', 'considers_per_hour', 'catch_up_tokens'):
        limits = {'consider_after': (0, 3600), 'idle_after': (0, 7 * 86400), 'considers_per_hour': (0, 60),
                  'catch_up_tokens': (1000, 24000)}[key]
        try:
            value = int(value)
        except (TypeError, ValueError):
            raise Invalid(f'{key}: a whole number')
        if not limits[0] <= value <= limits[1]:
            raise Invalid(f'{key}: from {limits[0]} to {limits[1]}')
        return value
    if key in ('discretion', 'verbosity'):
        allowed = DISCRETIONS if key == 'discretion' else VERBOSITIES
        if value not in allowed:
            raise Invalid(f"{key}: one of {', '.join(allowed)}")
        return value
    if key in ('mention_effort', 'consider_effort'):
        if value not in EFFORTS:
            raise Invalid(f"{key}: one of {', '.join(EFFORTS)}")
        return value
    if key == 'model':
        value = str(value or '').strip()
        # Pi names a model by vendor, and some vendors' ids carry slashes,
        # colons or a leading ~ of their own (openrouter/~anthropic/claude-fable-latest).
        if not re.fullmatch(r'[A-Za-z0-9._:~/\-]{0,160}', value) or value.startswith('-'):
            raise Invalid('model: letters, digits, and . _ : ~ / -')
        return value
    if key == 'ultracode':
        if value in (True, False):
            return value
        if str(value).lower() in ('true', 'on', '1', 'yes'):
            return True
        if str(value).lower() in ('false', 'off', '0', 'no', ''):
            return False
        raise Invalid('ultracode: on or off')
    if key in ('archived', 'pinned'):
        if isinstance(value, bool):
            return value
        raise Invalid(f'{key}: true or false')
    if key == 'name_art':
        value = str(value or '').strip()
        if len(value) > NAME_ART_MAX or not value.isprintable():
            raise Invalid(f'name_art: at most {NAME_ART_MAX} printable characters, on one line')
        return value
    if key in ('name_colours', 'name_backs'):
        value = str(value or '').strip().lower()
        if len(value) > NAME_ART_MAX or not re.fullmatch(r'[.0-9a-f]*', value):
            raise Invalid(f"{key}: at most {NAME_ART_MAX} codes, each 0-f or '.'")
        return value.rstrip('.')
    if key == 'icon_colour':
        value = str(value or '').strip().lower()
        if value and not re.fullmatch(r'#[0-9a-f]{6}', value):
            raise Invalid('icon_colour: a colour as #rrggbb')
        return value
    if key in ('voice', 'speaker_voice', 'narrator_voice'):
        value = str(value or '').strip()
        if len(value) > 100:
            raise Invalid(f'{key}: a name, at most 100 characters')
        return value
    if key == 'voice_usd_per_day':
        try:
            value = round(float(value), 2)
        except (TypeError, ValueError):
            raise Invalid('voice_usd_per_day: a number')
        if not 0 <= value <= 500:
            raise Invalid('voice_usd_per_day: from 0 to 500')
        return value
    if key in ('tidings_age_hours', 'tidings_gravity', 'tidings_comment_weight'):
        low, high = {'tidings_age_hours': (1, 168), 'tidings_gravity': (0.5, 3), 'tidings_comment_weight': (0, 3)}[key]
        try:
            value = round(float(value), 2)
        except (TypeError, ValueError):
            raise Invalid(f'{key}: a number')
        if not low <= value <= high:
            raise Invalid(f'{key}: from {low} to {high}')
        return value
    if key == 'settings_may_set':
        if isinstance(value, str):
            value = [v for v in re.split(r'[\s,]+', value) if v]
        if not isinstance(value, list) or not all(isinstance(v, str) and re.fullmatch(r'[\w.-]{1,50}', v) for v in value):
            raise Invalid('settings_may_set: agents\' names')
        return list(dict.fromkeys(v.lower() for v in value))[:20]
    if key == TIDINGS_PINNED:
        import uuid
        try:
            value = list(dict.fromkeys(str(uuid.UUID(str(v))) for v in (value or [])))
        except (TypeError, ValueError):
            raise Invalid(f'{key}: links\' ids')
        return value[:20]
    if key == 'consider_usd_per_day':
        try:
            value = round(float(value), 2)
        except (TypeError, ValueError):
            raise Invalid('consider_usd_per_day: a number')
        if not 0 <= value <= 500:
            raise Invalid('consider_usd_per_day: from 0 to 500')
        return value
    raise Invalid(f'no such setting: {key}')


def looks():
    """{person: {'art', 'colours', 'backs', 'colour'}} for whoever has set any: how the Moods page writes them.
    `colours` and `backs` are cut to the art they colour (a code for each character, '.' for none)."""
    from conversations.models import Setting
    fields = {'name_art': 'art', 'name_colours': 'colours', 'name_backs': 'backs', 'icon_colour': 'colour'}
    found = {}
    for row in Setting.objects.filter(key__in=list(fields), mood__isnull=True).order_by('created_at'):
        found.setdefault(row.agent_id, {'art': '', 'colours': '', 'backs': '', 'colour': ''})[fields[row.key]] = row.value or ''
    for look in found.values():
        for codes in ('colours', 'backs'):
            look[codes] = look[codes][:len(look['art'])].rstrip('.') if look['art'] else ''
    return {name: look for name, look in found.items() if look['art'] or look['colour']}


def latest(rows):
    """{(mood slug, agent_id, key): row}, the newest row per slot."""
    from conversations.models import mood_slug
    current = {}
    for row in sorted(rows, key=lambda r: r.created_at):
        current[(mood_slug(row.mood_id), row.agent_id, row.key)] = row
    return current


def resolve(mood, agent, now=None, rows=None):
    """The settings in force for `agent` in `mood` (either may be None for 'every')."""
    from conversations.models import Setting
    now = now or datetime.now(timezone.utc)
    if rows is None:
        rows = Setting.objects.filter(key__in=list(KNOBS)).exclude(key__in=MODERATION_KEYS)
    current = latest(rows)
    mood_id = getattr(mood, 'slug', mood)  # rows are keyed by slug (latest)
    agent_id = getattr(agent, 'pk', agent)
    resolved = {}
    for key, (fallback, _) in KNOBS.items():
        value = fallback
        for slot in ((mood_id, agent_id), (mood_id, None), (None, agent_id), (None, None)):
            row = current.get((*slot, key))
            if row is not None:
                value = row.value
                break
        resolved[key] = value
    listening = resolved['listening']
    if listening.get('until'):
        until = datetime.fromisoformat(listening['until'])
        if until <= now:
            resolved['listening'] = {'mode': 'on', 'until': None}  # its time is up
    return resolved


def set_by(mood, agent, keys, rows=None):
    """{key: {'by', 'at'}}: who set each of `keys` as it stands for `agent` in `mood`, and when.
    A key still at its default, or set before anyone was recorded, is left out."""
    from conversations.models import Setting
    if rows is None:
        rows = Setting.objects.filter(key__in=list(keys))
    current = latest(rows)
    mood_id = getattr(mood, 'slug', mood)
    agent_id = getattr(agent, 'pk', agent)
    out = {}
    for key in keys:
        for slot in ((mood_id, agent_id), (mood_id, None), (None, agent_id), (None, None)):
            row = current.get((*slot, key))
            if row is not None:
                if row.set_by_id:
                    out[key] = {'by': row.set_by_id, 'at': row.created_at.isoformat()}
                break
    return out


def mood_flagged(key):
    """The Moods where `key` (a MOOD_KEYS flag) is on now: the newest row for each says so."""
    from conversations.models import Setting
    rows = Setting.objects.filter(key=key, agent=None, mood__isnull=False)
    return {slug for (slug, _, _), row in latest(rows).items() if row.value}


def mood_value(slug, key):
    """A MOOD_KEYS setting for one Mood, as it stands now (its default if never set)."""
    from conversations.models import Setting
    row = Setting.objects.filter(key=key, agent=None, mood__slug=slug).order_by('-created_at').first()
    return row.value if row is not None else default(key)


def archived_slugs():
    return mood_flagged('archived')


def global_values(keys):
    """{key: its value now} for several settings at once: one question of the database, not one each."""
    from conversations.models import Setting
    found = {}
    for row in Setting.objects.filter(key__in=keys, mood=None, agent=None).order_by('-created_at'):
        found.setdefault(row.key, row.value)
    return {key: found[key] if key in found else default(key) for key in keys}


def global_value(key):
    from conversations.models import Setting
    row = Setting.objects.filter(key=key, mood=None, agent=None).order_by('-created_at').first()
    return row.value if row is not None else default(key)


def scram():
    """The scram in force, or None: {'by': ..., 'at': ..., 'note': ...}."""
    from conversations.models import Setting
    row = Setting.objects.filter(key='scram', mood=None, agent=None).order_by('-created_at').first()
    if row is None or not row.value:
        return None
    return {'by': row.set_by_id, 'at': row.created_at.isoformat(), 'note': row.note}


def banned(name):
    from conversations.models import Setting
    row = Setting.objects.filter(key='banned', mood=None, agent_id=name).order_by('-created_at').first()
    return bool(row and row.value)


def change(key, value, mood=None, agent=None, by=None, note=''):
    """Record a setting (a new row; nothing is overwritten)."""
    from conversations.models import Setting
    if key in MODERATION_KEYS:
        raise Invalid(f'{key} is set only by an admin, with their key')
    if key in GLOBAL_KNOBS and (mood is not None or agent is not None):
        raise Invalid(f'{key} applies everywhere at once')
    if key in MOOD_KEYS and (mood is None or agent is not None):
        raise Invalid(f'{key} is set for one Mood, not for an agent')
    if key in PERSON_KEYS and (agent is None or mood is not None):
        raise Invalid(f'{key} is set for one person or agent, everywhere')
    return Setting.objects.create(mood=mood, agent=agent, key=key, value=clean(key, value), set_by=by,
                                  note=str(note or '')[:200])


def describe(row):
    return {
        'mood': row.mood.slug if row.mood_id else None, 'agent': row.agent_id, 'key': row.key, 'value': row.value,
        'set_by': row.set_by_id, 'note': row.note, 'at': row.created_at.isoformat(),
    }
