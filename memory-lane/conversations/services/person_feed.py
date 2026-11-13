"""A person's page (Justin, 5 Nov, #pickipedia-style-and-main-page): their last MESSAGES posts in the Moods
and their last CONTRIBS edits on PickiPedia, as one stream, newest first. A picture they put on the wiki
shows as the picture; a sound as a player; a Release they uploaded (its page is made by Blue Railroad
Imports, so it isn't among their edits: its YAML's `uploaded_by` names them) plays here, as it does on
Tidings. The card (services/person_card) is this in brief; this is the whole of it.

Each item is {'kind', 'at', 'block', ...}: a 'message' (id, mood, mood_title, text), an 'edit' (title, url,
comment, made, size), a 'file' (as an edit, with media: {'kind': 'image'|'audio'|'video'|'file', 'src',
'full', 'mime'}) or a 'release' (cid, title, page, poster, player: embeds.release or None).
"""

import hashlib

from django.core.cache import cache
from django.utils.dateparse import parse_datetime

from conversations.models import Message, ThinkingEntity
from conversations.services import embeds, tidings
from conversations.services.mood_view import pickipedia_url, prose, snippet_of
from conversations.services.person_card import wiki_name

MESSAGES = 20
CONTRIBS = 20
RELEASES = 20
TEXT = 240           # characters of a message kept (Justin: "truncated to 240")
LOOK_BACK = 80       # their newest posts looked through for MESSAGES with words
WIKI_KEPT = 300      # seconds the wiki's answers are kept
WIKI_MISS = 60       # ... when it didn't answer
FILE_NS = 6
RELEASE_NS = 3004
HEADERS = {'User-Agent': 'memory-lane (magenta; person page)'}


def _wiki(http, params, timeout=6):
    import requests
    return (http or requests).get(f'{pickipedia_url()}/api.php', timeout=timeout, headers=HEADERS,
                                  params={**params, 'format': 'json', 'formatversion': 2}).json()


def _url(title):
    return f"{pickipedia_url()}/wiki/{title.replace(' ', '_')}"


def _kept(key, make):
    found = cache.get(key)
    if found is not None:
        return found
    try:
        found = make()
    except Exception:  # noqa: BLE001 -- a wiki that doesn't answer is an empty list, not a broken page
        cache.set(key, [], WIKI_MISS)
        return []
    cache.set(key, found, WIKI_KEPT)
    return found


def _media(info):
    """What a File: page holds, from its imageinfo: how to show it."""
    mime = (info.get('mime') or '').lower()
    kind = ('image' if mime.startswith('image/') else 'audio' if mime.startswith('audio/')
            else 'video' if mime.startswith('video/') else 'file')
    return {'kind': kind, 'src': info.get('thumburl') or info.get('url', ''), 'full': info.get('url', ''), 'mime': mime}


def _files(http, titles):
    """{File title: media} for the files among a person's edits, in one ask."""
    if not titles:
        return {}
    pages = _wiki(http, {'action': 'query', 'titles': '|'.join(titles), 'prop': 'imageinfo',
                         'iiprop': 'url|mime|size', 'iiurlwidth': 640}).get('query', {}).get('pages', [])
    found = {}
    for page in pages:
        info = (page.get('imageinfo') or [{}])[0]
        if info.get('url'):
            found[page['title']] = _media(info)
    return found


def _player(cid):
    try:
        return embeds.release(cid)
    except Exception:  # noqa: BLE001
        return None


def contribs(name, http=None):
    """The last CONTRIBS edits `name` made on PickiPedia, newest first, files with their media and a Release
    page with its player."""
    user = wiki_name(name)

    def make():
        rows = _wiki(http, {'action': 'query', 'list': 'usercontribs', 'ucuser': user, 'uclimit': CONTRIBS,
                            'ucprop': 'title|timestamp|comment|ids|sizediff|flags'}).get('query', {}).get('usercontribs', [])
        media = _files(http, [r['title'] for r in rows if r.get('ns') == FILE_NS])
        out = []
        for row in rows:
            title = row.get('title') or ''
            item = {'kind': 'edit', 'title': title, 'url': _url(title), 'at': row.get('timestamp', ''),
                    'comment': (row.get('comment') or '')[:200], 'made': bool(row.get('new')),
                    'size': row.get('sizediff'), 'ns': row.get('ns')}
            if title in media:
                item.update(kind='file', media=media[title], name=title.partition(':')[2])
            elif row.get('ns') == RELEASE_NS and '/' not in title:
                item['cid'] = embeds.normal_cid(title.partition(':')[2].replace(' ', '_'))
            out.append(item)
        return out

    found = _kept('person-contribs:' + hashlib.sha1(user.encode()).hexdigest(), make)
    for item in found:  # players aren't kept with the list: embeds keeps its own
        if item.get('cid'):
            item['player'] = _player(item['cid'])
    return found


def releases(name, http=None):
    """The Releases `name` uploaded (their YAML's uploaded_by), newest first, each with its player."""
    user = wiki_name(name)
    mine = {user.lower(), name.lower(), f'wiki:{user}'.lower()}

    def make():
        return [r for r in tidings.release_scan(limit=50, http=http) if (r.get('by') or '').lower() in mine][:RELEASES]

    found = _kept('person-releases:' + hashlib.sha1(user.encode()).hexdigest(), make)
    out = []
    for r in found:
        player = _player(r['cid'])
        if player is None and r['video']:  # the wiki's list didn't say its type: a video plays from its pin
            base = f"{tidings.RELEASE_GATEWAY}/{r['cid']}"
            player = {'cid': r['cid'], 'kind': 'video', 'src': base, 'hls': f'{base}/master.m3u8', 'poster': r['poster'],
                      'warning': r.get('warning', '')}
        out.append({'kind': 'release', 'cid': r['cid'], 'title': r['title'], 'page': r['page'], 'poster': r['poster'],
                    'video': r['video'], 'at': r['at'], 'player': player})
    return out


def messages(name):
    """Their last MESSAGES posts with words, newest first, each cut to TEXT characters."""
    posts = (Message.objects.filter(sender_id=name, is_sidechain=False, mood__isnull=False)
             .select_related('mood').order_by('-created_at')[:LOOK_BACK])
    out = []
    for post in posts:
        text = snippet_of(prose(post.content).strip())
        if not text or text == '[deleted]':
            continue
        out.append({'kind': 'message', 'id': str(post.id), 'mood': post.mood.slug,
                    'mood_title': post.mood.title or post.mood.slug, 'at': post.created_at,
                    'block': post.eth_blockheight, 'text': text if len(text) <= TEXT else text[:TEXT - 1].rstrip() + '…',
                    'url': f'/moods/{post.mood.slug}/#m-{post.id}'})
        if len(out) == MESSAGES:
            break
    return out


def _when(value):
    if isinstance(value, str):
        return parse_datetime(value)
    return value


def feed(name, http=None):
    """The page: {'name', 'wiki_name', 'page', 'contribs_page', 'profile', 'colour', 'items'}, or None if `name`
    isn't a person here. Items are the three lists as one, newest first; a Release among their edits and
    among their uploads is shown once, as the upload."""
    from conversations.services import wiki_preview
    from conversations.services import settings as knobs
    from conversations.services.eth_blocks import BlockClock
    person = ThinkingEntity.objects.filter(name=name, is_biological_human=True).first()
    if person is None:
        return None
    user = wiki_name(name)
    uploaded = releases(name, http=http)
    cids = {r['cid'] for r in uploaded}
    items = messages(name) + [c for c in contribs(name, http=http) if c.get('cid') not in cids] + uploaded
    for item in items:
        item['at'] = _when(item['at'])
    items = [i for i in items if i['at'] is not None]
    items.sort(key=lambda i: i['at'], reverse=True)
    clock = BlockClock()
    for item in items:
        if not item.get('block'):
            item['block'] = clock.block_at(item['at'].timestamp())
        item['block_s'] = f"{int(item['block']):,}" if item.get('block') else ''
    look = knobs.looks().get(name, {})
    return {'name': name, 'wiki_name': user, 'page': f"{pickipedia_url()}/wiki/User:{user.replace(' ', '_')}",
            'contribs_page': f"{pickipedia_url()}/wiki/Special:Contributions/{user.replace(' ', '_')}",
            'profile': wiki_preview.preview(f'User:{user}', http=http),
            'colour': look.get('colour', ''), 'items': items}
