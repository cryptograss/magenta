"""/announce: what a person says in one Mood, told to the emergents in every other (Justin, 27 Oct: "I don't want it
to _show_ in those other Moods, just to be added to the context of the emergents in each").

"/announce Beta is tonight at 8" is posted where it's typed, as "📣 Beta is tonight at 8", for everyone there to
see. Every other open Mood gets a quiet row (source 'announce', a system row, never drawn in its thread), and
each emergent's next wake there, whatever wakes it, carries it once (the poller's announced_lines). Nobody is
woken by it: it waits for the next wake.
"""

import re
import time
import uuid

SOURCE = 'announce'
_COMMAND = re.compile(r'^/announce(?:\s+|$)', re.I)
MARK = '📣 '
KEEP_DAYS = 7  # an announcement older than this isn't news to an emergent waking now


class Refused(Exception):
    pass


def parse(text):
    """The words of an announcement, if `text` is one ('/announce ...'); None otherwise."""
    if not _COMMAND.match(text or ''):
        return None
    words = _COMMAND.sub('', text, count=1).strip()
    if not words:
        raise Refused('/announce what? Write the announcement after it.')
    return words


def spread(message, by, words):
    """A quiet row in every other open Mood, pointing at `message`. How many Moods it reached."""
    from conversations.models import ConversationParticipant, Message, Mood
    from . import settings as knobs
    system, _ = ConversationParticipant.objects.get_or_create(name='system', defaults={'participant_type': 'system'})
    shut = set(knobs.archived_slugs())
    reached = 0
    for mood in Mood.objects.exclude(pk=message.mood_id):
        if mood.slug in shut:
            continue
        Message.objects.create(id=uuid.uuid4(), sender=system, mood=mood, source_file=SOURCE,
                               timestamp=int(time.time() * 1000),
                               content={'type': 'announce', 'by': by, 'from_mood': message.mood_slug,
                                        'message': str(message.id), 'text': words})
        reached += 1
    return reached


def in_mood(mood, limit=20):
    """The announcements told to this Mood lately, oldest first: what the runner reads with /turns/."""
    from datetime import timedelta
    from django.utils import timezone
    from conversations.models import Message
    rows = (Message.objects.filter(mood=mood, source_file=SOURCE, created_at__gt=timezone.now() - timedelta(days=KEEP_DAYS))
            .order_by('-created_at').values('id', 'created_at', 'content')[:limit])
    return [{'id': str(r['id']), 'created_at': r['created_at'].isoformat(), **{k: (r['content'] or {}).get(k)
             for k in ('by', 'from_mood', 'message', 'text')}} for r in reversed(rows)]
