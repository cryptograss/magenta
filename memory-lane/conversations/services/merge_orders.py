"""Merge orders by mail (Cryptograss:Moods/ramp/Plan, section 8, 8 Nov 2026).

A person signs a merge order with their SSH key (`magenta.sh merge`, tools/mood_merge.py) and posts it
here; githouse on maybelle fetches what's waiting every few seconds and carries each out itself, checking
the signature, the nonce and the brakes (maybelle-config's shared/githouse/githouse.py). Only Justin has
ssh to maybelle, as root; skyler doesn't, and no phone does, so the orders need a carrier, and memory-lane
is that and nothing more. It does screen them: only an order its claimed signer's key signed (by hunter's
inventory) gets in, so strangers can't fill the mailbox; githouse still checks everything itself. The signature makes the carrier irrelevant (re, 7 Nov): memory-lane can delay or
drop an order, never forge one. What an order claims (repo, branch, by) is kept for showing, unchecked.

What came of an order is read back from githouse's own records, beside the repositories (mounted
read-only): .merges/<nonce>.json for an order that counted as signed, .merges/mailed/<sha256>.json for one
refused before that.
"""

import hashlib
import json
import os
import re
import subprocess
import tempfile
import time

from django.conf import settings

from .githouse import root

NAMESPACE = 'magenta-merge'          # githouse.MERGE_NAMESPACE
HEADER = 'githouse merge order v1'   # githouse.ORDER_HEADER
FIELDS = ('repo', 'branch', 'commit', 'into', 'by', 'nonce', 'expires')
LIFE_MAX = 2 * 3600                  # githouse.ORDER_LIFE_MAX: an order lasts at most this
AT_ONCE = 20                         # orders githouse is handed in one look
# The merge button (Plan, section 10, 8 Nov): a phone has no key, so memory-lane signs with its own, naming
# whose press it was in a 'for' line (githouse.DEVICE_SIGNER, FOR_FIELD). githouse merges it only for a person
# who may merge in their own right, and asks the brakes about that person.
DEVICE_SIGNER = 'memory-lane'
FOR_FIELD = 'for'
# Where each repository's work goes: the branch its deploys come from (tools/mood_merge.py keeps the same
# table, standard library only). Not guessed from which branches exist: magenta's production is long dead.
DEPLOYS_FROM = {'magenta': 'main', 'maybelle-config': 'production', 'pickipedia': 'production',
                'arthel': 'production', 'rabbithole': 'main', 'pickipedia-mcp': 'master'}
_BRANCH = re.compile(r'[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*')
_FORMS = {'repo': r'[a-z0-9._-]{1,64}', 'branch': r'[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*', 'commit': r'[0-9a-f]{40}',
          'into': r'[A-Za-z0-9._/-]{1,100}', 'by': r'[a-z][a-z0-9-]{0,31}', 'nonce': r'[0-9a-f]{32,64}',
          'expires': r'\d{1,12}'}


class Invalid(Exception):
    """Not an order memory-lane will carry, and why."""


class Limited(Exception):
    """Too many orders from one signer this minute."""


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
        form = _FORMS['by'] if key == FOR_FIELD else _FORMS.get(key)
        if not sep or form is None or key in found or not re.fullmatch(form, value):
            raise Invalid(f'an order has one line for each of {", ".join(FIELDS)}, each well formed, and nothing else')
        found[key] = value
    if not set(FIELDS) <= set(found):
        raise Invalid(f'missing: {", ".join(sorted(set(FIELDS) - set(found)))}')
    if (found['by'] == DEVICE_SIGNER) != (FOR_FIELD in found):
        raise Invalid(f"only an order by {DEVICE_SIGNER} says whose press it was")
    found['expires'] = int(found['expires'])
    return found


def signed_by(order, signature, by):
    """Whether `by`'s key, as allowed_signers (hunter's inventory) lists it, signed this order in the merge
    namespace. A screen, not the check: githouse checks against its own signers, of only those who may merge.
    It keeps strangers out of the mailbox (re, 8 Nov), so junk can't fill it or use up anyone's turn."""
    from . import mood_auth
    key = mood_auth.public_key_of(by)
    if not key:
        return False
    with tempfile.TemporaryDirectory() as d:
        signers, sig = os.path.join(d, 'signers'), os.path.join(d, 'sig')
        with open(signers, 'w') as f:
            f.write(f'{by} namespaces="{NAMESPACE}" {key}\n')
        with open(sig, 'w') as f:
            f.write(signature)
        try:
            done = subprocess.run(['ssh-keygen', '-Y', 'verify', '-f', signers, '-I', by, '-n', NAMESPACE, '-s', sig],
                                  input=order, capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            return False
    return done.returncode == 0


def receive(order, signature, mood='', now=None, under_limit=None):
    """Keep a signed order for githouse; the MergeOrder. under_limit(bucket, per_minute), if given, is asked
    once the signature checks out, per signer: a stranger can't use up anyone else's turn."""
    from conversations.models import MergeOrder
    now = int(time.time() if now is None else now)
    if not isinstance(order, str) or not isinstance(signature, str) or len(order) > 1000 or len(signature) > 4000:
        raise Invalid('expected an order and its signature, as text')
    if '-----BEGIN SSH SIGNATURE-----' not in signature:
        raise Invalid('the signature is ssh-keygen -Y sign\'s, armored')
    fields = parse(order)
    if not now <= fields['expires'] <= now + LIFE_MAX + 60:
        raise Invalid(f'an order expires within {LIFE_MAX // 3600} hours, and not before it is sent')
    if not signed_by(order, signature, fields['by']):
        raise Invalid(f"not signed by {fields['by']}'s key, in {NAMESPACE}")
    if under_limit and not under_limit(f"merge-order:{fields['by']}", 20):
        raise Limited(f"{fields['by']} has sent 20 orders this minute; wait a minute")
    return MergeOrder.objects.create(order=order, signature=signature, nonce=fields['nonce'], repo=fields['repo'],
                                     branch=fields['branch'], by=fields['by'], expires=fields['expires'],
                                     mood=str(mood or '')[:100])


def mergers():
    """Who may press the merge button: githouse_mergers, as deployed (settings.GITHOUSE_MERGERS)."""
    return tuple(getattr(settings, 'GITHOUSE_MERGERS', ()) or ())


def _key():
    """memory-lane's merge key, as text, or '' if it has none. The vault may hold it base64'd or not."""
    import base64
    path = getattr(settings, 'MERGE_KEY_PATH', '')
    try:
        text = open(path).read().strip() if path else ''
    except OSError:
        return ''
    if text and 'BEGIN OPENSSH PRIVATE KEY' not in text:
        try:
            text = base64.b64decode(text, validate=False).decode().strip()
        except (ValueError, UnicodeDecodeError):
            return ''
    return text if 'BEGIN OPENSSH PRIVATE KEY' in text else ''


def can_press(person):
    """Whether the button is offered to this person: a merger, and memory-lane has its key."""
    return bool(person) and person in mergers() and bool(_key())


def preview(repo, branch='', number=None):
    """What merging this work would bring, from githouse's copy: {'repo', 'branch', 'commit', 'into', 'tip',
    'log', 'stat'}. The commit is the branch's head here, or its pull request's head (a branch pushed only to
    GitHub). What's shown is memory-lane's word: the trade Justin chose for merging from a phone (7 Nov)."""
    from .githouse import _git, _sha, repository
    held = repository(repo)
    if held is None:
        raise Invalid(f'githouse holds no repository {repo}')
    base = ''
    if number:  # a pull request: its branch and base as its card has them (ramp.board, cached)
        from . import ramp
        card = next((i for i in ramp.board(stale_ok=True).get('open', []) if i.get('repo') == repo and i.get('number') == int(number)),
                    None)
        if card is None:
            raise Invalid(f'{repo}#{number} is not an open pull request here')
        branch, base = branch or card.get('branch', ''), card.get('base', '')
    if not _BRANCH.fullmatch(branch or '') or branch in ('main', 'master', 'production'):
        raise Invalid('which branch? (not one that is merged into)')
    if not number and not _sha(held, f'refs/heads/{branch}'):
        # Named repo:branch, but pushed only to GitHub, with a pull request (Justin, 10 Nov: "githouse has no
        # maybelle-config:magent/resdtp-releases"; 18 open then): githouse holds its head as refs/pull/<n>/head, and
        # merges it by reading GitHub's head (githouse.merge). Its card says which pull request it is.
        from . import ramp
        card = next((i for i in ramp.board(stale_ok=True).get('open', [])
                     if i.get('repo') == repo and i.get('branch') == branch and i.get('number')), None)
        if card is not None:
            number, base = int(card['number']), card.get('base', '')
    commit = _sha(held, f'refs/heads/{branch}') or (_sha(held, f'refs/pull/{int(number)}/head') if number else None)
    if not commit:
        raise Invalid(f'githouse has no {repo}:{branch}')
    into = base or DEPLOYS_FROM.get(repo) or next((b for b in ('production', 'main', 'master')
                                                   if _sha(held, f'refs/heads/{b}')), '')
    tip = _sha(held, f'refs/heads/{into}') if into else None
    if not tip:
        raise Invalid(f'{repo} has no {into or "branch to merge into"} here')
    if _git(held, 'rev-list', '--count', f'{tip}..{commit}').strip() == '0':  # nothing it has that the target hasn't
        raise Invalid(f'{repo}:{branch} ({commit[:8]}) is in {into} already')
    return {'repo': repo, 'branch': branch, 'number': int(number) if number else None, 'commit': commit,
            'into': into, 'tip': tip,
            'log': _git(held, 'log', '--no-decorate', '--format=%h %an: %s', f'{tip}..{commit}'),
            'stat': _git(held, 'diff', '--stat', f'{tip}...{commit}')}


def press(person, repo, branch, commit, into, mood='', now=None):
    """The merge button, pressed: an order signed with memory-lane's key for this person, in the mailbox.
    The MergeOrder. githouse checks it all again: the signature, that the person may merge, the brakes."""
    from conversations.models import MergeOrder
    from . import settings as knobs
    now = int(time.time() if now is None else now)
    if person not in mergers():
        raise Invalid(f'{person} may not merge')
    if knobs.banned(person):
        raise Invalid(f'{person} is banned')
    key = _key()
    if not key:
        raise Invalid('memory-lane has no merge key here')
    fields = {'repo': repo, 'branch': branch, 'commit': commit, 'into': into, 'by': DEVICE_SIGNER,
              'nonce': os.urandom(16).hex(), 'expires': str(now + 3600), FOR_FIELD: person}
    text = HEADER + '\n' + ''.join(f'{k}: {fields[k]}\n' for k in FIELDS + (FOR_FIELD,))
    parsed = parse(text)
    with tempfile.TemporaryDirectory() as d:
        key_file, order_file = os.path.join(d, 'key'), os.path.join(d, 'order')
        with open(os.open(key_file, os.O_WRONLY | os.O_CREAT, 0o600), 'w') as f:
            f.write(key + '\n')
        with open(order_file, 'w') as f:
            f.write(text)
        done = subprocess.run(['ssh-keygen', '-Y', 'sign', '-q', '-f', key_file, '-n', NAMESPACE, order_file],
                              capture_output=True, text=True, timeout=10)
        if done.returncode != 0:
            raise Invalid(f'signing failed: {done.stderr.strip()[:200]}')
        with open(order_file + '.sig') as f:
            signature = f.read()
    return MergeOrder.objects.create(order=text, signature=signature, nonce=parsed['nonce'], repo=repo,
                                     branch=branch, by=DEVICE_SIGNER, pressed=person, expires=parsed['expires'],
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


def announce_merged(now=None):
    """A line in the Mood each merge was pressed or ordered from, once githouse says it merged: "✓
    magenta:re/foo · 1a2b3c4d merged by justin" (todo.MERGED_SOURCE, the line GitHub's webhook writes for a
    pull request on a Mood's list). Asked every time githouse looks at the mailbox, so it lands within seconds,
    whether or not anyone kept the sheet open. Each order said once. The number of lines written."""
    import uuid
    from django.utils import timezone
    from conversations.models import ConversationParticipant, Message, MergeOrder, Mood
    from .todo import MERGED_SOURCE
    now = int(time.time() if now is None else now)
    said = 0
    for order in MergeOrder.objects.filter(expires__gte=now - LIFE_MAX, announced_at__isnull=True) \
            .exclude(mood='').iterator():
        found = outcome(order)
        if not found or found.get('status') != 'merged':
            continue
        mood = Mood.objects.filter(slug=order.mood).first()
        # Claimed in the database, so of memory-lane's workers asked at once, one says it.
        if mood is None or not MergeOrder.objects.filter(id=order.id, announced_at__isnull=True) \
                .update(announced_at=timezone.now()):
            continue
        system, _ = ConversationParticipant.objects.get_or_create(name='system', defaults={'participant_type': 'system'})
        fields = parse(order.order)
        merge = str(found.get('merge') or '')
        Message.objects.create(id=uuid.uuid4(), sender=system, mood=mood, source_file=MERGED_SOURCE,
                               timestamp=int(time.time() * 1000),
                               content={'type': 'merged', 'pull': f'{order.repo}:{order.branch}',
                                        'title': f"{order.repo}:{order.branch} into {fields['into']}",
                                        'commit': merge[:40], 'by': order.pressed or order.by,
                                        'commit_url': f'https://github.com/cryptograss/{order.repo}/commit/{merge}'
                                        if merge else ''})
        # githouse's "not merged yet" is kept a minute (githouse.UNMERGED_FOR): drop it, or a list looked at in the
        # minute before the press is built again from it and the item waits that minute out (re, 10 Nov).
        from django.core.cache import cache
        cache.delete_many([f'githouse:branch:{order.repo}:{order.branch}', f'githouse:closed:{order.repo}:{order.branch}'])
        said += 1
    if said:
        from . import ramp, todo
        ramp.changed()  # the ramp drops what merged
        todo.forget_all()  # and a to-do item linking its page ticks now, not in a minute
    return said
