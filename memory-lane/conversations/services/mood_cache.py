"""Cache for the Moods list API endpoint, kept fresh in the background.

Building the list reads every Mood (~13 s on maybelle, 31 Oct). With a plain 30 s expiry, every request
that came while it rebuilt missed too and built it again: four workers full of the same list, and a send
waiting behind them (Justin, 31 Oct: a minute to load, 10 s to send). So the list is never dropped: past
API_MOODS_CACHE_TTL it is still served, and one thread, holding a lock, builds the next. Only an empty
cache (a fresh container, or a Mood created, renamed, archived...) makes anyone wait, and then one request
builds while the others wait for it."""

import logging
import threading
import time

from django.core.cache import cache

API_MOODS_CACHE_KEY = 'api:moods'
API_MOODS_CACHE_TTL = 30  # seconds the list counts as fresh: message counts and last_at freshen this often
BUILDING_KEY = 'api:moods:building'
BUILD_LOCK_TTL = 120  # seconds: a build that died lets the next one start after this
WAIT_FOR_BUILD = 40  # seconds a request with nothing cached waits on another's build before building itself


def bust_moods_cache():
    """A Mood was created, renamed, archived, pinned, closed, reforked or deleted: dropped, so whoever asks
    next sees it as it is now. The next ask builds it; any others meanwhile wait for that build."""
    cache.delete(API_MOODS_CACHE_KEY)


def moods_list(build):
    """(entry, how) for /api/moods/: entry is {'body', 'timing', 'at'}; how is 'hit', 'stale' or 'built'.
    `build` returns (body, timing)."""
    kept = cache.get(API_MOODS_CACHE_KEY)
    if kept is not None:
        if time.time() - kept.get('at', 0) < API_MOODS_CACHE_TTL:
            return kept, 'hit'
        if cache.add(BUILDING_KEY, 1, BUILD_LOCK_TTL):
            spawn(_build_in_background, build)
        return kept, 'stale'
    # Nothing at all: one builds, the rest wait for it.
    if not cache.add(BUILDING_KEY, 1, BUILD_LOCK_TTL):
        until = time.time() + WAIT_FOR_BUILD
        while time.time() < until:
            time.sleep(0.25)
            kept = cache.get(API_MOODS_CACHE_KEY)
            if kept is not None:
                return kept, 'hit'
    try:
        return _store(build), 'built'
    finally:
        cache.delete(BUILDING_KEY)


def kept(key, build, fresh, lock_ttl=BUILD_LOCK_TTL, on_error=None, miss_fresh=0):
    """The value under `key`, built by `build()` and served however old it is: past `fresh` seconds one
    thread (the lock) builds the next in the background while everyone keeps getting this one. Only an
    empty cache builds in line, so a slow build lands on a thread, not on whoever asks (the Tidings
    release and picture scans: 5-30 s on maybelle, hug-watch, 11 Nov). A build that raises with nothing
    kept raises too, unless `on_error()` gives what to keep instead, counted fresh for `miss_fresh`
    seconds so a broken source isn't asked again at once; one that raises in the background keeps the
    old value and logs. `stale(key)` says the value is old without dropping it."""
    entry = cache.get(key)
    now = time.time()
    if entry is not None:
        if now - entry['at'] >= fresh and cache.add(key + ':building', 1, lock_ttl):
            spawn(_rebuild, key, build)
        return entry['value']
    try:
        value = build()
    except Exception:
        if on_error is None:
            raise
        value = on_error()
        keep(key, value, at=now - fresh + miss_fresh)
        return value
    keep(key, value)
    return value


def keep(key, value, at=None):
    """`value` under `key`, counted fresh from `at` (now)."""
    cache.set(key, {'value': value, 'at': time.time() if at is None else at}, None)


def stale(key):
    """What's kept under `key` is old: still served, and rebuilt on the next ask. Nothing kept: nothing to do."""
    entry = cache.get(key)
    if entry is not None:
        cache.set(key, {'value': entry['value'], 'at': 0}, None)


def _rebuild(key, build):
    from django.db import connection
    try:
        keep(key, build())
    except Exception:
        logging.getLogger(__name__).exception('%s: background build failed; the old one stays', key)
    finally:
        cache.delete(key + ':building')
        if threading.current_thread() is not threading.main_thread():
            connection.close()  # this thread's own connection (the tests run it in line, on theirs)


def spawn(fn, *args):
    """fn(*args) on its own thread (the tests run it in line: their rows aren't seen from another connection)."""
    threading.Thread(target=fn, args=args, daemon=True).start()


def _store(build):
    body, timing = build()
    entry = {'body': body, 'timing': timing, 'at': time.time()}
    cache.set(API_MOODS_CACHE_KEY, entry, None)
    return entry


def _build_in_background(build):
    from django.db import connection
    try:
        _store(build)
    except Exception:
        import logging
        logging.getLogger(__name__).exception('Moods list: background build failed')
    finally:
        cache.delete(BUILDING_KEY)
        connection.close()  # this thread's own connection
