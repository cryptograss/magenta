"""What each emergent is for, as its own page says ({{Emergent|for=...}} on Cryptograss:Emergents/<Name>, at its newest
trusted revision): the same words the bundle's roster gives the other emergents. Shown beside its name when a person
types @ (Skyler, 29 Oct: "put the specific superpower next to the name so our people know which ones do what").

Read from PickiPedia at most every KEEP seconds, in one request for all of them; a wiki that doesn't answer leaves
what was read last, or nothing.
"""

import json
import re
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


# --- pronouns, as each sets their own (PickiPedia's Pronouns preference, pickipedia#162) ---------------------------
# Never a card's or anyone else's say: each person and each emergent sets theirs on their own wiki account (Justin,
# re, blu and magent, 29 Oct: "it absolutely needs to be in a place for which the user is responsible").
PRONOUNS_KEY = 'pronouns'
PRONOUNS_SEP = '\u241f'  # between one name's answer and the next


def wiki_name(name, people_names, agents):
    """Their PickiPedia account: a person's from the inventory, an emergent's its own name, capitalised."""
    if name in people_names:
        return people_names[name]
    return name[:1].upper() + name[1:] if name in agents else None


def pronouns(entities, expand=None):
    """{name: 'it/its'} for those who've set theirs; kept a while (PRONOUNS_KEY). `entities`: [(name, is_human)]."""
    kept = cache.get(PRONOUNS_KEY)
    if kept is not None:
        return kept
    from .wiki_auth import names
    people_names, agents = names(), {n for n, human in entities if not human}
    asked = [(n, wiki_name(n, people_names, agents)) for n, _ in entities]
    asked = [(n, w) for n, w in asked if w and re.fullmatch(r"[\w .'-]{1,80}", w)]
    try:
        text = PRONOUNS_SEP.join(f'{{{{#pronouns:{w}}}}}' for _, w in asked)
        said = (expand or _expand)(text).split(PRONOUNS_SEP) if asked else []
        found = {n: p.strip() for (n, _), p in zip(asked, said) if p.strip() and not p.strip().startswith('{{')}
        cache.set(PRONOUNS_KEY, found, KEEP)
        cache.set(PRONOUNS_KEY + ':last', found, None)
    except Exception:
        found = cache.get(PRONOUNS_KEY + ':last') or {}
        cache.set(PRONOUNS_KEY, found, KEEP_FAILED)
    return found


def _expand(text):
    from poller.wiki_rules import API, USER_AGENT
    url = API + '?' + urllib.parse.urlencode({'action': 'expandtemplates', 'text': text, 'prop': 'wikitext',
                                              'format': 'json'})
    with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': USER_AGENT}), timeout=5) as r:
        return json.load(r)['expandtemplates']['wikitext']


# --- a name's frame, as each sets their own (Justin, re and blu, 1 Nov) ---------------------------------------------
# While a name is at work in the Moods list it stands in a frame: dots by default, up to FRAME_WIDTH characters, so a
# short name gives the cursor room; or the characters its owner chose, as re chose `~re~`. Chosen on their own User
# page, {{Name frame|~}}, and read only from a revision they made themselves: theirs to set, nobody else's.
FRAMES_KEY = 'name-frames'
_FRAME = re.compile(r'\{\{\s*[Nn]ame frame\s*\|\s*([^{}|\n]{1,3}?)\s*\}\}')
_FRAME_OK = re.compile(r'[^\w\s<>&"\'`]{1,3}')  # a few marks: no letters or digits, nothing markup


def frame_in(text):
    """The frame `text` (a User page) chooses, or ''."""
    m = _FRAME.search(text or '')
    return m.group(1) if m and _FRAME_OK.fullmatch(m.group(1)) else ''


def frames(entities, fetch=None):
    """{name: '~'} for those who've chosen a frame; kept a while (FRAMES_KEY). `entities`: [(name, is_human)]."""
    kept = cache.get(FRAMES_KEY)
    if kept is not None:
        return kept
    from .wiki_auth import names
    from poller.wiki_rules import API, USER_AGENT
    people_names, agents = names(), {n for n, human in entities if not human}

    def newest_own(user):
        """Their User page as they last left it themselves: their own newest revision of it."""
        url = API + '?' + urllib.parse.urlencode({
            'action': 'query', 'prop': 'revisions', 'titles': f'User:{user}', 'rvprop': 'content', 'rvslots': 'main',
            'rvuser': user, 'rvlimit': 1, 'format': 'json', 'formatversion': '2'})
        with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': USER_AGENT}), timeout=5) as r:
            page = json.load(r)['query']['pages'][0]
        revs = page.get('revisions') or []
        return revs[0]['slots']['main']['content'] if revs else ''

    try:
        found = {}
        for name, _ in entities:
            user = wiki_name(name, people_names, agents)
            if user and re.fullmatch(r"[\w .'-]{1,80}", user):
                chosen = frame_in((fetch or newest_own)(user))
                if chosen:
                    found[name] = chosen
        cache.set(FRAMES_KEY, found, KEEP)
        cache.set(FRAMES_KEY + ':last', found, None)
    except Exception:
        found = cache.get(FRAMES_KEY + ':last') or {}
        cache.set(FRAMES_KEY, found, KEEP_FAILED)
    return found
