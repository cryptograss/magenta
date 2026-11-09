"""The tidings pages: /tidings/ and its /api/tidings/ show pictures;
/prompts/ and its /api/prompts/ show prompts. The two live here as one
file because a prompt's answering turn is read from the same posts the
picture scan walks.

What Justin asked for, while building the status page (block ~26,228,235):
  1. Pictures: the last 20 images shared in the Moods, in order, with who
     shared each one, whether it has gone up to PickiPedia, and whether the
     faces service has looked at it.
  2. Prompts: the last 20 prompts issued by each biological human, each with
     the tokens and cost of the turn that answered it.

A prompt is a post that addresses an agent, as the Moods themselves count
it (mood_view.addressed_in): an @mention, or a command that names one.
A very short post (under 80 characters, Justin's "very short") that
addresses an agent counts too -- but the same person's own post right
before it gave it its meaning, so the two are shown together as one
prompt. If that preceding post itself addresses an agent, it is already
a prompt on its own, and both stand alone. A post of 80 characters or
more stands alone, whatever came before it; so does a command
("/compact magent ..."), and a short post whose author said nothing in
that Mood in the PAIR_WITHIN before it.

A prompt's turn is what the agent it names did next: its messages after
that post and before the next person's, as the page draws a turn. Only
the latest person's post before a turn is what it answered, and only if
that post was a prompt naming it; a turn after a post that named no one
(an agent listening to everything) is no prompt's. The tokens and cost are
each message's own usage, counted once per response: one response can be
stored as several rows (thinking, words, a tool call), each repeating its
usage, and repeated usage is one response (mood_view.how_payload's own
rule). The cost is what the harness said it cost (Pi does); otherwise
how_payload's price-list call.

Reading is public, like the rest of the record: pictures and prompts are
already there in the Moods for anyone to see. How far back a Mood is
looked is a bound, not a claim: MAX_PER_MOOD visible human posts, with
up to AGENT_CAP of the agents' messages from the same stretch.
"""

import heapq
from collections import defaultdict
from datetime import timedelta

from conversations.models import FaceLook, FaceName, Media, Message, Mood, ThinkingEntity
from conversations.services.mood_view import (addressed_in, from_wiki_tier, how_payload, pickipedia_url, prose,
                                               snippet_of)

PICTURES = 20
PROMPTS_PER_HUMAN = 20
SHORT_POST = 80   # under this many characters, a post that addresses an agent pairs with the one before it
PAIR_WITHIN = timedelta(minutes=10)  # a short post's nudge is about what its author just said
MAX_PER_MOOD = 300   # how many of a Mood's visible human posts the prompt scan looks at
AGENT_CAP = 3000     # ... and how many of the agents' messages from the same stretch it reads for turns
# What how_payload reads: an agent's message is read for its usage, never its words. And its Mood: read
# through mood.messages, each row is handed its Mood, and a deferred mood_id was one query a row (re, 21 Oct:
# the prompts page took 17 s).
USAGE_FIELDS = ('id', 'sender', 'mood', 'created_at', 'model_backend', 'effort', 'input_tokens', 'output_tokens',
                'cache_read_input_tokens', 'cache_creation_input_tokens', 'cost_usd', 'stop_reason')
# What a reply answers, never looked up: it addresses that message's author only if a person, and a
# prompt is what addresses an agent (mood_view.addressed_in). Two queries a reply, saved.
NOT_ASKED = {'sender': None, 'is_human': False}
MAX_POSTS_WITH_PICTURES = 400  # how many of the newest posts showing pictures the picture scan looks at

IMAGE_MIMES = ('image/png', 'image/jpeg', 'image/gif', 'image/webp')
# image/heic is not listed: a HEIC photo is kept as an original beside the
# JPEG it is shown as (services/media.store), and is not a second picture.


def _text(message):
    return prose(message.content)


def _brief(message, mood_slug, agents):
    """One post, as the page shows it."""
    text = _text(message)
    return {
        'id': str(message.id),
        'mood': mood_slug,
        'at': message.created_at,
        'block': message.eth_blockheight,
        'text': text,
        'snippet': snippet_of(text)[:280],
        'emergents': agents or [],
    }


def _entry_at(entry):
    """When the prompt happened: the short post's time in a pair, its own otherwise."""
    return (entry['trigger'] if entry['kind'] == 'paired' else entry)['at']


def _run_metrics(run):
    """One agent turn's tokens and cost, one response's usage counted once.

    `run` is that agent's messages in a row. A response stored as several
    rows repeats its usage on each; consecutive rows with the identical
    usage are one response. None when a turn carried no measured usage.
    """
    totals = {'in': 0, 'out': 0, 'usd': 0.0, 'priced': False, 'paid': False, 'calls': 0, 'model': ''}
    last_usage = None
    for message in run:
        payload = how_payload(message)
        if 'out' not in payload:
            last_usage = None
            continue
        usage = (payload.get('ctx', 0), payload['out'])
        if usage == last_usage:
            continue
        if not totals['model'] and payload.get('model'):
            totals['model'] = payload['model']
        totals['in'] += usage[0]
        totals['out'] += usage[1]
        if payload.get('usd') is not None:
            totals['usd'] += payload['usd']
            totals['priced'] = True
        totals['paid'] = totals['paid'] or payload.get('paid', False)
        totals['calls'] += 1
        last_usage = usage
    if not totals['calls']:
        return None
    totals['in'], totals['out'] = int(totals['in']), int(totals['out'])
    totals['usd'] = round(totals['usd'], 4)
    return totals


def _k(n):
    """Tokens as the Mood page writes them (moods.html kTokens): 980, 2.1k, 535k, 1.2M."""
    if n >= 1_000_000:
        return f'{n / 1_000_000:.1f}M'
    if n >= 1000:
        return f'{n / 1000:.1f}k' if n < 10000 and n % 1000 else f'{round(n / 1000)}k'
    return str(n)


def _add_turn(entry, agent, messages):
    """The prompt `entry` gets the tokens and cost of `agent`'s turn answering it, if it measured any."""
    metrics = _run_metrics(messages)
    if metrics is not None:
        usd = metrics['usd']
        entry['turns'].append({'emergent': agent, **metrics, 'tokens': f"{_k(metrics['in'])}→{_k(metrics['out'])}",
                               # As the page's usdText; nothing when no call had a price (a model the list lacks).
                               'cost': '' if not metrics['priced'] else '<$0.01' if usd < 0.005 else f'${usd:.2f}'})


def prompt_tidings(per_human=PROMPTS_PER_HUMAN):
    """prompt_scan, kept PROMPTS_KEPT seconds."""
    from django.core.cache import cache
    key = f'tidings:prompts:{per_human}:emergents'  # not a copy kept before the rename's keys
    found = cache.get(key)
    if found is None:
        found = prompt_scan(per_human)
        cache.set(key, found, PROMPTS_KEPT)
    return found


def prompt_scan(per_human=PROMPTS_PER_HUMAN):
    """Every biological human's prompts, newest first, at most `per_human` each.

    One pass over each Mood's visible posts, oldest first, so a short
    post that addresses an agent can look back one post: the same person's
    own post right before it, in that Mood (magent's reading of "the message
    immediately preceding", which Justin let stand), and only if that post
    does not itself address an agent -- then each is shown on its own,
    neither twice. Posts that address nobody are never prompts.

    Agents' messages on the same pass become their turns, for the tokens
    and cost of what answered each prompt (_add_turn).
    """
    humans = list(ThinkingEntity.objects.filter(is_biological_human=True))
    human_names = [human.name for human in humans]
    agents = set(ThinkingEntity.objects.filter(is_biological_human=False)
                 .values_list('name', flat=True))
    grouped = defaultdict(list)

    for mood in Mood.objects.order_by('id'):
        human_rows = (mood.messages.filter(is_sidechain=False, sender_id__in=human_names)
                      .select_related('sender').order_by('-created_at')[:MAX_PER_MOOD])
        human_rows = list(reversed(list(human_rows)))
        if not human_rows:
            continue
        agent_rows = list(mood.messages.filter(is_sidechain=False, sender_id__in=agents,
                                               created_at__gte=human_rows[0].created_at)
                          .only(*USAGE_FIELDS).order_by('created_at')[:AGENT_CAP])
        last = {}          # each human's own previous visible post in this Mood: what pairing looks back to
        entries = []       # this Mood's prompts, oldest first
        answering = None   # the prompt the latest person's post made, if it made one
        answers = defaultdict(list)  # (prompt's index, agent) -> that agent's messages after it

        for message in heapq.merge(human_rows, agent_rows, key=lambda m: m.created_at):
            if message.sender_id in agents:
                if answering is not None and message.sender_id in entries[answering]['emergents']:
                    answers[(answering, message.sender_id)].append(message)
                continue
            answering = None  # a person's post ends every turn, as on the page
            if from_wiki_tier(message):
                # From a PickiPedia device, an @agent is just text: no agent
                # is waking for it, so it is not a prompt (mood_view.activities).
                last[message.sender_id] = message
                continue
            text = _text(message)
            named = [n for n in addressed_in(text, agents, NOT_ASKED, by=message.sender_id)]
            if not named:
                last[message.sender_id] = message
                continue
            previous = last.get(message.sender_id)
            if (previous and len(text) < SHORT_POST and not text.startswith('/')
                    and message.created_at - previous.created_at <= PAIR_WITHIN
                    and not addressed_in(_text(previous), agents, NOT_ASKED, by=previous.sender_id)):
                entry = _brief(previous, mood.slug, named)
                entry.update(kind='paired', trigger=_brief(message, mood.slug, named), by=message.sender_id)
            else:
                entry = _brief(message, mood.slug, named)
                entry.update(kind='mention', by=message.sender_id)
            entry['turns'] = []
            entries.append(entry)
            answering = len(entries) - 1
            last[message.sender_id] = message
        for (index, agent), messages in answers.items():
            _add_turn(entries[index], agent, messages)
        for entry in entries:
            grouped[entry['by']].append(entry)

    out = []
    for human in humans:
        prompts = sorted(grouped[human.name], key=_entry_at, reverse=True)
        out.append({'name': human.name, 'prompts': prompts[:per_human]})
    with_prompts = sorted([h for h in out if h['prompts']],
                          key=lambda h: _entry_at(h['prompts'][0]), reverse=True)
    without_prompts = sorted([h for h in out if not h['prompts']], key=lambda h: h['name'])
    return with_prompts + without_prompts


# Kept a while (Justin, 21 Oct: Tidings took 1.5-2.6 s, Prompts 1.1-1.6 s, live; still 1.4-2.8 s on 26 Oct): the
# picture scan reads every message for "/media/", and the prompts read every Mood. The pictures' copy goes the
# moment what they show changes (pictures_changed: a picture stored or posted, a face looked at or named, an
# upload to PickiPedia, a post edited or deleted) and after PICTURES_KEPT regardless; the prompts' after PROMPTS_KEPT.
PICTURES_KEPT = 300  # seconds
PROMPTS_KEPT = 30


def pictures_changed():
    """What Tidings' pictures show has changed: their kept copy goes, and the next look reads afresh."""
    from django.core.cache import cache
    cache.delete_many([f'tidings:pictures:{limit}' for limit in (PICTURES, PICTURES * 2, 60, 120)])


def picture_tidings(limit=PICTURES):
    """picture_scan, kept until pictures_changed (or PICTURES_KEPT)."""
    from django.core.cache import cache
    key = f'tidings:pictures:{limit}'
    found = cache.get(key)
    if found is None:
        found = picture_scan(limit)
        cache.set(key, found, PICTURES_KEPT)
    return found


def picture_scan(limit=PICTURES):
    """The latest `limit` images, newest first, each with its two marks.

    Only pictures a visible Mood post shows: a picture pasted into a
    terminal session is someone's work, not public, and stays where it is.
    Pushed: a 'wiki-upload' event line in its Mood (services/wiki_upload.py
    records one when a picture goes up). Faces: a FaceLook row, which exists
    once the faces service has been run on it, with how many faces it found
    and how many have been named. Each picture carries a small `preview`
    beside its `url` (views_moods.media_preview), so a gallery of them
    costs thumbnails, not the pictures.
    """
    from conversations.services.media import MEDIA_PATH
    posts = (Message.objects.filter(mood__isnull=False, is_sidechain=False, content__icontains='/media/',
                                    sender__in=ThinkingEntity.objects.all())
             .select_related('sender').order_by('-created_at')[:MAX_POSTS_WITH_PICTURES])
    shown = {}  # sha -> the newest post showing it
    for post in posts:
        for match in MEDIA_PATH.finditer(prose(post.content)):
            shown.setdefault(match.group(1), post)
    media = {row.sha256: row for row in Media.objects.filter(pk__in=list(shown), mime__in=IMAGE_MIMES)
             .select_related('added_by')}
    shas = [sha for sha in shown if sha in media][:limit]  # newest post first
    looked = {look.pk: look for look in FaceLook.objects.filter(pk__in=shas)}
    named = defaultdict(int)
    people = defaultdict(list)  # sha -> whom people here have named in it, left to right
    for media_id, person, box in (FaceName.objects.filter(media_id__in=shas)
                                  .values_list('media_id', 'person', 'box')):
        named[media_id] += 1
        people[media_id].append((box[0] if isinstance(box, list) and box else 0, person))
    uploads = {}  # sha -> the wiki-upload event line, newest per picture (one is all there is)
    for event in Message.objects.filter(source_file='wiki-upload', content__sha__in=shas) \
                                .order_by('created_at'):
        uploads[event.content['sha']] = event

    out = []
    for sha in shas:
        row, post = media[sha], shown[sha]
        look = looked.get(sha)
        if look:
            faces = {'looked': True, 'count': len(look.faces or []),
                     'named': named[sha], 'model': look.model, 'at': look.looked_at}
        else:
            faces = {'looked': False}
        event = uploads.get(sha)
        out.append({
            'sha256': sha,
            'url': row.url,
            'preview': f'{row.url}/preview',
            'mime': row.mime,
            'size': row.size,
            'added_by': row.added_by.name if row.added_by_id else post.sender_id,
            'at': post.created_at,
            'mood': post.mood_slug,
            'message': str(post.id),
            'faces': faces,
            # Whom it shows, as people here named them, each once: their PickiPedia pages.
            'people': [{'name': person, 'page': f'{pickipedia_url()}/wiki/{person.replace(" ", "_")}'}
                       for person in dict.fromkeys(p for _, p in sorted(people[sha]))],
            'shared_by': post.sender_id,  # credited if it goes to PickiPedia from here
            'wiki': (None if event is None else {
                'page': event.content.get('page', ''),
                'file': event.content.get('file', ''),
                'by': event.content.get('by', ''),
                'mood': event.mood_slug,
                'at': event.created_at,
            }),
        })
    return out


# --- releases: what delivery-kid has pinned, beside the pictures (Justin, 2 Nov: "the latest Releases on
# tidings, interspersed with latest uploaded photos") ----------------------------------------------------------
#
# A Release: page is made once, when a release is finalized (by Blue Railroad Imports, for delivery-kid), so
# the wiki's page-creation log is its release date: kept for good, unlike recent changes (90 days). Its YAML,
# read for the lot in one more ask, says what it is and who uploaded it. A track a record was split into is
# the record's, not news of its own, and one record would bury the pictures under its tracks; a release
# marked deleted or unpinned isn't offered. Asked of the wiki at most every RELEASES_KEPT (a new release
# clears it: wiki_feed.refresh_releases); a wiki that doesn't answer in time is no releases, never a broken
# page, and isn't asked again for RELEASES_MISS.

RELEASES = 12
RELEASES_KEPT = 600   # seconds
RELEASES_MISS = 60
RELEASE_GATEWAY = 'https://ipfs.delivery-kid.cryptograss.live/ipfs'
NOT_NEWS = {'track'}


def releases_changed():
    from django.core.cache import cache
    cache.delete('tidings:releases')


def release_tidings(limit=RELEASES, http=None):
    """release_scan, kept RELEASES_KEPT (RELEASES_MISS if the wiki didn't answer)."""
    from django.core.cache import cache
    found = cache.get('tidings:releases')
    if found is None:
        try:
            found = release_scan(limit, http=http)
            cache.set('tidings:releases', found, RELEASES_KEPT)
        except Exception:
            found = []
            cache.set('tidings:releases', found, RELEASES_MISS)
    return found[:limit]


def _cid(title):
    cid = title.partition(':')[2].strip().replace(' ', '_')
    return cid.lower() if cid[:4] == 'Bafy' else cid  # base32 CIDv1: MediaWiki capitalised it


def release_scan(limit=RELEASES, http=None):
    """The newest `limit` releases, newest first: {'cid', 'title', 'kind', 'by', 'at', 'page', 'poster'}."""
    import requests
    import yaml
    from django.utils.dateparse import parse_datetime
    http = http or requests
    api, headers = f'{pickipedia_url()}/api.php', {'User-Agent': 'memory-lane (magenta; tidings)'}
    made = http.get(api, timeout=4, headers=headers, params={
        'action': 'query', 'list': 'logevents', 'letype': 'create', 'lenamespace': 3004,
        'leprop': 'title|timestamp|user', 'lelimit': 50, 'format': 'json'}).json()['query']['logevents']
    made = [e for e in made if (e.get('title') or '').startswith('Release:') and '/' not in e['title']]
    if not made:
        return []
    pages = http.get(api, timeout=4, headers=headers, params={
        'action': 'query', 'prop': 'revisions', 'rvprop': 'content', 'rvslots': 'main', 'format': 'json',
        'formatversion': 2, 'titles': '|'.join(dict.fromkeys(e['title'] for e in made))}).json()['query']['pages']
    yamls = {}
    for page in pages:
        try:
            yamls[page['title']] = yaml.safe_load(page['revisions'][0]['slots']['main']['content'])
        except Exception:
            continue
    out, seen = [], set()
    for event in made:
        title = event['title']
        data = yamls.get(title)
        if title in seen or not isinstance(data, dict):
            continue
        seen.add(title)
        kind = str(data.get('release_type') or '').strip().lower()
        if kind in NOT_NEWS or data.get('delete') or data.get('unpin'):
            continue
        file_type = str(data.get('file_type') or '').lower()
        video = kind in ('video', 'blue-railroad') or file_type == 'video' or file_type.startswith('video/')
        cid = _cid(title)
        out.append({
            'cid': cid,
            'title': str(data.get('title') or '').strip()[:200] or cid,
            'kind': kind or ('video' if video else 'release'),
            'video': video,
            'by': str(data.get('uploaded_by') or '').removeprefix('wiki:')[:80],
            'at': parse_datetime(event.get('timestamp') or ''),
            'page': f"{pickipedia_url()}/wiki/{title.replace(' ', '_')}",
            # Pinned beside the stream since delivery-kid made posters; older ones have none, and say so by hiding it.
            'poster': f'{RELEASE_GATEWAY}/{cid}/poster.jpg' if video else '',
        })
        if len(out) >= limit:
            break
    return out


def to_webvtt(text):
    """Captions as a text track wants them: WebVTT as written, or SRT turned into it (as PickiPedia's own
    player does, ext.pickipediaReleases.videoPlayer.js). '' if there are no cues."""
    import re
    text = str(text or '').lstrip('\ufeff').replace('\r\n', '\n').replace('\r', '\n').strip()
    if '-->' not in text:
        return ''
    if text.startswith('WEBVTT'):
        return text + '\n'
    return 'WEBVTT\n\n' + re.sub(r'(\d{1,2}:\d{2}:\d{2}),(\d{3})', r'\1.\2', text) + '\n'


def release_info(cid, http=None):
    """What a release's page on Tidings shows besides its player, from its Release page on the wiki: its
    title, uploader, blocks, performers, description and commentary (pickipedia#170), its captions
    (Release:<CID>/captions, WebVTT or SRT) and its shape -- an upright phone video is drawn beside what's
    said about it, not above. Kept RELEASES_KEPT; {} if the wiki didn't answer (kept RELEASES_MISS)."""
    from django.core.cache import cache
    key = f'tidings:release:{cid}'
    found = cache.get(key)
    if found is not None:
        return found
    try:
        found = _release_info(cid, http)
        cache.set(key, found, RELEASES_KEPT)
    except Exception:  # noqa: BLE001 -- the page plays without it
        found = {}
        cache.set(key, found, RELEASES_MISS)
    return found


def _release_info(cid, http=None):
    import requests
    import yaml
    http = http or requests
    page = 'Release:' + cid[:1].upper() + cid[1:]
    got = http.get(f'{pickipedia_url()}/api.php', timeout=4, headers={'User-Agent': 'memory-lane (magenta; tidings)'},
                   params={'action': 'query', 'prop': 'revisions', 'rvprop': 'content', 'rvslots': 'main',
                           'format': 'json', 'formatversion': 2, 'titles': f'{page}|{page}/captions'}).json()
    texts = {}
    for found in got['query']['pages']:
        if found.get('revisions'):
            texts[found['title']] = found['revisions'][0]['slots']['main']['content']
    data = yaml.safe_load(texts.get(page) or '') if texts.get(page) else None
    if not isinstance(data, dict):
        return {}
    transcode = data.get('transcode') if isinstance(data.get('transcode'), dict) else {}
    source = transcode.get('source') if isinstance(transcode.get('source'), dict) else {}
    width, height = transcode.get('output_width') or source.get('width'), transcode.get('output_height') or source.get('height')
    commentary = []
    for note in data.get('commentary') or []:
        if isinstance(note, dict) and str(note.get('text') or '').strip():
            commentary.append({'by': str(note.get('by') or '')[:80], 'role': str(note.get('role') or '')[:40],
                               'text': str(note['text']).strip()[:2000]})
    performers = [str(p)[:80] for p in (data.get('performers') or []) if isinstance(p, (str, int))][:20]

    def block(x):
        try:
            return int(x)
        except (TypeError, ValueError):
            return None
    return {
        'title': str(data.get('title') or '').strip()[:200],
        'description': str(data.get('description') or '').strip()[:4000],
        'by': str(data.get('uploaded_by') or '').removeprefix('wiki:')[:80],
        'block': block(data.get('blockheight')),
        'upload_block': block(data.get('upload_blockheight')),
        'upload_block_s': f"{block(data.get('upload_blockheight')):,}" if block(data.get('upload_blockheight')) else '',
        'performers': [{'name': p, 'page': f"{pickipedia_url()}/wiki/{p.replace(' ', '_')}"} for p in performers],
        'commentary': commentary,
        'captions': to_webvtt(texts.get(f'{page}/captions')),
        'captions_page': f"{pickipedia_url()}/wiki/{page}/captions",
        'portrait': bool(width and height and int(height) > int(width)),
        'page': f'{pickipedia_url()}/wiki/{page}',
    }


def stream(pictures, releases):
    """The pictures and releases as one list, newest first: each {'kind': 'picture'|'release', 'at', 'item'}."""
    from datetime import datetime, timezone as tz
    never = datetime.min.replace(tzinfo=tz.utc)
    both = ([{'kind': 'picture', 'at': p['at'], 'item': p} for p in pictures]
            + [{'kind': 'release', 'at': r['at'], 'item': r} for r in releases])
    return sorted(both, key=lambda entry: entry['at'] or never, reverse=True)


def tidings_state(viewer=None, order='top'):
    """What /tidings/ shows: the links promoted from the Moods (services/tiding_links.py), then the
    pictures and the latest releases, which the page shows as one stream (`stream`). (Each picture's
    `preview` is its small form for the gallery; see views_moods.media_preview.)
    `viewer` is who's looking: each link says whether they voted for it. `order`: top (pinned, then ranked) or new."""
    from conversations.services import tiding_links
    # Each release says whether it's on Tidings, and where it's talked about (tiding_links.promote_release).
    on = tiding_links.released()
    releases = [{**release, 'tiding': on.get(release['cid'], '')} for release in release_tidings()]
    return {'links': tiding_links.listed(viewer=viewer, order=order), 'pictures': picture_tidings(),
            'releases': releases}


def prompts_state():
    """What /prompts/ shows: each human's prompts, with their Wiki names
    joined on so the page can write people as the wiki knows them."""
    from conversations.services import wiki_auth
    state = {'prompts': prompt_tidings()}
    wiki = wiki_auth.names()
    for human in state['prompts']:
        human['wiki_name'] = wiki.get(human['name'], '')
    return state
