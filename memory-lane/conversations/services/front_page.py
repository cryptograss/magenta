"""PickiPedia's front page, the live bits: two templates the wiki bot keeps written.

Justin (#pickipedia-style-and-main-page, 11 Nov 2026) wanted the Main Page to carry the top
tidings on magenta and the last few things people said in the Moods. The wiki can't read
magenta from the browser (no CORS), and the words are ours to publish, so magenta writes them
to the wiki as wikitext, the way it ticks to-do items and writes the pull requests' cards
(todo_ticks.Bot): Template:Magenta tidings and Template:Magenta latest, which Template:Front
page transcludes. Rewritten at most every LOOK_EVERY seconds, and only when they'd change.
"""

import threading
from datetime import datetime
from urllib.parse import unquote, urlparse

from django.core.cache import cache
from django.utils.dateparse import parse_datetime

LOOK_EVERY = 600     # seconds between looks
TIDINGS = 7          # links shown
LATEST = 6           # posts shown
TEXT = 240           # characters of a post kept, as on a person's page
LOOK_BACK = 80       # newest human posts looked through for LATEST with words
TIDINGS_PAGE = 'Template:Magenta tidings'
LATEST_PAGE = 'Template:Magenta latest'

_KICKER = ("font-family:ui-monospace,Menlo,Consolas,monospace; font-size:.7em; letter-spacing:.08em; "
           "text-transform:uppercase; color:#7a6450;")
_ROW = "padding:.5em 0; border-bottom:1px solid #e4dccb;"
_CARD = ("flex:1 1 230px; min-width:200px; padding:.6em .8em; border:1px solid #e4dccb; border-radius:8px; "
         "font-size:.92em; line-height:1.4;")
_BY = ("font-family:ui-monospace,Menlo,Consolas,monospace; font-size:.74em; letter-spacing:.06em; "
       "color:#7a6450; margin-bottom:.3em;")


def magenta_url():
    from django.conf import settings
    return getattr(settings, 'MAGENTA_URL', 'https://magenta.cryptograss.live').rstrip('/')


def plain(text):
    """Words as wikitext shows them verbatim: one line, inside nowiki, with nothing in them read as markup."""
    text = ' '.join(str(text or '').split())
    return '<nowiki>' + text.replace('</nowiki', '&lt;/nowiki') + '</nowiki>'


def _when(value):
    if isinstance(value, datetime):
        return value
    return parse_datetime(str(value or '')) if value else None


def wiki_title(url):
    """The page a PickiPedia address names, or None if it isn't one ('/wiki/Song:August' -> 'Song:August')."""
    from conversations.services.mood_view import pickipedia_url
    parts = urlparse(url or '')
    if parts.netloc != urlparse(pickipedia_url()).netloc or not parts.path.startswith('/wiki/'):
        return None
    return unquote(parts.path[len('/wiki/'):]).replace('_', ' ') or None


def tidings_wikitext(links):
    """The rows of Template:Magenta tidings: the links as Tidings ranks them (tiding_links.listed)."""
    rows = []
    for link in links[:TIDINGS]:
        at = _when(link.get('at'))
        kicker = [link.get('host') or '', link.get('by') or '']
        if at:
            kicker.append(f"{at.day} {at.strftime('%b')}")
        if link.get('release'):
            kicker.append('release')
        kicker = ('📌 ' if link.get('pinned') else '') + ' · '.join(k for k in kicker if k)
        title = wiki_title(link.get('url'))
        label = plain(link.get('title') or link.get('url') or '')
        head = f'[[{title}|{label}]]' if title else f"[{link.get('url')} {label}]"
        talk = int(link.get('talk') or 0)  # what people said of it: comments, and the thread it was shared in
        more = (f'<div style="font-size:.8em; color:#7a6450;">[{magenta_url()}/tidings/{link["id"]}/ '
                f'{talk} in the talk]</div>') if talk else ''
        rows.append(f'<div style="{_ROW}"><div style="{_KICKER}">{plain(kicker)}</div>'
                    f'<div style="font-weight:600; line-height:1.3;">{head}</div>{more}</div>')
    rows.append(f'<div style="font-size:.8em; color:#7a6450; padding:.6em 0 0;">[{magenta_url()}/tidings/ '
                f'All the tidings on magenta →]</div>')
    return '<div class="pp-magenta-tidings">\n' + '\n'.join(rows) + '\n</div>'


def latest(limit=LATEST):
    """The last `limit` posts with words by people, newest first, across the Moods that aren't archived."""
    from conversations.models import Message, ThinkingEntity
    from conversations.services import settings as knobs
    from conversations.services.mood_view import MACHINERY_SENDERS, prose, snippet_of
    people = set(ThinkingEntity.objects.filter(is_biological_human=True).values_list('name', flat=True))
    posts = (Message.objects.filter(is_sidechain=False, mood__isnull=False, sender_id__in=people - MACHINERY_SENDERS)
             .exclude(mood__slug__in=knobs.archived_slugs())
             .select_related('mood').order_by('-created_at')[:LOOK_BACK])
    out = []
    for post in posts:
        text = snippet_of(prose(post.content).strip())
        if not text or text == '[deleted]' or text.startswith('/'):
            continue
        out.append({'id': str(post.id), 'by': post.sender_id, 'mood': post.mood.slug, 'at': post.created_at,
                    'text': text if len(text) <= TEXT else text[:TEXT - 1].rstrip() + '…'})
        if len(out) == limit:
            break
    return out


def latest_wikitext(posts):
    """The cards of Template:Magenta latest."""
    cards = []
    for post in posts:
        at = _when(post['at'])
        when = at.strftime('%d %b %H:%MZ').lstrip('0') if at else ''
        mood = f"[{magenta_url()}/moods/{post['mood']}/#m-{post['id']} #{post['mood']}]"
        cards.append(f'<div style="{_CARD}"><div style="{_BY}">\'\'\'{plain(post["by"])}\'\'\' · {mood} · {when}</div>'
                     f'{plain(post["text"])}</div>')
    if not cards:
        cards.append('<div style="color:#7a6450;">Nothing said yet.</div>')
    return '<div class="pp-magenta-latest" style="display:flex; flex-wrap:wrap; gap:.8em;">\n' + '\n'.join(cards) + '\n</div>'


def page(body, what):
    return (f"<noinclude>\n{what} '''Written by magenta's wiki bot''' (memory-lane, services/front_page.py) every "
            f"few minutes, for [[Template:Front page]]: an edit here is gone at the next write. "
            f"Change it on [{magenta_url()}/ magenta] instead.\n\n[[Category:Templates]]\n</noinclude>"
            f"<includeonly>{body}</includeonly>")


def look(bot=None):
    """Write both templates, if they'd change: {page: 'written' | 'same' | 'refused'}."""
    from conversations.services import tiding_links, todo_ticks
    bot = bot or todo_ticks.Bot(purpose='front page')
    if not bot.login():
        return {}
    texts = {
        TIDINGS_PAGE: page(tidings_wikitext(tiding_links.listed(order='top')),
                           f"The top links on [{magenta_url()}/tidings/ magenta's Tidings]: the pinned ones, then by rank."),
        LATEST_PAGE: page(latest_wikitext(latest()),
                          f"The last few things people said on [{magenta_url()}/ magenta], across the Moods."),
    }
    return {title: bot.write(title, text, "The front page's feed, from magenta") for title, text in texts.items()}


def nudge():
    """Look, in the background, if it's been LOOK_EVERY seconds and the bot can write."""
    from . import todo_ticks
    if not todo_ticks.enabled() or not cache.add('front-page:lock', 1, LOOK_EVERY):
        return False

    def run():
        from django.db import connection
        try:
            look()
        except Exception:
            pass  # the wiki, or the network: the next look tries again
        finally:
            connection.close()
    threading.Thread(target=run, daemon=True).start()
    return True
