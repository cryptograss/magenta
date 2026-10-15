"""Tokens shown beside people's names, from what PickiPedia knows.

For now, Blue Railroads: how many each person holds, all songs together.
The "Blue Railroad Imports" bot keeps a page per token on PickiPedia, current
every couple of minutes across both contracts, with an Owner Address and
burned tokens in a category of their own. A person is linked to their wallets
by an Ethereum Address on their PickiPedia user page ({{#set: |Ethereum
Address=0x...}}), and the name here is theirs by wiki_auth.local_name_for.
Owner addresses are matched without regard to case.
"""

import logging
from collections import Counter

from django.core.cache import cache

logger = logging.getLogger(__name__)

BLUE_RAILROADS = 'tokens:blue-railroad'
BLUE_RAILROADS_FOR = 600     # seconds a count is kept
BLUE_RAILROADS_LAST = 'tokens:blue-railroad:last'  # the last good count, for when the wiki doesn't answer
PAGE = 500


def _ask_all(query):
    """Every result of a Semantic MediaWiki query: {title: printouts}."""
    from .embeds import _ask
    found, offset = {}, 0
    while offset is not None:
        answer = _ask({'action': 'ask', 'query': f'{query}|limit={PAGE}|offset={offset}'})
        found.update({title: page.get('printouts', {}) for title, page in (answer.get('query', {}).get('results') or {}).items()})
        offset = answer.get('query-continue-offset')
    return found


def _address(value):
    text = value.get('fulltext', '') if isinstance(value, dict) else str(value or '')
    return text.strip().lower()


def blue_railroads():
    """{name here: Blue Railroad tokens they hold}, for everyone holding any."""
    from .wiki_auth import local_name_for
    found = cache.get(BLUE_RAILROADS)
    if found is not None:
        return found
    try:
        held_by = Counter(_address(a) for printouts in _ask_all('[[Category:Blue Railroad Tokens]]|?Owner Address').values()
                          for a in printouts.get('Owner Address', []))
        counts = Counter()
        for title, printouts in _ask_all('[[Ethereum Address::+]]|?Ethereum Address').items():
            if not title.startswith('User:'):
                continue
            wallets = {_address(a) for a in printouts.get('Ethereum Address', [])}
            counts[local_name_for(title[len('User:'):])] += sum(held_by.get(w, 0) for w in wallets if w)
        found = {name: n for name, n in counts.items() if n}
    except Exception as e:  # the wiki away: the last count, asked again in a minute
        logger.warning(f'Blue Railroad counts: {e}')
        found = cache.get(BLUE_RAILROADS_LAST) or {}
        cache.set(BLUE_RAILROADS, found, 60)
        return found
    cache.set(BLUE_RAILROADS, found, BLUE_RAILROADS_FOR)
    cache.set(BLUE_RAILROADS_LAST, found, None)
    return found
