"""Where each person is in the Moods (Justin, 23 Oct): in the one they last opened, typed or spoke in,
for three minutes; nowhere else. Agents where they've spoken lately, as before."""

import json
import uuid
from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.test import Client, TestCase
from django.utils import timezone

from conversations.models import Message, Mood, ThinkingEntity
from conversations.services import mood_auth, presence


class PresenceTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.justin = ThinkingEntity.objects.create(name='justin', is_biological_human=True)
        cls.skyler = ThinkingEntity.objects.create(name='skyler', is_biological_human=True)
        cls.magent = ThinkingEntity.objects.create(name='magent', is_biological_human=False)
        for slug in ('jams', 'general', 'm26'):
            Mood.objects.create(slug=slug, title=slug)

    def setUp(self):
        cache.delete(presence.KEY)
        cache.delete('moods-live')

    def client_for(self, who):
        _, token = mood_auth.enrol_device(who, 'laptop', tier='key')
        client = Client()
        client.cookies[mood_auth.COOKIE] = token
        return client

    def said(self, who, slug, ago=0):
        msg = Message.objects.create(id=uuid.uuid4(), sender=who, mood=Mood.objects.get(slug=slug), content='hi')
        Message.objects.filter(pk=msg.pk).update(created_at=timezone.now() - timedelta(seconds=ago))

    def live(self):
        cache.delete('moods-live')
        moods = Client().get('/api/moods/live/').json()['moods']
        return {slug: entry['speakers'] for slug, entry in moods.items() if entry['speakers']}

    def test_opening_a_mood_moves_you_there_from_where_you_spoke(self):
        self.said(self.justin, 'general', ago=60)
        self.assertEqual(self.live(), {'general': ['justin']})
        self.assertEqual(self.client_for(self.justin).post('/api/moods/jams/here/').status_code, 200)
        self.assertEqual(self.live(), {'jams': ['justin']})  # gone from #general
        self.client_for(self.justin).post('/api/moods/m26/here/')
        self.assertEqual(self.live(), {'m26': ['justin']})

    def test_three_minutes_then_hidden(self):
        self.client_for(self.justin).post('/api/moods/jams/here/')
        later = presence.time.time() + presence.SHOWN_FOR + 1
        with mock.patch('conversations.services.presence.time.time', return_value=later):
            self.assertEqual(self.live(), {})

    def test_typing_or_stopping_starts_the_three_minutes_again_there(self):
        client = self.client_for(self.justin)
        start = presence.time.time()
        with mock.patch('conversations.services.presence.time.time', return_value=start):
            client.post('/api/moods/jams/here/')
        typed = start + 150
        with mock.patch('time.time', return_value=typed):
            client.post('/api/moods/jams/typing/', json.dumps({'typing': False}), content_type='application/json')
        with mock.patch('conversations.services.presence.time.time', return_value=start + 200):
            self.assertEqual(self.live(), {'jams': ['justin']})  # three minutes from when they stopped, not arrived
        with mock.patch('conversations.services.presence.time.time', return_value=typed + presence.SHOWN_FOR + 1):
            self.assertEqual(self.live(), {})

    def test_saying_something_from_a_terminal_counts_as_being_there(self):
        self.client_for(self.justin).post('/api/moods/jams/here/')
        self.said(self.justin, 'general')  # later than the click: there now
        self.assertEqual(self.live(), {'general': ['justin']})

    def test_each_person_their_own_place_and_agents_as_before(self):
        self.client_for(self.justin).post('/api/moods/jams/here/')
        self.client_for(self.skyler).post('/api/moods/general/here/')
        self.said(self.magent, 'jams', ago=300)     # five minutes ago: still around, an agent
        self.said(self.magent, 'general', ago=60)
        live = self.live()
        self.assertEqual(sorted(live['jams']), ['justin', 'magent'])
        self.assertEqual(sorted(live['general']), ['magent', 'skyler'])

    def test_only_a_signed_in_person_is_shown(self):
        self.assertEqual(Client().post('/api/moods/jams/here/').status_code, 401)
        self.assertEqual(self.live(), {})
