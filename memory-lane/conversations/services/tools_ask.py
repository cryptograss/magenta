"""Which emergents asked for their full tools where (Justin, 28 Oct: a hammer by the Mood's title "so that we know
you're waiting for that"): a look-only turn's reply of only <tools to="…"/> (mood_view._TOOLS), still open until a
full-tools wake of that emergent in that Mood (its wake says "This turn has your full tools").
"""

from datetime import timedelta

from django.core.cache import cache

KEY = 'tools-asked'
KEEP = 15            # seconds: the Moods list asks often
LOOK_BACK = timedelta(days=7)
FULL = 'This turn has your full tools'


def open_requests():
    """{slug: [{'agent', 'for', 'at'}]}: requests not yet answered by a full-tools wake there."""
    kept = cache.get(KEY)
    if kept is not None:
        return kept
    from django.db.models import TextField
    from django.db.models.functions import Cast
    from django.utils import timezone
    from conversations.models import BundleRecord, Message, ThinkingEntity
    from .mood_view import BUNDLE_SAID, prose, quiet_reason
    agents = set(ThinkingEntity.objects.filter(is_biological_human=False).values_list('name', flat=True))
    since = timezone.now() - LOOK_BACK
    latest = {}
    for m in (Message.objects.filter(created_at__gt=since, mood__isnull=False, sender_id__in=agents)
              .annotate(t=Cast('content', TextField())).filter(t__contains='<tools')
              .select_related('mood').order_by('created_at')):
        said = quiet_reason((prose(m.content) or '').strip())
        if said and said.get('type') == 'tools':
            latest[(m.mood.slug, m.sender_id)] = (m, said['reason'][len('requested tools for '):])
    found = {}
    for (slug, agent), (m, purpose) in latest.items():
        wakes = (Message.objects.filter(mood=m.mood, sender_id='mood-poller', created_at__gt=m.created_at)
                 .annotate(t=Cast('content', TextField())).filter(t__contains=FULL))
        # Its wake names the bundle the runner made for it (BundleRecord): known the moment the wake is, before the
        # agent has said a word -- so the 🔨 goes as its turn starts, not when it ends (Justin, 29 Oct).
        bundles = {str(b) for b in BundleRecord.objects.filter(agent=agent, mood=slug, requested_at__gt=m.created_at)
                   .values_list('id', flat=True)}
        answered = bundles and any(bundles & set(BUNDLE_SAID.findall(t)) for t in wakes.values_list('t', flat=True))
        if not answered:  # an older runner, no bundle: a wake in a session the agent has spoken in
            sessions = set(Message.objects.filter(mood=m.mood, sender_id=agent).values_list('session_id', flat=True))
            answered = wakes.filter(session_id__in=sessions).exists()
        if not answered:
            found.setdefault(slug, []).append({'agent': agent, 'for': purpose, 'at': m.created_at.isoformat()})
    cache.set(KEY, found, KEEP)
    return found
