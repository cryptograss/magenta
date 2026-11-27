"""A PickiPedia page on Tidings, drawn as an article's opening: its first paragraphs and its picture.

Justin, 11 Nov (#interfaces), of "Kuba's Last Merged Commits": "pickipedia pages posted as tidings need to
have a special look. It needs to include the first couple paragraphs or so ... as well as a pic if there is one."

The measure: paragraphs are taken whole, in order, until there are LEAD_CHARS characters of them or
LEAD_PARAGRAPHS of substance; a short one (a dateline, "11 Nov 2026") comes along without counting. Only the
page's own paragraphs, before its first heading once there's something: not its lists, tables or boxes. The
picture is the first on the page big enough to look at and not a banner or a strip (a graph 1376x212 isn't
the page's face). The words are rebuilt here from the wiki's HTML, text escaped and only links, bold and
italics kept, so nothing of the page's markup reaches magenta's.

Nothing unverified (Justin, 12 Nov: "not to show unverified content at all"): what a bot proposed and no person
has verified yet (PickiPediaVerification's <proposed> and Bot_proposes, drawn pv-proposed, bot-proposal,
claim-unverified) is left out, words and pictures; a proposal to remove something leaves it in, as it reads now,
without the proposal's badge. And the page's "Listen in Rabbithole" (Template:Listen in Rabbithole, drawn
pp-listen-stack) is taken out of the words and drawn as a button above them ("a fully rendered button to play on
rabbithole, as the first bit of content here"): `listen`, {'play': {href, text}, 'trail': {...}}.

A post (somebody's own writing, User:Name/Title, read on the wiki in its Broadside skin) opens here under the
same masthead it has there: `post`, {'headline', 'kicker', 'dek', 'author': {'name', 'url', 'avatar'},
'published', 'reads', 'cap'}, from the wiki's own card of the page (api.php?action=pagecard). Justin, 13 Nov
(#pickipedia-style-and-main-page): "it needs to preview really nicely on its tidings page and then be a smooth
cognitive transition when the user then visits the full page." What that masthead says, the dek and the kicker
a post writes in its own body (pp-dek-source, pp-kicker-source), isn't said again in the words under it.
"""

import hashlib
import re
from html import escape
from html.parser import HTMLParser
from urllib.parse import unquote

LEAD_CHARS = 500
LEAD_PARAGRAPHS = 3
SHORT = 40           # fewer characters than this: a dateline or a byline, not one of the paragraphs counted
CAP_CHARS = 80       # a post's paragraph of fewer characters is a line, not the one its large first letter stands on
PICTURE_MIN = 120    # the file's width in pixels, at least
PICTURE_RATIO = 3.0  # longer side over shorter, at most
KEPT, MISS = 600, 60  # seconds: an edit shows within ten minutes; a wiki that didn't answer is asked again soon
# Bump when lead_of reads a page differently: its key changes, so a deploy shows the new reading at once,
# not after an opening the old code kept runs out (Justin, 12 Nov: merged and redeployed, "why no refreshed?").
SHAPE = 3
NOT_ARTICLES = ('special', 'file', 'image', 'release', 'media')  # a Release has its own page (tiding_links._release_of)

VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'source', 'track', 'wbr'}
KEPT_TAGS = {'b': 'b', 'strong': 'b', 'i': 'i', 'em': 'i'}
UNVERIFIED = {'bot-proposal', 'claim-unverified', 'pv-proposed__badge', 'pv-proposed__byline', 'pv-verify-all'}
# The line under a post's headline and the line over it, which the post writes in its own body and its masthead
# says (PickiPedia's Posts/Post::sourced). Under that masthead here they aren't its opening's words too.
MASTHEAD = {'pp-dek-source', 'pp-kicker-source'}


def unverified(classes):
    """Whether an element is what a bot proposed and nobody has verified: its words aren't the page's yet."""
    return bool(UNVERIFIED.intersection(classes)) or ('pv-proposed' in classes and 'pv-proposed--removal' not in classes)


def title_of(url, base):
    """The article a PickiPedia address names, or None: not a special page, a file, a Release, or an action."""
    prefix = f'{base}/wiki/'
    if not (url or '').startswith(prefix):
        return None
    rest = url[len(prefix):]
    if '?' in rest:
        return None  # ?action=edit, ?oldid=...: not the page as it reads
    title = unquote(rest.split('#')[0]).replace('_', ' ').strip()
    if not title or title.split(':')[0].strip().lower() in NOT_ARTICLES:
        return None
    return title


class _Lead(HTMLParser):
    """The top-level paragraphs of a parsed page (as escaped HTML, and their length as text) and its first
    picture worth showing."""

    def __init__(self, base, post=False):
        super().__init__(convert_charrefs=True)
        self.base = base
        self.post = post      # read for a post drawn under its masthead: what the masthead says isn't read again
        self.raw = 0          # inside this many <style>s and <script>s: not words
        self.plain = []       # the paragraph being read, as the wiki counts its characters (all its words)
        self.source = False   # it is one of the masthead's
        self.cap = None       # which of blocks the large first letter stands on (-1: a paragraph not kept here)
        self.cap_wait = None  # one long enough for it, until what follows shows it isn't a note over a rule
        self.cap_done = False  # settled, or past the first heading
        self.depth = 0
        self.blocks = []      # ('p', html, chars) or ('h', '', 0), in page order
        self.para = None      # the paragraph being read: [html parts]
        self.para_depth = None
        self.open = []        # inside it: what each open tag closes with ('' for a tag not kept)
        self.links = []       # the depths of the links it's inside
        self.bare = 0         # how much of it is words outside any link
        self.skip_at = None   # depth of an element whose words aren't the article's (references, styles)
        self.picture = None
        self.file_href = None
        self.figure_at = None
        self.caption_at = None
        self.caption = []
        self.listen = {}        # the page's first Listen in Rabbithole: {'play': {...}, 'trail': {...}}
        self.listen_at = None   # depth of the listen stack being read
        self.listen_kind = None  # ('play' or 'trail', the depth of its pill)
        self.listen_link = None  # (kind, href, [words], depth) of the link being read in it

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        classes = (a.get('class') or '').split()
        if tag == 'img':
            self._img(a, classes)
        if tag in VOID:
            if tag == 'br' and self.para is not None and self.skip_at is None:
                self.para.append(' ')
            if self.depth == 1:
                self._follows(tag, classes)
            return
        self.depth += 1
        if tag in ('style', 'script'):
            self.raw += 1
        if self.depth == 2 and tag not in ('style', 'script'):  # the wiki's rule doesn't see those either
            self._follows(tag, classes)
        if self.skip_at is None and (tag in ('style', 'script') or 'reference' in classes or 'mw-editsection' in classes
                                     or unverified(classes) or (self.post and MASTHEAD.intersection(classes))):
            self.skip_at = self.depth
        # The page's Listen in Rabbithole: a button of its own, above the words; not words in a paragraph.
        if self.skip_at is None and self.listen_at is None and 'pp-listen-stack' in classes and not self.listen:
            self.listen_at = self.skip_at = self.depth
        if self.listen_at is not None:
            if 'pp-listen-rh' in classes:
                self.listen_kind = ('play', self.depth)
            elif 'pp-trail-rh' in classes:
                self.listen_kind = ('trail', self.depth)
            elif tag == 'a' and self.listen_kind and self.listen_link is None:
                self.listen_link = (self.listen_kind[0], a.get('href') or '', [], self.depth)
        if tag == 'figure':
            self.figure_at, self.file_href = self.depth, None
        elif tag == 'figcaption' and self.figure_at is not None:
            self.caption_at, self.caption = self.depth, []
        elif tag == 'a' and 'mw-file-description' in classes:
            self.file_href = a.get('href')
        # A heading: <h2> itself, or (MediaWiki 1.43 on) the <div class="mw-heading"> around it.
        if self.depth == 2 and (re.fullmatch(r'h[1-6]', tag) or 'mw-heading' in classes):
            self.blocks.append(('h', '', 0))
        if self.para is None and self.depth == 2 and tag == 'p':
            self.para, self.para_depth, self.open, self.links, self.bare = [], self.depth, [], [], 0
            self.plain, self.source = [], bool(MASTHEAD.intersection(classes))
            return
        if self.para is not None and tag == 'a':
            self.links.append(self.depth)
        if self.para is not None and self.skip_at is None:
            if tag == 'a':
                href, kind = self._href(a.get('href') or '', classes)
                if href:
                    self.para.append(f'<a class="{kind}" href="{escape(href)}" target="_blank" rel="noopener">')
                    self.open.append('</a>')
                    return
            if tag in KEPT_TAGS:
                self.para.append(f'<{KEPT_TAGS[tag]}>')
                self.open.append(f'</{KEPT_TAGS[tag]}>')
                return
        if self.para is not None:
            self.open.append('')

    def handle_startendtag(self, tag, attrs):
        if tag in VOID:
            self.handle_starttag(tag, attrs)
        else:
            self.handle_starttag(tag, attrs)
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if self.para is not None and self.depth == self.para_depth and tag == 'p':
            html = ''.join(self.para + [c for c in reversed(self.open)])
            html = re.sub(r'\s+', ' ', html).strip()
            text = re.sub(r'<[^>]+>', '', html)
            # Nothing but a link ("edit", from a template on the page): the page's furniture, not its words.
            kept = bool(text.strip() and self.bare)
            if kept:
                self.blocks.append(('p', html, len(text)))
            if not self.cap_done and not self.source and len(re.sub(r'\s+', ' ', ''.join(self.plain)).strip()) >= CAP_CHARS:
                self.cap_wait = len(self.blocks) - 1 if kept else -1
            self.para, self.para_depth, self.open = None, None, []
        elif self.para is not None and self.open:
            closer = self.open.pop()
            if closer and self.skip_at is None:
                self.para.append(closer)
        if self.links and self.links[-1] == self.depth:
            self.links.pop()
        if self.caption_at == self.depth:
            if self.picture is not None and self.picture.get('_figure') == self.figure_at and not self.picture['caption']:
                self.picture['caption'] = re.sub(r'\s+', ' ', ''.join(self.caption)).strip()[:300]
            self.caption_at = None
        if self.figure_at == self.depth:
            self.figure_at = None
        if self.listen_link and self.listen_link[3] == self.depth:
            kind, href, words, _ = self.listen_link
            text = re.sub(r'^[\s▶🐇]+', '', re.sub(r'\s+', ' ', ''.join(words).replace('\xa0', ' '))).strip()
            if href.startswith('https://') and text and kind not in self.listen:
                self.listen[kind] = {'href': href, 'text': text[:120]}
            self.listen_link = None
        if self.listen_kind and self.listen_kind[1] == self.depth:
            self.listen_kind = None
        if self.listen_at == self.depth:
            self.listen_at = None
        if self.skip_at == self.depth:
            self.skip_at = None
        if tag in ('style', 'script') and self.raw:
            self.raw -= 1
        self.depth -= 1

    def _follows(self, tag, classes):
        """One of the page's own elements opens: what that settles about a post's large first letter. The rule is
        the wiki's, where the post's own page sets the letter (PickiPedia's Posts/Post::capAt; change one, change
        the other): the first of the post's paragraphs that is one. Not a line (fewer than CAP_CHARS characters: a
        note, a date, "Work in progress"), not a note set off by a rule under it, not what the masthead says, and
        not past the first heading, which opens its own part."""
        if self.cap_done:
            return
        if self.cap_wait is not None:
            if tag != 'hr':
                self.cap, self.cap_done, self.cap_wait = self.cap_wait, True, None
                return
            self.cap_wait = None
        if re.fullmatch(r'h[1-6]', tag) or 'mw-heading' in classes:
            self.cap_done = True

    def handle_data(self, data):
        if self.para is not None and not self.raw:
            self.plain.append(data)
        if self.listen_link:
            self.listen_link[2].append(data)
        if self.skip_at is not None:
            return
        if self.caption_at is not None:
            self.caption.append(data)
        if self.para is not None:
            self.para.append(escape(data))
            if not self.links:
                self.bare += len(data.strip())

    def _href(self, href, classes=()):
        """Where a link in the opening goes, and what it is: 'wiki' (a page on PickiPedia, drawn blue), 'wiki new'
        (one not written yet, drawn red, as the wiki draws it: Justin, 12 Nov), 'ext' (elsewhere); ('', '') to keep
        only its words (an edit link, a fragment)."""
        from urllib.parse import parse_qs, quote, urlsplit
        if href.startswith('/'):
            href = self.base + href
        if href.startswith(self.base + '/wiki/'):
            return href, 'wiki new' if 'new' in classes else 'wiki'
        if href.startswith(self.base):
            query = parse_qs(urlsplit(href).query)
            title = (query.get('title') or [''])[0]
            if title and query.get('redlink') == ['1']:  # a page not written yet: to where it would be
                return f"{self.base}/wiki/{quote(title.replace(' ', '_'), safe=':/()_,-.')}", 'wiki new'
            return '', ''  # the wiki's index.php otherwise: an edit link
        if href.startswith(('https://', 'http://')):
            return href, 'ext'
        return '', ''  # a fragment, a protocol we don't follow: the words stay, the link doesn't

    def _img(self, a, classes):
        if self.picture is not None or 'mw-file-element' not in classes or self.skip_at is not None:
            return
        try:
            w, h = int(a.get('data-file-width') or 0), int(a.get('data-file-height') or 0)
        except ValueError:
            return
        if w < PICTURE_MIN or not h or max(w, h) / min(w, h) > PICTURE_RATIO:
            return
        src = a.get('src') or ''
        for candidate in (a.get('srcset') or '').split(','):  # the largest the wiki made
            if candidate.strip():
                src = candidate.split()[0]
        if src.startswith('/'):
            src = self.base + src
        if not src.startswith('https://') and not src.startswith('http://'):
            return
        self.picture = {'src': src, 'width': w, 'height': h, 'alt': a.get('alt') or '', 'caption': '',
                        'page': self.base + self.file_href if (self.file_href or '').startswith('/wiki/') else '',
                        '_figure': self.figure_at}


def lead_of(html, base, post=False):
    """{'paragraphs': [html], 'more': bool, 'picture': {...} or None} from a page's parsed HTML. post: the page is
    a post and its masthead is drawn over this, so the dek and the kicker it wrote in its body are left out, and
    'cap' says which paragraph its large first letter stands on (_Lead._follows)."""
    reader = _Lead(base, post)
    reader.feed(html)
    reader.close()
    paragraphs, chars, counted, more, shown = [], 0, 0, False, {}
    for at, (kind, words, length) in enumerate(reader.blocks):
        if kind == 'h':
            if counted:
                more = True
                break
            continue
        if chars >= LEAD_CHARS or counted >= LEAD_PARAGRAPHS:
            more = True
            break
        paragraphs.append(words)
        shown[at] = len(paragraphs) - 1
        chars += length
        counted += length >= SHORT
    picture = reader.picture
    if picture:
        picture = {k: v for k, v in picture.items() if not k.startswith('_')}
    if not counted:
        paragraphs, shown = [], {}  # only a dateline: nothing to read here
    listen = reader.listen if 'play' in reader.listen else None  # a trail alone isn't the button
    lead = {'paragraphs': paragraphs, 'more': more, 'picture': picture, 'listen': listen}
    if post:
        # Which of the paragraphs shown the large first letter stands on, as on the post's own page; None if it
        # has none, or stands on one that isn't shown here.
        lead['cap'] = shown.get(reader.cap if reader.cap_done else reader.cap_wait)
    return lead


def _key(title):
    return f'tidings:wiki:{SHAPE}:' + hashlib.sha1(title.encode()).hexdigest()


def forget(title):
    """Drop a page's kept opening, so its tiding reads the edit at once (services/wiki_changes.py)."""
    from django.core.cache import cache
    title = re.sub(r'\s+', ' ', (title or '').replace('_', ' ')).strip()
    if title:
        cache.delete(_key(title))


def wiki_lead(url, http=None):
    """A PickiPedia article's opening for its page on Tidings, or None (not an article, or nothing to show).
    Kept KEPT seconds; if the wiki doesn't answer, None, kept MISS."""
    from django.core.cache import cache
    from .mood_view import pickipedia_url
    base = pickipedia_url()
    title = title_of(url, base)
    if not title:
        return None
    key = _key(title)
    found = cache.get(key)
    if found is not None:
        return found or None
    try:
        found = _fetch(title, base, http) or {}
        cache.set(key, found, KEPT)
    except Exception:  # noqa: BLE001 -- the link's page shows without it
        found = {}
        cache.set(key, found, MISS)
    return found or None


def is_post(title):
    """Whether a title is somebody's own writing, as the wiki reads it (PickiPedia's Posts/Post::isPostTitle): a
    subpage of a user's."""
    space, colon, rest = (title or '').partition(':')
    return bool(colon) and space.strip().lower() == 'user' and '/' in rest.strip().strip('/')


def post_of(card, base):
    """A post's masthead from the wiki's card of its page (PickiPedia's Posts/PageCard.php), as the page there
    draws it; None if the card isn't a post's. Words are kept as text and cut to size, an address only if it is
    the wiki's own, the date and the reading time said as the wiki says them."""
    from datetime import datetime
    post = card.get('post') if isinstance(card, dict) and card.get('kind') == 'post' else None
    if not isinstance(post, dict):
        return None

    def words(value, most):
        return re.sub(r'\s+', ' ', value).strip()[:most] if isinstance(value, str) else ''

    def own(value):
        return value if isinstance(value, str) and value.startswith(base + '/') and len(value) <= 1000 else ''

    headline = words(post.get('headline'), 300)
    if not headline:
        return None
    author = post.get('author') if isinstance(post.get('author'), dict) else {}
    published = ''
    try:
        at = datetime.fromisoformat(str(post.get('published') or '').replace('Z', '+00:00'))
        published = f'{at.day} {at:%B} {at.year}'
    except ValueError:
        pass
    minutes = post.get('minutes')
    minutes = minutes if type(minutes) is int and 0 < minutes < 10000 else 0
    return {'headline': headline, 'kicker': words(post.get('kicker'), 120), 'dek': words(post.get('dek'), 400),
            'author': {'name': words(author.get('name'), 80), 'url': own(author.get('url')),
                       'avatar': own(author.get('avatar'))},
            'published': published,
            'reads': ("a minute's read" if minutes == 1 else f"{minutes} minutes' read") if minutes else ''}


def _post(title, base, http):
    """The wiki's card of a post, as post_of reads it; None if the wiki has none to give (one older than its
    Broadside skin answers an error) or doesn't answer: the page then opens as any article does."""
    try:
        got = http.get(f'{base}/api.php', timeout=4, headers={'User-Agent': 'memory-lane (magenta; tidings)'},
                       params={'action': 'pagecard', 'titles': title, 'format': 'json', 'formatversion': 2}).json()
        cards = got.get('pagecard') if isinstance(got, dict) else None
        return post_of(cards[0], base) if isinstance(cards, list) and cards else None
    except Exception:  # noqa: BLE001 -- the article opens without its masthead
        return None


def _fetch(title, base, http=None):
    import requests
    http = http or requests
    got = http.get(f'{base}/api.php', timeout=4, headers={'User-Agent': 'memory-lane (magenta; tidings)'},
                   params={'action': 'parse', 'page': title, 'prop': 'text', 'redirects': 1, 'format': 'json',
                           'formatversion': 2, 'disableeditsection': 1, 'disablelimitreport': 1}).json()
    html = (got.get('parse') or {}).get('text')
    if not html:
        return None
    post = _post(title, base, http) if is_post(title) else None
    lead = lead_of(html, base, post=bool(post))
    if not lead['paragraphs'] and not lead['picture'] and not lead['listen'] and not post:
        return None
    if post:
        # The large first letter, on the paragraph its own page sets it on (lead_of's 'cap').
        post['cap'] = lead.pop('cap')
        lead['post'] = post
    return lead
