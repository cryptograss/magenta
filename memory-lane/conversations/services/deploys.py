"""The deploy queue: one box, at most, for each server, and what each is waiting for.

Justin, #ramp, 25 Nov 2026: "I still show 7 deploy tasks, and they don't seem aware of each other. When will I
see a new interface, with deploys separated, and at-most one for each box? Show which PRs are already merged and
awaiting deploy?"

Eight open deploy items that evening asked for six pickipedia redeploys, two hunter ones and a maybelle one,
each written in its own Mood on its own day, none of them aware that one deploy of a box carries everything
merged into everything that box runs. A box is the unit, not the item.

What each box is waiting for is read from githouse and from the deploys the boxes themselves reported, not from
anybody's list:

* **owed** -- a merge that has landed on a deploy branch the box runs, and isn't in what the box last deployed.
  Where a box's deploy says which commit of a repository it ran (`ramp.running`: maybelle-config for every box,
  magenta for maybelle and hunter), that commit answers it. Where it doesn't (pickipedia and rabbithole, which
  the pickipedia box builds from a clone), the merge's own time against the box's last finished deploy does.
* maybelle-config is every box's deploy logic, so a merge there is read **by what it touched**: `hunter/` is
  hunter's, `maybelle/` maybelle's, `pickipedia-vps/` the pickipedia box's, `delivery-kid/` delivery-kid's, and
  `shared/` every box's. A merge that touches none of a box's paths (a script, this repository's own CI) owes
  that box nothing, which is why two merges can sit in front of a box that is current.
* **pinned** -- pickipedia-mcp reaches hunter at a commit written into `hunter/Dockerfile`
  (`PICKIPEDIA_MCP_COMMIT`), so merging it onto master changes nothing on hunter until that line moves. The box
  says so rather than claiming a deploy would pick it up.
* **coming** -- open candidate branches, not merged yet, in the repositories the box runs: what will owe it a
  deploy once they land. In maybelle-config a branch is a box's by what *its own diff* touches, read before it
  merges. This is the half a hand-written deploy item used to carry ("when these two land, these three boxes will
  want it"), derived instead, so nothing depends on anybody remembering (Justin, #ramp, 27 Nov).
* **asks** -- the open deploy items of every Mood (services/todo.py) that name the box, folded into it, with the
  branches each is still waiting on and whether githouse has merged them. An item naming three boxes shows in
  all three; what it waits for is the same work. *No new deploy item is written*: with **owed** and **coming**
  both derived, a written one can only go stale, which is what Justin was reading on 27 Nov -- seven items, three
  of them already done and unable to say so. The ones still open tick themselves as they always did
  (todo_types.deployed); when the last is gone, nothing here has an **asks** section.

Nothing here deploys anything, and nothing ticks a list: todo_types.deployed still closes an item when its box
runs what it named. This only puts the four boxes, and what each is owed, in one place.
"""

import logging
import re

from django.core.cache import cache

log = logging.getLogger(__name__)

KEY = 'deploys:queue'
KEEP = 60               # seconds the queue is kept: githouse fetches every minute
MERGES_SHOWN = 12       # merges listed in front of one box, newest first
SINCE_MOST = 30 * 86400  # how far back a box with no running commit is read: a month

# Which repositories each server runs. maybelle-config is every box's deploy logic (PATHS says whose a merge is).
RUNS = {
    'maybelle-config': ('hunter', 'maybelle', 'pickipedia', 'delivery-kid'),
    'magenta': ('maybelle', 'hunter'),
    'pickipedia': ('pickipedia',),
    'rabbithole': ('pickipedia',),
}
# A maybelle-config merge is a box's when it touched one of these. shared/ is everyone's.
PATHS = {
    'hunter': ('hunter/',),
    'maybelle': ('maybelle/',),
    'pickipedia': ('pickipedia-vps/',),
    'delivery-kid': ('delivery-kid/',),
}
SHARED = ('shared/',)
BY_PATH = ('maybelle-config',)

# A repository a box runs at a commit written into another repository: merging it is not enough.
PINNED = {
    'pickipedia-mcp': {'server': 'hunter', 'pin_in': 'maybelle-config', 'file': 'hunter/Dockerfile',
                       'arg': 'PICKIPEDIA_MCP_COMMIT',
                       'how': "hunter builds it into its image at this commit, so a merge reaches hunter only "
                              "once the line moves in maybelle-config -- a pull request, then a hunter deploy"},
}

_GITHOUSE_MERGE = re.compile(r'^Merge ([\w.-]+):(\S+?) \(([0-9a-f]{7,40})\)$')
_PULL_MERGE = re.compile(r'^Merge pull request #(\d+) from (\S+)')


def _pin_in(text, arg):
    """The commit a Dockerfile's `ARG <arg>=<sha>` names, or '' if it says none."""
    found = re.search(rf'^ARG\s+{re.escape(arg)}\s*=\s*([0-9a-f]{{7,40}})\s*$', text or '', re.M)
    return found.group(1) if found else ''


def _deploy_branch(repo):
    from .merge_orders import DEPLOYS_FROM
    return DEPLOYS_FROM.get(repo) or 'main'


def servers_named(text):
    """The boxes a line of writing names (tools_ask.named_servers, aliases and all). Anything that looks like a
    candidate branch, a pull request or a URL is dropped first, so "pickipedia:magent/invite-night" doesn't name
    the pickipedia box by accident -- the same reading todo_types.deployed does."""
    from .todo_types import _NAMED_THINGS
    from .tools_ask import named_servers
    return named_servers(_NAMED_THINGS.sub(' ', text or ''))


# --- what githouse says has merged -------------------------------------------------------------------

def _author(held, commit):
    """Who wrote the work a merge brought in, as this place calls them ('magent-cryptograss' is magent)."""
    from .glance import person_key
    from .work_pages import _read
    name = _read(held, 'log', '-1', '--format=%an', commit).strip()
    return person_key(name) or name


def _touched(held, commit, parent):
    """The top-level paths a merge changed, as prefixes ('hunter/'), or None if git couldn't say."""
    from .work_pages import _read
    out = _read(held, 'diff', '--name-only', parent, commit)
    if not out.strip():
        return set()
    return {line.split('/', 1)[0] + '/' for line in out.splitlines() if line.strip()}


def _merge(held, line):
    """One line of `git log --first-parent` read as a merge: {'commit', 'at', 'subject', 'parents'} and, where
    its message says so, the work it brought in ('repo', 'branch', 'head' or 'pull')."""
    commit, at, subject, parents = (line.split('\x1f') + ['', '', '', ''])[:4]
    if not commit:
        return None
    found = {'commit': commit, 'at': int(at) if at.isdigit() else None, 'subject': subject,
             'parents': parents.split(), 'branch': '', 'pull': None, 'who': ''}
    branch = _GITHOUSE_MERGE.match(subject)
    if branch:
        found.update(repo=branch.group(1), branch=branch.group(2), head=branch.group(3))
    else:
        pull = _PULL_MERGE.match(subject)
        if pull:
            found.update(pull=int(pull.group(1)))
    if len(found['parents']) > 1:
        found['who'] = _author(held, found['parents'][1])
    return found


def merges(repo, since_commit='', since_ts=None, limit=MERGES_SHOWN):
    """The merges on this repository's deploy branch that are not in since_commit (or, with none, that landed
    after since_ts), newest first. [] if githouse holds no such repository, or can't say."""
    from . import githouse
    from .work_pages import _read
    held = githouse.repository(repo)
    if held is None:
        return []
    branch = _deploy_branch(repo)
    tip = githouse._sha(held, f'refs/heads/{branch}')
    if not tip:
        return []
    form = '--format=%H\x1f%ct\x1f%s\x1f%P'
    if since_commit and githouse._sha(held, since_commit):
        out = _read(held, 'log', '--first-parent', form, f'-{limit + 1}', f'{since_commit}..{tip}')
        after = None
    elif since_ts:
        out = _read(held, 'log', '--first-parent', form, f'-{limit + 1}', tip)
        after = int(since_ts)   # filtered here, not with git's --since: a date git can't parse filters nothing
    else:
        return []
    found = []
    for line in out.splitlines():
        one = _merge(held, line)
        if one is None or (after is not None and (one['at'] or 0) <= after):
            continue
        found.append(one)
    return found


def _mine(server, repo, merge, held):
    """Whether this merge is work the box runs. Every merge is, except in maybelle-config, where a merge belongs
    to the boxes whose paths it touched (a merge git can't be read for counts as everyone's: better a deploy
    nobody needed than a box quietly left behind)."""
    if repo not in BY_PATH:
        return True
    parents = merge.get('parents') or []
    if not parents:
        return True
    touched = _touched(held, merge['commit'], parents[0])
    if touched is None:
        return True
    return bool(touched & set(PATHS.get(server, ()) + SHARED))


def owed(server, runs=None, finished=None):
    """[{'repo', 'branch', 'commit', 'at', 'who', 'subject', 'link', 'by_time'}]: every merge waiting for this
    box's next deploy, newest first. by_time marks one read from the deploy's clock rather than its commit."""
    from . import githouse, ramp, servers as srv
    from .todo_types import _started
    runs = ramp.running() if runs is None else runs
    last = (srv.last_finished(server) or {}) if finished is None else (finished or {})
    out = []
    for repo, boxes in RUNS.items():
        if server not in boxes:
            continue
        held = githouse.repository(repo)
        if held is None:
            continue
        running = (runs.get(repo) or {}).get(server)
        by_time = not running
        # A deploy that began before a merge landed may finish after it (the pickipedia box clones mid-playbook),
        # so a merge is owed until a deploy *started* after it -- not one that merely finished after (re, 26 Nov).
        since = _started(last) if by_time else None
        found = merges(repo, since_commit=running or '',
                       since_ts=max(since, _month_ago()) if by_time else None)
        for merge in found:
            if not _mine(server, repo, merge, held):
                continue
            out.append({'repo': repo, 'branch': merge.get('branch') or '', 'pull': merge.get('pull'),
                        'commit': merge['commit'], 'at': merge['at'], 'who': merge.get('who') or '',
                        'subject': merge['subject'], 'by_time': by_time,
                        'link': _link(repo, merge)})
    out.sort(key=lambda m: m['at'] or 0, reverse=True)
    return out[:MERGES_SHOWN]


def _month_ago():
    import time
    return time.time() - SINCE_MOST


def _link(repo, merge):
    from .work_pages import address
    if merge.get('branch'):
        return address(repo, merge['branch'])
    if merge.get('pull'):
        return f'https://github.com/cryptograss/{repo}/pull/{merge["pull"]}'
    return ''


# --- coming: open work that will owe this box a deploy ------------------------------------------------

def _branch_touches(held, sha, tip, server):
    """Whether this unmerged branch, once merged, would owe the box anything: everywhere but maybelle-config,
    yes; there, only if the branch's own diff touches that box's paths or shared/. Read from the branch before
    it merges, which is the whole point -- the queue can say what is coming without anybody writing it down."""
    base = _read_sha(held, 'merge-base', sha, tip)
    if not base:
        return True
    touched = _touched(held, sha, base)
    if touched is None:
        return True
    return bool(touched & set(PATHS.get(server, ()) + SHARED))


def _read_sha(held, *args):
    """The first line of git's answer here, stripped, or '' if it said nothing."""
    from . import githouse
    lines = githouse._git(held, *args).strip().splitlines()
    return lines[0].strip() if lines else ''


def _candidates(held, repo):
    """[{'branch', 'head', 'at', 'who', 'subject'}] for every candidate branch githouse holds open here: the
    branches under refs/heads/ that aren't GitHub's own (githouse.OWNED), newest first. A branch whose merge has
    landed is in refs/merged/ instead, and a branch its owner dropped in refs/closed/, so neither shows up."""
    from . import githouse
    from .glance import person_key
    out = githouse._git(held, 'for-each-ref', '--sort=-committerdate',
                        '--format=%(refname:short)%00%(objectname)%00%(committerdate:unix)%00%(authorname)%00%(subject)',
                        'refs/heads/')
    owned = set(githouse.OWNED)
    found = []
    for line in out.splitlines():
        parts = line.split('\x00')
        if len(parts) != 5:
            continue
        branch, sha, at, who, subject = parts
        if branch in owned or branch.startswith('release/') or branch == _deploy_branch(repo):
            continue
        found.append({'branch': branch, 'head': sha, 'at': int(at) if at.isdigit() else None,
                      'who': person_key(who) or who, 'subject': subject})
    return found


def coming(server, limit=MERGES_SHOWN):
    """[{'repo', 'branch', 'head', 'at', 'who', 'subject', 'link'}]: the open candidate branches that will owe
    this box a deploy when they merge, newest first.

    Justin, #ramp, 27 Nov 2026: "I don't think we want 'deploy' as a type of todo at all any more, right? It's
    now its own thing." What a hand-written deploy item carried that the queue couldn't was *intent about work
    that hasn't merged yet* -- "when these two land, these three boxes will want it". That is derived too: it is
    the open branches of the repositories this box runs. Nobody writes a deploy item again, and the
    forward-looking half stops depending on anybody remembering.
    """
    from . import githouse
    from .work_pages import address
    out = []
    for repo, boxes in RUNS.items():
        if server not in boxes:
            continue
        held = githouse.repository(repo)
        if held is None:
            continue
        tip = githouse._sha(held, f'refs/heads/{_deploy_branch(repo)}')
        if not tip:
            continue
        for one in _candidates(held, repo):
            if githouse._is_ancestor(held, one['head'], tip):
                continue          # already in what the box deploys from: owed, not coming
            if repo in BY_PATH and not _branch_touches(held, one['head'], tip, server):
                continue
            out.append(dict(one, repo=repo, link=address(repo, one['branch'])))
    out.sort(key=lambda b: b['at'] or 0, reverse=True)
    return out[:limit]


# --- pinned: merged, and still not on its way --------------------------------------------------------

def pinned(server, runs=None):
    """[{'repo', 'pin', 'head', 'behind', 'merges', 'how', 'pin_in', 'file', 'arg'}] for what this box runs at a
    commit another repository names. behind is how many merges the deploy branch has had since the pin."""
    from . import githouse, ramp
    from .work_pages import _read
    runs = ramp.running() if runs is None else runs
    out = []
    for repo, how in PINNED.items():
        if how['server'] != server:
            continue
        held = githouse.repository(repo)
        config = githouse.repository(how['pin_in'])
        if held is None or config is None:
            continue
        at = (runs.get(how['pin_in']) or {}).get(server) or f'refs/heads/{_deploy_branch(how["pin_in"])}'
        pin = _pin_in(_read(config, 'show', f'{at}:{how["file"]}'), how['arg'])
        head = githouse._sha(held, f'refs/heads/{_deploy_branch(repo)}')
        if not pin or not head:
            continue
        # A pin usually names a branch's own head, which the merge that landed it is built on. That merge brings
        # the box nothing it hasn't got, so it isn't waiting for anything: only merges whose work the pin doesn't
        # already hold are.
        waiting = [] if githouse._is_ancestor(held, head, pin) else [
            m for m in merges(repo, since_commit=pin)
            if not (m.get('head') and githouse._is_ancestor(held, m['head'], pin))]
        out.append({'repo': repo, 'pin': pin, 'head': head, 'behind': len(waiting), 'merges': waiting,
                    'how': how['how'], 'pin_in': how['pin_in'], 'file': how['file'], 'arg': how['arg']})
    return out


# --- the Moods' own deploy items, folded into their box ------------------------------------------------

def _branch_state(repo, branch):
    from . import githouse
    if githouse.branch_merged(repo, branch):
        return 'merged'
    if githouse.branch_closed(repo, branch):
        return 'closed'
    if githouse.branch_gone(repo, branch):
        return 'gone'
    return 'open'


def asks(items=None):
    """{server: [ask]} for every open deploy item of every Mood that names a box: its words, its Mood, who it's
    for, and each candidate branch it names with what githouse says of that branch."""
    from .todo import all_todos
    from .tools_ask import named_branches
    from .work_pages import address
    if items is None:
        items = (all_todos() or {}).get('items') or []
    found = {}
    for item in items:
        if item.get('type') != 'deploy' or item.get('done') or item.get('merged'):
            continue
        boxes = servers_named(item.get('task') or '')
        if not boxes:
            continue
        waiting = []
        named = named_branches(f"{item.get('task') or ''} {item.get('note') or ''}") or []
        for repo, branch in named:
            state = _branch_state(repo, branch)
            waiting.append({'repo': repo, 'branch': branch, 'state': state, 'link': address(repo, branch)})
        ask = {'task': item.get('task') or '', 'note': item.get('note') or '', 'mood': item.get('mood') or '',
               'mood_title': item.get('mood_title') or item.get('mood') or '', 'who': item.get('who') or [],
               'said': item.get('said') or '', 'waiting': waiting,
               'held': [w for w in waiting if w['state'] == 'open'],
               'boxes': boxes}
        for box in boxes:
            found.setdefault(box, []).append(ask)
    return found


# --- the queue ----------------------------------------------------------------------------------------

def queue(fresh=False):
    """{'boxes': [...], 'owed': N, 'ready': N, 'asks': N}: one box for each server, in servers.py's order, with
    what it last deployed, what has merged and waits for it, what it runs at a pin, what open work will want it
    when it merges, and the Moods' asks folded in. Kept KEEP seconds; a finished deploy drops it (servers.record_deploy)."""
    if not fresh:
        kept = cache.get(KEY)
        if kept is not None:
            return kept
    from . import ramp, servers as srv
    runs = {}
    try:
        runs = ramp.running()
    except Exception as e:  # noqa: BLE001 -- no running commits: every box is read by its deploy's clock
        log.warning('deploys: running: %s', e)
    all_asks = {}
    try:
        all_asks = asks()
    except Exception as e:  # noqa: BLE001 -- the lists are PickiPedia's; the boxes stand without them
        log.warning('deploys: asks: %s', e)
    boxes = []
    for server in srv.SERVERS:
        name = server['name']
        last = srv.last_finished(name) or {}
        try:
            waiting = owed(name, runs=runs, finished=last)
        except Exception as e:  # noqa: BLE001 -- githouse unreadable: the box says what it ran, and no more
            log.warning('deploys: owed %s: %s', name, e)
            waiting = []
        try:
            holds = pinned(name, runs=runs)
        except Exception as e:  # noqa: BLE001
            log.warning('deploys: pinned %s: %s', name, e)
            holds = []
        try:
            soon = coming(name)
        except Exception as e:  # noqa: BLE001 -- githouse unreadable: the box still says what has merged
            log.warning('deploys: coming %s: %s', name, e)
            soon = []
        mine = all_asks.get(name) or []
        boxes.append({
            'name': name, 'about': server.get('about') or '', 'page': srv.page_url(name),
            'deploy': {k: v for k, v in last.items() if k in ('at', 'at_ts', 'by', 'commit', 'commits', 'note')},
            'running': {repo: (where or {}).get(name) for repo, where in runs.items() if (where or {}).get(name)},
            'owed': waiting, 'pinned': holds, 'asks': mine, 'coming': soon,
            'held': sorted({f"{w['repo']}:{w['branch']}" for ask in mine for w in ask['held']}),
            'ready': bool(waiting),
        })
    # A box that is owed something first: that is what anyone opening this came to see. Within each half,
    # servers.py's order, so the page doesn't shuffle under a reader.
    boxes.sort(key=lambda b: not (b['owed'] or any(p['behind'] for p in b['pinned'])))
    found = {'boxes': boxes, 'owed': sum(len(b['owed']) for b in boxes),
             'ready': sum(1 for b in boxes if b['ready']), 'asks': sum(len(b['asks']) for b in boxes),
             'coming': sum(len(b['coming']) for b in boxes)}
    cache.set(KEY, found, KEEP)
    return found


def forget():
    """Read again at the next look: a deploy finished, or a merge landed."""
    cache.delete(KEY)
