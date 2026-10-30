"""A real Pi turn, translated by the runner and posted as record events,
becomes the same kind of rows any turn does (poller/pi_harness.py)."""

import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

from django.test import TestCase, override_settings
from unittest import TestCase as PlainCase  # for tests that need no database

from conversations.models import Message, Mood, ThinkingEntity, ToolResult, ToolUse
from poller.pi_harness import EventsPoster, LoopGuard, PiTranslator, PiWaker

FIXTURE = Path(__file__).with_name('fixtures') / 'pi-1.1.0-turn.jsonl'  # Pi 1.1.0, local model, 2026-10-11
KEY = 'k' * 64
SESSION = '01a12c78-a26d-70bf-9a54-ad9e286ba1d3'
COST_SESSION = 'c0575e55-0000-4000-8000-000000000001'


def translated(session=SESSION):
    t = PiTranslator(session, turn='t1')
    events = []
    for line in FIXTURE.read_text().splitlines():
        events += t.feed(json.loads(line))
    return t, events


class TranslatorTest(TestCase):

    def test_a_real_run_becomes_a_turn_of_record_events(self):
        t, events = translated()
        self.assertEqual([e['type'] for e in events],
                         ['turn_started', 'user_message', 'tool_call', 'tool_result', 'assistant_message', 'turn_ended'])
        prompt, call, result, reply, ended = events[1:]
        self.assertEqual(prompt['from'], 'mood-poller')
        self.assertIn('Use the read tool', prompt['text'])  # Pi says the prompt; Claude Code's -p doesn't
        self.assertEqual((call['name'], call['call_id']), ('read', result['call_id']))
        self.assertEqual(call['input']['path'], 'x.txt')
        self.assertIn('banjo', result['output'])
        self.assertEqual(reply['model'], 'qwen2.5-1.5b-instruct-q4_k_m')
        self.assertEqual((ended['outcome'], ended['stop_reason']), ('completed', 'end_turn'))
        self.assertEqual(ended['usage']['input'], 1438 + 36)  # summed over the run's two model responses
        self.assertEqual(t.reply, reply['text'])

    def test_ids_are_the_same_every_time_and_differ_by_session(self):
        _, once = translated()
        _, again = translated()
        _, other = translated(session=str(uuid.uuid4()))
        ids = lambda evs: [e.get('id') for e in evs if e['type'] not in ('turn_started', 'turn_ended')]
        self.assertEqual(ids(once), ids(again))
        self.assertFalse(set(ids(once)) & set(ids(other)))

    def test_the_system_prompt_is_not_recorded(self):
        _, events = translated()
        self.assertFalse(any('expert coding assistant' in json.dumps(e) for e in events))

    def test_a_refused_call_ends_the_turn_saying_why(self):
        # re on Anthropic through Pi, 2026-10-19: the subscription refused, and the Mood saw nothing.
        t = PiTranslator(SESSION)
        refusal = ('400 {"type":"error","error":{"type":"invalid_request_error","message":"Third-party apps now '
                   'draw from your extra usage, not your plan limits."}}')
        t.feed({'type': 'session', 'timestamp': '2026-10-19T17:29:36.413Z'})
        said = t.feed({'type': 'message_end', 'message': {'role': 'assistant', 'content': [], 'stopReason': 'error',
                                                          'errorMessage': refusal, 'usage': {}}})
        self.assertEqual(said, [])
        ended = t.feed({'type': 'agent_settled'})[0]
        self.assertEqual(ended['outcome'], 'failed')
        self.assertIn('Third-party apps', ended['error'])

    def test_a_text_on_the_way_to_a_tool_call_is_not_the_turns_end(self):
        # re, 27 Oct: Pi turns carried no stop, so every progress line read as an ended turn to the poller.
        t = PiTranslator(SESSION)
        t.feed({'type': 'session', 'timestamp': '2026-10-27T10:54:00.000Z'})
        on_the_way = t.feed({'type': 'message_end', 'message': {
            'role': 'assistant', 'stopReason': 'toolUse', 'usage': {},
            'content': [{'type': 'text', 'text': 'Checking the deploy.'},
                        {'type': 'toolCall', 'id': 'c1', 'name': 'read', 'arguments': {'path': 'x'}}]}})
        self.assertEqual([e['type'] for e in on_the_way], ['assistant_message', 'tool_call'])
        self.assertEqual(on_the_way[0]['stop_reason'], 'tool_use')
        answer = t.feed({'type': 'message_end', 'message': {
            'role': 'assistant', 'stopReason': 'stop', 'usage': {},
            'content': [{'type': 'text', 'text': 'Yes, redeploy.'}]}})
        self.assertEqual(answer[0]['stop_reason'], 'end_turn')

    def test_a_compaction_and_an_aborted_run(self):
        t = PiTranslator(SESSION)
        compacted = t.feed({'type': 'compaction_end', 'reason': 'threshold',
                            'result': {'summary': 'Banjo setlist.', 'tokensBefore': 150000, 'estimatedTokensAfter': 32000}})
        self.assertEqual((compacted[0]['type'], compacted[0]['trigger'], compacted[0]['tokens_before']),
                         ('compaction', 'auto', 150000))
        self.assertEqual(t.feed({'type': 'compaction_end', 'reason': 'manual', 'errorMessage': 'too small'}), [])
        self.assertEqual(t.feed({'type': 'agent_settled', 'aborted': True})[0]['outcome'], 'canceled')


class CommandTest(TestCase):

    def test_look_only_reads_and_a_fork_names_its_parent(self):
        waker = PiWaker(pi='pi', model='local/qwen', session_dir='/tmp/s')
        cmd = waker.command('old', 'new', 'hi', effort='max')
        self.assertEqual(cmd[:2], ['pi', '--mode'])
        self.assertIn('--fork', cmd)
        self.assertEqual(cmd[cmd.index('--session-id') + 1], 'new')
        self.assertEqual(cmd[cmd.index('--tools') + 1], 'read,grep,find,ls,codemode')
        self.assertEqual(cmd[cmd.index('--thinking') + 1], 'xhigh')
        self.assertNotIn('--tools', waker.command(None, 'new', 'hi', full=True))
        self.assertEqual(cmd[-1], 'hi')


# A Pi in RPC mode, as Pi 1.1.0 answers `compact` (tried on hunter, 18 Oct): its events, then the response.
FAKE_RPC_PI = r'''#!PYTHON
import json, sys
open(sys.argv[0] + '.argv', 'w').write(json.dumps(sys.argv[1:]))
asked = json.loads(sys.stdin.readline())
open(sys.argv[0] + '.asked', 'w').write(json.dumps(asked))
small = 'too small' in asked['customInstructions']
print(json.dumps({'type': 'compaction_start', 'reason': 'manual'}), flush=True)
if small:
    print(json.dumps({'type': 'compaction_end', 'reason': 'manual', 'aborted': False,
                      'errorMessage': 'Compaction failed: Nothing to compact (session too small)'}))
    print(json.dumps({'id': asked['id'], 'type': 'response', 'command': 'compact', 'success': False,
                      'error': 'Nothing to compact (session too small)'}), flush=True)
else:
    result = {'summary': 'Kept: the setlist.', 'firstKeptEntryId': 'abc123', 'tokensBefore': 150000,
              'estimatedTokensAfter': 32000, 'usage': {'cost': {'total': 0.03}}}
    print(json.dumps({'type': 'compaction_end', 'reason': 'manual', 'aborted': False, 'willRetry': False,
                      'result': result}))
    print(json.dumps({'id': asked['id'], 'type': 'response', 'command': 'compact', 'success': True,
                      'data': result}), flush=True)
sys.stdin.read()  # until the runner closes it, as Pi does
'''


class CompactTest(TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.pi = os.path.join(self.dir, 'pi')
        Path(self.pi).write_text(FAKE_RPC_PI.replace('PYTHON', sys.executable))
        os.chmod(self.pi, 0o755)
        sessions = Path(self.dir, 's')
        sessions.mkdir()
        (sessions / f'2026-10-18T15-00-00-000Z_{SESSION}.jsonl').write_text(
            json.dumps({'type': 'session', 'version': 3, 'id': SESSION, 'cwd': self.dir}) + '\n')
        self.waker = PiWaker(pi=self.pi, model='opus', session_dir=str(sessions))

    def test_compact_asks_pis_rpc_mode_from_a_fork_and_records_what_it_kept(self):
        events = []
        new, reply = self.waker.wake(SESSION, '/compact keep the setlist', new_session_id='new-1',
                                     on_event=events.append, effort='high')
        self.assertEqual((new, reply), ('new-1', ''))
        argv = json.loads(Path(self.pi + '.argv').read_text())
        self.assertEqual(argv[:2], ['--mode', 'rpc'])
        self.assertEqual((argv[argv.index('--fork') + 1], argv[argv.index('--session-id') + 1]), (SESSION, 'new-1'))
        self.assertEqual(argv[argv.index('--model') + 1], 'anthropic/claude-opus-5-5')
        self.assertEqual(json.loads(Path(self.pi + '.asked').read_text())['customInstructions'], 'keep the setlist')
        self.assertEqual([e['type'] for e in events], ['turn_started', 'compaction', 'turn_ended'])
        self.assertEqual((events[1]['trigger'], events[1]['summary']), ('manual', 'Kept: the setlist.'))
        self.assertEqual((events[2]['outcome'], events[2]['cost_usd']), ('completed', 0.03))
        self.assertEqual(self.waker.last_result['local_command'], 'compact')

    def test_one_pi_wont_do_is_a_failed_turn_saying_why(self):
        events = []
        self.waker.wake(SESSION, '/compact too small', new_session_id='new-2', on_event=events.append)
        self.assertEqual([e['type'] for e in events], ['turn_started', 'turn_ended'])
        self.assertEqual(events[-1]['outcome'], 'failed')
        self.assertIn('Nothing to compact', events[-1]['error'])
        self.assertEqual(self.waker.last_result['subtype'], 'error')

    def test_an_ordinary_wake_that_mentions_compact_is_a_turn(self):
        self.assertEqual(self.waker.command(SESSION, 'n', 'You were woken: /compact re keep it')[-1],
                         'You were woken: /compact re keep it')
        self.assertIsNone(__import__('poller.pi_harness', fromlist=['COMPACT']).COMPACT.match('woken: /compact x'))


class LoopGuardTest(PlainCase):
    """The tripwire both wakers share: three identical calls is a runaway;
    text between the repeats doesn't help (the 28 Oct specimen had escape text every few seconds).
    Also checks alternating loops and max call ceiling."""

    def test_three_identical_calls_trip_it(self):
        guard = LoopGuard()
        self.assertEqual(guard.see('read', {'path': 'x'}), '')
        self.assertEqual(guard.see('read', {'path': 'x'}), '')
        self.assertIn('tool loop: read called 3×', guard.see('read', {'path': 'x'}))

    def test_a_different_call_breaks_the_streak(self):
        guard = LoopGuard()
        guard.see('read', {'path': 'x'})
        guard.see('read', {'path': 'x'})
        self.assertEqual(guard.see('read', {'path': 'y'}), '')  # the different one: 1
        self.assertEqual(guard.see('read', {'path': 'x'}), '')  # 1 again, y broke it
        self.assertEqual(guard.see('read', {'path': 'x'}), '')  # 2
        self.assertIn('tool loop', guard.see('read', {'path': 'x'}))  # 3

    def test_text_between_repeats_does_not_break_the_streak(self):
        # The 28 Oct specimen said "let me check that again" between every repeat: it still loops.
        guard = LoopGuard()
        for _ in range(2):
            self.assertEqual(guard.see('read', {'path': 'x'}), '')
        self.assertIn('tool loop', guard.see('read', {'path': 'x'}))  # the guard never sees the text

    def test_the_same_tool_with_different_args_is_not_a_loop(self):
        guard = LoopGuard()
        for i in range(5):
            self.assertEqual(guard.see('read', {'path': f'f{i}'}), '')

    def test_alternating_calls_trip_sliding_window(self):
        guard = LoopGuard(window_size=10, window_limit=5)
        calls = [('grep', {'path': 'x'}), ('read', {'path': 'y'})] * 4  # 8 calls: 4 grep, 4 read
        for name, inp in calls:
            self.assertEqual(guard.see(name, inp), '')
        # 9th call: 5th grep in 9 calls
        self.assertIn('tool loop: grep called 5× within last 9 calls', guard.see('grep', {'path': 'x'}))

    def test_total_tool_calls_cap_trips(self):
        guard = LoopGuard(max_calls=10)
        for i in range(9):
            self.assertEqual(guard.see(f'tool_{i}', {'path': str(i)}), '')
        self.assertIn('maximum tool calls limit (10)', guard.see('tool_9', {'path': '9'}))


# A pi stuck in a machine attractor, as re's was on 28 Oct: the same read over and over,
# with escape text every time, until something kills it.
FAKE_LOOP_PI = r'''#!PYTHON
import json, sys, time

lines = [
    {'type': 'session', 'version': 3, 'id': 'x', 'timestamp': '2026-10-30T03:00:00.000Z'},
    {'type': 'turn_start'},
]

mode = sys.argv[-1]
if 'alternating' in mode:
    for i in range(10):
        tool = 'grep' if i % 2 == 0 else 'read'
        lines.append({'type': 'message_end', 'message': {'role': 'assistant', 'stopReason': 'toolUse', 'usage': {},
                      'content': [{'type': 'toolCall', 'id': f'c{i}', 'name': tool, 'arguments': {'path': 'x'}}]}})
elif 'normal' in mode:
    for i in range(3):
        lines.append({'type': 'message_end', 'message': {'role': 'assistant', 'stopReason': 'toolUse', 'usage': {},
                      'content': [{'type': 'toolCall', 'id': f'c{i}', 'name': 'read', 'arguments': {'path': f'f{i}'}}]}})
    lines.append({'type': 'message_end', 'message': {'role': 'assistant', 'stopReason': 'stop', 'usage': {},
                  'content': [{'type': 'text', 'text': 'Done reading.'}]}})
    lines.append({'type': 'agent_settled', 'aborted': False})
    for line in lines:
        print(json.dumps(line), flush=True)
    sys.exit(0)
else:
    call = {'type': 'message_end', 'message': {'role': 'assistant', 'stopReason': 'toolUse', 'usage': {},
            'content': [{'type': 'text', 'text': 'Let me check that file again.'},
                        {'type': 'toolCall', 'id': 'c1', 'name': 'read', 'arguments': {'path': 'x'}}]}}
    for _ in range(3):
        lines.append(call)

for line in lines:
    print(json.dumps(line), flush=True)

time.sleep(60)  # until the tripwire kills us
'''


class LoopKillTest(PlainCase):
    """The tripwire in PiWaker.wake: by the third identical call, the run is dead."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.pi = os.path.join(self.dir, 'pi')
        Path(self.pi).write_text(FAKE_LOOP_PI.replace('PYTHON', sys.executable))
        os.chmod(self.pi, 0o755)
        sessions = Path(self.dir, 's')
        sessions.mkdir()
        (sessions / f'2026-10-18T15-00-00-000Z_{SESSION}.jsonl').write_text(
            json.dumps({'type': 'session', 'version': 3, 'id': SESSION, 'cwd': self.dir}) + '\n')
        self.waker = PiWaker(pi=self.pi, model='opus', session_dir=str(sessions), new_cwd=self.dir)

    def test_a_tool_loop_is_killed_and_said_so(self):
        import time
        start = time.time()
        events = []
        with self.assertRaises(RuntimeError) as ctx:
            self.waker.wake(SESSION, 'p', new_session_id='new-9', on_event=events.append)
        self.assertIn('tool loop: read called 3× with identical arguments', str(ctx.exception))
        self.assertIn('tool loop', self.waker.last_result['error'])
        # The record keeps what the loop did: every call, escape text and all, plus loop notice
        self.assertEqual([e['type'] for e in events],
                         ['turn_started'] + ['assistant_message', 'tool_call'] * 3 + ['assistant_message'])
        self.assertIn('⚠️ Turn stopped by loop tripwire', events[-1]['text'])
        self.assertLess(time.time() - start, 10)  # killed, not slept out

    def test_a_tool_loop_that_yields_nothing_still_names_itself(self):
        # A run the poller sees nothing of but the kill: the error names the loop anyway.
        with self.assertRaises(RuntimeError) as ctx:
            self.waker.wake(SESSION, 'p', new_session_id='new-9')
        self.assertIn('tool loop', str(ctx.exception))
        self.assertEqual(self.waker.last_result['subtype'], 'error')

    def test_alternating_tool_calls_trip_sliding_window_tripwire(self):
        events = []
        with self.assertRaises(RuntimeError) as ctx:
            self.waker.wake(SESSION, 'alternating loop test', new_session_id='new-10', on_event=events.append)
        self.assertIn('called 5× within last', str(ctx.exception))
        self.assertIn('tool loop', self.waker.last_result['error'])

    def test_different_tool_calls_do_not_trip_tripwire(self):
        events = []
        _, reply = self.waker.wake(SESSION, 'normal test', new_session_id='new-11', on_event=events.append)
        self.assertEqual(reply, 'Done reading.')
        self.assertEqual(self.waker.last_result['subtype'], 'success')


@override_settings(MOOD_RUNNER_KEYS={'re': KEY}, TOOL_RESULT_CONTENT_CHARS=20000)
class EndToEndTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        ThinkingEntity.objects.get_or_create(name='re', defaults={'is_biological_human': False})
        cls.mood = Mood.objects.create(slug='docs-tests-qa')

    def test_a_pi_turn_reaches_the_mood_as_re(self):
        _, events = translated()
        client = self.client

        class Through:  # the poster's HTTP, through Django's test client
            @staticmethod
            def post(url, json=None, headers=None, timeout=None):
                return client.post(url.replace('http://ml', ''), data=__import__('json').dumps(json),
                                   content_type='application/json', HTTP_AUTHORIZATION=headers['Authorization'])

        poster = EventsPoster('http://ml', KEY, 'docs-tests-qa', SESSION, harness='pi 1.1.0', http=Through)
        poster.send(events)  # here, not on the poster's thread: a test's database is this thread's
        poster.close()
        self.assertEqual((poster.sent, poster.failed, poster.errors), (len(events), 0, []))

        rows = Message.objects.filter(session_id=SESSION)
        self.assertEqual(rows.get(sender_id='mood-poller').mood, self.mood)
        self.assertEqual(ToolUse.objects.get(session_id=SESSION).tool_name, 'read')
        self.assertIn('banjo', ToolResult.objects.get(session_id=SESSION).content)
        said = rows.filter(sender_id='re').exclude(tooluse__isnull=False).get()
        self.assertEqual((said.client_version, said.model_backend), ('pi 1.1.0', 'qwen2.5-1.5b-instruct-q4_k_m'))
        self.assertEqual(said.stop_reason, 'end_turn')
        turns = self.client.get('/api/moods/docs-tests-qa/turns/').json()
        self.assertEqual(len(turns['turns']), 1)
        self.assertEqual([s['tool'] for s in turns['steps']], ['read'])

    def test_every_call_counts_with_what_pi_says_it_cost(self):
        """A response that's only thinking and a tool call still carries its usage (on its last
        block), and Pi's own price for it: the turn's head counts every call, and its cost."""
        t = PiTranslator(SESSION, turn='t2')
        usage = {'input': 900, 'output': 120, 'cacheRead': 40000, 'cacheWrite': 0, 'cost': {'total': 0.0123}}
        events = t.feed({'type': 'message_end', 'message': {
            'role': 'assistant', 'timestamp': 1760800000000, 'model': 'deepseek/deepseek-v4-pro', 'usage': usage,
            'stopReason': 'toolUse', 'content': [{'type': 'thinking', 'thinking': 'Look first.'},
                                                 {'type': 'toolCall', 'id': 'c1', 'name': 'grep', 'arguments': {}}]}})
        self.assertNotIn('usage', events[0])
        self.assertEqual(events[1]['usage']['cost'], 0.0123)
        self.assertEqual(t.usage['output'], 120)  # the turn's sums stay token counts

        client = self.client

        class Through:
            @staticmethod
            def post(url, json=None, headers=None, timeout=None):
                return client.post(url.replace('http://ml', ''), data=__import__('json').dumps(json),
                                   content_type='application/json', HTTP_AUTHORIZATION=headers['Authorization'])

        poster = EventsPoster('http://ml', KEY, 'docs-tests-qa', COST_SESSION, harness='pi 1.1.0', http=Through)
        poster.send([t.event('turn_started', '2026-10-18T15:00:00+00:00'), *events])
        poster.close()
        step = ToolUse.objects.get(session_id=COST_SESSION)
        self.assertEqual((step.output_tokens, step.cache_read_input_tokens, step.cost_usd), (120, 40000, 0.0123))
        shown = self.client.get('/api/moods/docs-tests-qa/turns/').json()['steps'][0]
        self.assertEqual((shown['out'], shown['ctx'], shown['usd'], shown['paid']), (120, 40900, 0.0123, True))

    def test_a_routed_call_is_recorded_as_what_ran_at_what_openrouter_billed(self):
        """A router ('openrouter/pareto-code') is priced at $0 in Pi's list; OpenRouter ran Fable 5.1 and
        billed $6.11 (magent, 28 Oct). The record keeps the model that ran and the bill, and the turn's end
        says the bill's sum."""
        t = PiTranslator(SESSION, turn='t3')
        usage = {'input': 610065, 'output': 156, 'cacheRead': 0, 'cacheWrite': 0, 'cost': {'total': 0}}
        said = t.feed({'type': 'message_end', 'message': {
            'role': 'assistant', 'timestamp': 1793193797026, 'provider': 'openrouter',
            'model': 'openrouter/pareto-code', 'responseModel': 'anthropic/claude-fable-5.1',
            'responseId': 'gen-1793193797-x', 'usage': usage, 'stopReason': 'stop',
            'content': [{'type': 'text', 'text': 'Yes.'}]}})
        self.assertEqual(said[0]['model'], 'anthropic/claude-fable-5.1')
        self.assertEqual((said[0]['usage']['generation'], said[0]['usage']['cost']), ('gen-1793193797-x', None))
        ended = t.feed({'type': 'agent_settled'})

        asked = []

        class OpenRouter:
            @staticmethod
            def get(url, params=None, headers=None, timeout=None):
                asked.append(params['id'])
                return type('R', (), {'status_code': 404 if len(asked) == 1 else 200,
                                      'json': lambda self: {'data': {'total_cost': 6.10845}}})()

        poster = EventsPoster('http://ml', KEY, 'docs-tests-qa', COST_SESSION, http=OpenRouter, pause=0,
                              openrouter_key='or-key')
        events = said + ended
        poster.bill(events)
        poster.close()
        self.assertEqual(asked, ['gen-1793193797-x'] * 2)  # not ready at first, then priced
        self.assertEqual((events[0]['usage']['cost'], events[0]['usage']['billed']), (6.10845, True))
        self.assertEqual(events[-1]['cost_usd'], 6.10845)
        self.assertEqual(poster.harness, 'pi · openrouter')  # who billed it, on the rows that follow

        from conversations.services.mood_view import call_usd, model_label
        self.assertEqual(model_label('anthropic/claude-fable-5.1'), 'Fable 5.1')
        self.assertEqual(call_usd('anthropic/claude-fable-5.1', 610065, 156, 0, 0),
                         call_usd('claude-fable-5-1', 610065, 156, 0, 0))

    def test_without_an_openrouter_key_pis_figure_stands(self):
        t = PiTranslator(SESSION, turn='t4')
        said = t.feed({'type': 'message_end', 'message': {
            'role': 'assistant', 'timestamp': 1793193797027, 'provider': 'openrouter', 'model': 'deepseek/deepseek-v4-pro',
            'responseModel': 'deepseek/deepseek-v4-pro', 'responseId': 'gen-y', 'stopReason': 'stop',
            'usage': {'input': 9, 'output': 1, 'cost': {'total': 0.002}}, 'content': [{'type': 'text', 'text': 'k'}]}})
        poster = EventsPoster('http://ml', KEY, 'docs-tests-qa', COST_SESSION, http=None, openrouter_key='')
        poster.bill(said)
        poster.close()
        self.assertEqual(said[0]['usage']['cost'], 0.002)

    def test_a_router_is_neither_offered_nor_run(self):
        """OpenRouter's routers pick any model at its price: pareto-code picked Fable 5.1, $265 (28 Oct)."""
        from poller.pi_harness import is_router
        for model in ('openrouter/auto', 'openrouter/openrouter/pareto-code', 'auto', 'openrouter/openrouter/auto-beta'):
            self.assertTrue(is_router(model), model)
        for model in ('openrouter/deepseek/deepseek-v4-pro', 'anthropic/claude-opus-5-5', 'opus', '',
                      'openrouter/~deepseek/deepseek-pro-latest'):
            self.assertFalse(is_router(model), model)
        waker = PiWaker(pi='pi', session_dir=tempfile.mkdtemp())
        with self.assertRaisesRegex(RuntimeError, 'router'):
            waker.command(None, 's1', 'hi', model='openrouter/openrouter/pareto-code')
        self.assertIn('--model', waker.command(None, 's1', 'hi', model='openrouter/deepseek/deepseek-v4-pro'))

    def test_a_wake_that_failed_closes_its_turn(self):
        client, posted = self.client, []

        class Capture:
            @staticmethod
            def post(url, json=None, headers=None, timeout=None):
                posted.append(json)
                return client.post(url.replace('http://ml', ''), data=__import__('json').dumps(json),
                                   content_type='application/json', HTTP_AUTHORIZATION=headers['Authorization'])

        poster = EventsPoster('http://ml', KEY, 'docs-tests-qa', SESSION, http=Capture)
        poster.thread = None  # read the queue here instead
        poster.queue = __import__('queue').Queue()
        poster.answers = ['5f0c1a52-0000-4000-8000-000000000001']  # what woke it: said, as Claude Code's poster says it
        poster.put({'type': 'result', 'subtype': 'error_during_execution', 'is_error': True})
        poster.send([poster.queue.get_nowait()])
        self.assertEqual(posted[0]['events'][0]['type'], 'turn_ended')
        self.assertEqual(posted[0]['answers'], ['5f0c1a52-0000-4000-8000-000000000001'])
        self.assertTrue(Message.objects.filter(session_id=SESSION, content__type='turn-result').exists())
