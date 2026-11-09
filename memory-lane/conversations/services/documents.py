"""Magenta's own pages, read in magenta (Justin, 9 Nov, #interfaces: "if something's in the magenta namespace and it
has the category of top level document, then it just automatically gets included as slash whatever its name is").

A page in the Magenta namespace with [[Category:Magenta top-level documents]] is served at /<its name>/: Magenta:About
at /about/, Magenta:Signing in at /signing-in/. Add the category and the page is there within a minute; take it off
and it's gone. A name some other part of magenta already answers (/status/, /tidings/, /moods/...) is that part's,
so a page called Magenta:Status is never shown here.

How it's shown is the page's own say, by a second category: with [[Category:Magenta slideshows]] it's slides, each
top-level bullet a slide and what's nested under it that slide's body, as the FAQ has always been; without it, a
straight document. The FAQ is the first slideshow (services/faq.py; /faq/).

Read as MediaWiki renders it, with plain HTTP, at most every KEEP seconds; if the wiki can't be reached, the last
copy read stands.
"""

import json
import re
import urllib.parse
import urllib.request

from django.core.cache import cache

API = 'https://pickipedia.xyz/api.php'
WIKI = 'https://pickipedia.xyz/wiki/'
NAMESPACE = 3012  # Magenta
PREFIX = 'Magenta:'
LISTED = 'Magenta top-level documents'
SLIDES = 'Magenta slideshows'
KEEP = 30  # seconds: an edit, or a category added, shows within half a minute
LIST_KEY = 'documents:list'
USER_AGENT = 'magenta-documents/1 (https://github.com/cryptograss/magenta; memory-lane/conversations/services/documents.py)'


def name_of(title):
    """Its address: 'Magenta:Signing in' -> 'signing-in'."""
    bare = title[len(PREFIX):] if title.startswith(PREFIX) else title
    return re.sub(r'[^a-z0-9]+', '-', bare.lower()).strip('-')


def page_url(title):
    return WIKI + urllib.parse.quote(title.replace(' ', '_'), safe='/:')


def _get(params):
    query = urllib.parse.urlencode({**params, 'format': 'json', 'formatversion': 2})
    request = urllib.request.Request(f'{API}?{query}', headers={'User-Agent': USER_AGENT})
    with urllib.request.urlopen(request, timeout=8) as response:
        return json.load(response)


def _kept(key, fetch):
    """fetch(), kept KEEP seconds; the wiki down, the last copy read, else None."""
    got = cache.get(key)
    if got is not None:
        return got
    try:
        got = fetch()
        cache.set(key + ':last', got, None)
    except Exception:
        got = cache.get(key + ':last')
        if got is None:
            return None
    cache.set(key, got, KEEP)
    return got


def listed():
    """{name: title} for every Magenta page in the top-level category."""
    def fetch():
        out, more = {}, {}
        while True:
            got = _get({'action': 'query', 'list': 'categorymembers', 'cmtitle': f'Category:{LISTED}',
                        'cmnamespace': NAMESPACE, 'cmlimit': 500, **more})
            for m in got.get('query', {}).get('categorymembers', []):
                if m.get('title', '').startswith(PREFIX) and name_of(m['title']):
                    out.setdefault(name_of(m['title']), m['title'])
            more = got.get('continue') or {}
            if not more:
                return out
    return _kept(LIST_KEY, fetch) or {}


def read(title):
    """{'title', 'html', 'revid', 'slides', 'error'} for a page as MediaWiki renders it."""
    def fetch():
        parsed = _get({'action': 'parse', 'page': title, 'prop': 'text|revid|categories|displaytitle',
                       'disableeditsection': 1})['parse']
        cats = {c.get('category', '').replace('_', ' ') for c in parsed.get('categories') or ()}
        shown = re.sub(r'<[^>]+>', '', parsed.get('displaytitle') or title)
        return {'title': shown[len(PREFIX):] if shown.startswith(PREFIX) else shown, 'html': parsed['text'],
                'revid': parsed.get('revid'), 'slides': SLIDES in cats, 'error': ''}
    got = _kept('documents:page:' + title, fetch)
    if got is None:
        bare = title[len(PREFIX):] if title.startswith(PREFIX) else title
        return {'title': bare, 'html': '', 'revid': None, 'slides': False, 'error': 'PickiPedia could not be read'}
    return got


def forget(title=None):
    """Read again at the next look: one page (an edit to it), or the list too."""
    cache.delete(LIST_KEY)
    if title:
        cache.delete('documents:page:' + title)
