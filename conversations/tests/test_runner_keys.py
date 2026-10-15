"""Each agent's runner key, from a variable of its own (memory_viewer/runner_keys.py)."""

from unittest import TestCase

from memory_viewer.runner_keys import runner_keys


class RunnerKeysTest(TestCase):

    def test_one_variable_per_agent_and_any_character_in_a_key(self):
        keys = runner_keys({'MOOD_RUNNER_KEY_MAGENT': 'm' * 64, 'MOOD_RUNNER_KEY_RE': 'a,b:c&(d)@e', 'PATH': '/bin'})
        self.assertEqual(keys, {'magent': 'm' * 64, 're': 'a,b:c&(d)@e'})  # commas and colons are fine now

    def test_the_older_single_variable_still_works_and_yields_to_the_new(self):
        env = {'MOOD_RUNNER_KEYS': 'magent:old,re:old'}
        self.assertEqual(runner_keys(env), {'magent': 'old', 're': 'old'})
        env['MOOD_RUNNER_KEY_RE'] = 'new'
        self.assertEqual(runner_keys(env), {'magent': 'old', 're': 'new'})

    def test_names_and_empty_values(self):
        keys = runner_keys({'MOOD_RUNNER_KEY_SOME_AGENT': ' k \n', 'MOOD_RUNNER_KEY_BLU': '', 'MOOD_RUNNER_KEY_': 'x'})
        self.assertEqual(keys, {'some-agent': 'k'})  # as hunter names AGENT_<NAME>_*: dashes become underscores
        self.assertEqual(runner_keys({}), {})
