"""Jenkins alarms in #ops (services/alarms.py, views_runner.api_alarms)."""

import json

from django.test import TestCase, override_settings

from conversations.models import Message, Mood
from conversations.services import mood_view

KEY = 'a' * 40


@override_settings(MOOD_ALARM_KEY=KEY)
class AlarmsTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        Mood.objects.create(slug='ops', title='ops')
        Mood.objects.create(slug='general', title='general')

    def alarm(self, body, key=KEY):
        return self.client.post('/api/alarms/', json.dumps(body), content_type='application/json',
                                HTTP_AUTHORIZATION=f'Bearer {key}')

    def test_a_red_stage_is_a_line_in_ops_and_nowhere_else(self):
        r = self.alarm({'job': 'cron-health', 'stage': 'maybelle disk', 'state': 'red',
                        'url': 'https://maybelle.cryptograss.live/job/cron-health/1/'})
        self.assertEqual(r.status_code, 201)
        events = self.client.get('/api/moods/ops/turns/').json()['events']
        self.assertEqual([(e['type'], e['stage'], e['state']) for e in events], [('alarm', 'maybelle disk', 'red')])
        self.assertEqual(self.client.get('/api/moods/ops/turns/').json()['turns'], [])  # not a turn
        self.assertEqual(self.client.get('/api/moods/general/turns/').json()['events'], [])

    def test_an_alarm_wakes_nobody(self):
        # The pulse's 'newest' is what starts a consider turn; machinery isn't in it.
        self.alarm({'job': 'cron-health', 'stage': 'maybelle disk', 'state': 'red'})
        row = Message.objects.get(source_file='alarm')
        self.assertIn(row.sender_id, mood_view.MACHINERY_SENDERS)
        ops = next(m for m in self.client.get('/api/moods/pulse/').json()['moods'] if m['slug'] == 'ops')
        self.assertIsNone(ops['newest'])

    def test_its_line(self):
        self.assertEqual(mood_view.event_line({'type': 'alarm', 'job': 'cron-health', 'stage': 'maybelle disk',
                                               'state': 'red', 'note': '91%'}),
                         '🚨 cron-health · maybelle disk went red: 91%')
        self.assertEqual(mood_view.event_line({'type': 'alarm', 'job': 'cron-health', 'stage': 'maybelle disk',
                                               'state': 'green'}),
                         '✓ cron-health · maybelle disk is green again')

    def test_only_the_alarm_key_and_well_formed_alarms(self):
        self.assertEqual(self.alarm({'job': 'j', 'stage': 's', 'state': 'red'}, key='x').status_code, 401)
        self.assertEqual(self.alarm({'job': 'j', 'stage': 's', 'state': 'purple'}).status_code, 400)
        self.assertEqual(self.alarm({'job': '', 'stage': 's', 'state': 'red'}).status_code, 400)
        self.assertEqual(self.alarm({'job': 'j', 'stage': 's'}).status_code, 400)
        self.assertEqual(self.alarm({'job': 'j', 'stage': 's', 'state': 'red',
                                     'url': 'javascript:alert(1)'}).status_code, 400)
        self.assertFalse(Message.objects.filter(source_file='alarm').exists())

    @override_settings(MOOD_ALARM_KEY='')
    def test_without_a_key_nobody_can(self):
        self.assertEqual(self.alarm({'job': 'j', 'stage': 's', 'state': 'red'}, key='').status_code, 503)
