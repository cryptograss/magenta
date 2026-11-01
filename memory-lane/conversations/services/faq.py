"""The FAQ as slides (Justin, 1 Nov, #general: "a magenta-styled page to show these ... a slideshow-esque
presentation"). Kept on PickiPedia (Magenta:FAQ), where it's written; read here as MediaWiki renders it, so links
and formatting come through, and split into slides in the page: each top-level bullet is a question, what's nested
under it the answer.

Read with plain HTTP at most every KEEP seconds; if the wiki can't be reached, the last copy read stands.
"""

import json
import urllib.parse
import urllib.request

from django.core.cache import cache

PAGE = 'Magenta:FAQ'
API = 'https://pickipedia.xyz/api.php'
KEY = 'faq-page'
LAST = 'faq-page-last'
KEEP = 300  # seconds
USER_AGENT = 'magenta-faq/1 (https://github.com/cryptograss/magenta; memory-lane/conversations/services/faq.py)'


def page_url():
    return 'https://pickipedia.xyz/wiki/' + urllib.parse.quote(PAGE.replace(' ', '_'), safe='/:')


def _fetch():
    query = urllib.parse.urlencode({'action': 'parse', 'page': PAGE, 'prop': 'text|revid',
                                    'disableeditsection': 1, 'format': 'json', 'formatversion': 2})
    request = urllib.request.Request(f'{API}?{query}', headers={'User-Agent': USER_AGENT})
    with urllib.request.urlopen(request, timeout=8) as response:
        parsed = json.load(response)['parse']
    return {'html': parsed['text'], 'revid': parsed.get('revid'), 'error': ''}


def state():
    """{'html', 'revid', 'error'}: the page as rendered, its revision, and why it couldn't be read, if it couldn't."""
    got = cache.get(KEY)
    if got:
        return got
    try:
        got = _fetch()
        cache.set(LAST, got, None)
    except Exception as e:  # the wiki down, or the page gone: the last copy, else say so
        got = dict(cache.get(LAST) or {'html': '', 'revid': None}, error=f'PickiPedia could not be read ({e})')
    cache.set(KEY, got, KEEP)
    return got
