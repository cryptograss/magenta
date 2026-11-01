"""Saved clips: the team's library of quotes, kept in the record itself.

    /clips save <name> <yarn link>   saves a clip under a name (or one of our own clips' links)
    /clips <name>                    posts the saved clip (lines after it: words of yours, under it)
    /clips forget <name>             forgets a name
    /clips                           lists the names (to whoever asked; nothing posted)

/clips was /yarn until 2026-10-05 -- named for where clips come from today,
Yarn, rather than what they are -- so '/yarn' is still understood, and posts
made with it are still part of the library.

Saving and forgetting are posts like any other (shown as one line, "🎬 saved
tony", without the clip), so the team sees new names as they arrive, and the
library is read back from those posts: no table of its own, and history and
who-saved-what come with it. '/clips tony' posts the clip itself, as
'/clips tony <link>' -- shown as just the clip; the name stays in the record --
so saving the name again later never changes what was already said.

Agents play clips too: a reply of theirs that is only '/clips <name>' (or
'/clip <name>') is stored as the composer would post it (played_by, from the
runner). Only playing: an agent's '/clips save' or 'forget' is just words,
since the library is read back from people's posts alone.

A clip can be our own, too: cut from a local file by `magenta.sh clips save
<file> <start> <end> "<subtitle>"` (tools/mood_clip.py), kept here as a
YarnClip with made_by, linked as /clips/<id>, and saved under a name by the
same kind of post.

A name is first come, first served: only whoever saved it can save over it or
forget it. That rule is applied when the library is read back, so it holds
even for a post that never went through command().
"""

import re

from django.core.cache import cache

from .mood_view import own_clip, yarn_clip

NAME = re.compile(r'^[a-z0-9][a-z0-9_-]{0,79}$')  # up to 80 (Justin, 1 Nov; was 35)


def name_of(said):
    """A clip's name as said: lower case, apostrophes dropped (Justin, 1 Nov: "yeah-that's-a-good-one")."""
    return re.sub(r"['’]", '', said).lower()
RESERVED = {'save', 'forget', 'list'}
SAVED = re.compile(r'^/(?:clips|yarn) save (\S+) (\S+)$')
FORGOTTEN = re.compile(r'^/(?:clips|yarn) forget (\S+)$')
CACHE_KEY = 'clips-library'
COMMANDS = ('/clips', '/yarn')  # /yarn: what it was first called
USAGE = '/clips <name> posts a saved clip; /clips save <name> <yarn link>; /clips forget <name>; /clips lists them'


AGENT_PLAY = re.compile(r"^/clips? ([a-z0-9][a-z0-9_'’-]{0,90})$", re.I)


class Refused(ValueError):
    pass


def played_by(text):
    """An agent's reply whose first line is only '/clips <name>' (or '/clip <name>'): the clip, as the
    composer posts it ('/clips <name> <link>'), and any lines after it as they were. None for anything
    else, or a name not saved."""
    first, _, rest = text.strip().partition('\n') if isinstance(text, str) else ('', '', '')
    played = AGENT_PLAY.match(first.strip())
    if not played and isinstance(text, str):
        # On its own last line, after its words (blu, 28 Oct: "...then write the claim.\n\n/clip interesting"):
        # the same clip, moved to the top, its words under it, as the page shows a clip with words.
        lines = text.strip().split('\n')
        last = AGENT_PLAY.match(lines[-1].strip()) if len(lines) > 1 else None
        if last:
            played, first, rest = last, lines[-1], '\n'.join(lines[:-1])
    found = every().get(name_of(played[1])) if played else None
    return f'/clips {name_of(played[1])} {link_of(found)}' + (f'\n{rest.strip()}' if rest.strip() else '') if found else None


def clip_link(clip):
    return f'https://www.yarn.co/yarn-clip/{clip}'


def link_of(entry):
    """A library entry's link: our own clip's as it was saved, else its Yarn page."""
    return entry.get('link') or clip_link(entry['clip'])


def own_link(clip, base):
    return f'{base.rstrip("/")}/clips/{clip}'


def library():
    """name -> {'clip', 'by', 'at'}: each name's latest save, unless since forgotten."""
    found = cache.get(CACHE_KEY)
    if found is not None:
        return found
    from django.db.models import TextField
    from django.db.models.functions import Cast

    from conversations.models import Message
    from conversations.views_auth import WEB_SOURCE

    from django.db.models import Q
    posts = (Message.objects.filter(source_file=WEB_SOURCE)
             .annotate(as_text=Cast('content', TextField()))
             .filter(Q(as_text__startswith='"/clips ') | Q(as_text__startswith='"/yarn '))
             .order_by('created_at').values_list('content', 'sender_id', 'created_at'))
    found = {}
    for content, by, at in posts:
        if not isinstance(content, str):
            continue
        saved, forgotten = SAVED.match(content), FORGOTTEN.match(content)
        if saved:
            name, clip, own = saved[1], yarn_clip(saved[2]), own_clip(saved[2])
            if (clip or own) and NAME.match(name) and found.get(name, {}).get('by', by) == by:
                found[name] = {'clip': clip or own, 'by': by, 'at': at.isoformat(), **({'link': saved[2]} if own else {})}
        elif forgotten and found.get(forgotten[1], {}).get('by') == by:
            del found[forgotten[1]]
    cache.set(CACHE_KEY, found, 300)
    return found


def forget_cached():
    cache.delete(CACHE_KEY)


def command(text, who):
    """For a post: (what to post instead -- None to post nothing --, a note for
    whoever sent it). Text that isn't a /clips command comes back as it is.
    Raises Refused, with what to do instead."""
    words = text.strip().split()
    if not words or words[0].lower() not in COMMANDS:
        return text, ''
    args = words[1:]
    # '/clips <name>' on its first line, and words of the sender's under it: the clip, then them (Justin, 24 Oct).
    first, _, rest = text.strip().partition('\n')
    if rest.strip() and len(first.split()) == 2 and first.split()[1].lower() not in RESERVED | {'list'}:
        args = first.split()[1:]
    found, playable = library(), every()

    if not args or args == ['list']:
        said = uses()
        names = sorted(playable)
        return None, ('Saved clips (what each is for: Cryptograss:Clips): '
                      + ', '.join(f'{n} ({said[n]})' if n in said else n for n in names)) if names else \
            'No clips saved yet: /clips save <name> <yarn link>'

    def owner_check(name):
        owner = found.get(name, {}).get('by')
        if owner and owner != who:
            raise Refused(f'"{name}" is {owner}\'s clip; pick another name')

    if args[0].lower() == 'save':
        if len(args) != 3:
            raise Refused('/clips save <name> <yarn link>')
        name = name_of(args[1])
        if not NAME.match(name) or name in RESERVED:
            raise Refused('a name: letters, digits, - and _, up to 80, and not save, forget or list')
        link = args[2].rstrip('.,;:!?)')
        clip = yarn_clip(link)
        if not clip and not own_clip(link):
            raise Refused("that isn't a Yarn clip link (https://www.yarn.co/yarn-clip/…), or one of ours (…/clips/…)")
        owner_check(name)
        return f'/clips save {name} {clip_link(clip) if clip else link}', f'Saved "{name}": /clips {name} posts it'

    if args[0].lower() == 'forget':
        if len(args) != 2:
            raise Refused('/clips forget <name>')
        name = name_of(args[1])
        if name not in found:
            raise Refused(f'No clip called "{name}"')
        owner_check(name)
        return f'/clips forget {name}', f'Forgot "{name}"'

    if len(args) == 1:
        name = name_of(args[0])
        if name not in playable:
            raise Refused(f'No clip called "{name}": save one with /clips save {name} <yarn link>')
        return f'/clips {name} {link_of(playable[name])}' + (f'\n{rest.strip()}' if rest.strip() else ''), ''

    raise Refused(USAGE)


# --- the clips page: what each clip is for (Justin, 1 Nov) ------------------------------------------------------
# Cryptograss:Clips is where the team says what each clip is for: one row a clip, name || link || for. The
# emergents are told it whole, as a layer of their bundle (poller/instructions.py), and the page here shows each
# use beside its name (/clips, the composer's suggestions, `magenta.sh clips list`). A row whose name isn't saved
# here yet plays too: the page can add a clip. Only a trusted revision counts, as for every page the emergents are
# told: a link or a use is only as good as who wrote it.
PAGE = 'Cryptograss:Clips'
USES_KEY = 'clips-page'
USES_FOR = 600       # seconds the page is kept
USES_FAILED = 120    # and how soon a failed read is tried again
_ROW = re.compile(r'^\|(?![-}+])\s*(.+?)\s*\|\|\s*(\S*)\s*\|\|\s*(.*?)\s*$')


def page_rows(wikitext):
    """{name: {'link', 'for'}} from the page's table rows: | name || link || for."""
    rows = {}
    for line in (wikitext or '').splitlines():
        m = _ROW.match(line.strip())
        if not m:
            continue
        name, link, use = name_of(m[1].strip('`<>[] ')), m[2].strip('[]'), m[3]
        use = re.sub(r'\{\{\s*(?:verified|source)\s*\|[^{}]*\}\}', '', use).strip()
        if NAME.match(name) and name not in RESERVED:
            rows[name] = {'link': link, 'for': use}
    return rows


def page(get=None):
    """The clips page's rows, at its newest trusted revision; kept a while, and the last read stands if it fails."""
    kept = cache.get(USES_KEY)
    if kept is not None and get is None:
        return kept
    try:
        try:
            from poller.wiki_rules import EDITORS, WikiTexts
        except ImportError:
            from wiki_rules import EDITORS, WikiTexts
        trusted = {e.lower().replace('_', ' ') for e in EDITORS}
        history = (get or WikiTexts().get)(PAGE)
        text = next((w for _, editor, w in history if (editor or '').lower().replace('_', ' ') in trusted), '')
        found = page_rows(text)
        cache.set(USES_KEY, found, USES_FOR)
        cache.set(USES_KEY + ':last', found, None)
    except Exception:
        found = cache.get(USES_KEY + ':last') or {}
        cache.set(USES_KEY, found, USES_FAILED)
    return found


def uses():
    """{name: what it's for}, as the clips page says."""
    return {name: row['for'] for name, row in page().items() if row['for']}


def every():
    """The library with the page's rows that aren't saved here yet: name -> {'clip', 'by', ...}."""
    found = dict(library())
    for name, row in page().items():
        if name in found:
            continue
        clip, own = yarn_clip(row['link']), own_clip(row['link'])
        if clip or own:
            found[name] = {'clip': clip or own, 'by': 'the clips page', **({'link': row['link']} if own else {})}
    return found
