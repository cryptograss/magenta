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

Who may: anyone signed in promotes; whoever promoted it, or anyone signed in
with an SSH key, retitles it (Justin, 21 Oct); whoever promoted it, or an
admin, withdraws it. Agents don't promote (their sessions have no device).
"""

import re
import time
import uuid
from datetime import datetime
from urllib.parse import urlsplit

from django.core.cache import cache

from .redaction import redact

LISTED = 30          # links on Tidings
CANDIDATES = 100     # the newest links ranked for it: an older one has faded from the top anyway
MAX_TITLE = 300
_BY_MESSAGE = 'tidings:by-message:all'  # a list per message now: the one-each form, under its old name, is never read


class Refused(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def links_in(text):
    """The web links in what was said, in order, each once: any page, not a picture kept here or a
    link back into magenta. A [[wikilink]] is its PickiPedia page's address (a [[File:...]] is a
    picture shown, not a link)."""
    from .mood_view import OWN_HOSTS, _INLINE_CODE, _URL, _WIKILINK, _trim_url, pickipedia_url
    text = re.sub(r'```.*?(```|$)', ' ', text or '', flags=re.S)
    text = _INLINE_CODE.sub(' ', text)
    found = []
    for match in _URL.finditer(text):
        url, _ = _trim_url(match.group(1))
        if (urlsplit(url).hostname or '') not in OWN_HOSTS:
            found.append((match.start(), url))
    for match in _WIKILINK.finditer(text):
        target = match.group(1).strip()
        if target and target.partition(':')[0].lower() not in ('file', 'image', 'release'):
            found.append((match.start(), f"{pickipedia_url()}/wiki/{target.replace(' ', '_')}"))
    out = []
    for _, url in sorted(found):
        if url not in out:
            out.append(url)
    return out


def host_of(url):
    host = (urlsplit(url).hostname or '').lower()
    return host[4:] if host.startswith('www.') else host


def embed_of(url):
    """How a link's page can be played in place, if it's a video Justin (21 Oct): {"kind", "src"} for
    the players we know -- YouTube and Vimeo pages, and a bare media file -- else None. Downloading and
    keeping a copy of a yt-dlp-supported video is a separate job, not yet built."""
    parts = urlsplit(url)
    host = (parts.hostname or '').lower().removeprefix('www.')
    if host in ('youtube.com', 'm.youtube.com', 'youtu.be'):
        from urllib.parse import parse_qs
        path = parts.path.strip('/').split('/')
        vid = (path[0] if host == 'youtu.be' else path[1] if len(path) > 1 and path[0] in ('shorts', 'embed', 'live')
               else (parse_qs(parts.query).get('v') or [''])[0])
        if re.fullmatch(r'[\w-]{6,20}', vid or ''):  # a video's id, nothing else, goes into the player's address
            return {'kind': 'iframe', 'src': f'https://www.youtube-nocookie.com/embed/{vid}'}
    if host in ('vimeo.com', 'player.vimeo.com'):
        vid = parts.path.strip('/').split('/')[0]
        if vid.isdigit():
            return {'kind': 'iframe', 'src': f'https://player.vimeo.com/video/{vid}'}
    if any(parts.path.lower().endswith('.' + ext) for ext in ('mp4', 'webm', 'mov', 'm4v', 'ogv')):
        return {'kind': 'video', 'src': url}
    return None


def _clean_tags(tags):
    """Whichever words a promoter gave each link, one each, lowercase, no # or commas: at most eight."""
    out = []
    # As typed in the promote box, "music, security": a tag between each pair of commas.
    tags = tags.split(',') if isinstance(tags, str) else tags if isinstance(tags, list) else []
    for tag in tags:
        tag = re.sub(r'[^\w -]', ' ', str(tag or '')).strip().lower()
        tag = re.sub(r'\s+', ' ', tag)
        if tag and tag not in out:
            out.append(tag[:40])
    return out[:8]


def title_for(url, http=None):
    """The page's own title, as its preview card reads it; its address, shortened, if it gives none.
    A PickiPedia page is titled as the wiki titles it."""
    from . import unfurl
    from .mood_view import pickipedia_page
    page = pickipedia_page(url)
    if page:
        return (f'{page[0]} § {page[1]}' if page[1] else page[0])[:MAX_TITLE]
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


def promote_many(message, by, urls, http=None, title=None, tags=None):
    """Put each of `urls` (links of the message) on Tidings. [(Tiding, whether it's new)], in their order.
    A title given is one link's: with several, each is titled as its page titles itself."""
    urls = list(dict.fromkeys(urls))
    title = title if len(urls) == 1 else None
    return [promote(message, by, url=url, title=title, tags=tags, http=http, with_news=True) for url in urls]


def promote(message, by, url=None, title=None, tags=None, http=None, with_news=False):
    """Put one of a message's links on Tidings (its first, unless `url` says which), once, under the
    promoter's title and tags if they gave any. The Tiding, or the one already there for that link;
    with `with_news`, (it, whether it's new)."""
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
                                   title=(title or '').strip()[:MAX_TITLE] or title_for(url, http),
                                   tags=_clean_tags(tags))
    _changed()
    return (tiding, True) if with_news else tiding


def may_retitle(device, promoted_by):
    """Whoever promoted it, or anyone signed in with an SSH key: any title (Justin, 21 Oct)."""
    return device is not None and (device.tier == 'key' or promoted_by == device.entity_id)


def may_withdraw(device, promoted_by):
    from .retract import is_admin
    return device is not None and (promoted_by == device.entity_id or is_admin(device))


def retitle(tiding, device, title):
    if not may_retitle(device, tiding.by_id):
        raise Refused('only whoever promoted it, or someone signed in with an SSH key, can retitle it', status=403)
    title = (title or '').strip()[:MAX_TITLE]
    if not title:
        raise Refused('give it a title')
    type(tiding).objects.filter(pk=tiding.pk).update(title=title)
    tiding.title = title
    _changed()
    return tiding


def withdraw(tiding, device):
    if not may_withdraw(device, tiding.by_id):
        raise Refused('only whoever promoted it, or an admin, can take it off Tidings', status=403)
    tiding.delete()
    _changed()


def _walk(tree):
    """Every node of a reply tree, one at a time."""
    for node in tree:
        yield node
        yield from _walk(node.get('replies', []))


def said_html(text):
    """A comment as the Mood draws it: its paragraphs, lists, code and links, without the reply mark
    (and a ```voice or ```handoff block left out, as there)."""
    from .handoff import split as split_handoffs
    from .mood_view import render_html, reply_to
    from .voice import split_voices
    text, _ = split_voices(text or '')
    text, _ = split_handoffs(text)
    return render_html(reply_to(text)[1])


def _snippets_and_votes(tree, viewer=None):
    """Words (a snippet, and as drawn), score and whether `viewer` voted, onto every node: a few questions
    of the database, whatever the tree's size. A comment said on Tidings already carries its words; a Mood
    reply's are read here, all at once."""
    from django.db.models import Count
    from conversations.models import Message, TidingVote
    from .mood_view import prose, reply_to, snippet_of
    nodes = list(_walk(tree))
    mood_ids = [n['id'] for n in nodes if n.get('kind', 'mood') == 'mood']
    comment_ids = [n['id'] for n in nodes if n.get('kind') == 'comment']
    rows = {str(m.id): m for m in Message.objects.filter(id__in=mood_ids).select_related('change')} if mood_ids else {}
    texts = {mid: prose(m.content) for mid, m in rows.items()}
    scored, mines = {}, set()
    if mood_ids:
        scored.update({str(i): c for i, c in TidingVote.objects.filter(message_id__in=mood_ids).values('message_id')
                       .annotate(c=Count('id')).values_list('message_id', 'c')})
    if comment_ids:
        scored.update({str(i): c for i, c in TidingVote.objects.filter(comment_id__in=comment_ids).values('comment_id')
                       .annotate(c=Count('id')).values_list('comment_id', 'c')})
    if viewer:
        if mood_ids:
            mines |= {str(i) for i in TidingVote.objects.filter(message_id__in=mood_ids, by_id=viewer)
                      .values_list('message_id', flat=True)}
        if comment_ids:
            mines |= {str(i) for i in TidingVote.objects.filter(comment_id__in=comment_ids, by_id=viewer)
                      .values_list('comment_id', flat=True)}
    for node in nodes:
        if node.get('kind', 'mood') == 'mood':
            node['snippet'] = snippet_of(reply_to(texts.get(node['id'], ''))[1])
            node['html'] = said_html(texts.get(node['id'], ''))
            row = rows.get(node['id'])
            change = getattr(row, 'change', None) if row is not None else None
            node['changed'] = change.kind if change else ''
            node['text'] = '' if node['changed'] == 'deleted' else reply_to(texts.get(node['id'], ''))[1]
            node['source'], node['said_at'] = (row.source_file, row.created_at) if row is not None else ('', None)
            if isinstance(node['at'], str):
                node['at'] = datetime.fromisoformat(node['at'])  # the index keeps it ISO (it's cached); the page says it
        node['score'] = scored.get(node['id'], 0)
        node['mine'] = node['id'] in mines
    return tree


def tree_of(tiding, message_id, index=None, viewer=None):
    """A link's whole discussion, both kinds of comment together, oldest first at each level:
    the Mood's replies under its message ({'kind': 'mood', ...}, from the links index) and the comments
    said on Tidings itself ({'kind': 'comment', ...}, TidingComment) -- neither a record apart."""
    from conversations.models import TidingComment
    from .links import current
    index = index if index is not None else current()
    seen = {str(message_id)}

    def mood_under(mid):
        out = []
        for entry in sorted(index.get(mid, []), key=lambda e: e['at']):
            if entry['reply'] and entry['id'] not in seen:
                seen.add(entry['id'])
                out.append({**entry, 'kind': 'mood', 'at': datetime.fromisoformat(entry['at']),
                            'replies': mood_under(entry['id'])})
        return out

    extras = {}
    for row in TidingComment.objects.filter(tiding=tiding).select_related('by').order_by('at'):
        node = {'kind': 'comment', 'id': str(row.id), 'sender': row.by.name if row.by_id else '',
                'at': row.at, 'snippet': row.text, 'html': said_html(row.text), 'replies': [],
                'text': '' if row.changed == 'deleted' else row.text, 'changed': row.changed}
        key = 'c:' + str(row.parent_id) if row.parent_id else 'm:' + str(row.parent_message_id or message_id)
        extras.setdefault(key, []).append(node)

    def splice(nodes, key):
        nodes = nodes + extras.pop(key, [])
        nodes.sort(key=lambda n: n['at'])
        for node in nodes:
            node['replies'] = splice(node.get('replies', []),
                                     ('m:' if node['kind'] == 'mood' else 'c:') + node['id'])
        return nodes
    return _snippets_and_votes(splice(mood_under(str(message_id)), 'm:' + str(message_id)), viewer)


def _tree(message_id, index=None):
    """The replies under a message, and theirs, oldest first at each level, as the links index has them:
    [{'id', 'sender', 'mood', 'at', 'replies': [...]}]."""
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


def thread(message_id, index=None, viewer=None):
    """The Mood's replies under a promoted link's message, each with its words (`snippet`), its upvotes
    (`score`) and whether `viewer` gave one (`mine`) -- tree_of's Mood half, for listing counts."""
    return _snippets_and_votes(_tree(message_id, index), viewer)


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
    in_tree = any(node['id'] == str(message_id) for node in _walk(_tree(tiding.message_id)))
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


def _vote_state(tiding_id=None, message_id=None, comment_id=None, by_id=None):
    """A link's or a comment's score -- everyone's upvotes -- and whether this voter's among them."""
    from conversations.models import TidingVote
    votes = TidingVote.objects.filter(tiding_id=tiding_id) if tiding_id else (
        TidingVote.objects.filter(message_id=message_id) if message_id else TidingVote.objects.filter(comment_id=comment_id))
    return {'score': votes.count(), 'mine': votes.filter(by_id=by_id).exists()}


def vote_on_comment(tiding, by, comment_id):
    """One person's upvote of a comment said on Tidings itself, or the vote taken back. {'score', 'mine'}."""
    from django.db import IntegrityError
    from conversations.models import TidingComment, TidingVote
    row = TidingComment.objects.filter(id=comment_id, tiding=tiding).first()
    if row is None:
        raise Refused('no such comment under this link', status=404)
    vote = TidingVote.objects.filter(comment=row, by=by).first()
    if vote:
        vote.delete()
    else:
        try:
            TidingVote.objects.create(comment=row, by=by)
        except IntegrityError:
            pass
    return _vote_state(comment_id=row.id, by_id=by.pk)


COMMENT_SOURCE = 'tiding-comment'


def _notice(tiding, by, said=None, asks=()):
    """A line in the link's Mood: '💬 new comment by …', referring to the message it was posted from.
    The comment's words stay on Tidings (Justin, 21 Oct); the notice is all the Mood gets, and only
    if the link isn't muted there -- with the link's title and the comment's first words, which open it
    (Justin, 23 Oct)."""
    from .mood_view import snippet_of
    import time as _time
    from conversations.models import ConversationParticipant, Message
    system, _ = ConversationParticipant.objects.get_or_create(name='system',
                                                              defaults={'participant_type': 'system'})
    Message.objects.create(id=uuid.uuid4(), sender=system, mood=tiding.message.mood, source_file=COMMENT_SOURCE,
                           timestamp=int(_time.time() * 1000),
                           content={'type': COMMENT_SOURCE, 'by': by.name, 'message': str(tiding.message_id),
                                    'tiding': str(tiding.id), 'title': (tiding.title or tiding.url)[:200],
                                    **({'comment': snippet_of(said.text)[:120], 'comment_id': str(said.id)} if said else {}),
                                    # The agents it asks by name: each woken in this Mood (mention_of), its
                                    # answer coming back under the comment (answer_asked).
                                    **({'asks': list(asks)} if asks else {})})


def mute(tiding, device, muted):
    """Quiet the 'new comment by …' lines this link leaves in its Mood -- whoever promoted it, or an admin."""
    if not may_withdraw(device, tiding.by_id):
        raise Refused('only whoever promoted it, or an admin, can mute it in its Mood', status=403)
    type(tiding).objects.filter(pk=tiding.pk).update(muted_in_mood=bool(muted))
    tiding.muted_in_mood = bool(muted)
    _changed()
    return tiding


def badges(since, viewer=None):
    """What's new on Tidings since `since`: promotions and comments said on Tidings (Justin, 21 Oct) --
    others', not the viewer's own."""
    from conversations.models import Tiding, TidingComment
    tidings, comments = Tiding.objects.filter(at__gt=since), TidingComment.objects.filter(at__gt=since)
    if viewer:
        tidings, comments = tidings.exclude(by_id=viewer), comments.exclude(by_id=viewer)
    return {'tidings': tidings.count(), 'comments': comments.count()}


def comment(tiding, by, text, parent=None, client_version=''):
    """Say it about a link, on Tidings itself: a TidingComment -- the record of what's said there -- and,
    unless the link is muted, a one-line note in its Mood. `parent` is (kind, id): ('message', id) answers
    the link's message or one of the Mood's replies under it; ('comment', id) answers a comment said here."""
    from conversations.models import TidingComment
    from .mood_view import prose
    from .retract import DELETED
    message = tiding.message
    if message.mood_id is None:
        raise Refused('only what was said in a Mood', status=404)
    if prose(message.content) == DELETED:
        raise Refused('that message was deleted', status=404)
    kind, parent_id = parent or (None, None)
    comment_parent, parent_message_id = None, None
    if kind is None:
        parent_message_id = message.id
    elif kind == 'message':
        if str(parent_id) != str(message.id) and not any(node['id'] == str(parent_id) for node in _walk(_tree(message.id))):
            raise Refused('no such comment under this link', status=404)
        parent_message_id = parent_id
    elif kind == 'comment':
        comment_parent = TidingComment.objects.filter(id=parent_id, tiding=tiding).first()
        if comment_parent is None:
            raise Refused('no such comment under this link', status=404)
    else:
        raise Refused('expected a comment or a Mood reply to answer', status=400)
    text, _ = redact(text)  # a secret pasted here is kept out of the record, as in the Mood's own box
    row = TidingComment.objects.create(tiding=tiding, by=by, parent=comment_parent,
                                       parent_message_id=parent_message_id, text=text)
    asks = asked_in(text, by, client_version)
    if asks or not tiding.muted_in_mood:  # muted or not, an agent asked by name hears of it in the Mood
        _notice(tiding, by, row, asks)
    return {'id': str(row.id), 'kind': 'comment', 'sender': by.name, 'at': row.at,
            'snippet': text, 'html': said_html(text), 'replies': [], 'score': 0, 'mine': False}


# --- agents, asked on Tidings (Justin, 23 Oct: mention-only; their comments don't count toward points;
# their answers read aloud, as what they say in the Mood is) -------------------------------------------

ASKED_CONTEXT = 4     # comments above the one that asks, given to the agent with it
ASKED_EACH = 1500     # characters of each


def asked_in(text, by, client_version=''):
    """The agents a comment on Tidings asks by name (@magent), as in a Mood: only a person's words wake an
    agent, and not one signed in with PickiPedia (their @agent is just text)."""
    from .mood_view import agent_names, mentions_in
    if by is None or not by.is_biological_human or 'wiki' in (client_version or '').split('/')[1:]:
        return []
    agents = agent_names()
    return [name for name in mentions_in(text or '', agents | {by.name}) if name in agents]


def mention_of(notice, agent):
    """A comment on Tidings that asks `agent`, as the mention that wakes it in the link's Mood
    (views_moods.api_mentions) -- its words, the link, and the comments it answers -- or None."""
    from conversations.models import TidingComment
    c = notice.content if isinstance(notice.content, dict) else {}
    if c.get('type') != COMMENT_SOURCE or agent not in (c.get('asks') or []):
        return None
    asked = TidingComment.objects.select_related('tiding', 'by').filter(id=c.get('comment_id')).first()
    if asked is None or asked.by_id is None or asked.changed == 'deleted':
        return None  # deleted since: nothing to answer
    tiding = asked.tiding
    above, at = [], asked
    while len(above) < ASKED_CONTEXT and (at.parent_id or at.parent_message_id):
        if at.parent_id:
            at = TidingComment.objects.select_related('by').filter(id=at.parent_id).first()
            if at is None:
                break
            above.append(f"[{at.by_id or 'someone'}, on Tidings] {at.text[:ASKED_EACH]}")
        else:
            from .mood_view import prose, reply_to
            said = type(notice).objects.filter(id=at.parent_message_id).first()
            if said is not None and str(said.id) != str(tiding.message_id):
                # No #m- link: the poller would read the Mood from there (mood_poller.linked_message).
                above.append(f"[{said.sender_id}, in #{said.mood_slug}] {(reply_to(prose(said.content) or '')[1])[:ASKED_EACH]}")
            break
    where = f"/moods/tidings/{tiding.id}/#c-{asked.id}"
    text = (f"@{agent}: {asked.by_id} asked you on Tidings, in the discussion of “{tiding.title or tiding.url}” "
            f"({tiding.url}), {where}:\n\n{asked.text}"
            + (('\n\nWhat it answers, nearest first:\n' + '\n'.join(above)) if above else '')
            + '\n\nYour reply goes back there as a comment under theirs, as well as here.')
    return {'id': str(notice.id), 'sender': asked.by_id, 'is_human': True, 'created_at': notice.created_at.isoformat(),
            # Typed on Tidings, into no session: owed a wake at once, as a web post is.
            'via': 'web', 'tier': None, 'text': text, 'mentions': [agent], 'reply': None,
            'tiding': {'id': str(tiding.id), 'comment': str(asked.id)}}


def answer_asked(mood, session_id, agent, asked_ids):
    """A turn of `agent`'s has ended, woken for `asked_ids` (the runner says which: mood_poller): its last
    words go to Tidings too -- under each comment there that asked it (mention_of), and under each of the
    Mood's replies in a link's discussion that did -- so an answer lands where it was asked, magenta routing
    it rather than the agent remembering to. A silence goes nowhere. The new comments."""
    from conversations.models import Message, ThinkingEntity, Tiding, TidingComment
    from .mood_view import is_wrapper, prose, quiet_reason
    ids = []
    for i in asked_ids or ():
        try:
            ids.append(uuid.UUID(str(i)))
        except ValueError:
            continue
    if not ids:
        return []
    asked = list(Message.objects.filter(id__in=ids[:50], mood=mood))
    if not asked:
        return []
    last = (Message.objects.filter(session_id=session_id, sender_id=agent, is_sidechain=False, thought__isnull=True,
                                   tooluse__isnull=True, toolresult__isnull=True)
            .order_by('-created_at').first())
    text = (prose(last.content) or '').strip() if last is not None else ''
    if not text or is_wrapper(text) or quiet_reason(text) is not None:
        return []
    text, _ = redact(text)
    entity = ThinkingEntity.objects.filter(name=agent, is_biological_human=False).first()
    if entity is None:
        return []
    made = []
    # Asked on Tidings: under the comment that asked.
    for notice in asked:
        c = notice.content if isinstance(notice.content, dict) else {}
        if notice.source_file != COMMENT_SOURCE or agent not in (c.get('asks') or []):
            continue
        comment = TidingComment.objects.filter(id=c.get('comment_id')).first()
        if comment is not None and not TidingComment.objects.filter(parent=comment, by=entity).exists():
            made.append(TidingComment.objects.create(tiding_id=comment.tiding_id, by=entity, parent=comment, text=text))
    # Asked in the Mood, by a reply in a link's discussion (or the link's own message): under that reply.
    plain = {str(m.id) for m in asked if m.source_file != COMMENT_SOURCE}
    if plain:
        for tiding in Tiding.objects.filter(message__mood=mood).order_by('at'):
            here = {str(tiding.message_id)} | {node['id'] for node in _walk(_tree(tiding.message_id))}
            for mid in sorted(plain & here):
                if not TidingComment.objects.filter(tiding=tiding, parent_message_id=mid, by=entity).exists():
                    made.append(TidingComment.objects.create(tiding=tiding, by=entity, parent_message_id=mid, text=text))
    return made


def people_said(tree, agents):
    """How much people have said in a reply tree: what an agent says doesn't raise a link (Justin, 23 Oct)."""
    return sum((node.get('sender') not in agents) + people_said(node.get('replies', []), agents) for node in tree)


def post_text(text, url):
    """What the promoted message says besides its link (Justin, 24 Oct): shown in full at the top of the
    discussion, as the post's own words -- not a comment: not counted, not voted on, and answered by a
    top-level comment, as HN shows a text post's text. The link is the title already, so it's left out
    where it stands apart -- a line of its own, or a line's start or end ("This one: <link>") -- and kept
    where it's part of a sentence; a [words](link) keeps its words. '' when the link is all there is."""
    from .mood_view import reply_to
    text = reply_to(text or '')[1]
    link = '<?' + re.escape(url) + r'>?(?![\w/#?=&%-])'
    text = re.sub(r'\[([^\]]*)\]\(' + re.escape(url) + r'\)', r'\1', text)          # [words](link): the words
    text = re.sub(r'[ \t]*[:\u2013\u2014-]?[ \t]*' + link + r'(?=[.!?]*[ \t]*$)', '', text, flags=re.M)  # ending a line
    text = re.sub(r'^[ \t]*' + link + r'[ \t]*[:\u2013\u2014-]?[ \t]*', '', text, flags=re.M)              # starting one
    text = text.strip()
    return text if re.search(r'\w', text) else ''


def may_change_said(node, device):
    """(may edit, may delete) a node of a link's discussion: change_said's rules, for the page's buttons."""
    from types import SimpleNamespace
    from django.utils import timezone
    from . import retract
    if device is None or node.get('changed') == 'deleted':
        return False, False
    if node.get('kind') == 'comment':
        age, mine = timezone.now() - node['at'], node['sender'] == device.entity_id
        return mine and age <= retract.EDIT_OWN_WITHIN, retract.is_admin(device) or (mine and age <= retract.DELETE_OWN_WITHIN)
    if node.get('said_at') is None:
        return False, False
    said = SimpleNamespace(sender_id=node['sender'], source_file=node.get('source', ''), created_at=node['said_at'])
    return retract.may_edit(said, device), retract.may_delete(said, device)


def change_said(tiding, device, kind, node_id, text=None, delete=False):
    """Edit or delete something said about a link (Justin, 21 Oct: "How about comment editing?"), by the
    Mood's own rules: yours, within a day to edit and a week to delete, or anyone's deleted by an admin;
    what goes is sealed to the recovery key first (services/retract.py). `kind` 'comment', said here: its
    words change in place, a deleted one stays as '[deleted]' so what answered it stays under it. 'mood', a
    reply in the Mood: changed there, as the Mood changes it, still answering what it answered.
    {'id', 'changed'}; Refused with why if not."""
    from types import SimpleNamespace
    from django.utils import timezone
    from conversations.models import Message, TidingComment
    from . import retract
    from .mood_view import prose, reply_to
    if device is None:
        raise Refused('sign in first', status=401)
    if not delete:
        text = (text if isinstance(text, str) else '').replace('\x00', '').strip()
        if not text:
            raise Refused('nothing left: delete it instead')
    if kind == 'mood':
        message = Message.objects.filter(id=node_id, mood_id=tiding.message.mood_id).select_related('mood').first()
        if message is None or not any(node['id'] == str(node_id) for node in _walk(_tree(tiding.message_id))):
            raise Refused('no such comment under this link', status=404)
        try:
            if delete:
                if not retract.may_delete(message, device):
                    raise Refused('only what you wrote, within a week of writing it, can be deleted '
                                  '(or anything, by an admin with their SSH key)', status=403)
                retract.delete(message, device.entity_id)
            else:
                if not retract.may_edit(message, device):
                    raise Refused('only what you wrote here yourself, within a day of writing it, can be edited',
                                  status=403)
                target, _ = reply_to(prose(message.content))
                retract.edit(message, f'↩ #m-{target}\n{text}' if target else text, device.entity_id)
        except retract.Refused as e:
            raise Refused(str(e), status=e.status)
        return {'id': str(message.id), 'changed': 'deleted' if delete else 'edited'}
    if kind != 'comment':
        raise Refused('expected a comment or a Mood reply')
    row = TidingComment.objects.filter(id=node_id, tiding=tiding).first()
    if row is None or row.changed == 'deleted':
        raise Refused('no such comment under this link', status=404)
    age, mine = timezone.now() - row.at, row.by_id == device.entity_id
    if delete and not (retract.is_admin(device) or (mine and age <= retract.DELETE_OWN_WITHIN)):
        raise Refused('only what you wrote, within a week of writing it, can be deleted '
                      '(or anything, by an admin with their SSH key)', status=403)
    if not delete and not (mine and age <= retract.EDIT_OWN_WITHIN):
        raise Refused('only what you wrote here yourself, within a day of writing it, can be edited', status=403)
    try:
        key = retract._ready(device.entity_id)
    except retract.Refused as e:
        raise Refused(str(e), status=e.status)
    # Sealed as a Mood message's words are: the same record (SealedCopy keeps an id, not a key), the same key.
    said = SimpleNamespace(pk=row.id, content=row.text, source_file=COMMENT_SOURCE, sender_id=row.by_id,
                           mood=tiding.message.mood, mood_id=tiding.message.mood_id)
    kind = 'deleted' if delete else 'edited'
    retract._seal(said, kind, device.entity_id, key, [])
    if not delete:
        text, _ = redact(text)
    TidingComment.objects.filter(pk=row.pk).update(text=retract.DELETED if delete else text, changed=kind,
                                                   changed_at=timezone.now())
    retract._say_so(said, device.entity_id, f'a comment on Tidings {kind}')
    return {'id': str(row.id), 'changed': kind}


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
        'post': said_html(post_text(text, tiding.url)) if post_text(text, tiding.url) else '',
        'score': scored.get(str(tiding.id), 0),
        'mine': str(tiding.id) in mines,
        'tags': tiding.tags or [],
        'muted': tiding.muted_in_mood,
        'embed': embed_of(tiding.url),
        'comments': tree_of(tiding, message.id, viewer=viewer),
    }


def rank(score, comments, at, now, weights):
    """How high a link stands on Tidings (Justin, 22 Oct): HN's gravity on our clock --
    (1 + upvotes + weight x comments) / (1 + age in units) ** gravity. The 1 lets a new link with no votes
    start on the board; talk counts for something; and nothing stays on top long without more."""
    age = max((now - at).total_seconds() / 3600, 0) / weights['tidings_age_hours']
    return (1 + score + weights['tidings_comment_weight'] * comments) / (1 + age) ** weights['tidings_gravity']


def pinned_ids():
    """The links pinned to the top, last pinned first."""
    from . import settings as knobs
    return list(knobs.global_value(knobs.TIDINGS_PINNED) or [])


def pin(tiding, device, on):
    """Pin a link to the top of Tidings, or let it go: anyone signed in with an SSH key (Justin, 22 Oct).
    The ids pinned now, last pinned first."""
    from . import settings as knobs
    if device is None or device.tier != 'key':
        raise Refused('pinning is for someone signed in with an SSH key', status=403)
    ids = [i for i in pinned_ids() if i != str(tiding.id)]
    ids = [str(tiding.id)] + ids if on else ids
    knobs.change(knobs.TIDINGS_PINNED, ids, by=device.entity, note=('pinned ' if on else 'unpinned ') + str(tiding.id))
    from . import front
    front.forget()
    return knobs.clean(knobs.TIDINGS_PINNED, ids)


def listed(limit=LISTED, viewer=None, order='top'):
    """The links on Tidings as the page shows them: numbered there, each with its score and whether
    `viewer` voted. `order` 'top': pinned first, then by rank (Justin, 22 Oct); 'new': newest first."""
    from django.utils import timezone
    from conversations.models import Tiding
    from . import settings as knobs
    from .links import current
    from .mood_view import prose, snippet_of
    from .retract import DELETED
    index = current()
    knob = knobs.global_values([knobs.TIDINGS_PINNED, 'tidings_age_hours', 'tidings_gravity', 'tidings_comment_weight'])
    pins = list(knob[knobs.TIDINGS_PINNED] or [])
    found = list(Tiding.objects.select_related('message', 'by').order_by('-at')[:CANDIDATES if order == 'top' else limit])
    have = {str(t.id) for t in found}
    found += [t for t in Tiding.objects.select_related('message', 'by').filter(id__in=[p for p in pins if p not in have])]
    rows = []
    for tiding in found:
        message = tiding.message
        text = prose(message.content) or ''
        if text == DELETED or message.mood_id is None:
            continue
        rows.append(tiding)
    scored, mines = _tiding_scores(rows, viewer)
    # How much has been said: the Mood's replies (the links index, free) and the comments said on Tidings,
    # counted in one question of the database -- not read one link at a time.
    from django.db.models import Count
    from conversations.models import TidingComment
    # And what people said, for ranking: an agent's comments are shown and counted, but don't raise a link.
    from django.db.models import Q
    from .mood_view import agent_names
    said, people = {}, {}
    for tid, c, by_people in (TidingComment.objects.filter(tiding__in=rows).values('tiding_id')
                              .annotate(c=Count('id'), p=Count('id', filter=Q(by__is_biological_human=True)))
                              .values_list('tiding_id', 'c', 'p')):
        said[str(tid)], people[str(tid)] = c, by_people
    agents = agent_names()
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
            'comments': count(_tree(message.id, index)) + said.get(str(tiding.id), 0),
            'talk': people_said(_tree(message.id, index), agents) + people.get(str(tiding.id), 0),
            'tags': tiding.tags or [],
            'muted': tiding.muted_in_mood,
            'score': scored.get(str(tiding.id), 0),
            'mine': str(tiding.id) in mines,
            'pinned': str(tiding.id) in pins,
        })
    if order != 'top':
        out.sort(key=lambda link: link['at'], reverse=True)
        return out[:limit]
    now = timezone.now()
    weights = {k: float(knob[k]) for k in ('tidings_age_hours', 'tidings_gravity', 'tidings_comment_weight')}
    for link in out:
        link['rank'] = rank(link['score'], link['talk'], link['at'], now, weights)
    out.sort(key=lambda link: link['rank'], reverse=True)
    out.sort(key=lambda link: pins.index(link['id']) if link['pinned'] else len(pins))  # pinned first, as pinned
    return out[:limit]
