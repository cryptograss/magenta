"""The emergent rename's last step (PickiPedia: User:Magent/Emergent rename): every edge says `emergent` only.

The step before (magenta#364) had every edge take both names, so memory-lane and the pollers could be deployed
in either order. These tests prove the old names are gone: the /api/agents/ paths, the poller's --agent flags,
the `agent` keys memory-lane sent and read. (A Mood's stored lines keep the keys they were written with.)"""
import json
from unittest import mock, skipUnless

from django.test import SimpleTestCase, TestCase
from django.urls import Resolver404, resolve

from conversations.models import Mood, Setting, ThinkingEntity
from conversations.tests.test_mood_auth import HAS_SSH_KEYGEN
from conversations.tests.test_mood_devices import SignedInCase


class UrlsTest(TestCase):
    def test_only_the_emergents_paths(self):
        for path in ('/api/emergents/re/models/', '/api/emergents/re/tasks/Journal/'):
            resolve(path)
        for path in ('/api/agents/re/models/', '/api/agents/re/tasks/Journal/'):
            with self.subTest(path=path), self.assertRaises(Resolver404):
                resolve(path)

    def test_the_old_paths_are_not_found(self):
        ThinkingEntity.objects.get_or_create(name='re', defaults={'is_biological_human': False})
        self.assertEqual(self.client.get('/api/agents/re/models/').status_code, 404)
        self.assertEqual(self.client.post('/api/agents/re/models/', '{}', content_type='application/json').status_code, 404)
        self.assertEqual(self.client.post('/api/agents/re/tasks/Journal/', '{}', content_type='application/json').status_code, 404)


class PollerTest(SimpleTestCase):
    def test_the_old_flags_are_refused(self):
        from poller.mood_poller import build_parser
        self.assertEqual(build_parser().parse_args(['--emergent', 'blu']).agent, 'blu')
        self.assertEqual(build_parser().parse_args(['--emergent-wakes-per-hour', '4']).agent_wakes_per_hour, 4)
        for flag, value in (('--agent', 'blu'), ('--agent-wakes-per-hour', '4')):
            with self.subTest(flag=flag), mock.patch('sys.stderr'), self.assertRaises(SystemExit):
                build_parser().parse_args([flag, value])

    def test_no_either_left(self):
        from poller import mood_poller
        self.assertFalse(hasattr(mood_poller, 'either'))

    def test_it_asks_with_the_new_name_only(self):
        from poller.mood_poller import MoodAPI
        http = mock.Mock()
        http.get.return_value.json.return_value = {}
        api = MoodAPI('https://m.example', http=http)
        api.pulse('re')
        api.interrupt('m26', 're')
        for call in http.get.call_args_list:
            self.assertEqual(call.kwargs['params'], {'emergent': 're'})

    def test_it_reads_the_new_keys_only(self):
        from poller.mood_poller import MoodAPI
        http = mock.Mock()
        http.get.return_value.json.return_value = {'agents': ['blu']}
        self.assertEqual(MoodAPI('https://m.example', http=http).agents(), [])
        http.get.return_value.json.return_value = {'emergents': ['blu']}
        self.assertEqual(MoodAPI('https://m.example', http=http).agents(), ['blu'])

    def test_the_wake_names_the_special_page_by_its_new_name(self):
        import inspect
        from poller import mood_poller
        source = inspect.getsource(mood_poller)
        self.assertIn('(Special:EmergentBundles).', source)
        self.assertNotIn('(Special:AgentBundles).', source)


class ServerKeysTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        ThinkingEntity.objects.get_or_create(name='justin', defaults={'is_biological_human': True})
        ThinkingEntity.objects.get_or_create(name='re', defaults={'is_biological_human': False})
        Mood.objects.create(slug='m26')

    def test_settings_say_emergents(self):
        state = self.client.get('/api/settings/').json()
        self.assertIn('re', state['emergents'])
        self.assertNotIn('agents', state)

    def test_the_pulse_says_whose_it_is_as_emergent(self):
        got = self.client.get('/api/moods/pulse/', {'emergent': 're'}).json()
        self.assertEqual(got['emergent'], 're')
        self.assertNotIn('agent', got)
        self.assertEqual(self.client.get('/api/moods/pulse/', {'agent': 're'}).json()['emergent'], 'magent')

    def test_a_moods_turns_say_emergents(self):
        got = self.client.get('/api/moods/m26/turns/').json()
        self.assertIn('re', got['emergents'])
        self.assertNotIn('agents', got)


@skipUnless(HAS_SSH_KEYGEN, 'needs ssh-keygen')
class OldPageTest(SignedInCase):
    """A page loaded before the deploy still says "agent": refused, never read as naming no one."""

    def test_a_setting_or_a_stop_naming_an_agent_is_refused(self):
        client = self.sign_in()
        post = lambda url, body: client.post(url, json.dumps(body), content_type='application/json',
                                             HTTP_X_CSRFTOKEN=client.cookies['csrftoken'].value)
        r = post('/api/settings/', {'key': 'verbosity', 'value': 'brief', 'mood': 'm26', 'agent': 'magent'})
        self.assertEqual(r.status_code, 400)
        self.assertIn('reload', r.json()['error'])
        self.assertFalse(Setting.objects.exists())  # not set for every emergent instead
        r = post('/api/moods/m26/interrupt/', {'agent': 'magent'})
        self.assertEqual(r.status_code, 400)
        self.assertIn('reload', r.json()['error'])
        r = post('/api/settings/', {'key': 'verbosity', 'value': 'brief', 'mood': 'm26', 'emergent': 'magent'})
        self.assertEqual((r.status_code, r.json()['emergent']), (201, 'magent'))
