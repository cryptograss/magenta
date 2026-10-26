"""Pinned messages: a few things a Mood keeps at its top (Justin, 26 Oct: "Pinned messages in moods will be
useful too. Keep it low-profile, at the top.").

Anyone signed in with an SSH key may pin a message in a Mood, or unpin it: a system row (source 'pin': which
message, by whom), kept out of the thread as reactions are, and taken away to unpin. Each look at the Mood
brings its pins, newest first, each with whose message it is and its first words, so the line at the top can
show one that isn't on the page. A deleted message's pin goes with it.
"""

import time
import uuid

SOURCE = 'pin'
MAX_PINS = 20  # a Mood's top is for a few things


class Refused(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def toggle(message, by):
    """Pin the message in its Mood, or unpin it. True if it's pinned now."""
    from conversations.models import ConversationParticipant, Message
    mine = Message.objects.filter(source_file=SOURCE, mood=message.mood, content__message=str(message.id))
    if mine.exists():
        mine.delete()
        return False
    if Message.objects.filter(source_file=SOURCE, mood=message.mood).count() >= MAX_PINS:
        raise Refused(f'at most {MAX_PINS} pinned here: unpin one first')
    system, _ = ConversationParticipant.objects.get_or_create(name='system', defaults={'participant_type': 'system'})
    Message.objects.create(id=uuid.uuid4(), sender=system, mood=message.mood, source_file=SOURCE,
                           timestamp=int(time.time() * 1000),
                           content={'type': 'pin', 'message': str(message.id), 'by': by})
    return True


def in_mood(mood):
    """[{'id', 'sender', 'snippet', 'by', 'at'}], newest pin first: each pinned message as it stands now."""
    from conversations.models import Message, MessageChange
    from .mood_view import prose, snippet_of
    rows = list(Message.objects.filter(source_file=SOURCE, mood=mood).order_by('-created_at')
                .values_list('content', 'created_at'))
    ids = [c.get('message') for c, _ in rows if isinstance(c, dict)]
    if not ids:
        return []
    messages = {str(m.id): m for m in Message.objects.filter(id__in=ids, mood=mood)}
    gone = {str(i) for i in MessageChange.objects.filter(message_id__in=ids, kind='deleted').values_list('message_id', flat=True)}
    out = []
    for content, at in rows:
        message = messages.get(content.get('message'))
        if message is None or str(message.id) in gone:
            continue
        out.append({'id': str(message.id), 'sender': message.sender_id,
                    'snippet': snippet_of(prose(message.content) or '')[:160],
                    'by': content.get('by'), 'at': at.isoformat()})
    return out
