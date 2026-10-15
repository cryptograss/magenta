"""A real Pi turn, translated by the runner and posted as record events,
becomes the same kind of rows any turn does (poller/pi_harness.py)."""

import json
import uuid
from pathlib import Path

from django.test import TestCase, override_settings

from conversations.models import Message, Mood, ThinkingEntity, ToolResult, ToolUse
from poller.pi_harness import EventsPoster, PiTranslator, PiWaker

FIXTURE = Path(__file__).with_name('fixtures') / 'pi-1.1.0-turn.jsonl'  # Pi 1.1.0, local model, 2026-10-11
KEY = 'k' * 64
SESSION = '01a12c78-a26d-70bf-9a54-ad9e286ba1d3'


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
        self.assertEqual(cmd[cmd.index('--tools') + 1], 'read,grep,find,ls')
        self.assertEqual(cmd[cmd.index('--thinking') + 1], 'xhigh')
        self.assertNotIn('--tools', waker.command(None, 'new', 'hi', full=True))
        self.assertEqual(cmd[-1], 'hi')


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
        poster.put({'type': 'result', 'subtype': 'error_during_execution', 'is_error': True})
        poster.send([poster.queue.get_nowait()])
        self.assertEqual(posted[0]['events'][0]['type'], 'turn_ended')
        self.assertTrue(Message.objects.filter(session_id=SESSION, content__type='turn-result').exists())
