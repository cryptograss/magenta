"""Where each person is in the Moods, for everyone to see (Justin, 23 Oct: "if people are clicking around the
moods, I'd like for us all to be able to see that").

A person is in one Mood at a time: the one they last opened, typed in or said something in. Their name is
shown there -- and nowhere else -- for SHOWN_FOR seconds after, then not at all until they do one of those
again. While they're typing it pulses there, as before (views_moods.typing_in); stopping, sent or not,
starts the three minutes again; opening another Mood takes them there. Agents don't click around: they're
shown where they've spoken lately, as before.

One entry per person, in a shared cache entry, as typing and listening are.
"""

import time

from django.core.cache import cache

KEY = 'presence'
SHOWN_FOR = 180  # seconds: three minutes (Justin, 23 Oct)


def note(name, slug, now=None):
    """`name` is in `slug` as of now: they opened it, or typed there."""
    now = now or time.time()
    here = {n: e for n, e in (cache.get(KEY) or {}).items() if now - e['at'] < SHOWN_FOR}
    here[name] = {'mood': slug, 'at': now}
    cache.set(KEY, here, SHOWN_FOR * 2)


def places(said=(), now=None):
    """{name: slug} for each person to be shown, where. `said`: (name, slug, epoch seconds) for what people
    said lately -- from a terminal too, which opens nothing -- each counting as being there then. Whichever
    is latest says where they are."""
    now = now or time.time()
    latest = {n: (e['at'], e['mood']) for n, e in (cache.get(KEY) or {}).items()}
    for name, slug, at in said:
        if name not in latest or at > latest[name][0]:
            latest[name] = (at, slug)
    return {name: slug for name, (at, slug) in latest.items() if now - at < SHOWN_FOR}
