"""Eras, read from Cryptograss:Eras: a message's era comes from its block.

An era starts at a block and runs until the next one starts
(https://pickipedia.xyz/wiki/Cryptograss:Eras). Nothing about eras is stored
on a message; the page is the one place they are declared. Each era there is
an {{Era}} call, so it can be verified like any other claim, and a call still
inside a bot's proposal is not yet an era: it is left out until a person
verifies it.

Messages imported from Cursor have no time and so no block. They keep the
era their conversation's label gives them ("Foundation (Era 0)",
"Compacting Meta-Conversation (Era 1)"); no other label names an era.
"""
import logging
import re
from bisect import bisect_right
from dataclasses import dataclass

from django.conf import settings
from django.core.cache import cache
from django.db.models import Q

logger = logging.getLogger(__name__)

PAGE = 'Cryptograss:Eras'
CACHE_KEY = 'eras:v1'
CACHE_SECONDS = 600
LABELLED = re.compile(r'\(Era (\d+)\)\s*$')
# The two labels that name an era: the Cursor imports, which have no times.
CURSOR_HEAPS = Q(context_heap__era__name__endswith='(Era 0)') | Q(context_heap__era__name__endswith='(Era 1)')


@dataclass(frozen=True)
class Era:
    number: int
    name: str
    start: int
    declared_by: str
    declared_at: int | None

    def as_dict(self):
        return {'number': self.number, 'name': self.name, 'start': self.start,
                'declared_by': self.declared_by, 'declared_at': self.declared_at}


def template_calls(text, name='Era'):
    """(params, proposed) for each {{name ...}} call in text, in order. proposed: the call sits inside
    an unverified <proposed>…</proposed> or {{Bot_proposes|…}}."""
    calls = []
    opening = re.compile(r'\{\{\s*' + re.escape(name) + r'\s*[|}\n]', re.I)
    for found in opening.finditer(text):
        start, depth, i = found.start(), 0, found.start()
        while i < len(text) - 1:
            pair = text[i:i + 2]
            if pair == '{{':
                depth, i = depth + 1, i + 2
            elif pair == '}}':
                depth, i = depth - 1, i + 2
                if depth == 0:
                    break
            else:
                i += 1
        body = text[start + 2:i - 2]
        params = {}
        for part in body.split('|')[1:]:
            key, eq, value = part.partition('=')
            if eq:
                params[key.strip()] = value.strip()
        before = text[:start]
        proposed = (before.rfind('<proposed') > before.rfind('</proposed>')
                    or _inside_bot_proposes(text, start))
        calls.append((params, proposed))
    return calls


def _inside_bot_proposes(text, at):
    for found in re.finditer(r'\{\{\s*Bot[_ ]proposes\s*\|', text[:at], re.I):
        depth, i = 0, found.start()
        while i < at:
            pair = text[i:i + 2]
            if pair in ('{{', '}}'):
                depth += 1 if pair == '{{' else -1
                i += 2
            else:
                i += 1
        if depth > 0:
            return True
    return False


def number(value):
    try:
        return int(str(value).replace(',', '').strip())
    except (TypeError, ValueError):
        return None


def parse(text):
    """(eras, waiting): the page's verified eras sorted by start, and how many calls await verification
    or are left out because a number in them doesn't read."""
    eras, waiting = [], 0
    for params, proposed in template_calls(text):
        era = Era(number(params.get('number')), params.get('name', ''), number(params.get('start')),
                  params.get('declared_by', ''), number(params.get('declared_at')))
        if proposed or era.number is None or era.start is None:
            waiting += 1
            continue
        eras.append(era)
    return sorted(eras, key=lambda e: e.start), waiting


def fetch():
    import requests
    base = getattr(settings, 'PICKIPEDIA_URL', 'https://pickipedia.xyz').rstrip('/')
    response = requests.get(f'{base}/index.php', params={'title': PAGE, 'action': 'raw'}, timeout=10)
    response.raise_for_status()
    return response.text


def eras():
    """The verified eras, oldest first, cached for ten minutes. [] if the page can't be read: an era
    unknown is said as unknown, never guessed."""
    found = cache.get(CACHE_KEY)
    if found is None:
        try:
            found, waiting = parse(fetch())
        except Exception as e:
            logger.warning(f'Could not read {PAGE}: {e}')
            return []
        if waiting:
            logger.info(f'{PAGE}: {waiting} era(s) wait for verification and are left out')
        cache.set(CACHE_KEY, found, CACHE_SECONDS)
    return found


def era_of_block(block, known=None):
    """The era whose span holds this block, or None (no block, or before the first verified era)."""
    if block is None:
        return None
    known = eras() if known is None else known
    at = bisect_right([e.start for e in known], block) - 1
    return known[at] if at >= 0 else None


def era_of(message, known=None):
    """A message's era: by its block; with no block, by its conversation's label when that label
    names an era (the Cursor imports); else None."""
    known = eras() if known is None else known
    if message.eth_blockheight is not None:
        return era_of_block(message.eth_blockheight, known)
    heap = getattr(message, 'context_heap', None)
    label = getattr(getattr(heap, 'era', None), 'name', '') or ''
    labelled = LABELLED.search(label)
    if labelled and labelled.group(1) in ('0', '1'):
        return next((e for e in known if e.number == int(labelled.group(1))), None)
    return None
