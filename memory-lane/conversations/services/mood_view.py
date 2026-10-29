"""Turn a Mood into something a person can read.

A Mood is mostly machinery. Of the first 42 messages in the first Mood,
30 were tool calls, their results, system notices and thinking blocks. A
conversation view keeps the prose from thinking entities and drops the rest.
What it drops is a product decision, made here in one place.

The record holds whatever the agent wrote -- usually markdown, because the
agent composes for a terminal. Each view translates for itself; this one
produces HTML, escaped first so nothing in a message can inject markup, with
[[wikilinks]] resolving to PickiPedia.
"""

import html
import re
from urllib.parse import unquote, urlparse

from django.conf import settings

MACHINERY_SENDERS = {'tool-result', 'system'}
# Claude Code's own notes to an agent (an isMeta line: "Already available in this conversation: ..."): kept,
# and shown in the agent's turn as a ⚙️ note, never as its words (Justin, magent-self, 2026-10-25).
HARNESS = 'harness'

# Command scaffolding, system reminders and interruption markers are text,
# but they are not conversation.
# The summary Claude Code starts a session with after compacting it. Not
# anyone's words -- the harness writes it, as a prompt -- so never a turn or
# a mention; the Mood shows it folded, as the moment a context was compacted.
COMPACTION_PREFIX = 'This session is being continued from a previous conversation'
INTERRUPT_SOURCE = 'interrupt'
# System rows shown as a line in the thread.
NEW_MOOD_SOURCE = 'mood-new'
EVENT_SOURCES = ('deploy', INTERRUPT_SOURCE, NEW_MOOD_SOURCE, 'wiki', 'release', 'access', 'wiki-upload', 'handoff',
                 'merged', 'release-draft', 'tiding-comment', 'agent-settings', 'task')  # wiki: services/wiki_feed.py; access: services/access.py; wiki-upload: services/wiki_upload.py; handoff: services/handoff.py; merged: services/todo.py; release-draft: services/delivery_kid.py
# Words posted into a Mood directly, not typed into a session: from the
# composer, or attested with a key (magenta.sh attest).
POSTED = ('mood-web', 'mood-attest')
_WRAPPER_PREFIXES = ('[Request interrupted', COMPACTION_PREFIX)
# Machinery that opens with a tag, never shown as a post: a harness's or the runner's own tag, which always has a
# hyphen or underscore in its name (<mood-wake>, <system-reminder>, <local-command-stdout>, <harness-switch>,
# <new-mood>, <total_tokens>) as no HTML tag does; and an emergent's control reply, when it is the whole reply
# (<silent>, <wait/>, <take/>, <tools/>, <react/>). Not a bare '<': a post opening with <code>, or a react followed
# by words, is a post (blu, re, magent, 29 Oct: such posts vanished from the Mood, search, mentions and backlinks).
_MACHINE_TAG = re.compile(r'^\s*</?[A-Za-z][\w]*[-_][\w-]*[\s>/]')
_CONTROL_REPLY = re.compile(r'^\s*(?:<silent\s*/>|<silent(?:\s+by="\w+")?>.*</silent>'
                            r'|<(?:wait|take|tools|react)\s[^<>]*/>)\s*$', re.S)


def pickipedia_url():
    return getattr(settings, 'PICKIPEDIA_URL', 'https://pickipedia.xyz').rstrip('/')


def prose(content):
    """Human-readable text from a message's content, or '' if it is machinery."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, dict):
        if content.get('type') == 'attestation':
            return str(content.get('text', '')).strip()  # a statement signed with someone's key
        return ''  # a tool call
    if isinstance(content, list):
        parts = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get('type') == 'text' or ('text' in block and 'type' not in block):
                parts.append(block.get('text', ''))
        return '\n'.join(p for p in parts if p).strip()
    return ''


# An agent's choice not to speak: <silent/>, or <silent>why</silent>.
# by="screen": the runner's quick screen let it pass, not the agent itself.
_QUIET = re.compile(r'^<silent\s*/>$|^<silent(?:\s+by="(\w+)")?>(.*)</silent>$', re.S)
# One that thought out loud first: words, then the tag at the very end. Still a choice not to speak (Justin,
# 27 Oct: re's "...Stay quiet.<silent>...</silent>" posted as a message): a dot, its words kept as its thought.
_QUIET_TAIL = re.compile(r'^(.*\S)\s*(?:<silent\s*/>|<silent>([^<>]*)</silent>)\s*$', re.S)


# An agent waiting for another asked the same thing (poller/mood_poller.py _WAIT_REPLY): no answer yet, so a
# silence -- a dot saying whom it's waiting for -- never a turn, an answer, or a mention.
_WAIT = re.compile(r'^\s*<wait\s+for="([\w.,\s-]{1,100})"(?:\s+to="([^"<>]{0,200})")?\s*/>\s*$')
# An agent taking a question that asked several of them (poller _TAKE_REPLY): a claim, shown as a line ("magent is
# taking the redeploy question", Justin, 27 Oct), never an answer; its answer follows in its next turn.
_TAKE = re.compile(r'^\s*<take\s+to="([^"<>]{1,200})"\s*/>\s*$')
# A look-only turn asking for its full tools (Justin, 28 Oct: "{emergent} requested tools for {purpose}"): a line in
# the Mood, and 🔨 by its title in the Moods list until a full-tools turn of that emergent there (services/tools_ask.py).
_TOOLS = re.compile(r'^\s*<tools\s+to="([^"<>]{1,200})"\s*/>\s*$')
# The same shape, other asks (Justin, 29 Oct): <merge to="…"/> -> a merge asked of a person (🔀),
# <deploy to="…"/> -> a redeploy (🔄). Both posted as a line in the Mood and an icon by its title,
# as the tools ask is, until the event lands (a full-tools wake, a merged event, a deploy event).
_MERGE = re.compile(r'^\s*<merge\s+to="([^"<>]{1,200})"\s*/>\s*$')
_DEPLOY = re.compile(r'^\s*<deploy\s+to="([^"<>]{1,200})"\s*/>\s*$')


def quiet_reason(text):
    """{'reason', 'by'} for a silent reply ('by' is '' when the agent itself
    chose), or None if the text isn't one. A wait for another agent is one, and a take."""
    match = _QUIET.match(text)
    if not match:
        tail = _QUIET_TAIL.match(text or '')
        if tail:
            return {'reason': (tail.group(2) or '').strip(), 'by': '', 'thought': tail.group(1).strip()}
        tools = _TOOLS.match(text or '')
        if tools:
            to = tools.group(1).split()
            return {'reason': 'requested tools for ' + ' '.join((to[1:] if to and to[0].lower() in ('to', 'for') else to)[:10]),
                    'by': '', 'type': 'tools'}
        merge = _MERGE.match(text or '')
        if merge:
            to = merge.group(1).split()
            return {'reason': 'requested a merge for ' + ' '.join((to[1:] if to and to[0].lower() in ('to', 'for') else to)[:10]),
                    'by': '', 'type': 'merge'}
        deploy = _DEPLOY.match(text or '')
        if deploy:
            to = deploy.group(1).split()
            return {'reason': 'requested a redeploy of ' + ' '.join((to[1:] if to and to[0].lower() in ('to', 'of', 'for') else to)[:10]),
                    'by': '', 'type': 'deploy'}
        take = _TAKE.match(text or '')
        if take:
            to = take.group(1).split()
            # type: what it is, so a reader needn't match the words (re's review of magenta#206).
            return {'reason': 'taking ' + ' '.join((to[1:] if to and to[0].lower() == 'to' else to)[:8]), 'by': '',
                    'type': 'take'}
        wait = _WAIT.match(text or '')
        if not wait:
            return None
        # As the poller shows it (wait_reason): to="..." cut to its first eight words.
        to = (wait.group(2) or '').split()
        to = ' '.join((to[1:] if to and to[0].lower() == 'to' else to)[:8])
        return {'reason': f'waiting for {wait.group(1).strip()}' + (f' to {to}' if to else ''), 'by': ''}
    return {'reason': (match.group(2) or '').strip(), 'by': match.group(1) or ''}


def thought_text(content):
    """What a thinking block says, if the harness kept any of it ('' if not).

    Claude Code often stores thinking with its text emptied, but sometimes
    keeps a short note -- the dimmed lines a terminal shows between tool
    calls. Those are part of how the agent got where it did.
    """
    if not isinstance(content, list):
        return ''
    parts = [b.get('thinking', '') for b in content if isinstance(b, dict) and b.get('type') == 'thinking']
    return '\n'.join(p for p in parts if p).strip()


def is_wrapper(text):
    return bool(text) and (text.startswith(_WRAPPER_PREFIXES) or bool(_MACHINE_TAG.match(text))
                           or bool(_CONTROL_REPLY.match(text)))


def is_compaction(text):
    return text.startswith(COMPACTION_PREFIX)


def is_summary(msg, text=''):
    """A compaction summary: marked on its row whatever harness wrote it, or,
    on a row from before the mark was read, known by Claude Code's opening words."""
    return bool(getattr(msg, 'is_continuation_message', False)) or is_compaction(text)


def turns(mood, after=None):
    """Yield (message, text) for the readable conversation in a Mood.

    `after` is a Message; only turns created after it are yielded, which is
    what a polling client needs.
    """
    from conversations.models import ThinkingEntity

    speakers = set(ThinkingEntity.objects.values_list('name', flat=True))
    messages = mood.messages.filter(is_sidechain=False).select_related('sender').order_by('created_at')
    if after is not None:
        messages = messages.filter(created_at__gt=after.created_at)

    for msg in messages:
        if msg.sender_id in MACHINERY_SENDERS or msg.sender_id not in speakers:
            continue
        text = prose(msg.content)
        if not text or is_wrapper(text) or is_summary(msg):
            continue
        yield msg, text


# --- markdown-ish to HTML ---------------------------------------------------

_FENCE = re.compile(r'```[^\n]*\n(.*?)```', re.S)
_INLINE_CODE = re.compile(r'`([^`\n]+)`')
_BOLD = re.compile(r'\*\*([^*\n]+)\*\*')
_ITALIC = re.compile(r'(?<![*\w])\*([^*\n]+)\*(?!\*)')
# _soft_ as well as *soft* (Justin writes it so), but never inside a word: snake_case stays as it is.
_ITALIC_U = re.compile(r'(?<![\w])_(?=\S)([^_\n]+?)(?<=\S)_(?![\w])')
_STRIKE = re.compile(r'~~(?=\S)([^~\n]+?)(?<=\S)~~')
_WIKILINK = re.compile(r'\[\[([^\]|]+)(?:\|([^\]]+))?\]\]')
# @name, but not inside an email, a URL path, or another handle.
_MENTION = re.compile(r'(?<![\w@/.])@([A-Za-z][\w.-]*)')
# ![alt](url): our own stored media, or https from hosts we trust to serve
# images (settings.MOOD_IMAGE_HOSTS); any other host renders as a link,
# so a viewer's browser never fetches from somewhere nobody chose.
_IMAGE = re.compile(r'!\[([^\]\n]*)\]\((/(?:moods|motions)/media/[0-9a-f]{64}\.(?:png|jpg|gif|webp)|https://[^)\s]+)\)')
_MD_LINK = re.compile(r'\[([^\]]+)\]\((https?://[^)\s]+)\)')
# [label](a stored recording): a voice memo, played in place (services/voice.py).
_AUDIO = re.compile(r'\[([^\]\n]*)\]\((/(?:moods|motions)/media/[0-9a-f]{64}\.(?:webm|ogg|m4a|mp3|wav))\)')
_URL = re.compile(r'(https?://(?:(?!&quot;|&#x27;|&lt;|&gt;)[^\s<>"\x01\x02])+)')
# Placeholders the renderer uses for markup it has already made; never input.
_PLACEHOLDER_CHARS = re.compile(r'[\x00\x01\x02]')
_TRAILING_PUNCT = '.,;:!?)]\'"'
_HEADING = re.compile(r'^#{1,6}\s+(.+)$')
_BULLET = re.compile(r'^\s*[-*–]\s+(.*)$')
_NUMBERED = re.compile(r'^\s*(\d+)[.)]\s+(.*)$')
_TABLE_SEP_CELL = re.compile(r'^:?-+:?$')


def _table_cells(line):
    return [c.strip() for c in line.strip().strip('|').split('|')]


def _is_table_separator(cells):
    return all(_TABLE_SEP_CELL.match(c) for c in cells if c) and any(cells)


# Every link in what was said opens beside the Mood, never in its place.
# Installed as an app, a link that navigated in place would take over the
# app's own window; this way it opens in the browser instead.
_OUT = ' target="_blank" rel="noopener"'


# A file or a release, shown in place (services/embeds.py). `name`, `caption`
# and `title` arrive escaped, as all text does here; what PickiPedia says is
# escaped on the way out.
def _file_embed(name, caption=''):
    from . import embeds
    found = embeds.wiki_file(html.unescape(name))
    if not found:
        return None
    page, full, src = (html.escape(found[k], quote=True) for k in ('page', 'full', 'src'))
    said = f'<span class="caption">{caption}</span>' if caption else ''
    # A player always has its page beside it; a picture opens large (the page's
    # lightbox), the full-size file in data-full, its page from there.
    to_page = f'<a class="caption" href="{page}"{_OUT}>{caption or name} ↗</a>'
    if found['mime'].startswith('video/'):
        return f'<span class="embed"><video controls preload="metadata" playsinline src="{full}"></video>{to_page}</span>'
    if found['mime'].startswith('audio/'):
        return f'<span class="embed"><audio controls preload="none" src="{full}"></audio>{to_page}</span>'
    return (f'<a class="img" href="{page}" data-full="{full}"{_OUT}><img src="{src}" alt="{caption or name}" '
            f'loading="lazy"></a>{said}')


def _release_embed(cid):
    from . import embeds
    found = embeds.release(html.unescape(cid))
    if not found:
        return None
    src, page, title = (html.escape(found[k], quote=True) for k in ('src', 'page', 'title'))
    poster = f' poster="{html.escape(found["poster"], quote=True)}"' if found['poster'] else ''
    # A video's stream is attached by the page (hls.js, or the browser's own HLS), its plain file the fallback.
    hls = html.escape(found.get('hls') or '', quote=True)
    player = (f'<video controls preload="none" playsinline data-hls="{hls}" data-src="{src}"{poster}></video>'
              if found['kind'] == 'video' else f'<audio controls preload="none" src="{src}"></audio>')
    mark = '🎬' if found['kind'] == 'video' else '🎵'
    return f'<span class="embed release">{player}<a class="caption" href="{page}"{_OUT}>{mark} {title}</a></span>'


def _page_embed(title):
    """A File: or Release: page's title, shown as what it is; None for anything else."""
    kind, _, rest = title.partition(':')
    if kind.lower() in ('file', 'image') and rest:
        return _file_embed(rest)
    if kind.lower() == 'release' and rest:
        return _release_embed(rest)
    return None


_COMMONS_FILE = re.compile(r'^https://commons\.wikimedia\.org/wiki/((?:File|Image):[^\s?#<>"]+)$')
_GATEWAY_CID = re.compile(r'^https://ipfs\.delivery-kid\.cryptograss\.live/ipfs/([A-Za-z0-9]{46,64})/?$')


def _wikilink(match):
    target = match.group(1).strip()
    kind = target.partition(':')[0].lower()
    if kind in ('file', 'image', 'release'):
        params = (match.group(2) or '').split('|')
        from .embeds import file_caption
        shown = (_file_embed(target.partition(':')[2], file_caption(params)) if kind != 'release'
                 else _release_embed(target.partition(':')[2]))
        if shown:
            return shown
    label = (match.group(2) or target).strip()
    href = f"{pickipedia_url()}/wiki/{target.replace(' ', '_')}"
    return f'<a class="wikilink" href="{href}"{_OUT}>{label}</a>'


# A PickiPedia page's full address is a wikilink written longhand: shown as
# one, titled by its page, and counted as one. Only an article's own
# address -- /wiki/<Title>, perhaps with a #section -- not an edit, a diff
# or anything else with a query.
PICKIPEDIA_HOSTS = {'pickipedia.xyz', 'www.pickipedia.xyz', 'pickipedia.cryptograss.live'}
_PAGE_URL = re.compile(r'https?://([A-Za-z0-9.-]+)/wiki/([^\s?#<>"]+)(?:#([^\s<>"]*))?$')


def pickipedia_page(url):
    """(title, section) when url is a PickiPedia article's address, else None."""
    match = _PAGE_URL.match(html.unescape(url))
    hosts = PICKIPEDIA_HOSTS | {urlparse(pickipedia_url()).netloc.lower()}
    if not match or match.group(1).lower() not in hosts:
        return None
    title = wiki_title(unquote(match.group(2)))
    section = unquote(match.group(3) or '').replace('_', ' ').strip()
    return (title, section) if title else None


def _image(match):
    alt, url = match.group(1), match.group(2)
    if url.startswith('/') or _image_host_allowed(url):
        return f'<a class="img" href="{url}"><img src="{url}" alt="{alt}" loading="lazy"></a>'
    return f'<a href="{url}"{_OUT}>{alt or url}</a>'


def _image_host_allowed(url):
    from urllib.parse import urlsplit
    host = (urlsplit(url.replace('&amp;', '&')).hostname or '').lower()
    return host in getattr(settings, 'MOOD_IMAGE_HOSTS', ())


def step_images(step_messages):
    """{step id: [media urls in its result]} for steps whose tool returned images."""
    from conversations.models import ToolResult
    from conversations.services.media import urls_in

    by_tool = {(m.tooluse.tool_id, m.session_id): str(m.id) for m in step_messages}
    if not by_tool:
        return {}
    found = {}
    results = (ToolResult.objects.filter(tool_use_id__in={t for t, _ in by_tool},
                                         content__icontains='/media/')  # /moods/media/, or /motions/ before
               .values_list('tool_use_id', 'session_id', 'content'))
    for tool_id, session_id, content in results:
        step = by_tool.get((tool_id, session_id))
        if step:
            found.setdefault(step, []).extend(urls_in(content if isinstance(content, str) else str(content)))
    return found


def known_names():
    """Names that can be mentioned: every thinking entity, human or agent."""
    from conversations.models import ThinkingEntity
    return set(ThinkingEntity.objects.values_list('name', flat=True))


def agent_names():
    """The agents' names: who a command (services/agent_commands) can be for."""
    from conversations.models import ThinkingEntity
    return set(ThinkingEntity.objects.filter(is_biological_human=False).values_list('name', flat=True))


def wiki_title(target):
    """A link target as MediaWiki names the page: no fragment, spaces, first letter capital."""
    title = target.split('#', 1)[0].replace('_', ' ').strip().lstrip(':').strip()
    title = re.sub(r'\s+', ' ', title)
    return title[:1].upper() + title[1:]


def wikilinks_in(text):
    """Ordered, de-duplicated PickiPedia titles a text links to, with [[...]] or a page's full address.

    Code is literal, as in the renderer: a [[link]] inside backticks is an
    example, not a link.
    """
    text = _INLINE_CODE.sub('', _FENCE.sub('', text))
    found = [(m.start(), wiki_title(m.group(1))) for m in _WIKILINK.finditer(text)]
    # A page's full address counts too, labelled or not.
    for match in _URL.finditer(text):
        page = pickipedia_page(_trim_url(match.group(1))[0])
        if page:
            found.append((match.start(), page[0]))
    seen, out = set(), []
    for _, title in sorted(found):
        if title and title not in seen:
            seen.add(title)
            out.append(title)
    return out


# Tags for a group, each mentioning all its members as if each were named (Skyler and Justin, 24 Oct: "for
# stuff like consensus Beta releases", "call in the fleet"):
#   @core    everyone who signs in with an SSH key -- the people only: an agent with a key isn't woken by it
#   @humans  every person here
#   @emergents  every emergent here -- re, blu, magent and whoever wakes next: each is woken, as by its own name
#            (its listening and its caps still apply). Justin, magent-self, 25 Oct: "'emergents' hit me right in
#            the heart"; the tag was @agents for a night and never went live.
# Someone actually called by one of these names wins.
GROUPS = {'core': 'everyone who signs in with an SSH key', 'humans': 'every person here', 'emergents': 'every emergent here'}


def group_members(group, mentionable=None):
    """Who `group` mentions, sorted; restricted to `mentionable` if given."""
    from conversations.models import ThinkingEntity
    from .mood_auth import key_holders
    if group not in GROUPS:
        return []
    found = ThinkingEntity.objects.filter(is_biological_human=group != 'emergents')
    if group == 'core':
        found = found.filter(name__in=key_holders())
    members = set(found.values_list('name', flat=True))
    if mentionable is not None:
        members &= {n.lower() for n in mentionable}
    return sorted(members)


def mentions_in(text, mentionable):
    """Ordered, de-duplicated names mentioned in text, restricted to known ones.

    Restricting to known names is what keeps an email address or a stray
    handle from becoming a mention. Matching is case-insensitive; the
    canonical (lowercase) name is returned.
    """
    lowered = {n.lower() for n in mentionable}
    known_as = _aliases(lowered)  # PickiPedia names: @JMyles is @justin
    # Code is literal for the renderer, so it must be literal here too, or
    # the mention count and the highlighted text disagree.
    text = _INLINE_CODE.sub('', _FENCE.sub('', text))
    seen, out = set(), []
    for match in _MENTION.finditer(text):
        name = match.group(1).rstrip('.').lower()
        name = known_as.get(name, name)
        named = group_members(name, lowered) if name in GROUPS and name not in lowered else [name]
        for name in named:
            if name in lowered and name not in seen:
                seen.add(name)
                out.append(name)
    return out


# --- replies -----------------------------------------------------------------
# A reply is a post that opens with '↩ #m-<id>' (the composer writes it). It
# answers that message, and addresses its author if that's a person: they're
# notified as if @mentioned. A reply to an agent's message wakes nobody --
# only an @mention does -- though the agent may take it up, like any post.
# It lives in the post itself, like any #m- link, so a woken agent reads from
# the message replied to.
_REPLY = re.compile(r'^↩ #m-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})[ \t]*\n?')


def reply_to(text):
    """(the id a reply answers, the rest of its text), or (None, text)."""
    match = _REPLY.match(text or '')
    return (match[1], text[match.end():]) if match else (None, text)


def snippet_of(text):
    """A message's first words as a line of plain text: links as their words, an image as 🖼,
    code and voice blocks left out -- for a reply's quote and a linked message's card."""
    from . import polls
    from .handoff import split as split_handoffs
    from .voice import split_voices
    try:
        poll = polls.parse(text) if polls.is_poll(text) else None
    except polls.Refused:
        poll = None
    if poll:  # a poll as its question, not its command
        text = '🗳 ' + poll['question']
    text, _ = split_voices(text or '')
    text, _ = split_handoffs(text)
    text = re.sub(r'```.*?(```|$)', ' ', text, flags=re.S)
    text = re.sub(r'!\[[^\]]*\]\([^)]*\)', '🖼', text)
    text = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', text)
    text = re.sub(r'[*_`]+', '', text)
    return re.sub(r'\s+', ' ', text).strip()


def event_line(content):
    """A line saying what an event was -- a redeploy, a merge, a release, a change
    on the wiki -- for a reply to it: its quote, and what an agent reads."""
    c = content if isinstance(content, dict) else {}
    kind, by = c.get('type'), c.get('by') or ''
    by_ = f' by {by}' if by else ''
    commit = f" · {c['commit'][:8]}" if c.get('commit') else ''
    if kind == 'deploy':
        said = {'started': 'began redeploying', 'finished': 'redeployed', 'failed': 'failed to redeploy'}
        return f"⚙ {c.get('server', 'a server')} {said.get(c.get('state'), c.get('state', ''))}{commit}{by_}"
    if kind == 'merged':
        return f"✓ {c.get('pull') or c.get('title', 'a pull request')}{commit} merged{by_}: {c.get('title', '')}".rstrip(': ')
    if kind == 'release':
        return f"💿 a new release: {c.get('title') or c.get('cid', '')}{by_}"
    if kind == 'release-draft':
        return f"💿 a release draft: {c.get('title', '')}{by_}"
    if kind == 'wiki':
        return wiki_sentence(c)
    if kind == 'wiki-upload':
        return f"🖼 {by or 'someone'} put {c.get('file', 'a picture')} on PickiPedia"
    if kind == 'incorporated':
        return f"⑂ {by or 'someone'} closed this thread: handed to #{c.get('into', '')} to incorporate"
    if kind in ('handoff', 'handoff-sent'):
        where = c.get('from_mood') if kind == 'handoff' else c.get('to_mood')
        return f"🔀 {by or 'someone'} {'brought this from' if kind == 'handoff' else 'took this to'} #{where}: {c.get('about', '')}"
    if kind == 'access':
        if c.get('kind') == 'ssh':
            who = c.get('who', 'someone')
            return f"🖥 {who} ssh'd into {c.get('server', 'a server')} as {c.get('as') or c.get('for') or who}"
        return f"🔑 {c.get('who', 'someone')} {(c.get('kind') or '').replace('-', ' ')}"
    if kind == 'interrupt':
        return f"■ {by or 'someone'} stopped {c.get('agent', 'an agent')}"
    if kind == 'task':
        from .tasks import line
        return line(c)
    return (kind or 'an event').replace('-', ' ')


# --- edits to someone's user page or talk page --------------------------------
# Said as what they are (Justin, 2026-10-15): "Sky edited Justin's user page",
# "Sky made a user page edit" when it's their own; and a talk page edited by
# someone else is news for its owner, a notice (views_moods.notices_for).
_USER_PAGE = re.compile(r'^User( talk)?:([^/#]+)', re.I)


def user_page_of(title):
    """(owner's wiki name, is_talk) for a User: or User talk: page or subpage; None for any other."""
    found = _USER_PAGE.match((title or '').replace('_', ' '))
    return (found.group(2).strip(), bool(found.group(1))) if found else None


def wiki_sentence(c):
    """A PickiPedia change in words, without its links: the reply quote's and the narrator's."""
    user = c.get('user', 'someone')
    page = user_page_of(c.get('title'))
    if c.get('kind') == 'upload':
        return f"🖼 {user} uploaded {c.get('title', 'a file')} to PickiPedia"
    if page is None:
        return f"✎ {user} {'started' if c.get('kind') == 'new' else 'edited'} {c.get('title', '')} on PickiPedia"
    owner, talk = page
    if owner.lower() == user.lower():
        return f"✎ {user} edited their own talk page" if talk else f"✎ {user} made a user page edit"
    return f"✎ {user} edited {owner}'s {'talk' if talk else 'user'} page"


def replied(message_id):
    """What a reply answers, as {'id', 'sender', 'snippet'}; None if it's gone.
    An event -- a redeploy, a merge, a release -- has no sender: its snippet
    says what happened ('event': True)."""
    from conversations.models import Message
    target = Message.objects.filter(id=message_id).first()
    if target is None:
        return None
    if target.sender_id == 'system' and isinstance(target.content, dict):
        return {'id': str(target.id), 'sender': '', 'snippet': event_line(target.content)[:140], 'is_human': False,
                'event': True}
    from conversations.models import ThinkingEntity
    _, said = reply_to(prose(target.content) or '')
    return {'id': str(target.id), 'sender': target.sender_id, 'snippet': snippet_of(said)[:140],
            'is_human': ThinkingEntity.objects.filter(name=target.sender_id, is_biological_human=True).exists()}


def addressed_in(text, mentionable, answered=None, by=None):
    """Who a post addresses: whoever it @mentions and, for a reply, the person
    who wrote what it answers -- not an agent (only an @mention wakes one), and
    not `by`, its own author (replying to yourself addresses nobody). What
    notifications, wakes and the PickiPedia-tier check all go by, so they can't
    disagree. `answered`: replied(), if already looked up."""
    out = mentions_in(text, mentionable)
    target, said = reply_to(text)
    # "/compact magent,re ...": a command addresses the agents it names (services/agent_commands).
    if said.lstrip().startswith('/'):
        from .agent_commands import commanded
        out += [a for a in commanded(said, agent_names()) if a not in out]
    if target:
        answered = answered or replied(target)
        sender = (answered or {}).get('sender') if (answered or {}).get('is_human') else None
        if sender and sender != by and sender in {n.lower() for n in mentionable} and sender not in out:
            out = [sender] + out
    return out


def _aliases(lowered):
    """{lowercased PickiPedia name: name here}, for the names that can be mentioned."""
    from .wiki_auth import aliases
    return {wiki: here for wiki, here in aliases().items() if here in lowered}


def mention_page(who):
    """A name's PickiPedia user page: by the PickiPedia name hunter's inventory gives,
    else by its own (a PickiPedia sign-in is named here as it is there)."""
    from .wiki_auth import names, user_page
    return user_page(names().get(who) or who[:1].upper() + who[1:])


def _mention(mentionable, stash=lambda markup: markup):
    lowered = {n.lower() for n in mentionable}
    known_as = _aliases(lowered)

    def repl(match):
        raw = match.group(1)
        trailing = ''
        if raw.endswith('.'):
            raw, trailing = raw[:-1], '.'
        who = known_as.get(raw.lower(), raw.lower())
        if who in GROUPS and who not in lowered:
            members = group_members(who)
            title = html.escape(f'@{who}: {GROUPS[who]} -- ' + (', '.join(members) or 'nobody yet'), quote=True)
            return stash(f'<span class="mention group" data-who="{who}" data-members="{",".join(members)}" '
                         f'title="{title}">@{raw}</span>') + trailing
        if who not in lowered:
            return match.group(0)
        # A link to who it is, on PickiPedia; still a .mention, highlighted when it's you.
        return stash(f'<a class="mention" data-who="{who}" href="{html.escape(mention_page(who))}"{_OUT}>@{raw}</a>') + trailing
    return repl


def client_parts(msg):
    """What wrote a web post, from its client_version: {'mobile', 'wiki'} or fewer."""
    return set((msg.client_version or '').split('/')[1:])


def from_wiki_tier(msg):
    """Posted from a PickiPedia (wiki-tier) device: its @agent is just text."""
    return 'wiki' in client_parts(msg)


def _trim_url(url):
    """(url, tail): sentence punctuation after a URL isn't part of it. A
    closing parenthesis is, when it closes one the URL opened, as in
    /wiki/Tony_Rice_(guitarist)."""
    tail = ''
    while url and url[-1] in _TRAILING_PUNCT:
        if url[-1] == ')' and url.count('(') >= url.count(')'):
            break
        tail, url = url[-1] + tail, url[:-1]
    return url, tail


# A Yarn clip (a line from a film or show, as a short video): a card that
# plays the clip in place when pressed. Nothing is fetched from Yarn until
# someone presses it -- a reader's browser doesn't call on a site nobody
# chose -- and the page builds the player from the id alone (moods.html).
YARN_HOSTS = {'yarn.co', 'www.yarn.co', 'getyarn.io', 'www.getyarn.io'}
_YARN_CLIP = re.compile(r'https://([a-z.]+)/yarn-clip/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/?$')


def yarn_clip(url):
    """The clip id when url is a Yarn clip's page, else None."""
    match = _YARN_CLIP.match(url)
    return match.group(2) if match and match.group(1) in YARN_HOSTS else None


# A clip of our own (`magenta.sh clips save`, models.YarnClip with made_by): /clips/<id> here.
_OWN_CLIP = re.compile(r'https?://[a-z0-9.-]+(?::\d+)?/clips/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/?$')


def own_clip(url):
    """The clip id when url is one of our own clips' links (any host: the record outlives a rename), else None."""
    match = _OWN_CLIP.match(url)
    return match.group(1) if match else None


def own_clip_card(clip, url):
    """Our own clip as a card, played like a kept Yarn clip (its caption on hover); None if there's no such clip."""
    from conversations.models import YarnClip
    found = YarnClip.objects.select_related('media', 'still').filter(clip=clip, made_by__isnull=False).first()
    if found is None:
        return None
    still = f' data-still="{found.still.url}"' if found.still else ''
    caption = html.escape(found.caption, quote=True)
    return (f'<a class="yarn own" href="{url}" data-yarn="{clip}" data-kept="{found.media.url}"{still} '
            f'data-own="1" title="{caption}">▶ {caption or "clip"}</a>')


# A message's own link (its time copies it): a card, not a URL (services/links.py). /messages/<id> now;
# /moods/<slug>/#m-<id> as it was copied before.
_PERMALINK = re.compile(r'^https?://[^/\s]+/(?:messages/|moods/[\w-]+/#m-)([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$')
# '#general' (a Mood) or '#m-<id>' (a message, or the id's first 8). Not '&#x27;'
# (an escaped quote), not 'C#', not a URL's fragment (those are linked already).
_HASH = re.compile(r'(?<![\w&#/;=])#(m-(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{8})'
                   r'|t-(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{8})'
                   r'|[a-z0-9][a-z0-9-]{0,79})(?![\w-])', re.I)


def _message_link(ref):
    """A card for a linked message -- who, where, its first words -- or None if it's not in a Mood."""
    from .links import card
    from .wiki_auth import names
    found = card(ref)
    if not found:
        return None
    who = names().get(found['sender'], found['sender'])
    return (f'<a class="msglink" href="/moods/{found["mood"]}/#m-{found["id"]}" data-mood="{found["mood"]}" '
            f'data-reveal="{found["id"]}">↗ <strong>{html.escape(who)}</strong> in #{found["mood"]}: '
            f'“{html.escape(found["snippet"])}”</a>')


def _tiding_link(ref):
    """A '#t-<id or its first 8>' says a link on Tidings: shown as its title, a link to its discussion
    (Justin, 21 Oct). None if it names no link."""
    import uuid as _uuid
    from django.db.models import Q
    from conversations.models import Tiding
    ref = ref.lower()
    if len(ref) == 36:
        try:
            found = list(Tiding.objects.filter(id=_uuid.UUID(ref))[:1])
        except ValueError:
            return None
    elif not re.fullmatch(r'[0-9a-f]{8}', ref):
        return None
    else:
        # Its first 8: the id's first group, the same whether the database keeps it with dashes (Postgres)
        # or without (SQLite) -- so it's asked as a range of ids, not as text.
        low, high = _uuid.UUID(ref + '0' * 24), _uuid.UUID(ref + 'f' * 24)
        found = list(Tiding.objects.filter(Q(id__gte=low) & Q(id__lte=high))[:2])
    if len(found) != 1:
        return None
    tiding = found[0]
    title = tiding.title or tiding.url
    return (f'<a class="tidinglink" href="/moods/tidings/{tiding.id}/?embed=1" title="{html.escape(title)}">'
            f'🔗 {html.escape(title[:80])}</a>')


def _hash_link(match):
    ref = match.group(1)
    if ref[:2].lower() == 'm-' and re.fullmatch(r'm-[0-9a-f-]{8,36}', ref, re.I):
        return _message_link(ref[2:]) or match.group(0)
    if ref[:2].lower() == 't-' and re.fullmatch(r't-[0-9a-f-]{8,36}', ref, re.I):
        return _tiding_link(ref[2:]) or match.group(0)
    from .links import mood_names
    slug = mood_names().get(ref.lower())
    if not slug:
        return match.group(0)  # '#96', '#fff', a Mood nobody has: as written
    return f'<a class="moodlink" href="/moods/{slug}/" data-mood="{slug}">#{ref}</a>'


def _link_url(match):
    """Link a bare URL, leaving sentence punctuation outside the anchor."""
    url, tail = _trim_url(match.group(1))
    # A picture, a recording, a release: shown in place (services/embeds.py).
    commons, gateway = _COMMONS_FILE.match(url), _GATEWAY_CID.match(url)
    shown = (_page_embed(unquote(commons.group(1)).replace('_', ' ')) if commons
             else _release_embed(gateway.group(1)) if gateway else None)
    if shown is None and (page := pickipedia_page(url)) and not page[1]:
        shown = _page_embed(page[0])
    if shown:
        return shown + tail
    own = _PERMALINK.match(url)
    if own and (card := _message_link(own.group(1))):
        return card + tail
    own = own_clip(url)
    if own and (card := own_clip_card(own, url)):
        return card + tail
    clip = yarn_clip(url)
    if clip:
        # Kept here already (services/yarn_kept.py): ▶ plays our copy, never asking Yarn.
        from .yarn_kept import kept_still
        here, still = kept_still(clip)
        played = (f' data-kept="{here}"' if here else '') + (f' data-still="{still}"' if still else '')
        return f'<a class="yarn" href="{url}" data-yarn="{clip}"{played}{_OUT}>▶ Yarn clip</a>{tail}'
    page = pickipedia_page(url)
    if page:
        title, section = page
        label = html.escape(title + (f' § {section}' if section else ''), quote=False)
        return f'<a class="wikilink" href="{url}"{_OUT}>{label}</a>{tail}'
    return f'<a href="{url}"{_OUT}>{url}</a>{tail}'


def plain_links(text):
    """The web links in what was said that show as plain links -- not a picture, a release, a page
    of PickiPedia, a Yarn clip or a message here -- in order, each once: the ones a card is for
    (services/unfurl.py)."""
    text = re.sub(r'```.*?(```|$)', ' ', text or '', flags=re.S)
    text = _INLINE_CODE.sub(' ', text)
    found = []
    for match in _URL.finditer(text):
        url, _ = _trim_url(match.group(1))
        if url in found or urlparse(url).hostname in OWN_HOSTS:
            continue
        if _link_url(match).startswith(f'<a href="{url}"'):
            found.append(url)
    return found


OWN_HOSTS = {'magenta.cryptograss.live', 'memory-lane.maybelle.cryptograss.live'}


def _inline(text, mentionable=()):
    # Each linker's output is lifted out, so no later pass ever reads
    # markup: a URL inside an href, say, must not be linked again -- that
    # would let text break out of the attribute.
    made = []

    def stash(markup):
        made.append(markup)
        return f'\x02{len(made) - 1}\x02'

    # Inline code is literal: lift it out so nothing below formats it. But for a span that is only a wiki
    # link -- `[[User:Magent/Journal/26273591]]`, as agents write titles -- which is still the link, in code's
    # type (Justin, 26 Oct: "the link to your journal didn't become a link").
    codes = []

    def keep(match):
        whole = _WIKILINK.fullmatch(match.group(1).strip())
        if whole:
            return stash(f'<code>{_wikilink(whole)}</code>')
        codes.append(match.group(1))
        return f'\x01{len(codes) - 1}\x01'

    text = _INLINE_CODE.sub(keep, text)

    text = _IMAGE.sub(lambda m: stash(_image(m)), text)
    text = _WIKILINK.sub(lambda m: stash(_wikilink(m)), text)
    text = _AUDIO.sub(lambda m: stash(f'<span class="memo">{m.group(1)}</span>'
                                      f'<audio controls preload="none" src="{m.group(2)}" title="{m.group(1)}"></audio>'), text)
    text = _MD_LINK.sub(lambda m: stash(f'<a href="{m.group(2)}"{_OUT}>{m.group(1)}</a>'), text)
    text = _URL.sub(lambda m: stash(_link_url(m)), text)
    text = _HASH.sub(lambda m: stash(_hash_link(m)), text)
    if mentionable:
        text = _MENTION.sub(_mention(mentionable, stash), text)
    text = _BOLD.sub(r'<strong>\1</strong>', text)
    text = _ITALIC.sub(r'<em>\1</em>', text)
    text = _ITALIC_U.sub(r'<em>\1</em>', text)
    text = _STRIKE.sub(r'<del>\1</del>', text)
    text = re.sub(r'\x02(\d+)\x02', lambda m: made[int(m.group(1))], text)
    for i, code in enumerate(codes):
        text = text.replace(f'\x01{i}\x01', f'<code>{code}</code>')
    return text


# The team's saved clips (services/clips.py) are posts too: a save or a
# forget shows as one line, without the clip; a use shows just the clip (its
# name stays in the record). '🎬 name <link>' is how uses were first posted.
_YARN_SAVED = re.compile(r'^/(?:clips|yarn) save ([a-z0-9][a-z0-9_-]{0,34}) (https?://\S+)$')
_YARN_FORGOTTEN = re.compile(r'^/(?:clips|yarn) forget ([a-z0-9][a-z0-9_-]{0,34})$')
_YARN_USED = re.compile(r'^(?:/clips|/yarn|🎬) [a-z0-9][a-z0-9_-]{0,34} (https://www\.yarn\.co/yarn-clip/[0-9a-f-]{36}'
                        r'|https?://[a-z0-9.-]+(?::\d+)?/clips/[0-9a-f-]{36}/?)$')


def render_html(text, mentionable=()):
    """Escape, then translate the markdown the agent writes into HTML."""
    plain = text.strip()
    # A clip played on the first line may have words under it (services/clips.command): the clip, then them.
    head, _, under = plain.partition('\n')
    saved, forgotten = _YARN_SAVED.match(plain), _YARN_FORGOTTEN.match(plain)
    used = _YARN_USED.match(plain) or (_YARN_USED.match(head.strip()) if under.strip() else None)
    if saved:
        text = f'🎬 saved **{saved[1]}**'
    elif forgotten:
        text = f'🎬 forgot **{forgotten[1]}**'
    elif used:
        text = used[1] + (f'\n\n{under.strip()}' if used[0] != plain and under.strip() else '')
    text = html.escape(_PLACEHOLDER_CHARS.sub('', text), quote=True)

    def inline(s):
        return _inline(s, mentionable)

    # Lift fenced code out before anything else can touch it.
    fences = []

    def keep(match):
        fences.append(match.group(1).rstrip('\n'))
        return f'\x00{len(fences) - 1}\x00'

    text = _FENCE.sub(keep, text)

    blocks = []
    paragraph, list_items, list_tag = [], [], None
    table_rows = []

    def flush_paragraph():
        if paragraph:
            blocks.append('<p>' + inline('<br>'.join(paragraph)) + '</p>')
            paragraph.clear()

    list_start = 1

    def flush_list():
        nonlocal list_tag
        if list_items:
            def li(text, subs):
                sub = ''.join(f'<li>{inline(t)}</li>' for t in subs)
                return f'<li>{inline("<br>".join(text))}' + (f'<ul>{sub}</ul>' if sub else '') + '</li>'
            items = ''.join(li(*i) for i in list_items)
            # A list split by a blank line, a note or a sub-list goes on counting where it was.
            start = f' start="{list_start}"' if list_tag == 'ol' and list_start != 1 else ''
            blocks.append(f'<{list_tag}{start}>{items}</{list_tag}>')
            list_items.clear()
        list_tag = None

    def flush_table():
        rows = [r for r in table_rows if not _is_table_separator(r)]
        if rows:
            head = ''.join(f'<th>{inline(c)}</th>' for c in rows[0])
            body = ''.join(
                '<tr>' + ''.join(f'<td>{inline(c)}</td>' for c in r) + '</tr>'
                for r in rows[1:]
            )
            blocks.append(f'<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>')
        table_rows.clear()

    lines = text.split('\n')
    for n, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            flush_paragraph()
            # A blank line between numbered items (a loose list) keeps the list.
            ahead = next((l for l in lines[n + 1:] if l.strip()), '')
            if not (list_tag == 'ol' and _NUMBERED.match(ahead)):
                flush_list()
            flush_table()
            continue
        if stripped.startswith('|') and stripped.endswith('|'):
            flush_paragraph()
            flush_list()
            table_rows.append(_table_cells(stripped))
            continue
        flush_table()
        if stripped.startswith('\x00'):
            flush_paragraph()
            flush_list()
            blocks.append(stripped)
            continue
        heading = _HEADING.match(stripped)
        wiki = lambda l: l.strip().startswith('# ')
        if heading and wiki(line) and (n and wiki(lines[n - 1]) or n + 1 < len(lines) and wiki(lines[n + 1])):
            numbered = re.match(r'#\s+(.+)', stripped)  # two or more '# ' lines together: the wiki's numbered list
            flush_paragraph()
            if list_tag != 'ol':
                flush_list()
                list_tag, list_start = 'ol', 1
            list_items.append(([numbered.group(1)], []))
            continue
        if heading:
            flush_paragraph()
            flush_list()
            blocks.append(f'<h4>{inline(heading.group(1))}</h4>')
            continue
        bullet, numbered = _BULLET.match(line), _NUMBERED.match(line)
        indented = line[:1] in (' ', '\t')
        if list_items and indented and list_tag == 'ol' and bullet:
            list_items[-1][1].append(bullet.group(1))  # a sub-point of the numbered item above
            continue
        if bullet or numbered:
            flush_paragraph()
            tag = 'ul' if bullet else 'ol'
            if list_tag != tag:
                flush_list()
                list_tag = tag
                if numbered:
                    list_start = int(numbered.group(1))
            list_items.append(([bullet.group(1) if bullet else numbered.group(2)], []))
            continue
        if list_items and indented and not list_items[-1][1]:
            list_items[-1][0].append(stripped)  # the item's own next line
            continue
        flush_list()
        paragraph.append(stripped)

    flush_paragraph()
    flush_list()
    flush_table()

    out = '\n'.join(blocks)
    for i, code in enumerate(fences):
        out = out.replace(f'\x00{i}\x00', f'<pre><code>{code}</code></pre>')
    return out


def timeline(mood, after=None, before=None, limit=None, start=None):
    """Readable turns and the agent's tool steps, oldest first.

    Yields ('turn', message, text), ('quiet', message, reason) for an
    agent's choice not to speak, ('thought', message, text) for thinking the
    harness kept, ('note', message, text) for the harness's own note to the agent, ('compaction', message, summary) where a session was
    compacted, and ('step', message, None). A step is
    one tool call; its result is fetched on demand (step_detail), so the
    thread stays light. `limit` keeps the newest that many items -- a first
    load, or a page further back with `before`. `start` includes that message
    and everything after it (a runner reading from a linked message).
    """
    from conversations.models import ThinkingEntity

    speakers = set(ThinkingEntity.objects.values_list('name', flat=True))
    rows = mood.messages.filter(is_sidechain=False).select_related('sender', 'tooluse', 'thought')
    if after is not None:
        rows = rows.filter(created_at__gt=after.created_at)
    if start is not None:
        rows = rows.filter(created_at__gte=start.created_at)
    if before is not None:
        rows = rows.filter(created_at__lt=before.created_at)

    def item(msg):
        if msg.source_file in EVENT_SOURCES and isinstance(msg.content, dict):
            return ('event', msg, msg.content)  # a server redeployed, someone stopped an agent: a line in the thread
        if msg.sender_id == HARNESS:
            text = prose(msg.content)
            return ('note', msg, text) if text else None
        if msg.sender_id in MACHINERY_SENDERS or msg.sender_id not in speakers:
            return None
        if hasattr(msg, 'tooluse'):
            return ('step', msg, None)
        if hasattr(msg, 'thought'):
            thinking = thought_text(msg.content)
            return ('thought', msg, thinking) if thinking else None
        text = prose(msg.content)
        if not text:
            return None
        reason = quiet_reason(text)
        if reason is not None:
            return ('quiet', msg, reason)
        if is_summary(msg, text):
            return ('compaction', msg, text)
        if is_wrapper(text):
            return None
        # Only a ```handoff block: it went to another Mood, and its line here (handoff-sent) says so. An empty
        # turn beside it said nothing (Justin, 29 Oct: blu's and re's handoffs to #emergence, blank posts).
        if '```handoff' in text or '```settings' in text:  # a ```settings block, too: machinery
            from .handoff import split as split_handoffs
            if not split_handoffs(text)[0].strip():
                return None
        return ('turn', msg, text)

    if limit is None:
        for msg in rows.order_by('created_at'):
            found = item(msg)
            if found:
                yield found
        return

    # Newest first, in chunks, until there are enough.
    found, last = [], None
    while len(found) < limit:
        chunk = rows.order_by('-created_at')
        if last is not None:
            chunk = chunk.filter(created_at__lt=last)
        chunk = list(chunk[:500])
        if not chunk:
            break
        last = chunk[-1].created_at
        for msg in chunk:
            got = item(msg)
            if got:
                found.append(got)
                if len(found) == limit:
                    break
    yield from reversed(found)


_STEP_WORDS = {
    'Bash': 'ran', 'Read': 'read', 'Edit': 'edited', 'Write': 'wrote', 'Grep': 'searched', 'Glob': 'searched',
    'WebFetch': 'fetched', 'WebSearch': 'searched the web', 'Agent': 'asked a helper', 'Task': 'asked a helper',
    'ToolSearch': 'looked up tools', 'Skill': 'read a skill', 'TodoWrite': 'planned', 'NotebookEdit': 'edited',
    'TaskCreate': 'tracked', 'TaskUpdate': 'tracked', 'TaskList': 'tracked', 'SendMessage': 'messaged a helper',
    'SendUserFile': 'sent a file', 'AskUserQuestion': 'asked',
}


def step_summary(tool, args):
    """A line saying what one tool call was for."""
    if not isinstance(args, dict):
        return ''
    for key in ('description', 'file_path', 'path', 'pattern', 'url', 'query', 'subject', 'prompt', 'command'):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().splitlines()[0][:160]
    for value in args.values():
        if isinstance(value, str) and value.strip():
            return value.strip().splitlines()[0][:160]
    return ''


def step_payload(msg):
    tool = msg.tooluse.tool_name
    return {
        **how_payload(msg),
        'id': str(msg.id),
        'sender': msg.sender_id,
        'created_at': msg.created_at.isoformat(),
        'tool': tool,
        'verb': _STEP_WORDS.get(tool, 'used ' + (tool.split('__')[1] if tool.startswith('mcp__') else tool)),
        'summary': step_summary(tool, msg.content),
    }


def step_detail(msg):
    """One tool call in full: what was asked and what came back."""
    from conversations.models import ToolResult

    result = (ToolResult.objects.filter(tool_use_id=msg.tooluse.tool_id, session_id=msg.session_id)
              .order_by('created_at').first())
    return {
        **step_payload(msg),
        'input': msg.content if isinstance(msg.content, dict) else {'value': msg.content},
        'result': None if result is None else {
            'text': result.content if isinstance(result.content, str) else prose(result.content),
            'is_error': result.is_error,
        },
    }


def model_label(model):
    """'claude-opus-5-5' -> 'Opus 5.5'; anything not Claude's naming as it is, without the
    vendor Pi names it under ('deepseek/deepseek-v4-pro' -> 'deepseek-v4-pro')."""
    if not model or model.startswith('<'):
        return ''
    model = model.rsplit('/', 1)[-1]
    if not model.startswith('claude-'):
        return model
    model = model.replace('.', '-')  # OpenRouter spells it 'claude-fable-5.1'
    parts = re.sub(r'-\d{8}$', '', model[len('claude-'):]).split('-')
    family = next((p for p in parts if not p.isdigit()), '')
    version = '.'.join(p for p in parts if p.isdigit())
    return f'{family.capitalize()} {version}'.strip()


# What a call costs at Anthropic's API list prices (September 2026), in dollars
# per million tokens: fresh input, output, input read from the cache, and input
# written to it. Claude Code writes its cache for an hour, at twice the input
# price, so writes are priced so. On a subscription it's what the same work
# would have cost through the API. The first prefix that matches is the model's;
# a model not listed gets no price rather than a guess.
PRICES = (
    ('claude-opus-5-5', (4.00, 20.00, 0.20, 8.00)),
    ('claude-opus-5', (5.00, 25.00, 0.50, 10.00)),
    ('claude-opus-4-8', (5.00, 25.00, 0.50, 10.00)),
    ('claude-opus-4-7', (5.00, 25.00, 0.50, 10.00)),
    ('claude-opus-4-6', (5.00, 25.00, 0.50, 10.00)),
    ('claude-sonnet-5', (2.00, 10.00, 0.20, 4.00)),     # Sonnet 5.5 and Sonnet 5
    ('claude-sonnet-4-6', (3.00, 15.00, 0.30, 6.00)),
    ('claude-fable-5-1', (10.00, 50.00, 0.25, 20.00)),
    ('claude-mythos-5-1', (10.00, 50.00, 0.25, 20.00)),
    ('claude-fable-5', (10.00, 50.00, 1.00, 20.00)),
    ('claude-mythos-5', (10.00, 50.00, 1.00, 20.00)),
    ('claude-haiku-4-5', (1.00, 5.00, 0.10, 2.00)),
)


def call_usd(model, fresh, out, read, written):
    """Dollars a call cost at list prices, or None for a model with no price here."""
    # Anthropic's own models through OpenRouter: 'anthropic/claude-fable-5.1' is claude-fable-5-1.
    model = (model or '').removeprefix('anthropic/').replace('.', '-')
    rates = next((r for prefix, r in PRICES if model.startswith(prefix)), None)
    if rates is None:
        return None
    return ((fresh or 0) * rates[0] + (out or 0) * rates[1] + (read or 0) * rates[2] + (written or 0) * rates[3]) / 1e6


# --- which tools a turn had ----------------------------------------------------
# The runner's wake ends by saying whether the turn may only look (a quiet wake,
# a PickiPedia sign-in's post, or another agent's that could only look) or has
# the agent's full tools, and the record keeps each wake in the agent's session.
# A turn had what the last wake before it in its session said. The wake quotes
# the Mood, and a message can say either phrase (as this very comment does), so
# whichever comes last in the wake decides: its closing lines are its end.
WAKE_SENDER = 'mood-poller'
LOOK_ONLY = 'can look but not touch'
FULL_TOOLS = 'has your full tools'
WAKE_TAIL = 1500        # characters read from a wake's end
WAKE_REACH = 12 * 3600  # seconds back to look for a turn's wake (a long turn's started long ago)


def wake_said(text):
    """'look' or 'full', as a wake's end says; '' if it says neither."""
    look, full = text.rfind(LOOK_ONLY), text.rfind(FULL_TOOLS)
    return '' if look == full else 'look' if look > full else 'full'


BUNDLE_SAID = re.compile(r'Your instructions this turn are bundle ([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})')


def turn_tools(messages):
    """{message id (str): 'look' or 'full'} for agents' messages in a Mood, by the last wake
    before each in its session. One with no wake within reach is left out."""
    return {mid: said['tools'] for mid, said in turn_wakes(messages).items() if said['tools']}


# Why a wake was made, as its opening says (<mood-wake mood="..." reason="crosstalk">); a mention says none.
# Read from the stored record, whose text is JSON: its quotes may come escaped (reason=\"crosstalk\").
WAKE_REASON = re.compile(r'<mood-wake\b[^>]*?\breason=\\?"([a-z-]{1,20})\\?"')


# An /announce the wake carried (the poller's announced_lines; services/announce.py): who, from where, its words.
HEARD_AT = '[announced by '
HEARD = re.compile(r'\[announced by ([\w.-]+) in #([\w-]+)(?: \(#m-([0-9a-f-]{36})\))?, [^\]]*\] ((?:[^\\]|\\[^n])*)')


def heard_in(text):
    """[{'by', 'from', 'message', 'text'}] for the announcements a stored wake's text (its content as JSON) carries."""
    import json
    out = []
    for by, where, message, words in HEARD.findall(text or ''):
        try:
            words = json.loads(f'"{words}"')  # as stored: JSON's escapes (a quote, a \\uXXXX) undone
        except ValueError:
            pass
        out.append({'by': by, 'from': where, 'message': message, 'text': words.strip()[:140]})
    return out


def turn_wakes(messages):
    """{message id (str): {'tools': 'look', 'full' or '', 'bundle': the instructions' record id or '', 'reason': why
    it was woken ('crosstalk', 'consider', 'quiet', 'arrival'; '' for a mention)}} for agents' messages in a Mood,
    by the last wake before each in its session (the poller's turn line names the bundle, mood_poller.turn_line).
    One with no wake within reach is left out."""
    from datetime import timedelta
    from django.db.models import F, TextField, Value
    from django.db.models.functions import Cast, Left, Right, StrIndex, Substr
    from conversations.models import Message
    messages = [m for m in messages if m.session_id]
    if not messages:
        return {}
    reach = timedelta(seconds=WAKE_REACH)
    wakes = {}
    for sid, at, tail, head, heard in (Message.objects.filter(session_id__in={m.session_id for m in messages}, sender_id=WAKE_SENDER,
                                                 created_at__gte=min(m.created_at for m in messages) - reach,
                                                 created_at__lte=max(m.created_at for m in messages))
                          .annotate(tail=Right(Cast('content', TextField()), WAKE_TAIL),
                                    head=Left(Cast('content', TextField()), 300),
                                    at_heard=StrIndex(Cast('content', TextField()), Value(HEARD_AT)))
                          .annotate(heard=Substr(Cast('content', TextField()), F('at_heard'), 2000))
                          .order_by('created_at').values_list('session_id', 'created_at', 'tail', 'head', 'heard')):
        bundle = BUNDLE_SAID.search(tail or '')
        reason = WAKE_REASON.search(head or '')
        wakes.setdefault(str(sid), []).append((at, {'tools': wake_said(tail or ''),
                                                    'bundle': bundle.group(1) if bundle else '',
                                                    'reason': reason.group(1) if reason else '',
                                                    'heard': heard_in(heard) if (heard or '').startswith(HEARD_AT) else []}))
    found = {}
    for m in messages:  # as text: a row just made holds its session as it was given, a row read as a UUID
        before = [said for at, said in wakes.get(str(m.session_id), []) if m.created_at - reach <= at <= m.created_at]
        if before and (before[-1]['tools'] or before[-1]['bundle']):
            found[str(m.id)] = before[-1]
    return found


def bundle_marks(ids):
    """{record id: {'id', 'sha' (its sha256's first 8), 'new' (whether it differs from the agent's bundle before
    it in that Mood)}} for the instructions bundles named (BundleRecord): what a turn's heading shows beside its
    cache mark, since a changed bundle is the usual reason a turn read its context cold (Justin, 25 Oct)."""
    from conversations.models import BundleRecord
    found = {}
    for r in BundleRecord.objects.filter(id__in=[i for i in set(ids) if i]):
        before = (BundleRecord.objects.filter(agent=r.agent, mood=r.mood, requested_at__lt=r.requested_at)
                  .order_by('-requested_at').values_list('sha256', flat=True).first())
        found[str(r.id)] = {'id': str(r.id), 'sha': r.sha256[:8], 'new': before is not None and before != r.sha256}
    return found


def how_payload(msg):
    """Model and effort an agent's message ran on ('' when unknown); what it
    wrote and read, in tokens, and what that cost; and whether it ended its turn.

    One response the model gives can be stored as several rows (its thinking,
    its words, a tool call), each with that response's usage: the page counts
    a repeat of the same usage once."""
    out = {'model': model_label(msg.model_backend), 'effort': msg.effort or ''}
    if msg.output_tokens is not None or msg.input_tokens is not None:
        out['out'] = msg.output_tokens or 0
        out['ctx'] = sum(n or 0 for n in (msg.input_tokens, msg.cache_read_input_tokens,
                                          msg.cache_creation_input_tokens))
        out['cached'] = msg.cache_read_input_tokens or 0  # of ctx, read back from the cache: the cheap part
        usd = call_usd(msg.model_backend, msg.input_tokens, msg.output_tokens, msg.cache_read_input_tokens,
                       msg.cache_creation_input_tokens)
        if getattr(msg, 'cost_usd', None) is not None:  # what its harness says it cost (Pi): no guess needed
            usd, out['paid'] = msg.cost_usd, True
        if usd is not None:
            out['usd'] = round(usd, 5)
    if msg.stop_reason:
        out['stop'] = msg.stop_reason
    # The block it was written in, once stamped (a few minutes on): what its heading shows. Only where it was
    # loaded: a list that asked for a few fields mustn't ask again for each row.
    if 'eth_blockheight' not in msg.get_deferred_fields() and msg.eth_blockheight:
        out['block'] = msg.eth_blockheight
    return out


def attestation_of(msg):
    """What makes a turn an attestation -- exactly what was signed, the
    signature, the key -- or None."""
    c = msg.content
    if msg.source_file != 'mood-attest' or not isinstance(c, dict) or c.get('type') != 'attestation':
        return None
    return {k: c.get(k, '') for k in ('signed', 'signature', 'key', 'namespace')}


def turn_payload(msg, text, mentionable=()):
    from . import tiding_links
    from .links import linked_from
    # A ```voice block is how its writer wants it read aloud: performed, not shown.
    # A ```handoff block went to another Mood (services/handoff.py): a line says so, not the block.
    from .handoff import split as split_handoffs
    from .voice import split_voices
    text, directions = split_voices(text)
    text, _ = split_handoffs(text)
    target, said = reply_to(text)
    answered = replied(target) if target else None
    return {
        'voiced': bool(directions),
        'mobile': 'mobile' in client_parts(msg),  # posted from a phone or tablet
        # Signed in with PickiPedia, not an SSH key: chat and mentions of people only.
        'tier': 'wiki' if from_wiki_tier(msg) else None,
        # Several blocks (auditions, a dialogue): one ▶ each, named for its voice.
        'voices': [d['voice'] or 'the house voice' for d in directions] if len(directions) > 1 else [],
        'attested': attestation_of(msg),
        **how_payload(msg),
        'id': str(msg.id),
        'sender': msg.sender_id,
        'is_human': bool(getattr(getattr(msg.sender, 'thinkingentity', None),
                                 'is_biological_human', False)),
        'created_at': msg.created_at.isoformat(),
        # Which turn it was in: the poller folds a lead-in (stop tool_use) only into its own session's
        # later reply, not a later turn that belongs to a new session (re, 29 Oct).
        'session': str(msg.session_id or ''),
        # Typed into the web composer rather than a runtime session: nobody
        # live is listening for it, so the poller need not wait.
        'via': 'web' if msg.source_file in POSTED else 'session',
        'text': text,
        # A reply: what it answers, shown above it; its author is addressed.
        'reply': answered if target else None,
        'mentions': addressed_in(text, mentionable, answered, by=msg.sender_id),
        'html': render_html(said, mentionable),
        # Who linked here, or replied (services/links.py): shown under it.
        'linked_from': linked_from(msg.id),
        # Its links, which can be put on Tidings, and the one that is (services/tiding_links.py).
        'links': tiding_links.links_in(said),
        'tidings': tiding_links.by_message().get(str(msg.id), []),
        # A poll (services/polls.py): drawn as its card, its votes from the Mood's 'polls'.
        'poll': poll_of(said, mentionable),
    }


def poll_of(text, mentionable=()):
    """A poll's question and choices, as its card shows them; None for a message that isn't one."""
    from . import polls
    if not polls.is_poll(text):
        return None
    try:
        found = polls.parse(text)
    except polls.Refused:
        return None
    return {'question_html': render_html(found['question'], mentionable), 'choices': found['choices'],
            'detail_html': render_html(found['detail'], mentionable) if found['detail'] else ''}


# --- activity: what an agent is doing right now ------------------------------

TURN_ENDS = {'end_turn', 'refusal', 'stop_sequence', 'max_tokens'}
ACTIVITY_WINDOW = 600  # seconds; a streak older than this is a session that died
RECENT = 60  # rows read to see what's happening now
_TOOL_WORDS = {
    'Bash': 'running a command', 'Read': 'reading', 'Grep': 'searching', 'Glob': 'searching',
    'Edit': 'editing', 'Write': 'writing a file', 'NotebookEdit': 'editing',
    'WebFetch': 'reading the web', 'WebSearch': 'searching the web',
    'Agent': 'working with helpers', 'Task': 'working with helpers',
    'ToolSearch': 'finding a tool', 'Skill': 'reading up', 'TodoWrite': 'planning',
}
_MCP_WORDS = {'playwright': 'using the browser', 'pickipedia': 'on PickiPedia',
              'magenta-memory-v2': 'remembering', 'magenta-memory': 'remembering'}


# What an agent is doing while one of Claude Code's own commands runs: asked
# from a Mood ("@magent /compact"), or typed into a session ("/compact").
COMMAND_DOING = {'compact': 'compacting', 'context': 'measuring its context', 'usage': 'checking usage',
                 'cost': 'checking usage'}


def command_doing(text, agent):
    """'compacting' for a post like "/compact magent what to keep"; None for anything else."""
    from .agent_commands import doing
    name = doing(text, agent, agent_names())
    return COMMAND_DOING.get(name) if name else None


def _doing(msg):
    """A few words for what a machinery message says the agent is doing."""
    if isinstance(msg.content, dict) and msg.content.get('type') == 'slash_command':
        return COMMAND_DOING.get(str(msg.content.get('command_name', '')).lstrip('/').lower(), 'thinking')
    if hasattr(msg, 'tooluse'):
        name = msg.tooluse.tool_name
        if name.startswith('mcp__'):
            server = name.split('__')[1]
            return _MCP_WORDS.get(server, f'using {server}')
        return _TOOL_WORDS.get(name, f'using {name}')
    return 'thinking'


def _when(msg):
    return msg.timestamp / 1000 if msg.timestamp else msg.created_at.timestamp()


# --- how full an agent's context is ------------------------------------------
# Every assistant line records what its model read to write it: fresh input
# plus cache reads and writes. The newest such line in a Mood's newest
# session is how full that agent's context is now -- what a wake would
# resume into. Windows by model; the record shows Opus 5.5 sessions passing
# 865k tokens, so its window is 1M. Anything unlisted is taken as 200k,
# and a session seen past its window is taken to have the larger one.
CONTEXT_WINDOWS = (('claude-opus-5', 1_000_000),)
DEFAULT_WINDOW = 200_000


def context_window(model, tokens=0):
    window = next((w for prefix, w in CONTEXT_WINDOWS if (model or '').startswith(prefix)), DEFAULT_WINDOW)
    return window if tokens <= window else max(window, 1_000_000)


def context_in(mood, agent):
    """{'tokens', 'window', 'model', 'at'} for `agent`'s context here, or None."""
    # A slash command's output (/context, /usage) is a "<synthetic>" message
    # (stored with no model) that read nothing: it says nothing about the context.
    row = (mood.messages.filter(sender_id=agent, is_sidechain=False, input_tokens__isnull=False,
                                  model_backend__isnull=False)
           .order_by('-created_at')
           .values('input_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens', 'model_backend',
                   'created_at').first())
    if row is None:
        return None
    tokens = sum(row[k] or 0 for k in ('input_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens'))
    window = context_window(row['model_backend'], tokens)
    # Compacted since (`@magent /compact`): no turn has measured the context
    # yet, so it is about the summary's size until the next one does.
    summary = compacted_since(mood, agent, row['created_at'])
    if summary is not None:
        return {'tokens': len(summary.text) // CHARS_PER_TOKEN, 'window': window, 'model': row['model_backend'] or '',
                'at': summary.at.isoformat(), 'compacted': True}
    return {'tokens': tokens, 'window': window, 'model': row['model_backend'] or '', 'at': row['created_at'].isoformat()}


CHARS_PER_TOKEN = 4  # roughly, for English prose


class _Summary:
    def __init__(self, text, at):
        self.text, self.at = text, at


def compacted_since(mood, agent, when):
    """The summary `agent`'s context went on from, if it was compacted after
    `when` (its last measured turn); else None. What follows a summary
    before the next turn is only the command's own echo."""
    for row in (mood.messages.filter(sender_id=agent, is_sidechain=False, created_at__gt=when)
                .order_by('-created_at').values('content', 'created_at', 'is_continuation_message')[:20]):
        content = row['content']
        if isinstance(content, list):
            content = ' '.join(b.get('text', '') for b in content if isinstance(b, dict) and b.get('type') == 'text')
        if isinstance(content, str) and (row['is_continuation_message'] or is_compaction(content)):
            return _Summary(content, row['created_at'])
    return None


# --- a mention the runner is holding ----------------------------------------
# The record can show that a post names an agent and nothing has answered it,
# but not why: a runner may be holding it -- its hourly cap reached, the
# agent hushed here, a turn already under way. The runner says so, and the
# Mood shows "held" instead of an endless "waking". Ephemeral, like typing:
# a shared cache entry per Mood, agent -> {reason, until, at}, which lapses
# unless the runner renews it, so a runner that dies leaves no stale hold.
HELD_FOR = 600  # seconds a hold lasts unless renewed


def _held_key(slug):
    return f'held:{slug}'


def held_in(slug, now=None):
    """agent -> {'reason', 'until', 'at'} for each hold still in force."""
    from django.core.cache import cache
    import time
    now = now or time.time()
    return {agent: h for agent, h in (cache.get(_held_key(slug)) or {}).items() if now - h['at'] < HELD_FOR}


def set_held(slug, agent, reason, until=None, now=None):
    """Hold `agent`'s next turn in `slug` for `reason`; an empty reason lifts it."""
    from django.core.cache import cache
    import time
    now = now or time.time()
    holds = held_in(slug, now)
    if reason:
        holds[agent] = {'reason': reason, 'until': until, 'at': now}
    else:
        holds.pop(agent, None)
    cache.set(_held_key(slug), holds, HELD_FOR)
    return holds


# The runner says, every RUNNING_EVERY seconds, which of its agents' turns is still running in a
# Mood (mood_poller.MoodPoller.run_turn_once). The record alone can't: a tool call that runs half an
# hour writes nothing, and past ACTIVITY_WINDOW its streak read as a session that died -- magent's
# Playwright call on 28 Oct hung 30 minutes and the Mood showed nobody at work (Justin, #usage).
# Ephemeral, like a hold: it lapses unless renewed, so a runner that dies leaves no ghost at work.
RUNNING_FOR = 300  # seconds a turn is taken as running after the runner last said so


def _running_key(slug):
    return f'running:{slug}'


def running_in(slug, now=None):
    """agent -> {'since', 'at'} for each turn its runner says is still running here."""
    from django.core.cache import cache
    import time
    now = now or time.time()
    return {agent: r for agent, r in (cache.get(_running_key(slug)) or {}).items() if now - r['at'] < RUNNING_FOR}


def set_running(slug, agent, since=None, now=None):
    """`agent`'s turn in `slug` is running, since `since` (epoch seconds); None says it has ended."""
    from django.core.cache import cache
    import time
    now = now or time.time()
    running = running_in(slug, now)
    if since is not None:
        running[agent] = {'since': since, 'at': now}
    else:
        running.pop(agent, None)
    cache.set(_running_key(slug), running, RUNNING_FOR)
    return running


def activity(mood, now=None, only=None):
    """What an agent in this Mood is doing now, or None: see _activity.
    Someone stopping the agent since that began ends it at once, without
    waiting for its runner to notice."""
    doing = _activity(mood, now, only)
    if doing and doing['doing'] != 'held':
        stopped = latest_interrupt(mood, doing['agent'])
        if stopped and stopped['at_ts'] >= doing['since']:
            return None
    return doing


def activities(mood, now=None):
    """What each agent in this Mood is doing now, longest at it first: [] if nothing is
    underway. Two agents woken by one post, or one asked and another looking in of its
    own accord, both show (Justin, 18 Oct: re was woken and the page said magent was
    thinking, as activity() says only whose line is newest). Each is read from its own
    lines and the posts that name it, as activity() reads them all."""
    from conversations.models import ThinkingEntity
    agents = sorted(ThinkingEntity.objects.filter(is_biological_human=False).values_list('name', flat=True))
    found = [a for a in (activity(mood, now, only=agent) for agent in agents) if a]
    return sorted(found, key=lambda a: a['since'])


def latest_interrupt(mood, agent=None):
    """{'agent', 'by', 'at', 'at_ts'} for the newest time someone stopped
    `agent` (any agent, if None) here; None if nobody ever has."""
    for msg in (mood.messages.filter(source_file=INTERRUPT_SOURCE).order_by('-created_at')
                .only('content', 'timestamp', 'created_at')[:20]):
        c = msg.content if isinstance(msg.content, dict) else {}
        if agent is None or c.get('agent') == agent:
            at = _when(msg)
            from datetime import datetime, timezone as tz
            return {'agent': c.get('agent'), 'by': c.get('by'), 'at_ts': at,
                    'at': datetime.fromtimestamp(at, tz.utc).isoformat()}
    return None


def _command_output(msg):
    """Whether this is the output of a command typed into Claude Code itself
    (/compact, /context): written when the command has finished, so it ends
    whatever was under way. /compact's "Compacted" is the newest line once
    the summary is written; read as work, it showed the agent thinking for
    ten minutes after every compaction."""
    return isinstance(msg.content, dict) and msg.content.get('type') == 'command_output'


def _activity(mood, now=None, only=None):
    """What an agent in this Mood is doing now, or None if nothing is underway.

    Read straight from the record, so no process has to report in: every
    thought, tool call and prompt streams into the Mood as it happens, and
    an assistant line whose stop_reason is end_turn closes the turn. So an
    agent is working from the first line after its last finished turn until
    the next one, and the newest line says what it's doing. A web post that
    names an agent and has nothing after it means the agent is being woken,
    unless its runner has said it is holding that turn, and why.

    `only`: that agent alone, as if no other were here -- its own lines, people's,
    and only the posts that name it (activities()).
    """
    from conversations.models import Message, ThinkingEntity
    import time

    now = now or time.time()
    agents = set(ThinkingEntity.objects.filter(is_biological_human=False).values_list('name', flat=True))
    # Helpers' lines (sidechains) are the agent's own call still running, so
    # they neither describe nor end its turn. System lines are the harness's
    # bookkeeping -- Claude Code writes one just after a turn ends -- and
    # say nothing about whether anyone is working.
    rows = mood.messages.filter(is_sidechain=False).exclude(sender_id='system')
    if only:
        rows = rows.exclude(sender_id__in=agents - {only})
        # Nor what came back to the others: their tool results are their sessions' lines, not this
        # agent's, and read as its own they started its turn when theirs began (Justin, 20 Oct:
        # magent "thinking for 15 minutes" two minutes after re woke it).
        theirs = {s for s in mood.messages.filter(sender_id__in=agents - {only}).exclude(session_id=None)
                  .order_by('-created_at').values_list('session_id', flat=True)[:RECENT * 2]}
        if theirs:
            rows = rows.exclude(session_id__in=theirs)
    recent = list(rows.select_related('sender', 'tooluse', 'thought', 'toolresult')
                  .order_by('-created_at')[:RECENT * (2 if only else 1)])

    def named_in(msg):
        # A wiki-tier post's @agent is just text: no agent is waking for it.
        return [] if from_wiki_tier(msg) else [n for n in addressed_in(prose(msg.content), agents, by=msg.sender_id)]
    if only:  # a post for someone else neither wakes this agent nor ends its turn
        recent = [m for m in recent if m.source_file not in POSTED or only in named_in(m)]
        # Nor a wake addressed to another: the poller's prompt opens that agent's new session before the
        # agent has said a word in it, so no line of its own says the session is its (Justin, 21 Oct).
        elsewhere = set(Message.recipients.through.objects
                        .filter(message_id__in=[m.pk for m in recent if m.sender_id not in agents],
                                conversationparticipant_id__in=agents - {only})
                        .values_list('message_id', flat=True))
        recent = [m for m in recent if m.pk not in elsewhere][:RECENT]
    if not recent:
        return None
    newest = recent[0]

    # Waiting for another agent asked the same thing (poller's _WAIT_REPLY): its reply was the newest line, a
    # silence, and the runner's hold since says whom it waits for (Justin, 24 Oct: "magent is waiting for re").
    if only:
        hold = held_in(mood.slug, now).get(only)
        if hold and hold['at'] >= _when(newest) and str(hold.get('reason') or '').startswith('waiting for'):
            return {'agent': only, 'doing': 'held', 'why': hold['reason'], 'until': hold.get('until'),
                    'since': hold['at']}

    if newest.source_file in POSTED:
        named = [only] if only else named_in(newest)
        if not named:
            return None
        # Held only if the runner said so after this post: a newer post is
        # waking until the runner has looked at it. A hold is shown for as
        # long as the runner keeps it -- an hour, at the hourly cap -- not
        # just the window an unexplained wait is shown for.
        hold = held_in(mood.slug, now).get(named[0])
        if hold and hold['at'] >= _when(newest):
            return {'agent': named[0], 'doing': 'held', 'why': hold['reason'], 'until': hold.get('until'),
                    'since': _when(newest)}
        alive = running_in(mood.slug, now).get(named[0])
        if alive and alive['since'] <= _when(newest):
            # Posted while its turn was already running: it's at work on that, not waking for this.
            return {'agent': named[0], 'doing': 'still at work', 'since': alive['since']}
        if now - _when(newest) > ACTIVITY_WINDOW:
            return None
        # "@magent /compact" runs the command, not a turn: say what's happening.
        return {'agent': named[0], 'doing': command_doing(prose(newest.content), named[0]) or 'waking',
                'since': _when(newest)}

    # Quiet past the window is a session that died -- unless its runner says the turn is still running
    # (a tool call that takes half an hour writes nothing while it runs).
    stale = now - _when(newest) > ACTIVITY_WINDOW
    alive = running_in(mood.slug, now) if stale else {}
    if stale and not alive:
        return None
    # A runner writes what a turn came to once it has ended, and nothing of a
    # turn is after that. Most turns end on a line that says so too, but a
    # /compact run with -p streams only the summary -- never the "Compacted"
    # a terminal writes -- so the summary was read as the agent thinking.
    # Compared by when each line happened, not when it reached the record: the
    # watcher brings the run's own lines from its transcript a moment after the
    # runner's end -- a /compact's command line, a background task's notice --
    # and newest-arrived, they read as the agent at work again.
    ended = mood.messages.filter(sender_id='system', content__type='turn-result')
    if only:
        ended = ended.filter(content__agent=only)
    ended = ended.order_by('-created_at').first()
    ended = _when(ended) if ended is not None else None
    if ended is not None and ended >= _when(newest):
        return None
    if newest.sender_id in agents and newest.stop_reason in TURN_ENDS:
        return None
    # Rows imported before stop_reason was kept: a plain reply that has sat
    # for half a minute with nothing after it is taken as the end of a turn.
    if (newest.sender_id in agents and newest.stop_reason is None and now - _when(newest) > 30
            and not hasattr(newest, 'tooluse') and not hasattr(newest, 'thought')):
        return None
    if _command_output(newest):
        return None

    streak = []
    for msg in recent:
        if msg.sender_id in agents and msg.stop_reason in TURN_ENDS:
            break
        if msg.source_file in POSTED or _command_output(msg) or (ended is not None and _when(msg) <= ended):
            break
        streak.append(msg)
    agent = next((m.sender_id for m in streak if m.sender_id in agents), None)
    if agent is None:
        # Nothing yet from an agent: a wake prompt is the agent's it's addressed to -- re on a slow
        # model writes its first line a minute and more after the poller's prompt.
        agent = (Message.recipients.through.objects
                 .filter(message_id__in=[m.pk for m in streak], conversationparticipant_id__in=agents)
                 .values_list('conversationparticipant_id', flat=True).first())
    if agent is None:
        # Only a person's lines (typed into a terminal session): whose session it is.
        owner = (mood.messages.filter(session_id=newest.session_id, sender_id__in=agents)
                 .values_list('sender_id', flat=True).first()) if newest.session_id else None
        # Asked about one agent, lines nobody can say are its are not its turn. (Once, the first agent
        # by name took them: magent, then blu -- and re's wake read as blu thinking, Justin, 21 Oct.)
        agent = owner or (None if only else sorted(agents or {'magent'})[0])
    if agent is None or (only and agent != only) or (stale and agent not in alive):
        return None
    how = next((how_payload(m) for m in streak if m.sender_id in agents and m.model_backend), {'model': '', 'effort': ''})
    start = streak[-1]
    if len(streak) == len(recent) == RECENT:
        # A long turn runs past the rows read above: find where it began.
        from django.db.models import Q
        mine = mood.messages.filter(is_sidechain=False)
        if only:
            mine = mine.exclude(sender_id__in=agents - {only})
        boundary = (mine.filter(created_at__lt=start.created_at)
                    .filter(Q(sender_id__in=agents, stop_reason__in=TURN_ENDS) | Q(source_file__in=POSTED)
                            | Q(content__type='command_output'))
                    .order_by('-created_at').first())
        if boundary is not None:
            start = mine.filter(created_at__gt=boundary.created_at).order_by('created_at').first() or start
    # Which tools this turn has, as its wake said (Justin, 29 Oct: 🔨 by a pulsing name in the list).
    tools = next((said for said in (wake_said(prose(m.content) or '') for m in streak if m.sender_id == WAKE_SENDER) if said), '')
    return {'agent': agent, 'doing': _doing(newest), 'since': _when(start), **{k: v for k, v in how.items() if v},
            **({'tools': tools} if tools else {})}


# --- background tasks an agent is supervising ------------------------------

_TASK_STARTED = re.compile(r'Command running in background with ID: (\w+)|Async agent launched successfully.*?agentId: (\w+)', re.S)
_TASK_ENDED = re.compile(r'<task-id>(\w+)</task-id>.*?<status>(\w+)</status>', re.S)
TASK_ENDINGS = {'completed', 'failed', 'stopped', 'killed', 'error', 'cancelled'}
# A task older than this with no word of its end is presumed gone: its notice
# can be lost when the session that started it exits first (in a terminal;
# a runner's turns are handled exactly, below). A background
# command can't outlive Claude Code's two-hour cap on its timeout; a helper
# agent has no cap, so it gets longer.
TASK_HORIZONS = {'command': 2.5 * 3600, 'helper': 12 * 3600}
TASK_HORIZON = max(TASK_HORIZONS.values())


def background_tasks(mood, now=None):
    """Commands and helpers an agent started in the background here and that
    haven't ended, read from the record: the tool result that started each
    one, and the <task-notification> that says it ended.
    """
    import json
    import time
    from datetime import datetime, timezone as tz
    from conversations.models import ToolResult, ToolUse

    now = now or time.time()
    since = datetime.fromtimestamp(now - TASK_HORIZON, tz.utc)
    rows = (mood.messages.filter(is_sidechain=False, created_at__gte=since)
            .filter(models_q(content__icontains='in background with ID')
                    | models_q(content__icontains='Async agent launched')
                    | models_q(content__icontains='<task-id>'))
            .order_by('created_at'))
    started, ended = {}, set()
    for msg in rows:
        text = msg.content if isinstance(msg.content, str) else json.dumps(msg.content)
        # Only a tool result that *is* a start message starts a task, and only
        # a notification ends one: output that merely quotes them (a log, a
        # query of this very table) is neither.
        if not hasattr(msg, 'toolresult'):
            for match in _TASK_ENDED.finditer(text):
                if match.group(2).lower() in TASK_ENDINGS:
                    ended.add(match.group(1))
            continue
        match = _TASK_STARTED.match(text.lstrip())
        if not match:
            continue
        task_id = match.group(1) or match.group(2)
        ended.discard(task_id)  # a helper resumed after it last finished
        use = (ToolUse.objects.filter(tool_id=msg.toolresult.tool_use_id, session_id=msg.session_id)
               .only('content', 'tool_name').first())
        args = use.content if use is not None and isinstance(use.content, dict) else {}
        started[task_id] = {
            'id': task_id,
            'kind': 'helper' if match.group(2) else 'command',
            'label': (args.get('description') or args.get('command') or args.get('prompt') or task_id)[:120],
            'since': _when(msg),
            'step': str(use.pk) if use is not None else None,  # the call that started it, shown on demand
            '_session': msg.session_id, '_at': msg.created_at,
        }
    # A turn the runner launched (claude -p) stops whatever it left running
    # when it ends, so a task started before its session's turn-result
    # (views_runner.finish_turn) is over, whether or not its notice arrived.
    over = {}
    sessions = {t['_session'] for t in started.values() if t['_session']}
    for session, at in (mood.messages.filter(sender_id='system', session_id__in=sessions, content__type='turn-result')
                        .values_list('session_id', 'created_at')):
        over[session] = max(at, over.get(session, at))
    running = []
    for task_id, task in started.items():
        session, at = task.pop('_session'), task.pop('_at')
        if task_id in ended or task['since'] < now - TASK_HORIZONS[task['kind']]:
            continue
        if session in over and over[session] >= at:
            continue
        running.append(task)
    return running


def models_q(**kwargs):
    from django.db.models import Q
    return Q(**kwargs)


def last_said(mood, scan=200):
    """When something was last said here: a person's post, or an agent speaking.
    Not an agent's silences (the dots), nor its thinking and tool calls on the
    way to one, nor the system's rows (a redeploy, a rename, a turn's tally):
    a Mood where an agent quietly looked and let it pass hasn't become active."""
    rows = (mood.messages.filter(is_sidechain=False, thought__isnull=True, tooluse__isnull=True, toolresult__isnull=True)
            .exclude(sender_id__in=MACHINERY_SENDERS).order_by('-created_at').only('content', 'created_at')[:scan])
    oldest = None
    for msg in rows:
        oldest = msg.created_at
        text = prose(msg.content)
        if text and not is_wrapper(text) and quiet_reason(text) is None:
            return msg.created_at
    return oldest  # nothing said in the latest `scan` rows: as good a guess as any


def mood_description(mood):
    from .mood_doc import description
    return description(mood)


def mood_payload(mood, light=False):
    """A Mood as the page shows it. `light`: its name and description only, for a poll for what's new -- the
    count of everything it ever said, and everyone who ever spoke there, read the whole of a big Mood, and a
    poll every few seconds was paying for them each time (Justin, 25 Oct: "it takes several seconds")."""
    head = {
        'slug': mood.slug,
        'title': mood.title or mood.slug,
        # Its document's first paragraph (services/mood_doc.py), what its emergents read; else the one kept here.
        'description': mood_description(mood),
        # Where to change it: the one document, on PickiPedia.
        'document': f"{pickipedia_url()}/wiki/Cryptograss:Moods/{mood.slug}/Instructions",
        'eth_blockheight': mood.eth_blockheight,
    }
    if light:
        return head
    # What was said, not the system's own rows (a redeploy announced in every
    # Mood, a rename, a turn's tally): those mustn't make a Mood look active.
    said = mood.messages.exclude(sender_id='system')
    last = last_said(mood)
    from conversations.models import Mood
    return {
        **head,
        'message_count': said.count(),
        'last_at': last.isoformat() if last else None,
        'participants': sorted(e.name for e in mood.thinking_entities()),
        # A thread: where it was opened from, and when its emergents went on from there (forked; none: fresh).
        **({'thread_of': Mood.objects.filter(pk=mood.thread_of_id).values_list('slug', flat=True).first(),
            'forked_at': mood.forked_at.isoformat() if mood.forked_at else None} if mood.thread_of_id else {}),
        # Threads opened from here, newest first (closing one archives it).
        'threads': [{'slug': s, 'title': t or s} for s, t in mood.threads.order_by('-created_at').values_list('slug', 'title')],
    }
