"""An agent's instructions from PickiPedia, given to each turn as system prompt (poller/instructions.py): for an
agent whose poller is run with --instructions (blu first), never for one whose instructions live elsewhere yet."""

import json
import tempfile
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

from conversations.tests.test_mood_poller import FakeAPI, FakeWaker
from poller.mood_poller import ClaudeCodeWaker, MoodPoller
from poller.pi_harness import PiWaker

RULES = '<instructions agent="blu" mood="m26">\n<identity agent="blu">You are blu, the blue agent.</identity>\n</instructions>'


class RulesPromptTest(SimpleTestCase):

    def poller(self, **kwargs):
        state = Path(tempfile.mkdtemp()) / 'state.json'
        state.write_text(json.dumps({'since': '2026-10-21T00:00:00+00:00', 'handled': [], 'wakes': []}))
        waker = FakeWaker()
        return MoodPoller(FakeAPI([]), waker, agent='blu', state_path=state, consider=False, **kwargs), waker

    def test_each_turn_is_given_the_agents_instructions_for_that_mood(self):
        poller, waker = self.poller(instructions=True)
        with mock.patch('poller.instructions.bundle', return_value=mock.Mock(text=RULES, **{'record.return_value': {
                'id': 'r-1', 'parts': [RULES], 'sha256': '0' * 64, 'stamps': {}}})) as gathered:
            poller.run_turn_for('m26', 's-local', 'You were woken')
        gathered.assert_called_once_with('blu', 'm26', record_id=mock.ANY, others=[])  # no block: it'd change every turn
        self.assertEqual(waker.options['system'], RULES)

    def test_without_the_flag_nothing_is_added(self):
        poller, waker = self.poller()
        with mock.patch('poller.instructions.bundle') as gathered:
            poller.run_turn_for('m26', 's-local', 'You were woken')
        gathered.assert_not_called()
        self.assertNotIn('system', waker.options)

    def test_never_gathered_at_all_the_recovery_instructions_stand(self):
        poller, waker = self.poller(instructions=True)
        with mock.patch('poller.instructions.bundle', side_effect=RuntimeError('cache unreadable')):
            poller.run_turn_for('m26', 's-local', 'You were woken')
        self.assertIn('<recovery>', waker.options['system'])
        self.assertIn('Never print a credential.', waker.options['system'])

    def test_both_harnesses_append_it_to_their_own(self):
        pi = PiWaker(pi='pi', model='openrouter/x', session_dir='/tmp/s').command('old', 'new', 'hi', system=RULES)
        self.assertEqual(pi[pi.index('--append-system-prompt') + 1], RULES)
        self.assertEqual(pi[-1], 'hi')
        claude = ClaudeCodeWaker(claude='claude').command('old', 'new', 'hi', system=RULES)
        self.assertEqual(claude[claude.index('--append-system-prompt') + 1], RULES)
        self.assertEqual(claude[-2:], ['--', 'hi'])
        self.assertNotIn('--append-system-prompt', PiWaker(pi='pi', session_dir='/tmp/s').command('o', 'n', 'hi'))

    def test_the_old_flag_still_turns_them_on(self):
        from poller.mood_poller import build_parser
        for flag in ('--instructions', '--rules-bundle'):  # the inventory says --rules-bundle until it is changed
            self.assertTrue(build_parser().parse_args(['--agent', 'blu', flag]).instructions)
