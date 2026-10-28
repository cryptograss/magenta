"""Every bundle of instructions an agent's turn was given, and each one rebuilt to read.

The runner (poller/mood_poller.py MoodPoller.instructions_for) records each bundle it
hands a harness: its parts, the PickiPedia revisions it was made of, its
sha256, and when it was asked for, made and delivered (BundleRecord). Reading
is open, as the Moods are: a record holds only page titles, revision numbers
and the bundle's own wrapper words, and the instructions are public pages. Writing
takes the agent's runner key.

    GET  /api/bundles/?agent=re&mood=magent-self&before=<iso>&limit=50   newest first
    POST /api/bundles/                                                    a runner records one
    GET  /api/bundles/<id>/                                               one, with its parts
    GET  /api/bundles/<id>/text/                                          rebuilt from the wiki and checked
"""

import json
import re
import uuid

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET

from .models import BundleRecord
from .views_runner import runner_agent

MAX_PARTS_CHARS = 400_000  # a bundle is an argument to a harness, which Linux caps far below this
SHA256 = re.compile(r'^[0-9a-f]{64}$')


def summary(r):
    stamps = r.stamps or {}
    return {'id': str(r.id), 'agent': r.agent, 'mood': r.mood, 'session_id': r.session_id, 'harness': r.harness,
            'requested_at': r.requested_at.isoformat(), 'produced_at': r.produced_at and r.produced_at.isoformat(),
            'delivered_at': r.delivered_at and r.delivered_at.isoformat(), 'block': r.block, 'sha256': r.sha256,
            'pages': r.pages(), 'degraded': stamps.get('degraded') or [], 'error': r.error, 'commit': r.commit}


def full(r):
    return {**summary(r), 'parts': r.parts, 'transform': r.transform, 'stamps': r.stamps}


def when(value, name, required=False):
    if value in (None, ''):
        if required:
            raise ValueError(f'{name} is required')
        return None
    found = parse_datetime(str(value))
    if found is None:
        raise ValueError(f'{name}: not a time')
    return found


def parsed(body, agent):
    """The BundleRecord fields from a runner's body, or ValueError saying what's wrong."""
    try:
        record_id = uuid.UUID(str(body.get('id')))
    except ValueError:
        raise ValueError('id: not a uuid') from None
    if body.get('agent') != agent:
        raise ValueError(f"agent: this key speaks for {agent}")
    parts = body.get('parts')
    if not isinstance(parts, list) or not parts:
        raise ValueError('parts: a list')
    size = 0
    for part in parts:
        if isinstance(part, str):
            size += len(part)
        elif not (isinstance(part, dict) and isinstance(part.get('page'), str)
                  and isinstance(part.get('revision'), int) and not isinstance(part.get('revision'), bool)):
            raise ValueError("parts: each a string or {'page', 'revision'}")
    if size > MAX_PARTS_CHARS:
        raise ValueError('parts: too long')
    if not SHA256.match(str(body.get('sha256', ''))):
        raise ValueError('sha256: 64 hex digits')
    block = body.get('block')
    if block is not None and (not isinstance(block, int) or isinstance(block, bool)):
        raise ValueError('block: a number')
    stamps = body.get('stamps') or {}
    if not isinstance(stamps, dict):
        raise ValueError('stamps: an object')
    return {'id': record_id, 'agent': agent, 'mood': str(body.get('mood') or '')[:120],
            'session_id': str(body.get('session_id') or '')[:80], 'harness': str(body.get('harness') or '')[:60],
            'requested_at': when(body.get('requested_at'), 'requested_at', required=True),
            'produced_at': when(body.get('produced_at'), 'produced_at'),
            'delivered_at': when(body.get('delivered_at'), 'delivered_at'),
            'block': block, 'sha256': body['sha256'], 'parts': parts,
            'transform': str(body.get('transform') or '')[:200], 'stamps': stamps,
            'commit': str(body.get('commit') or '')[:40], 'error': str(body.get('error') or '')[:300]}


@csrf_exempt
def api_bundles(request):
    if request.method == 'POST':
        if not getattr(settings, 'MOOD_RUNNER_KEYS', {}):
            return JsonResponse({'error': 'no runners are configured'}, status=503)
        agent = runner_agent(request)
        if agent is None:
            return JsonResponse({'error': 'unauthorized'}, status=401)
        try:
            body = json.loads(request.body or b'{}')
            fields = parsed(body if isinstance(body, dict) else {}, agent)
        except (ValueError, json.JSONDecodeError) as e:
            return JsonResponse({'error': str(e)}, status=400)
        record, made = BundleRecord.objects.get_or_create(id=fields.pop('id'), defaults=fields)
        if not made and record.agent != agent:
            return JsonResponse({'error': 'that id is taken'}, status=409)
        return JsonResponse({'id': str(record.id), 'recorded': made}, status=201 if made else 200)
    if request.method != 'GET':
        return JsonResponse({'error': 'GET or POST'}, status=405)
    found = BundleRecord.objects.all()
    for name in ('agent', 'mood', 'session_id', 'sha256'):
        if request.GET.get(name):
            found = found.filter(**{name: request.GET[name]})
    try:
        before = when(request.GET.get('before'), 'before')
        limit = max(1, min(int(request.GET.get('limit', 50)), 200))
    except ValueError as e:
        return JsonResponse({'error': str(e)}, status=400)
    if before:
        found = found.filter(requested_at__lt=before)
    rows = list(found[:limit])
    return JsonResponse({'bundles': [summary(r) for r in rows],
                         'before': rows[-1].requested_at.isoformat() if len(rows) == limit else None})


def record_or_404(record_id):
    try:
        return BundleRecord.objects.filter(id=uuid.UUID(str(record_id))).first()
    except ValueError:
        return None


@require_GET
def api_bundle(request, record_id):
    record = record_or_404(record_id)
    if record is None:
        return JsonResponse({'error': 'no such bundle'}, status=404)
    return JsonResponse(full(record))


@require_GET
def api_bundle_text(request, record_id):
    """The bundle rebuilt from PickiPedia's revisions, and whether its sha256 is the one it was given
    with. ?format=text: the text alone (or 409, saying why it can't be had)."""
    from django.core.cache import cache

    from poller.instructions import rebuilt

    from .services.mood_view import pickipedia_url
    record = record_or_404(record_id)
    if record is None:
        return JsonResponse({'error': 'no such bundle'}, status=404)
    key = f'bundle-rebuilt:{record.id}'
    again = cache.get(key)
    if again is None:
        try:
            again = rebuilt(full(record), api=f'{pickipedia_url()}/api.php')
        except Exception as e:  # noqa: BLE001 -- PickiPedia down or slow: say so, keep nothing
            return JsonResponse({'error': f"PickiPedia couldn't be read ({type(e).__name__})"}, status=502)
        cache.set(key, again, 3600)
    if request.GET.get('format') == 'text':
        if again['text'] is None:
            return HttpResponse(f'The wiki no longer has revision(s) {", ".join(map(str, again["missing"]))}.\n',
                                status=409, content_type='text/plain; charset=utf-8')
        response = HttpResponse(again['text'], content_type='text/plain; charset=utf-8')
        response['X-Bundle-Matches'] = 'yes' if again['matches'] else 'no'
        return response
    return JsonResponse({'id': str(record.id), 'given_sha256': record.sha256, **again})
