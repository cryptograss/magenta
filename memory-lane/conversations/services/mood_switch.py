"""Going to a Mood by voice (Justin, 22 Oct: switching Moods while moving, without looking).

One tap, then "go to jams": the words are heard (voice.hear), matched here against the Moods' names,
and the narrator says where they are before the page goes there -- "Now in pound jams." Nothing is
ever posted, so a Mood misheard sends no one's words to the wrong place; a name not caught leaves
them where they were, and says so.
"""

import difflib
import re

# What's said around a Mood's name: "okay, go to the jams mood please", "switch over to pound general".
_LEAD = re.compile(r'^(?:(?:ok|okay|hey|um|uh|so|and|now)\s+)*(?:please\s+)?'
                   r'(?:(?:go|switch|change|move|jump|take\s+me|open|get\s+me)\s+)?(?:over\s+|back\s+)?'
                   r'(?:to\s+|into\s+)?(?:the\s+)?(?:mood\s+)?(?:pound|hash|hashtag|number)?\s*')
_TAIL = re.compile(r'\s*(?:mood|please|thanks|thank\s+you|now)\s*$')
CLOSE_ENOUGH = 0.78   # how alike the words and a name must be
CLEAR_BY = 0.08       # and how much more alike than the next best


def _plain(text):
    return ' '.join(re.sub(r'[^a-z0-9 ]+', ' ', (text or '').lower().replace('-', ' ').replace('_', ' ')).split())


def asked_for(heard):
    """The words naming the Mood, with what's said around them left off."""
    words = _plain(heard)
    for _ in range(2):
        words = _TAIL.sub('', _LEAD.sub('', words)).strip()
    return words


def names_of(mood):
    """How a Mood may be said: its title and its slug, as words."""
    return {name for name in (_plain(mood.title), _plain(mood.slug)) if name}


def match(heard, moods):
    """The slug of the Mood `heard` names, or None when no name is clearly it."""
    asked = asked_for(heard)
    if not asked:
        return None
    scored = []
    for mood in moods:
        best = max((1.0 if asked == name else difflib.SequenceMatcher(None, asked, name).ratio()
                    for name in names_of(mood)), default=0)
        scored.append((best, mood.slug))
    scored.sort(reverse=True)
    if not scored or scored[0][0] < CLOSE_ENOUGH:
        return None
    if len(scored) > 1 and scored[0][0] < 1.0 and scored[0][0] - scored[1][0] < CLEAR_BY:
        return None  # two names about as alike: rather ask again than guess
    return scored[0][1]
