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

A check that stays red wakes someone, by a task (services/tasks.py): once
an alarm has been red for STILL_RED with no word since -- longer than
cron-health's five-minute round, so a check that recovers on its next run
(arthel's rsync, 5 Nov 18:30Z) wakes nobody -- run_due_tasks starts
WAKES's task in #ops with the alarm's job, stage, build and note as its
blanks. Jenkins never touches the Task API: it holds the alarm key, which
does nothing else. Each red wakes once; a check that goes green and red
again is a new red. The task page sets the rest (its tools, its cap), and
runs only once a trusted editor has saved it.
"""

import time
import uuid
from datetime import datetime, timedelta, timezone

SOURCE = 'alarm'
ROOM = 'ops'
STATES = ('red', 'green')
STILL_RED = timedelta(minutes=8)   # more than one cron-health round: a red the next run didn't clear
LOOK_BACK = timedelta(hours=6)     # a red older than this, never woken (the page unsaved), is let go
WAKES = ('magent', 'Jenkins red')  # (emergent, task): Cryptograss:Emergents/Magent/Tasks/Jenkins red


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


def still_red(now=None, get=None):
    """Wake WAKES's task for each alarm that has stayed red for STILL_RED. Returns [(stage, what)]:
    the task's line, or why it didn't start. Run every minute (run_due_tasks)."""
    from conversations.models import Message
    from conversations.services import tasks
    now = now or datetime.now(timezone.utc)
    rows = list(Message.objects.filter(source_file=SOURCE, created_at__gte=now - LOOK_BACK).order_by('created_at'))
    newest = {}
    for row in rows:  # the latest word on each check
        newest[(row.content.get('job'), row.content.get('stage'))] = row
    done = []
    for (job, stage), row in newest.items():
        c = row.content
        if c.get('state') != 'red' or c.get('woke') or now - row.created_at < STILL_RED:
            continue
        try:
            task = tasks.read(*WAKES, get=get)
        except tasks.Refused as e:  # no trusted page yet: say nothing, try again next minute
            done.append((stage, str(e)))
            continue
        except Exception as e:  # PickiPedia down: the same
            done.append((stage, f"couldn't read {WAKES[1]}: {e}"))
            continue
        offered = {'job': job or '', 'stage': stage or '', 'url': c.get('url') or '', 'note': c.get('note') or '',
                   'since': row.created_at.strftime('%Y-%m-%d %H:%MZ')}
        params = {k: v for k, v in offered.items() if k in task.blanks}  # the page uses what it wants
        line = tasks.start(task, 'jenkins', params=params, mood=ROOM, now=now)
        c['woke'] = str(line.id)
        row.content = c
        row.save(update_fields=['content'])
        done.append((stage, line))
    return done
