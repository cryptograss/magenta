"""The Moods list: a Mood is as recent as the newest word in it or any of its threads (Justin, 31 Oct)."""

import uuid

from django.core.cache import cache
from django.test import TestCase

from conversations.models import Message, Mood, ThinkingEntity


class MoodOrderTest(TestCase):

    def setUp(self):
        cache.clear()
        ThinkingEntity.objects.get_or_create(name='justin', defaults={'is_biological_human': True})

    def say(self, mood, n):
        Message.objects.create(id=uuid.uuid4(), sender_id='justin', mood=mood, session_id=uuid.uuid4(), timestamp=n,
                               content=[{'type': 'text', 'text': f'word {n}'}])

    def test_a_busy_thread_keeps_its_mood_up_the_list(self):
        quiet = Mood.objects.create(slug='quiet', title='quiet')
        middle = Mood.objects.create(slug='middle', title='middle')
        thread = Mood.objects.create(slug='quiet-thread', title='quiet thread', thread_of=quiet)
        deeper = Mood.objects.create(slug='deeper', title='deeper', thread_of=thread)
        self.say(quiet, 1)
        self.say(thread, 2)
        self.say(middle, 3)
        self.say(deeper, 4)  # newest of all: three threads down from quiet
        order = [m['slug'] for m in self.client.get('/api/moods/').json()['moods']]
        self.assertLess(order.index('quiet'), order.index('middle'))
        self.assertLess(order.index('quiet-thread'), order.index('middle'))
