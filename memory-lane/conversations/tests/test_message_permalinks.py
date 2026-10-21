"""/messages/<id>: a message's own address, wherever it lives (Justin, 20 Oct: a Mood can be renamed,
which moves its page; its messages' addresses shouldn't move with it)."""

import uuid

from django.test import TestCase

from conversations.models import Message, Mood, ThinkingEntity
from conversations.services.memory import resolve_message


class MessageAddressTest(TestCase):

    def setUp(self):
        self.justin = ThinkingEntity.objects.create(name='justin', is_biological_human=True)
        self.mood = Mood.objects.create(slug='magenta-interface')
        self.said = Message.objects.create(id=uuid.uuid4(), sender=self.justin, mood=self.mood, content='hello',
                                           timestamp=1, source_file='mood-web')

    def test_it_sends_you_to_the_message_in_its_mood_as_named_now(self):
        response = self.client.get(f'/messages/{self.said.id}')
        self.assertEqual((response.status_code, response['Location']), (302, f'/moods/magenta-interface/#m-{self.said.id}'))
        Mood.objects.filter(pk=self.mood.pk).update(slug='magenta-home')
        self.assertEqual(self.client.get(f'/messages/{self.said.id}/')['Location'], f'/moods/magenta-home/#m-{self.said.id}')
        self.assertEqual(self.client.get(f'/api/messages/{self.said.id}/').json(),
                         {'id': str(self.said.id), 'mood': 'magenta-home'})

    def test_only_what_was_said_in_a_mood(self):
        elsewhere = Message.objects.create(id=uuid.uuid4(), sender=self.justin, content='a terminal session', timestamp=1)
        helper = Message.objects.create(id=uuid.uuid4(), sender=self.justin, mood=self.mood, content='a helper',
                                        timestamp=1, is_sidechain=True)
        for ref in (elsewhere.id, helper.id, uuid.uuid4(), 'not-an-id'):
            with self.subTest(ref=ref):
                self.assertEqual(self.client.get(f'/messages/{ref}').status_code, 404)
                self.assertEqual(self.client.get(f'/api/messages/{ref}/').status_code, 404)

    def test_an_agent_can_look_it_up_by_its_address(self):
        message, _, problem = resolve_message(f'https://magenta.cryptograss.live/messages/{self.said.id}')
        self.assertEqual((message, problem), (self.said, None))

    def test_the_page_copies_the_messages_own_address(self):
        page = self.client.get('/moods/').content.decode()
        self.assertIn('/messages/${encodeURIComponent(hash.replace(/^#m-/,', page)
