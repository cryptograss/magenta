"""The ramp, as a page (/ramp/; Cryptograss:Moods/ramp/Plan, section 2).

Justin's voice memo of 30 Oct, in #ramp: every open pull request across our repositories, and the last few
merged, with who worked on it and where, sorted by last push or last mention, with a temperature (re, the
same day). Records on PickiPedia, the view on magenta: this page writes nothing; it reads

* the cards (VersionControl:PRs/<repo>/<n>, services/pr_cards.py): title, author, who worked on it, the
  Moods, the deploy note, from the foot of each description;
* githouse's copy (services/githouse.py): each pull request's head and when it was made, how far it is
  from the branch it targets, whether it has merged (fresher than a card, which is looked at hourly), and
  the branches pushed there that have no pull request yet -- a mergeable is a branch, named once;
* the record: the last two posts in any Mood that name it ("magenta#415", "#415" for magenta, a link to
  the pull request, or its branch), and any merge ask (🪢) still open for it;
* what's running: magenta's own commit, and the maybelle-config commit each server last deployed. No other
  repository's deploys say what they ran yet (Plan, section 4), so for those "live" is left unsaid.
"""

import html
import logging
import re
import time
from datetime import datetime, timedelta, timezone

from django.core.cache import cache

log = logging.getLogger(__name__)

KEY = 'ramp:board'
BOARD_FOR = 60          # seconds the page's data is kept: githouse fetches every minute
CARDS_KEY = 'ramp:cards'
CARDS_FOR = 300         # seconds the cards are kept: they're written hourly, or when GitHub's webhook says
# Who named what: an index of every post naming a pull request or an owner's branch, built once (in the
# background) over MENTIONS_WITHIN, then kept up from what's new only -- as links.py keeps the '#m-' links.
# The page asked the whole record afresh at first (#420), and on maybelle that took past its 4 s budget.
MENTIONS_WITHIN = timedelta(days=60)
REFS = 'ramp:refs'                # {key: [mention, mention]}, newest first
REFS_BUILT = 'ramp:refs:built'    # when the whole index was last built
REFS_CURSOR = 'ramp:refs:cursor'  # the newest post it has read
REFS_FRESH = 'ramp:refs:fresh'    # when it last read what's new
REFS_BUILDING = 'ramp:refs:building'
REBUILD_EVERY = 3600    # seconds: a post imported out of order is met within the hour
FRESH_EVERY = 30        # seconds between reads of what's new
BUILD_BUDGET_MS = 60000  # the build's most, on PostgreSQL; it runs off the request
BUILD_RETRY = 600       # seconds before a build that failed is tried again
BUILD_IN_BACKGROUND = True  # tests build in the request, so nothing writes into another test's database
MERGED_SHOWN = 12        # with every branch githouse merged, not only pull requests (Justin, 10 Nov)
NAMES_SHOWN = 12
LOG_SHOWN = 15
# Temperature: hours since the last push or mention.
WARMTH = ((24, 'hot'), (72, 'warm'), (14 * 24, 'cool'))
_FIELD = re.compile(r'^\|(\w+)=(.*)$', re.M)
_QUIET_SENDERS = ('mood-poller', 'system')  # wakes quote what was said; a mention is someone saying it


# --- the cards, from PickiPedia --------------------------------------------------------------------

_BODY = re.compile(r'<pre class="pr-body">(.*?)</pre>', re.S)


def _parse(text):
    """A card's {{PR}} values: {field: value}, entities undone; and its description, if the card keeps it."""
    found = {k: html.unescape(v).strip() for k, v in _FIELD.findall(text or '')}
    body = _BODY.search(text or '')
    if body:
        found['description'] = html.unescape(body[1]).strip()
    return found


def cards():
    """{'open': [card], 'merged': [card]}: every open or draft pull request's card, and the newest merged."""
    found = cache.get(CARDS_KEY)
    if found is not None:
        return found
    from .embeds import _ask
    try:
        open_ = list((_ask({'action': 'ask', 'query': '[[Category:Pull requests]][[PR state::open||draft]]|limit=200'})
                      .get('query', {}).get('results') or {}))
        merged = list((_ask({'action': 'ask', 'query': '[[Category:Pull requests]][[PR state::merged]]'
                                                       f'|sort=Merged at|order=desc|limit={MERGED_SHOWN * 2}'})
                       .get('query', {}).get('results') or {}))
        texts = {}
        wanted = open_ + merged
        for start in range(0, len(wanted), 50):
            answer = _ask({'action': 'query', 'titles': '|'.join(wanted[start:start + 50]), 'prop': 'revisions',
                           'rvprop': 'content', 'rvslots': 'main'}, timeout=8)
            for page in (answer.get('query', {}).get('pages') or {}).values():
                revs = page.get('revisions') or [{}]
                texts[page.get('title')] = ((revs[0].get('slots') or {}).get('main') or {}).get('*', '')
        found = {'open': [_parse(texts.get(t)) for t in open_ if texts.get(t)],
                 'merged': sorted((_parse(texts.get(t)) for t in merged if texts.get(t)),
                                  key=lambda c: c.get('merged', ''), reverse=True)}
    except Exception as e:  # noqa: BLE001 -- the wiki away: githouse's half still shows
        log.warning('ramp: cards: %s', e)
        found = {'open': [], 'merged': [], 'error': 'PickiPedia didn\'t answer: the cards are missing'}
        cache.set(CARDS_KEY, found, 30)
        return found
    cache.set(CARDS_KEY, found, CARDS_FOR)
    return found


# --- githouse's copy ---------------------------------------------------------------------------------

def _refs(repo):
    """{refname: (sha, committed unix time, author, subject)} for every branch, pull head and retired name."""
    from .githouse import _git
    out = _git(repo, 'for-each-ref', '--format=%(refname)%00%(objectname)%00%(committerdate:unix)%00%(authorname)%00%(subject)',
               'refs/heads/', 'refs/pull/', 'refs/merged/', 'refs/closed/')
    found = {}
    for line in out.splitlines():
        parts = line.split('\0')
        if len(parts) == 5:
            found[parts[0]] = (parts[1], int(parts[2] or 0), parts[3], parts[4])
    return found


def githouse_merged(repo, found, name, reach=200):
    """The branches githouse has merged into this repository's deploy branch, newest first: [{'repo', 'branch',
    'number': None, 'title', 'url': '', 'author', 'worked_on_by': '', 'moods': [], 'deploy': '', 'merged', 'base',
    'head', 'merge'}]. Each is a refs/merged/<branch>, matched to the merge on the deploy branch whose second parent
    is its head; one with no such merge in the last `reach` (merged by hand elsewhere) takes its head's time."""
    from .githouse import _git
    base = _base(found, '', name)
    if not base:
        return []
    brought = {}
    out = _git(repo, 'log', '--first-parent', '--merges', f'-n{reach}', '--format=%H%x00%ct%x00%P',
               f'refs/heads/{base}')
    for line in out.splitlines():
        parts = line.split('\0')
        if len(parts) == 3 and len(parts[2].split()) > 1:
            brought.setdefault(parts[2].split()[1], (parts[0], int(parts[1] or 0)))
    rows = []
    for ref, (sha, unix, author, subject) in found.items():
        if not ref.startswith('refs/merged/'):
            continue
        branch = ref[len('refs/merged/'):]
        merge, when = brought.get(sha, ('', unix))
        rows.append({'repo': name, 'number': None, 'title': subject, 'url': '', 'branch': branch,
                     'author': branch.split('/')[0] if '/' in branch else author, 'worked_on_by': '', 'moods': [],
                     'deploy': '', 'merged': _when(when), 'base': base, 'head': sha, 'merge': merge})
    floor = datetime.min.replace(tzinfo=timezone.utc)
    rows.sort(key=lambda r: r['merged'] or floor, reverse=True)
    return rows


def _base(refs, wanted='', repo=''):
    """The branch a piece of work goes into: the one its pull request names, else the one its repository deploys
    from (merge_orders.DEPLOYS_FROM, as the Merge button merges into), else production, main, master. Not
    production first for every repository: magenta's is long dead, and a branch measured against it showed a
    year of commits (9 Nov)."""
    from .merge_orders import DEPLOYS_FROM
    deploys = [DEPLOYS_FROM[repo]] if repo in DEPLOYS_FROM else []
    for name in ([wanted] if wanted else []) + deploys + ['production', 'main', 'master']:
        if f'refs/heads/{name}' in refs:
            return name
    return ''


def _size(repo, base, head):
    """{'commits', 'files', 'added', 'removed'} of head beyond base, or {}."""
    from .githouse import _git
    if not base or not head:
        return {}
    count = _git(repo, 'rev-list', '--count', f'refs/heads/{base}..{head}').strip()
    stat = _git(repo, 'diff', '--shortstat', f'refs/heads/{base}...{head}')
    number = lambda word: int((re.search(rf'(\d+) {word}', stat) or [0, 0])[1])
    return {'commits': int(count or 0), 'files': number('file'), 'added': number('insertion'),
            'removed': number('deletion')}


def _when(unix):
    return datetime.fromtimestamp(unix, timezone.utc) if unix else None


def _card_time(text):
    try:
        return datetime.strptime(text, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc) if text else None
    except ValueError:
        return None


# --- the record ------------------------------------------------------------------------------------

def _aliases(repo):
    from .tools_ask import _REPOS
    return sorted({repo} | {alias for alias, name in _REPOS.items() if name == repo and alias != 'pr'})


def _pattern(item):
    """What naming this piece of work looks like in a post (refs_in finds these; kept for reading it plainly)."""
    ways = []
    if item.get('number'):
        n = item['number']
        ways.append(rf'\b(?:{"|".join(map(re.escape, _aliases(item["repo"])))})#{n}\b')
        ways.append(rf'/{re.escape(item["repo"])}/pull/{n}\b')
        if item['repo'] == 'magenta':  # bare, a number is magenta's (tools_ask.named_pulls)
            ways.append(rf'(?<![\w.#/&-])#{n}\b')
    if '/' in (item.get('branch') or ''):
        ways.append(rf'(?<![\w/.-]){re.escape(item["branch"])}(?![\w/-])')
    return re.compile('|'.join(ways), re.I) if ways else None


_PULL_REF = re.compile(r'(?:\b([A-Za-z][\w.-]*))?#(\d+)\b')
_URL_REF = re.compile(r'/cryptograss/([\w.-]+)/pull/(\d+)\b', re.I)
_BRANCH_REF = re.compile(r'(?<![\w/.-])([a-z][a-z0-9-]*)/([A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*)')
# What the database narrows by before a post is read: a number after '#', a pull request's address, or a slash.
_MAYBE = r'#[0-9]|/pull/[0-9]|[a-z]/[A-Za-z0-9]'


def refs_in(text, owners):
    """The work a post names: {('pull', repo, n)} and {('branch', '<owner>/<name>')}, owners being the names a
    branch may start with. Bare, a number is magenta's; a prefix not known here names nothing."""
    from .tools_ask import _REPOS
    found = set()
    for m in _URL_REF.finditer(text):
        found.add(('pull', m[1].lower(), int(m[2])))
    for m in _PULL_REF.finditer(text):
        if m[1] is None and m.start() and text[m.start() - 1] in '#/&.-':  # an entity, a URL's anchor
            continue
        repo = _REPOS.get((m[1] or 'magenta').lower())
        if repo:
            found.add(('pull', repo, int(m[2])))
    for m in _BRANCH_REF.finditer(text):
        if m[1] in owners:
            found.add(('branch', f'{m[1]}/{m[2]}'.rstrip('.')))
    return found


def _owners():
    """The names a branch may start with: everyone here, and every prefix githouse holds a branch under."""
    from conversations.models import ThinkingEntity
    from . import githouse
    owners = {n.lower() for n in ThinkingEntity.objects.values_list('name', flat=True)}
    for name in githouse.repositories():
        for ref in _refs(githouse.repository(name)):
            parts = ref.split('/')
            if len(parts) > 3 and parts[1] in ('heads', 'merged', 'closed'):
                owners.add(parts[2].lower())
    return owners


def _posts(since):
    """(mood, id, at, who, text) of every post said in a Mood since then that might name some work, oldest first:
    no tool calls, thinking, wrappers or wakes -- what a person or an emergent said."""
    from django.db.models import TextField
    from django.db.models.functions import Cast
    from conversations.models import Message, Mood
    from .mood_view import MACHINERY_SENDERS, is_wrapper, prose
    # Tool calls and thinking are left out by the database, not after their content has come back (re's review of
    # #421): on a 60-day build, that's most of the rows.
    rows = (Message.objects.filter(mood_id__in=list(Mood.objects.values_list('id', flat=True)), is_sidechain=False,
                                   created_at__gt=since, tooluse__isnull=True, thought__isnull=True)
            .exclude(sender_id__in=MACHINERY_SENDERS | set(_QUIET_SENDERS))
            .annotate(t=Cast('content', TextField())).filter(t__regex=_MAYBE)
            .select_related('mood').order_by('created_at'))
    for m in rows:
        text = prose(m.content)
        if text and not is_wrapper(text):
            yield m.mood.slug, str(m.id), m.created_at, m.sender_id, text


def _add(index, owners, posts):
    """Each post into the index: the two newest per piece of work."""
    for slug, mid, at, who, text in posts:
        for key in refs_in(text, owners):
            here = index.setdefault(key, [])
            if any(e['id'] == mid for e in here):
                continue
            here.insert(0, {'mood': slug, 'id': mid, 'at': at, 'who': who})
            here.sort(key=lambda e: e['at'], reverse=True)
            del here[2:]


def _build():
    """The whole index, over MENTIONS_WITHIN; under a budget on PostgreSQL."""
    from django.db import connection, transaction
    from django.utils import timezone
    started = timezone.now()
    index, owners = {}, _owners()
    try:
        with transaction.atomic():
            if connection.vendor == 'postgresql':
                with connection.cursor() as cursor:
                    cursor.execute(f'SET LOCAL statement_timeout = {int(BUILD_BUDGET_MS)}')
            _add(index, owners, _posts(started - MENTIONS_WITHIN))
        cache.set(REFS, index, None)
        cache.set(REFS_CURSOR, started, None)
        cache.set(REFS_BUILT, started.timestamp(), None)
        cache.set(REFS_FRESH, started.timestamp(), None)
        cache.delete(REFS_BUILDING)
    except Exception as e:  # noqa: BLE001 -- tried again in ten minutes, not on the next page
        log.warning('ramp: mentions index: %s', e)
        # The lock is held ten minutes: before any index is built, found is None and nothing else stops the next
        # load from starting another full read (re's review of #421).
        cache.set(REFS_BUILDING, 1, BUILD_RETRY)
        cache.set(REFS_BUILT, started.timestamp() - REBUILD_EVERY + BUILD_RETRY, None)


def mention_index():
    """{('pull', repo, n) | ('branch', name): [mention, mention]}, or None while it's first being built."""
    import time
    found = cache.get(REFS)
    now = time.time()
    if found is None or now - (cache.get(REFS_BUILT) or 0) > REBUILD_EVERY:
        if cache.add(REFS_BUILDING, 1, BUILD_BUDGET_MS // 1000 + 30):
            if BUILD_IN_BACKGROUND:
                from .todo import _in_background
                _in_background(_build)
            else:
                _build()
                found = cache.get(REFS)
        return found
    if now - (cache.get(REFS_FRESH) or 0) > FRESH_EVERY and cache.add(REFS_BUILDING + ':fresh', 1, 30):
        try:
            from django.utils import timezone
            started = timezone.now()
            cursor = (cache.get(REFS_CURSOR) or started) - timedelta(seconds=60)  # a write still in flight
            _add(found, _owners(), _posts(cursor))
            cache.set(REFS, found, None)
            cache.set(REFS_CURSOR, started, None)
            cache.set(REFS_FRESH, now, None)
        except Exception as e:  # noqa: BLE001 -- what's new is read next time
            log.warning('ramp: mentions, what is new: %s', e)
        finally:
            cache.delete(REFS_BUILDING + ':fresh')
    return found


def mentions(items, now):
    """Each item's last two mentions in any Mood, newest first: item['mentions'] = [{'mood', 'id', 'at', 'who'}].
    False while the index is first being built (the page says so); True otherwise."""
    index = mention_index()
    for item in items:
        keys = []
        if item.get('number'):
            keys.append(('pull', item['repo'], item['number']))
        if '/' in (item.get('branch') or ''):
            keys.append(('branch', item['branch']))
        seen, found = set(), []
        for key in keys:
            for e in (index or {}).get(key, []):
                if e['id'] not in seen:
                    seen.add(e['id'])
                    found.append(e)
        item['mentions'] = sorted(found, key=lambda e: e['at'], reverse=True)[:2]
    return index is not None


def asks(items):
    """The open merge asks (🪢) naming each item: item['asks'] = [{'by', 'mood', 'id', 'at', 'colour'}], one per
    emergent asking (its newest), oldest first, each drawn as a dot of its colour (Justin, 9 Nov); and
    item['asked'], the newest of them."""
    from .front import agent_colour
    from .tools_ask import named_branches, named_pulls, open_requests
    for slug, open_ in open_requests().items():
        for ask in open_:
            if ask['kind'] != 'merge':
                continue
            pulls = {(repo, n) for _, repo, n in (named_pulls(ask['for']) or [])}
            branches = set(named_branches(ask['for']) or [])
            for item in items:
                if (item['repo'], item.get('number')) in pulls or (item['repo'], item.get('branch')) in branches:
                    mine = {'by': ask['emergent'], 'mood': slug, 'id': ask['id'], 'at': ask['at'],
                            'colour': agent_colour(ask['emergent'])}
                    had = next((a for a in item.setdefault('asks', []) if a['by'] == mine['by']), None)
                    if had is None:
                        item['asks'].append(mine)
                    elif str(had['at']) < str(mine['at']):
                        had.update(mine)
    for item in items:
        if item.get('asks'):
            item['asks'].sort(key=lambda a: str(a['at']))
            item['asked'] = item['asks'][-1]
    return items


def temperature(item, now):
    times = [t for t in [item.get('pushed')] + [m['at'] for m in item.get('mentions', [])] if t]
    item['active'] = max(times) if times else None
    if item['active'] is None:
        return 'cold'
    hours = (now - item['active']).total_seconds() / 3600
    return next((name for limit, name in WARMTH if hours < limit), 'cold')


# --- what's running --------------------------------------------------------------------------------

def running():
    """{repo: {server: commit}} for what we know is running: magenta's own commit on maybelle, and the
    maybelle-config commit each server's last deploy ran."""
    from . import servers, status
    found = {}
    try:
        commit = (status.magenta_version() or {}).get('commit')
        if commit:
            found['magenta'] = {'maybelle': commit}
    except Exception as e:  # noqa: BLE001
        log.warning('ramp: magenta version: %s', e)
    for name in servers.NAMES:
        deploy = servers.last_deploy(name) or {}
        if deploy.get('commit') and deploy.get('state') == 'finished':
            found.setdefault('maybelle-config', {})[name] = deploy['commit']
    return found


# --- branch tests (Plan, section 11): what Jenkins' branch-tests job said of each commit -----------------
TESTS_KEY = 'ramp:tests'
TESTS_FOR = 30   # seconds: a run takes a few minutes, and the page is looked at while one runs
_TESTED = {'SUCCESS': 'passed', 'FAILURE': 'failed', 'UNSTABLE': 'failed', 'ABORTED': 'stopped',
           'NOT_BUILT': 'no ci/test'}


def test_runs():
    """{(repo, commit): {'state', 'url', 'number'}}: the newest branch-tests run of each commit, from Jenkins'
    public API (anonymous may read). 'state' is passed, failed, testing, stopped or "no ci/test". {} if
    Jenkins doesn't answer: the page goes on without."""
    from django.conf import settings
    import requests
    found = cache.get(TESTS_KEY)
    if found is not None:
        return found
    base = (getattr(settings, 'JENKINS_URL', '') or '').rstrip('/')
    found = {}
    if base:
        tree = 'builds[number,result,building,url,actions[parameters[name,value]]]{0,300}'
        try:
            answer = requests.get(f'{base}/job/branch-tests/api/json', params={'tree': tree}, timeout=5)
            answer.raise_for_status()
            for run in answer.json().get('builds') or []:  # newest first
                said = {p.get('name'): p.get('value') for a in run.get('actions') or [] if isinstance(a, dict)
                        for p in a.get('parameters') or []}
                key = (said.get('REPO'), said.get('COMMIT'))
                if all(key) and key not in found:
                    state = 'testing' if run.get('building') else _TESTED.get(run.get('result'), 'failed')
                    found[key] = {'state': state, 'url': run.get('url', ''), 'number': run.get('number')}
        except (requests.RequestException, ValueError) as e:
            log.warning('ramp: branch tests: %s', e)
    cache.set(TESTS_KEY, found, TESTS_FOR)
    return found


def with_tests(items):
    """The items, each with 'tests': its head commit's newest branch-tests run, or None."""
    runs = test_runs()
    for item in items:
        item['tests'] = runs.get((item.get('repo'), item.get('head')))
    return items


def live_on(repo_name, head, runs):
    """The servers running a commit that contains head; None if this repository's deploys don't say."""
    from . import githouse
    repo = githouse.repository(repo_name)
    if repo is None or not head or repo_name not in runs:
        return None
    return [server for server, commit in sorted(runs[repo_name].items())
            if githouse._sha(repo, commit) and githouse._is_ancestor(repo, head, commit)]


# --- the board -------------------------------------------------------------------------------------

def board(now=None, stale_ok=False):
    """Everything /ramp/ shows: {'open', 'merged', 'names', 'log', 'repos', 'error'}.

    Kept BOARD_FOR seconds for the page. Building it takes seconds (4.8 s on maybelle, 9 Nov), so a caller that
    only needs a card's branch and base -- the merge sheet's preview, a 👍, a work's page, a wake's candidates --
    passes stale_ok: it gets the last board at once, however old, and an old one is rebuilt off the request
    (Justin, 9 Nov: "Looking at githouse's copy…" sat there while it built)."""
    if now is None:
        found = cache.get(KEY)
        if found is not None:
            age = time.time() - found.get('built', 0)
            if age < BOARD_FOR:
                return found
            if stale_ok:
                if cache.add(KEY + ':building', 1, 120):
                    if BUILD_IN_BACKGROUND:
                        from .todo import _in_background
                        _in_background(_rebuild)
                    else:
                        _rebuild()
                return found
    now = now or datetime.now(timezone.utc)
    from . import githouse
    held = {name: githouse.repository(name) for name in githouse.repositories()}
    refs = {name: _refs(repo) for name, repo in held.items()}
    got = cards()
    runs = running()
    items, carded = [], set()
    for c in got['open']:
        repo_name, number = c.get('repo', ''), int(c.get('number') or 0)
        item = {'repo': repo_name, 'number': number, 'title': c.get('title', ''), 'url': c.get('url', ''),
                'author': c.get('author', ''), 'branch': c.get('branch', ''), 'base': c.get('base', ''),
                'state': c.get('state', 'open'), 'worked_on_by': c.get('worked_on_by', ''),
                'moods': [m.strip() for m in c.get('moods', '').split(',') if m.strip()],
                'deploy': c.get('deploy', ''), 'opened': _card_time(c.get('opened')),
                'pushed': _card_time(c.get('pushed')), 'size': {}, 'held': repo_name in held,
                'description': c.get('description', '')}
        head = refs.get(repo_name, {}).get(f'refs/pull/{number}/head')
        if head:
            item['head'] = head[0]
            item['pushed'] = _when(head[1]) or item['pushed']
            item['size'] = _size(held[repo_name], _base(refs[repo_name], item['base'], repo_name), head[0])
            if githouse.commit_merged(repo_name, head[0]):
                item['state'] = 'merged'  # githouse knows before the card is looked at again
        carded.add((repo_name, item['branch']))
        items.append(item)
    for name, found in refs.items():
        for ref, (sha, unix, author, subject) in found.items():
            if not ref.startswith('refs/heads/'):
                continue
            branch = ref[len('refs/heads/'):]
            if githouse_owned(branch) or (name, branch) in carded:
                continue
            base = _base(found, '', name)
            items.append({'repo': name, 'number': None, 'title': subject, 'url': '', 'author': branch.split('/')[0],
                          'branch': branch, 'base': base, 'state': 'pushed', 'worked_on_by': '', 'moods': [],
                          'deploy': '', 'opened': None, 'pushed': _when(unix), 'head': sha,
                          'size': _size(held[name], base, sha), 'held': True})
    items = [i for i in items if i['state'] != 'merged'] + [i for i in items if i['state'] == 'merged']
    said = mentions(items, now)
    try:
        asks(items)
    except Exception as e:  # noqa: BLE001 -- the asks are a nicety here
        log.warning('ramp: asks: %s', e)
    for item in items:
        item['temperature'] = temperature(item, now)
    merged = []
    for c in got['merged'][:MERGED_SHOWN]:
        repo_name, number = c.get('repo', ''), int(c.get('number') or 0)
        head = refs.get(repo_name, {}).get(f'refs/pull/{number}/head')
        merged.append({'repo': repo_name, 'number': number, 'title': c.get('title', ''), 'url': c.get('url', ''),
                       'branch': c.get('branch', ''),
                       'author': c.get('author', ''), 'worked_on_by': c.get('worked_on_by', ''),
                       'moods': [m.strip() for m in c.get('moods', '').split(',') if m.strip()],
                       'deploy': c.get('deploy', ''), 'merged': _card_time(c.get('merged')), 'base': c.get('base', ''),
                       'live_on': live_on(repo_name, head[0] if head else '', runs)})
    # And every branch githouse merged, pull request or not (Justin, 10 Nov: "we need to list branches merged
    # through githouse"): refs/merged/<branch>, with when and as what from the merge that brought it in.
    carded = {(m['repo'], m['branch']) for m in merged if m['branch']}
    for name, repo in held.items():
        for m in githouse_merged(repo, refs.get(name, {}), name):
            if (name, m['branch']) not in carded:
                merged.append({**m, 'live_on': live_on(name, m['head'], runs)})
    floor = datetime.min.replace(tzinfo=timezone.utc)
    merged.sort(key=lambda m: m.get('merged') or floor, reverse=True)
    merged = merged[:MERGED_SHOWN]
    names = []
    for name, found in refs.items():
        for ref, (sha, unix, author, subject) in found.items():
            for kind in ('merged', 'closed'):
                if ref.startswith(f'refs/{kind}/'):
                    names.append({'repo': name, 'branch': ref[len(f'refs/{kind}/'):], 'kind': kind,
                                  'at': _when(unix), 'subject': subject})
    names.sort(key=lambda n: n['at'] or now, reverse=True)
    result = {'open': items, 'merged': merged, 'names': names[:NAMES_SHOWN], 'log': log_tail(),
              'repos': sorted(held), 'error': got.get('error', ''), 'at': now,
              'mentions_missing': not said, 'built': time.time()}
    cache.set(KEY, result, None)  # kept: an old board is what a stale_ok caller gets while the next is built
    return result


def changed():
    """Something the board shows has changed (a merge, Justin, 9 Nov: "it still shows that CB as unmerged"): the
    kept board is marked old, so the ramp page builds a fresh one, and one is built now, behind the request."""
    found = cache.get(KEY)
    if found is None:
        return  # none kept: the next to ask builds one
    cache.set(KEY, {**found, 'built': 0}, None)
    if cache.add(KEY + ':building', 1, 120):
        if BUILD_IN_BACKGROUND:
            from .todo import _in_background
            _in_background(_rebuild)
        else:
            _rebuild()


def _rebuild():
    try:
        board(now=datetime.now(timezone.utc))
    except Exception as e:  # noqa: BLE001 -- the old board serves until the next try
        log.warning('ramp: board rebuild: %s', e)
    finally:
        cache.delete(KEY + ':building')


def githouse_owned(branch):
    import fnmatch
    from .githouse import OWNED
    return any(fnmatch.fnmatchcase(branch, pattern) for pattern in OWNED)


def log_tail():
    """githouse's own log, its last lines, newest first: pushes, forwards, refusals."""
    from . import githouse
    try:
        lines = (githouse.root() / 'githouse.log').read_text(errors='replace').splitlines()
    except OSError:
        return []
    return list(reversed([line for line in lines if line.strip()][-LOG_SHOWN:]))


def ordered(items, by):
    """Open work, sorted: 'pushed' (newest push first), 'mentioned' (newest mention first), or by activity."""
    floor = datetime.min.replace(tzinfo=timezone.utc)
    key = {'pushed': lambda i: i.get('pushed') or floor,
           'mentioned': lambda i: (i.get('mentions') or [{'at': floor}])[0]['at']}.get(by, lambda i: i.get('active') or floor)
    return sorted(items, key=lambda i: (i['state'] == 'merged', -key(i).timestamp()))


# --- who and where, for the ramp's filters (Justin, 9 Nov, #ramp: "sortable by mood", "buttons to include/
# exclude each of the participants") ---------------------------------------------------------------------

_SAID_IN = re.compile(r'^\s*(in\s+)?#')


def people_of(item):
    """The people on a candidate, by the names they go by here: its "worked on by" (free words, read loosely:
    "magent, with skyler (testing on an iphone 17), justin"), then its author, a GitHub login mapped by
    work.FORGE_PEOPLE ("jMyles" is justin)."""
    from .work import FORGE_PEOPLE
    names = []
    said = re.sub(r'\([^)]*\)?', '', item.get('worked_on_by') or '')  # asides first: they hold commas too
    for part in re.split(r',|;|\band\b', said):
        part = part.strip()
        if not part or _SAID_IN.match(part):
            continue
        part = re.sub(r'^(with|by)\s+', '', part, flags=re.I)
        found = re.match(r'@?([A-Za-z][\w.-]*(\[bot\])?)', part)
        if found:
            names.append(found.group(1))
    names.append(item.get('author') or '')
    out = []
    for name in names:
        name = FORGE_PEOPLE.get(name.lower(), name.lower())
        if name and name not in out:
            out.append(name)
    return out


def with_people(items):
    """Each item gets 'people' (people_of) and 'where': its Moods, else the Moods it was last said in."""
    for i in items:
        i['people'] = people_of(i)
        where = list(i.get('moods') or [])
        for m in i.get('mentions') or []:
            if not i.get('moods') and m.get('mood') and m['mood'] not in where:
                where.append(m['mood'])
        i['where'] = where
    return items


def with_blocks(items, now=None):
    """Each item's push in blocks ago (Justin, 9 Nov, #ramp: "show 'pushed' as blocks ago"): 'pushed_block', the
    block at the push (eth_blocks' anchors), and 'pushed_ago', the head now less that. The head is tasks.head's,
    else the one the block bar fetched; with neither, nothing is guessed: the page shows the time."""
    from conversations.models import BlockAnchor
    from .eth_blocks import SECONDS_PER_SLOT, BlockClock
    from .tasks import head
    now = now or datetime.now(timezone.utc)
    anchors = list(BlockAnchor.objects.order_by('number').values_list('number', 'timestamp'))
    top = head(now, anchors)
    if top is None:  # no fresh anchor: the head the page's block bar fetched (views_moods.api_block), if it has one
        from django.core.cache import cache
        fetched = cache.get('eth-head') or {}
        try:
            at = datetime.fromisoformat(str(fetched['at']).replace('Z', '+00:00'))
            top = int(fetched['height']) + max(0, int((now - at).total_seconds())) // SECONDS_PER_SLOT
        except (KeyError, TypeError, ValueError):
            return None
    clock = BlockClock(anchors)
    for i in items:
        pushed = i.get('pushed')
        if not pushed:
            continue
        block = clock.block_at(int(pushed.timestamp()))
        if block is None:  # after the newest anchor: its 12-second slots, as tasks.head counts them
            block = top - max(0, int((now - pushed).total_seconds())) // SECONDS_PER_SLOT
        i['pushed_block'], i['pushed_ago'] = block, max(0, top - block)
        i['pushed_ago_said'] = f"{i['pushed_ago']:,}"
    return top


def everyone(items):
    """[{'name', 'count', 'colour'}] for the ramp's buttons: everyone on an open candidate, most candidates first;
    an emergent with its colour, a person (or an account) without."""
    from collections import Counter
    from conversations.models import ThinkingEntity
    from .front import agent_colour
    counts = Counter(p for i in items for p in i.get('people', []))
    humans = set(ThinkingEntity.objects.filter(name__in=counts, is_biological_human=True).values_list('name', flat=True))
    agents = set(ThinkingEntity.objects.filter(name__in=counts, is_biological_human=False).values_list('name', flat=True))
    return [{'name': n, 'count': c, 'colour': agent_colour(n) if n in agents and n not in humans else ''}
            for n, c in sorted(counts.items(), key=lambda nc: (-nc[1], nc[0]))]


# --- the candidate branches, told to each emergent on each wake (Justin, 9 Nov, #ramp) ----------------------
# "Candidate branches", not "pull requests": a pull request is GitHub's page for one (Justin, 9 Nov).

CANDIDATE_SAID = 160      # characters of a candidate's description told in a wake
_FOOTER = re.compile(r'^\s*(deploy(ment)?|worked on by)\s*:', re.I)


def short_description(text):
    """A candidate's description, short: its first paragraph that isn't its Deploy / Worked on by footer, as
    plain words, cut at CANDIDATE_SAID."""
    from .mood_view import snippet_of
    for para in re.split(r'\n\s*\n', text or ''):
        lines = [line for line in para.splitlines() if line.strip() and not _FOOTER.match(line)]
        said = snippet_of('\n'.join(lines))
        if said:
            return said if len(said) <= CANDIDATE_SAID else said[:CANDIDATE_SAID - 1].rstrip() + '…'
    return ''


def branch_of(repo, number):
    """The branch of repo's pull request #number, as the last board built says; '' if it doesn't say. Never builds a
    board (seconds, on maybelle): what asks it is drawn every few seconds (the Mood list's merge asks)."""
    for item in (cache.get(KEY) or {}).get('open', []):
        if item.get('repo') == repo and item.get('number') == number:
            return item.get('branch') or ''
    return ''


def candidates():
    """Every open candidate branch, most active first: [{'ref', 'repo', 'branch', 'number', 'title', 'about',
    'who', 'moods', 'state'}]. 'ref' is repo#N for one with a pull request, else repo:branch."""
    out = []
    for i in ordered([i for i in board(stale_ok=True).get('open', []) if i.get('state') != 'merged'], ''):
        out.append({'ref': f"{i['repo']}#{i['number']}" if i.get('number') else f"{i['repo']}:{i.get('branch', '')}",
                    'repo': i['repo'], 'branch': i.get('branch', ''), 'number': i.get('number'),
                    'title': i.get('title', ''), 'about': short_description(i.get('description', '')),
                    'who': i.get('worked_on_by') or i.get('author', ''), 'moods': i.get('moods', []),
                    'state': i.get('state', 'open')})
    return out
