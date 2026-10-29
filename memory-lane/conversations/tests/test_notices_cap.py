"""A person's @mention isn't crowded out of the bell by agents' answers (29 Oct: Skyler's @JMyles
tags in #pickipedia-and-rabbithole never reached Justin; 100 answers came after them)."""

import uuid
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from conversations.models import Message, Mood, ThinkingEntity
from conversations.views_moods import notices_for


class NoticesCapTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        for name, human in (('justin', True), ('skyler', True), ('magent', False)):
            ThinkingEntity.objects.update_or_create(name=name, defaults={'is_biological_human': human})
        cls.mood = Mood.objects.create(slug='rh', title='rh')

    def say(self, who, text, minutes_ago, **kw):
        m = Message.objects.create(id=uuid.uuid4(), sender_id=who, mood=self.mood, content=[{'type': 'text', 'text': text}],
                                   timestamp=1, **kw)
        Message.objects.filter(id=m.id).update(created_at=timezone.now() - timedelta(minutes=minutes_ago))

    def test_an_older_mention_survives_a_flood_of_answers(self):
        self.say('skyler', '@justin is Harry on mando?', 60, source_file='mood-web')
        self.say('justin', '@magent build it', 50, source_file='mood-web')
        for i in range(6):
            self.say('magent', f'step {i} done', 40 - i, stop_reason='end_turn')
        told = notices_for('justin', timezone.now() - timedelta(hours=2), limit=3)
        self.assertEqual(len(told), 3)
        self.assertIn(('mention', 'skyler'), [(n['kind'], n['turn']['sender']) for n in told])
        self.assertEqual([n['kind'] for n in told].count('answer'), 2)  # the newest of the rest
