"""A change on PickiPedia reaches what this server keeps of the page, within a minute.

Many pages are read from the wiki and kept a while: a link's preview and a person's card
(services/wiki_preview.py, six hours), the interface's words (Magenta:Interface, Magenta:System
Messages, Magenta:Sounds), the FAQ, the clips, the voice words, the tour, the slogans, the eras,
each emergent's page, a Mood's instructions. Each kept its own time, so an edit showed when its
clock ran out: a User page took hours to reach its card (Justin, #interfaces, 5 Nov 2026).

This asks the wiki for its recent changes once a minute (every edit, uploads and moves included,
bots' too: the Magenta: pages are often edited by an emergent), on the pulse's clock as the wiki
feed is (services/wiki_feed.py, which shows people's edits as lines), and forgets what's kept of
each changed page. The next look fetches it again; nothing is fetched that nobody looks at.

The push (PickiPediaContent's hook on PickiPedia, telling /api/wiki/hook/ of each save, signed
with settings.WIKI_HOOK_SECRET, as GitHub's webhook tells of merges) is the fast road; this sweep
is the check on it. What a change does, by either road, is services/wiki_hooks.py. Once a push has ever
been heard, a change the sweep finds that no push told of -- after GRACE, to give the push its
moment -- is said in #ops as a line (Justin, 5 Nov: "if the poll finds something that didn't get
pushed, we oughta get a notice about that"). Until the first push, no change was expected to be
pushed, and the sweep says nothing.
"""

import hashlib
import re
import threading
import time
import uuid
from datetime import datetime, timezone

from django.core.cache import cache

EVERY = 60          # seconds between sweeps
LIMIT = 50          # changes asked for per sweep
GRACE = 90          # seconds a change is given to be pushed before the sweep calls it missed
ROOM = 'ops'        # where a missed push is said
SOURCE = 'wiki-missed'
CURSOR = 'wiki-changes:cursor'          # the newest rcid judged
PUSH_HEARD = 'wiki-changes:push-heard'  # when a push was last heard: set, the sweep expects pushes
PUSHED_FOR = 86400                      # seconds a pushed revision is remembered
_MOOD_DOC = re.compile(r'^Cryptograss:Moods/[^/]+/Instructions$', re.I)
_EMERGENT = re.compile(r'^Cryptograss:Emergents/', re.I)


def nudge(http=None, wait=False):
    """Sweep if it's been a minute: in the background, unless `wait`."""
    if not cache.add('wiki-changes:lock', 1, EVERY):
        return False
    if wait:
        sweep(http)
    else:
        threading.Thread(target=_sweep_and_close, args=(http,), daemon=True).start()
    return True


def _sweep_and_close(http):
    from django.db import connection
    try:
        sweep(http)
    except Exception:
        pass  # the wiki or the network: next minute
    finally:
        connection.close()


def changes(http=None):
    """The wiki's newest changes, oldest first: everyone's, bots included; edits, new pages, uploads and moves."""
    import requests
    from .mood_view import pickipedia_url
    http = http or requests
    answer = http.get(f'{pickipedia_url()}/api.php', timeout=8, headers={'User-Agent': 'memory-lane (magenta; changes)'},
                      params={'action': 'query', 'list': 'recentchanges', 'rctype': 'edit|new|log',
                              'rcprop': 'title|ids|user|timestamp|loginfo', 'rclimit': LIMIT, 'format': 'json'})
    found = [c for c in answer.json()['query']['recentchanges']
             if c.get('type') != 'log' or c.get('logtype') in ('upload', 'move', 'delete')]
    return list(reversed(found))


def sweep(http=None, now=None):
    """Forget what's kept of each page changed since the last sweep; say in #ops each change the push
    missed. Returns (titles forgotten, changes missed)."""
    now = time.time() if now is None else now
    found = changes(http)
    if not found:
        return [], []
    cursor = cache.get(CURSOR)
    forgotten, missed = [], []
    judged = cursor
    for change in found:
        rcid = change.get('rcid', 0)
        if cursor is not None and rcid <= cursor:
            continue
        titles = [change.get('title', '')]
        if change.get('logtype') == 'move' and (change.get('logparams') or {}).get('target_title'):
            titles.append(change['logparams']['target_title'])
            from .tiding_links import follow_move  # the push did it too, if it was heard: it's idempotent
            follow_move(titles[0], titles[1])
        for title in titles:
            if title and cache.add(f'wiki-changes:forgot:{rcid}:{hashlib.sha1(title.encode()).hexdigest()}', 1, 86400):
                # Told of by the push already: its hooks ran then; the sweep only judges it below.
                if change.get('revid') and was_pushed(change['revid']):
                    continue
                from .wiki_hooks import run
                run(title, change.get('user', ''), change.get('revid'), via='sweep')
                forgotten.append(title)
        # Judged once it has had its GRACE; the cursor stops short of a change still within it.
        at = _when(change.get('timestamp'))
        if at is not None and now - at < GRACE:
            break
        judged = max(judged or 0, rcid)
        if cursor is not None and cache.get(PUSH_HEARD) and change.get('revid') and not was_pushed(change['revid']):
            if record_missed(change) is not None:
                missed.append(change)
    if judged is not None and judged != cursor:
        cache.set(CURSOR, judged, None)
    elif cursor is None:  # the first sweep: everything found is history, not news
        cache.set(CURSOR, max(c.get('rcid', 0) for c in found), None)
    return forgotten, missed


def _when(stamp):
    try:
        return datetime.strptime(stamp, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError):
        return None


def forget(title, user=''):
    """Drop what's kept of a page (and of its editor's card, for their edits). Cheap and idempotent."""
    title = re.sub(r'\s+', ' ', (title or '').replace('_', ' ')).strip()
    if not title:
        return
    from . import interface_words, wiki_exists, wiki_lead, wiki_preview, wiki_titles
    wiki_preview.forget(title)
    wiki_lead.forget(title)
    wiki_titles.forget(title)
    wiki_exists.forget(title)
    interface_words.forget(title)
    if title.startswith('User:'):
        from .emergent_powers import FRAMES_KEY
        cache.delete(FRAMES_KEY)
    if title.startswith('File:'):
        cache.delete('embeds:file:' + re.sub(r'\s+', '_', title[5:].lower())[:200])
    if title.startswith('Release:'):
        cache.delete('embeds:releases')
        from .tidings import releases_changed
        releases_changed()
    if _MOOD_DOC.match(title):
        from .mood_doc import KEY
        cache.delete(KEY)
    if _EMERGENT.match(title):
        from .emergent_powers import KEY
        cache.delete(KEY)
    for page, key in _PAGES.items():
        if title.lower() == page.lower():
            cache.delete(key)
    from .verify import KEY as VERIFY
    cache.delete(VERIFY)  # what awaits verifying may have changed with any edit
    if user:
        from .person_card import forget_edits
        forget_edits(user)


def _pages():
    from . import clips, eras, faq, tour, voice_words
    from .front import SLOGANS_PAGE
    from .sprint_notes import POINTER
    return {faq.PAGE: faq.KEY, clips.PAGE: clips.USES_KEY, voice_words.PAGE: 'voice-words', tour.PAGE: tour.KEY,
            SLOGANS_PAGE: 'front:slogans', eras.PAGE: eras.CACHE_KEY, POINTER: 'current-sprint'}


class _Pages(dict):
    """Page -> cache key, built on first use (the services import each other)."""
    def items(self):
        if not self:
            self.update(_pages())
        return super().items()


_PAGES = _Pages()


# --- the push, when it comes -----------------------------------------------------------

def note_pushed(title, revid, user=''):
    """A save PickiPedia told of: its hooks run now (services/wiki_hooks.run), and the revision is remembered
    as pushed so the sweep doesn't call it missed. Returns the log rows."""
    from .wiki_hooks import run
    cache.set(PUSH_HEARD, int(time.time()), None)
    if revid:
        cache.set(f'wiki-changes:pushed:{int(revid)}', 1, PUSHED_FOR)
    return run(title, user, revid, via='push')


def heard(outcome, refused=False):
    """What PickiPedia last sent and what came of it, for /wiki-hooks/; a refused one (unsigned, or signed with
    another secret) kept apart, so a stray POST can't hide the last good one (as todo.heard does for GitHub)."""
    cache.set('wiki-changes:hook:refused' if refused else 'wiki-changes:hook:last',
              {'outcome': outcome, 'at': int(time.time())}, None)


def push_state():
    from django.conf import settings
    return {'configured': bool(getattr(settings, 'WIKI_HOOK_SECRET', '')),
            'heard': cache.get(PUSH_HEARD), 'last': cache.get('wiki-changes:hook:last'),
            'refused': cache.get('wiki-changes:hook:refused')}


def was_pushed(revid):
    return bool(cache.get(f'wiki-changes:pushed:{int(revid)}'))


def record_missed(change):
    """A line in #ops for a change no push told of; None when there's no #ops."""
    from conversations.models import ConversationParticipant, Message, Mood
    mood = Mood.by_slug(ROOM)
    if mood is None:
        return None
    system, _ = ConversationParticipant.objects.get_or_create(name='system', defaults={'participant_type': 'system'})
    content = {'type': 'wiki-missed', 'title': (change.get('title') or '')[:200], 'revid': change.get('revid'),
               'user': (change.get('user') or '')[:80], 'at': change.get('timestamp', '')}
    return Message.objects.create(id=uuid.uuid4(), sender=system, mood=mood, content=content,
                                  timestamp=int(time.time() * 1000), source_file=SOURCE)


def line(c):
    """What a missed line says (mood_view.event_line)."""
    by = f" by {c['user']}" if c.get('user') else ''
    return f"📡 PickiPedia didn't push rev {c.get('revid', '?')} of [[{c.get('title', '')}]]{by}: the sweep found it"
