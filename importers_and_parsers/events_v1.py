"""The importer for record events (docs/RECORD_EVENTS.md): memory-lane's own
format for an agent's turn, whatever harness ran it.

A runner translates its harness's output into these events and posts them
to a Mood's stream (conversations/views_runner.py). This is the only place
they become rows, and it writes the rows the Claude Code importer writes for
the same turn -- same kinds, senders, content shapes and fields -- so every
view and tool reads them without knowing which way they came.
"""

import json
import uuid
from datetime import datetime

from django.conf import settings
from django.utils import timezone

from conversations.models import (
    ContextHeap, ContextHeapType, ConversationParticipant, Era, Message, Thought, ThinkingEntity,
    ToolResult, ToolUse,
)
from conversations.services.media import markdown, store_base64
from conversations.services.mood_view import COMPACTION_PREFIX
from conversations.services.redaction import redact_value

FORMAT = 'events/1'
TYPES = ('turn_started', 'user_message', 'assistant_message', 'reasoning', 'tool_call', 'tool_result',
         'compaction', 'task_notice', 'turn_ended')
OUTCOMES = {'completed': 'success', 'failed': 'error', 'canceled': 'canceled'}
# A turn's metadata, stamped on its rows: from its turn_started, or else from
# the latest row of the same session that has it (a turn posts in batches).
TURN_FIELDS = ('model_backend', 'effort', 'cwd', 'git_branch')


class Refused(ValueError):
    """An event this format can't take; reported, and the rest go on."""


def participant(name, kind):
    found, _ = ConversationParticipant.objects.get_or_create(name=name, defaults={'participant_type': kind})
    return found


def millis(at):
    try:
        return int(datetime.fromisoformat(str(at).replace('Z', '+00:00')).timestamp() * 1000)
    except ValueError:
        raise Refused(f'`at` is not an ISO time: {at!r}')


def usage_fields(usage):
    usage = usage if isinstance(usage, dict) else {}
    return {'input_tokens': usage.get('input'), 'output_tokens': usage.get('output'),
            'cache_read_input_tokens': usage.get('cache_read'),
            'cache_creation_input_tokens': usage.get('cache_write')}


def tool_output(output, images, keep):
    """A tool result's stored text: its output, and any images as stored
    media, cut to settings.TOOL_RESULT_CONTENT_CHARS (nothing, by default:
    tool output is where secrets are likeliest)."""
    limit = settings.TOOL_RESULT_CONTENT_CHARS if keep else 0
    text = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)
    parts = [Message.sanitize_content(text)]
    for image in images or ():
        media = store_base64(image.get('data')) if limit and isinstance(image, dict) else None
        parts.append(markdown(media) if media else '[image omitted]')
    text = '\n'.join(p for p in parts if p)
    if len(text) > limit:
        text = text[:limit] + (f"\n[{len(text) - limit} more characters not kept]" if limit else '')
    return text


class Turn:
    """One post's events, for one agent in one session of one Mood."""

    def __init__(self, *, agent, mood, session_id, harness=None, forked_from=None, source='mood-runner',
                 keep_tool_output=True):
        self.agent, _ = ThinkingEntity.objects.get_or_create(name=agent, defaults={'is_biological_human': False})
        self.mood, self.session_id, self.harness = mood, session_id, harness
        self.forked_from, self.keep = forked_from, keep_tool_output
        self.source_file = f'ingest-{source}'
        self.era, _ = Era.objects.get_or_create(name='Current Working Era (Era N)')
        self.meta = {}
        self.finished = None

    # --- where a row goes ------------------------------------------------

    def latest(self, session_id, but=None):
        if not session_id:
            return None
        rows = Message.objects.filter(session_id=session_id, is_sidechain=False).exclude(sender_id='system')
        return rows.exclude(pk=but).order_by('-created_at').first() if but else rows.order_by('-created_at').first()

    def place(self, message):
        """Thread the row after the session's last one and put it in that
        one's context heap: a session -- and a fork, which goes on from the
        history it copied -- is one context window until it is compacted."""
        before = self.latest(self.session_id, but=message.pk) or self.latest(self.forked_from)
        if message.is_continuation_message or before is None or before.context_heap is None:
            kind = ContextHeapType.POST_COMPACTING if message.is_continuation_message else ContextHeapType.FRESH
            heap = ContextHeap.objects.create(era=self.era, type=kind)
        else:
            heap = before.context_heap
        if before is not None:
            message.parent_id = before.pk
        heap.add_event(message)

    def stamp(self):
        """The turn's model, effort, cwd and branch, for its rows."""
        if all(self.meta.get(k) for k in TURN_FIELDS):
            return self.meta
        before = self.latest(self.session_id) or self.latest(self.forked_from)
        inherited = {k: getattr(before, k) for k in TURN_FIELDS} if before else {}
        return {**inherited, **{k: v for k, v in self.meta.items() if v}}

    def row(self, model, event, sender, content, recipient=None, **fields):
        common = {
            'sender': sender, 'content': content, 'mood': self.mood, 'session_id': self.session_id,
            'timestamp': millis(event['at']), 'is_sidechain': bool(event.get('helper')),
            'source_file': self.source_file, 'client_version': self.harness, 'created_at': timezone.now(),
            **self.stamp(),
        }
        common.update({k: v for k, v in fields.items() if v is not None or k not in common})
        message, created = model.objects.get_or_create(id=event['id'], defaults=common)
        if created:
            if recipient is not None:
                message.recipients.add(recipient)
            if not message.is_sidechain:
                self.place(message)
        return message, created

    # --- the events --------------------------------------------------------

    def turn_started(self, event):
        self.meta.update({'model_backend': event.get('model'), 'effort': event.get('effort'),
                          'cwd': event.get('cwd'), 'git_branch': event.get('git_branch')})
        return None, False

    def user_message(self, event):
        # People's words reach the record through the Mood, where they typed
        # them; a runner can't put words in anyone's mouth. What it can say
        # is that its poller woke the agent, or that the agent briefed a helper.
        if event.get('helper'):
            sender = self.agent
        elif event.get('from', 'mood-poller') == 'mood-poller':
            sender = participant('mood-poller', 'system')
        else:
            raise Refused("user_message: only 'mood-poller' (or a helper's brief) comes from a runner")
        return self.row(Message, event, sender, event.get('text', ''), recipient=self.agent)

    def assistant_message(self, event):
        return self.row(Message, event, self.agent, [{'type': 'text', 'text': event.get('text', '')}],
                        model_backend=event.get('model'), **usage_fields(event.get('usage')))

    def reasoning(self, event):
        block = {'type': 'thinking', 'thinking': event.get('text', ''), 'signature': event.get('signature', '')}
        return self.row(Thought, event, self.agent, [block], recipient=self.agent,
                        signature=event.get('signature', ''), model_backend=event.get('model'))

    def tool_call(self, event):
        if not event.get('call_id') or not event.get('name'):
            raise Refused('tool_call needs call_id and name')
        return self.row(ToolUse, event, self.agent, event.get('input') or {},
                        recipient=participant(event['name'], 'tool'),
                        tool_name=event['name'][:100], tool_id=str(event['call_id'])[:100],
                        model_backend=event.get('model'))

    def tool_result(self, event):
        if not event.get('call_id'):
            raise Refused('tool_result needs call_id')
        content = tool_output(event.get('output', ''), event.get('images'), self.keep)
        return self.row(ToolResult, event, participant('tool-result', 'tool'), content, recipient=self.agent,
                        tool_use_id=str(event['call_id'])[:100], is_error=bool(event.get('is_error')))

    def compaction(self, event):
        # The record marks a summary by its opening words (mood_view's
        # COMPACTION_PREFIX), Claude Code's to begin with: every reader that
        # leaves summaries out of the conversation looks for them. A summary
        # from another harness gets them too.
        summary = str(event.get('summary', ''))
        if not summary.startswith(COMPACTION_PREFIX):
            summary = f'{COMPACTION_PREFIX}. The summary below covers the earlier portion of it.\n\n{summary}'
        return self.row(Message, event, self.agent, summary, is_continuation_message=True)

    def task_notice(self, event):
        content = (f"<task-notification>\n<task-id>{event.get('task_id', '')}</task-id>\n"
                   f"<tool-use-id>{event.get('call_id') or ''}</tool-use-id>\n"
                   f"<status>{event.get('status', '')}</status>\n<summary>{event.get('summary') or ''}</summary>\n"
                   f"</task-notification>")
        return Message.objects.get_or_create(id=event['id'], defaults={
            'sender': participant('system', 'system'), 'content': content, 'timestamp': millis(event['at']),
            'session_id': self.session_id, 'mood': self.mood, 'source_file': 'task-notification'})

    def turn_ended(self, event):
        self.finished = {
            'uuid': event['id'],
            'subtype': OUTCOMES.get(event.get('outcome'), event.get('outcome')),
            'is_error': event.get('outcome') == 'failed',
            'stop_reason': event.get('stop_reason'),
            'run_cost_usd': event.get('cost_usd'),
            'duration_ms': event.get('duration_ms'),
            'error': event.get('error'),
            'usage': event.get('usage'),
        }
        return None, False

    # --- a post ------------------------------------------------------------

    def take(self, events):
        """(imported, skipped, ignored, errors) for a post's events, in order."""
        imported = skipped = ignored = 0
        errors = []
        for n, event in enumerate(events):
            try:
                if not isinstance(event, dict) or not isinstance(event.get('type'), str):
                    raise Refused('an event is an object with a type')
                if event['type'] not in TYPES:
                    ignored += 1  # newer than this importer: counted, never an error
                    continue
                if event['type'] not in ('turn_started', 'turn_ended'):
                    event['id'] = str(uuid.UUID(str(event.get('id'))))
                    if 'at' not in event:
                        raise Refused('every event needs `at`')
                elif event['type'] == 'turn_ended':
                    event['id'] = str(uuid.UUID(str(event.get('id') or uuid.uuid5(
                        uuid.NAMESPACE_URL, f"turn-result:{self.session_id}:{event.get('turn')}"))))
                event, _ = redact_value(event)
                _, created = getattr(self, event['type'])(event)
                if created:
                    imported += 1
                elif event['type'] not in ('turn_started', 'turn_ended'):
                    skipped += 1
            except (Refused, ValueError, TypeError, KeyError) as e:
                errors.append(f'event {n}: {e}')
        return imported, skipped, ignored, errors
