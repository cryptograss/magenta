"""Pi (https://github.com/earendil-works/pi) as a harness the runner can wake.

Pi is one open-source harness for many model providers. The runner starts a
turn with `pi --mode rpc`, the way it starts Claude Code with `claude -p`,
and this module translates what Pi prints into record events
(docs/RECORD_EVENTS.md), memory-lane's own format, before anything is
posted. Nothing of Pi's format reaches memory-lane.

Written against a real run of Pi 1.1.0 (conversations/tests/fixtures/
pi-1.1.0-turn.jsonl). What that run showed, and the translation leans on:

  - Our turn (one wake) is Pi's agent run, `agent_start` to `agent_settled`.
    Pi's own `turn_start`/`turn_end` mark each model response inside it.
  - `message_end` is authoritative; `message_update` deltas are for display.
  - Pi echoes the prompt as a user message, so the record gets the wake
    prompt from the stream (Claude Code's `-p` never says it).
  - Messages carry no ids on the wire, only millisecond timestamps, so each
    event's id is derived from the session, the timestamp and the block.
  - Every assistant message carries its own usage and cost.
"""

import collections
import json
import logging
import os
import queue
import re
import shutil
import signal
import subprocess
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

STOP_REASONS = {'stop': 'end_turn', 'length': 'max_tokens', 'toolUse': 'tool_use', 'error': 'error',
                'aborted': 'aborted'}
# Codemode is a look-only tool: its scripts may call other tools (the MCP ones
# included) but may not touch the filesystem, and only their output reaches the
# model. Without it, a look-only Pi turn can read neither its memory nor the
# wiki: `--tools` with plain names replaces the whole selection, so MCP tools
# left at their default `codemode` exposure are registered but unreachable.
#
# But an entry starting with `mcp__` switches `--tools` from "keep every MCP
# tool" to "only these" (pi --help), and without it codemode was a doorway to
# every MCP tool there is: the wiki's write tools (create-page, update-page,
# delete-page, a full-rights bot account), and playwright, which acts on the
# web (browser_click, browser_fill_form). So the look-only MCP tools are
# allowlisted by name, one place for both harnesses (poller/wake_policy.py;
# re, 30 Oct, into #295 before it merged): reads enabled, writes absent.
try:
    from poller.wake_policy import (
        WIKI_MCP_SERVER, WIKI_READ_TOOLS, MEMORY_MCP_SERVERS,
        TOOL_LOOP_KILL, TOOL_LOOP_WINDOW, TOOL_LOOP_WINDOW_KILL, TOOL_LOOP_MAX_CALLS,
    )
except ImportError:  # run as a script, from poller/
    from wake_policy import (
        WIKI_MCP_SERVER, WIKI_READ_TOOLS, MEMORY_MCP_SERVERS,
        TOOL_LOOP_KILL, TOOL_LOOP_WINDOW, TOOL_LOOP_WINDOW_KILL, TOOL_LOOP_MAX_CALLS,
    )


def _look_only_tools():
    entries = ['read', 'grep', 'find', 'ls', 'codemode']
    entries += [f'mcp__{WIKI_MCP_SERVER}__{name}' for name in WIKI_READ_TOOLS]
    entries += [f'mcp__{server}__*' for server in MEMORY_MCP_SERVERS]  # the record, whole
    return ','.join(entries)


LOOK_ONLY_TOOLS = _look_only_tools()
COMPACT = re.compile(r'/compact(?:\s+(.+))?\Z', re.S)  # what the runner sends for "/compact re [what to keep]"
# Seconds to let a stopped turn wind down, its in-flight tool included, before SIGKILL.
TURN_STOP_GRACE = 10
# TOOL_LOOP_KILL / WINDOW / WINDOW_KILL / MAX_CALLS come from poller/wake_policy.py, one place for
# both harnesses. The per-turn ceiling there is a budget, not a kill: on 31 Oct (magent, #interfaces,
# 22:50 and 01:13) it stopped two productive turns cold -- big PR work, never a loop. So it sits
# high, and at 2/3 of it the turn gets a warning steered in over RPC (pi's `steer` lands before the
# next model call; the record keeps it as the poller's user message). Only the attractor detectors
# kill; a run that ignores the warning still trips the ceiling and dies. Claude Code's `-p` has no
# stdin: there the budget only moves, no warning.
#
# Repeats are caught inside Pi, before the call runs: pi_extensions/loop_guard.ts (pi-death-loop-guard
# 0.1.2, vendored) counts a call together with its result, steers a warning in at 3 identical pairs,
# blocks the 5th (the model reads why as the tool's result), and ends the run after 3 blocks. So on
# Pi, LoopGuard keeps only the budget; its repeat detectors still guard Claude Code (mood_poller).
LOOP_GUARD_EXTENSION = Path(__file__).resolve().parent / 'pi_extensions' / 'loop_guard.ts'


class LoopGuard:
    """The tripwire both wakers share: watch a turn's tool calls, and by the
    third identical one (same tool, same arguments), say the turn is a runaway.
    Text between the repeats does not break the streak — the 28 Oct specimen
    had escape text every few seconds — only a *different* call does.
    Also catches alternating ping-pong loops (A -> B -> A -> B) via a sliding
    window, and enforces a ceiling on tool calls per wake. limit / window_limit of
    None turn those detectors off (the Pi wake: its extension catches repeats)."""

    def __init__(self, limit=TOOL_LOOP_KILL, window_limit=TOOL_LOOP_WINDOW_KILL,
                 window_size=TOOL_LOOP_WINDOW, max_calls=None):
        self.limit = limit
        self.window_limit = window_limit
        self.max_calls = TOOL_LOOP_MAX_CALLS if max_calls is None else max_calls
        self.warn_at = (self.max_calls * 2) // 3  # where the wrap-up warning goes out (2/3 of the budget)
        self.warn = None  # set once, at warn_at: the pi harness delivers it as an RPC steer
        self.streak, self.last = 0, None
        self.total = 0
        self.recent = deque(maxlen=window_size)

    def see(self, name, args):
        """Note one tool call; a nonempty string once the tripwire has tripped."""
        self.total += 1
        try:
            arg_str = json.dumps(args or {}, sort_keys=True)
        except (TypeError, ValueError):
            arg_str = str(args or {})
        key = (name, arg_str)
        self.streak = self.streak + 1 if key == self.last else 1
        self.last = key
        self.recent.append(key)
        window_count = sum(1 for k in self.recent if k == key)

        if self.limit and self.streak >= self.limit:
            return f'tool loop: {name} called {self.streak}× with identical arguments'
        if self.window_limit and window_count >= self.window_limit:
            return f'tool loop: {name} called {window_count}× within last {len(self.recent)} calls'
        if self.total >= self.max_calls:
            return f'tool loop: turn exceeded maximum tool calls limit ({self.total})'
        if self.warn is None and self.total >= self.warn_at:
            self.warn = (f'tool budget: {self.total} of {self.max_calls} tool calls used this turn. '
                         'Wrap up soon: finish what is in hand, then post your reply and stop calling tools.')
        return ''
EFFORTS = {'max': 'xhigh'}  # our effort names that Pi spells differently
# The names the model menu has always offered, as Pi should read them. Left
# to Pi, a bare 'opus' is fuzzy-matched across every vendor it knows, and
# Pi 1.1.0 picked Amazon Bedrock's.
ALIASES = {'opus': 'anthropic/claude-opus-5-5', 'sonnet': 'anthropic/claude-sonnet-5-5',
           'fable': 'anthropic/claude-fable-5-1', 'haiku': 'anthropic/claude-haiku-5-5'}
# OpenRouter's routers ('auto', 'openrouter/pareto-code', ...) pick a model per call, at that model's
# price, and no setting here caps which: on 28 Oct pareto-code picked Fable 5.1 for 46 uncached calls,
# $265 (magent, #usage). Neither offered nor run (Justin: "it was just wrong").
def is_router(model):
    """Whether a model setting ('openrouter/auto', 'openrouter/openrouter/pareto-code', 'auto') names one."""
    model = (model or '').strip()
    rest = model.split('/', 1)[1] if model.startswith('openrouter/') else model
    return rest in ('auto', 'auto-beta') or rest.startswith('openrouter/')


# Every model Pi's own library knows, by vendor (its public `compat` entry).
CATALOG_JS = """
import { getProviders, getModels } from '@earendil-works/pi-ai/compat';
const out = {};
for (const p of getProviders()) out[p] = getModels(p).map(m => [m.id, m.contextWindow || null]);
console.log(JSON.stringify(out));
"""


def iso(millis):
    return datetime.fromtimestamp(millis / 1000, tz=timezone.utc).isoformat()


def joined_text(content):
    if isinstance(content, str):
        return content
    return '\n'.join(b.get('text', '') for b in content or () if isinstance(b, dict) and b.get('type') == 'text')


def images_in(content):
    return [{'data': b['data'], 'mime_type': b.get('mimeType')} for b in content or ()
            if isinstance(b, dict) and b.get('type') == 'image' and b.get('data')]


class PiTranslator:
    """Pi's JSON events in, record events out, for one wake."""

    def __init__(self, session_id, turn=None, effort=None, prompt_from='mood-poller'):
        self.session_id, self.effort, self.prompt_from = session_id, effort, prompt_from
        self.turn = turn or str(uuid.uuid4())
        self.cost = 0.0
        self.usage = {'input': 0, 'output': 0, 'cache_read': 0, 'cache_write': 0}
        self.last_stop = None
        self.error = None  # the vendor's refusal, when a call failed: Pi keeps it on the message
        self.reply = ''
        self.started = time.monotonic()
        self.settled = False

    def id(self, millis, role, index=0):
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f'pi:{self.session_id}:{millis}:{role}:{index}'))

    def event(self, kind, at, **fields):
        return {'type': kind, 'at': at, 'turn': self.turn, **fields}

    def feed(self, raw):
        """The record events one of Pi's events stands for (often none)."""
        kind = raw.get('type') if isinstance(raw, dict) else None
        if kind == 'session':
            return [self.event('turn_started', raw.get('timestamp') or iso(time.time() * 1000),
                               cwd=raw.get('cwd'), effort=self.effort)]
        if kind == 'message_end':
            return self.message(raw.get('message') or {})
        if kind == 'compaction_end' and isinstance(raw.get('result'), dict):
            result = raw['result']
            now = time.time() * 1000
            return [self.event('compaction', iso(now), id=self.id(now, 'compaction'),
                               trigger='manual' if raw.get('reason') == 'manual' else 'auto',
                               tokens_before=result.get('tokensBefore'),
                               tokens_after=result.get('estimatedTokensAfter'), summary=result.get('summary', ''))]
        if kind == 'agent_settled':
            self.settled = True
            outcome = ('canceled' if raw.get('aborted') else 'failed' if self.last_stop == 'error' else 'completed')
            ended = self.event('turn_ended', iso(time.time() * 1000), id=str(uuid.uuid4()), outcome=outcome,
                               stop_reason=STOP_REASONS.get(self.last_stop, 'end_turn'),
                               cost_usd=round(self.cost, 6), usage=dict(self.usage),
                               duration_ms=int((time.monotonic() - self.started) * 1000))
            if outcome == 'failed' and self.error:
                ended['error'] = self.error
            return [ended]
        return []

    def message(self, message):
        role, millis = message.get('role'), message.get('timestamp') or int(time.time() * 1000)
        at = iso(millis)
        if role == 'user':
            return [self.event('user_message', at, id=self.id(millis, 'user'), text=joined_text(message.get('content')),
                               **{'from': self.prompt_from})]
        if role == 'toolResult':
            return [self.event('tool_result', at, id=self.id(millis, 'toolResult'), call_id=message.get('toolCallId'),
                               output=joined_text(message.get('content')), is_error=bool(message.get('isError')),
                               images=images_in(message.get('content')))]
        if role != 'assistant':
            return []  # the system prompt, and anything newer than this adapter
        usage = message.get('usage') or {}
        cost = float((usage.get('cost') or {}).get('total') or 0)
        self.cost += cost
        mine = {'input': usage.get('input'), 'output': usage.get('output'),
                'cache_read': usage.get('cacheRead'), 'cache_write': usage.get('cacheWrite')}
        for k, v in mine.items():
            self.usage[k] += int(v or 0)
        mine['cost'] = round(cost, 6)  # Pi's own price for the call, from its library's price list
        # What ran, which isn't always what was asked: a router ('auto', 'openrouter/pareto-code') names a
        # model per call, and Pi's list prices a router at $0, so its every call read as free while OpenRouter
        # billed it as Fable 5.1 (magent, 28 Oct: $6.11 for one 610k-token call recorded as $0).
        ran = message.get('responseModel') or message.get('model')
        if message.get('provider'):
            mine['provider'] = message['provider']  # who billed it: EventsPoster puts it on the rows' harness
        if message.get('provider') == 'openrouter' and message.get('responseId'):
            mine['generation'] = message['responseId']  # EventsPoster asks OpenRouter what it billed
        if not cost and ran != message.get('model'):
            mine['cost'] = None  # unknown, not free: a router's $0 is no price
        model = ran
        self.last_stop = message.get('stopReason')
        if self.last_stop == 'error':
            # Without this the Mood saw a turn end with no word and no reason: re on Anthropic,
            # 2026-10-18 and -19 ("400 ... Third-party apps now draw from your extra usage").
            self.error = str(message.get('errorMessage') or 'the model call failed')[:500]
        out = []
        blocks = [b for b in message.get('content') or () if isinstance(b, dict)]
        for i, block in enumerate(blocks):
            # The message's usage goes on its last block, whatever it is, so it's counted once.
            extra = {'usage': mine} if i == len(blocks) - 1 else {}
            if block.get('type') == 'text':
                # How the message stopped, in the record's words, so a text said on the way to a tool call
                # (toolUse) reads as a progress line, not the turn's answer: the poller's waits and stagger
                # lift on an ended turn only (re's review of magenta#206).
                out.append(self.event('assistant_message', at, id=self.id(millis, 'assistant', i), text=block.get('text', ''),
                                      model=model, stop_reason=STOP_REASONS.get(self.last_stop, 'end_turn'), **extra))
                if block.get('text', '').strip():
                    self.reply = block['text']
            elif block.get('type') == 'thinking':
                out.append(self.event('reasoning', at, id=self.id(millis, 'assistant', i), text=block.get('thinking', ''),
                                      signature=block.get('thinkingSignature', ''), model=model, **extra))
            elif block.get('type') == 'toolCall':
                out.append(self.event('tool_call', at, id=self.id(millis, 'assistant', i), call_id=block.get('id'),
                                      name=block.get('name'), input=block.get('arguments') or {}, model=model, **extra))
        return out


class EventsPoster:
    """Posts a turn's record events to its Mood's stream, in batches, off the
    reading thread. Same contract as mood_poller.StreamPoster."""

    def __init__(self, base, key, slug, session_id, harness='pi', forked_from=None, http=requests, retries=3,
                 pause=1.0, openrouter_key=None):
        self.url = f"{base.rstrip('/')}/api/moods/{slug}/stream/"
        self.headers = {'Authorization': f'Bearer {key}'}
        self.slug, self.session_id, self.harness, self.forked_from = slug, session_id, harness, forked_from
        self.full_tools = None  # set by the runner: whether this wake has the agent's full tools
        self.answers = None  # set by the runner: the posts that woke it (an answer asked on Tidings goes there)
        self.http, self.retries, self.pause = http, retries, pause
        # OpenRouter's own bill for each call (bill()), when the turn ran there; Pi's key is OpenRouter's.
        self.openrouter_key = openrouter_key if openrouter_key is not None else os.environ.get('OPENROUTER_API_KEY')
        self.spent, self.billed = 0.0, 0  # this turn's cost, call by call, and how many calls OpenRouter priced
        self.queue = queue.Queue()
        self.sent = self.failed = 0
        self.errors = []
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def put(self, event):
        if not isinstance(event, dict):
            return
        if event.get('type') == 'result':  # the runner closing a turn that failed to run
            event = {'type': 'turn_ended', 'id': str(uuid.uuid4()), 'at': datetime.now(timezone.utc).isoformat(),
                     'outcome': 'failed', 'error': event.get('subtype')}
        self.queue.put(event)

    def close(self, wait=60):
        self.queue.put(None)
        self.thread.join(wait)

    def run(self):
        done = False
        while not done:
            batch = [self.queue.get()]
            while len(batch) < 50:
                try:
                    batch.append(self.queue.get_nowait())
                except queue.Empty:
                    break
            done = None in batch
            events = [e for e in batch if e is not None]
            if events:
                self.bill(events)
                self.send(events)

    def bill(self, events, tries=4):
        """Each call's cost as OpenRouter billed it (GET /api/v1/generation), in place of Pi's list price:
        what was paid, not what Pi's price list guessed. Its stats can lag the response by a second or two,
        so a 404 is asked again. A call it won't price keeps Pi's figure (None for a router's). The turn's
        end then says the sum."""
        for event in events:
            usage = event.get('usage') if isinstance(event.get('usage'), dict) else None
            if usage and usage.get('generation') and self.openrouter_key:
                for attempt in range(tries):
                    try:
                        response = self.http.get('https://openrouter.ai/api/v1/generation',
                                                 params={'id': usage['generation']}, timeout=10,
                                                 headers={'Authorization': f'Bearer {self.openrouter_key}'})
                        if response.status_code == 200:
                            paid = (response.json().get('data') or {}).get('total_cost')
                            if isinstance(paid, (int, float)):
                                usage['cost'], usage['billed'] = round(float(paid), 6), True
                                self.billed += 1
                            break
                        if response.status_code != 404:
                            break
                    except (requests.RequestException, ValueError) as e:
                        logger.warning(f'{self.slug}: OpenRouter would not say what a call cost: {e}')
                    time.sleep(self.pause * (attempt + 1))
            if usage and usage.get('provider') and self.harness.startswith('pi'):
                # Which vendor billed the turn, on its rows ('pi 1.1.0 · openrouter'): Pi reaches OpenRouter
                # and vendors directly alike, and the usage page tabs them by who was paid (re, #237).
                self.harness = f"{self.harness.split(' · ')[0]} · {usage['provider']}"[:50]
            if usage and isinstance(usage.get('cost'), (int, float)):
                self.spent += usage['cost']
            if event.get('type') == 'turn_ended' and self.billed:
                event['cost_usd'] = round(self.spent, 6)

    def send(self, events):
        body = {'format': 'events/1', 'harness': self.harness, 'session_id': self.session_id, 'events': events}
        if self.forked_from:
            body['forked_from'] = self.forked_from
        if self.full_tools is not None:
            body['full_tools'] = self.full_tools
        if self.answers:
            body['answers'] = self.answers
        for attempt in range(self.retries):
            try:
                response = self.http.post(self.url, json=body, headers=self.headers, timeout=30)
                if response.status_code == 200:
                    self.sent += len(events)
                    self.errors += response.json().get('errors') or []
                    return True
                logger.warning(f'{self.slug}: stream post answered {response.status_code}')
                if response.status_code in (400, 401, 403, 404, 413, 503):
                    break
            except requests.RequestException as e:
                logger.warning(f'{self.slug}: stream post failed: {e}')
            time.sleep(self.pause * 2 ** attempt)
        self.failed += len(events)
        return False


class PiWaker:
    """Starts one Pi turn, new or forked from a session Pi keeps on this
    machine. Same interface as mood_poller.ClaudeCodeWaker, except that what
    reaches `on_event` is already record events."""

    def __init__(self, pi='pi', model=None, session_dir='~/.local/state/magenta/pi-sessions',
                 new_cwd='~/workspace', quiet_limit=1500, version=None):
        self.pi, self.model = pi, model
        self.session_dir = Path(session_dir).expanduser()
        self.new_cwd, self.quiet_limit = new_cwd, quiet_limit
        self.version = version
        self._local = threading.local()

    @property
    def last_result(self):
        return getattr(self._local, 'last_result', None)

    @last_result.setter
    def last_result(self, value):
        self._local.last_result = value

    def harness(self):
        if self.version is None:
            try:
                self.version = subprocess.run([self.pi, '--version'], capture_output=True, text=True,
                                              timeout=30).stdout.strip().split()[-1]
            except (OSError, subprocess.SubprocessError, IndexError):
                self.version = ''
        return f'pi {self.version}'.strip()

    def package(self):
        """Pi's own package directory (where its libraries resolve from), or None."""
        found = shutil.which(self.pi) or self.pi
        for parent in Path(found).resolve().parents:
            manifest = parent / 'package.json'
            if manifest.is_file():
                try:
                    if json.loads(manifest.read_text()).get('name') == '@earendil-works/pi-coding-agent':
                        return parent
                except ValueError:
                    pass
        return None

    def run_quietly(self, cmd, cwd=None):
        try:
            return subprocess.run(cmd, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                  timeout=120).stdout
        except (OSError, subprocess.SubprocessError):
            return ''

    def catalog(self):
        """{'harness', 'vendors': [{'name', 'usable', 'models': [[id, context], ...]}]}: every model this
        Pi knows, and whether the keys here reach each vendor (what `pi --list-models` lists is what
        it can run). Never the keys themselves. None if Pi says nothing."""
        known = {}
        package = self.package()
        if package is not None:
            try:
                known = json.loads(self.run_quietly(['node', '--input-type=module', '-e', CATALOG_JS], cwd=package)
                                   or '{}')
            except ValueError:
                known = {}
        usable = {}
        lines = self.run_quietly([self.pi, '--list-models']).splitlines()
        if lines and lines[0].split()[:2] == ['provider', 'model']:
            for line in lines[1:]:
                parts = line.split()
                if len(parts) >= 2:
                    usable.setdefault(parts[0], []).append(parts[1])
        if not known and not usable:
            return None
        vendors = []
        for name in sorted(set(known) | set(usable)):
            models = [list(m)[:2] for m in known.get(name, [])]
            ids = {m[0] for m in models}
            models += [[m, None] for m in usable.get(name, []) if m not in ids]  # a vendor of its own (models.json)
            models = [m for m in models if not is_router(f'{name}/{m[0]}')]
            vendors.append({'name': name, 'usable': name in usable, 'models': models})
        return {'harness': self.harness(), 'vendors': vendors}

    def find(self, session_id):
        matches = sorted(self.session_dir.glob(f'*_{session_id}.jsonl'))
        return matches[0] if matches else None

    def can_wake(self, session_id):
        return self.find(session_id) is not None

    def last_touched(self, session_id):
        """When `session_id` last ran (its file's last write), or None."""
        path = self.find(session_id)
        try:
            return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) if path else None
        except OSError:
            return None

    def session_bytes(self, session_id):
        """The session file's size on disk (0 if unknown): the poller's size cap reads it."""
        path = self.find(session_id)
        try:
            return path.stat().st_size if path else 0
        except OSError:
            return 0

    def cwd_for(self, session_id):
        path = self.find(session_id)
        if path is None:
            return None
        with open(path) as f:
            header = json.loads(f.readline() or '{}')
        cwd = header.get('cwd')
        return cwd if cwd and os.path.isdir(cwd) else None

    def compact(self, session_id, keep, new_session_id=None, on_event=None, model=None, stop=None):
        """Compact a session, keeping what `keep` asks: Pi's RPC mode, whose `compact` is what its own
        /compact runs (its print mode takes '/compact' as words). Forked like a turn, so the session it
        leaves is the next wake's. (new session id, ''): what it kept is the compaction event."""
        cwd = self.cwd_for(session_id)
        if cwd is None:
            raise RuntimeError(f'pi session {session_id} cannot be resumed from here')
        new_session_id = new_session_id or str(uuid.uuid4())
        translator = PiTranslator(new_session_id)

        def say(events):
            for event in events:
                if on_event:
                    try:
                        on_event(event)
                    except Exception as e:  # never let reporting stop the turn
                        logger.warning(f'on_event failed: {e}')

        cmd = [self.pi, '--mode', 'rpc', '--session-dir', str(self.session_dir), '--session-id', new_session_id,
               '--fork', session_id]
        if is_router(model or self.model):
            raise RuntimeError(f'{model or self.model} is a router, which may pick any model at any price: not run')
        if model or self.model:
            cmd += ['--model', ALIASES.get(model or self.model, model or self.model)]
        say([translator.event('turn_started', iso(time.time() * 1000), cwd=cwd)])
        proc = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, start_new_session=True)
        done = self._watch(proc, stop)
        answer = None
        payload = {'id': 'compact', 'type': 'compact'}
        if keep:
            payload['customInstructions'] = keep
        try:
            proc.stdin.write(json.dumps(payload) + '\n')
            proc.stdin.flush()
            for line in proc.stdout:
                done.heard = time.monotonic()
                try:
                    raw = json.loads(line)
                except ValueError:
                    continue
                if raw.get('type') == 'response' and raw.get('id') == 'compact':
                    answer = raw
                    break
                say(translator.feed(raw))
        except BrokenPipeError:
            pass
        finally:
            try:
                proc.stdin.close()  # Pi's orderly exit
            except BrokenPipeError:
                pass
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGTERM)
                proc.wait()
            done.set()
        if answer is None:
            err = (proc.stderr.read() or '').strip()[:500]
            self.last_result = {'subtype': 'error', 'run_cost_usd': 0.0, 'local_command': 'compact'}
            raise RuntimeError(f'pi exited {proc.returncode} without compacting: {err}')
        cost = float((((answer.get('data') or {}).get('usage') or {}).get('cost') or {}).get('total') or 0)
        ok = bool(answer.get('success'))
        say([translator.event('turn_ended', iso(time.time() * 1000), id=str(uuid.uuid4()),
                              outcome='completed' if ok else 'failed', stop_reason='end_turn', cost_usd=round(cost, 6),
                              duration_ms=int((time.monotonic() - translator.started) * 1000),
                              **({} if ok else {'error': f"/compact: {answer.get('error') or 'failed'}"}))])
        self.last_result = {'subtype': 'success' if ok else 'error', 'run_cost_usd': cost, 'local_command': 'compact'}
        return new_session_id, ''

    def _watch(self, proc, stop):
        """Ends `proc` if the Mood stops it or it says nothing for quiet_limit seconds. The Event it
        returns: set() it when the process is done; its `heard` is when it last said something."""
        done = threading.Event()
        done.heard = time.monotonic()

        def watch():
            while not done.wait(3):
                why = ('stopped' if stop and stop() else
                       'silent too long' if time.monotonic() - done.heard > self.quiet_limit else None)
                if why:
                    logger.warning(f'ending pi turn: {why}')
                    try:
                        os.killpg(proc.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    return

        threading.Thread(target=watch, daemon=True).start()
        return done

    def command(self, session_id, new_session_id, model=None, effort=None, full=False, system=None):
        """The argv for one wake's pi process: RPC mode, so the runner prompts it on stdin and
        can steer it mid-turn (the tool budget's wrap-up warning)."""
        cmd = [self.pi, '--mode', 'rpc', '--session-dir', str(self.session_dir), '--session-id', new_session_id,
               '-e', str(LOOP_GUARD_EXTENSION)]  # repeats caught before they run (LOOP_GUARD_EXTENSION)
        if session_id:
            cmd += ['--fork', session_id]
        model = model or self.model
        if is_router(model):
            raise RuntimeError(f'{model} is a router, which may pick any model at any price: not run')
        if model:
            cmd += ['--model', ALIASES.get(model, model)]
        if effort:
            cmd += ['--thinking', EFFORTS.get(effort, effort)]
        if not full:
            cmd += ['--tools', LOOK_ONLY_TOOLS]  # look-only: reads and the allowlisted MCP tools (wake_policy.py)
        if system:  # its instructions (mood_poller.MoodPoller.instructions_for): after Pi's own, never in the session
            cmd += ['--append-system-prompt', system]
        return cmd

    def wake(self, session_id, prompt, new_session_id=None, on_event=None, model=None, effort=None, full=False,
             stop=None, system=None, cwd=None, news=None, **ignored):
        """Run one turn; (new session id, its reply). '/compact <what to keep>' compacts instead.
        The prompt goes in on stdin (RPC mode), and LoopGuard's tool-budget warning is steered in mid-turn,
        as is `news()`, what was said in the Mood meanwhile (MoodPoller.news_check), asked at each tool call."""
        keep = COMPACT.match(prompt or '')
        if keep and session_id:
            instructions = keep.group(1).strip() if (keep and keep.group(1)) else ''
            return self.compact(session_id, instructions, new_session_id=new_session_id, on_event=on_event,
                                model=model, stop=stop)
        if session_id is None:  # a new session: in its Mood's directory (`cwd`), else where new ones start
            cwd = cwd or str(Path(self.new_cwd).expanduser())
        else:
            cwd = self.cwd_for(session_id)
            if cwd is None:
                raise RuntimeError(f'pi session {session_id} cannot be resumed from here')
        new_session_id = new_session_id or str(uuid.uuid4())
        self.session_dir.mkdir(parents=True, exist_ok=True)
        translator = PiTranslator(new_session_id, effort=effort)
        cmd = self.command(session_id, new_session_id, model=model, effort=effort, full=full, system=system)
        proc = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, start_new_session=True)
        done = self._watch(proc, stop)
        # The budget only: repeats are the extension's (LOOP_GUARD_EXTENSION), stopped before they run.
        guard, killed, steered = LoopGuard(limit=None, window_limit=None), None, False
        try:
            proc.stdin.write(json.dumps({'id': 'turn', 'type': 'prompt', 'message': prompt}) + '\n')
            proc.stdin.flush()
            # RPC mode streams no session record, so the turn starts here (as compact does).
            if on_event:
                try:
                    on_event(translator.event('turn_started', iso(time.time() * 1000), cwd=cwd, effort=effort))
                except Exception as e:  # never let reporting stop the turn
                    logger.warning(f'on_event failed: {e}')
            for line in proc.stdout:  # Pi frames on LF only; one JSON object per line
                done.heard = time.monotonic()
                try:
                    raw = json.loads(line)
                except ValueError:
                    continue
                for event in translator.feed(raw):
                    if on_event:
                        try:
                            on_event(event)
                        except Exception as e:  # never let reporting stop the turn
                            logger.warning(f'on_event failed: {e}')
                    if event.get('type') == 'tool_call' and not killed:
                        killed = guard.see(event.get('name'), event.get('input'))
                        if killed:
                            logger.warning(f'ending pi turn for {new_session_id}: {killed}')
                            if on_event:
                                try:
                                    now = time.time() * 1000
                                    on_event({
                                        'type': 'assistant_message',
                                        'id': translator.id(now, 'assistant', 9999),
                                        'turn': translator.turn,
                                        'at': iso(now),
                                        'text': f'⚠️ Turn stopped by loop tripwire: {killed}',
                                        'stop_reason': 'error',
                                    })
                                except Exception:
                                    pass
                            try:
                                os.killpg(proc.pid, signal.SIGTERM)
                            except (OSError, ProcessLookupError, AttributeError):
                                try:
                                    proc.terminate()
                                except (OSError, ProcessLookupError, AttributeError):
                                    pass
                        elif news and not killed:
                            try:
                                said = news()
                            except Exception as e:  # can't ask: the turn goes on
                                logger.debug(f'news check failed: {e}')
                                said = None
                            if said:
                                try:
                                    proc.stdin.write(json.dumps({'type': 'steer', 'message': said}) + '\n')
                                    proc.stdin.flush()
                                except (BrokenPipeError, OSError, AttributeError):
                                    pass
                        if not killed and guard.warn and not steered:
                            # The tool budget's wrap-up warning: pi delivers a steer before the
                            # next model call, and the record keeps it as the poller's user message.
                            steered = True
                            try:
                                proc.stdin.write(json.dumps({'type': 'steer', 'message': guard.warn}) + '\n')
                                proc.stdin.flush()
                            except (BrokenPipeError, OSError, AttributeError):
                                pass
                if killed:
                    break
                if translator.settled:
                    break  # RPC pi lives on after its run; closing stdin is its orderly exit
        finally:
            done.set()
            try:
                proc.stdin.close()  # Pi's orderly exit
            except (BrokenPipeError, OSError, AttributeError):
                pass
        # SIGTERM asks; SIGKILL is the last resort. A turn stopped mid-tool leaves the tool's own
        # child running (a `git commit`, a test run), and SIGKILL to the group murders it mid-write:
        # magent's commit died 137 that way on 31 Oct. So let the polite signal land first.
        try:
            proc.wait(timeout=TURN_STOP_GRACE)
        except (subprocess.TimeoutExpired, AttributeError):
            logger.warning(f'pi still running {TURN_STOP_GRACE}s after SIGTERM: killing the group')
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (OSError, ProcessLookupError, AttributeError):
                try:
                    proc.kill()
                except (OSError, ProcessLookupError, AttributeError):
                    pass
            try:
                proc.wait(timeout=2)
            except (subprocess.TimeoutExpired, AttributeError):
                pass
        if not translator.settled:
            # The runner closes the turn in the Mood when a wake raises.
            err = (proc.stderr.read() or '').strip()[:500]
            self.last_result = {'subtype': 'error', 'run_cost_usd': translator.cost}
            if killed:
                self.last_result['error'] = killed
                raise RuntimeError(f'pi exited {proc.returncode} without settling: {killed}: {err}')
            raise RuntimeError(f'pi exited {proc.returncode} without settling: {err}')
        self.last_result = {'subtype': 'error' if translator.error else 'success', 'run_cost_usd': translator.cost,
                            'stop_reason': translator.last_stop}
        if translator.error:
            self.last_result['error'] = translator.error
        return new_session_id, translator.reply.strip()
