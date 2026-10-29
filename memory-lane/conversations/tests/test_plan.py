"""The Claude plan's rolling windows, as Claude Code reports them (services/plan.py)."""

import json
from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from conversations.models import PlanWindow
from conversations.services import plan

KEY = 'k' * 64
AUTH = {'HTTP_AUTHORIZATION': f'Bearer {KEY}'}
SOON = int((timezone.now() + timedelta(hours=3)).timestamp())


class PlanTest(TestCase):

    def test_a_reading_names_its_window_and_any_it_lists(self):
        info = {'status': 'allowed', 'rateLimitType': 'five_hour', 'utilization': 0.42, 'resetsAt': SOON,
                'windows': [{'rateLimitType': 'seven_day', 'utilization': 0.63, 'resetsAt': SOON, 'status': 'allowed'},
                            {'rateLimitType': 'five_hour', 'utilization': 0.43}, {'rateLimitType': 'odd'}]}
        found = {w['window']: w for w in plan.windows_in(info)}
        self.assertEqual(set(found), {'five_hour', 'seven_day'})
        self.assertEqual(found['five_hour']['utilization'], 0.43)  # the last said
        self.assertEqual(found['seven_day']['resets_at'].timestamp(), SOON)
        self.assertEqual(plan.windows_in({'status': 'allowed'}), [])
        self.assertEqual(plan.windows_in('nonsense'), [])

    def test_a_window_is_kept_when_it_changes(self):
        info = {'rateLimitType': 'seven_day', 'utilization': 0.5, 'resetsAt': SOON, 'status': 'allowed'}
        self.assertEqual(len(plan.record(info, 're')), 1)
        self.assertEqual(len(plan.record(info, 'blu')), 0)  # the same reading again: nothing new
        self.assertEqual(len(plan.record({**info, 'utilization': 0.51}, 'magent')), 1)
        state = plan.plan_state(timezone.now() - timedelta(hours=1))['windows']['seven_day']
        self.assertEqual((state['label'], state['now']['utilization'], len(state['points'])), ('week', 0.51, 2))

    def test_a_window_past_its_reset_reads_zero(self):
        gone = int((timezone.now() - timedelta(minutes=5)).timestamp())
        plan.record({'rateLimitType': 'five_hour', 'utilization': 0.9, 'resetsAt': gone}, 're')
        state = plan.plan_state(timezone.now() - timedelta(hours=1))['windows']['five_hour']
        self.assertEqual((state['now']['utilization'], state['now']['status'], state['points'][-1][1]), (0.0, 'reset', 0.0))

    @override_settings(MOOD_RUNNER_KEYS={'re': KEY})
    def test_the_runner_says_it_and_the_usage_api_shows_it(self):
        def post(body, auth=AUTH):
            return self.client.post('/api/usage/plan/', json.dumps(body), content_type='application/json', **auth)
        self.assertEqual(post({'rate_limit_info': {}}, auth={}).status_code, 401)
        self.assertEqual(post({'nope': 1}).status_code, 400)
        answered = post({'rate_limit_info': {'rateLimitType': 'seven_day', 'utilization': 0.3, 'resetsAt': SOON}})
        self.assertEqual(answered.json(), {'kept': ['seven_day']})
        self.assertEqual(PlanWindow.objects.get().agent, 're')
        shown = self.client.get('/api/usage/?hours=24').json()['plan']
        self.assertEqual(shown['seven_day']['now']['utilization'], 0.3)
