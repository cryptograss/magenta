"""The ramp, as a page (/moods/pulls/; Cryptograss:Moods/ramp/Plan, section 2).

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
from datetime import datetime, timedelta, timezone

from django.core.cache import cache

log = logging.getLogger(__name__)

KEY = 'ramp:board'
BOARD_FOR = 60          # seconds the page's data is kept: githouse fetches every minute
CARDS_KEY = 'ramp:cards'
CARDS_FOR = 300         # seconds the cards are kept: they're written hourly, or when GitHub's webhook says
MENTIONS_WITHIN = timedelta(days=21)
MENTIONS_KEY = 'ramp:mentions'
MENTIONS_FOR = 300      # seconds the mentions are kept: the record is the slow part of the page
MENTIONS_BUDGET_MS = 4000  # the query's most, on PostgreSQL: past it, the page shows no mentions, not a spinner
MENTION_ROWS = 3000     # candidate posts read at most, newest first
MERGED_SHOWN = 5
NAMES_SHOWN = 12
LOG_SHOWN = 15
# Temperature: hours since the last push or mention.
WARMTH = ((24, 'hot'), (72, 'warm'), (14 * 24, 'cool'))
_FIELD = re.compile(r'^\|(\w+)=(.*)$', re.M)
_QUIET_SENDERS = ('mood-poller', 'system')  # wakes quote what was said; a mention is someone saying it


# --- the cards, from PickiPedia --------------------------------------------------------------------

def _parse(text):
    """A card's {{PR}} values: {field: value}, entities undone."""
    return {k: html.unescape(v).strip() for k, v in _FIELD.findall(text or '')}


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


def _base(refs, wanted=''):
    """The branch a piece of work goes into: the one its pull request names, else production, main, master."""
    for name in ([wanted] if wanted else []) + ['production', 'main', 'master']:
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
    """What naming this piece of work looks like in a post."""
    ways = []
    if item.get('number'):
        n = item['number']
        ways.append(rf'\b(?:{"|".join(map(re.escape, _aliases(item["repo"])))})#{n}\b')
        ways.append(rf'/{re.escape(item["repo"])}/pull/{n}\b')
        if item['repo'] == 'magenta':  # bare, a number is magenta's (tools_ask.named_pulls)
            ways.append(rf'(?<![\w.#/-])#{n}\b')
    if '/' in (item.get('branch') or ''):
        ways.append(rf'(?<![\w/.-]){re.escape(item["branch"])}(?![\w/-])')
    return re.compile('|'.join(ways), re.I) if ways else None


def _needles(item):
    """Plain strings a post naming it must contain: the database narrows by these, the pattern decides."""
    out = []
    if item.get('number'):
        out += [f'#{item["number"]}', f'/pull/{item["number"]}']
    if '/' in (item.get('branch') or ''):
        out.append(item['branch'])
    return out


def _candidates(want, now):
    """The posts that might name something, newest first, or None if the record took longer than its budget.
    One query, every item's needles OR'd: at most MENTION_ROWS posts in MENTIONS_WITHIN days, so a very
    busy name can crowd a quiet one's out of the window -- a cap, not a promise (re's review of #420)."""
    from django.db import OperationalError, connection, transaction
    from django.db.models import TextField
    from django.db.models.functions import Cast
    from conversations.models import Message
    try:
        query = (Message.objects.filter(created_at__gte=now - MENTIONS_WITHIN, mood__isnull=False, is_sidechain=False)
                 .exclude(sender_id__in=_QUIET_SENDERS).annotate(t=Cast('content', TextField())).filter(want)
                 .select_related('mood').order_by('-created_at')[:MENTION_ROWS])
        with transaction.atomic():
            if connection.vendor == 'postgresql':
                with connection.cursor() as cursor:
                    cursor.execute(f'SET LOCAL statement_timeout = {int(MENTIONS_BUDGET_MS)}')
            return [(m.mood.slug, str(m.id), m.created_at, m.sender_id, m.content) for m in query]
    except OperationalError as e:  # the budget ran out (or the database is away): the page stands without them
        log.warning('ramp: mentions: %s', e)
        return None


def mentions(items, now):
    """Each item's last two mentions in any Mood, newest first: item['mentions'] = [{'mood', 'id', 'at', 'who'}].
    False if the record couldn't say in time (the page says so); True otherwise."""
    from django.db.models import Q
    from .mood_view import prose
    want, patterns = Q(), []
    for item in items:
        item['mentions'] = []
        pattern = _pattern(item)
        if pattern is None:
            continue
        patterns.append((item, pattern))
        for needle in _needles(item):
            want |= Q(t__icontains=needle)
    if not patterns:
        return True
    key = MENTIONS_KEY + ':' + str(abs(hash(tuple(sorted(n for i, _ in patterns for n in _needles(i))))))
    rows = cache.get(key)
    if rows is None:
        rows = _candidates(want, now)
        if rows is None:
            return False
        cache.set(key, rows, MENTIONS_FOR)
    for slug, mid, at, who, content in rows:
        text = prose(content)
        if not text:
            continue
        for item, pattern in patterns:
            if len(item['mentions']) < 2 and pattern.search(text):
                item['mentions'].append({'mood': slug, 'id': mid, 'at': at, 'who': who})
    return True


def asks(items):
    """Each item an open merge ask (🪢) names: item['asked'] = {'by', 'mood', 'id', 'at'}."""
    from .tools_ask import named_branches, named_pulls, open_requests
    for slug, open_ in open_requests().items():
        for ask in open_:
            if ask['kind'] != 'merge':
                continue
            pulls = {(repo, n) for _, repo, n in (named_pulls(ask['for']) or [])}
            branches = set(named_branches(ask['for']) or [])
            for item in items:
                if (item['repo'], item.get('number')) in pulls or (item['repo'], item.get('branch')) in branches:
                    item['asked'] = {'by': ask['emergent'], 'mood': slug, 'id': ask['id'], 'at': ask['at']}
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


def live_on(repo_name, head, runs):
    """The servers running a commit that contains head; None if this repository's deploys don't say."""
    from . import githouse
    repo = githouse.repository(repo_name)
    if repo is None or not head or repo_name not in runs:
        return None
    return [server for server, commit in sorted(runs[repo_name].items())
            if githouse._sha(repo, commit) and githouse._is_ancestor(repo, head, commit)]


# --- the board -------------------------------------------------------------------------------------

def board(now=None):
    """Everything /moods/pulls/ shows: {'open', 'merged', 'names', 'log', 'repos', 'error'}."""
    if now is None:
        found = cache.get(KEY)
        if found is not None:
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
                'pushed': _card_time(c.get('pushed')), 'size': {}, 'held': repo_name in held}
        head = refs.get(repo_name, {}).get(f'refs/pull/{number}/head')
        if head:
            item['head'] = head[0]
            item['pushed'] = _when(head[1]) or item['pushed']
            item['size'] = _size(held[repo_name], _base(refs[repo_name], item['base']), head[0])
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
            base = _base(found)
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
                       'author': c.get('author', ''), 'worked_on_by': c.get('worked_on_by', ''),
                       'moods': [m.strip() for m in c.get('moods', '').split(',') if m.strip()],
                       'deploy': c.get('deploy', ''), 'merged': _card_time(c.get('merged')), 'base': c.get('base', ''),
                       'live_on': live_on(repo_name, head[0] if head else '', runs)})
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
              'mentions_missing': not said}
    cache.set(KEY, result, BOARD_FOR)
    return result


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
