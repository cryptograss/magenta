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

    def test_their_pickipedia_name_any_case_its_spaces_aside(self):
        self.assertTrue(art_fits('~=[ j.M.y.L.e.S ]=~', 'JMyles'))
        self.assertTrue(art_fits('JUSTIN*HOLMES', 'Justin Holmes'))
        self.assertFalse(art_fits('justin', 'JMyles'))


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
        self.assertEqual(done.json(), {'art': '░▒▓ JuStIn ▓▒░', 'colours': '', 'backs': '', 'colour': '#ff3c00'})
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
        self.assertEqual(self.look(client, art='', colour='').json(), {'art': '', 'colours': '', 'backs': '', 'colour': ''})
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

    def test_a_background_for_each_character_too(self):
        # Justin, 21 Oct: "the BBSs I remember... had more colors" -- each cell had its background (iCE colour).
        client = self.client_for(self.justin)
        done = self.look(client, art='[justin]', colours='.eeeeee.', backs='11111111..').json()
        self.assertEqual((done['colours'], done['backs']), ('.eeeeee', '11111111'))
        people = {p['name']: p for p in self.client.get('/api/moods/').json()['people']}
        self.assertEqual(people['justin']['backs'], '11111111')
        self.assertEqual(self.look(client, backs='1g').status_code, 400)

    def test_only_ansi_codes(self):
        client = self.client_for(self.justin)
        for colours in ('99g', '#ff00', '#ff0000;x', '<b>', 'c' * 31):  # a colour of their own is fine (SafeArtTest)
            self.assertEqual(self.look(client, art='justin', colours=colours).status_code, 400, colours)

    # The art writes their PickiPedia name (Justin, 21 Oct: "use the pickipedia username").
    def test_written_to_their_pickipedia_name(self):
        from unittest import mock
        with mock.patch('conversations.services.wiki_auth.names', return_value={'justin': 'JMyles'}):
            client = self.client_for(self.justin)
            refused = self.look(client, art='-=[ justin ]=-')
            self.assertEqual(refused.status_code, 400)
            self.assertIn('JMyles', refused.json()['error'])
            self.assertEqual(self.look(client, art='-=[ jmYLES ]=-').json()['art'], '-=[ jmYLES ]=-')


class SafeArtTest(TestCase):
    """Justin, 25 Oct: only shapes besides the name's letters; any colour; and never a name blanked out."""

    def setUp(self):
        self.justin = ThinkingEntity.objects.update_or_create(name='justin', defaults={'is_biological_human': True})[0]

    def client_for(self, who):
        _, token = mood_auth.enrol_device(who, 'laptop', tier='key')
        client = Client()
        client.cookies[mood_auth.COOKIE] = token
        return client

    def look(self, client, **body):
        return client.post('/api/me/look/', json.dumps(body), content_type='application/json')

    def test_only_shapes_beside_the_letters(self):
        for art in ('░▒▓ justin ▓▒░', '♥ j.u.s.t.i.n ♥', '─┤justin├─'):
            self.assertTrue(art_fits(art, 'justin'), art)
        # another name beside it, numbers, look-alike letters, circled letters, letterlike symbols, combining marks
        for art in ('justin re', 'justin 2', 'xjustin', 'justinⓡⓔ', 'justin™', 'justiñ', 'jυstin', 'justiń'):
            self.assertFalse(art_fits(art, 'justin'), art)

    def test_any_colour_of_their_own(self):
        client = self.client_for(self.justin)
        r = self.look(client, art='[justin]', colours='.,#c2185b,c,#1e88e5', backs='')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['colours'], '.,#c2185b,c,#1e88e5')
        self.assertEqual(self.look(client, art='[justin]', colours='.,#c2185,c').status_code, 400)

    def test_a_letter_never_blanked_out(self):
        client = self.client_for(self.justin)
        same = self.look(client, art='justin', colours='4' * 6, backs='4' * 6)  # red on red: gone
        self.assertEqual(same.status_code, 400)
        self.assertIn('hard to see', same.json()['error'])
        near = self.look(client, art='justin', colours='#101010,' * 5 + '#101010', backs='0' * 6)  # black on near-black
        self.assertEqual(near.status_code, 400)
        page = self.look(client, art='justin', colours='#161417,' * 5 + '#161417')  # the dark page's own colour
        self.assertEqual(page.status_code, 400)
        # the shapes around it may be anything: only the name's letters must be seen
        ok = self.look(client, art='▓justin▓', colours='0ffffff0', backs='0000000' + '0')
        self.assertEqual(ok.status_code, 200, ok.content)

    def test_one_set_before_the_rules_is_their_name_as_it_is(self):
        from conversations.services import settings as knobs
        knobs.change('name_art', 'justin', agent=self.justin, by=self.justin)
        knobs.change('name_colours', '444444', agent=self.justin, by=self.justin)
        knobs.change('name_backs', '444444', agent=self.justin, by=self.justin)
        self.assertNotIn('justin', knobs.looks())
