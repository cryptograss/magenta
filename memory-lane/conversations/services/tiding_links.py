"""Links on Tidings: a message's link, promoted by someone who thinks it's news (Justin, 20 Oct).

A message with links in it can be promoted -- whichever of its links the promoter picks, each once
(Justin, 21 Oct) -- and each picked link is listed on Tidings,
newest first for now, under a title -- the page's own, as its preview card
reads it (services/unfurl.py), until someone gives it another -- with who
promoted it, the message and Mood it came from, and how much has been said
about it.

What's said about a link is the message's replies, and theirs: one
conversation, seen as chat in its Mood and as a tree on Tidings. Nothing is
kept apart for it, so "N comments" is the reply tree under the message, read
from the links index (services/links.py).

Who may: anyone signed in promotes; whoever promoted it, or an admin, retitles
or withdraws it. Agents don't promote (their sessions have no device).
"""

import re
import time
import uuid
from datetime import datetime
from urllib.parse import urlsplit

from django.core.cache import cache

LISTED = 30          # links on Tidings
MAX_TITLE = 300
_BY_MESSAGE = 'tidings:by-message:all'  # a list per message now: the one-each form, under its old name, is never read


class Refused(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def links_in(text):
    """The web links in what was said, in order, each once: any page, not a picture kept here or a
    link back into magenta."""
    from .mood_view import OWN_HOSTS, _INLINE_CODE, _URL, _trim_url
    text = re.sub(r'```.*?(```|$)', ' ', text or '', flags=re.S)
    text = _INLINE_CODE.sub(' ', text)
    found = []
    for match in _URL.finditer(text):
        url, _ = _trim_url(match.group(1))
        if url not in found and (urlsplit(url).hostname or '') not in OWN_HOSTS:
            found.append(url)
    return found


def host_of(url):
    host = (urlsplit(url).hostname or '').lower()
    return host[4:] if host.startswith('www.') else host


def title_for(url, http=None):
    """The page's own title, as its preview card reads it; its address, shortened, if it gives none."""
    from . import unfurl
    try:
        card = unfurl.card(url, http) if unfurl.public(urlsplit(url).hostname or '') else None
    except Exception:  # noqa: BLE001 -- a page that won't say: its address will do
        card = None
    title = ((card or {}).get('title') or '').strip()
    if title:
        return title[:MAX_TITLE]
    parts = urlsplit(url)
    return (host_of(url) + parts.path.rstrip('/'))[:MAX_TITLE]


def by_message():
    """{message id: [{'id', 'title', 'url'}, ...]} for every promoted message (a few hundred at most), in the
    order promoted; kept until one changes."""
    found = cache.get(_BY_MESSAGE)
    if found is None:
        from conversations.models import Tiding
        found = {}
        for tiding in Tiding.objects.order_by('at'):
            found.setdefault(str(tiding.message_id), []).append(
                {'id': str(tiding.id), 'title': tiding.title, 'url': tiding.url})
        cache.set(_BY_MESSAGE, found, 3600)
    return found


def _changed():
    cache.delete(_BY_MESSAGE)


def promote_many(message, by, urls, http=None):
    """Put each of `urls` (links of the message) on Tidings. [(Tiding, whether it's new)], in their order."""
    return [promote(message, by, url=url, http=http, with_news=True) for url in dict.fromkeys(urls)]


def promote(message, by, url=None, title=None, http=None, with_news=False):
    """Put one of a message's links on Tidings (its first, unless `url` says which), once. The Tiding, or the
    one already there for that link; with `with_news`, (it, whether it's new)."""
    from conversations.models import Tiding
    from .mood_view import prose
    from .retract import DELETED
    if message.mood_id is None or message.is_sidechain:
        raise Refused('only what was said in a Mood', status=404)
    text = prose(message.content) or ''
    if text == DELETED:
        raise Refused('that message was deleted', status=404)
    found = links_in(text)
    if not found:
        raise Refused('that message has no link to promote')
    url = url or found[0]
    if url not in found:
        raise Refused('that link isn\'t in that message')
    already = Tiding.objects.filter(message=message, url=url).first()
    if already:
        return (already, False) if with_news else already
    tiding = Tiding.objects.create(message=message, url=url, by=by,
                                   title=(title or '').strip()[:MAX_TITLE] or title_for(url, http))
    _changed()
    return (tiding, True) if with_news else tiding


def _may_change(tiding, device):
    from .retract import is_admin
    return device is not None and (tiding.by_id == device.entity_id or is_admin(device))


def retitle(tiding, device, title):
    if not _may_change(tiding, device):
        raise Refused('only whoever promoted it, or an admin, can retitle it', status=403)
    title = (title or '').strip()[:MAX_TITLE]
    if not title:
        raise Refused('give it a title')
    type(tiding).objects.filter(pk=tiding.pk).update(title=title)
    tiding.title = title
    _changed()
    return tiding


def withdraw(tiding, device):
    if not _may_change(tiding, device):
        raise Refused('only whoever promoted it, or an admin, can take it off Tidings', status=403)
    tiding.delete()
    _changed()


def _walk(tree):
    """Every node of a reply tree, one at a time."""
    for node in tree:
        yield node
        yield from _walk(node.get('replies', []))


def _snippets_and_votes(tree, viewer=None):
    """Snippet, score and whether `viewer` voted, onto every node: three questions of the database,
    whatever the tree's size."""
    from django.db.models import Count
    from conversations.models import Message, TidingVote
    from .mood_view import reply_to, snippet_of
    nodes = list(_walk(tree))
    ids = [node['id'] for node in nodes]
    texts = {str(m.id): snippet_of(reply_to(m.content or '')[1]) for m in Message.objects.filter(id__in=ids)} if ids else {}
    scored = ({str(mid): c for mid, c in TidingVote.objects.filter(message_id__in=ids).values('message_id')
               .annotate(c=Count('id')).values_list('message_id', 'c')} if ids else {})
    mines = ({str(mid) for mid in TidingVote.objects.filter(message_id__in=ids, by_id=viewer)
              .values_list('message_id', flat=True)} if viewer and ids else set())
    for node in nodes:
        node['snippet'] = texts.get(node['id'], '')
        node['score'] = scored.get(node['id'], 0)
        node['mine'] = node['id'] in mines
        node['at'] = datetime.fromisoformat(node['at'])  # the index keeps it ISO (it's cached); the page says it
    return tree


def thread(message_id, index=None, viewer=None):
    """The replies under a message, and theirs, oldest first at each level:
    [{'id', 'sender', 'mood', 'at', 'snippet', 'score', 'mine', 'replies': [...]}]. What's said about a
    promoted link; `snippet` is the reply's words, read all at once, and the votes its upvotes."""
    from .links import current
    index = index if index is not None else current()
    seen = {str(message_id)}

    def under(mid):
        out = []
        for entry in sorted(index.get(mid, []), key=lambda e: e['at']):
            if entry['reply'] and entry['id'] not in seen:
                seen.add(entry['id'])
                out.append({**entry, 'replies': under(entry['id'])})
        return out
    return _snippets_and_votes(under(str(message_id)), viewer)


def count(tree):
    return sum(1 + count(node['replies']) for node in tree)


def _tiding_scores(tidings, viewer=None):
    """(score per Tiding, which of them `viewer` voted) -- two questions of the database."""
    from django.db.models import Count
    from conversations.models import TidingVote
    ids = [t.id for t in tidings]
    if not ids:
        return {}, set()
    scored = {str(tid): c for tid, c in TidingVote.objects.filter(tiding_id__in=ids).values('tiding_id')
              .annotate(c=Count('id')).values_list('tiding_id', 'c')}
    mines = ({str(tid) for tid in TidingVote.objects.filter(tiding_id__in=ids, by_id=viewer)
              .values_list('tiding_id', flat=True)} if viewer else set())
    return scored, mines


def vote(tiding, by):
    """One person's upvote of a link: there or not -- again takes it back. {'score', 'mine'} after."""
    from django.db import IntegrityError
    from conversations.models import TidingVote
    row = TidingVote.objects.filter(tiding=tiding, by=by).first()
    if row:
        row.delete()
    else:
        try:
            TidingVote.objects.create(tiding=tiding, by=by)
        except IntegrityError:  # two presses racing: the vote's in; leave it
            pass
    return _vote_state(tiding_id=tiding.id, by_id=by.pk)


def vote_comment(tiding, by, message_id):
    """One person's upvote of a comment in a link's discussion, or the vote taken back. {'score', 'mine'}.
    The comment must be under the link's message: a vote never reaches a message that isn't said there."""
    from django.db import IntegrityError
    from conversations.models import Message, TidingVote
    if message_id is None:
        raise Refused('no such comment under this link', status=404)
    message = Message.objects.filter(id=message_id, mood_id=tiding.message.mood_id).first()
    in_tree = message_id == tiding.message_id or any(node['id'] == str(message_id) for node in _walk(thread(tiding.message_id)))
    if message is None or not in_tree:
        raise Refused('no such comment under this link', status=404)
    row = TidingVote.objects.filter(message=message, by=by).first()
    if row:
        row.delete()
    else:
        try:
            TidingVote.objects.create(message=message, by=by)
        except IntegrityError:
            pass
    return _vote_state(message_id=message.id, by_id=by.pk)


def _vote_state(tiding_id=None, message_id=None, by_id=None):
    """A link's or a comment's score -- everyone's upvotes -- and whether this voter's among them."""
    from conversations.models import TidingVote
    votes = TidingVote.objects.filter(tiding_id=tiding_id) if tiding_id else TidingVote.objects.filter(message_id=message_id)
    return {'score': votes.count(), 'mine': votes.filter(by_id=by_id).exists()}


def comment(tiding, by, text, parent=None, client_version=''):
    """Say it about a link: a message in the link's Mood, as a reply to the link's message or to a comment
    under it. One record: on Tidings it's a comment, in the Mood it's a reply. The new node, as the
    discussion shows it."""
    from conversations.models import Message
    from .mood_view import prose, snippet_of
    from .retract import DELETED
    message = tiding.message
    if message.mood_id is None:
        raise Refused('only what was said in a Mood', status=404)
    if prose(message.content) == DELETED:
        raise Refused('that message was deleted', status=404)
    target = parent if parent else message.id
    if str(target) != str(message.id) and not any(node['id'] == str(target) for node in _walk(thread(message.id))):
        raise Refused('no such comment under this link', status=404)
    row = Message.objects.create(id=uuid.uuid4(), sender=by, content=f'↩ #m-{target}\n{text}', mood=message.mood,
                                 timestamp=int(time.time() * 1000), source_file='mood-web',
                                 client_version=client_version)
    from .links import note_new
    note_new(row, [str(target)], str(target))  # a reload the same second still shows it
    return {'id': str(row.id), 'sender': by.name, 'mood': message.mood_slug, 'at': row.created_at,
            'snippet': snippet_of(text), 'replies': [], 'score': 0, 'mine': False}


def discussion(tiding_id, viewer=None):
    """One link's page: itself, then what's been said about it. Missing or deleted: Refused."""
    from conversations.models import Tiding
    from .mood_view import prose, snippet_of
    from .retract import DELETED
    tiding = Tiding.objects.select_related('message', 'by').filter(id=tiding_id).first()
    if tiding is None:
        raise Refused('no such link on Tidings', status=404)
    message = tiding.message
    text = prose(message.content) or ''
    if text == DELETED or message.mood_id is None:
        raise Refused('that message was deleted', status=404)
    scored, mines = _tiding_scores([tiding], viewer)
    return {
        'id': str(tiding.id),
        'url': tiding.url,
        'host': host_of(tiding.url),
        'title': tiding.title or tiding.url,
        'by': tiding.by.name if tiding.by_id else '',
        'at': tiding.at,
        'message': str(message.id),
        'mood': message.mood_slug,
        'said_by': message.sender_id,
        'snippet': snippet_of(text)[:200],
        'score': scored.get(str(tiding.id), 0),
        'mine': str(tiding.id) in mines,
        'comments': thread(message.id, viewer=viewer),
    }


def listed(limit=LISTED, viewer=None):
    """The links on Tidings, newest first, as the page shows them: numbered there, each with its
    score and whether `viewer` voted."""
    from conversations.models import Tiding
    from .links import current
    from .mood_view import prose, snippet_of
    from .retract import DELETED
    index = current()
    rows = []
    for tiding in Tiding.objects.select_related('message', 'by').order_by('-at')[:limit]:
        message = tiding.message
        text = prose(message.content) or ''
        if text == DELETED or message.mood_id is None:
            continue
        rows.append(tiding)
    scored, mines = _tiding_scores(rows, viewer)
    out = []
    for tiding in rows:
        message = tiding.message
        text = prose(message.content) or ''
        out.append({
            'id': str(tiding.id),
            'url': tiding.url,
            'host': host_of(tiding.url),
            'title': tiding.title or tiding.url,
            'by': tiding.by.name if tiding.by_id else '',
            'at': tiding.at,
            'message': str(message.id),
            'mood': message.mood_slug,
            'said_by': message.sender_id,
            'snippet': snippet_of(text)[:200],
            'comments': count(thread(message.id, index)),
            'score': scored.get(str(tiding.id), 0),
            'mine': str(tiding.id) in mines,
        })
    return out
