"""What a poll of a Mood asks every few seconds, asked once and kept (Justin, 6 Nov: 1.4 to 5.4 s a poll)."""

import uuid

from django.core.cache import cache
from django.test import TestCase

from conversations.models import Message, Mood, ThinkingEntity
from conversations.views_moods import last_model_of, pronouns_here, spoke_in


class PollLightTest(TestCase):

    def setUp(self):
        cache.clear()
        ThinkingEntity.objects.get_or_create(name='justin', defaults={'is_biological_human': True})
        ThinkingEntity.objects.get_or_create(name='re', defaults={'is_biological_human': False})
        self.mood = Mood.objects.create(slug='jams', title='jams')
        Message.objects.create(id=uuid.uuid4(), sender_id='justin', mood=self.mood, timestamp=1,
                               content=[{'type': 'text', 'text': 'hi'}])

    def test_who_spoke_here_is_asked_once_a_minute(self):
        self.assertEqual(spoke_in(self.mood), {'justin'})
        with self.assertNumQueries(0):
            self.assertEqual(spoke_in(self.mood), {'justin'})
        Message.objects.create(id=uuid.uuid4(), sender_id='re', mood=self.mood, timestamp=2,
                               content=[{'type': 'text', 'text': 'hello'}])
        self.assertEqual(spoke_in(self.mood), {'justin'})  # until the minute is up
        cache.clear()
        self.assertEqual(spoke_in(self.mood), {'justin', 're'})

    def test_pronouns_here_reads_the_kept_speakers(self):
        cache.set('pronouns', {'justin': 'it/its', 'skyler': 'they/them'}, 60)
        self.assertEqual(pronouns_here(self.mood), {'justin': 'it/its'})  # skyler hasn't spoken here

    def test_the_model_an_agent_last_ran_on_is_kept(self):
        self.assertEqual(last_model_of('re'), '')
        Message.objects.create(id=uuid.uuid4(), sender_id='re', mood=self.mood, timestamp=3, model_backend='sonnet',
                               content=[{'type': 'text', 'text': 'x'}])
        with self.assertNumQueries(0):
            self.assertEqual(last_model_of('re'), '')  # kept
        cache.clear()
        self.assertEqual(last_model_of('re'), 'sonnet')
