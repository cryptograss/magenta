"""Which emergents asked for what where (Justin, 28 Oct: a hammer by the Mood's title "so that we know you're
waiting for that"): a look-only turn's reply of only <tools to="…"/> (also <merge to="…"/>, <deploy to="…"/>,
the same ask, other things -- Justin, 29 Oct) is a line in the Mood and an icon by its name until someone does
it. Each ask stays open until its event lands in that Mood: a full-tools wake answers the tools ask, a merged
event the merge ask, a deploy event the deploy ask.
"""

from datetime import timedelta

from django.core.cache import cache

KEY = 'asks-asked'
KEEP = 15            # seconds: the Moods list asks often
LOOK_BACK = timedelta(days=7)
FULL = 'This turn has your full tools'

# kind -> (the tag the reply starts with, its emoji, the event source that answers it, how its reason reads)
KINDS = {
    'tools': {'tag': '<tools', 'emoji': '🔨', 'source': None},
    'merge': {'tag': '<merge', 'emoji': '🔀', 'source': 'merged'},
    'deploy': {'tag': '<deploy', 'emoji': '🔄', 'source': 'deploy'},
}
# Each kind's reason, from quiet_reason (mood_view.py), is "requested … for/of …": what comes after those words.
_PREFIX = {'tools': 'requested tools for ', 'merge': 'requested a merge for ', 'deploy': 'requested a redeploy of '}


def open_requests():
    """{slug: [{'kind', 'agent', 'for', 'at', 'id'}]}: asks not yet answered by their event there."""
    kept = cache.get(KEY)
    if kept is not None:
        return kept
    from django.db.models import Q, TextField
    from django.db.models.functions import Cast
    from django.utils import timezone
    from conversations.models import BundleRecord, Message, ThinkingEntity
    from .mood_view import BUNDLE_SAID, prose, quiet_reason
    agents = set(ThinkingEntity.objects.filter(is_biological_human=False).values_list('name', flat=True))
    since = timezone.now() - LOOK_BACK
    want = Q()
    for kind in KINDS:
        want |= Q(t__contains=KINDS[kind]['tag'])
    latest = {}
    for m in (Message.objects.filter(created_at__gt=since, mood__isnull=False, sender_id__in=agents)
              .annotate(t=Cast('content', TextField())).filter(want)
              .select_related('mood').order_by('created_at')):
        said = quiet_reason((prose(m.content) or '').strip())
        kind = (said or {}).get('type')
        if kind in KINDS:
            latest[(m.mood.slug, m.sender_id, kind)] = (m, said['reason'][len(_PREFIX[kind]):])
    found = {}
    for (slug, agent, kind), (m, purpose) in latest.items():
        if not _answered(kind, slug, agent, m):
            found.setdefault(slug, []).append({'kind': kind, 'agent': agent, 'for': purpose,
                                               'at': m.created_at.isoformat(), 'id': str(m.id)})
    cache.set(KEY, found, KEEP)
    return found


def _answered(kind, slug, agent, request):
    """Whether an ask's event has landed: a full-tools wake of the asker there; a merged or deploy event.
    The tools ask is answered the moment the turn starts, not when it ends: its wake names the bundle the runner
    made for it (BundleRecord), which the record has before the agent has said a word (Justin, 29 Oct)."""
    from django.db.models import TextField
    from django.db.models.functions import Cast
    from conversations.models import BundleRecord, Message
    from .mood_view import BUNDLE_SAID
    source = KINDS[kind]['source']
    if source:
        return Message.objects.filter(mood=request.mood, source_file=source, created_at__gt=request.created_at)\
            .exists()
    wakes = (Message.objects.filter(mood=request.mood, sender_id='mood-poller', created_at__gt=request.created_at)
             .annotate(t=Cast('content', TextField())).filter(t__contains=FULL))
    bundles = {str(b) for b in BundleRecord.objects.filter(agent=agent, mood=slug, requested_at__gt=request.created_at)
               .values_list('id', flat=True)}
    if bundles and any(bundles & set(BUNDLE_SAID.findall(t)) for t in wakes.values_list('t', flat=True)):
        return True
    # An older runner, no bundle: a wake in a session the agent has spoken in since the ask.
    sessions = set(Message.objects.filter(mood=request.mood, sender_id=agent).values_list('session_id', flat=True))
    return wakes.filter(session_id__in=sessions).exists()