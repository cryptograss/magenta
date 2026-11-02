"""The emergent rename's window (PickiPedia: User:Magent/Emergent rename): every edge takes both names.

memory-lane and the pollers are deployed at different moments, so for one deploy a request may say `emergent`
or `agent`, the poller takes --emergent or --agent, and it reads either key from memory-lane. The rename's last
step drops the old names, and these tests with them."""
from django.test import SimpleTestCase
from django.urls import resolve

from conversations.services.emergent_names import named


class NamedTest(SimpleTestCase):
    def test_the_new_name_first_then_the_old(self):
        self.assertEqual(named({'emergent': 're'}), 're')
        self.assertEqual(named({'agent': 'blu'}), 'blu')
        self.assertEqual(named({'emergent': 're', 'agent': 'blu'}), 're')

    def test_nothing_named(self):
        self.assertEqual(named({}, 'magent'), 'magent')
        self.assertIsNone(named(None))
        self.assertEqual(named({'emergent': ''}, 'magent'), 'magent')


class UrlsTest(SimpleTestCase):
    def test_both_paths_reach_the_same_view(self):
        for new, old in (('/api/emergents/re/models/', '/api/agents/re/models/'),
                         ('/api/emergents/re/tasks/Journal/', '/api/agents/re/tasks/Journal/')):
            self.assertIs(resolve(new).func, resolve(old).func)
            self.assertEqual(resolve(new).kwargs, resolve(old).kwargs)


class PollerTest(SimpleTestCase):
    def test_both_flags(self):
        from poller.mood_poller import build_parser
        for flag in ('--emergent', '--agent'):
            self.assertEqual(build_parser().parse_args([flag, 'blu']).agent, 'blu')
        for flag in ('--emergent-wakes-per-hour', '--agent-wakes-per-hour'):
            self.assertEqual(build_parser().parse_args([flag, '4']).agent_wakes_per_hour, 4)

    def test_either_key(self):
        from poller.mood_poller import either
        self.assertEqual(either({'emergents': ['re']}, 'emergents', 'agents'), ['re'])
        self.assertEqual(either({'agents': ['blu']}, 'emergents', 'agents'), ['blu'])
        self.assertEqual(either({'emergents': {}, 'agents': {'blu': 1}}, 'emergents', 'agents'), {})  # the new key, even empty
        self.assertIsNone(either({}, 'emergents', 'agents'))

    def test_the_wake_names_the_special_page_by_its_new_name(self):
        import inspect
        from poller import mood_poller
        source = inspect.getsource(mood_poller)
        self.assertIn('(Special:EmergentBundles).', source)
        self.assertNotIn('(Special:AgentBundles).', source)
