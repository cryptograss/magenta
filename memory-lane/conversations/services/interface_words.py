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
FRESH_FOR = 600
KEPT_FOR = 86400
MAX_LENGTH = 300

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
    'move-inform': 'This thread moved from {from} to {to}, and belongs to {to} now. Its worktrees go under {dir}',
    'move-inform-ask': "Tell the thread: a line here, from you, that its emergents read at their next wake",
    'move-button': 'Move the thread',
}

_SHOWN = re.compile(r'<!--.*?-->|<nowiki>.*?</nowiki>|<pre>.*?</pre>', re.S | re.I)
_LINE = re.compile(r'^\*\s*([a-z][a-z-]*)\s*:\s*(.+?)\s*$')


def parse(wikitext):
    """{key: text} for each known key the page sets; the rest stay as they are here."""
    found = {}
    for line in _SHOWN.sub('', wikitext or '').splitlines():
        m = _LINE.match(line.strip())
        if m and m.group(1) in DEFAULTS and len(m.group(2)) <= MAX_LENGTH:
            found[m.group(1)] = re.sub(r'\{\{[^{}]*\}\}', '', m.group(2)).strip()
    return {**DEFAULTS, **{k: v for k, v in found.items() if v}}


def _fetch():
    from django.conf import settings
    if not getattr(settings, 'INTERFACE_WORDS_FROM_WIKI', True):
        return None
    try:
        from poller.wiki_rules import EDITORS, WikiTexts
    except ImportError:  # pragma: no cover -- the poller ships beside the server
        return None
    editors = {e.lower().replace('_', ' ') for e in (getattr(settings, 'MOOD_WIKI_RULES_EDITORS', None) or EDITORS)}
    for _, editor, wikitext in WikiTexts().get(PAGE):
        if editor.lower().replace('_', ' ') in editors:
            return wikitext
    return None


def words(fetch=None):
    """The words in force: the page's, as a trusted editor last left it; else what was read last; else these."""
    found = cache.get('interface-words')
    if found is not None:
        return found
    try:
        text = (fetch or _fetch)()
        # No page (or no trusted revision of it): this file's words, never an older read's -- those may be the
        # defaults of a deploy since replaced.
        found = parse(text) if text is not None else dict(DEFAULTS)
    except Exception as e:  # noqa: BLE001 -- the wiki away: the last good read, a while longer
        log.warning('interface words: %s', e)
        found = {**DEFAULTS, **(cache.get('interface-words:last') or {})}
        cache.set('interface-words', found, 60)
        return found
    cache.set('interface-words', found, FRESH_FOR)
    cache.set('interface-words:last', found, KEPT_FOR)
    return found
