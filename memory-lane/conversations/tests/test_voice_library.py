"""Voices found by typing, from ElevenLabs' whole library, or an id pasted (Justin, 23 Oct: "elevenlabs has
like a zillion. Maybe autocomplete is a better approach? Or just let me paste a voice id?")."""

import json
from unittest import mock

from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from conversations.models import Mood, Setting, ThinkingEntity
from conversations.services import mood_auth, voice
from conversations.tests.test_voice import MP3, VOICES, Answer

LIBRARY_ID = 'Lib0Voice0Id00000001'
LIBRARY = {'voices': [
    {'voice_id': LIBRARY_ID, 'name': 'Old Timey Banjo', 'public_owner_id': 'owner123abc', 'gender': 'male',
     'accent': 'american', 'description': 'A porch storyteller.', 'preview_url': 'https://x/banjo.mp3'},
    {'voice_id': 'v-aria', 'name': 'Aria', 'public_owner_id': 'elevenlabs1', 'preview_url': 'https://x/aria.mp3'},  # ours already
    {'voice_id': 'not an id', 'name': 'Broken', 'public_owner_id': 'x'},
]}


class Eleven:
    def __init__(self):
        self.asked = []

    def get(self, url, **kw):
        self.asked.append(('GET', url, kw))
        if url.endswith('/shared-voices'):
            return Answer(body=LIBRARY)
        if url.endswith('banjo.mp3'):
            return Answer(content=MP3)
        return Answer(body=VOICES)

    def post(self, url, **kw):
        self.asked.append(('POST', url, kw))
        if '/voices/add/' in url:
            return Answer(body={'voice_id': LIBRARY_ID})
        return Answer(content=MP3)


@override_settings(ELEVENLABS_API_KEY='test-eleven-key')
class VoiceLibraryTest(TestCase):

    def setUp(self):
        cache.clear()
        self.justin = ThinkingEntity.objects.create(name='justin', is_biological_human=True)
        self.skyler = ThinkingEntity.objects.create(name='skyler', is_biological_human=True)
        Mood.objects.create(slug='general', title='general')
        self.fake = Eleven()
        patches = [mock.patch('requests.get', self.fake.get), mock.patch('requests.post', self.fake.post)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def client_for(self, who, tier='key'):
        _, token = mood_auth.enrol_device(who, 'laptop', tier=tier)
        client = Client()
        client.cookies[mood_auth.COOKIE] = token
        return client

    def test_ours_first_then_the_librarys_each_once(self):
        found = voice.search_voices('ar', http=self.fake)
        self.assertEqual(found[0]['name'], 'Aria')  # ours, by its name
        self.assertFalse(found[0]['library'])
        library = [v for v in found if v['library']]
        self.assertEqual([v['voice_id'] for v in library], [LIBRARY_ID])  # not Aria twice, not a broken id
        self.assertEqual(library[0]['owner'], 'owner123abc')
        self.assertIn('male, american', library[0]['description'])
        self.assertEqual(voice.search_voices('american', http=self.fake)[0]['name'], 'Aria')  # by what it's labelled
        asked = len(self.fake.asked)
        voice.search_voices('ar', http=self.fake)
        self.assertEqual(len(self.fake.asked), asked)  # the library's answer, kept

    def test_the_search_answers_anyone_signed_in(self):
        self.assertEqual(Client().get('/api/voice/search/', {'q': 'banjo'}).status_code, 401)
        found = self.client_for(self.skyler, tier='wiki').get('/api/voice/search/', {'q': 'banjo'}).json()['voices']
        self.assertIn(LIBRARY_ID, [v['voice_id'] for v in found])

    def test_a_library_voice_is_added_to_the_account_when_chosen_with_a_key(self):
        choose = lambda client, name: client.post('/api/voice/speaker/', json.dumps(  # noqa: E731
            {'name': name, 'voice': LIBRARY_ID, 'owner': 'owner123abc', 'voice_name': 'Old Timey Banjo'}),
            content_type='application/json')
        self.assertEqual(choose(self.client_for(self.skyler, tier='wiki'), 'skyler').status_code, 403)  # it changes the account
        self.assertFalse([a for a in self.fake.asked if '/voices/add/' in a[1]])
        self.assertEqual(choose(self.client_for(self.justin), 'skyler').status_code, 201)
        added = [a for a in self.fake.asked if '/voices/add/' in a[1]]
        self.assertEqual(added[0][1].rsplit('/voices/add/', 1)[1], f'owner123abc/{LIBRARY_ID}')
        self.assertEqual(added[0][2]['json'], {'new_name': 'Old Timey Banjo'})
        self.assertEqual(Setting.objects.filter(key='speaker_voice', agent=self.skyler).latest('created_at').value, LIBRARY_ID)

    def test_an_id_pasted_is_taken_as_it_is(self):
        said = self.client_for(self.justin).post('/api/voice/speaker/', json.dumps({'name': 'justin', 'voice': 'Pasted0Voice0Id00001'}),
                                                 content_type='application/json')
        self.assertEqual(said.status_code, 201)
        self.assertFalse([a for a in self.fake.asked if '/voices/add/' in a[1]])

    def test_a_library_voices_sample_plays_once_its_been_found(self):
        self.assertEqual(self.client.get(f'/api/voice/sample/{LIBRARY_ID}/').status_code, 404)  # nothing found yet
        voice.search_voices('banjo', http=self.fake)
        heard = self.client.get(f'/api/voice/sample/{LIBRARY_ID}/')
        self.assertEqual((heard.status_code, heard['Content-Type']), (200, 'audio/mpeg'))
