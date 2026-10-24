"""An agent's rules from PickiPedia, given to each turn as system prompt (poller/rules_bundle.py): for an
agent whose poller is run with --rules-bundle (blu first), never for one whose rules live elsewhere yet."""

import json
import tempfile
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

from conversations.tests.test_mood_poller import FakeAPI, FakeWaker
from poller.mood_poller import ClaudeCodeWaker, MoodPoller
from poller.pi_harness import PiWaker

RULES = '<rules agent="blu" mood="m26">\n<identity agent="blu">You are blu, the blue agent.</identity>\n</rules>'


class RulesPromptTest(SimpleTestCase):

    def poller(self, **kwargs):
        state = Path(tempfile.mkdtemp()) / 'state.json'
        state.write_text(json.dumps({'since': '2026-10-21T00:00:00+00:00', 'handled': [], 'wakes': []}))
        waker = FakeWaker()
        return MoodPoller(FakeAPI([]), waker, agent='blu', state_path=state, consider=False, **kwargs), waker

    def test_each_turn_is_given_the_agents_rules_for_that_mood(self):
        poller, waker = self.poller(rules_bundle=True)
        with mock.patch('poller.rules_bundle.bundle', return_value=mock.Mock(text=RULES)) as gathered:
            poller.run_turn_for('m26', 's-local', 'You were woken')
        gathered.assert_called_once_with('blu', 'm26')
        self.assertEqual(waker.options['system'], RULES)

    def test_without_the_flag_nothing_is_added(self):
        poller, waker = self.poller()
        with mock.patch('poller.rules_bundle.bundle') as gathered:
            poller.run_turn_for('m26', 's-local', 'You were woken')
        gathered.assert_not_called()
        self.assertNotIn('system', waker.options)

    def test_rules_never_gathered_at_all_the_recovery_rules_stand(self):
        poller, waker = self.poller(rules_bundle=True)
        with mock.patch('poller.rules_bundle.bundle', side_effect=RuntimeError('cache unreadable')):
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
