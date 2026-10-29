"""An agent's instructions bundle degraded, said once in the Mood -- for the people there.

The poller gives the agent the degraded block in its system prompt (magenta#107); this is the line for
everyone else: what failed, since when, and for which agent. One row per change: a Mood gets a line when an
agent's bundle first degrades, another only when what's wrong changes, and one more when it's whole again.
The runner says it on every wake (poller instructions_for); here it's compared with the newest line for that
agent in that Mood, and nothing is written when nothing changed.
"""
import uuid

SOURCE = 'instructions'


def say_degraded(slug, agent, lines):
    """Record what's wrong with `agent`'s instructions in Mood `slug` (`lines`: the bundle's degraded lines; []
    when all is well). Returns the row made, or None when it says what the newest row already says."""
    from conversations.models import ConversationParticipant, Message, Mood
    mood = Mood.by_slug(slug)
    if mood is None:
        return None
    lines = [str(l)[:300] for l in lines][:12]
    newest = (Message.objects.filter(mood=mood, source_file=SOURCE, content__type='degraded', content__agent=agent)
              .order_by('-created_at').first())
    said = (newest.content.get('lines') or []) if newest else []
    if said == lines:
        return None
    system, _ = ConversationParticipant.objects.get_or_create(name='system', defaults={'participant_type': 'system'})
    import time
    return Message.objects.create(id=uuid.uuid4(), sender=system, mood=mood, source_file=SOURCE,
                                  timestamp=int(time.time() * 1000),
                                  content={'type': 'degraded', 'agent': agent, 'lines': lines})
