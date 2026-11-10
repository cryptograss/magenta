"""A page for each piece of work (Cryptograss:Moods/ramp/Plan, section 9; Justin, 9 Nov: "use the tidings
interface"). A piece of work is <repo>:<branch>, with or without a pull request. Its page is a Tiding, so what's
said there works as on any Tiding: comments, replies to its line in the Mood, @asks that wake an emergent. Above
the comments the page shows the work itself, read from githouse's copy: its description, its commits, the files
it changes, each file's diff on demand, and the whole file at its head, every line with its own address.

The Tiding is made the first time someone opens the page from the ramp. It hangs on a 'work' line in the work's
Mood: the first Mood its card names, else #ramp. The page is kept off Tidings' list of links, since the ramp
already lists the work.

Everything here reads githouse's copy, which memory-lane mounts read-only. Nothing is run that a pusher wrote:
only git's plumbing, on objects.
"""

import html
import re
import time
import uuid

SOURCE = 'work'           # the event line a work's page hangs on (mood_view.EVENT_SOURCES)
HOME_MOOD = 'ramp'        # where a piece of work with no Mood of its own is talked about
MAX_DIFF = 400_000        # characters of one file's diff shown; past that, a link to the whole file
MAX_FILE = 1_000_000      # characters of a file shown whole
MAX_COMMITS = 200
MAX_HIGHLIGHT = 300_000   # characters of a file given syntax colours; a longer one is shown plain
_SHA = re.compile(r'[0-9a-f]{40}')


class Invalid(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _repo(name):
    from .githouse import repository
    held = repository(name)
    if held is None:
        raise Invalid(f'githouse holds no repository {name}', 404)
    return held


def _commit(held, sha):
    from .githouse import _sha
    if not isinstance(sha, str) or not _SHA.fullmatch(sha) or _sha(held, sha) != sha:
        raise Invalid('which commit? (a full hash githouse has)', 404)
    return sha


def _path(path):
    """A path in the repository, as git names one: no leading dash or slash, no '..', nothing odd."""
    if (not isinstance(path, str) or not path or len(path) > 500 or path.startswith(('-', '/'))
            or '..' in path.split('/') or any(c in path for c in '\0\n\r')):
        raise Invalid('which file?')
    return path


# --- which work, and what the ramp knows of it -----------------------------------------------------------

def find(repo, branch, fresh_if_missing=False):
    """The ramp's item for this work while it's open, or None. From the last board, however old, so a work's page
    never waits on a rebuild (Justin, 9 Nov: "± See the full diff" sat ~8 s on Tidings, the board rebuilding).
    fresh_if_missing: not on an old board, ask a fresh one (a branch pushed since it was built)."""
    import time
    from . import ramp
    board = ramp.board(stale_ok=True)
    item = next((i for i in board.get('open', []) if i.get('repo') == repo and i.get('branch') == branch), None)
    if item is None and fresh_if_missing and time.time() - board.get('built', 0) >= ramp.BOARD_FOR:
        board = ramp.board()
        item = next((i for i in board.get('open', []) if i.get('repo') == repo and i.get('branch') == branch), None)
    return item


def address(repo, branch):
    """A candidate branch's page, in the ramp (Justin, 9 Nov: "why does it take me to tidings?"):
    /ramp/<repo>/<branch>/, the branch's slashes kept."""
    from urllib.parse import quote
    return f'/ramp/{quote(repo, safe="")}/{quote(branch, safe="/")}/'


def page_of(repo, branch):
    """The WorkTiding for this work, or None."""
    from conversations.models import WorkTiding
    return WorkTiding.objects.select_related('tiding').filter(repo=repo, branch=branch).first()


# A candidate named in what's said, as repo:branch or repo#N: a link to its page (Justin, 10 Nov). Only a work
# whose page opens: one open on the ramp, or one that has a page already (a merged one, say). Anything else
# stays as written, so a link never leads to "not open on the ramp".
LINKS_KEY = 'work:links'
LINKS_FOR = 30   # seconds what's linkable is kept: messages are drawn on every poll


def linkable():
    """{'branches': {(repo, branch)}, 'numbers': {(repo, number): branch}}: the work a name in a message may link.
    From the last board, however old, never building one (that takes seconds), and the pages already opened."""
    from django.core.cache import cache
    found = cache.get(LINKS_KEY)
    if found is not None:
        return found
    from . import ramp
    from conversations.models import WorkTiding
    branches, numbers = set(), {}
    for item in (cache.get(ramp.KEY) or {}).get('open', []):
        repo, branch = item.get('repo'), item.get('branch')
        if repo and branch:
            branches.add((repo, branch))
            if item.get('number'):
                numbers[(repo, int(item['number']))] = branch
    for repo, branch, number in WorkTiding.objects.values_list('repo', 'branch', 'number'):
        branches.add((repo, branch))
        if number:
            numbers.setdefault((repo, int(number)), branch)
    found = {'branches': branches, 'numbers': numbers}
    cache.set(LINKS_KEY, found, LINKS_FOR)
    return found


def link_for(repo, branch=None, number=None):
    """The page's address for repo:branch or repo#number, if its page opens; else None."""
    known = linkable()
    if number is not None:
        branch = known['numbers'].get((repo, int(number)))
    if branch and (repo, branch) in known['branches']:
        return address(repo, branch)
    return None


def pages_for(items):
    """{(repo, branch): {'id', 'comments'}} for the items that have a page: for the ramp's 💬."""
    from conversations.models import WorkTiding
    keys = {(i.get('repo'), i.get('branch')) for i in items if i.get('branch')}
    if not keys:
        return {}
    found = {}
    for w in WorkTiding.objects.filter(repo__in={r for r, _ in keys}, branch__in={b for _, b in keys}):
        if (w.repo, w.branch) in keys:
            found[(w.repo, w.branch)] = {'id': str(w.tiding_id), 'comments': _count(w.tiding_id)}
    return found


def _count(tiding_id):
    from conversations.models import TidingComment
    return TidingComment.objects.filter(tiding_id=tiding_id).count()


def with_pages(items):
    """The items, each with 'page': {'id', 'comments'} if it has one, else None."""
    found = pages_for(items)
    for item in items:
        item['page'] = found.get((item.get('repo'), item.get('branch')))
    return items


def with_merged_pages(items):
    """The ramp's merged items, each with 'page': {'id'} if its work had one: by its pull request's number, or
    by its branch for one githouse merged without a pull request (ramp.githouse_merged)."""
    from django.db.models import Q
    from conversations.models import WorkTiding
    numbers = {(i.get('repo'), i.get('number')) for i in items if i.get('number')}
    branches = {(i.get('repo'), i.get('branch')) for i in items if i.get('branch')}
    found = {}
    if numbers or branches:
        q = Q(pk__in=[])
        if numbers:
            q |= Q(repo__in={r for r, _ in numbers}, number__in={n for _, n in numbers})
        if branches:
            q |= Q(repo__in={r for r, _ in branches}, branch__in={b for _, b in branches})
        for w in WorkTiding.objects.filter(q):
            if w.number:
                found[(w.repo, 'n', w.number)] = {'id': str(w.tiding_id)}
            found[(w.repo, 'b', w.branch)] = {'id': str(w.tiding_id)}
    for item in items:
        item['page'] = (found.get((item.get('repo'), 'n', item.get('number'))) if item.get('number') else None) \
            or found.get((item.get('repo'), 'b', item.get('branch')))
    return items


def open_page(by, repo, branch):
    """The Tiding for this work, made now if it has none: a 'work' line in its Mood, and the Tiding on it.
    `by` is the person (ThinkingEntity) opening it. Invalid if the ramp doesn't have this work open."""
    from django.db import IntegrityError, transaction
    from conversations.models import ConversationParticipant, Message, Mood, Tiding, WorkTiding
    from django.conf import settings
    have = page_of(repo, branch)
    if have is not None:
        return have.tiding
    item = find(repo, branch, fresh_if_missing=True)
    if item is None:
        raise Invalid(f'{repo}:{branch} is not open on the ramp', 404)
    mood = None
    for slug in list(item.get('moods') or []) + [HOME_MOOD]:
        mood = Mood.objects.filter(slug=slug).first()
        if mood is not None:
            break
    if mood is None:
        raise Invalid(f'no Mood to talk about {repo}:{branch} in', 404)
    ref = f"{repo}#{item['number']}" if item.get('number') else f'{repo}:{branch}'
    site = getattr(settings, 'MAGENTA_URL', 'https://magenta.cryptograss.live').rstrip('/')
    url = item.get('url') or f'{site}/ramp/#{repo}:{branch}'
    system, _ = ConversationParticipant.objects.get_or_create(name='system', defaults={'participant_type': 'system'})
    try:
        with transaction.atomic():
            line = Message.objects.create(
                id=uuid.uuid4(), sender=system, mood=mood, source_file=SOURCE, timestamp=int(time.time() * 1000),
                content={'type': 'work', 'repo': repo, 'branch': branch, 'number': item.get('number'),
                         'ref': ref, 'title': item.get('title', ''), 'url': url, 'by': by.name})
            tiding = Tiding.objects.create(message=line, url=url, title=f"{ref} · {item.get('title', '')}".strip(' ·'),
                                           tags=['work'], by=by)
            WorkTiding.objects.create(tiding=tiding, repo=repo, branch=branch, number=item.get('number'))
    except IntegrityError:  # opened by someone else at the same moment: theirs
        return page_of(repo, branch).tiding
    line.content = {**line.content, 'tiding': str(tiding.id)}
    Message.objects.filter(id=line.id).update(content=line.content)
    from django.core.cache import cache
    cache.delete(LINKS_KEY)  # its name links from now on, not in half a minute
    return tiding


# --- the work itself, from githouse's copy ---------------------------------------------------------------

def panel(work):
    """What the page shows above its comments: {'repo', 'branch', 'number', 'ref', 'item', 'state', 'head', 'base',
    'description', 'commits', 'files', 'size'}. 'state' is open, merged, closed or gone."""
    from .githouse import _git, _sha, repository
    from . import ramp
    repo, branch = work.repo, work.branch
    item = find(repo, branch)
    held = repository(repo)
    out = {'repo': repo, 'branch': branch, 'number': work.number, 'item': item, 'head': None, 'base': '',
           'description': '', 'commits': [], 'files': [], 'size': {},
           'ref': f'{repo}#{work.number}' if work.number else f'{repo}:{branch}'}
    if held is None:
        out['state'] = 'gone'
        return out
    if item is not None:
        # The head from githouse's copy, now: the board may be a minute or more old.
        state, head = item.get('state', 'open'), _sha(held, f'refs/heads/{branch}') or item.get('head')
    else:  # off the ramp: merged or closed since, its name retired (Plan, section 1)
        state, head = 'gone', None
        for kind in ('merged', 'closed'):
            head = _sha(held, f'refs/{kind}/{branch}')
            if head:
                state = kind
                break
        if head is None and work.number:
            head = _sha(held, f'refs/pull/{work.number}/head')
            if head:
                state = 'merged' if ramp_merged(repo, head) else 'closed'
    out['state'] = state
    if head is None:
        return out
    refs = ramp._refs(held)
    base = ramp._base(refs, (item or {}).get('base', ''), repo)
    out.update(head=head, base=base, description=(item or {}).get('description', ''))
    if not base:
        return out
    out['merge_base'] = _git(held, 'merge-base', f'refs/heads/{base}', head).strip()
    log = _read(held, 'log', f'--max-count={MAX_COMMITS}', '--format=%H%x00%an%x00%ct%x00%s%x00%b%x1e',
               f'refs/heads/{base}..{head}')
    for entry in log.split('\x1e'):
        parts = entry.strip('\n').split('\0')
        if len(parts) == 5 and _SHA.fullmatch(parts[0]):
            out['commits'].append({'sha': parts[0], 'author': parts[1], 'at': ramp._when(int(parts[2] or 0)),
                                   'subject': parts[3], 'body': parts[4].strip()})
    if out.get('merge_base'):
        out['files'] = changed_files(held, out['merge_base'], head)
        out['size'] = {'commits': len(out['commits']), 'files': len(out['files']),
                       'added': sum(f['added'] or 0 for f in out['files']),
                       'removed': sum(f['removed'] or 0 for f in out['files'])}
    return out


def ramp_merged(repo, head):
    from .githouse import commit_merged
    return bool(commit_merged(repo, head))


def changed_files(held, base, head):
    """[{'path', 'added', 'removed', 'binary'}] of what head changes since base (renames as the new path)."""
    out = _read(held, 'diff', '--numstat', '-z', '--no-renames', base, head)
    files = []
    for entry in out.split('\0'):
        parts = entry.split('\t', 2)
        if len(parts) == 3 and parts[2]:
            binary = parts[0] == '-'
            files.append({'path': parts[2], 'binary': binary,
                          'added': None if binary else int(parts[0]), 'removed': None if binary else int(parts[1])})
    return files


# --- one file's diff, and a whole file: every line an address --------------------------------------------

_HUNK = re.compile(r'^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)$')


def diff_of(repo, base, head, path):
    """One file's changes, as rows: [{'kind': hunk|add|del|ctx, 'old', 'new', 'text'}]; and 'cut' if too long."""
    held = _repo(repo)
    base, head, path = _commit(held, base), _commit(held, head), _path(path)
    text = _read(held, 'diff', '--no-color', '--no-ext-diff', '--no-renames', '-U5', base, head, '--', path)
    cut = len(text) > MAX_DIFF
    rows, old, new = [], 0, 0
    for line in text[:MAX_DIFF].split('\n'):
        hunk = _HUNK.match(line)
        if hunk:
            old, new = int(hunk[1]), int(hunk[2])
            rows.append({'kind': 'hunk', 'old': None, 'new': None, 'text': line})
        elif not rows:
            continue  # diff --git, index, ---/+++ lines: the file's own header says it
        elif line.startswith('+'):
            rows.append({'kind': 'add', 'old': None, 'new': new, 'text': line[1:]})
            new += 1
        elif line.startswith('-'):
            rows.append({'kind': 'del', 'old': old, 'new': None, 'text': line[1:]})
            old += 1
        elif line.startswith(' '):
            rows.append({'kind': 'ctx', 'old': old, 'new': new, 'text': line[1:]})
            old += 1
            new += 1
        elif line.startswith('\\'):
            rows.append({'kind': 'note', 'old': None, 'new': None, 'text': line})
    # Each side's whole file coloured once, and each line given its own: a line inside a long string or comment
    # is coloured as what it's in, which a diff's lines alone can't tell.
    sides = {'new': highlighted(path, _blob(held, head, path)), 'old': highlighted(path, _blob(held, base, path))}
    for row in rows:
        side = 'old' if row['kind'] == 'del' else 'new'
        number, lines = row.get(side), sides[side]
        coloured = lines[number - 1] if row['kind'] in ('add', 'del', 'ctx') and lines and 0 < number <= len(lines) else None
        row['html'] = coloured if coloured is not None else _plain(row['text'])
    return {'repo': repo, 'base': base, 'head': head, 'path': path, 'rows': rows, 'cut': cut}


def _read(held, *args):
    """git's stdout as text, never raising on what a file holds: bytes read raw, undecodable ones replaced (a
    binary or a Latin-1 file is git's to hold, not ours to fail on; re's review of #448: a PNG's "whole file" was
    a 500). '' if git failed."""
    import subprocess
    try:
        done = subprocess.run(['git', '-c', 'safe.directory=*', '--git-dir', str(held), *args],
                              capture_output=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return ''
    return done.stdout.decode('utf-8', errors='replace') if done.returncode == 0 else ''


# --- syntax colours (Pygments): each line its own HTML, so every line keeps its address --------------------

def _lines(text):
    lines = text.split('\n')
    if lines and lines[-1] == '':
        lines.pop()
    return lines


def _plain(line):
    return html.escape(line, quote=False)


def _blob(held, commit, path):
    """The file at a commit, '' if it isn't there (added or removed by the work) or is too long to colour."""
    from .githouse import _git
    if _git(held, 'cat-file', '-t', f'{commit}:{path}').strip() != 'blob':
        return ''
    size = _git(held, 'cat-file', '-s', f'{commit}:{path}').strip()
    if not size.isdigit() or int(size) > MAX_HIGHLIGHT:
        return ''
    return _read(held, 'cat-file', 'blob', f'{commit}:{path}')


def _token_class(ttype):
    from pygments.token import STANDARD_TYPES
    while ttype not in STANDARD_TYPES:
        ttype = ttype.parent
    return STANDARD_TYPES[ttype]


def highlighted(path, text):
    """The file's lines as HTML, each token in a <span class> of Pygments' short names; None if there's nothing
    to colour by (no lexer for its name, binary, too long), or the colours wouldn't line up with git's lines."""
    if not text or len(text) > MAX_HIGHLIGHT or '\0' in text[:8000]:
        return None
    from pygments.lexers import get_lexer_for_filename
    from pygments.util import ClassNotFound
    try:
        lexer = get_lexer_for_filename(path.rsplit('/', 1)[-1], stripnl=False, ensurenl=False)
    except ClassNotFound:
        return None
    lines = ['']
    try:
        for ttype, value in lexer.get_tokens(text.replace('\r\n', '\n')):
            name = _token_class(ttype)
            for n, piece in enumerate(value.split('\n')):
                if n:
                    lines.append('')
                if piece:
                    escaped = html.escape(piece, quote=False)
                    lines[-1] += f'<span class="{name}">{escaped}</span>' if name else escaped
    except Exception:  # noqa: BLE001 -- a lexer that chokes on a file: shown plain, not an error
        return None
    if lines and lines[-1] == '':
        lines.pop()
    return lines if len(lines) == len(_lines(text.replace('\r\n', '\n'))) else None


_CSS = None


def highlight_css():
    """Pygments' colours for table.code: a light style, and a dark one when the reader's system is dark."""
    global _CSS
    if _CSS is None:
        from pygments.formatters import HtmlFormatter
        rules = lambda style: '\n'.join(line for line in HtmlFormatter(style=style).get_style_defs('table.code td.t')
                                        .splitlines() if line.startswith('table.code td.t .')
                                        # no red box round what a lexer didn't expect (a template's {% %} in HTML)
                                        and not line.startswith(('table.code td.t .err', 'table.code td.t .hll')))
        # Dark: the light style's colours undone first, so none it sets and the dark one doesn't is left on.
        _CSS = (f'{rules("default")}\n@media (prefers-color-scheme: dark) {{\n'
                f'table.code td.t span {{ color: inherit; font-weight: inherit; font-style: inherit; background: none; }}\n'
                f'{rules("github-dark")}\n}}')
    return _CSS


def file_at(repo, commit, path):
    """A whole file at a commit: {'lines': [...], 'cut', 'binary'}. Invalid if it isn't there."""
    from .githouse import _git
    held = _repo(repo)
    commit, path = _commit(held, commit), _path(path)
    kind = _git(held, 'cat-file', '-t', f'{commit}:{path}').strip()
    if kind != 'blob':
        raise Invalid(f'{path} is not a file at {commit[:8]}', 404)
    text = _read(held, 'cat-file', 'blob', f'{commit}:{path}')
    binary = '\0' in text[:8000]
    cut = len(text) > MAX_FILE
    lines = [] if binary else _lines(text[:MAX_FILE])
    shown = (None if binary or cut else highlighted(path, text)) or [_plain(line) for line in lines]
    return {'repo': repo, 'commit': commit, 'path': path, 'lines': shown, 'cut': cut, 'binary': binary}
