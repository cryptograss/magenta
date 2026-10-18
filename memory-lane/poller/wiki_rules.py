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
        depth, j = 0, at
        while j < len(text):
            two = text[j:j + 2]
            if two == '{{':
                depth, j = depth + 1, j + 2
            elif two == '}}':
                depth, j = depth - 1, j + 2
                if not depth:
                    break
            else:
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


class WikiTexts:
    """Pages read from the wiki, each kept TTL seconds: {title: (fetched_at, revision, blocks)}."""

    def __init__(self, api=API, ttl=TTL, get=None, clock=time.time, editors=EDITORS):
        self.api, self.ttl, self.clock = api, ttl, clock
        self.editors = {e.strip().lower().replace('_', ' ') for e in editors if e.strip()}
        self.get = get or self._get
        self.pages = {}

    def _get(self, title):
        """The page's newest revisions, newest first: [(revision, editor, wikitext)]; [] if there's no page."""
        url = self.api + '?' + urllib.parse.urlencode({
            'action': 'query', 'prop': 'revisions', 'titles': title, 'rvprop': 'content|ids|user', 'rvslots': 'main',
            'rvlimit': HISTORY, 'format': 'json', 'formatversion': '2'})
        with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': USER_AGENT}), timeout=10) as r:
            page = json.load(r)['query']['pages'][0]
        return [(rev['revid'], rev.get('user', ''), rev['slots']['main']['content'])
                for rev in page.get('revisions') or [] if not page.get('missing')]

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
            return None, []
        self.pages[title] = (self.clock(), revision, blocks(wikitext))
        return self.pages[title][1], self.pages[title][2]

    def dial(self, key, value, fallback=''):
        """The text for one setting of a dial (discretion=reserved), else `fallback`."""
        _, found = self.page(DIALS)
        return next((b['text'] for b in found if b['dial'] == key and b['value'] == value), fallback)
