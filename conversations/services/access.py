"""Comings and goings, said in #general: every new sign-in, and every admin action.

A line in the thread, like a redeploy's ('🔑 skyler signed in with their SSH
key · "laptop"'), from a system row (source 'access'). The room sees who has
come in and how -- a sign-in nobody expected, on someone's account, is just
what it should see -- and when an admin has acted. Ordinary sign-outs aren't
announced. Announcing never stands in the way of what it announces.
"""

import logging
import time
import uuid

SOURCE = 'access'
ROOM = 'general'

logger = logging.getLogger(__name__)


def announce(kind, who, **details):
    """A line in #general; None if there's no #general, or it couldn't be written."""
    from conversations.models import ConversationParticipant, Message, Mood
    try:
        mood = Mood.by_slug(ROOM)
        if mood is None:
            return None
        system, _ = ConversationParticipant.objects.get_or_create(name='system', defaults={'participant_type': 'system'})
        content = {'type': 'access', 'kind': kind, 'who': who,
                   **{k: v for k, v in details.items() if v not in (None, '')}}
        return Message.objects.create(id=uuid.uuid4(), sender=system, mood=mood, content=content,
                                      timestamp=int(time.time() * 1000), source_file=SOURCE)
    except Exception as e:  # a sign-in or a kick mustn't fail for want of its announcement
        logger.warning(f'could not announce {kind} for {who}: {e}')
        return None


def signed_in(device):
    return announce('signed-in', device.entity_id, tier=device.tier, label=device.label)


SSH_AGAIN_AFTER = 30 * 60  # seconds: the same way in again within this is the same visit


def came_in_by_ssh(who, server, into='', how=''):
    """Someone opened an SSH session on a server: a line in #general -- unless
    they came the same way within the half hour, which is the same visit (a
    tmux reattach, a second terminal, a copy of a file). None if not announced."""
    from datetime import timedelta
    from django.utils import timezone
    from conversations.models import Message
    since = timezone.now() - timedelta(seconds=SSH_AGAIN_AFTER)
    into = '' if into == who else into
    for row in Message.objects.filter(source_file=SOURCE, created_at__gt=since).values_list('content', flat=True):
        c = row if isinstance(row, dict) else {}
        if c.get('kind') == 'ssh' and c.get('who') == who and c.get('server') == server and c.get('for', '') == into:
            return None
    return announce('ssh', who, server=server, label=how, **({'for': into} if into else {}))


ARRIVALS_FOR = 30  # minutes a sign-in stays in the pulse, for a runner that was busy or restarting


def recent_arrivals(minutes=ARRIVALS_FOR):
    """Sign-ins of the last `minutes`, oldest first, each with when that person
    last said anything in a Mood before it -- so a runner can tell someone
    coming back from someone who never left (poller: greet_arrivals)."""
    from datetime import timedelta

    from django.utils import timezone

    from conversations.models import Message, mood_slug
    out = []
    since = timezone.now() - timedelta(minutes=minutes)
    for row in Message.objects.filter(source_file=SOURCE, created_at__gt=since).order_by('created_at'):
        content = row.content if isinstance(row.content, dict) else {}
        if content.get('kind') != 'signed-in' or not content.get('who'):
            continue
        last = (Message.objects.filter(sender_id=content['who'], mood__isnull=False, created_at__lt=row.created_at)
                .order_by('-created_at').values_list('created_at', flat=True).first())
        out.append({'id': str(row.id), 'who': content['who'], 'tier': content.get('tier'),
                    'label': content.get('label', ''), 'mood': mood_slug(row.mood_id), 'at': row.created_at.isoformat(),
                    'last_said': last.isoformat() if last else None})
    return out
