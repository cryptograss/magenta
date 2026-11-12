"""Every PickiPedia page people link to, by title, for the composer's [[ suggestions to match anywhere in a
title, not only at its start (Justin, #interfaces, 12 Nov 2026: "against any part of a page name").

The wiki's prefixsearch only matches a title's opening, and its search matches whole words. The titles are few
(about 2,100 in NAMESPACES, Nov 2026), so they're asked for once, kept KEPT seconds, and sent whole to a page
that wants them; the page matches as you type, asking nobody. A page written since is caught two ways: the wiki
hooks drop the list when a title it doesn't hold changes (services/wiki_changes.forget), and the page still asks
prefixsearch beside it. A wiki that doesn't answer leaves the list empty, kept MISS, and prefixsearch alone, as
before.
"""

import re

from django.core.cache import cache

KEY = 'wiki-titles:all'
KEPT, MISS = 3600, 60
# The namespaces a [[link]] in a Mood names: articles, people, the project's own, releases, songs, emergents,
# magenta's. Not files, templates, properties, drafts or the wiki's machinery; typed with its prefix
# (File:…), such a page is still found by prefixsearch.
NAMESPACES = (0, 2, 4, 12, 14, 3000, 3004, 3008, 3010, 3012, 3016)


def _title(title):
    return re.sub(r'\s+', ' ', (title or '').replace('_', ' ')).strip()


def forget(title):
    """A change to a title the list doesn't hold (a page written, or moved here) drops the list."""
    held = cache.get(KEY)
    if held and _title(title) not in held:
        cache.delete(KEY)


def titles(http=None):
    """Every title in NAMESPACES, sorted; [] if the wiki didn't answer."""
    held = cache.get(KEY)
    if held is not None:
        return held
    try:
        found = sorted(_fetch(http))
        cache.set(KEY, found, KEPT)
    except Exception:  # noqa: BLE001 -- prefixsearch still answers in the page
        found = []
        cache.set(KEY, found, MISS)
    return found


def _fetch(http=None):
    import requests
    from .mood_view import pickipedia_url
    http = http or requests
    api = f'{pickipedia_url()}/api.php'
    found = set()
    for ns in NAMESPACES:
        more = {}
        for _ in range(40):  # 20,000 titles a namespace, at most: a runaway continue stops
            got = http.get(api, timeout=8, headers={'User-Agent': 'memory-lane (magenta; wiki titles)'},
                           params={'action': 'query', 'list': 'allpages', 'apnamespace': ns, 'aplimit': 500,
                                   'format': 'json', 'formatversion': 2, **more}).json()
            found.update(p['title'] for p in (got.get('query') or {}).get('allpages', []))
            more = got.get('continue') or {}
            if not more:
                break
    return found
