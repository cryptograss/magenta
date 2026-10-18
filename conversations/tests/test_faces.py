"""Whose faces are in a picture sent into a Mood: asked of the faces service once, offered as
names, named only by a person, and those names go up with it to PickiPedia.

What faces says a face looks like is never what anyone is called until someone says so.
"""

import json
import uuid
from unittest import mock

from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from conversations.models import FaceLook, FaceName, Message, Mood, ThinkingEntity
from conversations.services import faces, media, mood_auth, wiki_upload

PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 64
GIF = b'GIF89a' + b'\x00' * 64
FACES = {'FACES_URL': 'https://faces.example/', 'FACES_KEY': 'k' * 32}
SAID = {'model': 'opencv-yunet-2023mar+sface-2021dec', 'threshold': 0.363, 'width': 2048, 'height': 1536, 'faces': [
    {'box': [1100, 300, 383, 383], 'score': 0.93, 'match': None,
     'candidates': [{'person': 'Justin Myles Holmes', 'similarity': 0.12, 'file': 'File:A.jpg'}]},
    {'box': [700, 320, 337, 337], 'score': 0.91,
     'match': {'person': 'Justin Myles Holmes', 'similarity': 0.53, 'file': 'File:Justin-st-pete-thompson.jpg'},
     'candidates': [{'person': 'Justin Myles Holmes', 'similarity': 0.53, 'file': 'File:Justin-st-pete-thompson.jpg'}]},
    {'box': [200, 350, 279, 279], 'score': 0.9,
     'match': {'person': 'Cory Walker', 'similarity': 0.46, 'file': 'File:Cory.jpg'}, 'candidates': []},
]}


class Answer:
    def __init__(self, body, status=200):
        self.body, self.status_code = body, status

    def json(self):
        return self.body


class FakeFaces:
    def __init__(self, said=SAID, status=200, down=False):
        self.said, self.status, self.down, self.asked = said, status, down, []

    def post(self, url, data=None, timeout=None, headers=None):
        self.asked.append({'url': url, 'data': data, 'auth': (headers or {}).get('Authorization')})
        if self.down:
            raise ConnectionError('no route to host')
        return Answer(self.said, self.status)


@override_settings(**FACES)
class FacesTest(TestCase):

    def setUp(self):
        cache.clear()
        self.justin = ThinkingEntity.objects.create(name='justin', is_biological_human=True)
        self.picture = media.store(PNG, added_by=self.justin)
        _, token = mood_auth.enrol_device(self.justin, 'phone', tier='key')
        self.signed_in = Client()
        self.signed_in.cookies[mood_auth.COOKIE] = token
        self.url = f'/api/media/{self.picture.sha256}/faces/'

    def ask(self, service, client=None):
        with mock.patch('requests.post', service.post):
            return (client or self.signed_in).post(self.url)

    def name(self, box, person, client=None):
        return (client or self.signed_in).post(self.url + 'name/', json.dumps({'box': box, 'person': person}),
                                               content_type='application/json')

    def test_asked_once_with_the_key_and_kept_for_everyone(self):
        service = FakeFaces()
        said = self.ask(service).json()
        self.assertEqual(len(service.asked), 1)
        self.assertEqual(service.asked[0]['url'], 'https://faces.example/match')
        self.assertEqual(service.asked[0]['auth'], 'Bearer ' + 'k' * 32)
        self.assertEqual(service.asked[0]['data'], PNG)
        self.assertEqual((said['looked'], said['width'], said['height'], len(said['faces'])), (True, 2048, 1536, 3))
        self.ask(service)
        self.assertEqual(len(service.asked), 1)  # the second time, as kept
        anyone = Client().get(self.url).json()  # reading needs no sign-in, as the picture doesn't
        self.assertEqual([f['box'] for f in anyone['faces']], [f['box'] for f in SAID['faces']])

    def test_what_a_face_looks_like_is_an_offer_not_a_name(self):
        said = self.ask(FakeFaces()).json()
        self.assertEqual(said['faces'][1]['match'], {'person': 'Justin Myles Holmes', 'similarity': 0.53})
        self.assertEqual([f['named'] for f in said['faces']], [None, None, None])
        self.assertEqual(faces.people_in(self.picture), [])
        self.assertEqual(said['faces'][0]['match'], None)  # below the line: no offer, just the nearest
        self.assertEqual(said['faces'][0]['candidates'][0]['person'], 'Justin Myles Holmes')

    def test_a_person_names_a_face_and_can_take_it_back(self):
        self.ask(FakeFaces())
        said = self.name([700, 320, 337, 337], 'justin_Myles Holmes').json()
        self.assertEqual(said['faces'][1]['named']['person'], 'Justin Myles Holmes')  # as PickiPedia titles it
        self.assertEqual(said['faces'][1]['named']['by'], 'justin')
        self.name([200, 350, 279, 279], 'Cory Walker')
        self.assertEqual(faces.people_in(self.picture), ['Cory Walker', 'Justin Myles Holmes'])  # left to right
        self.name([200, 350, 279, 279], 'Kyle Tuttle')  # someone else after all
        self.assertEqual(FaceName.objects.get(box=[200, 350, 279, 279]).person, 'Kyle Tuttle')
        said = self.name([200, 350, 279, 279], '').json()
        self.assertIsNone(said['faces'][2]['named'])
        self.assertEqual(faces.people_in(self.picture), ['Justin Myles Holmes'])

    def test_only_a_face_it_found_and_only_a_name(self):
        self.assertEqual(self.name([1, 2, 3, 4], 'Cory Walker').status_code, 404)  # not looked at yet
        self.ask(FakeFaces())
        self.assertEqual(self.name([1, 2, 3, 4], 'Cory Walker').status_code, 404)
        self.assertEqual(self.name('the left one', 'Cory Walker').status_code, 400)
        for markup in ('[[Cory Walker]]', '{{Delete}}', 'Cory|x', '<b>Cory</b>'):
            self.assertEqual(self.name([200, 350, 279, 279], markup).status_code, 400, markup)
        self.assertFalse(FaceName.objects.exists())

    def test_signed_in_to_ask_or_name(self):
        service = FakeFaces()
        self.assertEqual(self.ask(service, client=Client()).status_code, 401)
        self.assertEqual(service.asked, [])
        self.ask(service)
        self.assertEqual(self.name([200, 350, 279, 279], 'Cory Walker', client=Client()).status_code, 401)

    def test_a_service_that_wont_answer_or_wont_take_the_key_is_said_and_nothing_kept(self):
        self.assertEqual(self.ask(FakeFaces(down=True)).status_code, 502)
        refused = self.ask(FakeFaces({'error': 'needs the key'}, status=401))
        self.assertEqual(refused.status_code, 503)
        self.assertIn('key', refused.json()['error'])
        self.assertFalse(FaceLook.objects.exists())
        self.assertEqual(self.ask(FakeFaces()).status_code, 200)  # and asked again next time

    def test_a_gif_isnt_sent(self):
        gif = media.store(GIF, added_by=self.justin)
        self.assertFalse(Client().get(f'/api/media/{gif.sha256}/faces/').json()['can_look'])
        service = FakeFaces()
        with mock.patch('requests.post', service.post):
            self.assertEqual(self.signed_in.post(f'/api/media/{gif.sha256}/faces/').status_code, 400)
        self.assertEqual(service.asked, [])

    def test_not_set_up_no_button_and_nothing_sent(self):
        self.assertIn('const facesOn = true;', Client().get('/moods/').content.decode())
        with override_settings(FACES_KEY=''):
            self.assertIn('const facesOn = false;', Client().get('/moods/').content.decode())
            self.assertFalse(Client().get(self.url).json()['can_look'])
            service = FakeFaces()
            self.assertEqual(self.ask(service).status_code, 503)
            self.assertEqual(service.asked, [])

    def test_the_names_go_up_with_it_to_pickipedia(self):
        self.ask(FakeFaces())
        self.name([700, 320, 337, 337], 'Justin Myles Holmes')
        self.name([200, 350, 279, 279], 'Cory Walker')
        mood = Mood.objects.create(slug='general', title='general')
        message = Message.objects.create(id=uuid.uuid4(), sender=self.justin, mood=mood, timestamp=1,
                                         content=f'![image]({self.picture.url})', source_file='mood-web')
        from conversations.tests.test_wiki_upload import BACK, CONSUMER, FakePickiPedia
        wiki = FakePickiPedia()
        with override_settings(**CONSUMER), \
                mock.patch('conversations.services.wiki_auth.names', return_value={'justin': 'JMyles'}):
            started = wiki_upload.begin(self.picture, 'IBMA', '', 'cc-by-sa-4.0', 'justin', mood, message.id, BACK, http=wiki)
            wiki_upload.finish(started['state'], 'code-1', 'justin', BACK, http=wiki)
        self.assertIn('{{Depicts|Cory Walker|Justin Myles Holmes}}', wiki.uploads[0]['data']['text'])

    def test_nobody_named_no_depicts(self):
        self.assertNotIn('Depicts', wiki_upload.page_text('porch', 'cc0', 'https://x'))
