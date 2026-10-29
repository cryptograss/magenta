"""Pinned messages (Justin, 26 Oct): a few things kept at a Mood's top, pinned and unpinned by a key holder."""

import json
import uuid

from django.core.cache import cache
from django.test import Client, TestCase

from conversations.models import Message, Mood, ThinkingEntity
from conversations.services import mood_auth, pins


class PinsTest(TestCase):

    def setUp(self):
        cache.clear()
        self.justin = ThinkingEntity.objects.update_or_create(name='justin', defaults={'is_biological_human': True})[0]
        self.skyler = ThinkingEntity.objects.update_or_create(name='skyler', defaults={'is_biological_human': True})[0]
        self.mood = Mood.objects.create(slug='jams')
        self.first = self.say('The setlist for Saturday: Salt Creek, Old Joe Clark, Whiskey Before Breakfast.')
        self.second = self.say('The bus leaves at nine from the depot.')

    def say(self, text, who=None):
        return Message.objects.create(id=uuid.uuid4(), sender=who or self.justin, mood=self.mood, content=text,
                                      timestamp=1, source_file='mood-web')

    def client_for(self, who, tier='key'):
        _, token = mood_auth.enrol_device(who, 'laptop', tier=tier)
        client = Client()
        client.cookies[mood_auth.COOKIE] = token
        return client

    def pin(self, client, message):
        return client.post(f'/api/moods/jams/messages/{message.id}/pin/', '{}', content_type='application/json')

    def test_pinned_newest_first_unpinned_by_pressing_again(self):
        justin = self.client_for(self.justin)
        self.assertTrue(self.pin(justin, self.first).json()['pinned'])
        r = self.pin(justin, self.second).json()
        self.assertEqual([p['id'] for p in r['pins']], [str(self.second.id), str(self.first.id)])
        self.assertEqual(r['pins'][1]['snippet'][:22], 'The setlist for Saturd')
        self.assertEqual((r['pins'][1]['sender'], r['pins'][1]['by']), ('justin', 'justin'))
        r = self.pin(justin, self.second).json()
        self.assertFalse(r['pinned'])
        self.assertEqual([p['id'] for p in r['pins']], [str(self.first.id)])

    def test_a_key_holder_only(self):
        self.assertEqual(self.pin(Client(), self.first).status_code, 401)
        self.assertEqual(self.pin(self.client_for(self.skyler, 'wiki'), self.first).status_code, 403)

    def test_every_look_brings_them_and_a_pin_is_never_a_message(self):
        self.pin(self.client_for(self.justin), self.first)
        page = Client().get('/api/moods/jams/turns/').json()
        self.assertEqual([p['id'] for p in page['pins']], [str(self.first.id)])
        self.assertEqual(len(page['turns']), 2)

    def test_a_deleted_messages_pin_goes_with_it(self):
        from conversations.models import MessageChange
        self.pin(self.client_for(self.justin), self.first)
        MessageChange.objects.create(message=self.first, mood=self.mood, kind='deleted', by='justin')
        self.assertEqual(pins.in_mood(self.mood), [])
