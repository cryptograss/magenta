"""Jenkins alarms in #ops (services/alarms.py, views_runner.api_alarms)."""

import json
from datetime import datetime, timedelta, timezone

from django.test import TestCase, override_settings

from conversations.models import Message, Mood
from conversations.services import alarms, mood_view

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


RED_TASK = ('{{Emergent task|mood=ops|cap=3|tools=look}}\n'
            'Jenkins says {job} · {stage} has been red since {since}: {url}. Find out why.')


def wiki(pages):
    return lambda title: pages.get(title, [])


class StillRedTest(TestCase):
    """A check still red after a round wakes the alarm task in #ops; one that recovers doesn't."""

    @classmethod
    def setUpTestData(cls):
        from conversations.models import ThinkingEntity
        ThinkingEntity.objects.update_or_create(name='magent', defaults={'is_biological_human': False})
        Mood.objects.create(slug='ops', title='ops')

    def setUp(self):
        from conversations.services import tasks
        self.pages = {tasks.title(*alarms.WAKES): [(70, 'JMyles', RED_TASK)]}

    def red(self, stage, minutes_ago, state='red'):
        row = alarms.record('cron-health', stage, state, url='https://maybelle.cryptograss.live/job/cron-health/9/')
        Message.objects.filter(id=row.id).update(created_at=self.now - timedelta(minutes=minutes_ago))
        return row

    @property
    def now(self):
        return datetime(2026, 11, 5, 20, 0, tzinfo=timezone.utc)

    def still(self, pages=None, at=None):
        return alarms.still_red(now=at or self.now, get=wiki(self.pages if pages is None else pages))

    def test_a_check_red_past_a_round_wakes_the_task_once(self):
        row = self.red('maybelle disk', 9)
        [(stage, line)] = self.still()
        row.refresh_from_db()
        self.assertEqual(row.content['woke'], str(line.id))  # the red row holds the line it woke
        self.assertEqual((stage, line.content['state'], line.content['by'], line.mood.slug),
                         ('maybelle disk', 'asked', 'jenkins', 'ops'))
        self.assertEqual(line.content['text'], 'Jenkins says cron-health · maybelle disk has been red since '
                                               '2026-11-05 19:51Z: https://maybelle.cryptograss.live/job/cron-health/9/. '
                                               'Find out why.')
        self.assertEqual(self.still(at=self.now + timedelta(minutes=5)), [])  # once per red
        self.assertEqual(Message.objects.filter(source_file='task').count(), 1)

    def test_a_red_the_next_round_cleared_wakes_nobody(self):
        self.red('arthel rsync (NFS deploy)', 15)
        self.red('arthel rsync (NFS deploy)', 9, state='green')
        self.assertEqual(self.still(), [])

    def test_not_before_a_round_has_passed(self):
        self.red('maybelle disk', 3)
        self.assertEqual(self.still(), [])
        self.assertFalse(Message.objects.filter(source_file='task').exists())

    def test_without_a_trusted_page_nothing_starts_and_it_tries_again(self):
        self.red('maybelle disk', 9)
        [(_, why)] = self.still(pages={})
        self.assertIn('there is no page', why)
        self.assertFalse(Message.objects.filter(source_file='task').exists())
        [(_, line)] = self.still()  # the page saved: the same red wakes now
        self.assertEqual(line.content['state'], 'asked')

    def test_the_page_takes_only_the_blanks_it_has(self):
        from conversations.services import tasks
        self.red('maybelle disk', 9)
        [(_, line)] = self.still(pages={tasks.title(*alarms.WAKES): [(71, 'JMyles', '{{Emergent task|mood=ops}}\nLook at {stage}.')]})
        self.assertEqual(line.content['text'], 'Look at maybelle disk.')
