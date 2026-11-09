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

import re
import time
import uuid

SOURCE = 'work'           # the event line a work's page hangs on (mood_view.EVENT_SOURCES)
HOME_MOOD = 'ramp'        # where a piece of work with no Mood of its own is talked about
MAX_DIFF = 400_000        # characters of one file's diff shown; past that, a link to the whole file
MAX_FILE = 1_000_000      # characters of a file shown whole
MAX_COMMITS = 200
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

def find(repo, branch):
    """The ramp's item for this work (open, or merged lately), or None."""
    from . import ramp
    board = ramp.board()
    return next((i for i in board.get('open', []) if i.get('repo') == repo and i.get('branch') == branch), None)


def page_of(repo, branch):
    """The WorkTiding for this work, or None."""
    from conversations.models import WorkTiding
    return WorkTiding.objects.select_related('tiding').filter(repo=repo, branch=branch).first()


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


def open_page(by, repo, branch):
    """The Tiding for this work, made now if it has none: a 'work' line in its Mood, and the Tiding on it.
    `by` is the person (ThinkingEntity) opening it. Invalid if the ramp doesn't have this work open."""
    from django.db import IntegrityError, transaction
    from conversations.models import ConversationParticipant, Message, Mood, Tiding, WorkTiding
    from django.conf import settings
    have = page_of(repo, branch)
    if have is not None:
        return have.tiding
    item = find(repo, branch)
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
    url = item.get('url') or f'{site}/moods/pulls/#{repo}:{branch}'
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
        state, head = item.get('state', 'open'), item.get('head') or _sha(held, f'refs/heads/{branch}')
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
    base = ramp._base(refs, (item or {}).get('base', ''))
    out.update(head=head, base=base, description=(item or {}).get('description', ''))
    if not base:
        return out
    out['merge_base'] = _git(held, 'merge-base', f'refs/heads/{base}', head).strip()
    log = _git(held, 'log', f'--max-count={MAX_COMMITS}', '--format=%H%x00%an%x00%ct%x00%s%x00%b%x1e',
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
    from .githouse import _git
    out = _git(held, 'diff', '--numstat', '-z', '--no-renames', base, head)
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
    from .githouse import _git
    held = _repo(repo)
    base, head, path = _commit(held, base), _commit(held, head), _path(path)
    text = _git(held, 'diff', '--no-color', '--no-ext-diff', '--no-renames', '-U5', base, head, '--', path)
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
    return {'repo': repo, 'base': base, 'head': head, 'path': path, 'rows': rows, 'cut': cut}


def file_at(repo, commit, path):
    """A whole file at a commit: {'lines': [...], 'cut', 'binary'}. Invalid if it isn't there."""
    from .githouse import _git
    held = _repo(repo)
    commit, path = _commit(held, commit), _path(path)
    kind = _git(held, 'cat-file', '-t', f'{commit}:{path}').strip()
    if kind != 'blob':
        raise Invalid(f'{path} is not a file at {commit[:8]}', 404)
    text = _git(held, 'cat-file', 'blob', f'{commit}:{path}')
    binary = '\0' in text[:8000]
    cut = len(text) > MAX_FILE
    lines = [] if binary else text[:MAX_FILE].split('\n')
    if lines and lines[-1] == '':
        lines.pop()
    return {'repo': repo, 'commit': commit, 'path': path, 'lines': lines, 'cut': cut, 'binary': binary}
