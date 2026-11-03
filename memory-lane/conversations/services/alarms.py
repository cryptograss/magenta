"""Jenkins alarms, said in #ops: a check turned red, or green again.

maybelle's Jenkins jobs (maybelle-config's report-alarm.sh) post to
/api/alarms/ with their own key from the vault (memory_lane_alarm_key),
only when a stage changes: red when it starts failing, green when it
recovers, never once per run. Each is a line in #ops from a system row
(source 'alarm'), like a redeploy's. A system row is machinery
(mood_view.MACHINERY_SENDERS), so an alarm wakes no emergent for a
consider turn: it is read at the next wake, and a usage limit can't
turn alarms into a loop. Before this (2 Nov 2026), a red stage reached
nobody: the podcast import sat red for 19 days, and maybelle's disk
filled without a word.
"""

import time
import uuid

SOURCE = 'alarm'
ROOM = 'ops'
STATES = ('red', 'green')


def record(job, stage, state, url='', note=''):
    """A line in #ops; None when there's no #ops."""
    from conversations.models import ConversationParticipant, Message, Mood
    mood = Mood.by_slug(ROOM)
    if mood is None:
        return None
    system, _ = ConversationParticipant.objects.get_or_create(name='system', defaults={'participant_type': 'system'})
    content = {'type': 'alarm', 'job': job[:80], 'stage': stage[:120], 'state': state,
               'url': url[:300], 'note': note[:300]}
    return Message.objects.create(id=uuid.uuid4(), sender=system, mood=mood, content=content,
                                  timestamp=int(time.time() * 1000), source_file=SOURCE)


def line(c):
    """What an alarm line says (mood_view.event_line)."""
    what = f"{c.get('job', 'a job')} · {c.get('stage', 'a stage')}"
    note = f": {c['note']}" if c.get('note') else ''
    if c.get('state') == 'green':
        return f"✓ {what} is green again{note}"
    return f"🚨 {what} went red{note}"
