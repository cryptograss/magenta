"""Spend by kind of turn, on the status page (conversations/services/spend_kinds.py)."""

import json
import uuid

from conversations.models import ConversationParticipant, Message, Thought, ToolUse
from conversations.services import spend_kinds
from conversations.tests.tidings_helpers import TidingsTests

FULL = 'This turn has your full tools, as in your terminal.'
LOOK = 'This turn can look but not touch.'


class SpendKindsTest(TidingsTests):

    def setUp(self):
        super().setUp()
        self.poller, _ = ConversationParticipant.objects.get_or_create(name='mood-poller',
                                                                       defaults={'participant_type': 'system'})

    def wake(self, session, mood, opening, ending, sender=None):
        message = self.post(sender or self.poller, mood, f'{opening}\nsomething said\n{ending}\n</mood-wake>')
        Message.objects.filter(id=message.id).update(session_id=session)

    def response(self, session, mood, *rows, fresh=1_000_000, out=1000, cost=None, agent=None, helper=False):
        """One model response: `rows` of (kind, characters), each repeating the response's usage."""
        for kind, chars in rows:
            content = {'thinking': [{'type': 'thinking', 'thinking': 'x' * chars}],
                       'words': [{'type': 'text', 'text': 'x' * chars}], 'tools': {'cmd': 'x' * chars}}[kind]
            model = {'thinking': Thought, 'words': Message, 'tools': ToolUse}[kind]
            self.clock = self.clock.__class__.fromtimestamp(self.clock.timestamp() + 1, self.clock.tzinfo)
            extra = {'tool_name': 'bash', 'tool_id': str(uuid.uuid4())} if kind == 'tools' else {}
            row = model.objects.create(id=uuid.uuid4(), sender=agent or self.magent, mood=mood, content=content,
                                       session_id=session, model_backend='claude-opus-5-5', input_tokens=fresh,
                                       output_tokens=out, cache_read_input_tokens=0, cache_creation_input_tokens=0,
                                       cost_usd=cost, timestamp=int(self.clock.timestamp() * 1000),
                                       is_sidechain=helper, **extra)
            Message.objects.filter(id=row.id).update(created_at=self.clock)

    def kinds(self, **kw):
        return {k['kind']: k for k in spend_kinds.spend_by_kind(**kw)['kinds']}

    def test_each_turn_is_its_wakes_kind_and_each_response_counts_once(self):
        a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        self.wake(a, self.mood_a, '<mood-wake mood="magenta-interface">', FULL)
        self.response(a, self.mood_a, ('thinking', 0), ('words', 400), ('tools', 400))  # one response, three rows
        self.wake(b, self.mood_a, '<mood-wake mood="magenta-interface">', LOOK)
        self.response(b, self.mood_a, ('words', 40))
        self.wake(c, self.mood_b, '<mood-wake mood="docs-tests-qa" reason="consider">', LOOK)
        self.response(c, self.mood_b, ('words', 40), cost=0.25)
        got = self.kinds()
        full = got['mention-full']
        self.assertEqual((full['turns'], full['calls'], full['ctx'], full['usd']), (1, 1, 1_000_000, 4.02))
        # Claude Code keeps no thinking text: words and tools at 4 characters a token, thinking the rest.
        self.assertEqual(full['words'], 100)
        self.assertAlmostEqual(full['tools'], 103, delta=2)  # the call's whole input, its keys too
        self.assertEqual(full['thinking'], 1000 - full['words'] - full['tools'])
        self.assertEqual(got['mention-look']['calls'], 1)
        self.assertEqual((got['consider']['turns'], got['consider']['usd']), (1, 0.25))
        self.assertEqual(set(self.kinds(mood='docs-tests-qa')), {k for k, _ in spend_kinds.KINDS} - {'other', 'unknown'})
        self.assertEqual(self.kinds(mood='docs-tests-qa')['consider']['calls'], 1)
        self.assertEqual(self.kinds(mood='docs-tests-qa')['mention-full']['calls'], 0)

    def test_a_wake_filed_as_the_person_from_a_transcript_still_counts(self):
        """A transcript's prompt opens with the harness's reminders, and is filed as the container's person."""
        s = uuid.uuid4()
        self.wake(s, self.mood_a, '<system-reminder>…</system-reminder>\n<mood-wake mood="m" reason="quiet">', LOOK,
                  sender=self.justin)
        self.response(s, self.mood_a, ('words', 40))
        self.assertEqual(self.kinds()['quiet']['calls'], 1)

    def test_a_turn_with_no_wake_is_said_so(self):
        self.response(uuid.uuid4(), self.mood_a, ('words', 40))
        self.assertEqual(self.kinds()['unknown']['calls'], 1)

    def test_where_the_thinking_was_kept_the_split_is_by_its_characters(self):
        self.assertEqual(spend_kinds.split_output(100, {'thinking': 300, 'words': 100}),
                         {'thinking': 75.0, 'words': 25.0, 'tools': 0.0})
        # Words and tools never claim more than the response wrote.
        self.assertEqual(spend_kinds.split_output(10, {'words': 400}), {'thinking': 0.0, 'words': 10.0, 'tools': 0.0})

    def test_a_screen_is_counted_with_what_it_cost(self):
        from conversations.tests.test_mood_runner import AUTH  # noqa: F401 -- the runner's key, as the poller has it
        Message.objects.create(id=uuid.uuid4(), sender=self.magent, mood=self.mood_a, content='<silent by="screen">small talk</silent>',
                               source_file='ingest-mood-runner', stop_reason='end_turn', cost_usd=0.002)
        Message.objects.create(id=uuid.uuid4(), sender=self.magent, mood=self.mood_a, content='<silent by="magent">mine</silent>',
                               source_file='ingest-mood-runner', stop_reason='end_turn')
        screened = self.kinds()['screened']
        self.assertEqual((screened['turns'], screened['usd'], screened['unpriced']), (1, 0.0, 0))

    def test_the_api_answers(self):
        answered = self.client.get('/api/usage/kinds/?hours=999&mood=docs-tests-qa').json()
        self.assertEqual((answered['hours'], answered['mood']), (168, 'docs-tests-qa'))
        self.assertEqual(json.loads(json.dumps(answered))['total']['calls'], 0)

    def test_it_counts_as_the_prompts_page_does_a_helpers_calls_too(self):
        """re's review of #243: a helper's (sidechain) calls were on the prompts page and not here."""
        from conversations.services import usage
        s = uuid.uuid4()
        self.wake(s, self.mood_a, '<mood-wake mood="magenta-interface">', FULL)
        self.response(s, self.mood_a, ('words', 40))
        self.response(s, self.mood_a, ('words', 40), fresh=2_000_000, helper=True)
        here = spend_kinds.spend_by_kind()['total']
        there = usage.usage_state(24)['totals']
        self.assertEqual((here['calls'], here['usd']), (2, 12.04))
        self.assertEqual(here['usd'], round(sum(t['usd'] for t in there.values()), 2))

    def test_a_pi_response_is_its_blocks_together(self):
        """Pi puts a response's usage on its last block: the thinking before it is the same response's."""
        s = uuid.uuid4()
        self.wake(s, self.mood_a, '<mood-wake mood="magenta-interface">', FULL)
        Thought.objects.create(id=uuid.uuid4(), sender=self.magent, mood=self.mood_a, session_id=s,
                               content=[{'type': 'thinking', 'thinking': 'x' * 300}], model_backend='claude-opus-5-5')
        self.response(s, self.mood_a, ('words', 100), out=100)
        full = self.kinds()['mention-full']
        self.assertEqual((full['calls'], full['thinking'], full['words']), (1, 75, 25))
