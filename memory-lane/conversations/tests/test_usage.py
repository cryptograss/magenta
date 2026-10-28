"""The usage section of the prompts page and /api/usage/ (conversations/services/usage.py)."""

import uuid

from conversations.models import Message
from conversations.services import usage
from conversations.tests.tidings_helpers import TidingsTests


class UsageTest(TidingsTests):

    def call(self, agent, mood, session, *, model='claude-opus-5-5', client='2.1.311', fresh=1000, read=0, out=10,
             cost=None, rows=1):
        """One model response, stored as `rows` rows that repeat its usage (thinking, words, a tool call)."""
        for _ in range(rows):
            message = self.agent_row(agent, mood)
            Message.objects.filter(id=message.id).update(
                session_id=session, model_backend=model, client_version=client, input_tokens=fresh,
                cache_read_input_tokens=read, cache_creation_input_tokens=0, output_tokens=out, cost_usd=cost)

    def test_a_response_on_several_rows_is_one_call_and_each_harness_is_priced_its_own_way(self):
        turn_a, turn_b = uuid.uuid4(), uuid.uuid4()
        # Claude Code on Opus 5.5, one response on three rows: priced from the list.
        self.call(self.magent, self.mood_a, turn_a, fresh=1_000_000, out=0, rows=3)
        # Pi routed to Fable 5.1: OpenRouter's bill.
        self.call(self.re, self.mood_b, turn_b, model='anthropic/claude-fable-5.1', client='pi 1.1.0',
                  fresh=610_065, out=156, cost=6.10845)
        state = usage.usage_state(24)
        self.assertEqual(state['totals']['anthropic']['calls'], 1)
        self.assertEqual(state['totals']['anthropic']['usd'], 4.0)  # 1M fresh at $4/M
        self.assertEqual(state['totals']['anthropic']['paid_usd'], 0.0)
        self.assertEqual((state['totals']['openrouter']['usd'], state['totals']['openrouter']['paid_usd']),
                         (6.11, 6.11))
        self.assertEqual(state['models'], ['Opus 5.5', 'Fable 5.1'])
        top = state['top']['openrouter'][0]
        self.assertEqual((top['agent'], top['mood'], top['models'], top['usd'], top['paid']),
                         ('re', 'docs-tests-qa', ['Fable 5.1'], 6.1085, True))
        self.assertEqual(state['top']['anthropic'][0]['turn'], str(turn_a))

    def test_slices_group_by_hour_agent_mood_model_and_harness(self):
        self.call(self.magent, self.mood_a, uuid.uuid4())
        self.call(self.magent, self.mood_a, uuid.uuid4(), out=20)
        state = usage.usage_state(24)
        self.assertEqual(state['unit'], 'hour')
        self.assertEqual(len(state['buckets']), 25)
        (slice_,) = state['slices']
        self.assertEqual(slice_[1:9], ['magent', 'magenta-interface', 'Opus 5.5', 'anthropic', 2, 2000, 0, 30])

    def test_the_api_answers_and_an_odd_range_is_a_day(self):
        self.call(self.magent, self.mood_a, uuid.uuid4())
        answered = self.client.get('/api/usage/?hours=5').json()
        self.assertEqual(answered['hours'], 24)
        self.assertEqual(self.client.get('/api/usage/?hours=168').json()['unit'], 'day')
