"""A Mood's temperature (Justin, 31 Oct): people's and emergents' messages in the past day and 3 hours, and when
each last spoke -- one grouped query for every Mood."""

import uuid
from datetime import timedelta

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from conversations.models import Message, Mood, ThinkingEntity, ToolUse
from conversations.services.temperature import temperatures


class TemperatureTest(TestCase):

    def setUp(self):
        cache.clear()
        ThinkingEntity.objects.get_or_create(name='justin', defaults={'is_biological_human': True})
        ThinkingEntity.objects.get_or_create(name='re', defaults={'is_biological_human': False})
        ThinkingEntity.objects.get_or_create(name='mood-poller', defaults={'is_biological_human': False})
        self.mood = Mood.objects.create(slug='jams', title='jams')

    def say(self, who, hours_ago, model=Message):
        row = model.objects.create(id=uuid.uuid4(), sender_id=who, mood=self.mood, session_id=uuid.uuid4(), timestamp=1,
                                   content=[{'type': 'text', 'text': 'hi'}])
        Message.objects.filter(id=row.id).update(created_at=timezone.now() - timedelta(hours=hours_ago))

    def test_people_and_emergents_apart_by_day_and_three_hours(self):
        self.say('justin', 1)
        self.say('justin', 10)
        self.say('justin', 30)  # older than a day: not counted, but it isn't the last either
        self.say('re', 0.1)
        self.say('re', 2, model=ToolUse)  # a tool call: not a message
        self.say('mood-poller', 0)  # the poller's own: not anyone speaking
        t = temperatures()[self.mood.pk]
        self.assertEqual((t['humans']['day'], t['humans']['warm']), (2, 1))
        self.assertEqual((t['emergents']['day'], t['emergents']['warm']), (1, 1))
        self.assertLess(timezone.now() - timezone.datetime.fromisoformat(t['emergents']['last_at']), timedelta(minutes=10))

    def test_who_spoke_last_on_each_side(self):
        # The list writes each side's time in its speaker's colour (Justin, 1 Nov).
        ThinkingEntity.objects.get_or_create(name='blu', defaults={'is_biological_human': False})
        self.say('re', 0.5)
        self.say('blu', 0.2)
        self.say('justin', 1)
        t = temperatures()[self.mood.pk]
        self.assertEqual((t['emergents']['last_by'], t['humans']['last_by']), ('blu', 'justin'))

    def test_the_moods_list_carries_it(self):
        self.say('justin', 1)
        mood = next(m for m in self.client.get('/api/moods/').json()['moods'] if m['slug'] == 'jams')
        self.assertEqual(mood['temperature']['humans']['day'], 1)
