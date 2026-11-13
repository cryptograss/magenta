"""What happens here when a page changes on PickiPedia: one registry, run by the push and by the sweep.

Justin, #interfaces, 6 Nov 2026: "Magenta has a clear discrete list of pages that matter, and things that
happen when those pages change (ie, hooks). When a page that's the subject of a hook changes, the full
hook runs. That all gets logged somewhere."

wiki_hooks.yml, beside this file, is the list (HOOKS, as read from it). Each names the pages it is for and what it does: forget what this server kept of the
page and read it again now, so the words, the card or the instructions are warm before anyone looks.
A page no hook is for still has what's kept of it forgotten (services/wiki_changes.forget), and is read
again when someone next asks for it.

run() is the one road in: the push (views_moods.api_wiki_hook, told by PickiPedia's PageSaveComplete)
and the sweep (services/wiki_changes.sweep, once a minute, the check on the push) both call it, so pull
and push can never disagree about what a change does. Every run is a row in wiki_hook_runs: the page,
its revision, who edited it, which hook, by which road, how long it took and what came of it. The page
/wiki-hooks/ lists them. A hook that fails is a line in #ops as well (record_failed), so a quiet failure
is never quiet for long.

The poller (hunter) keeps its own copies of the wake texts and the dials (poller/wiki_rules.py: re-read as soon as
the log here says the page changed, every five minutes as the fallback) and the instructions bundle's layers (poller/instructions.py, by revision): those are its own,
in another process on another machine, and are not reached from here.
"""

import re
import time
import uuid
from dataclasses import dataclass
from typing import Callable

from django.core.cache import cache

SOURCE = 'wiki-hook'      # a failed run's line in #ops
ROOM = 'ops'
KEPT = 2000               # rows of the log kept
_MOOD_PAGE = re.compile(r'^Cryptograss:Moods/([^/]+)/(Instructions|todo)$', re.I)


@dataclass(frozen=True)
class Hook:
    name: str
    about: str                                  # what it does, for /wiki-hooks/
    pages: str                                  # which pages, in words
    matches: Callable[[str], bool]
    run: Callable[[str, str], str]              # (title, editor) -> what came of it


def _title(title):
    return re.sub(r'\s+', ' ', (title or '').replace('_', ' ')).strip()


def _is(*pages):
    lowered = {p.lower() for p in pages}
    return lambda title: title.lower() in lowered


def _words(title, _user):
    from . import interface_words
    interface_words.forget(title)
    return f'{len(interface_words.words())} words read again'


def _faq(_title, _user):
    from . import faq
    cache.delete(faq.KEY)
    got = faq.state()
    return got.get('error') or f"FAQ read again (rev {got.get('revid')})"


def _clips(_title, _user):
    from . import clips
    cache.delete(clips.USES_KEY)
    return f'{len(clips.uses() or {})} clips read again'


def _voice(_title, _user):
    from . import voice_words
    cache.delete('voice-words')
    return f'{len(voice_words.words())} voice words read again'


def _tour(_title, _user):
    from . import tour
    cache.delete(tour.KEY)
    got = tour.state()
    return got.get('error') or f"{len(got.get('steps') or [])} tour steps read again"


def _slogans(_title, _user):
    from . import front
    front.forget()
    return f'{len(front.slogans())} slogans read again'


def _eras(_title, _user):
    from . import eras
    cache.delete(eras.CACHE_KEY)
    return f'{len(eras.eras())} eras read again'


def _bloom(_title, _user):
    from . import bloom_notes
    cache.delete('current-bloom')
    name, _ = bloom_notes.current()
    return f'current bloom: {name}'


def _card(title, _user):
    from . import wiki_preview
    from .emergent_powers import FRAMES_KEY
    wiki_preview.forget(title)
    cache.delete(FRAMES_KEY)
    got = wiki_preview.preview(title)
    return 'card read again' if got else 'no such User page: card forgotten'


def _emergent(_title, _user):
    from conversations.models import ThinkingEntity
    from . import emergent_powers
    cache.delete(emergent_powers.KEY)
    agents = list(ThinkingEntity.objects.filter(is_biological_human=False).values_list('name', flat=True))
    return f'{len(emergent_powers.powers(agents))} emergents read again'


def _mood_doc(title, _user):
    from . import mood_doc
    slug = _MOOD_PAGE.match(title).group(1)
    cache.delete(mood_doc.KEY)
    mood_doc.refresh()
    return f'#{slug}: instructions read again'


def _todo(title, _user):
    from conversations.models import Mood
    from . import todo
    from .wiki_feed import home_of
    slug = home_of(title)
    mood = Mood.by_slug(slug) if slug else None
    if mood is None:
        return 'no such Mood: nothing kept'
    todo.forget(mood)
    return f'#{mood.slug}: to-do list forgotten; open pages ask for it now'


def _journals(_title, _user):
    from . import journals
    journals.forget()
    return 'journals section forgotten; read again when next opened'


def _tasks(_title, _user):
    from . import tasks
    tasks.forget_board()
    return 'tasks section forgotten; read again when next opened'


def _file(title, _user):
    from . import embeds
    name = title[5:]
    cache.delete('embeds:file:' + re.sub(r'\s+', '_', name.lower())[:200])
    return 'file read again' if embeds.wiki_file(name) else 'no such file: forgotten'


def _release(_title, _user):
    cache.delete('embeds:releases')
    from .tidings import releases_changed
    releases_changed()
    return 'release lists forgotten'


def _pattern(page):
    """A page entry as a matcher: an exact title, or a pattern: * for any text short of a / (a User: subpage is
    not a card), ** for any text (every page under Cryptograss:Emergents/)."""
    page = page.replace('_', ' ')
    if '*' not in page:
        return lambda title: title.lower() == page.lower()
    rx = re.compile('^' + ''.join('.+' if part == '**' else '[^/]+' if part == '*' else re.escape(part)
                                  for part in re.split(r'(\*\*|\*)', page)) + '$', re.I)
    return lambda title: bool(rx.match(title))


def _load():
    """The registry, from wiki_hooks.yml beside this file: each entry's run names a function here."""
    import pathlib
    import yaml
    entries = yaml.safe_load(pathlib.Path(__file__).with_name('wiki_hooks.yml').read_text()) or []
    hooks = []
    for e in entries:
        pages = [str(p) for p in (e.get('pages') or [])]
        matchers = [_pattern(p) for p in pages]
        hooks.append(Hook(str(e['name']), str(e.get('about', '')), ', '.join(pages),
                          lambda title, ms=matchers: any(m(title) for m in ms), globals()['_' + str(e['run'])]))
    return hooks


HOOKS = _load()


def hooks_for(title):
    title = _title(title)
    return [h for h in HOOKS if h.matches(title)]


def run(title, user='', revid=None, via='sweep'):
    """Forget what's kept of the page, run every hook for it, and log each: the rows written."""
    from conversations.models import WikiHookRun
    from . import wiki_changes
    title = _title(title)
    if not title:
        return []
    rows = []
    wiki_changes.forget(title, user)
    for hook in hooks_for(title):
        started = time.monotonic()
        failed, result = False, ''
        try:
            result = hook.run(title, user) or 'done'
        except Exception as e:  # noqa: BLE001 -- one hook failing never stops the next, and is said
            failed, result = True, f'{type(e).__name__}: {e}'
        row = WikiHookRun.objects.create(
            title=title[:300], revid=revid, user=(user or '')[:120], hook=hook.name, via=via,
            took_ms=int((time.monotonic() - started) * 1000), result=result[:300], failed=failed)
        rows.append(row)
        if failed:
            record_failed(row)
    if not rows:  # no hook is for it: still a line in the log, so the page is seen to have been forgotten
        rows.append(WikiHookRun.objects.create(title=title[:300], revid=revid, user=(user or '')[:120], hook='forget',
                                               via=via, took_ms=0, result='forgotten; read again when next asked for'))
    prune()
    return rows


def prune():
    """The log keeps its newest KEPT rows."""
    from conversations.models import WikiHookRun
    if WikiHookRun.objects.count() > KEPT + 200 and cache.add('wiki-hooks:pruning', 1, 60):
        keep = WikiHookRun.objects.order_by('-at').values_list('id', flat=True)[:KEPT]
        WikiHookRun.objects.exclude(id__in=list(keep)).delete()


def record_failed(row):
    """A failed run, as a line in #ops; None when there's no #ops."""
    from conversations.models import ConversationParticipant, Message, Mood
    mood = Mood.by_slug(ROOM)
    if mood is None:
        return None
    system, _ = ConversationParticipant.objects.get_or_create(name='system', defaults={'participant_type': 'system'})
    content = {'type': 'wiki-hook', 'title': row.title, 'revid': row.revid, 'hook': row.hook, 'via': row.via,
               'error': row.result}
    return Message.objects.create(id=uuid.uuid4(), sender=system, mood=mood, content=content,
                                  timestamp=int(time.time() * 1000), source_file=SOURCE)


def line(c):
    """What a failed hook's line says (mood_view.event_line)."""
    return f"⚠ the {c.get('hook', '?')} hook failed on [[{c.get('title', '')}]] ({c.get('via', 'sweep')}): {c.get('error', '')}"


def seq():
    """The newest run's id, 0 before any: the pulse carries it, so hunter's poller knows when a page changed."""
    from conversations.models import WikiHookRun
    return WikiHookRun.objects.order_by('-id').values_list('id', flat=True).first() or 0


def state(limit=200, since=None):
    """For /wiki-hooks/: the registry, the log's newest rows, and what the push last said. With `since` (a run
    id), only the runs after it, oldest first: what hunter's poller asks for when the pulse's seq moves on."""
    from conversations.models import WikiHookRun
    from . import wiki_changes
    if since is None:
        runs = WikiHookRun.objects.order_by('-at')[:limit]
    else:
        runs = WikiHookRun.objects.filter(id__gt=since).order_by('id')[:limit]
    return {
        'hooks': [{'name': h.name, 'pages': h.pages, 'about': h.about} for h in HOOKS],
        'runs': [{'id': r.id, 'at': r.at.isoformat(), 'title': r.title, 'revid': r.revid, 'user': r.user,
                  'hook': r.hook, 'via': r.via, 'took_ms': r.took_ms, 'result': r.result, 'failed': r.failed}
                 for r in runs],
        'push': wiki_changes.push_state(),
        'seq': seq(),
    }
