"""magenta's front page (Justin, 22 Oct): the mark, a line about who it's for, the top three links on
Tidings, and who has spoken lately and where -- their names and the Moods', not what they said.

The line is one of PickiPedia's Magenta:Slogans -- each '*' line there; anyone may add or change one --
taking turns. Justin's own until that page is there, or when the wiki can't be read.

Read by anyone, signed in or not, so it's kept for a little while rather than asked of the
database on every visit.
"""

import functools
import re

from django.core.cache import cache

TOP = 5       # links from Tidings (Justin, 23 Oct: five)
LATELY = 5    # who spoke lately, and where
KEEP_FOR = 30  # seconds
_KEY = 'front:state'
SLOGANS_PAGE = 'Magenta:Slogans'
SLOGAN = ('a place for hackers, bluegrassers, deadheads, cyclists, generalists, and all those smartass folk come '
          'descended from monkeys')
SLOGANS_FOR = 600      # seconds the page's lines are kept
SLOGANS_KEPT = 86400   # and the last good ones, should the wiki not answer
MAX_SLOGANS, MAX_SLOGAN = 60, 240
HEX = set('0123456789abcdef')
# Each agent's own colour, as the Moods page draws them (AGENT_COLORS in moods.html, Justin, 21 Oct).
AGENT_COLOURS = {'magent': '#ff67fd', 're': '#ff3c00', 'blu': '#1976d2'}


def top_tidings(limit=TOP):
    """The top of Tidings as Tidings has it -- pinned, then by rank (tiding_links.rank): [{'id', 'url',
    'title', 'host', 'score', 'comments', 'pinned'}]."""
    from . import tiding_links
    return [{k: link[k] for k in ('id', 'url', 'title', 'host', 'score', 'comments', 'pinned')}
            for link in tiding_links.listed(limit=limit)]


def name_html(name, wiki_names, looks):
    """A person's name as the Moods page writes it (Justin, 23 Oct): their PickiPedia name (JMyles, not
    justin), and in their own art and colours if they've set any (settings.looks); an agent without art,
    in its colour. Safe HTML."""
    from django.utils.html import escape
    look = looks.get(name) or {}
    art = look.get('art') or ''
    if art:
        fg, bg = look.get('colours') or '', look.get('backs') or ''
        cells = []
        for i, ch in enumerate(art):  # a code each for its letter and its background: 0-f, ANSI's sixteen
            codes = []
            if i < len(fg) and fg[i] in HEX:
                codes.append(f'a{fg[i]}')
            if i < len(bg) and bg[i] in HEX:
                codes.append(f'b{bg[i]}')
            cells.append(f'<span class="{" ".join(codes)}">{escape(ch)}</span>' if codes else escape(ch))
        return f'<span class="art" title="{escape(wiki_names.get(name, name))}">{"".join(cells)}</span>'
    colour = AGENT_COLOURS.get(name, '')
    shown = escape(wiki_names.get(name, name))
    return f'<span class="agent" style="--agent: {colour}">{shown}</span>' if colour else shown


def lately(limit=LATELY, scan=300):
    """Who spoke last, and in which Mood, newest first: [{'who', 'name', 'mood', 'title', 'said', 'at', 'ago'}].
    What was said (a person's post, an agent speaking), not an agent's thinking or tool calls, its silences,
    the poller's wakes or the system's lines. Each person in each Mood once, at their latest (Justin,
    22 Oct: "the five most recent user-mood combos"), with its first 80 characters (23 Oct). Archived Moods
    aren't shown."""
    from django.utils.timesince import timesince
    from conversations.models import Message, ThinkingEntity
    from . import settings as knobs
    from . import wiki_auth
    from .mood_view import is_wrapper, prose, quiet_reason, reply_to, snippet_of
    archived = knobs.archived_slugs()
    wiki_names, looks = wiki_auth.names(), knobs.looks()
    people = set(ThinkingEntity.objects.values_list('name', flat=True))
    rows = (Message.objects.filter(mood__isnull=False, is_sidechain=False, thought__isnull=True, tooluse__isnull=True,
                                   toolresult__isnull=True, sender_id__in=people)
            .select_related('mood').order_by('-created_at').only('content', 'created_at', 'sender_id', 'mood')[:scan])
    out = []
    for msg in rows:
        if msg.mood.slug in archived:
            continue
        text = prose(msg.content)
        if not text or is_wrapper(text) or quiet_reason(text) is not None:
            continue
        if any((said['who'], said['mood']) == (msg.sender_id, msg.mood.slug) for said in out):
            continue  # this voice in this Mood is here already, at its latest
        words = snippet_of(reply_to(text)[1])
        out.append({'who': msg.sender_id, 'name': name_html(msg.sender_id, wiki_names, looks),
                    'mood': msg.mood.slug, 'title': msg.mood.title or msg.mood.slug,
                    'said': words[:80] + ('…' if len(words) > 80 else ''),
                    'at': msg.created_at, 'ago': timesince(msg.created_at).split(',')[0]})  # "1 day", not "1 day, 9 hours"
        if len(out) == limit:
            break
    return out


def slogans_in(wikitext):
    """The lines of a slogans page: each '*' line as plain words -- a [[link]] its words, bold and italic
    marks gone, tags and templates gone -- each once, in the page's order. A line an agent wrote waits,
    wrapped in {{Bot_proposes|...}}, until a person has verified it (Special:VerifyBotEdits): only then is it
    shown, as the wiki asks of what bots write."""
    out = []
    for line in (wikitext or '').splitlines():
        if not line.startswith('*'):
            continue
        text = line.lstrip('*').strip()
        if re.search(r'\{\{\s*Bot[ _]proposes', text, re.I):
            continue
        text = re.sub(r'\{\{[^{}]*\}\}', '', text)                        # any other template
        text = re.sub(r'\[\[(?:[^\]|]*\|)?([^\]]*)\]\]', r'\1', text)   # [[Page|words]], [[words]]
        text = re.sub(r'\[https?://\S+\s+([^\]]*)\]', r'\1', text)        # [url words]
        text = re.sub(r"'{2,}", '', text)
        text = re.sub(r'<[^>]*>', '', text)
        text = ' '.join(text.split())[:MAX_SLOGAN]
        if text and text not in out:
            out.append(text)
    return out[:MAX_SLOGANS]


def slogans(http=None):
    """Magenta:Slogans' lines, kept ten minutes; the last good ones for a day if the wiki won't answer;
    Justin's line if there have never been any."""
    found = cache.get('front:slogans')
    if found is not None:
        return found
    import requests
    from .mood_view import pickipedia_url
    try:
        response = (http or requests).get(f'{pickipedia_url()}/index.php',
                                         params={'title': SLOGANS_PAGE, 'action': 'raw'}, timeout=4,
                                         headers={'User-Agent': 'memory-lane (magenta; front page)'})
        if response.status_code not in (200, 404):
            raise ValueError(f'PickiPedia answered {response.status_code}')
        found = (slogans_in(response.text) if response.status_code == 200 else []) or [SLOGAN]
    except Exception:  # noqa: BLE001 -- the wiki away: what it said last, or Justin's line; asked again in a minute
        found = cache.get('front:slogans:last') or [SLOGAN]
        cache.set('front:slogans', found, 60)
        return found
    cache.set('front:slogans', found, SLOGANS_FOR)
    cache.set('front:slogans:last', found, SLOGANS_KEPT)
    return found


@functools.lru_cache(maxsize=8)
def qr_svg(url):
    """A QR code for `url` (the front page's own address: Justin, 23 Oct, to hold up for a phone across the
    table), as inline SVG: dark squares on light, which every phone's camera reads."""
    import segno
    return segno.make(url, error='m').svg_inline(scale=4, border=2, dark='#0e0c10', light='#ece7e1',
                                                 svgclass='qr', omitsize=True)


def forget():
    """Drawn again on the next visit: a pin changes the top at once, not in half a minute."""
    cache.delete(_KEY)


def state():
    found = cache.get(_KEY)
    if found is None:
        found = {'tidings': top_tidings(), 'lately': lately()}
        cache.set(_KEY, found, KEEP_FOR)
    return found
