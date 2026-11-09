"""Merge orders by mail: posted here, fetched by githouse, answered from githouse's records.
services/merge_orders.py says why; Cryptograss:Moods/ramp/Plan, section 8."""

import json

from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .models import MergeOrder
from .services import merge_orders


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
    """What githouse fetches every few seconds: the orders it hasn't carried out yet."""
    return JsonResponse({'orders': [{'order': o.order, 'signature': o.signature}
                                    for o in merge_orders.pending()]})


@require_GET
def api_merge_order(request, order_id):
    """One order's status, as githouse's records say: waiting, merged, refused (and why) or expired."""
    order = get_object_or_404(MergeOrder, id=order_id)
    return JsonResponse({'id': str(order.id), 'repo': order.repo, 'branch': order.branch, 'by': order.by,
                         **merge_orders.status(order)})


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
    return JsonResponse({**seen, 'can': device.tier == 'key' and merge_orders.can_press(device.entity_id),
                         'mergers': list(merge_orders.mergers())})


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
