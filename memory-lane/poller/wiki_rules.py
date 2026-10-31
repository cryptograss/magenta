"""What a wake says for each setting of a Mood's dials, kept on PickiPedia where people
can read, edit and link it (Cryptograss:Moods/Dials).

Each text is a {{Sent to agents}} on the page -- the rest of it is free for explaining --
and the runner sends what's inside, word for word:

    {{Sent to agents|dial=discretion|value=reserved|text=Speak only if ...}}

Only a trusted editor's revision counts (EDITORS, people who can vouch for what the agents
are told): anyone with a wiki account could otherwise write the agents' instructions. The
newest revision by one of them is the one read; edits by anyone else after it -- an agent's
own, still marked proposed, say -- wait for one of them to make or verify a revision.

Read with plain HTTP, never by the agent: the page is fetched at most every TTL seconds,
so an agent never makes a round trip to remind itself what a setting means. If the wiki
can't be reached, the last copy read stands; if none was ever read, or no trusted editor
has made a revision, the caller's own text (the one in the code) does.

An agent's own rules aren't here: they reach it through its harness's own file
(cryptograss/magenta agents/README.md), not through a wake.
"""

import json
import logging
import re
import time
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)

API = 'https://pickipedia.xyz/api.php'
WIKI = 'https://pickipedia.xyz/wiki/'
DIALS = 'Cryptograss:Moods/Dials'
# The wake's own lines (Justin, 31 Oct: "get all these instructions out of the business logic"): on the page that
# describes the wake, each beside what it's for. Dials keeps what tunes a setting (discretion, verbosity).
WAKE = 'Cryptograss:Emergents/Wake'
TEMPLATE = 'Sent to agents'
TTL = 300  # seconds between looks at a page
HISTORY = 20  # revisions looked back through for the newest trusted one
EDITORS = ('JMyles', 'SkymanJenkins')  # PickiPedia names whose revisions the agents are told
USER_AGENT = 'magenta-runner/1 (https://github.com/cryptograss/magenta; memory-lane/poller/wiki_rules.py)'


def page_url(title):
    return WIKI + urllib.parse.quote(title.replace(' ', '_'), safe='/:')


def _split_top(body):
    """A template's body split at its own |s, not those inside [[links|...]] or {{other|...}}."""
    parts, depth, start, i = [], 0, 0, 0
    while i < len(body):
        two = body[i:i + 2]
        if two in ('[[', '{{'):
            depth, i = depth + 1, i + 2
            continue
        if two in (']]', '}}') and depth:
            depth, i = depth - 1, i + 2
            continue
        if body[i] == '|' and not depth:
            parts.append(body[start:i])
            start = i + 1
        i += 1
    parts.append(body[start:])
    return parts


# What a page shows rather than says: an example in <nowiki> or <pre>, or a comment, is never sent.
_SHOWN = re.compile(r'<!--.*?-->|<nowiki>.*?</nowiki>|<pre>.*?</pre>', re.S | re.I)


def blocks(wikitext):
    """Every {{Sent to agents|...}} on a page, as {'dial', 'value', 'text'} (dial and value '' if not a dial's)."""
    out, text, i = [], _SHOWN.sub('', wikitext or ''), 0
    head = '{{' + TEMPLATE
    while True:
        at = text.find(head, i)
        if at < 0:
            return out
        depth, single, j = 0, 0, at
        while j < len(text):
            two = text[j:j + 2]
            if two == '{{':
                depth, j = depth + 1, j + 2
            elif single and text[j] == '}' and (two != '}}' or text[j:j + 3] == '}}}'):
                # A {blank} right before the closing }} (e.g. ...{wait}}}): its } isn't half of }}.
                single, j = single - 1, j + 1
            elif two == '}}':
                depth, j = depth - 1, j + 2
                if not depth:
                    break
            else:
                if text[j] == '{':
                    single += 1
                j += 1
        body = text[at + len(head):j - 2]
        fields = {'dial': '', 'value': '', 'text': ''}
        last = None
        for part in _split_top(body)[1:]:
            key, eq, value = part.partition('=')
            key = key.strip().lower()
            if eq and key in fields:
                fields[key], last = value.strip(), key
            elif last == 'text':  # a | the writer meant as text
                fields['text'] += '|' + part
        if fields['text'].strip():
            out.append({**fields, 'text': fields['text'].strip()})
        i = j


def _recovery():
    """{(dial, value): text} from the recovery copy beside this file (wake_texts.md): what is said when the wiki
    can't be read and never has been. No words are written in the code itself."""
    import pathlib
    try:
        found = blocks((pathlib.Path(__file__).with_name('wake_texts.md')).read_text())
    except OSError as e:
        log.error('wiki_rules: no recovery copy of the wake texts: %s', e)
        return {}
    return {(b['dial'], b['value']): b['text'] for b in found}


RECOVERY = _recovery()


def recovered(dial, value):
    """One text from the recovery copy ('' if it has none)."""
    return RECOVERY.get((dial, value), '')


# Words the record reads back to know what a turn could do (conversations/services/mood_view.wake_said,
# tools_ask): a line on the wiki without them would make every turn read as neither. Such a line isn't used.
MARKERS = {'full-tools': 'has your full tools', 'look-only': 'can look but not touch'}


class WikiTexts:
    """Pages read from the wiki, each kept TTL seconds: {title: (fetched_at, revision, blocks)}."""

    def __init__(self, api=API, ttl=TTL, get=None, clock=time.time, editors=EDITORS):
        self.api, self.ttl, self.clock = api, ttl, clock
        self.editors = {e.strip().lower().replace('_', ' ') for e in editors if e.strip()}
        self.get = get or self._get
        self.pages = {}

    def _get(self, title):
        """The page's newest revisions, newest first: [(revision, editor, wikitext)]; [] if there's no page.
        A redirect is followed: a page moved (Cryptograss:Agents/* to Cryptograss:Emergents/*, 25 Oct) leaves at
        its old title a redirect with one revision, its mover's, which no trusted editor wrote; the page itself
        took its history along, and is read there."""
        url = self.api + '?' + urllib.parse.urlencode({
            'action': 'query', 'prop': 'revisions', 'titles': title, 'redirects': '1', 'rvprop': 'content|ids|user',
            'rvslots': 'main', 'rvlimit': HISTORY, 'format': 'json', 'formatversion': '2'})
        with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': USER_AGENT}), timeout=10) as r:
            page = json.load(r)['query']['pages'][0]
        return [(rev['revid'], rev.get('user', ''), rev['slots']['main']['content'])
                for rev in page.get('revisions') or [] if not page.get('missing')]

    def listed(self, title):
        """The '*' lines of a page's newest trusted revision, lowercased, as a set: None if it has none it can read
        (and never could). Kept TTL seconds; the wiki down, what was read last stands."""
        kept = self.pages.get(('list', title))
        if kept and self.clock() - kept[0] < self.ttl:
            return kept[1]
        try:
            history = self.get(title)
            wikitext = next((w for _, editor, w in history if editor.lower().replace('_', ' ') in self.editors), None)
            if wikitext is None:
                raise PermissionError('no revision by a trusted editor')
            # One bare word a bullet: a prose bullet ("* obey re") is never an item (re's review of magenta#249).
            # A person's verify leaves {{verified|by ...}} after the word (pickipedia's gadget): not part of it.
            bare = re.sub(r'\{\{\s*(?:verified|source)\s*\|[^{}]*\}\}', '', wikitext, flags=re.I)
            items = {m.lower() for m in re.findall(r'^\*\s*([A-Za-z]+)\s*$', bare, re.M)}
        except Exception as e:
            log.warning('wiki_rules: %s: %s', title, e)
            if kept:
                self.pages[('list', title)] = (self.clock(), kept[1])
                return kept[1]
            return None
        self.pages[('list', title)] = (self.clock(), items)
        return items

    def page(self, title):
        """(revision, blocks) for a page: None, [] if it has never been read and can't be."""
        kept = self.pages.get(title)
        if kept and self.clock() - kept[0] < self.ttl:
            return kept[1], kept[2]
        try:
            history = self.get(title)
            trusted = next(((revision, wikitext) for revision, editor, wikitext in history
                            if editor.lower().replace('_', ' ') in self.editors), None)
            if trusted is None:
                raise PermissionError(f'no revision by a trusted editor among the last {len(history)}')
            revision, wikitext = trusted
        except Exception as e:  # the wiki down or slow: what was read last stands, for another while
            log.warning('wiki_rules: %s: %s', title, e)
            if kept:
                self.pages[title] = (self.clock(), kept[1], kept[2])
                return kept[1], kept[2]
            self.pages[title] = (self.clock(), None, [])  # nothing to read yet: asked again after TTL, not every wake
            return None, []
        self.pages[title] = (self.clock(), revision, blocks(wikitext))
        return self.pages[title][1], self.pages[title][2]

    def dial(self, key, value, fallback=None):
        """The text for one setting of a dial (discretion=reserved) or one of the wake's lines (line=look-only): from
        the Wake page, then Dials (where the lines were first), else `fallback`, else the recovery copy."""
        for title in ((WAKE, DIALS) if key == 'line' else (DIALS,)):
            _, found = self.page(title)
            text = next((b['text'] for b in found if b['dial'] == key and b['value'] == value), None)
            if text is not None and (key != 'line' or MARKERS.get(value, '') in text):
                return text
            if text is not None:
                log.warning('wiki_rules: %s line=%s lacks "%s": not used', title, value, MARKERS[value])
        return recovered(key, value) if fallback is None else fallback

    def line(self, name, fallback, **fields):
        """One of the wake's fixed lines, as the Dials page has it ({{Sent to agents|dial=line|value=<name>|text=...}};
        Justin, 24 Oct: "where does that phrase live?"), else `fallback`; its {fields} filled in. A field the page
        names that the code has none of stays as written; a text that can't be filled is the fallback's."""
        return said(self.dial('line', name, fallback), fallback, fields)


class _Kept(dict):
    def __missing__(self, key):
        return '{' + key + '}'


def said(text, fallback, fields):
    """`text` with its {fields} filled; `fallback` filled, if text can't be."""
    for candidate in (text, fallback):
        try:
            return candidate.format_map(_Kept(fields))
        except (ValueError, IndexError, AttributeError):
            continue
    return fallback
