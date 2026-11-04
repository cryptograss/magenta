"""The people pane under the to-do list (services/humans.py; Justin, 3 Nov)."""

import time
import uuid
from datetime import timedelta

from django.test import Client, TestCase
from django.utils import timezone

from conversations.models import Message, Mood, ThinkingEntity
from conversations.services import humans


class HumansPaneTest(TestCase):

    def setUp(self):
        self.names = ['justin', 'skyler', 'jake', 'fib', 'rhonda']
        for n in self.names:
            ThinkingEntity.objects.create(name=n, is_biological_human=True)
        ThinkingEntity.objects.create(name='magent', is_biological_human=False)
        self.here = Mood.objects.create(slug='interfaces', title='interfaces')
        self.there = Mood.objects.create(slug='jams', title='jams')

    def say(self, who, mood, text, ago_blocks):
        at = timezone.now() - timedelta(seconds=ago_blocks * humans.BLOCK_SECONDS)
        m = Message.objects.create(id=uuid.uuid4(), sender_id=who, mood=mood, source_file='mood-web',
                                   content=text, timestamp=int(time.time() * 1000))
        Message.objects.filter(id=m.id).update(created_at=at)
        return m

    def test_lately_here_then_everyone_else_to_ten(self):
        self.say('justin', self.here, 'one', 10)
        self.say('skyler', self.here, 'two', 50)
        self.say('jake', self.here, 'three', 100)
        self.say('fib', self.here, 'four', 200)
        self.say('rhonda', self.there, 'elsewhere', 5)
        self.say('magent', self.here, 'an emergent', 1)  # never listed
        d = humans.pane('interfaces')
        self.assertEqual([h['name'] for h in d['here']], ['justin', 'skyler', 'jake', 'fib'])
        self.assertEqual([(h['name'], h['title']) for h in d['elsewhere']], [('rhonda', 'jams')])

    def test_fewer_than_three_lately_shows_the_last_three_here(self):
        self.say('justin', self.here, 'now', 10)
        self.say('skyler', self.here, 'long ago', 5000)
        self.say('jake', self.here, 'longer ago', 9000)
        self.say('fib', self.here, 'longest ago', 20000)
        self.say('skyler', self.there, 'later, elsewhere', 20)
        d = humans.pane('interfaces')
        self.assertEqual([h['name'] for h in d['here']], ['justin', 'skyler', 'jake'])
        # fib below the rule, at their latest word anywhere
        self.assertEqual([h['name'] for h in d['elsewhere']], ['fib'])

    def test_the_endpoint(self):
        self.say('justin', self.here, 'hi', 1)
        r = Client().get('/api/moods/interfaces/humans/')
        self.assertEqual(r.json()['here'][0]['name'], 'justin')
        self.assertEqual(Client().get('/api/moods/nowhere/humans/').status_code, 404)
