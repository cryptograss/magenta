"""What each emergent is for, as its own page says ({{Emergent|for=...}} on Cryptograss:Emergents/<Name>, at its newest
trusted revision): the same words the bundle's roster gives the other emergents. Shown beside its name when a person
types @ (Skyler, 29 Oct: "put the specific superpower next to the name so our people know which ones do what").

Read from PickiPedia at most every KEEP seconds, in one request for all of them; a wiki that doesn't answer leaves
what was read last, or nothing.
"""

import json
import urllib.parse
import urllib.request

from django.core.cache import cache

KEY = 'emergent-powers'
KEEP = 3600            # seconds the words are kept
KEEP_FAILED = 300      # and how soon a failed read is tried again
SHORT = 44             # characters of the short form, shown in the menu (the whole of it on hover)


def short(words):
    """Its first clause, cut to SHORT: 'studies the connectome', 'maintains the magenta project'."""
    first = words.split(';')[0].split(':')[0].strip()
    if len(first) <= SHORT:
        return first
    cut = first[:SHORT].rsplit(' ', 1)[0].rstrip(',;: ')  # at a word
    return cut + '…'


def read(agents, fetch=None):
    """{agent: words} for those whose page has a trusted revision with an {{Emergent|for=...}}."""
    from poller.instructions import emergent_title, template_fields
    from poller.wiki_rules import API, EDITORS, USER_AGENT
    trusted = {e.lower().replace('_', ' ') for e in EDITORS}
    titles = {emergent_title(a): a for a in agents}

    def fetch_all():
        out = {}
        for title in titles:  # one each: a page's history, newest first (revisions can't be listed for several)
            url = API + '?' + urllib.parse.urlencode({
                'action': 'query', 'prop': 'revisions', 'titles': title, 'redirects': '1', 'rvprop': 'content|user',
                'rvslots': 'main', 'rvlimit': 10, 'format': 'json', 'formatversion': '2'})
            with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': USER_AGENT}), timeout=5) as r:
                page = json.load(r)['query']['pages'][0]
            out[title] = [(rev.get('user', ''), rev['slots']['main']['content']) for rev in page.get('revisions') or []]
        return out

    found = {}
    for title, history in (fetch or fetch_all)().items():
        text = next((t for user, t in history if (user or '').lower().replace('_', ' ') in trusted), None)
        words = (template_fields(text).get('for') or '').strip() if text else ''
        if words and title in titles:
            found[titles[title]] = words
    return found


def powers(agents, fetch=None):
    """{agent: {'for', 'short'}}, kept a while (KEY)."""
    kept = cache.get(KEY)
    if kept is not None:
        return kept
    try:
        found = {a: {'for': w, 'short': short(w)} for a, w in read(agents, fetch).items()}
        cache.set(KEY, found, KEEP)
    except Exception:
        found = cache.get(KEY + ':last') or {}
        cache.set(KEY, found, KEEP_FAILED)
        return found
    cache.set(KEY + ':last', found, None)
    return found
