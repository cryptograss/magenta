"""The words magenta's page says to people, where they're worth changing without a deploy (Justin, 27 Oct:
"Where is the language of the fork decision? Is that settable?"): the page Magenta:Interface, beside
Magenta:Voice. Not Cryptograss:Moods/Dials: that is what the emergents are told; this is what people read.

One '*' line a text, anywhere on the page:

    * fork: its emergents go on from where they are there, remembering all of it

Only the keys below are read, each up to MAX_LENGTH characters, as plain text ({mood}, {parent} and {agent}
are filled in where a key has them). As with Magenta:Voice, only a trusted editor's revision counts, an example
in <nowiki>, <pre> or a comment is never read, and the page is read at most every ten minutes; the wiki away,
the last good read stands a day; never read, the words here do.
"""

import logging
import re

from django.core.cache import cache

log = logging.getLogger(__name__)

PAGE = 'Magenta:Interface'
# What the system tells a person about what just happened (Justin, 3 Nov): longer, kept on their own page, the
# same '*' line a text; [[Page]] and [[Page|words]] in them become links.
SYSTEM_PAGE = 'Magenta:System Messages'
SOUNDS_PAGE = 'Magenta:Sounds'
FRESH_FOR = 600
KEPT_FOR = 86400
MAX_LENGTH = 300
SYSTEM_MAX_LENGTH = 1000

SYSTEM_DEFAULTS = {
    # A person signed in with PickiPedia @mentions an emergent: their post goes through, waking nobody.
    'wiki-mention': ("Mentions of emergents by users signed in via PickiPedia do not automatically trigger a wake, "
                     "and do not enable tool use. One or more emergents may wake if they are set to listen in this "
                     "Mood. You can ask an SSH-tier user to wake them for you, with tools. For more information, "
                     "see [[Magenta:Signing in]]."),
    # The same, as a hint under the box while they type one.
    'wiki-mention-hint': "Signed in with PickiPedia, your @{agent} is plain text: it wakes nobody.",
    # A poll's 🔔 nudge: the reply it posts under the poll, as its asker; {who} is the @names of those yet to vote.
    'poll-nudge': "🔔 {who}: this poll is waiting on your vote.",
}

DEFAULTS = {
    # Opening a Mood, or a thread of one (the ⑂ thread box).
    'new-hint': "An emergent's first @mention here starts it a new session, given its instructions and what's been said.",
    'thread-of': "A thread of {mood}, with its settings and (until it has its own) its instructions:",
    'fork-label': 'Fork',
    'fork': 'its emergents go on from where they are there, remembering all of it',
    'fresh-label': 'Fresh',
    'fresh': 'they start anew here',
    # Closing a thread.
    'close-ask': 'Have {agent} incorporate it into {parent}: one full account there of what was settled here, and what is still open',
    'close-note': 'Anything to be sure of (optional)',
    'close-button': 'Close the thread',
    # Moving a thread to another Mood (Justin, emergence, 1 Nov 2026: four boxes).
    'move-to': 'Move it to',
    'move-incorporate-from': 'Have {agent} incorporate it into {parent}, the Mood it leaves',
    'move-incorporate-to': 'Have {agent} incorporate it into the Mood it joins',
    'move-compact': 'Compact the emergents who have spoken here',
    # A poll's 🔔 nudge button, shown to its asker while it's open.
    'poll-nudge-button': '🔔 nudge',
    'poll-nudge-title': "Reply under the poll, @-naming everyone in @core who hasn't voted yet",
    'move-inform': 'This thread moved from {from} to {to}, and belongs to {to} now. Its worktrees go under {dir}',
    'move-inform-ask': "Tell the thread: a line here, from you, that its emergents read at their next wake",
    'move-button': 'Move the thread',
}

# The team's defaults for the bell's sounds and notifications, for a browser whose person hasn't chosen (Justin,
# 5 Nov), and the sound a message makes: a PickiPedia File: (FLAC, Ogg, MP3), else magenta's own tone.
SOUND_DEFAULTS = {
    'sound-person': 'off',    # off | mention | all
    'sound-agent': 'off',
    'tell-person': 'only',    # only (mentions and answers) | all
    'tell-agent': 'only',
    'chime-person': '',       # File:Something.flac
    'chime-agent': '',
}

_SHOWN = re.compile(r'<!--.*?-->|<nowiki>.*?</nowiki>|<pre>.*?</pre>', re.S | re.I)
_LINE = re.compile(r'^\*\s*([a-z][a-z-]*)\s*:\s*(.+?)\s*$')


def parse(wikitext, defaults=None, longest=MAX_LENGTH):
    """{key: text} for each known key the page sets; the rest stay as they are here."""
    defaults = DEFAULTS if defaults is None else defaults
    found = {}
    for line in _SHOWN.sub('', wikitext or '').splitlines():
        m = _LINE.match(line.strip())
        if m and m.group(1) in defaults and len(m.group(2)) <= longest:
            found[m.group(1)] = re.sub(r'\{\{[^{}]*\}\}', '', m.group(2)).strip()
    return {**defaults, **{k: v for k, v in found.items() if v}}


def _fetch(page=PAGE):
    from django.conf import settings
    if not getattr(settings, 'INTERFACE_WORDS_FROM_WIKI', True):
        return None
    try:
        from poller.wiki_rules import EDITORS, WikiTexts
    except ImportError:  # pragma: no cover -- the poller ships beside the server
        return None
    editors = {e.lower().replace('_', ' ') for e in (getattr(settings, 'MOOD_WIKI_RULES_EDITORS', None) or EDITORS)}
    for _, editor, wikitext in WikiTexts().get(page):
        if editor.lower().replace('_', ' ') in editors:
            return wikitext
    return None


def words(fetch=None):
    """The words in force, Magenta:Interface's and Magenta:System Messages': each page's, as a trusted editor
    last left it; else what was read last; else these."""
    return {**_words(PAGE, DEFAULTS, MAX_LENGTH, 'interface-words', fetch),
            **_words(SYSTEM_PAGE, SYSTEM_DEFAULTS, SYSTEM_MAX_LENGTH, 'system-messages', fetch and (lambda: None)),
            **_words(SOUNDS_PAGE, SOUND_DEFAULTS, MAX_LENGTH, 'sound-defaults', fetch and (lambda: None))}


_KEYS = {PAGE: 'interface-words', SYSTEM_PAGE: 'system-messages', SOUNDS_PAGE: 'sound-defaults'}


def forget(title):
    """A words page changed on the wiki: its words are read again on the next ask (services/wiki_changes.py)."""
    key = _KEYS.get((title or '').replace('_', ' '))
    if key:
        cache.delete(key)


def _words(page, defaults, longest, key, fetch=None):
    found = cache.get(key)
    if found is not None:
        return found
    try:
        text = (fetch or (lambda: _fetch(page)))()
        # No page (or no trusted revision of it): this file's words, never an older read's -- those may be the
        # defaults of a deploy since replaced.
        found = parse(text, defaults, longest) if text is not None else dict(defaults)
    except Exception as e:  # noqa: BLE001 -- the wiki away: the last good read, a while longer
        log.warning('%s: %s', page, e)
        found = {**defaults, **(cache.get(key + ':last') or {})}
        cache.set(key, found, 60)
        return found
    cache.set(key, found, FRESH_FOR)
    cache.set(key + ':last', found, KEPT_FOR)
    return found
