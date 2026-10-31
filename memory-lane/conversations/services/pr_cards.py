"""Pull requests, kept on PickiPedia as cards: VersionControl:PRs/<repo>/<number>.

The ramp (Justin, 30 Oct 2026, the voice memo in #ramp): every open pull
request across our repositories, and the last few merged, with who worked on
it, in which Moods, and what it needs deployed -- the two lines the general
orders put at the foot of every description. Records on the wiki, the view on
magenta (re's split, the same day): a card is one {{PR}} call, and
Template:PR sets the Semantic MediaWiki properties, so #ask finds the pull
requests Justin worked on, or the ones said in #ops.

VersionControl: is a namespace of machine records (pickipedia#168), exempt
from the verification gate (pickipedia-mcp#43): GitHub's word, mirrored, not a
claim for someone to verify. So a card is written whole from what GitHub says
now, by the to-do bot (todo_ticks.Bot), and an edit made on the wiki is
overwritten at the next look. No scheduler: the runner's pulse nudges this, as
it does the link ledger (wiki_ledger), and it looks at most every LOOK_EVERY
seconds; GitHub's webhook nudges it too, for the pull request it heard of.
"""

import re
import threading
from datetime import datetime, timedelta, timezone

from django.conf import settings
from django.core.cache import cache

ORG = 'cryptograss'
LOOK_EVERY = 600       # seconds between looks at every repository
PUSHED_WITHIN = 30     # days: a repository pushed to longer ago than this isn't asked about its pull requests
CLOSED_WITHIN = 14     # days: a closed pull request keeps its card fresh this long after it closed
_FOOT_DEPLOY = re.compile(r'^\W*deploy(?:ment)?\W*:\s*(.+?)\s*$', re.I | re.M)
_FOOT_WORKED = re.compile(r'^\W*worked on by\W*:\s*(.+?)\s*$', re.I | re.M)


def title(owner, repo, number):
    """The card's page: the org's repositories by name alone, anyone else's with its owner."""
    where = repo if owner.lower() == ORG else f'{owner}/{repo}'
    return f'VersionControl:PRs/{where}/{number}'


def foot(body):
    """(deploy, [who], [mood]) from a description's foot, as the general orders put it:
    "Deploy: redeploy hunter" and "Worked on by: magent, re, Justin, in #ops and #ramp"."""
    body = body or ''
    deploy = _FOOT_DEPLOY.findall(body)
    worked = _FOOT_WORKED.findall(body)
    if not worked:
        return (deploy[-1] if deploy else ''), [], []
    line = worked[-1]
    # The Moods follow the last ", in" or " in #": "Justin, in #ops and #ramp", "re in #ops".
    split = re.search(r'(?:,\s*|\s+)in\s+(?=#)', line) or re.search(r',\s*in\s+', line)
    people, moods = (line[:split.start()], line[split.end():]) if split else (line, '')
    who = [w.strip(' .*_`') for w in re.split(r',|\band\b|&', people)]
    where = re.findall(r'#([\w-]+)', moods) or [m.strip(' .*_`') for m in re.split(r',|\band\b|&', moods)]
    return ((deploy[-1] if deploy else '').strip(' *_`'),
            [w.lower() for w in who if w], [m.lower() for m in where if m])


def _date(stamp):
    return (stamp or '')[:19].replace('T', ' ')


def state(pull):
    if pull.get('merged_at'):
        return 'merged'
    if pull.get('state') == 'closed':
        return 'closed'
    return 'draft' if pull.get('draft') else 'open'


def card(pull, pushed=''):
    """The card's wikitext: one {{PR}}, its values as GitHub gives them, the foot read from the description."""
    owner, repo = pull['base']['repo']['full_name'].split('/', 1)
    deploy, who, moods = foot(pull.get('body'))
    # Nothing in a value can end the call or start another: |, { and } as entities.
    clean = lambda s: (s or '').replace('|', '&#124;').replace('{', '&#123;').replace('}', '&#125;').replace('\n', ' ').strip()
    fields = [
        ('repo', repo if owner.lower() == ORG else f'{owner}/{repo}'),
        ('number', pull['number']),
        ('title', clean(pull.get('title'))),
        ('url', pull.get('html_url', '')),
        ('author', (pull.get('user') or {}).get('login', '')),
        ('state', state(pull)),
        ('base', pull['base'].get('ref', '')),
        ('branch', (pull.get('head') or {}).get('ref', '')),
        ('opened', _date(pull.get('created_at'))),
        ('pushed', _date(pushed)),
        ('merged', _date(pull.get('merged_at'))),
        ('closed', _date(pull.get('closed_at')) if not pull.get('merged_at') else ''),
        ('worked_on_by', ', '.join(who)),
        ('moods', ', '.join(moods)),
        ('deploy', clean(deploy)),
    ]
    lines = ['{{PR'] + [f'|{k}={v}' for k, v in fields] + ['}}']
    return '\n'.join(lines) + '\n'


def _pushed(owner, repo, pull, http):
    """When the pull request's head commit was made: its last push, near enough. Kept by commit."""
    from .todo import _github
    sha = (pull.get('head') or {}).get('sha', '')
    if not sha:
        return ''
    key = f'pr-cards:pushed:{sha}'
    found = cache.get(key)
    if found is None:
        commit = _github(f'/repos/{owner}/{repo}/commits/{sha}', http)
        found = (((commit or {}).get('commit') or {}).get('committer') or {}).get('date', '')
        if found:
            cache.set(key, found, 86400 * 30)
    return found or ''


def repositories(http, now):
    """The org's repositories pushed to lately, and any others named in settings.PR_CARD_REPOS."""
    from .todo import _github
    since = now - timedelta(days=PUSHED_WITHIN)
    listed = _github(f'/orgs/{ORG}/repos', http, sort='pushed', direction='desc', per_page=100) or []
    names = [r['full_name'] for r in listed if isinstance(r, dict) and not r.get('archived')
             and (r.get('pushed_at') or '') >= since.strftime('%Y-%m-%dT%H:%M:%SZ')]
    return names + [n for n in getattr(settings, 'PR_CARD_REPOS', []) if n not in names]


def pulls(full_name, http, now):
    """A repository's pull requests worth a card: every open one, and those closed in CLOSED_WITHIN days."""
    from .todo import _github
    since = (now - timedelta(days=CLOSED_WITHIN)).strftime('%Y-%m-%dT%H:%M:%SZ')
    listed = _github(f'/repos/{full_name}/pulls', http, state='all', sort='updated', direction='desc',
                     per_page=50) or []
    return [p for p in listed if isinstance(p, dict)
            and (p.get('state') == 'open' or (p.get('closed_at') or '') >= since)]


def write(pull, bot, http):
    owner, repo = pull['base']['repo']['full_name'].split('/', 1)
    text = card(pull, _pushed(owner, repo, pull, http))
    return bot.write(title(owner, repo, pull['number']), text,
                     f"{owner}/{repo}#{pull['number']}, as GitHub has it")


def look(http=None, wiki=None, now=None):
    """Write every card that's due: {title: 'written' | 'same' | 'refused'}."""
    import requests
    from .todo_ticks import Bot
    now = now or datetime.now(timezone.utc)
    http = http or requests.Session()
    bot = Bot(wiki)
    if not bot.login():
        return {}
    done = {}
    for full_name in repositories(http, now):
        for pull in pulls(full_name, http, now):
            owner, repo = full_name.split('/', 1)
            done[title(owner, repo, pull['number'])] = write(pull, bot, http)
    return done


def one(owner, repo, number, http=None, wiki=None):
    """Write one pull request's card now (GitHub's webhook heard of it): the outcome, or None."""
    import requests
    from .todo import _github
    from .todo_ticks import Bot
    http = http or requests.Session()
    pull = _github(f'/repos/{owner}/{repo}/pulls/{number}', http)
    bot = Bot(wiki)
    if not pull or not bot.login():
        return None
    return write(pull, bot, http)


def _background(fn, *args):
    def run():
        from django.db import connection
        try:
            fn(*args)
        except Exception:
            pass  # GitHub, the wiki, or the network: the next look tries again
        finally:
            connection.close()
    threading.Thread(target=run, daemon=True).start()


def nudge():
    """Look, in the background, if it's been LOOK_EVERY seconds and the bot can write."""
    from . import todo_ticks
    if not todo_ticks.enabled() or not cache.add('pr-cards:lock', 1, LOOK_EVERY):
        return False
    _background(look)
    return True


def heard(owner, repo, number):
    """GitHub's webhook told of a pull request: its card, written in the background."""
    from . import todo_ticks
    if todo_ticks.enabled() and number:
        _background(one, owner, repo, number)
