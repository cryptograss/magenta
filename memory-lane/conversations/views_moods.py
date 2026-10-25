"""Read-only public view of Moods.

Served by memory-lane because memory-lane is where the record lives. This
is a projection: nothing here writes, and nothing a reader does can change
the conversation.
"""

import functools
import json
import hashlib
import re

from django.conf import settings
from django.db.models import Max
from django.http import Http404, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from django.core.exceptions import ValidationError
from django.views.decorators.http import require_GET, require_POST

from .models import Message, Mood, MoodSession, ThinkingEntity, mood_slug
from .services import delivery_kid, faces, push, servers, wiki_auth, wiki_feed, wiki_ledger, wiki_upload
from .services import agent_commands, mood_auth
from .services.mood_view import agent_names, bundle_marks, turn_tools, turn_wakes  # which tools and instructions a turn had
from .services.mood_view import (
    from_wiki_tier,
    MACHINERY_SENDERS, how_payload, activity, activities, background_tasks, is_summary, is_wrapper, known_names, mentions_in, addressed_in, mood_payload,
    prose, render_html, step_detail, step_images, step_payload, timeline, turn_payload, turns, wiki_title,
)


@ensure_csrf_cookie
@require_GET
def moods_page(request, slug=None, section=''):
    """The one page: the Moods, and tidings, prompts, status and settings beside them (`section`: which
    shows first). Each section is its own page, loaded once into it (?embed=1) and kept, so going back
    and forth loads nothing again (Justin, 21 Oct)."""
    if slug is not None:
        found = Mood.by_slug_or_404(slug)
        if found.slug != slug:  # renamed since: its page now has the new name
            from django.shortcuts import redirect
            return redirect(f'/moods/{found.slug}/', permanent=False)
    device = mood_auth.device_for(request)
    return render(request, 'conversations/moods.html', {
        'initial_slug': slug or '',
        'initial_section': section,
        # A preview can pretend to be someone, to show the composer; its
        # database is read-only, so nothing it sends is kept.
        'viewer': device.entity_id if device else getattr(settings, 'PREVIEW_VIEWER', ''),
        'preview_label': getattr(settings, 'PREVIEW_LABEL', ''),
        # [[ in the composer suggests PickiPedia titles, asked of the wiki itself.
        'pickipedia_url': getattr(settings, 'PICKIPEDIA_URL', 'https://pickipedia.xyz').rstrip('/'),
        # Voice memos and reading aloud, if an ElevenLabs key is set (services/voice.py).
        'voice_enabled': bool(getattr(settings, 'ELEVENLABS_API_KEY', '')),
        # 'key' or 'wiki' (services/wiki_auth.py): a wiki sign-in chats and mentions people only.
        'viewer_tier': device.tier if device else '',
        'wiki_signin': wiki_auth.enabled(),
        # "→ PickiPedia" on a picture sent here, if uploading as its sharer is set up (services/wiki_upload.py).
        'wiki_upload': wiki_upload.enabled(),
        # Videos to delivery-kid, then a ReleaseDraft page, from the composer (services/delivery_kid.py).
        'dk_upload': delivery_kid.enabled(),
        # "Who's in it?" on a photo sent here, if the faces service is set up (services/faces.py).
        'faces_on': faces.enabled(),
        # An admin signed in with their SSH key may delete anyone's message (services/retract.py).
        'viewer_admin': bool(device) and device.tier == 'key' and device.entity_id in getattr(settings, 'MOOD_ADMINS', ()),
        # PickiPedia names, shown for the names here (from hunter's inventory).
        'wiki_names': wiki_auth.names(),
        # Notifications with magenta closed (services/push.py): what browsers subscribe with. '' if off.
        'push_key': push_key(),
        'page_version': page_version(),
        # The slash commands, for the composer to offer as one is typed (services/agent_commands).
        'slash_commands': json.dumps(agent_commands.suggestions()),
    })


def push_key():
    """push.public_key(), or '' if anything about push is amiss: the page loads regardless."""
    try:
        return push.public_key()
    except Exception:
        import logging
        logging.getLogger(__name__).exception('push key unusable; push off for this page')
        return ''


@functools.cache
def page_version():
    """This page's code as served now, and the code that renders what it shows (a message's HTML is
    made on the server). A page left open compares it (api/servers) and offers a reload; a browser's
    kept copy of a Mood made under another version is dropped, not drawn (a fixed player would
    otherwise stay broken in it)."""
    from django.template.loader import get_template
    from .services import embeds, mood_view
    digest = hashlib.sha256()
    for path in (get_template('conversations/moods.html').origin.name,
                 get_template('conversations/name_art_css.html').origin.name, mood_view.__file__, embeds.__file__):
        with open(path, 'rb') as f:
            digest.update(f.read())
    return digest.hexdigest()[:12]


@require_GET
def api_moods(request):
    """Every Mood, most recently active first; archived ones flagged (the page lists them apart)."""
    from .services import settings as knobs
    archived, pinned = knobs.archived_slugs(), knobs.mood_flagged('pinned')
    moods = list(Mood.objects.all())
    payloads = [{**mood_payload(m), 'archived': m.slug in archived, 'pinned': m.slug in pinned} for m in moods]
    # A Mood nobody has spoken in yet ranks by when it was started: a new one first.
    started = {m.slug: m.created_at.isoformat() for m in moods}
    payloads.sort(key=lambda p: p['last_at'] or started[p['slug']], reverse=True)
    payloads.sort(key=lambda p: not p['pinned'])  # pinned first, each part still newest first
    people = ThinkingEntity.objects.order_by('name')
    look = knobs.looks()
    from .services.mood_view import GROUPS, group_members
    return JsonResponse({'moods': payloads, 'people': [
        {'name': p.name, 'is_human': p.is_biological_human,
         **(look[p.name] if p.is_biological_human and p.name in look else {})}
        for p in people],
        # Tags for a group (@core): what each is, and who it mentions now.
        'groups': [{'name': g, 'is': what, 'members': group_members(g)} for g, what in GROUPS.items()]})


@require_GET
def api_tokens(request):
    """Tokens to show beside people's names: {"blue_railroad": {name: count}}
    (services/tokens.py). Its own request, so a slow wiki never holds up the page."""
    from .services import tokens
    return JsonResponse({'blue_railroad': tokens.blue_railroads()})


def _watchers(request, mood):
    from .services import watchers
    device = mood_auth.device_for(request)
    if device is None:
        watchers.seen(request, mood.slug)
    else:
        watchers.signed_in(request, device.entity_id)
    return {'watchers': watchers.now_watching(mood.slug, admin=device is not None and device.tier == 'key')}


def note_agent(msg, _agents=None):
    """The agent whose session a harness note is in: the first agent to speak in it; None if none has."""
    if not msg.session_id:
        return None
    agents = ThinkingEntity.objects.filter(is_biological_human=False).values_list('name', flat=True)
    first = (Message.objects.filter(session_id=msg.session_id, sender_id__in=list(agents))
             .order_by('created_at').values_list('sender_id', flat=True).first())
    return first


@require_GET
def api_mood_turns(request, slug):
    """The readable conversation in one Mood.

    Pass ?after=<message id> to get only what arrived since -- the polling
    contract. An unknown `after` is treated as "everything", so a client
    that has fallen out of sync recovers rather than stalls.
    """
    mood = Mood.by_slug_or_404(slug)
    if mood.slug in wiki_feed.feed_moods():
        wiki_feed.nudge()  # what's new on PickiPedia, as lines here (at most once a minute, in the background)

    after = before = None
    if request.GET.get('after'):
        after = _message_or_none(request.GET['after'])
    if request.GET.get('before'):
        before = _message_or_none(request.GET['before'])
    # ?from=<id>: that message and everything since -- what a runner reads
    # when a post links a message to read from.
    start = _message_or_none(request.GET['from']) if request.GET.get('from') else None
    if request.GET.get('from') and (start is None or start.mood_id != mood.id):
        # Never this Mood read from another's moment: the message must be here.
        return JsonResponse({'error': 'no such message in this Mood',
                             'mood': start.mood_slug if start else None}, status=404)
    # A first load, or a page back, is the newest PAGE items; a poll is
    # everything since.
    limit = None if after is not None or start is not None else PAGE
    if request.GET.get('limit', '').isdigit():  # e.g. the runner wanting recent context only
        limit = max(1, min(int(request.GET['limit']), PAGE))

    names = known_names()
    turns_out, step_msgs, quiet_out, thoughts_out, compactions_out, events_out = [], [], [], [], [], []
    agents_said = []  # agents' messages: which tools each turn had (👀 or 🔨 by its name)
    for kind, msg, text in timeline(mood, after=after, before=before, limit=limit, start=start):
        if kind in ('turn', 'thought', 'quiet', 'step'):
            agents_said.append(msg)
        if kind == 'turn':
            turns_out.append(turn_payload(msg, text, names))
        elif kind == 'thought':
            thoughts_out.append({**how_payload(msg), 'id': str(msg.id), 'sender': msg.sender_id,
                                 'created_at': msg.created_at.isoformat(), 'text': text})
        elif kind == 'note':
            # The harness's note to an agent: in that agent's turn, beside its thinking, marked as the harness's.
            agent = note_agent(msg)
            if agent:
                thoughts_out.append({'id': str(msg.id), 'sender': agent, 'harness': True,
                                     'created_at': msg.created_at.isoformat(), 'text': text})
        elif kind == 'event':
            events_out.append({'id': str(msg.id), 'created_at': msg.created_at.isoformat(),
                               **{k: text.get(k) for k in ('type', 'server', 'state', 'commit', 'by', 'note', 'took', 'agent',
                                                   'kind', 'title', 'user', 'comment', 'delta', 'revid', 'at',
                                                   'who', 'tier', 'label', 'device', 'devices', 'file', 'page', 'sha',
                                                   'message', 'already', 'author', 'for', 'from_mood', 'to_mood', 'about', 'context',
                                                   'pulled', 'pull', 'commit_url', 'cid', 'draft', 'as', 'key',
                                                   'tiding', 'comment_id', 'changes', 'task', 'revision', 'reason', 'tools')},
                               # A deploy's server, linked to its page; a release, with its player.
                               **({'server_url': servers.page_url(text.get('server'))} if text.get('type') == 'deploy' else {}),
                               **({'html': render_html(f"[[Release:{text['cid']}]]")}
                                  if text.get('type') == 'release' and text.get('cid') else {}),
                               # A handoff's context, as a message is shown (services/handoff.py).
                               **({'html': render_html(text.get('context') or '')} if text.get('type') == 'handoff' else {}),
                               # A task's prompt, filled in, as it was sent (services/tasks.py).
                               **({'html': render_html(text.get('text') or '')} if text.get('type') == 'task' else {})})
        elif kind == 'compaction':
            compactions_out.append({'id': str(msg.id), 'session_id': str(msg.session_id or ''),
                                    'created_at': msg.created_at.isoformat(), 'html': render_html(text)})
        elif kind == 'quiet':
            # With what it cost: a silent reply can read a whole session to say nothing.
            quiet_out.append({**how_payload(msg), 'id': str(msg.id), 'sender': msg.sender_id,
                              'created_at': msg.created_at.isoformat(), **text})
        else:
            step_msgs.append(msg)
    images = step_images(step_msgs)
    steps_out = [{**step_payload(m), 'images': images.get(str(m.id), [])} for m in step_msgs]
    agent_set = agent_names()
    wakes = turn_wakes([m for m in agents_said if m.sender_id in agent_set])
    # Which instructions each turn was given, and whether they were new: shown by its cache mark (Justin, 25 Oct).
    marks = bundle_marks([w['bundle'] for w in wakes.values()])
    for p in turns_out + steps_out + quiet_out + thoughts_out:
        said = wakes.get(p['id'])
        if said and said['tools']:
            p['tools'] = said['tools']
        if said and said['bundle'] in marks:
            p['bundle'] = marks[said['bundle']]
    first = min([i['created_at'] for i in turns_out + steps_out + quiet_out + thoughts_out + compactions_out
                 + events_out], default=None)
    agents = agents_in(mood)
    # Edited or deleted (services/retract.py): shown as they stand now.
    from django.utils import timezone
    from .models import MessageChange
    from .services import retract
    changed = {str(c.message_id): c for c in MessageChange.objects.filter(
        mood=mood, message_id__in=[t['id'] for t in turns_out])}
    turns_out = [retract.mark(t, changed.get(t['id'])) for t in turns_out]
    changes = []
    since = parse_datetime(request.GET.get('changes_since') or '') if request.GET.get('changes_since') else None
    if since is not None:  # what changed since the page last asked, as it stands now
        for c in retract.changes_since(mood, since):
            msg = Message.objects.filter(id=c['id']).select_related('sender').first()
            text = prose(msg.content) if msg else ''
            changes.append({**c, 'turn': retract.mark(turn_payload(msg, text, names), retract.change_of(msg))
                            if msg and text else None})
    from .services import reactions
    return JsonResponse({
        'mood': mood_payload(mood),
        # Every message here that has reactions, as they now stand (services/reactions.py).
        'reactions': reactions.in_mood(mood),
        # What was edited or deleted since ?changes_since=, as it stands now; and the time to ask from next.
        'changes': changes,
        'now': timezone.now().isoformat(),
        # Changes when a to-do list may have (a merge GitHub told of, a wiki edit): ask for it again.
        'todo_stamp': todo_stamp(),
        # Prose only: the poller reads an agent turn here as an answer, so a
        # tool call must never appear in this list.
        'turns': turns_out,
        'steps': steps_out,
        # Choices not to speak, shown as dots. Not turns: they answer nothing.
        'quiet': quiet_out,
        # What the agent thought along the way, where the harness kept it.
        'thoughts': thoughts_out,
        # Where a session's context was compacted, and the summary it went on from.
        'compactions': compactions_out,
        # Things that happened around the conversation: a server redeployed.
        'events': events_out,
        'has_earlier': bool(limit) and first is not None and mood.messages.filter(
            is_sidechain=False, created_at__lt=first).exists(),
        'activity': activity(mood),
        # Every agent at work here, each with its own line on the page.
        'activities': activities(mood),
        # Who reads without signing in: how many, to everyone; their addresses, to an SSH-key sign-in.
        **_watchers(request, mood),
        # What an agent started in the background here and is still running.
        'tasks': background_tasks(mood),
        # Whether each agent is listening here (the hush menu in the head).
        'listening': {name: a['listening'] for name, a in agents.items()},
        # Each agent here: listening, model, effort, and how full its context is.
        'agents': agents,
        'scram': settings_scram(),
        'typing': typing_in(mood.slug),
    })


PAGE = 400


def todo_stamp():
    from .services import todo
    return todo.stamp()


def agents_in(mood):
    """name -> {listening, model, effort, context} for each agent, in this Mood."""
    from .services import settings as knobs
    from .services.mood_view import context_in
    from .models import Setting
    agents = ThinkingEntity.objects.filter(is_biological_human=False).values_list('name', flat=True)
    shown = ('listening', 'model', 'mention_effort', 'ultracode', 'catch_up_tokens', 'discretion', 'verbosity')
    rows = list(Setting.objects.filter(key__in=shown))
    from .services import agent_settings
    every = agent_settings._rows()
    # Which harness reported what it can run (the menu fetches the list itself, from api_agent_models).
    from .models import HarnessModels
    catalogs = dict(HarnessModels.objects.values_list('agent_id', 'harness'))
    out = {}
    for name in agents:
        resolved = knobs.resolve(mood.slug, name)
        out[name] = {'listening': resolved['listening'], 'model': resolved['model'],
                     'effort': resolved['mention_effort'], 'ultracode': bool(resolved['ultracode']),
                     'reads': resolved['catch_up_tokens'],
                     'discretion': resolved['discretion'], 'verbosity': resolved['verbosity'],
                     # Who set each as it stands, and when: the page says so, and flashes one that just changed.
                     'set_by': knobs.set_by(mood.slug, name, shown, rows=rows),
                     'context': context_in(mood, name),
                     'catalog': catalogs.get(name)}
        # What's changed in its settings here since its last word: told at its next wake (agent_settings).
        changes, by, note = agent_settings.changed_since(mood.slug, name, rows=every)
        if changes:
            out[name]['changed'] = {'changes': changes, 'by': by, 'note': note}
    return out


def settings_scram():
    from .services import settings as knobs
    return knobs.scram()


def _message_or_none(raw):
    try:
        return Message.objects.filter(id=raw).first()
    except (ValueError, ValidationError):
        return None


@require_GET
def own_clip(request, clip):
    """One of our own clips' links (/clips/<id>, made with `magenta.sh clips save`): its video."""
    from .models import YarnClip
    found = YarnClip.objects.select_related('media').filter(clip=clip, made_by__isnull=False).first()
    if found is None:
        raise Http404('no such clip')
    return HttpResponseRedirect(found.media.url)


def media_file(request, sha256, ext):
    """A stored picture, sound or clip. Immutable by construction: the name is its hash.
    A sound or a clip answers a byte range (Range: bytes=a-b), as Safari insists on for media."""
    from django.http import HttpResponse
    from .models import Media

    item = Media.objects.filter(sha256=sha256).first()
    if item is None or Media.EXTENSIONS.get(item.mime) != ext:
        raise Http404('no such image')
    data = bytes(item.data)
    wanted = re.match(r'^bytes=(\d*)-(\d*)$', request.headers.get('Range', '').strip())
    if wanted and item.mime.startswith(('video/', 'audio/')) and (wanted[1] or wanted[2]):
        if wanted[1]:
            start, end = int(wanted[1]), int(wanted[2]) if wanted[2] else len(data) - 1
        else:  # bytes=-N: the last N
            start, end = max(0, len(data) - int(wanted[2])), len(data) - 1
        if start >= len(data) or start > end:
            response = HttpResponse(status=416)
            response['Content-Range'] = f'bytes */{len(data)}'
            return response
        end = min(end, len(data) - 1)
        response = HttpResponse(data[start:end + 1], status=206, content_type=item.mime)
        response['Content-Range'] = f'bytes {start}-{end}/{len(data)}'
    else:
        response = HttpResponse(data, content_type=item.mime)
    if item.mime.startswith(('video/', 'audio/')):
        response['Accept-Ranges'] = 'bytes'
    response['Cache-Control'] = 'public, max-age=31536000, immutable'
    response['X-Content-Type-Options'] = 'nosniff'
    response['Content-Security-Policy'] = "default-src 'none'; sandbox"
    if item.mime == 'image/heic':  # an original kept whole: most browsers can't show it, so it's a download
        response['Content-Disposition'] = f'attachment; filename="{sha256[:12]}.heic"'
    return response


PREVIEW_EDGE = 288  # a tidings thumbnail's longest side, in pixels
PREVIEW_CACHE_SECONDS = 7 * 86400  # kept made here; a browser keeps it for good (named by its hash, it never changes)


@require_GET
def media_preview(request, sha256, ext):
    """A small picture for a grid of thumbs: the stored one, largest side
    PREVIEW_EDGE, a JPEG where it has no transparency and a PNG where it
    does. Generated once and cached; a picture pasted into a terminal and
    never posted is served to nobody who knows the hash anyway, but it is
    not linked from any page."""
    from django.core.cache import cache
    from django.http import HttpResponse

    from .models import Media

    item = Media.objects.filter(sha256=sha256).first()
    if (item is None or Media.EXTENSIONS.get(item.mime) != ext
            or item.mime not in ('image/png', 'image/jpeg', 'image/gif', 'image/webp')):
        raise Http404('no such image')
    cached = cache.get(f'media-preview:{sha256}')
    if cached is None:
        cached = _preview_of(item)
        cache.set(f'media-preview:{sha256}', cached, PREVIEW_CACHE_SECONDS)
    body, mime = cached
    response = HttpResponse(body, content_type=mime)
    response['Cache-Control'] = 'public, max-age=31536000, immutable'
    response['X-Content-Type-Options'] = 'nosniff'
    response['Content-Security-Policy'] = "default-src 'none'; sandbox"
    return response


def _preview_of(item):
    """(bytes, mime): the picture made small for media_preview."""
    import io
    try:
        from PIL import Image, ImageOps
        with Image.open(io.BytesIO(bytes(item.data))) as opened:
            if getattr(opened, 'is_animated', False):
                opened.seek(0)
            picture = ImageOps.exif_transpose(opened)
            picture.thumbnail((PREVIEW_EDGE, PREVIEW_EDGE))
            out = io.BytesIO()
            if picture.mode in ('RGBA', 'LA', 'P'):
                picture.convert('RGBA').save(out, 'PNG', optimize=True)
                mime = 'image/png'
            else:
                picture.convert('RGB').save(out, 'JPEG', quality=78, optimize=True)
                mime = 'image/jpeg'
            return out.getvalue(), mime
    except Exception:
        raise Http404('no such image')


def _in_a_mood(message_id):
    """A message said in a Mood (not a helper's line), by its id; None if there's none."""
    from .views_auth import _uuid_or_none
    return (Message.objects.filter(id=_uuid_or_none(message_id), mood__isnull=False, is_sidechain=False)
            .select_related('mood').first())


@require_GET
def front_page(request):
    """magenta's front page (services/front.py): anyone may read it, signed in or not."""
    import random
    from .services import front
    slogans = front.slogans()
    here = request.build_absolute_uri('/')
    return render(request, 'conversations/front.html', {'front': front.state(), 'slogans': slogans,
                                                        'slogan': random.choice(slogans),
                                                        'here': here, 'qr': front.qr_svg(here)})


@require_GET
def message_page(request, message_id):
    """/messages/<id>: a message's own address, wherever it lives. A Mood can be renamed, which moves
    its page; its messages' addresses stay (Justin, 20 Oct). Sent on to it in its Mood, as it is now."""
    from django.shortcuts import redirect
    message = _in_a_mood(message_id)
    if message is None:
        raise Http404('no such message in any Mood')
    return redirect(f'/moods/{message.mood.slug}/#m-{message.id}')


@require_GET
def api_message_wiki(request, message_id):
    """{"wikitext"}: the message as a card for PickiPedia, {{Magenta message|...}} with its words in it
    (services/wiki_embed.py). ?chars=N or all: how many characters the card shows (else the template's 280)."""
    from .models import MessageChange
    from .services import wiki_embed
    message = _in_a_mood(message_id)
    if message is None or MessageChange.objects.filter(message_id=message.id, kind='deleted').exists():
        return JsonResponse({'error': 'no such message in any Mood'}, status=404)
    chars = request.GET.get('chars') or None
    if chars is not None and chars != 'all' and not (chars.isdigit() and 0 < int(chars) <= 100000):
        return JsonResponse({'error': 'chars: a number, or all'}, status=400)
    return JsonResponse({'wikitext': wiki_embed.template_call(message, chars=chars)})


@require_GET
def api_message(request, message_id):
    """{"id", "mood"}: where a message is now, for whatever holds only its address (the poller)."""
    message = _in_a_mood(message_id)
    if message is None:
        return JsonResponse({'error': 'no such message in any Mood'}, status=404)
    return JsonResponse({'id': str(message.id), 'mood': message.mood.slug})


@require_GET
def api_pulse(request):
    """Every Mood at a glance, for a runner looking every few seconds.

    Per Mood: its newest message, newest web post and newest word from a
    person; who is typing; what an agent is doing. All of it is readable
    elsewhere already -- this only saves asking Mood by Mood.
    """
    from django.utils import timezone

    def brief(message):
        return message and {'id': str(message['id']), 'created_at': message['created_at'].isoformat(),
                            'sender': message['sender_id']}

    from .models import Setting
    from .services import settings as knobs

    humans = set(ThinkingEntity.objects.filter(is_biological_human=True).values_list('name', flat=True))
    agent = request.GET.get('agent', 'magent')
    rows = list(Setting.objects.exclude(key__in=knobs.MODERATION_KEYS))
    archived = knobs.archived_slugs()
    wiki_feed.nudge()  # asked every second by the runner: the wiki feed's clock (at most once a minute)
    push.nudge()       # and the push clock: notices to send to closed pages (at most every 10 s)
    wiki_ledger.nudge()  # and the Moods' links, written to PickiPedia (at most every 30 s)
    moods = []
    for mood in Mood.objects.all():
        said = mood.messages.filter(is_sidechain=False).exclude(sender_id__in=MACHINERY_SENDERS)
        fields = ('id', 'created_at', 'sender_id')
        moods.append({
            'slug': mood.slug,
            'newest': brief(said.order_by('-created_at').values(*fields).first()),
            'last_web_post': brief(said.filter(source_file='mood-web').order_by('-created_at').values(*fields).first()),
            'last_human': brief(said.filter(sender_id__in=humans).order_by('-created_at').values(*fields).first()),
            'typing': typing_in(mood.slug),
            'activity': activity(mood),
            # How this agent is to carry itself here (services/settings.py).
            'settings': knobs.resolve(mood.slug, agent, rows=rows),
            # Out of the list: no unprompted looks there (mentions still answered).
            'archived': mood.slug in archived,
            # Names it went by before a rename: a runner told the old one still knows it.
            'aliases': sorted(mood.aliases.values_list('slug', flat=True)),
        })
    from .services import access
    return JsonResponse({
        'now': timezone.now().isoformat(),
        'agent': agent,
        # Who has just signed in, for a runner to greet if it knows them (services/access.py).
        'arrivals': access.recent_arrivals(),
        # An admin's emergency stop: while set, a runner wakes nothing.
        'scram': knobs.scram(),
        'budget': {'consider_usd_per_day': knobs.global_value('consider_usd_per_day')},  # the runner's own
        'moods': moods,
    })


@require_GET
def api_mood_step(request, slug, step_id):
    """One tool call in full, for a step someone opened."""
    msg = _message_or_none(step_id)
    if msg is None or msg.mood_slug != Mood.by_slug_or_404(slug).slug or not hasattr(msg, 'tooluse'):
        raise Http404('no such step in this Mood')
    return JsonResponse(step_detail(msg))


# --- who is typing ---------------------------------------------------------
# Ephemeral, so not in the record: a shared cache entry per Mood holding
# name -> when they last typed. Every gunicorn worker shares the cache.
TYPING_FOR = 8  # seconds a keystroke counts as "typing"


def _typing_key(slug):
    return f'typing:{slug}'


def typing_in(slug):
    from django.core.cache import cache
    import time
    now = time.time()
    return sorted(name for name, at in (cache.get(_typing_key(slug)) or {}).items() if now - at < TYPING_FOR)


@require_POST
def api_typing(request, slug):
    """Say this device's person is (or stopped) typing in a Mood."""
    from django.core.cache import cache
    import json
    import time
    from .views_admin import locked_response
    if locked_response():
        return locked_response()
    device = mood_auth.device_for(request)
    if device is None:
        return JsonResponse({'error': 'sign in to write'}, status=401)
    Mood.by_slug_or_404(slug)
    try:
        typing = bool(json.loads(request.body or b'{}').get('typing', True))
    except (ValueError, AttributeError):
        return JsonResponse({'error': 'expected {"typing": true|false}'}, status=400)
    now = time.time()
    state = {n: at for n, at in (cache.get(_typing_key(slug)) or {}).items() if now - at < TYPING_FOR}
    if typing:
        state[device.entity_id] = now
    else:
        state.pop(device.entity_id, None)
    cache.set(_typing_key(slug), state, TYPING_FOR * 2)
    from .services import presence
    presence.note(device.entity_id, slug, now)  # typing, or stopped: shown here, three minutes from now
    return JsonResponse({'typing': sorted(state)})


@require_POST
def api_here(request, slug):
    """This device's person opened a Mood: shown there, and nowhere else, for three minutes (services/presence)."""
    from django.core.cache import cache
    from .services import presence
    device = mood_auth.device_for(request)
    if device is None:
        return JsonResponse({'error': 'sign in to be shown'}, status=401)
    mood = Mood.by_slug_or_404(slug)
    presence.note(device.entity_id, mood.slug)
    cache.delete('moods-live')  # seen there at the next look, not three seconds on
    return JsonResponse({'here': mood.slug})


@require_GET
def api_mood_sessions(request, slug):
    """Runtime sessions in a Mood, most recently active first.

    ?sender=<name> keeps only sessions in which that entity wrote. This is
    how the poller finds a session to resume when it wakes an agent: the
    Mood is durable, and whichever process last spoke for the agent is
    the one to continue. A session id is a local handle, not a credential.
    """
    mood = Mood.by_slug_or_404(slug)
    messages = Message.objects.filter(mood=mood, session_id__isnull=False)
    sender = request.GET.get('sender')
    if sender:
        messages = messages.filter(sender_id=sender.lower())
    sessions = (messages.values('session_id')
                .annotate(last_at=Max('created_at'))
                .order_by('-last_at'))
    return JsonResponse({'mood': mood.slug, 'sessions': [
        {'session_id': str(s['session_id']), 'last_at': s['last_at'].isoformat()} for s in sessions
    ]})


@require_GET
def api_wikilinks(request):
    """Turns in Moods that link to PickiPedia pages with [[...]], oldest first.

    This is what PickiPedia reads to reach Moods without storing them.
    ?page=<title> keeps one page's backlinks. ?since=<iso> starts after a
    moment; pass back `next_since` to page forward. A message's links are
    never split across pages. The cursor is ingest time (created_at), so a
    consumer should overlap its cursor by a few minutes and dedupe on
    (turn, page), and resync fully now and then: mood_assign attaches
    old messages to a Mood without changing their created_at.
    Titles are normalised as MediaWiki does.
    """
    page = request.GET.get('page')
    page = wiki_title(page) if page else None
    since_raw = request.GET.get('since')
    since = parse_datetime(since_raw) if since_raw else None
    if since_raw and since is None:
        return JsonResponse({'error': f'unparseable since: {since_raw!r} (URL-encode the +)'}, status=400)
    try:
        limit = min(max(int(request.GET.get('limit', 200)), 1), 1000)
    except ValueError:
        limit = 200

    names = known_names()
    messages = (
        Message.objects.filter(mood__isnull=False, is_sidechain=False)
        .exclude(sender_id__in=MACHINERY_SENDERS)
        .order_by('created_at')
    )
    if since:
        messages = messages.filter(created_at__gt=since)

    links, next_since = [], None
    for msg in messages.iterator():
        if len(links) >= limit:
            break
        next_since = msg.created_at.isoformat()
        for title in wiki_ledger.linked_pages(msg, names):
            if page and title != page:
                continue
            links.append({'page': title, 'mood': msg.mood_slug, 'turn': str(msg.id),
                          'sender': msg.sender_id, 'created_at': msg.created_at.isoformat(),
                          'eth_blockheight': msg.eth_blockheight})
    else:
        next_since = None  # read to the end

    return JsonResponse({'links': links, 'next_since': next_since})


SEARCH_MAX = 40
SEARCH_SCAN = 300  # candidates read, newest first, before keeping only what people and agents said


def snippet(text, q, width=160):
    """`text` around the first place `q` occurs, with its ends marked if cut."""
    at = text.lower().find(q.lower())
    if at < 0:
        return text[:width]
    start = max(0, at - width // 3)
    end = min(len(text), start + width)
    return ('…' if start else '') + text[start:end] + ('…' if end < len(text) else '')


@require_GET
def api_search(request):
    """What was said, in one Mood (?mood=<slug>) or all of them, that contains ?q=.

    Newest first, at most SEARCH_MAX. Only what people and agents said --
    not tool calls, their results or the harness's wrappers. A plain
    case-insensitive match on the stored text: about 0.15 s in one Mood,
    1.2 s across all of them, at 25k messages.
    """
    from django.db.models import TextField
    from django.db.models.functions import Cast

    q = (request.GET.get('q') or '').strip()
    if len(q) < 2:
        return JsonResponse({'error': 'two characters at least'}, status=400)
    q = q[:200]
    names = known_names()
    rows = (Message.objects.filter(mood__isnull=False, is_sidechain=False)
            .exclude(sender_id__in=MACHINERY_SENDERS)
            .annotate(text=Cast('content', TextField())).filter(text__icontains=q))
    mood = request.GET.get('mood')
    if mood:
        rows = rows.filter(mood__slug=mood)
    titles = dict(Mood.objects.values_list('slug', 'title'))
    hits = []
    for msg in rows.select_related('sender').order_by('-created_at')[:SEARCH_SCAN]:
        if msg.sender_id not in names:
            continue
        text = prose(msg.content)
        if not text or is_wrapper(text) or is_summary(msg) or q.lower() not in text.lower():
            continue
        hits.append({'id': str(msg.id), 'mood': msg.mood_slug, 'title': titles.get(msg.mood_slug, msg.mood_slug),
                     'sender': msg.sender_id, 'created_at': msg.created_at.isoformat(), 'text': snippet(text, q)})
        if len(hits) >= SEARCH_MAX:
            break
    return JsonResponse({'q': q, 'mood': mood, 'hits': hits})


RECENT_MAX = 30
RECENT_SCAN = 800  # rows read for what was said, newest first


@require_GET
def api_recent(request):
    """What's been happening across every Mood, newest first: what people said,
    agents' finished answers, renames, settings changed, Moods opened.

    ?since=<iso> (default: a day ago), ?limit= (at most RECENT_MAX). Read
    from the record; agents' progress lines between tool calls are left
    out, or they would be all there is. ?oldest=1: the first after `since`
    instead, oldest first -- for reading a stretch through from its start,
    a page at a time (moods.html, reading aloud from a message on).
    ?progress=1: an agent's words mid-turn too, as they're said -- read aloud as they come, not all at
    once when its turn ends (Justin, 22 Oct) -- never its thinking or its tool calls.
    """
    from datetime import timedelta
    from django.utils import timezone
    from .models import Setting
    from .services.mood_view import quiet_reason

    since = parse_datetime(request.GET.get('since') or '') or timezone.now() - timedelta(days=1)
    try:
        limit = min(max(int(request.GET.get('limit', RECENT_MAX)), 1), RECENT_MAX)
    except ValueError:
        limit = RECENT_MAX
    oldest = request.GET.get('oldest') == '1'
    order = 'created_at' if oldest else '-created_at'
    names = known_names()
    agents = set(ThinkingEntity.objects.filter(is_biological_human=False).values_list('name', flat=True))
    titles = dict(Mood.objects.values_list('slug', 'title'))
    events = []

    progress = request.GET.get('progress') == '1'
    # A listening page says so as it asks (services/listening.py): its person shows with 🎧 where they listen.
    if request.GET.get('listening') in ('here', 'everywhere'):
        device = mood_auth.device_for(request)
        if device is not None:
            from .services import listening
            listening.note(device.entity_id, request.GET['listening'], request.GET.get('mood') or None)
    said = (Message.objects.filter(mood__isnull=False, is_sidechain=False, created_at__gt=since,
                                   thought__isnull=True, tooluse__isnull=True, toolresult__isnull=True)
            .exclude(sender_id__in=MACHINERY_SENDERS).order_by(order)[:RECENT_SCAN])
    for msg in said:
        if msg.sender_id not in names:
            continue
        text = prose(msg.content)
        if not text or is_wrapper(text) or is_summary(msg) or quiet_reason(text) is not None:
            continue
        if msg.sender_id in agents and msg.stop_reason != 'end_turn' and not progress:
            continue
        events.append({'kind': 'answered' if msg.sender_id in agents else 'said', 'at': msg.created_at.isoformat(),
                       'mood': msg.mood_slug, 'who': msg.sender_id, 'id': str(msg.id), 'text': text[:140]})
        if len(events) >= limit:
            break

    for msg in (Message.objects.filter(source_file='mood-rename', created_at__gt=since)
                .order_by(order)[:limit]):
        content = msg.content if isinstance(msg.content, dict) else {}
        events.append({'kind': 'renamed', 'at': msg.created_at.isoformat(), 'mood': msg.mood_slug,
                       'who': content.get('by', ''), 'text': f"{(content.get('from') or {}).get('title', '')} → "
                                                             f"{(content.get('to') or {}).get('title', '')}"})

    for row in Setting.objects.filter(created_at__gt=since).order_by(order)[:limit]:
        value = row.value if not isinstance(row.value, dict) else (row.value.get('mode') or row.value)
        events.append({'kind': 'set', 'at': row.created_at.isoformat(), 'mood': mood_slug(row.mood_id),
                       'who': row.set_by_id or '', 'text': f"{row.key} for {row.agent_id or 'every agent'}: {value}"[:140]})

    told = set()  # a redeploy is announced in several Moods; list it once
    for row in (Message.objects.filter(source_file='deploy', created_at__gt=since).order_by(order)
                .values('id', 'content', 'created_at', 'timestamp')[:limit * 10]):
        c = row['content'] if isinstance(row['content'], dict) else {}
        key = (c.get('server'), c.get('state'), row['created_at'].replace(microsecond=0).isoformat()[:18])
        if key in told:
            continue
        told.add(key)
        verb = {'started': 'redeploy started', 'finished': 'redeployed', 'failed': 'redeploy failed'}.get(c.get('state'), '')
        # id: the same every time it's listed, whichever Mood's row comes first (one per Mood, one moment);
        # row: one of them, for the narrator to say (api_narrate).
        events.append({'kind': 'deploy', 'at': row['created_at'].isoformat(), 'mood': None, 'who': c.get('by', ''),
                       'id': f"deploy-{c.get('server')}-{c.get('state')}-{row['timestamp']}", 'row': str(row['id']),
                       'state': c.get('state', ''),
                       'text': f"{c.get('server')} {verb}" + (f" · {c['commit'][:8]}" if c.get('commit') else '')})

    for mood in Mood.objects.filter(created_at__gt=since).order_by(order)[:limit]:
        events.append({'kind': 'opened', 'at': mood.created_at.isoformat(), 'mood': mood.slug, 'who': '',
                       'text': mood.title or mood.slug})

    events.sort(key=lambda e: e['at'], reverse=not oldest)
    for e in events:
        e['title'] = titles.get(e['mood'], e['mood'] or 'every Mood')
    return JsonResponse({'events': events[:limit]})


NOTICES_MAX = 100


def answered_by(msg, humans):
    """Who an agent's finished turn was answering: the person whose words were
    the last a person said in that Mood before it, or None."""
    earlier = (Message.objects.filter(mood_id=msg.mood_id, is_sidechain=False, created_at__lt=msg.created_at,
                                      sender_id__in=humans)
               .order_by('-created_at').only('content', 'sender_id')[:5])
    for m in earlier:  # the latest that is someone's words, not a harness wrapper
        text = prose(m.content)
        if text and not is_wrapper(text) and not is_summary(m):
            return m.sender_id
    return None


@require_GET
def api_notices(request, name):
    """What `name` would want to hear about, across every Mood, since a moment.

    Two kinds, newest first: a 'mention' of them by someone else, and an
    'answer' -- an agent's turn that ended (end_turn) in a Mood where they
    were the last person to speak before it. That second kind is how someone
    who asked something and went elsewhere learns the agent is done, without
    the agent having to @mention them back.

    With ?all=1, a third: 'said' -- anything else someone said, a person's
    message or an agent's finished turn -- for the sounds and notifications
    of every message someone has asked for. Each notice says who it's 'from':
    'person' or 'agent'.

    ?since=<iso> (default: an hour ago). At most NOTICES_MAX.
    """
    from datetime import timedelta
    from django.utils import timezone

    name = name.lower()
    if not ThinkingEntity.objects.filter(name=name).exists():
        raise Http404
    since = parse_datetime(request.GET.get('since') or '') or timezone.now() - timedelta(hours=1)
    everything = request.GET.get('all') in ('1', 'true')
    return JsonResponse({'name': name, 'notices': notices_for(name, since, everything=everything)})


def notices_for(name, since, limit=NOTICES_MAX, everything=False):
    """api_notices' notices for `name` since a moment, newest first: what the
    bell tells them, in the page and by push (services/push.py). `everything`
    adds 'said' notices: whatever else people say, and agents' finished turns
    (not their progress lines, nor a turn that stayed silent)."""
    from .services.mood_view import quiet_reason
    names = known_names()
    agents = set(ThinkingEntity.objects.filter(is_biological_human=False).values_list('name', flat=True))
    humans = set(ThinkingEntity.objects.filter(is_biological_human=True).values_list('name', flat=True))
    messages = (Message.objects.filter(mood__isnull=False, is_sidechain=False, created_at__gt=since)
                .exclude(sender_id__in=MACHINERY_SENDERS).select_related('sender').order_by('-created_at'))
    found = []
    for msg in messages.iterator():
        if msg.sender_id not in names or msg.sender_id == name:
            continue
        text = prose(msg.content)
        if not text or is_wrapper(text) or is_summary(msg):
            continue
        by = 'agent' if msg.sender_id in agents else 'person'
        finished = msg.sender_id in agents and msg.stop_reason == 'end_turn' and quiet_reason(text) is None
        if name in addressed_in(text, names, by=msg.sender_id):
            found.append({'kind': 'mention', 'from': by, 'mood': msg.mood_slug, 'turn': turn_payload(msg, text, names)})
        elif finished and answered_by(msg, humans) == name:
            found.append({'kind': 'answer', 'from': by, 'mood': msg.mood_slug, 'turn': turn_payload(msg, text, names)})
        elif everything and (msg.sender_id in humans or finished):
            found.append({'kind': 'said', 'from': by, 'mood': msg.mood_slug, 'turn': turn_payload(msg, text, names)})
        if len(found) >= limit:
            break
    found += talk_page_notices(name, since)
    found.sort(key=lambda n: n['turn']['created_at'], reverse=True)
    return found[:limit]


def talk_page_notices(name, since):
    """Someone else edited `name`'s PickiPedia talk page: a 'talk' notice, from the
    wiki feed's line for it (services/wiki_feed.py). Their talk page is where
    people leave them word on the wiki, so it's word for them here too."""
    from .services.mood_view import user_page_of
    from .services.wiki_auth import local_name_for
    found = []
    rows = Message.objects.filter(source_file='wiki', created_at__gt=since,
                                  content__title__startswith='User talk:').order_by('-created_at')
    for msg in rows:
        c = msg.content if isinstance(msg.content, dict) else {}
        page = user_page_of(c.get('title'))
        editor = c.get('user', '')
        if not page or local_name_for(page[0]) != name or local_name_for(editor) == name:
            continue
        found.append({'kind': 'talk', 'from': 'person', 'mood': msg.mood_slug, 'turn': {
            'id': str(msg.id), 'sender': editor, 'created_at': msg.created_at.isoformat(),
            'text': f"edited your talk page on PickiPedia" + (f": {c['comment']}" if c.get('comment') else '')}})
    return found




@require_GET
def api_yarn(request, clip):
    """A Yarn clip's video, kept here (services/yarn_kept.py): {"url", "still"?}, its picture too if kept. Not kept yet, a signed-in
    device has it fetched now (an anonymous one doesn't set the server fetching); 404 if Yarn won't give it."""
    from .services import mood_auth, yarn_kept
    url, still = yarn_kept.kept_still(clip)
    if (url is None or still is None) and mood_auth.device_for(request) is not None:
        url = yarn_kept.keep(clip)  # the video if need be, and its picture
        url, still = yarn_kept.kept_still(clip)
    if url is None:
        return JsonResponse({'error': 'not kept here, and Yarn would not give it'}, status=404)
    return JsonResponse({'url': url, **({'still': still} if still else {})})

@require_GET
def api_wiki_preview(request):
    """?title=: a PickiPedia page in brief, for the card shown on a link to it (services/wiki_preview.py).
    {'title', 'url', 'summary', 'facts', 'art', 'image'}; 404 if there's no such page."""
    from .services import wiki_preview
    found = wiki_preview.preview(request.GET.get('title', ''))
    if not found:
        return JsonResponse({'error': 'no such page'}, status=404)
    response = JsonResponse(found)
    response['Cache-Control'] = 'public, max-age=3600'
    return response

@require_GET
def api_mentions(request, name):
    """Recent turns, across all Moods, that mention one thinking entity.

    Newest first. ?since=<iso> narrows to what arrived after a moment;
    ?limit= caps the result (default 50, max 200). Mentions are derived
    from message text at read time -- there is no mention table -- so this
    scans, bounded by `since` and `limit`. Fine at today's volume.

    This is what a notification badge reads, and what a poller reads to
    learn that an agent has been addressed by name.
    """
    name = name.lower()
    if not ThinkingEntity.objects.filter(name=name).exists():
        raise Http404

    since = parse_datetime(request.GET.get('since') or '')
    try:
        limit = min(max(int(request.GET.get('limit', 50)), 1), 200)
    except ValueError:
        limit = 50

    from django.db.models import Q
    from .services import handoff, tasks, tiding_links

    names = known_names()
    is_agent = not ThinkingEntity.objects.get(name=name).is_biological_human
    messages = (
        Message.objects.filter(mood__isnull=False, is_sidechain=False)
        # A handoff into a Mood "for" an agent is the system's line, but it
        # addresses that agent: it wakes it there (handoff_mention). So does
        # a task started for it (services/tasks.py).
        .filter(~Q(sender_id__in=MACHINERY_SENDERS) | Q(source_file__in=(handoff.SOURCE, tasks.SOURCE))
                # So is a comment on Tidings that asks an agent by name: its line in the link's Mood wakes it.
                | Q(source_file=tiding_links.COMMENT_SOURCE))
        .select_related('sender')
        .order_by('-created_at')
    )
    if since:
        messages = messages.filter(created_at__gt=since)

    found = []
    for msg in messages.iterator():
        if msg.source_file == handoff.SOURCE:
            turn = handoff_mention(msg, name, names) if is_agent else None
            if turn:
                found.append({'mood': msg.mood_slug, 'turn': turn})
                if len(found) >= limit:
                    break
            continue
        if msg.source_file == tasks.SOURCE:
            turn = tasks.mention(msg, name) if is_agent else None
            if turn:
                found.append({'mood': msg.mood_slug, 'turn': turn})
                if len(found) >= limit:
                    break
            continue
        if msg.source_file == tiding_links.COMMENT_SOURCE:
            turn = tiding_links.mention_of(msg, name) if is_agent else None
            if turn:
                found.append({'mood': msg.mood_slug, 'turn': turn})
                if len(found) >= limit:
                    break
            continue
        if msg.sender_id not in names:
            continue
        if is_agent and from_wiki_tier(msg):
            continue  # signed in with PickiPedia: an @agent is just text, it wakes nobody
        text = prose(msg.content)
        if not text or is_wrapper(text) or is_summary(msg):
            continue
        if name in addressed_in(text, names, by=msg.sender_id):
            turn = turn_payload(msg, text, names)
            if not turn['is_human']:
                # Whether the agent that wrote it was itself woken with full
                # tools: if so, it may wake this one with them (mood_poller.trusted).
                turn['full_tools'] = wake_tools(msg)
            found.append({'mood': msg.mood_slug, 'turn': turn})
            if len(found) >= limit:
                break

    return JsonResponse({'name': name, 'mentions': found})


def wake_tools(message):
    """Whether the agent wrote this message on a wake with its full tools: what the last
    wake before it in its session said (mood_view.turn_tools); else, for a session
    whose wakes say neither, what the runner said of the session."""
    if message is None or not message.session_id:
        return False
    said = turn_tools([message]).get(str(message.id))
    if said:
        return said == 'full'
    return MoodSession.objects.filter(session_id=message.session_id, full_tools=True).exists()


# An agent waking itself in another Mood by handoff (`wake: yes`): soft loop
# protection. Past this many in an hour, its handoffs to itself still arrive,
# to ride along in its next wake there, but wake nothing.
SELF_WAKES_PER_HOUR = 6


def handoff_mention(msg, name, names):
    """A handoff brought into this Mood for `name`, as the mention that wakes
    it here, or None. Its writer is the sender, and it carries full tools only
    if the writer's own wake had them. One for another agent always wakes it;
    one the writer sent itself only with `wake: yes`, and only the first
    SELF_WAKES_PER_HOUR in any hour."""
    from datetime import timedelta
    from .services import handoff

    c = msg.content if isinstance(msg.content, dict) else {}
    by = c.get('by')
    if c.get('type') != 'handoff' or c.get('for') != name or c.get('pulled') or by not in names:
        return None
    if by == name:
        if not c.get('wake'):
            return None
        earlier = Message.objects.filter(
            source_file=handoff.SOURCE, content__type='handoff', content__by=name, content__for=name,
            content__wake=True, created_at__gt=msg.created_at - timedelta(hours=1), created_at__lt=msg.created_at)
        if earlier.count() >= SELF_WAKES_PER_HOUR:
            return None
    origin = Message.objects.filter(id=c.get('message')).first()
    whose = 'you' if by == name else by
    text = (f"@{name}: {whose} handed this over from #{c.get('from_mood', '')}: {c.get('about', '')}\n\n"
            f"{c.get('context', '')}").strip()
    return {'id': str(msg.id), 'sender': by, 'is_human': False, 'created_at': msg.created_at.isoformat(),
            # Brought in, not typed into a live session: nobody here is about to answer it.
            'via': 'handoff', 'tier': None, 'text': text, 'mentions': [name], 'reply': None,
            'full_tools': wake_tools(origin)}


# --- installing it as an app (a web app manifest, an icon, a service worker) --
# Chrome and Edge (Ubuntu, Windows, macOS) offer "Install" for a page with a
# manifest, and Chrome on Android "Install app": it then opens in its own
# window, from the launcher, with no browser around it.

@require_GET
def app_manifest(request):
    return JsonResponse({
        'name': 'magenta',
        'short_name': 'magenta',
        'description': 'Moods: where cryptograss talks, people and agents together.',
        'id': '/moods/',
        'start_url': '/moods/',
        'scope': '/moods/',
        'display': 'standalone',
        'background_color': '#fbfaf7',
        'theme_color': '#b8106b',
        'icons': [{'src': f'/moods/icon-{size}.png', 'sizes': f'{size}x{size}', 'type': 'image/png',
                   'purpose': 'any maskable'} for size in (192, 512)],
        # In Android's share sheet: a link, words, pictures, a video or a recording from
        # another app. The service worker takes it (SERVICE_WORKER); app_share if it can't.
        'share_target': {
            'action': SHARE_ACTION, 'method': 'POST', 'enctype': 'multipart/form-data',
            'params': {'title': 'title', 'text': 'text', 'url': 'url',
                       'files': [{'name': 'files', 'accept': ['image/*', 'video/*', 'audio/*']}]},
        },
    }, content_type='application/manifest+json')


SHARE_ACTION = '/moods/share/'
SHARE_MAX_CHARS = 4000  # of each of title, text and url, passed on in the address


@csrf_exempt  # Android's POST, from the share sheet: no page, so no token. It only fills the box; nothing is sent.
def app_share(request):
    """A share that reached the server: the service worker, which keeps a share's
    files for the page, wasn't there to take it (not yet installed, or cleared).
    Its words go on in the address, for the page to offer; its files can't, and
    the page says how many didn't come."""
    from urllib.parse import urlencode
    from django.shortcuts import redirect
    if request.method != 'POST':
        return redirect('/moods/')
    said = {k: request.POST.get(k, '')[:SHARE_MAX_CHARS] for k in ('title', 'text', 'url')}
    lost = len(request.FILES.getlist('files'))
    query = {'shared': 'here', **{k: v for k, v in said.items() if v}, **({'lost': lost} if lost else {})}
    return _see_other('/moods/?' + urlencode(query))


def _see_other(url):
    """A 303: the browser follows a POST's answer with a GET."""
    from django.http import HttpResponseRedirect
    response = HttpResponseRedirect(url)
    response.status_code = 303
    return response


@require_GET
def app_icon(request, size, kind='icon'):
    """magenta's icon (services/app_icon.py): the app's, or the bare mark."""
    from django.http import HttpResponse
    from .services import app_icon as icon
    try:
        body = icon.png(size, kind)
    except KeyError:
        raise Http404(f'no {kind} that size')
    response = HttpResponse(body, content_type='image/png')
    response['Cache-Control'] = 'public, max-age=86400'
    return response


SERVICE_WORKER = """// magenta: here so the page can be installed as an app. It caches
// nothing: every request goes to the network, as if it weren't here --
// but for a share from another app, kept for the page to offer.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));
self.addEventListener('fetch', e => {
  if (e.request.method === 'POST' && new URL(e.request.url).pathname === '/moods/share/') {
    e.respondWith(takeShare(e.request));
    return;
  }
  if (e.request.mode === 'navigate') e.respondWith(fetch(e.request));
});
// A share (the manifest's share_target): its words and files kept in IndexedDB,
// where the page finds them and asks which Mood they go to; then the page.
// One share at a time is kept: the newest.
function shareDb() {
  return new Promise((resolve, reject) => {
    const open = indexedDB.open('magenta-share', 1);
    open.onupgradeneeded = () => open.result.createObjectStore('shared');
    open.onsuccess = () => resolve(open.result);
    open.onerror = () => reject(open.error);
  });
}
async function takeShare(request) {
  try {
    const form = await request.formData();
    const words = k => String(form.get(k) || '').slice(0, 4000);
    const files = form.getAll('files').filter(f => typeof f === 'object' && f.size);
    const db = await shareDb();
    await new Promise((resolve, reject) => {
      const tx = db.transaction('shared', 'readwrite');
      tx.objectStore('shared').put({ title: words('title'), text: words('text'), url: words('url'), files, at: Date.now() }, 'latest');
      tx.oncomplete = resolve;
      tx.onerror = () => reject(tx.error);
    });
  } catch (err) {
    return Response.redirect('/moods/?shared=failed', 303);
  }
  return Response.redirect('/moods/?shared=kept', 303);
}
// A push (services/push.py): a mention or an answer, while magenta is closed.
// A magenta page in front of its person tells them itself, so then: nothing.
self.addEventListener('push', e => {
  let n = null;
  try { n = e.data ? e.data.json() : null; } catch (err) {}
  if (!n || !n.title) return;
  e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(list => {
    if (list.some(c => c.visibilityState === 'visible' && new URL(c.url).pathname.startsWith('/moods/'))) return;
    // The badge: the mark alone, which Android shows in the status bar (it reads only its alpha).
    // timestamp: when it was said, so one held while the phone slept doesn't say "now".
    return self.registration.showNotification(n.title, { body: n.body || '', tag: n.tag, icon: '/moods/icon-192.png',
                                                         badge: '/moods/mark-96.png', data: n.data || {},
                                                         ...(n.timestamp ? { timestamp: n.timestamp } : {}) });
  }));
});
// A notification (a mention, an answer) opens its Mood at that message: in a
// window already open on magenta if there is one, else a new one. The open
// page says it heard; one asleep or gone doesn't, and is sent to the message
// instead (Justin, 19 Oct: a notice from one Mood opened #general).
self.addEventListener('notificationclick', e => {
  e.notification.close();
  const { slug, id } = e.notification.data || {};
  const url = '/moods/' + encodeURIComponent(slug || '') + '/' + (id ? '#m-' + id : '');
  e.waitUntil((async () => {
    const list = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    const open = list.find(c => new URL(c.url).pathname.startsWith('/moods/'));
    if (!open) return self.clients.openWindow(url);
    try { await open.focus(); } catch (err) {}
    const heard = await new Promise(resolve => {
      const channel = new MessageChannel();
      const wait = setTimeout(() => resolve(false), 1500);
      channel.port1.onmessage = () => { clearTimeout(wait); resolve(true); };
      open.postMessage({ open: slug, id }, [channel.port2]);
    });
    if (heard) return;
    try { if (open.navigate) return await open.navigate(url); } catch (err) {}
    return self.clients.openWindow(url);
  })());
});
"""


@require_GET
def app_service_worker(request):
    from django.http import HttpResponse
    response = HttpResponse(SERVICE_WORKER, content_type='text/javascript')
    response['Service-Worker-Allowed'] = '/moods/'
    response['Cache-Control'] = 'no-cache'
    return response


@require_GET
def api_work(request):
    """Open pull requests and new issues across our repositories, with the people
    and Moods each involves (services/work.py)."""
    from django.core.cache import cache
    from .services import work
    items = cache.get('work:open')
    if items is None:
        try:
            items = work.open_work()
        except Exception as e:  # the forge unreachable, or rate-limited: say so, don't fail the page
            return JsonResponse({'items': [], 'error': f'could not ask the forge: {type(e).__name__}'})
        cache.set('work:open', items, 120)
    return JsonResponse({'items': items})


# --- who's around in each Mood, and the chain's height -------------------------

AROUND_BLOCKS = 100     # who spoke within this many blocks counts as around
SECONDS_PER_BLOCK = 12  # since the merge, a slot every 12 s (a missed slot makes it a little more)


@require_GET
def api_mood_todo(request, slug):
    """The Mood's to-do list, from PickiPedia (services/todo.py): {"page", "edit", "exists", "items", "error"?}.
    ?fresh=1 asks PickiPedia again now (after an edit)."""
    from .services import todo
    mood = Mood.by_slug_or_404(slug)
    if request.GET.get('fresh'):
        todo.forget(mood)
    # And what GitHub's webhook last said, and when: whether merges are being told at once.
    return JsonResponse({**todo.for_mood(mood), 'hook': todo.last_heard(), 'hook_refused': todo.last_refused()})


@csrf_exempt  # GitHub's call, proven by its signature, not a browser's session
@require_POST
def api_github_hook(request):
    """GitHub's webhook: a pull request merged ticks every to-do list linking it, now (services/todo.py).

    Signed with settings.GITHUB_WEBHOOK_SECRET (X-Hub-Signature-256); unsigned
    or mis-signed, refused. Any other event, or a pull request closed unmerged,
    is acknowledged and changes nothing."""
    import hashlib
    import hmac
    import json
    from .services import todo
    secret = getattr(settings, 'GITHUB_WEBHOOK_SECRET', '')
    if not secret:
        return JsonResponse({'error': "the webhook isn't set up here"}, status=503)
    given = request.headers.get('X-Hub-Signature-256', '')
    expected = 'sha256=' + hmac.new(secret.encode(), request.body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(given, expected):
        todo.heard(request.headers.get('X-GitHub-Event', ''), 'signed' if given else 'unsigned', refused=True)
        return JsonResponse({'error': 'signature not accepted'}, status=403)
    event = request.headers.get('X-GitHub-Event', '')
    # GitHub sends JSON, or -- its default for a new webhook -- a form with the JSON as "payload".
    try:
        raw = request.POST.get('payload', '') if 'form' in request.content_type else request.body
        body = json.loads(raw or b'{}')
    except ValueError:
        todo.heard(event, 'unreadable')
        return JsonResponse({'error': 'expected JSON, or a form with it as payload'}, status=400)
    pull = body.get('pull_request') or {}
    name = (body.get('repository') or {}).get('full_name', '')
    if event == 'pull_request' and body.get('action') == 'closed' and pull.get('merged') and '/' in name:
        owner, repo = name.split('/', 1)
        todo.merged_now(owner, repo, int(pull.get('number') or 0), pull.get('title') or '',
                        (pull.get('merged_by') or {}).get('login') or '', commit=pull.get('merge_commit_sha') or '')
        todo.heard(event, f"ticked {name}#{pull.get('number')}")
        return JsonResponse({'ticked': f"{name}#{pull.get('number')}"})
    todo.heard(event, 'nothing to do')
    return JsonResponse({'ok': True, 'event': event})


@require_GET
def api_unfurl(request, slug, message_id):
    """The preview cards for a message's plain links (services/unfurl.py): {"cards": [...]}. Only links
    said in this Mood, so the server fetches nothing a reader names; each page kept a day."""
    from .services import unfurl
    from .views_auth import _under_limit, _uuid_or_none
    mood = Mood.by_slug_or_404(slug)
    message = Message.objects.filter(id=_uuid_or_none(message_id), mood=mood).first()
    if message is None:
        return JsonResponse({'error': 'no such message in this Mood'}, status=404)
    if not _under_limit('unfurl:' + (request.META.get('REMOTE_ADDR') or ''), 60):
        return JsonResponse({'error': 'slow down'}, status=429)
    cards = [c for c in (unfurl.card(url) for url in unfurl.links_in(prose(message.content))) if c]
    return JsonResponse({'cards': cards})


@require_GET
def api_moods_live(request):
    """Each Mood's people of the moment: each person in the one Mood they last opened, typed or
    spoke in, for three minutes (services/presence); each agent where it spoke in the last 100
    blocks (about 20 minutes); who's typing, which agent is working; and when anything
    was last added there (a page brings the Moods it holds up to date by it,
    before they're opened). Cached for 3 s: every open page asks every few seconds."""
    from datetime import timedelta
    from django.core.cache import cache
    from django.utils import timezone

    push.nudge()  # every open page asks this: the push clock runs while the runner's away, too
    cached = cache.get('moods-live')
    if cached is not None:
        return JsonResponse(cached)
    now = timezone.now()
    names = set(ThinkingEntity.objects.values_list('name', flat=True))
    since = now - timedelta(seconds=AROUND_BLOCKS * SECONDS_PER_BLOCK)
    spoke = (Message.objects.filter(mood__isnull=False, is_sidechain=False, created_at__gte=since,
                                    sender_id__in=names)
             .values('mood__slug', 'sender_id').annotate(last=Max('created_at')).order_by('-last'))
    # A person is shown in one Mood: where they last opened, typed or spoke, for three minutes after
    # (services/presence; Justin, 23 Oct). An agent, wherever it's spoken lately, as before.
    from .services import presence
    people = set(ThinkingEntity.objects.filter(is_biological_human=True).values_list('name', flat=True))
    spoke = list(spoke)
    where = presence.places(said=[(r['sender_id'], r['mood__slug'], r['last'].timestamp()) for r in spoke
                                  if r['sender_id'] in people])
    live = {}
    for row in spoke:
        if row['sender_id'] not in people:
            live.setdefault(row['mood__slug'], {'speakers': [], 'typing': [], 'busy': None})['speakers'].append(row['sender_id'])
    for name, slug in sorted(where.items()):
        live.setdefault(slug, {'speakers': [], 'typing': [], 'busy': None})['speakers'].append(name)
    for mood in Mood.objects.all():
        entry = live.setdefault(mood.slug, {'speakers': [], 'typing': [], 'busy': None})
        entry['typing'] = typing_in(mood.slug)
        last = mood.messages.order_by('-created_at').values_list('created_at', flat=True).first()  # (mood, created_at) index
        entry['last'] = last.isoformat() if last else None
        if mood.slug in {r['mood__slug'] for r in spoke}:  # only a Mood with recent words can have work under way
            doing = activities(mood)
            working = [a['agent'] for a in doing if a.get('doing') != 'held']
            # An agent waiting for another asked too (poller's _WAIT_REPLY): read aloud as thinking is.
            # 'waiting for re to settle the schema': for 're', to 'settle the schema'.
            waiting = [{'agent': a['agent'], **dict(zip(('for', 'to'), a['why'][len('waiting for '):].split(' to ', 1)))}
                       for a in doing if a.get('doing') == 'held' and str(a.get('why') or '').startswith('waiting for ')]
            if waiting:
                entry['waiting'] = waiting
            if doing:
                # Each at work there, as a Mood's own look has them: shown the moment it's opened, not once
                # its first look is back (Justin, 25 Oct: they "pop up" a couple of seconds later).
                entry['activities'] = doing
            if working:
                entry['busy'] = working[-1]  # the one that started last, as before: one, for what reads one
                entry['busy_all'] = working  # every agent at work there, each pulsing in the list
    from .services import listening, watchers
    for slug in list(live):  # who's listening by voice there, 🎧 beside their names (Justin, 22 Oct)
        heard_by = listening.in_mood(slug)
        if heard_by:
            live[slug]['listening'] = heard_by
    for slug, n in watchers.by_mood().items():  # who's reading without signing in: a count beside the Mood
        live.setdefault(slug, {'speakers': [], 'typing': [], 'busy': None})['watching'] = n
    body = {'moods': live, 'blocks': AROUND_BLOCKS}
    cache.set('moods-live', body, 3)
    return JsonResponse(body)


@require_GET
def api_block(request):
    """Ethereum mainnet's newest block -- height and time -- from a public
    explorer, cached a minute; the page counts on from it, a block every
    12 seconds."""
    from django.core.cache import cache
    block = cache.get('eth-head')
    if block is None:
        try:
            import requests
            head = requests.get('https://eth.blockscout.com/api/v2/main-page/blocks', timeout=5).json()[0]
            block = {'height': int(head['height']), 'at': head['timestamp'], 'seconds': SECONDS_PER_BLOCK}
        except Exception:
            block = {'height': None}
        cache.set('eth-head', block, 60 if block['height'] else 15)
    return JsonResponse(block)
