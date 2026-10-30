"""An agent's tool steps in the Mood view, typing, and paging back."""

import json
import uuid

from django.core.cache import cache
from django.test import TestCase

from conversations.models import ConversationParticipant, Message, Mood, ThinkingEntity, ToolResult, ToolUse
from conversations.views_moods import PAGE


class StepsTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.justin = ThinkingEntity.objects.create(name='justin', is_biological_human=True)
        cls.magent = ThinkingEntity.objects.create(name='magent', is_biological_human=False)
        cls.tool = ConversationParticipant.objects.create(name='tool-result', participant_type='tool')
        cls.mood = Mood.objects.create(slug='m26')
        cls.session = uuid.uuid4()

    def add(self, sender, content, model=Message, **fields):
        return model.objects.create(id=uuid.uuid4(), sender=sender, mood=self.mood, content=content,
                                    timestamp=1, session_id=self.session, **fields)

    def get(self, **params):
        return self.client.get('/api/moods/m26/turns/', params).json()

    def test_steps_come_separately_so_a_tool_call_is_never_an_answer(self):
        self.add(self.justin, '@magent check it')
        self.add(self.magent, {'command': 'ls', 'description': 'List files'}, model=ToolUse,
                 tool_name='Bash', tool_id='t1')
        self.add(self.tool, 'a\nb', model=ToolResult, tool_use_id='t1')
        self.add(self.magent, [{'type': 'text', 'text': 'two files'}])
        body = self.get()
        self.assertEqual([t['sender'] for t in body['turns']], ['justin', 'magent'])
        self.assertEqual([(s['tool'], s['verb'], s['summary']) for s in body['steps']], [('Bash', 'ran', 'List files')])

    def test_a_choice_not_to_speak_is_a_dot_not_a_turn(self):
        self.add(self.justin, '@magent anything?')
        self.add(self.magent, [{'type': 'text', 'text': '<silent/>'}])
        self.add(self.magent, [{'type': 'text', 'text': '<silent>they are sorting out the gig</silent>'}])
        body = self.get()
        self.assertEqual([t['sender'] for t in body['turns']], ['justin'])  # the poller reads turns as answers
        self.assertEqual([q['reason'] for q in body['quiet']], ['', 'they are sorting out the gig'])

    def test_thinking_the_harness_kept_shows_and_empty_thinking_doesnt(self):
        from conversations.models import Thought
        self.add(self.magent, [{'type': 'thinking', 'thinking': 'That resonates. Planning the work.',
                                'signature': 'x'}], model=Thought, signature='x')
        self.add(self.magent, [{'type': 'thinking', 'thinking': '', 'signature': 'y'}], model=Thought, signature='y')
        body = self.get()
        self.assertEqual([t['text'] for t in body['thoughts']], ['That resonates. Planning the work.'])
        self.assertEqual(body['turns'], [])  # a thought is never an answer

    def test_a_compaction_summary_is_folded_never_a_turn_or_a_mention(self):
        # As stored before the importer knew the string form: under the
        # container's person, which made it look like their words.
        summary = ('This session is being continued from a previous conversation that ran out of context.\n\n'
                   'Summary:\n- **Goal:** make the Mood home; @magent to build it.')
        self.add(self.justin, summary)
        self.add(self.magent, summary)  # as stored from now on
        self.add(self.justin, '@magent and now?')
        body = self.get()
        self.assertEqual([t['text'] for t in body['turns']], ['@magent and now?'])
        self.assertEqual(len(body['compactions']), 2)
        self.assertIn('<strong>Goal:</strong>', body['compactions'][0]['html'])
        mentions = self.client.get('/api/mentions/magent/').json()['mentions']
        self.assertEqual([m['turn']['text'] for m in mentions], ['@magent and now?'])

    def test_answers_are_compressed_for_a_browser_that_asks(self):
        for i in range(40):
            self.add(self.justin, f'line {i}: what time do we load the bus, and who has the capo?')
        plain = self.client.get('/api/moods/m26/turns/')
        packed = self.client.get('/api/moods/m26/turns/', HTTP_ACCEPT_ENCODING='gzip')
        self.assertEqual(packed['Content-Encoding'], 'gzip')
        self.assertLess(len(packed.content), len(plain.content) / 3)

    def test_a_step_opens_to_its_input_and_result(self):
        step = self.add(self.magent, {'command': 'false'}, model=ToolUse, tool_name='Bash', tool_id='t9')
        self.add(self.tool, 'exit 1', model=ToolResult, tool_use_id='t9', is_error=True)
        detail = self.client.get(f'/api/moods/m26/steps/{step.id}/').json()
        self.assertEqual(detail['input'], {'command': 'false'})
        self.assertEqual(detail['result'], {'text': 'exit 1', 'is_error': True})

    def test_a_step_is_only_found_in_its_own_mood(self):
        other = Mood.objects.create(slug='elsewhere')
        step = ToolUse.objects.create(id=uuid.uuid4(), sender=self.magent, mood=other, content={},
                                      timestamp=1, tool_name='Bash', tool_id='t2')
        self.assertEqual(self.client.get(f'/api/moods/m26/steps/{step.id}/').status_code, 404)
        self.assertEqual(self.client.get('/api/moods/m26/steps/not-a-uuid/').status_code, 404)

    def test_a_first_load_is_the_newest_page_and_earlier_pages_follow(self):
        for i in range(PAGE + 5):
            self.add(self.justin, f'line {i}')
        first = self.get()
        self.assertEqual(len(first['turns']), PAGE)
        self.assertEqual(first['turns'][-1]['text'], f'line {PAGE + 4}')
        self.assertTrue(first['has_earlier'])
        back = self.get(before=first['turns'][0]['id'])
        self.assertEqual([t['text'] for t in back['turns']], [f'line {i}' for i in range(5)])
        self.assertFalse(back['has_earlier'])

    def test_a_window_from_a_message_is_one_page_forward_not_the_whole_history(self):
        # Justin, 30 Oct: a link to an ancient message fetched everything since it (?from= without a cap)
        # and the "finding it" mask sat half a minute. from+limit is now the first N items *at* that
        # message, inclusive, so a reveal is one small page wherever the message sits.
        for i in range(PAGE * 2 + 7):
            self.add(self.justin, f'line {i}')
        target = self.get()['turns'][0]['id']  # the oldest drawn on the first load
        win = self.get(**{'from': target, 'limit': 20})
        self.assertEqual(len(win['turns']), 20)
        self.assertEqual(win['turns'][0]['id'], target)  # the message itself is first
        self.assertTrue(win['has_earlier'])
        # All of it, uncapped, is still there when only ?from= is asked (the runner's read).
        self.assertEqual(len(self.get(**{'from': target})['turns']), PAGE)

    def test_earlier_page_does_not_starve_turns_when_agent_has_many_steps(self):
        # Justin, 30 Oct: hundreds of steps of an emergent loop returned 0 readable turns.
        # timeline() now continues scanning through steps to ensure readable turns arrive.
        self.add(self.justin, 'the topic before the steps')
        for i in range(60):
            self.add(self.magent, {'command': f'step {i}'}, model=ToolUse, tool_name='Bash', tool_id=f't_{i}')
        self.add(self.justin, 'after the steps')
        # Request with limit=50 (smaller than the 60 steps)
        page = self.get(limit=50)
        turn_texts = [t['text'] for t in page['turns']]
        self.assertIn('the topic before the steps', turn_texts)
        self.assertIn('after the steps', turn_texts)

    def test_a_poll_after_an_id_is_everything_since(self):
        first = self.add(self.justin, 'one')
        self.add(self.justin, 'two')
        self.assertEqual([t['text'] for t in self.get(after=first.id)['turns']], ['two'])


class TypingTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        ThinkingEntity.objects.create(name='skyler', is_biological_human=True)
        Mood.objects.create(slug='m26')

    def setUp(self):
        cache.clear()

    def test_typing_needs_a_device_and_shows_in_the_turns(self):
        from unittest import mock
        device = mock.Mock(entity_id='skyler')
        self.assertEqual(self.client.post('/api/moods/m26/typing/', '{}', content_type='application/json').status_code, 401)
        with mock.patch('conversations.services.mood_auth.device_for', return_value=device):
            self.client.post('/api/moods/m26/typing/', json.dumps({'typing': True}), content_type='application/json')
            self.assertEqual(self.client.get('/api/moods/m26/turns/').json()['typing'], ['skyler'])
            self.client.post('/api/moods/m26/typing/', json.dumps({'typing': False}), content_type='application/json')
        self.assertEqual(self.client.get('/api/moods/m26/turns/').json()['typing'], [])
