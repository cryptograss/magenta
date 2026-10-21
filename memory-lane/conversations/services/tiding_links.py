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


def thread(message_id, index=None):
    """The replies under a message, and theirs, oldest first at each level:
    [{'id', 'sender', 'mood', 'at', 'replies': [...]}]. What's said about a promoted link."""
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
    return under(str(message_id))


def count(tree):
    return sum(1 + count(node['replies']) for node in tree)


def listed(limit=LISTED):
    """The links on Tidings, newest first, as the page shows them."""
    from conversations.models import Tiding
    from .links import current
    from .mood_view import prose, snippet_of
    from .retract import DELETED
    index = current()
    out = []
    for tiding in Tiding.objects.select_related('message', 'by').order_by('-at')[:limit]:
        message = tiding.message
        text = prose(message.content) or ''
        if text == DELETED or message.mood_id is None:
            continue
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
        })
    return out
