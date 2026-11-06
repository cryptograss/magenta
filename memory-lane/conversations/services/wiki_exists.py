"""Whether PickiPedia pages exist, for a message's [[links]]: blue for a page, red for one not yet written
(Justin, #interfaces, 6 Nov 2026). Asked in one request for up to fifty titles; each answer kept KEEP seconds,
and dropped when the page changes on the wiki (services/wiki_changes.forget), so a page written turns its links
blue within the minute. A wiki that doesn't answer leaves the titles out: the links keep their plain colour.
"""

import hashlib
import re

from django.core.cache import cache

KEEP = 3600


def _title(title):
    return re.sub(r'\s+', ' ', (title or '').replace('_', ' ')).strip()


def _key(title):
    return 'wiki-exists:' + hashlib.sha1(_title(title).lower().encode()).hexdigest()


def forget(title):
    cache.delete(_key(title))


def exists(titles, http=None):
    """{title: True/False} for the titles the wiki (or the cache) could say about."""
    import requests
    from .mood_view import pickipedia_url
    titles = [t for t in dict.fromkeys(_title(t) for t in titles) if t and '|' not in t][:50]
    found = {}
    asking = []
    for t in titles:
        kept = cache.get(_key(t))
        if kept is None:
            asking.append(t)
        else:
            found[t] = kept
    if asking:
        try:
            http = http or requests
            answer = http.get(f'{pickipedia_url()}/api.php', timeout=6, headers={'User-Agent': 'memory-lane (magenta; links)'},
                              params={'action': 'query', 'titles': '|'.join(asking), 'redirects': 1, 'format': 'json',
                                      'formatversion': 2}).json()
            query = answer.get('query') or {}
            # A title the wiki normalises (a lowercase first letter, say) and then redirects answers under its
            # final title: each asked title is followed forward to it, so the answer comes back as asked.
            normalized = {n['from']: n['to'] for n in (query.get('normalized') or [])}
            redirected = {r['from']: r['to'] for r in (query.get('redirects') or [])}
            final = {}
            for t in asking:
                end = normalized.get(t, t)
                for _ in range(5):  # a redirect to a redirect
                    if end not in redirected:
                        break
                    end = redirected[end]
                final.setdefault(end, []).append(t)
            for page in query.get('pages') or []:
                for asked in final.get(page.get('title'), []):
                    if not page.get('invalid'):
                        there = not page.get('missing')
                        found[asked] = there
                        cache.set(_key(asked), there, KEEP)
        except Exception:  # noqa: BLE001 -- the wiki away: these links keep their plain colour for now
            pass
    return found
