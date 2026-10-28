"""The settings page and its API: how each agent carries itself in each Mood.

Reading is public, like the rest of the record. Changing a setting takes a
signed-in person (a device) and the CSRF check; every change is a new row,
so the page also shows who changed what, when, and why.
"""

import json
import re

from django.http import JsonResponse
from django.conf import settings
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_http_methods

from .models import Mood, Setting, ThinkingEntity
from .services import mood_auth
from .services import settings as knobs


def settings_state():
    agents = list(ThinkingEntity.objects.filter(is_biological_human=False).order_by('name')
                  .values_list('name', flat=True))
    moods = [{'slug': m.slug, 'title': m.title or m.slug} for m in Mood.objects.order_by('slug')]
    from .services import wiki_auth
    wiki_names = wiki_auth.names()
    rows = list(Setting.objects.exclude(key__in=knobs.MODERATION_KEYS))
    current = knobs.latest(rows)
    return {
        'knobs': {k: {'default': d, 'help': h} for k, (d, h) in knobs.KNOBS.items()},
        'global_knobs': {k: {'default': d, 'help': h} for k, (d, h) in knobs.GLOBAL_KNOBS.items()},
        'efforts': knobs.EFFORTS,
        'choices': {'discretion': knobs.DISCRETIONS, 'verbosity': knobs.VERBOSITIES},
        'agents': agents,
        'moods': moods,
        # Every explicitly set slot, as it stands; anything absent is inherited.
        'set': [knobs.describe(row) for row in current.values()],
        # What each agent ends up with in each Mood, after inheritance.
        'resolved': {m['slug']: {a: knobs.resolve(m['slug'], a, rows=rows) for a in agents} for m in moods},
        'global': {k: knobs.global_value(k) for k in knobs.GLOBAL_KNOBS},
        'history': [knobs.describe(row) for row in Setting.objects.order_by('-created_at')[:100]],
        'scram': knobs.scram(),
        # Everyone who's read aloud: the Voices section's rows (as the Moods page names them).
        'people': [{'name': n, 'human': h, 'shown': wiki_names.get(n, n)}
                   for n, h in ThinkingEntity.objects.order_by('name').values_list('name', 'is_biological_human')],
    }


SECTIONS = ('tidings', 'prompts', 'status', 'settings')


def section(name):
    """A section's page as the one page shows it (?embed=1: inside it, without its own header); asked for
    directly, the one page, showing that section (views_moods.moods_page)."""
    def wrap(page):
        @ensure_csrf_cookie
        @require_GET
        def view(request):
            if request.GET.get('embed'):
                response = page(request)
                # Framed by the one page, and only by a page of ours (Django's default is DENY).
                response['X-Frame-Options'] = 'SAMEORIGIN'
                return response
            from .views_moods import moods_page
            return moods_page(request, section=name)
        view.__name__ = page.__name__
        return view
    return wrap


@section('settings')
def settings_page(request):
    device = mood_auth.device_for(request)
    return render(request, 'conversations/mood_settings.html', {
        'viewer': device.entity_id if device else '', 'embed': True,
        # Anyone's voice is chosen with an SSH key; your own from any sign-in (views_auth.api_speaker_voice).
        'key_holder': bool(device and device.tier == 'key' and device.entity.is_biological_human)})


@section('status')
def status_page(request):
    device = mood_auth.device_for(request)
    viewer = device.entity_id if device else ''
    viewer_tier = device.tier if device else ''
    return render(request, 'conversations/mood_status.html', {
        'viewer': viewer,
        'viewer_tier': viewer_tier,
        'embed': True,
    })


@require_GET
def api_status(request):
    from .services import status
    device = mood_auth.device_for(request)
    return JsonResponse(status.full_status(private=bool(device and device.tier == 'key')))


def _rights(link, device):
    """What this device may do to a link on Tidings: retitle it (✎), take it off; and, on its page, edit or
    delete each thing said about it."""
    from .services import tiding_links
    link['may_retitle'] = tiding_links.may_retitle(device, link['by'])
    link['may_withdraw'] = tiding_links.may_withdraw(device, link['by'])
    link['may_pin'] = device is not None and device.tier == 'key'
    comments = link.get('comments')
    for node in tiding_links._walk(comments if isinstance(comments, list) else []):  # the list says how many: no tree
        node['may_edit'], node['may_delete'] = tiding_links.may_change_said(node, device)
    return link


@section('tidings')
def tidings_page(request):
    from .services import tidings
    device = mood_auth.device_for(request)
    order = 'new' if request.GET.get('order') == 'new' else 'top'  # top: pinned, then ranked (Justin, 22 Oct)
    state = tidings.tidings_state(viewer=device.entity_id if device else None, order=order)
    for link in state['links']:
        _rights(link, device)
    return render(request, 'conversations/mood_tidings.html', {
        'tidings': state,
        'order': order,
        'picture_cap': tidings.PICTURES,
        'viewer': device.entity_id if device else '',
        'embed': True,
    })


@ensure_csrf_cookie
@require_GET
def tiding_page(request, tiding_id):
    """One link's page: itself and what's been said about it, as a comment can be said. Asked for with
    ?embed=1, it draws inside the one page's Tidings frame; without, the one page, on Tidings."""
    from .services import tiding_links
    from .views_auth import _uuid_or_none
    if not request.GET.get('embed'):
        from .views_moods import moods_page
        return moods_page(request, section='tidings')
    device = mood_auth.device_for(request)
    viewer = device.entity_id if device else None
    try:
        state = tiding_links.discussion(_uuid_or_none(tiding_id), viewer)
    except tiding_links.Refused as e:
        from django.http import HttpResponse
        response = HttpResponse(str(e), status=e.status)
    else:
        from .services import wiki_auth
        response = render(request, 'conversations/mood_tiding.html', {
            'tiding': _rights(state, device),
            'viewer': viewer,
            'embed': True,
            'wiki_signin': wiki_auth.enabled(),
            # [[ in the box suggests PickiPedia titles, asked of the wiki itself (as the Moods' composer does).
            'pickipedia_url': getattr(settings, 'PICKIPEDIA_URL', 'https://pickipedia.xyz').rstrip('/'),
        })
    response['X-Frame-Options'] = 'SAMEORIGIN'  # drawn in the one page's Tidings frame, as section() allows
    return response


@section('prompts')
def prompts_page(request):
    from .services import tidings
    return render(request, 'conversations/mood_prompts.html', {
        'prompts': tidings.prompts_state(),
        'prompt_cap': tidings.PROMPTS_PER_HUMAN,
        'embed': True,
    })


@require_GET
def api_emoji(request):
    """Every emoji Unicode has, grouped and named (conversations/data/emoji.json, from tools/emoji_data.py): the
    page's picker and :name completion ask for it once, and keep it."""
    import os
    from django.http import HttpResponse
    with open(os.path.join(os.path.dirname(__file__), 'data', 'emoji.json'), 'rb') as f:
        response = HttpResponse(f.read(), content_type='application/json; charset=utf-8')
    response['Cache-Control'] = 'public, max-age=604800'
    return response


@require_GET
def api_tidings(request):
    from .services import tidings
    device = mood_auth.device_for(request)
    return JsonResponse(tidings.tidings_state(viewer=device.entity_id if device else None))


@require_GET
def api_tidings_find(request):
    """{"links": [{id, title, host, at}]}: the links on Tidings whose title or address has every word of ?q=,
    newest first, eight at most -- what '#t-' offers in the composer. No ?q=: the newest eight."""
    from django.db.models import Q
    from urllib.parse import urlsplit
    from .models import Tiding
    found = Tiding.objects.only('id', 'title', 'url', 'at')
    for word in re.findall(r'[^\s\-_]+', request.GET.get('q', ''))[:6]:
        found = found.filter(Q(title__icontains=word) | Q(url__icontains=word))
    return JsonResponse({'links': [{'id': str(t.id), 'title': t.title or t.url, 'host': urlsplit(t.url).hostname or '',
                                    'at': t.at.isoformat()} for t in found.order_by('-at')[:8]]})


@require_GET
def api_prompts(request):
    from .services import tidings
    return JsonResponse(tidings.prompts_state())


@require_GET
def api_tidings_badges(request):
    """{"tidings", "comments"}: what's new on Tidings since ?since= (an ISO time) -- the nav button's two
    badges (Justin, 21 Oct)."""
    from datetime import datetime
    from .services import tiding_links
    try:
        since = datetime.fromisoformat(request.GET.get('since', '').replace(' ', '+'))  # a '+' offset comes as a space
    except ValueError:
        return JsonResponse({'error': 'expected ?since= as an ISO time'}, status=400)
    device = mood_auth.device_for(request)
    return JsonResponse(tiding_links.badges(since, viewer=device.entity_id if device else None))


@require_http_methods(['GET', 'POST'])
def api_settings(request):
    if request.method == 'GET':
        return JsonResponse(settings_state())

    from .views_admin import locked_response
    if locked_response():
        return locked_response()
    device, refused = mood_auth.key_device(request, 'change settings')
    if refused:
        return refused
    if not device.entity.is_biological_human:
        return JsonResponse({'error': 'settings are for the people in a Mood to change'}, status=403)
    try:
        body = json.loads(request.body)
        key = str(body['key'])
    except (ValueError, KeyError, TypeError):
        return JsonResponse({'error': 'expected {"key": ..., "value": ..., "mood": slug|null, "agent": name|null}'},
                            status=400)
    mood = agent = None
    if body.get('mood'):
        mood = Mood.objects.filter(slug=body['mood']).first()
        if mood is None:
            return JsonResponse({'error': f"no Mood {body['mood']}"}, status=400)
    if body.get('agent'):
        agent = ThinkingEntity.objects.filter(name=body['agent'], is_biological_human=False).first()
        if agent is None:
            return JsonResponse({'error': f"no agent {body['agent']}"}, status=400)
    try:
        if body.get('every_mood'):  # one agent's setting, in every Mood at once (the settings page's Set)
            if agent is None or mood is not None or key not in knobs.KNOBS:
                return JsonResponse({'error': 'every_mood: one agent, one per-agent setting, no Mood'}, status=400)
            rows = knobs.change_every_mood(key, body.get('value'), agent, by=device.entity, note=body.get('note', ''))
            return JsonResponse({'set': [knobs.describe(r) for r in rows]}, status=201)
        row = knobs.change(key, body.get('value'), mood=mood, agent=agent, by=device.entity,
                           note=body.get('note', ''))
    except knobs.Invalid as e:
        return JsonResponse({'error': str(e)}, status=400)
    return JsonResponse(knobs.describe(row), status=201)


# --- how an agent sees a Mood: its instructions, as they reach it -------------

STANDING_URL = 'https://raw.githubusercontent.com/cryptograss/magenta/main/agents/magent.md'
STANDING_SOURCE = 'https://github.com/cryptograss/magenta/blob/main/agents/magent.md'


def standing_instructions():
    """The agent's standing instructions (magenta's agents/magent.md, public), cached an hour; '' if unreachable."""
    from django.core.cache import cache
    text = cache.get('standing-instructions')
    if text is None:
        try:
            import requests
            response = requests.get(STANDING_URL, timeout=5)
            text = response.text if response.ok else ''
        except Exception:
            text = ''
        cache.set('standing-instructions', text, 3600 if text else 120)
    return text


_texts = None


def _wiki_texts():
    """One reader for the server's life, so the instructions page asks the wiki at most every few minutes."""
    global _texts
    if _texts is None:
        from poller.wiki_rules import EDITORS, WikiTexts
        _texts = WikiTexts(editors=getattr(settings, 'MOOD_WIKI_RULES_EDITORS', None) or EDITORS)
    return _texts


def instructions_now(agent, slug):
    """{'text', 'layers'}: the agent's instructions for the Mood, gathered as its runner gathers them
    (poller/instructions.py), cached two minutes; None if PickiPedia can't be read at all."""
    from django.core.cache import cache

    from poller.instructions import bundle
    from poller.wiki_rules import page_url
    key = f'instructions-now:{agent}:{slug}'
    found = cache.get(key)
    if found is None:
        try:
            b = bundle(agent, slug, state_dir=None)
        except Exception:  # noqa: BLE001 -- the page still shows its settings and wakes
            return None
        layers = [b.greeting, b.agent_page, *b.layers]
        found = {'text': b.text, 'layers': [{'page': layer.title, 'url': page_url(layer.title), 'revision': layer.revision,
                                             'by': layer.editor, 'problem': layer.problem, 'absent': layer.absent}
                                            for layer in layers if layer is not None]}
        cache.set(key, found, 120)
    return found


@require_GET
def instructions_page(request, slug):
    """What an agent is told in this Mood, as it reaches it: its instructions (gathered now, as its
    runner gathers them), the settings in force, what each kind of wake says, and what Claude Code
    adds. Read from the code that sends them (poller/instructions.py, poller/mood_poller.py), so it
    cannot drift from what is sent."""
    import urllib.parse

    from poller.mood_poller import wake_frames

    mood = Mood.by_slug_or_404(slug)
    agent = request.GET.get('agent', 'magent').lower()
    if not ThinkingEntity.objects.filter(name=agent, is_biological_human=False).exists():
        agent = 'magent'  # an agent's name only: it names the pages read
    resolved = knobs.resolve(slug, agent)
    frames = wake_frames(slug, agent=agent,
                         discretion=resolved.get('discretion') or 'normal', verbosity=resolved.get('verbosity') or 'normal',
                         trusted="anyone signed into magenta with an SSH key",
                         # As the runner sends them: PickiPedia's texts, else the code's.
                         texts=_wiki_texts() if getattr(settings, 'MOOD_WIKI_RULES', True) else None)
    def plain(key, value):
        if key == 'listening':
            return value.get('mode', 'on') + (f" until {value['until']}" if value.get('until') else '')
        if isinstance(value, bool):
            return 'on' if value else 'off'
        if key == 'model' and not value:
            return "(the harness's default)"
        return value
    shown = [(knobs.KNOBS[k][1], k, plain(k, resolved[k])) for k in knobs.KNOBS]
    bundles = 'https://pickipedia.xyz/wiki/Special:AgentBundles?' + urllib.parse.urlencode({'agent': agent, 'mood': slug})
    return render(request, 'conversations/mood_instructions.html', {
        'mood': mood, 'agent': agent, 'instructions': instructions_now(agent, slug), 'bundles_url': bundles,
        'settings': shown, 'frames': frames, 'standing': standing_instructions(), 'standing_source': STANDING_SOURCE,
    })


def rules_page(request, slug):
    """The old address of the instructions page."""
    from django.shortcuts import redirect
    return redirect(f'/moods/{slug}/instructions/' + (f'?{request.GET.urlencode()}' if request.GET else ''),
                    permanent=True)
