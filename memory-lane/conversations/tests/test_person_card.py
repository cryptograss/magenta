"""A person's card, opened by clicking their name (Justin, 31 Oct): their last messages with what answering
took, their last PickiPedia edits, and their trail across the Moods (services/person_card.py)."""

import uuid
from unittest import mock

from django.core.cache import cache
from django.test import Client, TestCase

from conversations.models import Message, Mood, ThinkingEntity
from conversations.services import mood_auth, person_card, presence


class FakeWiki:
    def __init__(self, contribs):
        self.contribs = contribs

    def get(self, url, params=None, **kw):
        body = ({'query': {'usercontribs': self.contribs}} if params.get('list') == 'usercontribs'
                else {'query': {'pages': [{'missing': True}]}})
        return mock.Mock(json=lambda: body)


class PersonCardTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.justin = ThinkingEntity.objects.create(name='justin', is_biological_human=True)
        cls.magent = ThinkingEntity.objects.create(name='magent', is_biological_human=False)
        for slug in ('jams', 'general', 'interfaces'):
            Mood.objects.create(slug=slug, title=slug)

    def setUp(self):
        cache.clear()

    def said(self, who, slug, text):
        return Message.objects.create(id=uuid.uuid4(), sender=who, mood=Mood.objects.get(slug=slug), content=text)

    def test_trail_is_latest_first_once_each_with_posts(self):
        presence.walked('justin', 'jams', now=presence.time.time() - 30)
        presence.walked('justin', 'general', now=presence.time.time() - 20)
        presence.walked('justin', 'jams', now=presence.time.time() - 10)  # again: to the front, not twice
        self.said(self.justin, 'interfaces', 'a thought')
        card = person_card.card('justin', http=FakeWiki([]))
        self.assertEqual([(t['mood'], t['posted']) for t in card['trail']],
                         [('interfaces', True), ('jams', False), ('general', False)])

    def test_opening_a_mood_walks_the_trail(self):
        _, token = mood_auth.enrol_device(self.justin, 'laptop', tier='key')
        client = Client()
        client.cookies[mood_auth.COOKIE] = token
        client.post('/api/moods/general/here/')
        client.post('/api/moods/jams/here/')
        self.assertEqual([t['mood'] for t in presence.trail('justin')], ['jams', 'general'])

    def test_last_five_messages_newest_first(self):
        for i in range(7):
            self.said(self.justin, 'jams', f'message {i}')
        card = person_card.card('justin', http=FakeWiki([]))
        self.assertEqual([m['snippet'] for m in card['messages']], [f'message {i}' for i in (6, 5, 4, 3, 2)])
        self.assertEqual(card['messages'][0]['mood'], 'jams')

    def test_edits_are_distinct_pages(self):
        rows = [{'title': t, 'timestamp': '2026-10-31T20:00:00Z'} for t in ('A', 'A', 'B', 'C', 'B', 'D', 'E', 'F')]
        card = person_card.card('justin', http=FakeWiki(rows))
        self.assertEqual([e['title'] for e in card['edits']], ['A', 'B', 'C', 'D', 'E'])

    def test_only_people_have_cards(self):
        self.assertIsNone(person_card.card('magent', http=FakeWiki([])))
        self.assertEqual(Client().get('/api/people/magent/card/').status_code, 404)
