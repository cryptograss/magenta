"""Voice tag aliases on PickiPedia User pages (Justin, #interfaces, 16 Nov 2026:
"people to be able to put voice tag aliases on their Pickipedia user page to
have those people tagged in" -- e.g. Nick / Silver Bucket for slvrbckt, R.J.
for rj, Sky for skyler).

Each person sets their own on `User:<PickiPedia name>` with:

    {{Voice aliases|Nick|Nick Jennings|Silver Bucket}}

or on their existing `{{Magenta person|...|voice=Nick, Silver Bucket}}` badge.
Only a revision saved by that account itself or by a trusted editor counts;
examples in comments, `<nowiki>`, `<pre>` or unverified bot proposals are never
read. Saving a `User:*` page runs the `card` wiki hook, which forgets this
cache at once.
"""

import json
import logging
import re
import urllib.parse
import urllib.request

from django.core.cache import cache

log = logging.getLogger(__name__)

KEY = 'voice-aliases'
KEEP = 3600
KEEP_FAILED = 300
MAX_PER_USER = 20
MAX_LEN = 40

_IGNORED = re.compile(r'<!--.*?-->|<nowiki>.*?</nowiki>|<pre>.*?</pre>|<proposed\b[^>]*>.*?</proposed>', re.S | re.I)
_BOT_START = re.compile(r'\{\{\s*Bot[_ ]proposes\s*\|', re.I)
_VOICE_TPL = re.compile(r'\{\{\s*Voice[_ ](?:aliases|tags?)\s*\|([^{}]*)\}\}', re.I)
_PERSON_TPL = re.compile(r'\{\{\s*Magenta[_ ]person\s*\|([^{}]*)\}\}', re.I)
_ALIAS_OK = re.compile(r"[A-Za-z0-9][A-Za-z0-9 .'-]{0,39}")
_RESERVED = {
    'at', 'in', 'on', 'to', 'of', 'or', 'and', 'the', 'for', 'you', 'all', 'here', 'now',
    'hey', 'hi', 'hello', 'ok', 'okay', 'yo', 'everyone', 'emergents', 'emergent',
    'agents', 'agent', 'mood', 'moods', 'magenta', 'pickipedia', 'system',
}


def _strip_unverified(text):
    cleaned = _IGNORED.sub('', text or '')
    while True:
        m = _BOT_START.search(cleaned)
        if not m:
            return cleaned
        depth, i, end = 1, m.end(), len(cleaned)
        while i < end and depth > 0:
            if cleaned[i:i + 2] == '{{':
                depth += 1
                i += 2
            elif cleaned[i:i + 2] == '}}':
                depth -= 1
                i += 2
            else:
                i += 1
        cleaned = cleaned[:m.start()] + cleaned[i:]


def _clean_item(raw):
    item = re.sub(r'\s+', ' ', (raw or '').strip().lstrip('@')).strip()
    if len(item) < 2 or len(item) > MAX_LEN or not _ALIAS_OK.fullmatch(item):
        return ''
    if item.lower().rstrip('.') in _RESERVED:
        return ''
    return item


def aliases_in(text):
    """The voice aliases written on one User page (preserving original casing)."""
    cleaned = _strip_unverified(text)
    raw_chunks = []
    for m in _VOICE_TPL.finditer(cleaned):
        for part in m.group(1).split('|'):
            if '=' in part:
                k, _, v = part.partition('=')
                if k.strip().isdigit() or k.strip().lower() in ('aliases', 'voice', 'names'):
                    raw_chunks.append(v)
            else:
                raw_chunks.append(part)
    for m in _PERSON_TPL.finditer(cleaned):
        for part in m.group(1).split('|'):
            if '=' not in part:
                continue
            k, _, v = part.partition('=')
            if k.strip().lower().replace('_', ' ') in ('voice', 'aliases', 'voice aliases'):
                raw_chunks.append(v)
    found, seen = [], set()
    for chunk in raw_chunks:
        for piece in re.split(r'[,;]', chunk):
            item = _clean_item(piece)
            if item and item.lower() not in seen and len(found) < MAX_PER_USER:
                seen.add(item.lower())
                found.append(item)
    return found


def initial_variants(alias):
    """Lowercased spoken forms for `alias`, expanding 2-3 letter initials ('rj' <-> 'r.j.')."""
    low = re.sub(r'\s+', ' ', (alias or '').strip().lower())
    if not low:
        return []
    if re.fullmatch(r'[a-z]{2,3}', low) and low not in _RESERVED:
        return list(dict.fromkeys([low, '.'.join(low) + '.', '. '.join(low) + '.']))
    if re.fullmatch(r'[a-z](?:\.\s*[a-z]){1,2}\.?', low):
        letters = re.sub(r'[^a-z]', '', low)
        if letters not in _RESERVED:
            return list(dict.fromkeys([low, letters, '.'.join(letters) + '.', '. '.join(letters) + '.', ' '.join(letters)]))
    return [low]


def _norm_user(name):
    return re.sub(r'\s+', ' ', (name or '').replace('_', ' ')).strip().lower()


def _wiki_user_of(name, people_names):
    if name in people_names:
        return people_names[name]
    spaced = name.replace('_', ' ').strip()
    return spaced[:1].upper() + spaced[1:] if spaced else ''


def _fetch_pages(users):
    """{wiki_user: trusted_or_own_wikitext} from PickiPedia, batched 50 at a time."""
    from django.conf import settings
    from poller.wiki_rules import API, EDITORS, USER_AGENT
    trusted = {_norm_user(e) for e in (getattr(settings, 'MOOD_WIKI_RULES_EDITORS', None) or EDITORS)}
    by_norm = {_norm_user(u): u for u in users}
    out = {}
    ulist = list(by_norm.values())
    for i in range(0, len(ulist), 50):
        batch = ulist[i:i + 50]
        url = API + '?' + urllib.parse.urlencode({
            'action': 'query', 'prop': 'revisions', 'titles': '|'.join(f'User:{u}' for u in batch),
            'rvprop': 'content|user', 'rvslots': 'main', 'format': 'json', 'formatversion': '2'})
        with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': USER_AGENT}), timeout=5) as r:
            pages = (json.load(r).get('query') or {}).get('pages') or []
        for page in pages:
            if page.get('missing'):
                continue
            owner_norm = _norm_user((page.get('title') or '')[5:])
            orig = by_norm.get(owner_norm)
            if not orig:
                continue
            revs = page.get('revisions') or []
            if not revs:
                continue
            top_user = _norm_user(revs[0].get('user', ''))
            if top_user == owner_norm or top_user in trusted:
                out[orig] = (revs[0].get('slots') or {}).get('main', {}).get('content', '')
                continue
            # Someone else edited it last: walk recent revisions for the newest by owner or trusted editor.
            hurl = API + '?' + urllib.parse.urlencode({
                'action': 'query', 'prop': 'revisions', 'titles': f'User:{orig}',
                'rvprop': 'content|user', 'rvslots': 'main', 'rvlimit': 10, 'format': 'json', 'formatversion': '2'})
            with urllib.request.urlopen(urllib.request.Request(hurl, headers={'User-Agent': USER_AGENT}), timeout=5) as hr:
                hpages = (json.load(hr).get('query') or {}).get('pages') or []
            for rev in (hpages[0].get('revisions') or []) if hpages else []:
                who = _norm_user(rev.get('user', ''))
                if who == owner_norm or who in trusted:
                    out[orig] = (rev.get('slots') or {}).get('main', {}).get('content', '')
                    break
    return out


def read(names_here=None, fetch=None):
    """{'aliases': {spoken_lower: entity_name}, 'keyterms': [str]} across human accounts."""
    from django.conf import settings
    if fetch is None and not getattr(settings, 'VOICE_WORDS_FROM_WIKI', True):
        return {'aliases': {}, 'keyterms': []}
    if names_here is None:
        from conversations.models import ThinkingEntity
        names_here = list(ThinkingEntity.objects.filter(is_biological_human=True).values_list('name', flat=True))
    from .wiki_auth import names as inventory_names
    people_names = inventory_names()
    user_for = {}
    for name in names_here:
        w = _wiki_user_of(name, people_names)
        if w and re.fullmatch(r"[\w .'-]{1,80}", w):
            user_for[name] = w
    if not user_for:
        return {'aliases': {}, 'keyterms': []}
    pages = (fetch or _fetch_pages)(list(user_for.values()))
    claimed, ambiguous, terms, seen_terms = {}, set(), [], set()
    for entity_name, wuser in user_for.items():
        wikitext = pages.get(wuser) or ''
        for raw in aliases_in(wikitext):
            if raw.lower() not in seen_terms:
                seen_terms.add(raw.lower())
                terms.append(raw)
            for variant in initial_variants(raw):
                prev = claimed.get(variant)
                if prev is not None and prev != entity_name:
                    ambiguous.add(variant)
                else:
                    claimed[variant] = entity_name
    for bad in ambiguous:
        claimed.pop(bad, None)
    return {'aliases': claimed, 'keyterms': terms}


def state(fetch=None):
    """Cached {'aliases': ..., 'keyterms': ...}; falls back to last good read when wiki is unreachable."""
    if fetch is None:
        kept = cache.get(KEY)
        if kept is not None:
            return kept
    try:
        found = read(fetch=fetch)
        cache.set(KEY, found, KEEP)
        cache.set(KEY + ':last', found, None)
        return found
    except Exception as e:  # noqa: BLE001 -- blocked in offline tests or wiki briefly unreachable
        log.debug('voice aliases: %s', e)
        found = cache.get(KEY + ':last') or {'aliases': {}, 'keyterms': []}
        if fetch is None:
            cache.set(KEY, found, KEEP_FAILED)
        return found


def aliases():
    return dict(state()['aliases'])


def keyterms():
    return list(state()['keyterms'])


def forget():
    cache.delete(KEY)
