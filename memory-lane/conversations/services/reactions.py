"""Emoji reactions to what was said in a Mood: a 👍 instead of a line.

Anyone signed in may react to any message -- a person's, an agent's -- with
any emoji, and take it back by pressing it again. Each reaction is a system
row in the Mood (source 'reaction': who, which message, which emoji), kept
out of the thread like the voice's records; the page asks for a Mood's
reactions with every look it takes (/turns/), so everyone sees them as they
change. Nobody is notified of one: a reaction is a nod, not a message.

An agent reacts by replying with only <react emoji="🎻" to="re"/> (any emoji; `to` a
name, for that one's newest post in the Mood, or a message's id): where it has
nothing to add to what another said, a nod on that post instead of a line
(Justin, magent-self, 2026-10-25: "maybe an emoji response?"). Its reply stays
in the record as a small dot saying so.
"""

import re
import time
import uuid

AGENT_REACT = re.compile(r'^\s*<react\s+((?:\w+="[^"]{0,80}"\s*){2})/>\s*$')
MESSAGE_ID = re.compile(r'^#?m?-?([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}|[0-9a-f]{8,})$')

SOURCE = 'reaction'
PALETTE = ['👍', '❤️', '😂', '🎉', '🙏', '👀', '🔥', '💯', '🪕', '🎻']
MAX_EMOJI = 16  # characters: an emoji with its modifiers and joiners, never words


KEYCAP = re.compile('^[0-9#*]\uFE0F?\u20E3$')  # #️⃣ 1️⃣ *️⃣: an ASCII character, made a key


def valid(emoji):
    """An emoji, not words or markup: non-ASCII throughout (but for a keycap), short."""
    return bool(emoji) and len(emoji) <= MAX_EMOJI and not emoji.isspace() and (
        all(ord(c) > 127 for c in emoji) or bool(KEYCAP.match(emoji)))


def toggle(message, by, emoji):
    """React to a message, or take that reaction back. True if it's on now."""
    from conversations.models import ConversationParticipant, Message
    mine = Message.objects.filter(source_file=SOURCE, mood=message.mood, content__message=str(message.id),
                                  content__by=by, content__emoji=emoji)
    if mine.exists():
        mine.delete()
        return False
    system, _ = ConversationParticipant.objects.get_or_create(name='system', defaults={'participant_type': 'system'})
    Message.objects.create(id=uuid.uuid4(), sender=system, mood=message.mood, source_file=SOURCE,
                           timestamp=int(time.time() * 1000),
                           content={'type': 'reaction', 'message': str(message.id), 'by': by, 'emoji': emoji})
    return True


def react_on(message, by, emoji):
    """React, never taking one back: an agent's reply is said once, but read again with each post."""
    from conversations.models import Message
    if not Message.objects.filter(source_file=SOURCE, mood=message.mood, content__message=str(message.id),
                                  content__by=by, content__emoji=emoji).exists():
        toggle(message, by, emoji)


def agent_reaction(text):
    """(emoji, to) for a reply that is only <react emoji="..." to="..."/>, else None."""
    match = AGENT_REACT.match(text) if isinstance(text, str) else None
    attrs = dict(re.findall(r'(\w+)="([^"]*)"', match.group(1))) if match else {}
    emoji, to = attrs.get('emoji', '').strip(), attrs.get('to', '').strip().lower()
    return (emoji, to) if valid(emoji) and to else None


def target(mood, to, before, by):
    """The message `to` names in the Mood: its id (or the start of one), or the newest post by that name
    before `before`, not a dot, not machinery. By name, never the reactor's own; by id, its own too: a ⭐ on
    its own post is how an agent marks what feels like itself (services/recall.how_i_speak; Justin, 27 Oct)."""
    from conversations.models import Message
    any_sender = Message.objects.filter(mood=mood, is_sidechain=False, created_at__lte=before)
    found = any_sender.exclude(sender_id=by)
    named = MESSAGE_ID.match(to)
    if named:
        hit = any_sender.filter(id__startswith=named.group(1)).first() if len(named.group(1)) < 36 else \
            any_sender.filter(id=named.group(1)).first()
        if hit:
            return hit
    from .mood_view import prose, quiet_reason
    for msg in found.filter(sender_id=to).order_by('-created_at')[:50]:
        text = prose(msg.content)
        if text and not quiet_reason(text.strip()):
            return msg
    return None


def agent_reacted(session_id, agent):
    """An agent's replies in this session that are only <react .../>: each becomes a reaction on the post it
    names, and the reply a small dot saying so ('reacted 🎻 to re'); one naming nothing that can be found
    becomes a dot saying that. Read with each post the runner sends, so a reply is turned once."""
    from conversations.models import Message

    from .mood_view import prose
    said = Message.objects.filter(session_id=session_id, sender_id=agent, mood__isnull=False,
                                  content__icontains='<react')
    for message in said:
        content = message.content
        block = isinstance(content, list) and len(content) == 1 and isinstance(content[0], dict) \
            and content[0].get('type') == 'text'
        found = agent_reaction(content[0].get('text') if block else prose(content))
        if not found:
            continue
        emoji, to = found
        hit = target(message.mood, to, message.created_at, agent)
        if hit:
            react_on(hit, agent, emoji)
        why = f'reacted {emoji} to {hit.sender_id}' if hit else f'meant to react {emoji} to {to}, found nothing there'
        dot = f'<silent>{why}</silent>'
        message.content = [{**content[0], 'text': dot}] if block else dot
        message.save(update_fields=['content'])


def in_mood(mood, message_ids=None):
    """{message id: {emoji: [who, ...]}}, emoji in the order first used, people in the order they reacted."""
    from conversations.models import Message
    rows = Message.objects.filter(source_file=SOURCE, mood=mood)
    if message_ids is not None:
        rows = rows.filter(content__message__in=[str(i) for i in message_ids])
    out = {}
    for c in rows.order_by('created_at').values_list('content', flat=True):
        if isinstance(c, dict) and c.get('message') and c.get('emoji'):
            people = out.setdefault(c['message'], {}).setdefault(c['emoji'], [])
            if c.get('by') not in people:
                people.append(c.get('by'))
    return out
