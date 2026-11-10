"""A candidate branch that conflicts wakes its author, in the Mood it came from (Justin and Skyler, 10 Nov:
"it makes sense for conflicts to awaken you in the mood where the CB originated").

Each minute (run_due_tasks) the last ramp board is read (services/ramp.clashes marks each open candidate: its
conflicts with its base, its clashes with other open candidates). An open candidate with either, whose author is
an emergent, starts that emergent's Conflict task -- Cryptograss:Emergents/<Name>/Tasks/Conflict -- in its first
Mood, with what conflicts as the task's blanks. As the Jenkins alarms do (services/alarms.py): which emergent,
where and with what words are read off the page and the board, and the page runs only once a trusted editor has
saved it. Until then nothing starts, and the ramp's marks still say it.

Once per head: a candidate is woken for a commit once, however many minutes it stays in conflict; a new commit
that still conflicts is a new wake. A person's candidate wakes nobody: its marks on the ramp say it.
"""

TASK = 'Conflict'
ROOM = 'ramp'   # where a candidate that names no Mood, and was said in none, is told


def author_of(item):
    """The emergent whose candidate this is: its branch's owner (re/foo is re's), else the first emergent the card
    names. None for a person's."""
    from conversations.models import ThinkingEntity
    from . import ramp
    emergents = set(ThinkingEntity.objects.filter(is_biological_human=False).values_list('name', flat=True))
    owner = (item.get('branch') or '').split('/')[0] if '/' in (item.get('branch') or '') else ''
    if owner:
        return owner if owner in emergents else None  # skyler/foo is skyler's, whoever else is on its card
    return next((n for n in ramp.people_of(item) if n in emergents), None)


def mood_of(item):
    """The Mood a candidate came from: the first its card names, else the first it was said in, else #ramp."""
    if item.get('moods'):
        return item['moods'][0]
    said = [m.get('mood') for m in item.get('mentions') or [] if m.get('mood')]
    return said[-1] if said else ROOM  # mentions are newest first: its first is the last


def due(now=None, get=None):
    """Wake each conflicting candidate's author, once per head. Returns [(ref, the task's line or why not)]."""
    from conversations.models import Message
    from django.core.cache import cache
    from . import ramp, tasks
    board = cache.get(ramp.KEY)  # the last board, never built here: that takes seconds, and the page builds it
    if not board:
        return []
    done = []
    for item in board.get('open', []):
        if item.get('state') == 'merged' or not item.get('head'):
            continue
        conflicts, clashes = item.get('conflicts') or [], item.get('clashes') or []
        if not conflicts and not clashes:
            continue
        agent = author_of(item)
        if agent is None:
            continue
        ref = item.get('ref') or f"{item['repo']}:{item.get('branch', '')}"
        if Message.objects.filter(source_file=tasks.SOURCE, content__task=TASK, content__for=agent,
                                  content__head=item['head']).exists():
            continue  # this commit's conflict is told already
        try:
            task = tasks.read(agent, TASK, get=get)
        except tasks.Refused as e:  # no trusted page yet: nothing starts, tried again next minute
            done.append((ref, str(e)))
            continue
        except Exception as e:  # noqa: BLE001 -- PickiPedia down: the same
            done.append((ref, f"couldn't read {agent}'s {TASK}: {e}"))
            continue
        base = item.get('base') or 'its base'
        offered = {
            'ref': ref, 'branch': item.get('branch', ''), 'repo': item['repo'], 'base': base,
            'head': item['head'], 'title': item.get('title', ''),
            'conflicts': ', '.join(conflicts) or 'none',
            'clashes': '; '.join(f"{c['ref']} in {', '.join(c['files'])}" for c in clashes) or 'none',
            'page': f"https://magenta.cryptograss.live{_page(item)}",
        }
        params = {k: v for k, v in offered.items() if k in task.blanks}
        line = tasks.start(task, 'ramp', params=params, mood=mood_of(item), now=now)
        line.content = {**line.content, 'head': item['head'], 'ref': ref}  # so this commit isn't told twice
        line.save(update_fields=['content'])
        done.append((ref, line))
    return done


def _page(item):
    from .work_pages import address
    return address(item['repo'], item['branch']) if item.get('branch') else '/ramp/'
