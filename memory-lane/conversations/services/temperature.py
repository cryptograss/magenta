"""How warm each Mood is (Justin, 31 Oct): in place of the count of everything it ever said, how many messages its
people and its emergents sent in the past day -- warmer the more of them came in the past 3 hours -- and how long
since each last spoke, lit while that's under half an hour.

One grouped query for every Mood (the Moods list was slow, 29 Oct). A message is a row someone sent: not a tool
call, not a thought, not the system's or the poller's own.
"""

from datetime import timedelta

DAY = timedelta(hours=24)
WARM = timedelta(hours=3)
LOOK_BACK = timedelta(days=30)   # "last spoke" further back than this: not shown
NOT_SPEAKERS = ('system', 'tool-result', 'mood-poller', 'harness')


def temperatures(now=None):
    """{mood_id: {'humans': {'day', 'warm', 'last_at', 'last_by'}, 'emergents': {...}}}. `last_by`: who spoke at
    `last_at`, so the list can write that time in their colour (Justin, 1 Nov)."""
    from django.db.models import Count, Max, Q
    from django.utils import timezone

    from conversations.models import Message, ThinkingEntity

    now = now or timezone.now()
    kinds = {name: 'humans' if human else 'emergents'
             for name, human in ThinkingEntity.objects.values_list('name', 'is_biological_human')}
    rows = (Message.objects.filter(mood__isnull=False, is_sidechain=False, created_at__gt=now - LOOK_BACK,
                                   tooluse__isnull=True, thought__isnull=True)
            .exclude(sender_id__in=NOT_SPEAKERS)
            .values('mood_id', 'sender_id')
            .annotate(day=Count('id', filter=Q(created_at__gt=now - DAY)),
                      warm=Count('id', filter=Q(created_at__gt=now - WARM)),
                      last=Max('created_at')))
    found = {}
    for r in rows:
        kind = kinds.get(r['sender_id'])
        if kind is None:
            continue  # a participant that isn't a person or an emergent
        t = found.setdefault(r['mood_id'], {}).setdefault(kind, {'day': 0, 'warm': 0, 'last_at': None, 'last_by': None})
        t['day'] += r['day']
        t['warm'] += r['warm']
        last = r['last'].isoformat() if r['last'] else None
        if last and (t['last_at'] is None or last > t['last_at']):
            t['last_at'] = last
            t['last_by'] = r['sender_id']
    return found
