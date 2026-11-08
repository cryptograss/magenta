"""Merge orders by mail (Cryptograss:Moods/ramp/Plan, section 8, 8 Nov 2026).

A person signs a merge order with their SSH key (`magenta.sh merge`, tools/mood_merge.py) and posts it
here; githouse on maybelle fetches what's waiting every few seconds and carries each out itself, checking
the signature, the nonce and the brakes (maybelle-config's shared/githouse/githouse.py). Only Justin has
ssh to maybelle, as root; skyler doesn't, and no phone does, so the orders need a carrier, and memory-lane
is that and nothing more. The signature makes the carrier irrelevant (re, 7 Nov): memory-lane can delay or
drop an order, never forge one. What an order claims (repo, branch, by) is kept for showing, unchecked.

What came of an order is read back from githouse's own records, beside the repositories (mounted
read-only): .merges/<nonce>.json for an order that counted as signed, .merges/mailed/<sha256>.json for one
refused before that.
"""

import hashlib
import json
import re
import time

from .githouse import root

HEADER = 'githouse merge order v1'   # githouse.ORDER_HEADER
FIELDS = ('repo', 'branch', 'commit', 'into', 'by', 'nonce', 'expires')
LIFE_MAX = 2 * 3600                  # githouse.ORDER_LIFE_MAX: an order lasts at most this
AT_ONCE = 20                         # orders githouse is handed in one look
_FORMS = {'repo': r'[a-z0-9._-]{1,64}', 'branch': r'[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*', 'commit': r'[0-9a-f]{40}',
          'into': r'[A-Za-z0-9._/-]{1,100}', 'by': r'[a-z][a-z0-9-]{0,31}', 'nonce': r'[0-9a-f]{32,64}',
          'expires': r'\d{1,12}'}


class Invalid(Exception):
    """Not an order memory-lane will carry, and why."""


def parse(text):
    """{field: value} of an order's text, as githouse reads it; Invalid if it isn't one."""
    lines = text.split('\n')
    if lines[0] != HEADER:
        raise Invalid('not a merge order')
    found = {}
    for line in lines[1:]:
        if not line:
            continue
        key, sep, value = line.partition(': ')
        if not sep or key not in FIELDS or key in found or not re.fullmatch(_FORMS[key], value):
            raise Invalid(f'an order has one line for each of {", ".join(FIELDS)}, each well formed, and nothing else')
        found[key] = value
    if set(found) != set(FIELDS):
        raise Invalid(f'missing: {", ".join(sorted(set(FIELDS) - set(found)))}')
    found['expires'] = int(found['expires'])
    return found


def receive(order, signature, mood='', now=None):
    """Keep a signed order for githouse; the MergeOrder."""
    from conversations.models import MergeOrder
    now = int(time.time() if now is None else now)
    if not isinstance(order, str) or not isinstance(signature, str) or len(order) > 1000 or len(signature) > 4000:
        raise Invalid('expected an order and its signature, as text')
    if '-----BEGIN SSH SIGNATURE-----' not in signature:
        raise Invalid('the signature is ssh-keygen -Y sign\'s, armored')
    fields = parse(order)
    if not now <= fields['expires'] <= now + LIFE_MAX + 60:
        raise Invalid(f'an order expires within {LIFE_MAX // 3600} hours, and not before it is sent')
    return MergeOrder.objects.create(order=order, signature=signature, nonce=fields['nonce'], repo=fields['repo'],
                                     branch=fields['branch'], by=fields['by'], expires=fields['expires'],
                                     mood=str(mood or '')[:100])


def _record(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def outcome(order):
    """What githouse made of this order, from its records: {'status', 'why'?, 'merge'?}, or None if nothing yet."""
    merges = root() / '.merges'
    kept = _record(merges / f'{order.nonce}.json')
    if kept is not None:
        if kept.get('order') == order.order and kept.get('signature') == order.signature:
            return {'status': kept.get('outcome'), 'why': kept.get('why'), 'merge': kept.get('merge')}
        return {'status': 'refused', 'why': 'another order with the same nonce was carried out first'}
    seen = hashlib.sha256(f'{order.order}\0{order.signature}'.encode()).hexdigest()
    mailed = _record(merges / 'mailed' / f'{seen}.json')
    if mailed is not None:
        return {'status': mailed.get('outcome'), 'why': mailed.get('why')}
    return None


def status(order, now=None):
    """outcome(), or 'waiting' while it can still be carried out, or 'expired'."""
    now = int(time.time() if now is None else now)
    found = outcome(order)
    if found:
        return {k: v for k, v in found.items() if v is not None}
    if order.expires < now:
        return {'status': 'expired', 'why': "githouse didn't carry it out before it expired"}
    return {'status': 'waiting'}


def pending(now=None):
    """The orders githouse hasn't carried out and can still: oldest first, AT_ONCE at most."""
    from conversations.models import MergeOrder
    now = int(time.time() if now is None else now)
    waiting = []
    # Every unexpired order, not the oldest 200: ones githouse has answered stay unexpired for up to two hours,
    # and 200 of them ahead of a real order hid it from githouse until they expired (re, 8 Nov).
    for order in MergeOrder.objects.filter(expires__gte=now).order_by('received_at').iterator():
        if outcome(order) is None:
            waiting.append(order)
        if len(waiting) == AT_ONCE:
            break
    return waiting
