"""What the agents are told, kept on PickiPedia where people can read, edit and link it.

Each text is a {{Sent to agents}} on a wiki page -- the rest of the page is free for
explaining it -- and the runner sends what's inside, word for word:

    {{Sent to agents|dial=discretion|value=reserved|text=Speak only if ...}}   (DIALS)
    {{Sent to agents|text=...}}                                                (an agent's own rules)

Only a revision by a trusted editor counts (EDITORS, people who can vouch for what the
agents are told): anyone with a wiki account could otherwise write an agent's
instructions. A page last edited by anyone else -- an agent's edit still marked
proposed, say -- is read as if unreadable until one of them edits or verifies it.

Read with plain HTTP, never by the agent: a page is fetched at most every TTL
seconds and kept by its revision, so an agent never makes a round trip to remind
itself of its rules. If the wiki can't be reached, the last copy read stands; if
none was ever read, the caller's own text (the one in the code) does.
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
EDITORS = ('JMyles', 'SkymanJenkins')  # PickiPedia names whose revisions the agents are told
USER_AGENT = 'magenta-runner/1 (https://github.com/cryptograss/memory-lane; poller/wiki_rules.py)'


def agent_page(agent):
    """An agent's standing rules, wherever it is."""
    return f'Cryptograss:Agents/{agent[:1].upper()}{agent[1:]}/rules'


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
        url = self.api + '?' + urllib.parse.urlencode({
            'action': 'query', 'prop': 'revisions', 'titles': title, 'rvprop': 'content|ids|user', 'rvslots': 'main',
            'format': 'json', 'formatversion': '2'})
        with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': USER_AGENT}), timeout=10) as r:
            page = json.load(r)['query']['pages'][0]
        if page.get('missing'):
            return None, '', ''
        rev = page['revisions'][0]
        return rev['revid'], rev['slots']['main']['content'], rev.get('user', '')

    def page(self, title):
        """(revision, blocks) for a page: None, [] if it has never been read and can't be."""
        kept = self.pages.get(title)
        if kept and self.clock() - kept[0] < self.ttl:
            return kept[1], kept[2]
        try:
            revision, wikitext, editor = self.get(title)
            if revision is not None and editor.lower().replace('_', ' ') not in self.editors:
                raise PermissionError(f'revision {revision} is by {editor or "nobody known"}, not a trusted editor')
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

    def agent_rules(self, agent):
        """(revision, text) of an agent's standing rules: (None, '') if it has none."""
        revision, found = self.page(agent_page(agent))
        text = '\n\n'.join(b['text'] for b in found if not b['dial'])
        return (revision, text) if text else (None, '')
