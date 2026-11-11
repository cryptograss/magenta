"""Merge orders by mail: posted here, fetched by githouse, answered from githouse's records.
services/merge_orders.py says why; Cryptograss:Moods/ramp/Plan, section 8."""

import json
import logging

from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .models import MergeOrder
from .services import merge_orders

log = logging.getLogger(__name__)


@csrf_exempt  # the order carries its own SSH signature, which githouse checks
@require_POST
def api_merge_orders(request):
    """POST {"order", "signature", "mood"?}: keep a signed order until githouse fetches it."""
    from .views_admin import locked_response
    from .views_auth import _under_limit
    if locked_response():
        return locked_response()
    # Bounds the signature checks (an ssh-keygen each) that strangers can cause; the real limit is per signer,
    # counted only once a signature checks out, so nobody uses up anyone else's turn (re, 8 Nov).
    if not _under_limit('merge-order-checks', 300):
        return JsonResponse({'error': 'too many orders; wait a minute'}, status=429)
    try:
        body = json.loads(request.body)
        kept = merge_orders.receive(body['order'], body['signature'], body.get('mood', ''), under_limit=_under_limit)
    except (ValueError, KeyError, TypeError, AttributeError):
        return JsonResponse({'error': 'expected {"order", "signature"}'}, status=400)
    except merge_orders.Invalid as e:
        return JsonResponse({'error': str(e)}, status=400)
    except merge_orders.Limited as e:
        return JsonResponse({'error': str(e)}, status=429)
    return JsonResponse({'id': str(kept.id), **merge_orders.status(kept)}, status=201)


@require_GET
def api_merge_orders_pending(request):
    """What githouse fetches every few seconds: the orders it hasn't carried out yet. Its looking is also
    when what it merged is said in the Moods the orders came from (merge_orders.announce_merged)."""
    try:
        merge_orders.announce_merged()
    except Exception:  # a line not said must never keep githouse from its orders
        import logging
        logging.getLogger(__name__).exception('merge orders: announcing what merged')
    return JsonResponse({'orders': [{'order': o.order, 'signature': o.signature}
                                    for o in merge_orders.pending()]})


@require_GET
def api_merge_order(request, order_id):
    """One order's status, as githouse's records say: waiting, merged, refused (and why) or expired."""
    order = get_object_or_404(MergeOrder, id=order_id)
    now = merge_orders.status(order)
    # Merged: the ramp's kept board is marked old now, whichever page pressed it. This answer is what turns the bar
    # ✓ and reloads the ramp behind a Mood; announce_merged would tell the ramp too, but only at githouse's next look
    # (5 s), and a reload before that drew the old board, the branch still open, until the ramp was left and
    # opened again (Justin, 10 Nov, #ramp: magent/mood-list-candidates, pressed in #interfaces).
    if now.get('status') == 'merged':
        from django.core.cache import cache
        from .services import ramp
        if cache.add(f'ramp-told:{order.id}', 1, 3 * 3600):  # once, however often it's asked after
            ramp.changed()
    return JsonResponse({'id': str(order.id), 'repo': order.repo, 'branch': order.branch, 'by': order.by, **now})


@require_GET
def api_merge_whose(request):
    """GET ?key=<public key>: the name hunter's inventory gives that key, for `magenta.sh merge` to sign as.
    Public keys only; a wrong answer only gets an order refused, since githouse checks its own signers."""
    from .services import mood_auth
    name = mood_auth.name_of_key(request.GET.get('key', ''))
    return JsonResponse({'name': name}, status=200 if name else 404)


# --- the merge button (Plan, section 10): a press on a signed-in device, signed by memory-lane ----------

def _presser(request, doing):
    """(person, None) if this device's person may press the merge button; else (None, the response)."""
    from .services import mood_auth
    device, refused = mood_auth.key_device(request, doing)
    if refused:
        return None, refused
    person = device.entity_id
    if not merge_orders.can_press(person):
        why = (f'{person} may not merge' if person not in merge_orders.mergers()
               else 'memory-lane has no merge key here')
        return None, JsonResponse({'error': why}, status=403)
    return person, None


@require_GET
def api_merge_preview(request):
    """GET ?repo=&branch=&number=: what merging it would bring, from githouse's copy, for the confirm sheet.
    {"commit", "into", "tip", "log", "stat"}; and "can": whether this device's person may press."""
    from .services import mood_auth
    device = mood_auth.device_for(request)
    if device is None:
        return JsonResponse({'error': 'sign in to merge'}, status=401)
    try:
        number = int(request.GET['number']) if request.GET.get('number') else None
        seen = merge_orders.preview(request.GET.get('repo', ''), request.GET.get('branch', ''), number)
    except ValueError:
        return JsonResponse({'error': 'number: a pull request number'}, status=400)
    except merge_orders.Invalid as e:
        return JsonResponse({'error': str(e)}, status=400)
    who = _promoting(seen) if seen.get('promote') else _who_wants(seen)
    return JsonResponse({**seen, 'can': device.tier == 'key' and merge_orders.can_press(device.entity_id),
                         'mergers': list(merge_orders.mergers()), **who})


def _promoting(seen):
    """A promotion's sheet (Justin, #ramp, 11 Nov): what it is, said plainly; no asks or 👍s, which are a branch's."""
    if seen['how'] == 'fast-forward':
        about = (f"main moves up to production: {seen['behind']} commits, a fast-forward. main becomes exactly "
                 f"production at {seen['commit'][:12]}.")
    else:
        own = f"{seen['own']} commit{'s' if seen['own'] != 1 else ''}"
        about = (f"main has {own} of its own that production hasn't. production takes them into its history first, "
                 f"with none of its files changed, so nothing deploys differently; then main fast-forwards to it "
                 f"({seen['behind']} commits). From then on, main and production are the same commit, and the next "
                 f"promotion is a fast-forward.")
    return {'asks': [], 'thumbs': [], 'title': f"Promote {seen['repo']}: main up to production", 'about': about}


def _who_wants(seen):
    """For the sheet (Justin, 9 Nov): {'asks': the open 🪢 asks naming it, a dot each ({'by', 'mood', 'id',
    'colour'}); 'thumbs': its 👍s ({'by', 'at', 'current'}: on the commit about to merge, or an older one);
    'title' and 'about': what it is, its title and the start of its description (Justin, 10 Nov: "more important
    than the commit messages"). A branch without a pull request has no description: its newest commit's message
    stands in, as the ramp takes its title from that commit."""
    from .services import ramp, thumbs
    try:
        item = next((i for i in ramp.board(stale_ok=True).get('open', [])
                     if i.get('repo') == seen['repo'] and i.get('branch') == seen['branch']), None)
        asks = [{k: a[k] for k in ('by', 'mood', 'id', 'colour')} for a in (item or {}).get('asks', [])]
        liked = [{'by': t['by'], 'at': t['at'].isoformat(), 'current': t['current']}
                 for t in thumbs.of(seen['repo'], seen['branch'], seen['commit'])]
    except Exception as e:  # noqa: BLE001 -- a nicety: the sheet merges without it
        log.warning('merge preview: asks and 👍s: %s', e)
        item, asks, liked = None, [], []
    title, about = (item or {}).get('title', ''), ramp.description_start((item or {}).get('description', ''))
    if not (title and about):
        subject, body = _head_message(seen)
        title, about = title or subject, about or ramp.description_start(body)
    return {'asks': asks, 'thumbs': liked, 'title': title, 'about': about}


def _head_message(seen):
    """(subject, body) of the commit about to merge, from githouse's copy; ('', '') if it can't be read."""
    from .services import githouse
    try:
        held = githouse.repository(seen['repo'])
        said = githouse._git(held, 'log', '-1', '--format=%s%x00%b', seen['commit']) if held else ''
    except Exception as e:  # noqa: BLE001 -- a nicety, as above
        log.warning('merge preview: head message: %s', e)
        return '', ''
    subject, _, body = said.partition('\0')
    return subject.strip(), body.strip()


@require_POST
def api_merge_press(request):
    """POST {"repo", "branch", "commit", "into", "mood"?}: merge exactly that commit, as this device's person.
    memory-lane signs the order with its own key, for them; githouse checks everything again."""
    from .views_admin import locked_response
    from .views_auth import _under_limit
    if locked_response():
        return locked_response()
    person, refused = _presser(request, 'merge')
    if refused:
        return refused
    if not _under_limit(f'merge-press:{person}', 10):
        return JsonResponse({'error': 'ten merges a minute is plenty; wait a minute'}, status=429)
    try:
        body = json.loads(request.body)
        repo, branch, commit, into = (str(body[k]) for k in ('repo', 'branch', 'commit', 'into'))
        # The press is for what the sheet showed: the commit and target must still be what githouse has.
        now = merge_orders.preview(repo, branch, body.get('number'))
        if (now['commit'], now['into']) != (commit, into):
            return JsonResponse({'error': f'{repo}:{branch} moved since you looked; look again'}, status=409)
        kept = merge_orders.press(person, repo, branch, commit, into, body.get('mood', ''))
    except (ValueError, KeyError, TypeError, AttributeError):
        return JsonResponse({'error': 'expected {"repo", "branch", "commit", "into"}'}, status=400)
    except merge_orders.Invalid as e:
        return JsonResponse({'error': str(e)}, status=400)
    return JsonResponse({'id': str(kept.id), **merge_orders.status(kept)}, status=201)


# --- the 👍 (Plan, section 8): "I like this", from any person's signed-in device; a signal, never a merge -------

@require_POST
def api_ramp_thumb(request):
    """POST {"repo", "branch", "commit", "number"?}: give that commit of the work a 👍 as this device's person,
    or take theirs back. The commit must be the head githouse has now, so a 👍 is on what the presser saw.
    {"on", "liked": names on the head now, "thumbs"}. Any signed-in person may, at either tier."""
    from .services import mood_auth, thumbs
    from .views_admin import locked_response
    from .views_auth import _under_limit
    if locked_response():
        return locked_response()
    device = mood_auth.device_for(request)
    if device is None:
        return JsonResponse({'error': 'sign in to 👍'}, status=401)
    if not device.entity.is_biological_human:
        return JsonResponse({'error': 'a 👍 is a person\'s'}, status=403)
    person = device.entity_id
    if not _under_limit(f'thumb:{person}', 30):
        return JsonResponse({'error': 'thirty a minute is plenty; wait a minute'}, status=429)
    try:
        body = json.loads(request.body)
        repo, branch, commit = (body[k] for k in ('repo', 'branch', 'commit'))
        if not all(isinstance(v, str) for v in (repo, branch, commit)):
            raise TypeError
        number = int(body['number']) if body.get('number') else None
        head = thumbs.head_of(repo, branch, number)
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        if isinstance(e, thumbs.Invalid):
            return JsonResponse({'error': str(e)}, status=400)
        return JsonResponse({'error': 'expected {"repo", "branch", "commit"}'}, status=400)
    if head != commit:
        return JsonResponse({'error': f'{repo}:{branch} moved since you looked; look again', 'head': head}, status=409)
    on = thumbs.toggle(person, repo, branch, commit)
    found = thumbs.of(repo, branch, head)
    return JsonResponse({'on': on, 'liked': [t['by'] for t in found if t['current']],
                         'thumbs': [{**t, 'at': t['at'].isoformat()} for t in found]})


# --- a page for each piece of work (Plan, section 9): a Tiding, with the work above its comments -------------

@require_POST
def api_ramp_work(request):
    """POST {"repo", "branch"}: this work's page, made now if it has none. {"id": the Tiding's}."""
    from .services import mood_auth, work_pages as work
    from .views_admin import locked_response
    from .views_auth import _under_limit
    if locked_response():
        return locked_response()
    device = mood_auth.device_for(request)
    if device is None:
        return JsonResponse({'error': 'sign in to open its page'}, status=401)
    if not device.entity.is_biological_human:
        return JsonResponse({'error': 'a person opens a page'}, status=403)
    if not _under_limit(f'work-page:{device.entity_id}', 10):
        return JsonResponse({'error': 'ten a minute is plenty; wait a minute'}, status=429)
    try:
        body = json.loads(request.body)
        repo, branch = body['repo'], body['branch']
        if not (isinstance(repo, str) and isinstance(branch, str)):
            raise TypeError
    except (ValueError, KeyError, TypeError):
        return JsonResponse({'error': 'expected {"repo", "branch"}'}, status=400)
    try:
        tiding = work.open_page(device.entity, repo, branch)
    except work.Invalid as e:
        return JsonResponse({'error': str(e)}, status=e.status)
    return JsonResponse({'id': str(tiding.id)})


def _fragment(request, template, build):
    from django.http import HttpResponse
    from django.shortcuts import render
    from .services import work_pages as work
    try:
        found = build()
    except work.Invalid as e:
        return HttpResponse(str(e), status=e.status, content_type='text/plain; charset=utf-8')
    response = render(request, template, found)
    response['Cache-Control'] = 'public, max-age=3600'  # by commit: what's there never changes
    return response


@require_GET
def api_ramp_diff(request):
    """GET ?repo=&base=&head=&path=: one file's changes, drawn for the work's page; every line an address."""
    from .services import work_pages as work
    q = request.GET
    return _fragment(request, 'conversations/_work_diff.html',
                     lambda: {'d': work.diff_of(q.get('repo', ''), q.get('base', ''), q.get('head', ''), q.get('path', ''))})


@require_GET
def api_ramp_file(request):
    """GET ?repo=&commit=&path=: a whole file at a commit, every line an address."""
    from .services import work_pages as work
    q = request.GET
    return _fragment(request, 'conversations/_work_file.html',
                     lambda: {'f': work.file_at(q.get('repo', ''), q.get('commit', ''), q.get('path', ''))})
