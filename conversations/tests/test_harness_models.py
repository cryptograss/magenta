"""What an agent's harness can run, reported by its runner, for the Mood's
model menu (views_runner.api_agent_models, poller/pi_harness.py)."""

import json
from unittest import mock

from django.test import TestCase, override_settings

from conversations.models import HarnessModels, Mood, ThinkingEntity
from conversations.services import settings as knobs
from poller.mood_poller import MoodAPI
from poller.pi_harness import PiWaker

KEY, OTHER = 'r' * 64, 'm' * 64
REPORT = {'harness': 'pi 1.1.0', 'vendors': [
    {'name': 'anthropic', 'usable': False, 'models': [['claude-opus-5-5', 1000000]]},
    {'name': 'openrouter', 'usable': True, 'models': [['moonshotai/kimi-k3', 1000000], ['~anthropic/claude-fable-latest', None]]},
]}


@override_settings(MOOD_RUNNER_KEYS={'re': KEY, 'magent': OTHER})
class ReportTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        ThinkingEntity.objects.get_or_create(name='re', defaults={'is_biological_human': False})
        ThinkingEntity.objects.get_or_create(name='magent', defaults={'is_biological_human': False})

    def post(self, body, key=KEY, agent='re'):
        return self.client.post(f'/api/agents/{agent}/models/', data=json.dumps(body), content_type='application/json',
                                HTTP_AUTHORIZATION=f'Bearer {key}')

    def test_a_runner_reports_and_the_menu_reads_it(self):
        self.assertEqual(self.client.get('/api/agents/re/models/').status_code, 404)
        r = self.post(REPORT)
        self.assertEqual((r.status_code, r.json()['models']), (200, 3))
        got = self.client.get('/api/agents/re/models/').json()
        self.assertEqual(got['harness'], 'pi 1.1.0')
        self.assertEqual([(v['name'], v['usable']) for v in got['vendors']], [('anthropic', False), ('openrouter', True)])
        self.assertEqual(got['vendors'][1]['models'][1], ['~anthropic/claude-fable-latest', None])

    def test_a_new_report_replaces_the_old(self):
        self.post(REPORT)
        self.post({'harness': 'pi 1.2.0', 'vendors': [{'name': 'openai', 'usable': True, 'models': [['gpt-6-sol', 272000]]}]})
        self.assertEqual(HarnessModels.objects.count(), 1)
        self.assertEqual(HarnessModels.objects.get().harness, 'pi 1.2.0')

    def test_only_the_agents_own_runner_reports_for_it(self):
        self.assertEqual(self.post(REPORT, key=OTHER).status_code, 401)  # magent's key, for re
        self.assertEqual(self.post(REPORT, key='x').status_code, 401)
        self.assertFalse(HarnessModels.objects.exists())

    def test_a_malformed_report_is_refused(self):
        for bad in ({'vendors': 'all'}, {'vendors': [{'name': 'a b', 'models': []}]},
                    {'vendors': [{'name': 'x', 'models': [['has space', 1]]}]}, {'vendors': [{'name': 'x', 'models': [[7, 1]]}]}):
            self.assertEqual(self.post(bad).status_code, 400, bad)
        self.assertFalse(HarnessModels.objects.exists())

    def test_the_mood_says_which_agents_have_a_menu_of_their_own(self):
        Mood.objects.create(slug='docs-tests-qa')
        self.post(REPORT)
        agents = self.client.get('/api/moods/docs-tests-qa/turns/').json()['agents']
        self.assertEqual((agents['re']['catalog'], agents['magent']['catalog']), ('pi 1.1.0', None))


class ModelSettingTest(TestCase):

    def test_vendor_and_model_as_pi_names_them(self):
        for ok in ('opus', 'claude-opus-5-5', 'anthropic/claude-sonnet-5-5', 'openrouter/moonshotai/kimi-k3',
                   'openrouter/~anthropic/claude-fable-latest', 'openrouter/deepseek/deepseek-v4.1-flash:batch',
                   'minimax/MiniMax-M3', ''):
            self.assertEqual(knobs.clean('model', ok), ok)
        for bad in ('--tools all', 'a b', '-x', 'x' * 161, 'a;b'):
            with self.assertRaises(knobs.Invalid, msg=bad):
                knobs.clean('model', bad)


class PiCatalogTest(TestCase):

    def test_the_menus_names_mean_anthropics_models_on_pi(self):
        waker = PiWaker(pi='pi')
        model_of = lambda m: (lambda c: c[c.index('--model') + 1])(waker.command(None, 'n', 'hi', model=m))
        self.assertEqual(model_of('opus'), 'anthropic/claude-opus-5-5')  # never Bedrock's, by fuzzy match
        self.assertEqual(model_of('sonnet'), 'anthropic/claude-sonnet-5-5')
        self.assertEqual(model_of('openrouter/moonshotai/kimi-k3'), 'openrouter/moonshotai/kimi-k3')

    def test_every_known_model_and_which_vendors_the_keys_reach(self):
        waker = PiWaker(pi='pi', version='1.1.0')
        known = json.dumps({'anthropic': [['claude-opus-5-5', 1000000]], 'openrouter': [['moonshotai/kimi-k3', 1000000]]})
        listed = ('provider    model               context  max-out  thinking  images\n'
                  'openrouter  moonshotai/kimi-k3  1M       128K     yes       no\n'
                  'local       qwen2.5-1.5b        32K      8K       no        no\n')
        with mock.patch.object(PiWaker, 'package', return_value='/opt/pi'), \
                mock.patch.object(PiWaker, 'run_quietly', side_effect=lambda cmd, cwd=None: known if cmd[0] == 'node' else listed):
            got = waker.catalog()
        self.assertEqual(got['harness'], 'pi 1.1.0')
        self.assertEqual([(v['name'], v['usable'], v['models']) for v in got['vendors']], [
            ('anthropic', False, [['claude-opus-5-5', 1000000]]),
            ('local', True, [['qwen2.5-1.5b', None]]),  # a vendor of its own, from models.json
            ('openrouter', True, [['moonshotai/kimi-k3', 1000000]]),
        ])

    def test_nothing_to_say_when_pi_says_nothing(self):
        with mock.patch.object(PiWaker, 'package', return_value=None), \
                mock.patch.object(PiWaker, 'run_quietly', return_value='No models available.\n'):
            self.assertIsNone(PiWaker(pi='pi').catalog())

    def test_the_poller_sends_it_with_its_key(self):
        sent = {}

        class HTTP:
            @staticmethod
            def post(url, json=None, headers=None, timeout=None):
                sent.update(url=url, json=json, headers=headers)
                return mock.Mock(status_code=200)

        self.assertTrue(MoodAPI('http://ml', http=HTTP, key=KEY).report_models('re', REPORT))
        self.assertEqual(sent['url'], 'http://ml/api/agents/re/models/')
        self.assertEqual(sent['headers'], {'Authorization': f'Bearer {KEY}'})
        self.assertFalse(MoodAPI('http://ml', http=HTTP).report_models('re', REPORT))  # no key, no report

    def test_a_refused_report_says_why_in_the_log(self):
        class Refused:
            @staticmethod
            def post(url, json=None, headers=None, timeout=None):
                return mock.Mock(status_code=401, text='{"error": "unauthorized"}')

        with self.assertLogs('mood_poller', level='WARNING') as logged:
            self.assertFalse(MoodAPI('http://ml', http=Refused, key=KEY).report_models('re', REPORT))
        self.assertIn('401', logged.output[0])
        self.assertNotIn(KEY, logged.output[0])
