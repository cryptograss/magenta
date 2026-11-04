"""The guided tour: steps on PickiPedia, run in the visitor's own browser on the live page (Justin, 4 Nov,
#docs-tests-qa: "is it possible for the tour to run in the user's browser and use the live site, rather than a
video?"). The overlay is driver.js (MIT, vendored at conversations/vendor/driverjs: re's pick, 4 Nov).

Magenta:Tour holds one call a step, in the order the tour runs (re's contract, #docs-tests-qa, 4 Nov):

    {{Tour step|at=composer|text=Type here.|try=yes|needs=signin}}

at: a data-tour name in the page's markup. text: a sentence or two; [[links]] and '''bold''' come through.
try=yes: the step waits for a click on what it points at. needs: signin, key or voice; the step is skipped for a
visitor without it (signedout: only for one who isn't signed in). Everything else on the page is for people
reading the wiki. Only the newest revision by a trusted editor is read, as for every page that steers what
magenta does: a visitor's browser is told what to point at by the page, so the page is only as good as who wrote it.

Both ends are checked (re, 4 Nov): a step naming an anchor the page doesn't have is skipped and listed on the
status page; an anchor no step names is listed there too (health()).
"""

import html
import re
from pathlib import Path

from django.core.cache import cache

PAGE = 'Magenta:Tour'
KEY = 'tour-steps'
KEEP = 300     # seconds the page is kept
FAILED = 120   # and how soon a failed read is tried again
NEEDS = ('signin', 'key', 'voice', 'signedout')
TEMPLATES = Path(__file__).resolve().parent.parent / 'templates' / 'conversations'
# The anchors: data-tour="name" in markup, or el.dataset.tour = 'name' where the script makes the element.
_ANCHOR = re.compile(r'''data-tour="([a-z0-9-]+)"|dataset\.tour\s*=\s*'([a-z0-9-]+)\'''')


def _calls(wikitext):
    """The {{Tour step|...}} calls in the page, in order, each as its raw inner text."""
    found, i = [], 0
    opener = re.compile(r'\{\{\s*[Tt]our[ _]step\s*\|')
    while True:
        m = opener.search(wikitext, i)
        if not m:
            return found
        depth, j = 2, m.end()
        while j < len(wikitext) and depth:
            two = wikitext[j:j + 2]
            if two == '{{':
                depth, j = depth + 2, j + 2
            elif two == '}}':
                depth, j = depth - 2, j + 2
            else:
                j += 1
        found.append(wikitext[m.end():j - 2] if not depth else wikitext[m.end():])
        i = j


def _fields(inner):
    """{name: value} from a call's inner text, split on the pipes that aren't inside [[...]] or {{...}}."""
    parts, depth, start = [], 0, 0
    for k, c in enumerate(inner):
        pair = inner[k:k + 2]
        if pair in ('[[', '{{'):
            depth += 1
        elif pair in (']]', '}}') and depth:
            depth -= 1
        elif c == '|' and not depth:
            parts.append(inner[start:k])
            start = k + 1
    parts.append(inner[start:])
    fields = {}
    for part in parts:
        name, eq, value = part.partition('=')
        if eq:
            fields[name.strip().lower()] = value.strip()
    return fields


def _wiki_href(title):
    from urllib.parse import quote
    return 'https://pickipedia.xyz/wiki/' + quote(title.strip().replace(' ', '_'), safe='/:#')


def text_html(wikitext):
    """A step's words as safe HTML: escaped, then [[Page|words]] as links to PickiPedia, '''bold''', ''italic''."""
    out = html.escape(wikitext, quote=False)  # element text only: quotes are safe, and ''' stays readable
    out = re.sub(r'\{\{[^{}]*\}\}', '', out)  # a {{verified|...}} mark, or any template: not for the overlay

    def link(m):
        title, _, words = m[1].partition('|')
        return (f'<a href="{html.escape(_wiki_href(html.unescape(title)), quote=True)}" target="_blank" '
                f'rel="noopener">{words or title}</a>')
    out = re.sub(r'\[\[([^\[\]]+)\]\]', link, out)
    out = re.sub(r"'''(.+?)'''", r'<b>\1</b>', out)
    out = re.sub(r"''(.+?)''", r'<i>\1</i>', out)
    return out.strip()


def steps_of(wikitext):
    """[{'at', 'text', 'try', 'needs'}] from the page, in order; a call without at= or text= is left out."""
    steps = []
    for inner in _calls(wikitext or ''):
        f = _fields(inner)
        at, words = f.get('at', '').strip().lower(), f.get('text', '')
        if not re.fullmatch(r'[a-z0-9-]+', at) or not words:
            continue
        needs = f.get('needs', '').strip().lower()
        steps.append({'at': at, 'text': text_html(words), 'try': f.get('try', '').strip().lower() in ('yes', '1', 'true'),
                      'needs': needs if needs in NEEDS else ''})
    return steps


def anchors():
    """Every data-tour name the page's markup and script give an element."""
    found = set()
    for name in ('moods.html', 'install_prompt.html'):
        path = TEMPLATES / name
        if path.exists():
            for a, b in _ANCHOR.findall(path.read_text()):
                found.add(a or b)
    return found


def _read(get=None):
    try:
        from poller.wiki_rules import EDITORS, WikiTexts
    except ImportError:
        from wiki_rules import EDITORS, WikiTexts
    trusted = {e.lower().replace('_', ' ') for e in EDITORS}
    history = (get or WikiTexts().get)(PAGE)
    revision, text = next(((r, w) for r, editor, w in history
                           if (editor or '').lower().replace('_', ' ') in trusted), (None, ''))
    return {'revision': revision, 'steps': steps_of(text), 'error': '' if revision else 'no revision by a trusted editor'}


def state(get=None):
    """{'revision', 'steps', 'error'}: the tour as its newest trusted revision says; kept a while, and the last
    good read stands if the wiki can't be reached. Steps naming an anchor the page lacks are left out."""
    kept = cache.get(KEY)
    if kept is not None and get is None:
        return kept
    try:
        got = _read(get)
        cache.set(KEY + ':last', got, None)
        cache.set(KEY, got, KEEP)
    except Exception as e:
        got = dict(cache.get(KEY + ':last') or {'revision': None, 'steps': []}, error=f'PickiPedia could not be read ({e})')
        cache.set(KEY, got, FAILED)
    return got


def runnable(get=None):
    """state(), with only the steps whose anchor the page has."""
    got, have = state(get), anchors()
    return dict(got, steps=[s for s in got['steps'] if s['at'] in have])


def health(get=None):
    """For the status page: what the tour and the page disagree on, both ways."""
    got, have = state(get), anchors()
    named = {s['at'] for s in got['steps']}
    return {'revision': got['revision'], 'error': got.get('error', ''), 'steps': len(got['steps']),
            'missing': sorted(named - have), 'unused': sorted(have - named),
            'page': _wiki_href(PAGE)}
