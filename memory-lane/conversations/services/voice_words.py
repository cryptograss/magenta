"""The voice's words, kept on PickiPedia where they can be read and changed without a deploy (Justin, 24 Oct:
"the pronunciation settings -- can we move them to pickipedia?"): the page Magenta:Voice.

Three lists, each a section of '*' lines:

    == Heard as ==        a name a transcription writes wrong, and the name it is: * magnet → magent
    == Expected words ==  words a memo's transcription should expect (its keyterms): * banjo
    == Said as ==         a word, and how the voice should say it: * PickiPedia → picky pedia

Only a trusted editor's revision counts (poller/wiki_rules.EDITORS: what the voice says, and what a memo is
heard as, are no one else's to change); an example in <nowiki>, <pre> or a comment is never read. Read at most
every ten minutes; the wiki away, the last good read stands a day; never read, the lists in this file do.
"""

import logging
import re

from django.core.cache import cache

log = logging.getLogger(__name__)

PAGE = 'Magenta:Voice'
FRESH_FOR = 600       # seconds a read is kept
KEPT_FOR = 86400      # and the last good one, should the wiki not answer
MAX_EACH = 300        # lines read from a list
MAX_WORD = 60         # characters in a word or what it's said as

# The lists as they were in code (voice.py, until 24 Oct): what's used until the page is read.
HEARD_AS = {'magnet': 'magent'}
KEYTERMS = ['banjo', 'mandolin', 'dobro', 'fiddle', 'upright bass', 'bluegrass', 'flatpicking', 'Scruggs',
            'Cryptograss', 'PickiPedia', 'magenta', 'magent', 'Mood', 'Moods', 'hunter', 'maybelle',
            'delivery-kid', 'Ethereum', 'blockchain']
SAID_AS = {}

_SECTIONS = {'heard as': 'heard_as', 'expected words': 'keyterms', 'said as': 'said_as'}
_SHOWN = re.compile(r'<!--.*?-->|<nowiki>.*?</nowiki>|<pre>.*?</pre>', re.S | re.I)
_ARROW = re.compile(r'\s*(?:→|->|=>)\s*')


def defaults():
    return {'heard_as': dict(HEARD_AS), 'keyterms': list(KEYTERMS), 'said_as': dict(SAID_AS)}


def parse(wikitext):
    """The three lists from the page's text: {'heard_as': {wrong: right}, 'keyterms': [...], 'said_as': {word: said}}.
    A section the page doesn't have is empty."""
    found = {'heard_as': {}, 'keyterms': [], 'said_as': {}}
    where = None
    for line in _SHOWN.sub('', wikitext or '').splitlines():
        heading = re.match(r'^(=+)\s*(.*?)\s*\1\s*$', line)
        if heading:
            where = _SECTIONS.get(heading.group(2).strip().lower())
            continue
        if where is None or not line.startswith('*'):
            continue
        item = re.sub(r'\{\{[^{}]*\}\}', '', line.lstrip('*')).strip()
        if not item:
            continue
        if where == 'keyterms':
            if len(item) <= MAX_WORD and item not in found['keyterms'] and len(found['keyterms']) < MAX_EACH:
                found['keyterms'].append(item)
            continue
        parts = _ARROW.split(item, maxsplit=1)
        if len(parts) != 2 or not all(0 < len(p.strip()) <= MAX_WORD for p in parts) or len(found[where]) >= MAX_EACH:
            continue
        word, meant = parts[0].strip(), parts[1].strip()
        if where == 'heard_as':
            found['heard_as'][word.lower()] = meant.lower()
        else:
            found['said_as'][word] = meant
    return found


def _fetch():
    """The page's text at its newest trusted revision: None if there's no page, or no trusted revision of it."""
    from django.conf import settings
    if not getattr(settings, 'VOICE_WORDS_FROM_WIKI', True):
        return None
    try:
        from poller.wiki_rules import EDITORS, WikiTexts
    except ImportError:  # pragma: no cover -- the poller ships beside the server
        return None
    texts = WikiTexts()
    editors = {e.lower().replace('_', ' ') for e in (getattr(settings, 'MOOD_WIKI_RULES_EDITORS', None) or EDITORS)}
    for _, editor, wikitext in texts.get(PAGE):
        if editor.lower().replace('_', ' ') in editors:
            return wikitext
    return None


def words(fetch=None):
    """The lists in force: the page's, as a trusted editor last left it; else what was read last; else this file's."""
    found = cache.get('voice-words')
    if found is not None:
        return found
    try:
        text = (fetch or _fetch)()
        found = parse(text) if text is not None else cache.get('voice-words:last') or defaults()
    except Exception as e:  # noqa: BLE001 -- the wiki away: the last good read, a while longer
        log.warning('voice words: %s', e)
        found = cache.get('voice-words:last') or defaults()
        cache.set('voice-words', found, 60)
        return found
    cache.set('voice-words', found, FRESH_FOR)
    cache.set('voice-words:last', found, KEPT_FOR)
    return found


def said(text, said_as=None):
    """`text` as the voice should say it: each word the Said as list names, whole and in any case, replaced."""
    said_as = words()['said_as'] if said_as is None else said_as
    if not said_as or not text:
        return text
    pattern = re.compile(r'(?<![\w-])(' + '|'.join(re.escape(w) for w in sorted(said_as, key=len, reverse=True))
                         + r')(?![\w-])', re.I)
    lowered = {w.lower(): s for w, s in said_as.items()}

    def say(match):
        word = match.group(1)
        meant = lowered.get(word.lower(), word)
        # A capital where the word had one: "Re is thinking" is "Ray is thinking".
        return meant[:1].upper() + meant[1:] if word[:1].isupper() and meant[:1].islower() else meant
    return pattern.sub(say, text)
