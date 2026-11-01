"""A Mood's own container on hunter (Justin, emergence, 1 Nov 2026).

Asked for with the Mood's 'container' setting (services/settings.py MOOD_KEYS), by someone signed in with an
SSH key. hunter's mood-containers process (maybelle-config: hunter's mood-containers role) reads what is wanted
(`wanted`), brings each up as mood-<id> from the Mood containers' template, and says how each stands (`report`,
kept in MoodContainer). A thread has none of its own: it shares the container of the Mood it came from,
threads of threads too, whose poller answers it there.
"""

import re

HOST = re.compile(r'^[a-z0-9][a-z0-9.-]{0,252}$')


def root_of(mood, parents=None):
    """The Mood whose container a Mood uses: itself, or the Mood its thread (of a thread...) came from."""
    seen = set()
    while mood.thread_of_id and mood.pk not in seen:
        seen.add(mood.pk)
        mood = parents[mood.thread_of_id] if parents is not None else mood.thread_of
    return mood


def wanted():
    """Each Mood that has asked for a container, or still has one running that it no longer wants; for hunter.

    [{"id": 17, "slug": ..., "aliases": [older slugs], "threads": [its threads' slugs, any depth],
      "wanted": true|false, "asked_by": ..., "asked_at": ...}]"""
    from conversations.models import Mood, MoodContainer, Setting
    from . import settings as knobs

    rows = Setting.objects.filter(key='container', agent=None, mood__isnull=False).select_related('set_by')
    newest = {mood_id: row for (mood_id, _, _), row in _latest_by_id(rows).items()}
    live = set(MoodContainer.objects.exclude(state='stopped').values_list('mood_id', flat=True))
    ids = {i for i, row in newest.items() if row.value} | live
    if not ids:
        return []
    moods = {m.pk: m for m in Mood.objects.all()}
    threads = {}
    for m in moods.values():
        if m.thread_of_id:
            threads.setdefault(root_of(m, moods).pk, []).append(m.slug)
    aliases = {}
    from conversations.models import MoodAlias
    for a in MoodAlias.objects.filter(mood_id__in=ids):
        aliases.setdefault(a.mood_id, []).append(a.slug)
    out = []
    for i in sorted(ids):
        mood = moods.get(i)
        if mood is None or mood.thread_of_id:
            continue  # a thread uses its Mood's
        row = newest.get(i)
        out.append({'id': i, 'slug': mood.slug, 'aliases': sorted(aliases.get(i, [])),
                    'threads': sorted(threads.get(i, [])),
                    'wanted': bool(row and row.value) and mood.slug not in knobs.archived_slugs(),
                    'asked_by': row.set_by.name if row and row.set_by_id else None,
                    'asked_at': row.created_at.isoformat() if row else None})
    return out


def _latest_by_id(rows):
    newest = {}
    for row in rows.order_by('created_at'):
        newest[(row.mood_id, row.agent_id, row.key)] = row
    return newest


class Invalid(ValueError):
    pass


def report(mood_id, body):
    """hunter says how mood-<id> stands: {"state": ..., "detail": ..., "hosts": [...], "ssh_port": n}."""
    from conversations.models import Mood, MoodContainer
    from .mood_cache import bust_moods_cache

    mood = Mood.objects.filter(pk=mood_id).first()
    if mood is None:
        raise LookupError(f'no Mood {mood_id}')
    state = str(body.get('state', ''))
    if state not in MoodContainer.STATES:
        raise Invalid(f"state: one of {', '.join(MoodContainer.STATES)}")
    hosts = body.get('hosts') or []
    if not isinstance(hosts, list) or not all(isinstance(h, str) and HOST.match(h) for h in hosts) or len(hosts) > 20:
        raise Invalid('hosts: a list of host names')
    port = body.get('ssh_port')
    if port is not None and not (isinstance(port, int) and 1024 <= port <= 65535):
        raise Invalid('ssh_port: a port number')
    MoodContainer.objects.update_or_create(mood=mood, defaults={
        'state': state, 'detail': str(body.get('detail') or '')[:300], 'hosts': hosts, 'ssh_port': port})
    bust_moods_cache()
    return mood


def for_list(moods):
    """{slug: {...}} for the Moods list: each Mood's container, its own or its root Mood's (a thread shares it),
    with whether it was asked for, so 'asked, not up yet' shows as such."""
    from conversations.models import MoodContainer
    from . import settings as knobs

    by_id = {m.pk: m for m in moods}
    asked = knobs.mood_flagged('container')
    up = {c.mood_id: c for c in MoodContainer.objects.all()}
    out = {}
    for m in moods:
        root = root_of(m, by_id)
        c = up.get(root.pk)
        if root.slug not in asked and (c is None or c.state == 'stopped'):
            continue
        out[m.slug] = {'name': f'mood-{root.pk}', 'state': c.state if c else 'asked', 'detail': c.detail if c else '',
                       'hosts': c.hosts if c else [], 'ssh_port': c.ssh_port if c else None,
                       'shared_from': root.slug if root.pk != m.pk else None,
                       'reported_at': c.reported_at.isoformat() if c else None, 'asked': root.slug in asked}
    return out



def answered_by(mood):
    """The container that answers `mood` (mood-<id>), its own or the Mood its thread came from, once it is up;
    None while there is none, so the poller that answers everywhere else still does."""
    from conversations.models import MoodContainer
    root = root_of(mood)
    if MoodContainer.objects.filter(mood_id=root.pk, state='running').exists():
        return f'mood-{root.pk}'
    return None
