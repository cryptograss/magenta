"""The servers the Moods run on: whether each answers, and when it's being redeployed.

The Moods show four dots -- hunter, maybelle, delivery-kid, pickipedia --
pulsing while one is redeployed. Whether a server answers is checked from
here, at most once a minute. Redeploys are told by the deploy scripts on
maybelle (maybelle-config's report-deploy.sh), which post to /api/deploys/
with a key from the vault; each start, finish or failure is announced in
the Moods it concerns -- hunter's and maybelle's in all of them, the others
in their own -- as a line in the thread, kept in the record.
"""

import re
import socket
import time
import uuid

from django.core.cache import cache

SERVERS = [
    {'name': 'hunter', 'check': ('tcp', 'hunter.cryptograss.live', 443), 'moods': '*',
     'about': 'the containers: people, Moods, the runners', 'page': 'Cryptograss:Hunter'},
    {'name': 'maybelle', 'check': ('self',), 'moods': '*',
     'about': 'the record (memory-lane), the memory server, the wiki builds', 'page': 'Cryptograss:Maybelle'},
    # Every server's redeploys are told in every Mood (Justin, 2026-10-05): a
    # list of slugs here would tell only those Moods, by any name they've had.
    {'name': 'delivery-kid', 'check': ('http', 'https://delivery-kid.cryptograss.live/health'),
     'moods': '*', 'about': 'pinning and delivery', 'page': 'Cryptograss:Delivery-kid'},
    {'name': 'pickipedia', 'check': ('http', 'https://pickipedia.xyz/api.php?action=query&meta=siteinfo&format=json'),
     'moods': '*', 'about': 'the wiki', 'page': ''},  # it is the wiki: its front page
]
CARD_FOR = 6 * 3600       # seconds a server's PickiPedia card (art, role) is kept
_INFOBOX_ART = re.compile(r'\|\s*image\s*=\s*<pre[^>]*>\n?(.*?)</pre>', re.S)
# Its portrait drawn for a dark ground (light marks where the photo is light): beside `image`.
_INFOBOX_ART_DARK = re.compile(r'\|\s*image_dark\s*=\s*<pre[^>]*>\n?(.*?)</pre>', re.S)
_INFOBOX_ROLE = re.compile(r'\|\s*role\s*=\s*([^\n|]+)')
NAMES = [s['name'] for s in SERVERS]
STATES = ('started', 'finished', 'failed')
SOURCE = 'deploy'
CHECK_EVERY = 60          # seconds between looks at a server
DEPLOY_PATIENCE = 90 * 60  # a redeploy unheard of for this long is presumed over


def probe(check, timeout=4):
    """(answers, milliseconds) for one server's check."""
    kind = check[0]
    start = time.monotonic()
    try:
        if kind == 'self':
            return True, 0
        if kind == 'tcp':
            with socket.create_connection((check[1], check[2]), timeout=timeout):
                pass
        elif kind == 'http':
            import requests
            response = requests.get(check[1], timeout=timeout)
            if response.status_code >= 500:
                return False, round((time.monotonic() - start) * 1000)
        return True, round((time.monotonic() - start) * 1000)
    except Exception:
        return False, round((time.monotonic() - start) * 1000)


def page_url(name):
    """A server's PickiPedia page (the wiki's front page, for the wiki itself); '' for one not listed."""
    from conversations.services.mood_view import pickipedia_url
    server = next((s for s in SERVERS if s['name'] == name), None)
    if server is None:
        return ''
    title = server.get('page') or ''
    return f"{pickipedia_url()}/wiki/{title.replace(' ', '_')}" if title else f'{pickipedia_url()}/'


def wiki_card(server):
    """{'url', 'role', 'art', 'art_dark'} from a server's PickiPedia page: the ASCII art
    and role in its infobox, so the dots' popover shows what the wiki does. art_dark is
    the portrait drawn for dark mode (`image_dark`), '' where the page has none.
    Kept six hours; a wiki that doesn't answer leaves just the link."""
    from conversations.services.mood_view import pickipedia_url
    base, title = pickipedia_url(), server.get('page') or ''
    url = f"{base}/wiki/{title.replace(' ', '_')}" if title else f'{base}/'
    if not title:
        return {'url': url, 'role': '', 'art': '', 'art_dark': ''}
    key = f'server-card2:{title}'  # 2: with art_dark
    card = cache.get(key)
    if card is None:
        card = {'url': url, 'role': '', 'art': '', 'art_dark': ''}
        try:
            import requests
            page = requests.get(f'{base}/api.php', timeout=5, headers={'User-Agent': 'memory-lane (magenta)'},
                                params={'action': 'query', 'titles': title, 'prop': 'revisions', 'rvprop': 'content',
                                        'rvslots': 'main', 'format': 'json', 'formatversion': 2}).json()
            text = page['query']['pages'][0]['revisions'][0]['slots']['main']['content']
            art, role = _INFOBOX_ART.search(text), _INFOBOX_ROLE.search(text)
            card['art'] = art.group(1).strip('\n').rstrip() if art else ''
            dark = _INFOBOX_ART_DARK.search(text)
            card['art_dark'] = dark.group(1).strip('\n').rstrip() if dark else ''
            card['role'] = role.group(1).strip() if role else ''
            cache.set(key, card, CARD_FOR)
        except Exception:
            cache.set(key, card, 600)  # try again in ten minutes
    return card


def _deploy_key(name):
    return f'server-deploy:{name}'


DEPLOY_KEPT = 600  # record_deploy busts it; the expiry is only a net


def last_deploy(name):
    """The newest deploy event told for a server, as stored, or None.

    Kept until record_deploy tells another (the only writer): the query walks every
    deploy row, and /api/servers asks it for four servers every minute, from every page."""
    from conversations.models import Message
    kept = cache.get(_deploy_key(name))
    if kept is not None:
        return kept or None  # {} is "none told"
    row = (Message.objects.filter(source_file=SOURCE, content__server=name)
           .order_by('-created_at').values('content', 'created_at').first())
    found = None if row is None else {
        **row['content'], 'at': row['created_at'].isoformat(), 'at_ts': row['created_at'].timestamp()}
    cache.set(_deploy_key(name), found or {}, DEPLOY_KEPT)
    return found


PROBING_KEY = 'server-check:probing'


def _look(server, now):
    up, ms = probe(server['check'])
    checked = {'up': up, 'ms': ms, 'checked_at': now}
    # Kept with no expiry: past CHECK_EVERY it's still shown while it's looked at again.
    cache.set(f"server-check:{server['name']}", checked, None)
    return checked


def _look_at(stale, now):
    """Probe these servers at once, not one after another: a server that doesn't answer costs its
    timeout once, not once for every server behind it."""
    if not stale:
        return {}
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=len(stale)) as pool:
        return dict(zip((s['name'] for s in stale), pool.map(lambda s: _look(s, now), stale)))


def _look_again(stale, now):
    try:
        _look_at(stale, now)
    finally:
        cache.delete(PROBING_KEY)


def status(now=None):
    """Every server: whether it answers, and its newest deploy event.

    A server never checked is probed now (all of them at once); one checked over a minute ago
    shows its last answer, with its checked_at, while a thread looks again -- the page asks every
    minute, and the one who asks just after the minute shouldn't wait on four servers."""
    from conversations.services.mood_cache import spawn
    now = now or time.time()
    checks = {s['name']: cache.get(f"server-check:{s['name']}") for s in SERVERS}
    checks.update(_look_at([s for s in SERVERS if checks[s['name']] is None], now))
    stale = [s for s in SERVERS if now - checks[s['name']]['checked_at'] >= CHECK_EVERY]
    if stale and cache.add(PROBING_KEY, True, 30):
        spawn(_look_again, stale, now)
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=len(SERVERS)) as pool:
        cards = list(pool.map(wiki_card, SERVERS))  # each kept six hours; a miss asks the wiki
    out = []
    for server, card in zip(SERVERS, cards):
        deploy = last_deploy(server['name'])
        deploying = bool(deploy and deploy.get('state') == 'started' and now - deploy['at_ts'] < DEPLOY_PATIENCE)
        out.append({'name': server['name'], 'about': server['about'], **card, **checks[server['name']],
                    'deploying': deploying,
                    'deploy': {k: v for k, v in (deploy or {}).items() if k != 'at_ts'} or None})
    return out


def moods_for(name):
    from conversations.models import Mood
    server = next(s for s in SERVERS if s['name'] == name)
    if server['moods'] == '*':
        return list(Mood.objects.all())
    # By any name the Mood has gone by: renaming it mustn't stop its notices.
    return [m for m in (Mood.by_slug(slug) for slug in server['moods']) if m is not None]


_REPO = re.compile(r'[A-Za-z0-9._-]{1,60}')
_COMMIT = re.compile(r'[0-9a-f]{7,40}')


def deployed_commits(given):
    """{repo: commit} from a deploy report's "commits", what its playbook put on the server (maybelle-config's
    report-deploy.sh): only names and hashes that look like ones, ten at most; {} for anything else."""
    if not isinstance(given, dict):
        return {}
    found = {}
    for repo, commit in list(given.items())[:10]:
        if isinstance(repo, str) and isinstance(commit, str) and _REPO.fullmatch(repo) and _COMMIT.fullmatch(commit):
            found[repo] = commit
    return found


def record_deploy(name, state, commit='', by='', note='', commits=None):
    """Announce a deploy event in the Moods it concerns; returns how many. `commits`, {repo: commit}, is what the
    deploy put on the server, kept with the event: the ramp shows a merge as live there once it's in that commit."""
    from conversations.models import ConversationParticipant, Message
    took = None
    if state in ('finished', 'failed'):
        started = last_deploy(name)
        if started and started.get('state') == 'started':
            took = round(time.time() - started['at_ts'])
    content = {'type': 'deploy', 'server': name, 'state': state, 'commit': commit[:40], 'by': by[:40],
               'note': note[:200], 'took': took, **({'commits': commits} if commits else {})}
    system, _ = ConversationParticipant.objects.get_or_create(name='system', defaults={'participant_type': 'system'})
    moods = moods_for(name)
    stamp = int(time.time() * 1000)
    Message.objects.bulk_create([
        Message(id=uuid.uuid4(), sender=system, mood=mood, content=content, timestamp=stamp, source_file=SOURCE)
        for mood in moods])
    cache.delete(f'server-check:{name}')  # look again: it may be back, or gone
    cache.delete(_deploy_key(name))
    if state == 'finished':
        from . import ramp
        ramp.changed()  # the ramp's "live on" column, now: not at the next rebuild
    return len(moods)
