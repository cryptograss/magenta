"""A person's name written their way, BBS-style, and their icon's colour (Justin, 21 Oct): at most 30
characters, every letter of their name in them, in order."""

import json

from django.test import Client, TestCase

from conversations.models import ThinkingEntity
from conversations.services import mood_auth
from conversations.services.settings import art_fits


class ArtFitsTest(TestCase):

    def test_every_letter_in_order_at_most_thirty(self):
        for art in ('justin', 'JUSTIN', 'j.u.s.t.i.n', '░▒▓ JuStIn ▓▒░', '-=[ j u s t i n ]=-', '~*~justin~*~'):
            self.assertTrue(art_fits(art, 'justin'), art)
        for art in ('jsutin', 'justi', 'nitsuj', 'x' * 25 + 'justin', 'just\nin', ''):
            self.assertFalse(art_fits(art, 'justin'), art)


class LookTest(TestCase):

    def setUp(self):
        self.justin = ThinkingEntity.objects.create(name='justin', is_biological_human=True)
        self.magent = ThinkingEntity.objects.create(name='magent', is_biological_human=False)

    def client_for(self, who, tier='key'):
        _, token = mood_auth.enrol_device(who, 'laptop', tier=tier)
        client = Client()
        client.cookies[mood_auth.COOKIE] = token
        return client

    def look(self, client, **body):
        return client.post('/api/me/look/', json.dumps(body), content_type='application/json')

    def test_set_from_any_device_and_shown_with_the_people(self):
        done = self.look(self.client_for(self.justin, tier='wiki'), art='░▒▓ JuStIn ▓▒░', colour='#FF3C00')
        self.assertEqual(done.json(), {'art': '░▒▓ JuStIn ▓▒░', 'colours': '', 'colour': '#ff3c00'})
        people = {p['name']: p for p in self.client.get('/api/moods/').json()['people']}
        self.assertEqual((people['justin']['art'], people['justin']['colour']), ('░▒▓ JuStIn ▓▒░', '#ff3c00'))
        self.assertNotIn('art', people['magent'])

    def test_only_what_writes_your_name_and_a_real_colour(self):
        client = self.client_for(self.justin)
        self.assertEqual(self.look(client, art='skyler').status_code, 400)
        self.assertEqual(self.look(client, art='j' * 31).status_code, 400)
        self.assertEqual(self.look(client, colour='red; background: url(x)').status_code, 400)
        self.assertEqual(self.look(client, colour='#12345').status_code, 400)

    def test_plain_again_and_never_an_agents_or_a_strangers(self):
        client = self.client_for(self.justin)
        self.look(client, art='j-u-s-t-i-n', colour='#00ff00')
        self.assertEqual(self.look(client, art='', colour='').json(), {'art': '', 'colours': '', 'colour': ''})
        self.assertNotIn('art', next(p for p in self.client.get('/api/moods/').json()['people'] if p['name'] == 'justin'))
        self.assertEqual(self.look(self.client_for(self.magent), art='m-a-g-e-n-t').status_code, 403)
        self.assertEqual(self.look(Client(), art='justin').status_code, 401)

    # Each character in one of ANSI's sixteen (Justin, 21 Oct: "I'd like to be able to color individual characters").
    def test_a_code_for_each_character_cut_to_the_art(self):
        client = self.client_for(self.justin)
        done = self.look(client, art='░▒▓ JuStIn ▓▒░', colours='999.cccccc.999..')
        self.assertEqual(done.json()['colours'], '999.cccccc.999')  # cut to the art's 14, trailing none dropped
        people = {p['name']: p for p in self.client.get('/api/moods/').json()['people']}
        self.assertEqual(people['justin']['colours'], '999.cccccc.999')
        self.look(client, art='justin')  # shorter art: the colours go only as far as it does
        self.assertEqual(self.look(client).json()['colours'], '999.cc')

    def test_only_ansi_codes(self):
        client = self.client_for(self.justin)
        for colours in ('99g', '#ff0000', '<b>', 'c' * 31):
            self.assertEqual(self.look(client, art='justin', colours=colours).status_code, 400, colours)
