"""What the stats section shows: each site's traffic, in three windows.

Each box that serves one of our sites keeps its own count, from its own Caddy access log
(maybelle-config, roles/goaccess: traffic-rollup.py, once a minute), and writes it where Caddy
already serves /stats/ from. This fetches both, together, and keeps the answer so nobody waits on
two other hosts to draw a page (Justin, #performance, 22 Nov).

The rollup is what decides what a window holds and what is left out of it; this only carries it.
Nothing here is per-reader, so one copy serves everyone.
"""

import logging

import requests

from conversations.services import mood_cache

KEY = 'traffic:windows'
KEPT = 60          # seconds before one thread fetches again; the rollups write once a minute
MISS_KEPT = 20     # a site that didn't answer isn't asked again at once
TIMEOUT = 6

SITES = (
    {'name': 'magenta', 'host': 'magenta.cryptograss.live',
     'what': 'This site: the Moods, Tidings, the ramp, and every poll the open tabs make.'},
    {'name': 'PickiPedia', 'host': 'pickipedia.xyz',
     'what': 'The wiki, and Rabbithole beside it.'},
)
WINDOWS = (('3h', 'the last 3 hours'), ('24h', 'the last 24 hours'), ('7d', 'the last 7 days'))


def state(http=None):
    """{'sites': [...], 'windows': [[key, words], ...]}, each site with its rollup's windows, or why
    it has none. Kept KEPT seconds and served as it is while one thread fetches again."""
    return mood_cache.kept(KEY, lambda: _fetch(http), KEPT, on_error=_nothing, miss_fresh=MISS_KEPT)


def forget():
    mood_cache.stale(KEY)


def _nothing():
    return {'sites': [dict(site, error='nothing fetched yet') for site in SITES],
            'windows': [list(w) for w in WINDOWS]}


def _fetch(http=None):
    http = http or requests
    sites = []
    for site in SITES:
        sites.append(dict(site, **_one(http, site['host'])))
    return {'sites': sites, 'windows': [list(w) for w in WINDOWS]}


def _one(http, host):
    """A site's rollup, as it wrote it. A site that can't be reached says so and shows no numbers:
    an empty window would read as no traffic, which is a different thing."""
    try:
        answer = http.get(f'https://{host}/stats/traffic.json', timeout=TIMEOUT,
                          headers={'User-Agent': 'memory-lane (magenta)'})
        if answer.status_code == 404:
            return {'error': 'the rollup hasn’t written its first count here yet'}
        answer.raise_for_status()
        body = answer.json()
    except Exception as e:  # noqa: BLE001 -- another box, over the network: anything can happen
        logging.getLogger(__name__).warning('traffic: %s said %s', host, e)
        return {'error': f'couldn’t read it from {host}'}
    if not isinstance(body, dict) or not isinstance(body.get('windows'), dict):
        return {'error': f'{host} answered with something unreadable'}
    # `since` and `catching_up`: how far back the box's own count reaches, and whether it is still
    # reading the rolled logs behind it. A window holding an hour would otherwise read exactly like a
    # quiet week (Justin, #performance, 22 Nov), and each window carries its own `since` and `full`.
    return {'updated': body.get('updated', ''), 'windows': body['windows'],
            'since': body.get('since') or '', 'catching_up': body.get('catching_up') or 0}
