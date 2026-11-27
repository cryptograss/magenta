"""At a glance: who's on what, and what needs people (R.J., 13 Nov, #general).

R.J., back after time away: "is there a place for people to share their intentions for the day / week / what
they're working on?"; "if someone jumps on, [they've been away], maybe there's a color pie chart or something that
shows quickly at-a-glance, projects, who's working on what, and what's needed the 'most'"; and "project X,
@SkymanJenkins @JMyles, priority 3 (priority ranges 1 - 5)". Justin: "a super-compact list of everybody's daily
intentions", "living in the right-hand pane of #general, maybe instead of 'todo'".

Nobody keeps this by hand, or it would be out of date by the second day. Three of its four parts are read from
what's already kept:

* **projects**: a project is a Mood, its threads counted with it. Who said how much there in the window (a day,
  7,200 blocks, or a week), most talked-of first; how many candidate branches the ramp has open for it.
* **needs**: what waits on a person. The emergents' open asks (a merge, a redeploy, a turn with tools:
  services/tools_ask.py) first, since someone is waiting on each right now; then the open to-do items of every
  Mood (services/todo.py), an item with a `priority` (1 the most needed, to 5) before one without.
* **people**: everyone who spoke in the window, and where most.

The fourth is the one thing people write: **intentions**, a line or a few from each person on what they mean to
do, kept as a person's setting (services/settings.py, 'intention': a new row each time, so its history stays)
and shown while it's younger than the window.
"""

from datetime import timedelta

from django.core.cache import cache

BLOCK_SECONDS = 12
WINDOWS = {'day': 7200, 'week': 50400}   # in blocks
KEEP = 45            # seconds one window's answer is kept: every open pane asks each minute
NEEDS_TOP = 8        # needs sent, of them all; and
NEEDS_EACH = 6       # of each person's own (and of nobody's: anyone's to take), for the page's "who" buttons
NOT_SPEAKERS = ('system', 'tool-result', 'mood-poller', 'harness')
INTENTION = 'intention'


def person_key(name):
    """A name on a to-do list or a candidate branch, as the person is called here: "jMyles" and "JMyles" are
    justin (work.FORGE_PEOPLE, then hunter's inventory of PickiPedia names); anyone else, their name in lower case."""
    from .wiki_auth import names
    from .work import FORGE_PEOPLE
    name = str(name or '').strip().lstrip('@').lower()
    if name in FORGE_PEOPLE:
        return FORGE_PEOPLE[name]
    for here, wiki in names().items():
        if wiki.lower() == name:
            return here
    return name


def _roots():
    """{mood id: the Mood its talk counts under}: itself, or the Mood it's a thread of, however deep; and
    {id: (slug, title)} for every Mood that isn't archived."""
    from conversations.models import Mood
    from . import settings as knobs
    archived = knobs.archived_slugs()
    rows = list(Mood.objects.values_list('id', 'slug', 'title', 'thread_of_id'))
    parent = {i: p for i, _, _, p in rows}
    named = {i: (slug, title or slug) for i, slug, title, _ in rows if slug not in archived}
    roots = {}
    for i in parent:
        seen, at = set(), i
        while parent.get(at) and at not in seen:
            seen.add(at)
            at = parent[at]
        roots[i] = at
    return roots, named


def said(since):
    """[(mood id, sender, how many, newest)]: what each person and emergent said in each Mood since then. A
    message is something someone sent: not a tool call, a thought, the poller's own, or an emergent's silence."""
    from django.db.models import Count, Max, TextField
    from django.db.models.functions import Cast
    from conversations.models import Message
    rows = (Message.objects.filter(mood__isnull=False, is_sidechain=False, created_at__gt=since,
                                   tooluse__isnull=True, thought__isnull=True, toolresult__isnull=True)
            .exclude(sender_id__in=NOT_SPEAKERS)
            .annotate(t=Cast('content', TextField())).exclude(t__contains='<silent>')
            .values('mood_id', 'sender_id').annotate(n=Count('id'), last=Max('created_at')))
    return [(r['mood_id'], r['sender_id'], r['n'], r['last']) for r in rows]


def _candidates():
    """{slug: how many candidate branches are open for it}, from the ramp's last board. Never builds one (seconds,
    on maybelle): no board yet, no counts."""
    from . import ramp
    counts = {}
    for item in (cache.get(ramp.KEY) or {}).get('open', []):
        if item.get('state') == 'merged':
            continue
        for slug in item.get('moods') or []:
            slug = slug.lstrip('#')
            counts[slug] = counts.get(slug, 0) + 1
    return counts


def needs(todos=None, asks=None):
    """What waits on a person, most needed first: [{'mood', 'mood_title', 'task', 'headline', 'kind', 'type',
    'who', 'link', 'said_url', 'priority', 'ask'}], each in its place by todo_types.rank (its priority, then its type). `ask`: an emergent's
    open ask (its 'who' is empty: whoever can do it), which comes first.
    Not an emergent's own job (the to-do's type 'work'), nor an item that names emergents only."""
    from conversations.models import Mood, ThinkingEntity
    from . import settings as knobs
    from .todo_types import HEADLINE_MAX
    if todos is None:
        from . import todo
        todos = todo.all_todos()
    if asks is None:
        from .tools_ask import open_requests
        asks = open_requests()
    archived = knobs.archived_slugs()
    titles = {slug: title or slug for slug, title in Mood.objects.values_list('slug', 'title')}
    emergents = set(ThinkingEntity.objects.filter(is_biological_human=False).values_list('name', flat=True))
    found = []
    for slug, requests_ in (asks or {}).items():
        if slug in archived:
            continue
        for r in requests_:
            what = {'tools': 'a turn with its tools', 'merge': 'a merge', 'deploy': 'a redeploy'}.get(r.get('kind'), 'help')
            found.append({'mood': slug, 'mood_title': titles.get(slug, slug),
                          'task': f"{r.get('emergent', '')} asks for {what}: {r.get('for') or ''}".rstrip(': '),
                          'headline': f"{r.get('emergent', '')}: {r.get('for') or what}"[:HEADLINE_MAX],
                          'type': r.get('kind') or '',   # merge and deploy are to-do types too; tools is an ask's own
                          'kind': r.get('kind') or '', 'who': [], 'priority': None, 'ask': True,
                          'link': f"/moods/{slug}/#m-{r['id']}" if r.get('id') else f'/moods/{slug}/',
                          'said_url': '',   # an ask's own link is already its message: no second way there
                          '_order': (0, 0, 0)})
    from .todo_types import headline, rank, said_url, type_of
    for n, item in enumerate(todos.get('items') or []):
        if item.get('done') or item.get('merged') or item.get('archived'):
            continue
        who = list(dict.fromkeys(person_key(w) for w in item.get('who') or []))
        if item.get('type') == 'work' or (who and all(w in emergents for w in who)):
            continue
        found.append({'mood': item.get('mood') or '', 'mood_title': item.get('mood_title') or item.get('mood') or '',
                      'task': item.get('task') or '', 'kind': item.get('kind') or item.get('type') or '',
                      'type': item.get('type') or type_of(item), 'headline': item.get('headline') or headline(item),
                      'who': who,
                      'link': item.get('link') or '', 'priority': item.get('priority'), 'ask': False,
                      'said_url': item.get('said_url') or said_url(item.get('said')),
                      '_order': (*rank(item), n)})   # n: all_todos has them newest first
    found.sort(key=lambda i: i['_order'])
    for item in found:
        del item['_order']
    return found


def intentions(since):
    """[{'name', 'text', 'at'}], newest first: what each person last said they mean to do, if they said it since
    then and haven't cleared it."""
    from conversations.models import Setting
    newest = {}
    for row in (Setting.objects.filter(key=INTENTION, mood=None, emergent__isnull=False, created_at__gt=since)
                .order_by('created_at')):
        newest[row.emergent_id] = row
    return [{'name': name, 'text': row.value, 'at': row.created_at.isoformat()}
            for name, row in sorted(newest.items(), key=lambda nr: nr[1].created_at, reverse=True) if row.value]


def glance(window='day', now=None, todos=None, asks=None):
    """{'window', 'blocks', 'projects', 'people', 'needs', 'needs_total', 'needs_by', 'needs_for', 'intentions'}:
    see this module's top. `needs` is the most needed few of them all; `needs_by`, how many wait on each person
    ('' is nobody named: anyone's to take), most first; `needs_for`, each of those people's own most needed few.
    Kept KEEP seconds for each window (not when `now` or the lists are given: a test's)."""
    from django.utils import timezone
    from conversations.models import ThinkingEntity
    window = window if window in WINDOWS else 'day'
    key = f'glance:{window}'
    kept = now is None and todos is None and asks is None
    if kept:
        found = cache.get(key)
        if found is not None:
            return found
    now = now or timezone.now()
    since = now - timedelta(seconds=WINDOWS[window] * BLOCK_SECONDS)
    human = dict(ThinkingEntity.objects.values_list('name', 'is_biological_human'))
    roots, named = _roots()

    projects, people = {}, {}
    for mood_id, sender, n, last in said(since):
        root = roots.get(mood_id)
        if sender not in human or root not in named or mood_id not in named:
            continue  # not a person or an emergent; or an archived Mood, or a thread of one
        slug, title = named[root]
        p = projects.setdefault(slug, {'slug': slug, 'title': title, 'total': 0, 'said': {}, 'last_at': None,
                                       'last_by': None, 'threads': set()})
        p['total'] += n
        p['said'][sender] = p['said'].get(sender, 0) + n
        if mood_id != root:
            p['threads'].add(mood_id)
        if p['last_at'] is None or last > p['last_at']:
            p['last_at'], p['last_by'] = last, sender
        who = people.setdefault(sender, {'name': sender, 'human': bool(human[sender]), 'n': 0, 'moods': {}})
        who['n'] += n
        who['moods'][slug] = who['moods'].get(slug, 0) + n

    waiting = needs(todos=todos, asks=asks)
    open_for, waits_on, needs_by, needs_for = {}, {}, {}, {}
    for item in waiting:
        open_for[item['mood']] = open_for.get(item['mood'], 0) + 1
        named = [n for n in item['who'] if human.get(n) is not False]   # an emergent named beside a person isn't waited on
        for name in named:
            by = waits_on.setdefault(item['mood'], {})
            by[name] = by.get(name, 0) + 1
        for name in named or ['']:
            needs_by[name] = needs_by.get(name, 0) + 1
            if len(needs_for.setdefault(name, [])) < NEEDS_EACH:
                needs_for[name].append(item)
    candidates = _candidates()

    by_n = lambda counts: [{'name': k, 'n': v} for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
    found = {
        'window': window, 'blocks': WINDOWS[window], 'since': since.isoformat(),
        'projects': [{'slug': p['slug'], 'title': p['title'], 'total': p['total'], 'said': by_n(p['said']),
                      'last_at': p['last_at'].isoformat(), 'last_by': p['last_by'], 'threads': len(p['threads']),
                      'candidates': candidates.get(p['slug'], 0), 'needs': open_for.get(p['slug'], 0),
                      'waits_on': by_n(waits_on.get(p['slug'], {}))}
                     for p in sorted(projects.values(), key=lambda p: (-p['total'], p['slug']))],
        'people': [{'name': w['name'], 'human': w['human'], 'n': w['n'],
                    'moods': [{'slug': m['name'], 'n': m['n']} for m in by_n(w['moods'])[:3]]}
                   for w in sorted(people.values(), key=lambda w: (-w['n'], w['name']))],
        'needs': waiting[:NEEDS_TOP], 'needs_total': len(waiting), 'needs_by': by_n(needs_by), 'needs_for': needs_for,
        'intentions': intentions(since),
    }
    if kept:
        cache.set(key, found, KEEP)
    return found


def forget():
    """An intention just changed: every window's answer is asked afresh."""
    cache.delete_many([f'glance:{w}' for w in WINDOWS])


def intend(person, text):
    """Record what `person` (a ThinkingEntity) means to do: a new row, the last one left as history. '' clears it."""
    from . import settings as knobs
    row = knobs.change(INTENTION, text, agent=person, by=person)
    forget()
    return row
