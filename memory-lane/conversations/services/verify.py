"""The pages the agents are told from, and whether each is verified (Justin, 31 Oct, #interfaces: "a page with links
to all the places that need to be verified for regular operation", in the menu beside status, prompts and todo).

Not a list kept by hand: the pages are the ones the runners' bundles named this past week (BundleRecord.stamps,
poller/instructions.py), and the wake's own pages (poller/wiki_rules.py: Wake, Dials; Pronouns). A page is verified
when its newest revision is a trusted editor's (wiki_rules.EDITORS); one whose newest edit is someone else's is
told to the agents at its last trusted revision until a trusted editor saves it, so it waits for a verify.
"""

import json
import urllib.parse
import urllib.request
from datetime import timedelta

from django.core.cache import cache

KEY = 'verify-pages'
KEEP = 120  # seconds
LOOK_BACK = timedelta(days=7)


def watched():
    """Every page an agent's instructions or wake were read from lately, sorted."""
    from django.utils import timezone

    from conversations.models import BundleRecord
    from poller.mood_poller import PRONOUNS_PAGE
    from poller.wiki_rules import DIALS, WAKE

    titles = {WAKE, DIALS, PRONOUNS_PAGE}
    for stamps in BundleRecord.objects.filter(requested_at__gt=timezone.now() - LOOK_BACK)\
            .values_list('stamps', flat=True).iterator():
        greeting = (stamps or {}).get('greeting') or {}
        layers = [greeting, greeting.get('agent_page') or {}, *(greeting.get('roster') or {}).values(),
                  *((stamps or {}).get('layers') or {}).values()]
        for layer in layers:
            if isinstance(layer, dict) and layer.get('page') and not layer.get('absent'):
                titles.add(layer['page'])
    return sorted(titles)


def _ask(params):
    from poller.wiki_rules import API, USER_AGENT
    url = API + '?' + urllib.parse.urlencode({**params, 'format': 'json', 'formatversion': '2'})
    with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': USER_AGENT}), timeout=10) as r:
        return json.load(r)


def _history(title):
    """[(revid, user, timestamp)], newest first, as far back as the runners look (wiki_rules.HISTORY)."""
    from poller.wiki_rules import HISTORY
    page = _ask({'action': 'query', 'prop': 'revisions', 'titles': title, 'redirects': '1',
                 'rvprop': 'ids|user|timestamp', 'rvlimit': HISTORY})['query']['pages'][0]
    return [(r['revid'], r.get('user', ''), r.get('timestamp', '')) for r in page.get('revisions') or []]


def state(history=_history):
    """{'pages': [...], 'editors': [...], 'waiting': n, 'error': str}: each page, its newest revision, its
    newest trusted one, and what that means; those waiting on a person first. Cached KEEP seconds."""
    from django.conf import settings

    from poller.wiki_rules import EDITORS, page_url

    found = cache.get(KEY) if history is _history else None
    if found is not None:
        return found
    editors = list(getattr(settings, 'MOOD_WIKI_RULES_EDITORS', None) or EDITORS)
    trusted = {e.strip().lower().replace('_', ' ') for e in editors}
    rows, error = [], ''
    for title in watched():
        row = {'page': title, 'url': page_url(title)}
        try:
            revisions = history(title)
        except Exception as e:  # noqa: BLE001 -- PickiPedia down or slow: said, and the rest still listed
            error = f"PickiPedia couldn't be read ({type(e).__name__})"
            rows.append({**row, 'state': 'unread'})
            continue
        if not revisions:
            rows.append({**row, 'state': 'missing'})
            continue
        newest = revisions[0]
        good = next((r for r in revisions if (r[1] or '').lower().replace('_', ' ') in trusted), None)
        row.update(revision=newest[0], by=newest[1], at=newest[2])
        if good is None:
            row['state'] = 'untrusted'  # nothing an agent may be told: all-hands if the runner never read it
        elif good[0] == newest[0]:
            row['state'] = 'verified'
        else:
            pending = revisions[:revisions.index(good)]
            row.update(state='waiting', trusted=good[0], edits=len(pending),
                       editors=sorted({r[1] for r in pending}),
                       diff=page_url(title).split('/wiki/')[0] + '/index.php?'
                       + urllib.parse.urlencode({'diff': newest[0], 'oldid': good[0]}))
        rows.append(row)
    order = {'untrusted': 0, 'missing': 0, 'waiting': 1, 'unread': 2, 'verified': 3}
    rows.sort(key=lambda r: (order[r['state']], r['page']))
    found = {'pages': rows, 'editors': editors, 'error': error,
             'waiting': sum(r['state'] in ('waiting', 'untrusted', 'missing') for r in rows)}
    if history is _history:
        cache.set(KEY, found, KEEP)
    return found
