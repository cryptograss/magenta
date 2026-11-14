"""The shared announcement bar under the nav (Justin, #ramp, 14 Nov 2026).

Everyone's githouse activity (merges and promotions on their way, merged, or refused — whether pressed on a
page or signed with `magenta.sh merge`) and everyone's newly completed to-dos (excluding merges, which the
githouse pipeline shows already, and deploys, which each Mood shows as a line). Served on `/api/moods/live/`
so every open browser — merger, teammate or visitor — sees the same one-row ticker.
"""

from datetime import timedelta
import hashlib
import os
import re
import threading
import time
import urllib.parse

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

GOING_FOR = 120       # seconds a waiting merge order spins in the bar
STALLED_FOR = 240     # up to this age, a still-waiting order shows the two-minute warning
OK_FOR = 60           # seconds a green ✓ (merge or completed to-do) stays in the live feed
ERR_FOR = 180         # seconds a refused/expired merge stays in the live feed (an open tab keeps it until ×)
LOOKBACK = 600        # seconds of MergeOrder rows inspected

EVENTS_KEY = 'ticker:todos'
STATE_KEY = 'todo:state:{slug}'
EXCLUDED_TYPES = ('merge', 'deploy')

_MEM_LOCK = threading.Lock()
_SPACES = re.compile(r'\s+')


def _norm(text):
    return _SPACES.sub(' ', (text or '').strip()).lower()


def shown_who(raw):
    """How a person or emergent's name is written in the bar: their PickiPedia display name where mapped
    (justin -> JMyles, skyler -> SkymanJenkins), lowercase for emergents (Magent -> magent), else as given."""
    raw = (raw or '').strip()
    if not raw:
        return ''
    try:
        from . import wiki_auth
        names = wiki_auth.names()
        aliases = {wiki.lower(): here for here, wiki in names.items()}
        canon = aliases.get(raw.lower(), raw.lower())
        if canon in names:
            return names[canon]
    except Exception:  # noqa: BLE001
        canon = raw.lower()
    try:
        from conversations.models import ThinkingEntity
        if ThinkingEntity.objects.filter(name=canon).exists():
            return canon
    except Exception:  # noqa: BLE001
        pass
    return raw


def _is_todo_bot(user):
    bot = (getattr(settings, 'PICKIPEDIA_TODO_BOT_USER', '') or '').strip()
    return bool(bot and user and user.strip().lower() == bot.lower())


def _state_of(items):
    open_tasks, done_tasks, open_links, done_links = [], [], [], []
    for item in items or ():
        nt = _norm(item.get('task'))
        lnk = (item.get('link') or '').strip()
        if item.get('done'):
            if nt:
                done_tasks.append(nt)
            if lnk:
                done_links.append(lnk)
        else:
            if nt:
                open_tasks.append(nt)
            if lnk:
                open_links.append(lnk)
    return {'open_tasks': open_tasks, 'done_tasks': done_tasks, 'open_links': open_links, 'done_links': done_links}


def has_state(slug):
    return cache.get(STATE_KEY.format(slug=slug)) is not None


def previous_from_wiki(title, slug, http=None):
    """When a Mood's to-do page is saved while its state cache is cold (right after a server restart), read its
    parent revision once so only the item just marked done is announced, never old history."""
    try:
        import requests
        from . import todo
        from .mood_view import pickipedia_url
        from .todo_types import with_types
        http = http or requests
        r = http.get(f'{pickipedia_url()}/api.php', timeout=5, headers={'User-Agent': 'memory-lane (magenta; ticker)'},
                     params={'action': 'query', 'prop': 'revisions', 'titles': title, 'rvprop': 'content',
                             'rvslots': 'main', 'rvlimit': 2, 'formatversion': 2, 'format': 'json'})
        revs = (((r.json().get('query') or {}).get('pages') or [{}])[0].get('revisions')) or []
        if len(revs) < 2:
            return []  # brand-new page: every done item on it is new if any, or none were open
        parent_text = ((revs[1].get('slots') or {}).get('main') or {}).get('content') or ''
        return with_types(todo.with_merges(todo.parse(parent_text), http))
    except Exception:  # noqa: BLE001 -- unreachable or unreadable: fall back to quiet baseline
        return None


def _with_events_lock(fn):
    """Mutate EVENTS_KEY atomically across gunicorn workers sharing FileBasedCache."""
    loc = (getattr(settings, 'CACHES', {}).get('default') or {}).get('LOCATION') or ''
    with _MEM_LOCK:
        if not loc or not os.path.isdir(loc):
            return fn()
        try:
            import fcntl
            lock_path = os.path.join(loc, '.ticker.lock')
            with open(lock_path, 'a+', encoding='utf-8') as lf:
                fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
                try:
                    return fn()
                finally:
                    fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
        except OSError:
            return fn()


def note_todos(slug, items, by='', previous_items=None, now=None):
    """Compare a Mood's freshly read to-do items with what was last seen there; record any non-merge,
    non-deploy item that just moved from open to done. Never announces on a cold baseline unless
    `previous_items` (from the page's parent revision) is supplied."""
    from .todo_types import TYPES, type_of
    now = float(time.time() if now is None else now)
    current = _state_of(items)
    key = STATE_KEY.format(slug=slug)
    prev = cache.get(key)
    if prev is None and previous_items is not None:
        prev = _state_of(previous_items)
    cache.set(key, current, None)
    if prev is None or _is_todo_bot(by):
        return []

    prev_open_t = set(prev.get('open_tasks') or ())
    prev_done_t = set(prev.get('done_tasks') or ())
    prev_open_l = set(prev.get('open_links') or ())
    prev_done_l = set(prev.get('done_links') or ())
    who = shown_who(by)
    added = []

    for item in items or ():
        if not item.get('done'):
            continue
        itype = item.get('type') or type_of(item)
        if itype in EXCLUDED_TYPES or item.get('merged') or item.get('closed') or item.get('deployed'):
            continue
        task = (item.get('task') or '').strip()
        nt = _norm(task)
        lnk = (item.get('link') or '').strip()
        if not nt or nt in prev_done_t:
            continue
        was_open = (nt in prev_open_t) or (bool(lnk) and lnk in prev_open_l and lnk not in prev_done_l)
        if not was_open:
            continue
        eid = 'todo:' + hashlib.sha1(f'{slug}\0{nt}'.encode()).hexdigest()[:16]
        if not cache.add(f'ticker:once:{eid}', 1, OK_FOR + 30):
            continue
        emoji, label, _ = TYPES.get(itype, ('✓', itype, ''))
        short = task if len(task) <= 80 else task[:79].rstrip() + '…'
        words = f'#{slug}: {short}' + (f' ({who})' if who else '')
        sub_parts = [label]
        if who:
            sub_parts.append(f'by {who}')
        owners = [shown_who(w) for w in (item.get('who') or []) if str(w).strip()]
        if owners:
            sub_parts.append(f"for {', '.join(owners)}")
        note = (item.get('note') or '').strip()
        full = task + (f'\n\n{note}' if note else '')
        added.append({
            'id': eid,
            'kind': 'todo',
            'state': 'ok',
            'words': words,
            'what': f'{emoji} To-do done in #{slug}',
            'sub': ' · '.join(sub_parts),
            'full': full,
            'link': lnk or f'/todo/#mood={urllib.parse.quote(slug)}',
            'link_label': 'Open link →' if lnk else f'#{slug} to-dos →',
            'by': who,
            'at': now,
        })

    if added:
        def _store():
            kept = [e for e in (cache.get(EVENTS_KEY) or []) if now - float(e.get('at') or 0) <= OK_FOR]
            seen = {e['id'] for e in kept}
            for e in added:
                if e['id'] not in seen:
                    kept.append(e)
            cache.set(EVENTS_KEY, kept, OK_FOR + 30)
        _with_events_lock(_store)
        cache.delete('moods-live')
    return added


def todo_items(now=None):
    now = float(time.time() if now is None else now)
    kept = cache.get(EVENTS_KEY) or []
    return [e for e in kept if now - float(e.get('at') or 0) <= OK_FOR]


def merge_items(now=None):
    """Active and recently finished githouse merge/promote orders from everyone."""
    from conversations.models import MergeOrder
    from . import merge_orders
    now = float(time.time() if now is None else now)
    cutoff = timezone.now() - timedelta(seconds=LOOKBACK)
    out = []
    for order in MergeOrder.objects.filter(received_at__gte=cutoff).order_by('-received_at'):
        try:
            fields = merge_orders.parse(order.order)
            into = fields['into']
            commit = fields['commit']
        except merge_orders.Invalid:
            into = merge_orders.DEPLOYS_FROM.get(order.repo, 'main')
            commit = ''
        promote = merge_orders.PROMOTES.get(order.branch) == into
        who = shown_who(order.pressed or order.by)
        by_suffix = f' ({who})' if who else ''
        by_words = f' by {who}' if who else ''
        name = f'{order.repo}: main up to production' if promote else f'{order.repo}:{order.branch}'
        what = f'Promote {name}?' if promote else f'Merge {name}?'
        sub = (f'{order.repo}:{order.branch}' + (f' at {commit[:12]}' if commit else '')
               + f' → {into}' + (f' · by {who}' if who else ''))
        link = ('' if promote else f"/ramp/{urllib.parse.quote(order.repo)}/"
                f"{'/'.join(urllib.parse.quote(p, safe='') for p in order.branch.split('/'))}/")
        link_label = '' if promote else 'Candidate branch →'

        st = merge_orders.status(order, now=int(now))
        state_name = st.get('status')
        received_ts = order.received_at.timestamp()

        if state_name == 'waiting':
            age = now - received_ts
            if age <= GOING_FOR:
                doing = 'Promoting' if promote else 'Merging'
                words = f"{doing} {name}{'' if promote else ' into ' + into}{by_suffix}…"
                out.append({
                    'id': f'merge:{order.id}', 'kind': 'merge', 'state': 'going',
                    'words': words, 'what': what, 'sub': sub, 'full': words,
                    'link': link, 'link_label': link_label, 'by': who, 'at': received_ts,
                })
            elif age <= STALLED_FOR:
                full = 'githouse hasn’t answered in two minutes; the order is good for an hour.'
                out.append({
                    'id': f'merge:{order.id}', 'kind': 'merge', 'state': 'err',
                    'words': f'{name}{by_suffix}: {full} Click for the whole of it.',
                    'what': what, 'sub': sub, 'full': full,
                    'link': link, 'link_label': link_label, 'by': who, 'at': received_ts + GOING_FOR,
                })
        elif state_name == 'merged':
            done_at = float(st.get('at') or (order.announced_at.timestamp() if order.announced_at else received_ts))
            if now - done_at > OK_FOR:
                continue
            sha = str(st.get('merge') or '')[:12]
            words = (f'{order.repo}: main promoted to production{by_words}'
                     if promote else f'{name} merged into {into}{by_words}')
            full = (f'main is production now, at {sha}{by_suffix}.'
                    if promote else f'Merged into {into} as {sha}{by_suffix}.')
            out.append({
                'id': f'merge:{order.id}', 'kind': 'merge', 'state': 'ok',
                'words': words, 'what': what, 'sub': sub, 'full': full,
                'link': link, 'link_label': link_label, 'by': who, 'at': done_at,
                'merged': {'repo': order.repo, 'branch': order.branch},
            })
        elif state_name in ('refused', 'expired'):
            done_at = float(st.get('at') or received_ts)
            if now - done_at > ERR_FOR:
                continue
            prefix = 'Expired' if state_name == 'expired' else 'Refused'
            full = f"{prefix}: {st.get('why') or ''}"
            short = full[:90] + '…' if len(full) > 90 else full
            out.append({
                'id': f'merge:{order.id}', 'kind': 'merge', 'state': 'err',
                'words': f'{name}{by_suffix}: {short} Click for the whole of it.',
                'what': what, 'sub': sub, 'full': full,
                'link': link, 'link_label': link_label, 'by': who, 'at': done_at,
            })
    return out


def items(now=None):
    """Everything currently on the shared announcement bar, newest first."""
    now = float(time.time() if now is None else now)
    combined = merge_items(now=now) + todo_items(now=now)
    combined.sort(key=lambda x: x.get('at') or 0, reverse=True)
    return combined
