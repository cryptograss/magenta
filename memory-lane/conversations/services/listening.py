"""Who is listening by voice -- reading aloud as they come, in one Mood or every Mood -- for everyone to see
(Justin, 22 Oct: "it's gonna be important as we increasingly embrace this voice interface").

A listening page asks for its Moods' news every few seconds (moods.html, readPoll), and says as it asks
that it's listening, and where. Its person counts as listening for LISTENING_FOR seconds after: long
enough for a phone in a pocket, whose page is asked less often. Turned off, it says so at once. One
entry per person, in a shared cache entry, as typing is.
"""

import time

from django.core.cache import cache

KEY = 'listening'
LISTENING_FOR = 150   # seconds: a hidden page on a phone asks about once a minute
MODES = ('here', 'everywhere')


def _now_listening(now):
    return {name: entry for name, entry in (cache.get(KEY) or {}).items() if now - entry['last'] < LISTENING_FOR}


def note(name, mode, slug=None, now=None):
    """`name` listening in `mode` ('here', with `slug` the Mood; 'everywhere'), or 'off'."""
    now = now or time.time()
    listening = _now_listening(now)
    if mode in MODES and (slug or mode == 'everywhere'):
        listening[name] = {'mode': mode, 'mood': slug if mode == 'here' else None, 'last': now}
    else:
        listening.pop(name, None)
    cache.set(KEY, listening, LISTENING_FOR * 2)


def in_mood(slug, now=None):
    """Who hears what's said in `slug`: [{'name', 'everywhere'}], those listening to it alone first."""
    now = now or time.time()
    found = [{'name': name, 'everywhere': entry['mode'] == 'everywhere'}
             for name, entry in _now_listening(now).items()
             if entry['mode'] == 'everywhere' or entry['mood'] == slug]
    return sorted(found, key=lambda who: (who['everywhere'], who['name']))


def everyone(now=None):
    """{name: {'mode', 'mood'}} for everyone listening now."""
    return {name: {'mode': e['mode'], 'mood': e['mood']} for name, e in _now_listening(now or time.time()).items()}
