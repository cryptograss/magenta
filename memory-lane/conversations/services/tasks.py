"""An emergent's tasks: work it is woken for by a schedule or by a person's call, not by a mention.

A task is a PickiPedia page, Cryptograss:Emergents/<Name>/Tasks/<Task>:

    {{Emergent task|mood=connectome|schedule=mondays 09:00|cap=2|tools=full}}
    Connect these pickers to the people they have played with: {names}.
    ...

The template holds its settings; everything else on the page, less <!--
comments -->, is the prompt, sent word for word (as instructions are,
poller/instructions.py). A {blank} is filled from the call's parameters.
Only a revision by a trusted editor counts (poller/wiki_rules.EDITORS): an
agent can edit pages, so it can propose a task but not set one going.

    mood      the Mood the agent is woken in, and where it answers
    schedule  when it runs by itself: "daily 09:00", "mondays 09:00", "weekdays
              18:30" (UTC), or "every 7200 blocks" (at Ethereum heights that are
              multiples of it); several joined by commas; none: only on call
    cap       the most the agent may spend in that Mood in a day, in dollars:
              past it, the task doesn't start, and says so
    tools     full (it may change things: edit the wiki, push a branch) or
              look (the default: read and search only)

Run, a task becomes a line in its Mood (source 'task'), which addresses the
agent as a mention does (views_moods.api_mentions), so its runner wakes it
there with the filled-in prompt (poller: via 'task'). Refused -- over its
cap, a blank unfilled -- it is a line too, waking nobody. Two ways in:

    POST /api/agents/<agent>/tasks/<task>/  {"params": {...}, "mood": ...}
        a person, by their enrolled key (mood_auth.key_device)
    manage.py run_due_tasks
        every minute, from cron: each task whose schedule has come round

Nothing here is a model's choice (Artificial Dumb): which task, when, and
what it is told are all read off the page (Justin, magent-self, 2026-10-22).
"""

import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

SOURCE = 'task'
TEMPLATE = 'Emergent task'
TEMPLATES = ('Emergent task', 'Agent task')  # both accepted while trusted revisions still say {{Agent task|...}}
COMMENTS = re.compile(r'<!--.*?-->', re.S)
BLANK = re.compile(r'(?<!\{)\{([a-z][a-z0-9_]*)\}(?!\})')
NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9 _-]{0,60}$')
MAX_PARAM = 2000
MAX_PROMPT = 20_000
LATE = timedelta(hours=1)  # a slot missed by more than this (the cron down) is skipped, not run late
MIN_BLOCKS = 5             # every n blocks, n at least this: a minute, the cron's own step
STALE_CLOCK = timedelta(hours=1)  # newest block anchor older than this: no height to schedule by
DAYS = ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')
GROUPS = {'daily': set(range(7)), 'weekdays': set(range(5)), 'weekends': {5, 6}}


class Refused(Exception):
    """Why a task can't start (said in the Mood, or to whoever called)."""


def title(agent, task):
    return f'Cryptograss:Emergents/{agent[:1].upper()}{agent[1:]}/Tasks/{task}'


@dataclass
class Task:
    agent: str
    name: str
    title: str
    revision: int
    editor: str
    prompt: str
    mood: str = ''
    schedule: str = ''
    cap: float = None
    tools: str = 'look'
    blanks: list = field(default_factory=list)


# --- the page -------------------------------------------------------------------

def template_fields(wikitext):
    """({setting: value}, the page without the template) for its {{Emergent task|...}} (or {{Agent task|...}}); ({}, page) if none."""
    from poller.wiki_rules import _split_top
    heads = ['{{' + n for n in TEMPLATES]
    at, head = -1, ''
    for i in range(len(wikitext)):
        for h in heads:
            if wikitext.startswith(h, i) and wikitext[i + len(h):i + len(h) + 1] in ('|', '}', '\n'):
                at, head = i, h
                break
        if at >= 0:
            break
    if at < 0:
        return {}, wikitext
    depth, j = 0, at
    while j < len(wikitext):
        two = wikitext[j:j + 2]
        if two == '{{':
            depth, j = depth + 1, j + 2
        elif two == '}}':
            depth, j = depth - 1, j + 2
            if not depth:
                break
        else:
            j += 1
    fields = {}
    for part in _split_top(wikitext[at + len(head):j - 2])[1:]:
        key, eq, value = part.partition('=')
        if eq:
            fields[key.strip().lower()] = value.strip()
    return fields, wikitext[:at] + wikitext[j:]


def parse(agent, name, revision, editor, wikitext, page_title=None):
    """A Task from one revision of its page."""
    fields, rest = template_fields(COMMENTS.sub('', wikitext or ''))
    prompt = rest.strip()
    cap = None
    if fields.get('cap'):
        try:
            cap = float(fields['cap'].lstrip('$'))
        except ValueError:
            raise Refused(f"its cap, '{fields['cap']}', isn't a number of dollars")
    if fields.get('schedule'):
        slots(fields['schedule'])  # refuse a schedule that can't be read now, not at its first slot
    tools = 'full' if fields.get('tools', '').lower() == 'full' else 'look'
    blanks = list(dict.fromkeys(BLANK.findall(prompt)))
    return Task(agent, name, page_title or title(agent, name), revision, editor, prompt,
                fields.get('mood', '').lstrip('#').strip().lower(), fields.get('schedule', ''), cap, tools, blanks)


def editors():
    from django.conf import settings
    from poller.wiki_rules import EDITORS
    return getattr(settings, 'MOOD_WIKI_RULES_EDITORS', None) or EDITORS


def history(page):
    """A page's newest revisions from PickiPedia, newest first: [(revision, editor, wikitext)]."""
    from poller.wiki_rules import WikiTexts
    return WikiTexts().get(page)


def read(agent, name, get=None):
    """The task as its newest trusted revision says, or Refused."""
    if not NAME.match(name or ''):
        raise Refused(f"'{name}' isn't a task name")
    page = title(agent, name)
    history_of = (get or history)(page)
    if not history_of:
        raise Refused(f'there is no page {page}')
    trusted = {e.strip().lower().replace('_', ' ') for e in editors()}
    found = next(((rev, ed, text) for rev, ed, text in history_of if (ed or '').lower().replace('_', ' ') in trusted),
                 None)
    if found is None:
        raise Refused(f'{page} has no revision by a trusted editor ({", ".join(editors())}) yet')
    task = parse(agent, name, *found, page_title=page)
    if not task.prompt:
        raise Refused(f'{page}, revision {task.revision}, says nothing to do')
    return task


def listed(agent, search=None):
    """The names of an agent's task pages (their titles' last part)."""
    prefix = title(agent, '')
    titles = (search or _prefix_search)(prefix)
    return [t[len(prefix):] for t in titles if t.startswith(prefix) and '/' not in t[len(prefix):]]


def _prefix_search(prefix):
    import json
    import urllib.parse
    import urllib.request
    from poller.wiki_rules import API, USER_AGENT
    url = API + '?' + urllib.parse.urlencode({'action': 'query', 'list': 'prefixsearch', 'pssearch': prefix,
                                              'pslimit': 100, 'format': 'json', 'formatversion': '2'})
    with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': USER_AGENT}), timeout=10) as r:
        return [p['title'] for p in json.load(r)['query']['prefixsearch']]


# --- filling it in ---------------------------------------------------------------

def fill(task, params):
    """The prompt with its blanks filled; Refused if one is left or one is given that it hasn't."""
    params = {str(k).lower(): v for k, v in (params or {}).items()}
    missing = [b for b in task.blanks if b not in params]
    extra = [k for k in params if k not in task.blanks]
    if missing:
        raise Refused(f"its blanks {', '.join('{' + b + '}' for b in missing)} need filling")
    if extra:
        raise Refused(f"it has no blank for {', '.join(extra)}")
    values = {}
    for key, value in params.items():
        text = ', '.join(map(str, value)) if isinstance(value, (list, tuple)) else str(value)
        if len(text) > MAX_PARAM:
            raise Refused(f'{key} is longer than {MAX_PARAM} characters')
        values[key] = text.replace('\x00', '')
    filled = BLANK.sub(lambda m: values.get(m.group(1), m.group(0)), task.prompt)
    if len(filled) > MAX_PROMPT:
        raise Refused(f'filled in, it is longer than {MAX_PROMPT} characters')
    return filled


# --- when ---------------------------------------------------------------------------

def slots(schedule):
    """The schedule's entries, joined by commas: ('days', {weekdays}, hour, minute) for
    'mondays 09:00' (UTC), or ('blocks', n) for 'every 7200 blocks' (Ethereum mainnet heights
    that are multiples of n)."""
    out = []
    for part in [p.strip().lower() for p in (schedule or '').split(',') if p.strip()]:
        every = re.fullmatch(r'every\s+(\d[\d_]*)\s+blocks?', part)
        if every:
            n = int(every.group(1).replace('_', ''))
            if n < MIN_BLOCKS:
                raise Refused(f"its schedule, '{part}', is more often than every {MIN_BLOCKS} blocks")
            out.append(('blocks', n))
            continue
        m = re.fullmatch(r'([a-z]+)\s+(\d{1,2}):(\d{2})(?:\s*utc)?', part)
        if not m or int(m.group(2)) > 23 or int(m.group(3)) > 59:
            raise Refused(f"its schedule, '{part}', isn't '<day> HH:MM' (UTC) or 'every <n> blocks'")
        day = m.group(1)
        if day in GROUPS:
            days = GROUPS[day]
        elif day.rstrip('s') in DAYS:
            days = {DAYS.index(day.rstrip('s'))}
        else:
            raise Refused(f"its schedule names '{day}': a weekday, daily, weekdays or weekends")
        out.append(('days', days, int(m.group(2)), int(m.group(3))))
    return out


def head(now, anchors=None):
    """The chain's height at `now`, from the newest block anchor (eth_blocks, synced every five
    minutes with the head) and 12-second slots after it; None if the newest is over an hour old."""
    from .eth_blocks import SECONDS_PER_SLOT, BlockClock
    clock = BlockClock(anchors)
    if not clock.numbers:
        return None
    ts = int(now.timestamp())
    number, at = clock.numbers[-1], clock.timestamps[-1]
    if ts < at:
        return clock.block_at(ts)
    if ts - at > STALE_CLOCK.total_seconds():
        return None
    return number + (ts - at) // SECONDS_PER_SLOT


def last_slot(schedule, now, block=None):
    """(key, when) for the latest slot at or before `now` the schedule names, or None: key is what
    the slot is remembered by -- its time, ISO, or 'block <height>' -- and when, its time. A
    schedule in blocks needs the chain's height now (head()); without one, Refused."""
    from .eth_blocks import SECONDS_PER_SLOT
    found = []
    for entry in slots(schedule):
        if entry[0] == 'blocks':
            if block is None:
                raise Refused("its schedule is in blocks, and the block clock here is more than an hour stale")
            height = block // entry[1] * entry[1]
            found.append((f'block {height}', now - timedelta(seconds=(block - height) * SECONDS_PER_SLOT)))
            continue
        _, days, hour, minute = entry
        for back in range(8):
            day = (now - timedelta(days=back)).replace(hour=hour, minute=minute, second=0, microsecond=0)
            if day.weekday() in days and day <= now:
                found.append((day.isoformat(), day))
                break
    return max(found, key=lambda slot: slot[1]) if found else None


# --- what it has cost --------------------------------------------------------------

def spent_today(agent, mood, now):
    """Dollars the agent's turns in this Mood cost over the past day (the runner's turn-result rows)."""
    from conversations.models import Message
    rows = Message.objects.filter(mood=mood, sender_id='system', content__type='turn-result',
                                  content__agent=agent, created_at__gte=now - timedelta(days=1))
    return sum(float(c.get('cost_usd') or 0) for c in rows.values_list('content', flat=True)
               if isinstance(c, dict))


# --- starting it --------------------------------------------------------------------

def start(task, by, params=None, mood=None, slot=None, now=None):
    """Put the task's line in its Mood: asked (which wakes the agent) or refused (which says why).
    Returns the Message. Raises Refused only for what can't be put anywhere: no Mood."""
    from conversations.models import ConversationParticipant, Message, Mood
    now = now or datetime.now(timezone.utc)
    where = Mood.by_slug(mood or task.mood) if (mood or task.mood) else None
    if where is None:
        raise Refused(f"no Mood '{mood or task.mood}'" if (mood or task.mood)
                      else f'{task.title} names no mood, and none was given')
    content = {'type': 'task', 'for': task.agent, 'task': task.name, 'page': task.title,
               'revision': task.revision, 'by': by, 'tools': task.tools, 'params': params or {},
               **({'slot': slot} if slot else {})}
    try:
        content['text'] = fill(task, params)
        if task.cap is not None:
            spent = spent_today(task.agent, where, now)
            if spent >= task.cap:
                raise Refused(f"{task.agent} has spent ${spent:.2f} here in the past day; its cap is ${task.cap:.2f}")
        content['state'] = 'asked'
    except Refused as e:
        content.update(state='refused', reason=str(e))
    system, _ = ConversationParticipant.objects.get_or_create(name='system', defaults={'participant_type': 'system'})
    return Message.objects.create(id=uuid.uuid4(), sender=system, mood=where, source_file=SOURCE,
                                  content=content, timestamp=int(time.time() * 1000))


NOT_YET = object()


def due(agents, now=None, get=None, search=None, anchors=None):
    """Start every task whose schedule has come round since its last run (once per slot; a slot
    missed by more than LATE is let go). Returns [(agent, task, the line or the reason)]."""
    from conversations.models import Message
    now = now or datetime.now(timezone.utc)
    done, block = [], NOT_YET  # the chain's height, looked up once, and only if a schedule is in blocks
    for agent in agents:
        try:
            names = listed(agent, search)
        except Exception as e:  # PickiPedia down: nothing starts, and the next minute tries again
            done.append((agent, None, f"couldn't list its tasks: {e}"))
            continue
        for name in names:
            try:
                task = read(agent, name, get)
            except Refused as e:
                done.append((agent, name, str(e)))
                continue
            except Exception as e:
                done.append((agent, name, f"couldn't read its page: {e}"))
                continue
            if not task.schedule:
                continue
            try:
                if block is NOT_YET and any(e[0] == 'blocks' for e in slots(task.schedule)):
                    block = head(now, anchors)
                found = last_slot(task.schedule, now, None if block is NOT_YET else block)
            except Refused as e:
                done.append((agent, name, str(e)))
                continue
            if found is None or now - found[1] > LATE:
                continue
            slot = found[0]
            if Message.objects.filter(source_file=SOURCE, content__for=agent, content__task=name,
                                      content__slot=slot).exists():
                continue
            try:
                done.append((agent, name, start(task, 'schedule', slot=slot, now=now)))
            except Refused as e:
                done.append((agent, name, str(e)))
    return done


# --- as a wake, and as a line ----------------------------------------------------------

def mention(msg, agent):
    """A task line asked of `agent`, as the turn that wakes it (views_moods.api_mentions), or None."""
    c = msg.content if isinstance(msg.content, dict) else {}
    if c.get('type') != 'task' or c.get('for') != agent or c.get('state') != 'asked':
        return None
    how = 'on its schedule' if c.get('by') == 'schedule' else f"at {c.get('by')}'s call"
    text = (f"@{agent}: your task {c.get('task')}, {how} ({c.get('page')}, revision {c.get('revision')}; "
            f"{'full tools' if c.get('tools') == 'full' else 'look-only'}):\n\n{c.get('text', '')}")
    # Not a person's words: the page decides its tools, not who called it (poller trusted()).
    return {'id': str(msg.id), 'sender': 'task', 'is_human': False, 'created_at': msg.created_at.isoformat(),
            'via': 'task', 'tier': None, 'text': text, 'mentions': [agent], 'reply': None,
            'full_tools': c.get('tools') == 'full'}


def line(c):
    """What a task line says (mood_view.event_line)."""
    who = f"{c.get('for', 'an agent')}'s task {c.get('task', '')}"
    how = 'on its schedule' if c.get('by') == 'schedule' else f"by {c.get('by') or 'someone'}"
    if c.get('state') == 'refused':
        return f"✗ {who} didn't start ({how}): {c.get('reason', '')}"
    return f"▶ {who}, started {how}"
