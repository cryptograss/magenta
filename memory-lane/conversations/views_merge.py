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
