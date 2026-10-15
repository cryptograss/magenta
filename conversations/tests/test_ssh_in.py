"""Someone ssh'd into a server: said in #general, every time (services/access.py)."""

import json

from django.test import TestCase, override_settings

from conversations.models import Message, Mood

KEY = 'a' * 64
AUTH = {'HTTP_AUTHORIZATION': f'Bearer {KEY}'}


@override_settings(MOOD_ACCESS_KEY=KEY)
class SshInTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.general = Mood.objects.create(slug='general', title='general')

    def tell(self, body, auth=AUTH):
        return self.client.post('/api/access/ssh/', json.dumps(body), content_type='application/json', **auth)

    def lines(self):
        return [m.content for m in Message.objects.filter(source_file='access', mood=self.general).order_by('created_at')]

    def test_only_the_access_key_can_say_so(self):
        with override_settings(MOOD_ACCESS_KEY=''):
            self.assertEqual(self.tell({'server': 'hunter', 'who': 'justin'}).status_code, 503)
        self.assertEqual(self.tell({'server': 'hunter', 'who': 'justin'}, auth={}).status_code, 401)
        self.assertEqual(self.tell({'server': 'hunter', 'who': 'justin'},
                                   auth={'HTTP_AUTHORIZATION': 'Bearer nope'}).status_code, 401)
        self.assertEqual(self.lines(), [])

    def test_a_shell_on_hunter_is_said_in_general(self):
        self.assertEqual(self.tell({'server': 'hunter', 'who': 'justin', 'how': 'tmux magenta'}).status_code, 201)
        self.assertEqual(self.lines(), [{'type': 'access', 'kind': 'ssh', 'who': 'justin', 'server': 'hunter',
                                         'label': 'tmux magenta'}])
        body = self.client.get('/api/moods/general/turns/').json()
        [event] = body['events']
        self.assertEqual((event['kind'], event['who'], event['server'], event['label']), ('ssh', 'justin', 'hunter', 'tmux magenta'))

    def test_every_login_is_said_and_joining_someone_is_its_own(self):
        self.tell({'server': 'hunter', 'who': 'skyler', 'how': 'a shell'})
        self.assertEqual(self.tell({'server': 'hunter', 'who': 'skyler', 'how': 'tmux magenta'}).json(), {'announced': True})
        self.tell({'server': 'hunter', 'who': 'skyler', 'into': 'justin', 'how': 'tmux jam'})
        self.tell({'server': 'hunter', 'who': 'justin', 'into': 'justin'})  # their own container, said plainly
        self.assertEqual([(c['who'], c.get('for')) for c in self.lines()],
                         [('skyler', None), ('skyler', None), ('skyler', 'justin'), ('justin', None)])

    def test_what_isnt_an_account_or_a_server_is_refused(self):
        for body in ({'server': 'hunter'}, {'server': 'elsewhere', 'who': 'justin'},
                     {'server': 'hunter', 'who': 'justin; rm -rf'}, {'server': 'hunter', 'who': 'justin', 'into': '<b>x</b>'}):
            self.assertEqual(self.tell(body).status_code, 400, body)
        self.assertEqual(self.lines(), [])

    def test_as_root_on_any_box_and_an_unknown_key_by_its_fingerprint(self):
        fp = 'SHA256:' + 'a' * 43
        self.assertEqual(self.tell({'server': 'maybelle', 'who': 'justin', 'as': 'root'}).status_code, 201)
        self.assertEqual(self.tell({'server': 'delivery-kid', 'who': 'someone', 'as': 'root', 'key': fp}).status_code, 201)
        self.tell({'server': 'pickipedia', 'who': 'skyler', 'as': 'skyler'})  # as themselves: no "as"
        self.assertEqual([(c['server'], c['who'], c.get('as'), c.get('key')) for c in self.lines()],
                         [('maybelle', 'justin', 'root', None), ('delivery-kid', 'someone', 'root', fp),
                          ('pickipedia', 'skyler', None, None)])
        # As root is its own visit from as themselves.
        self.assertEqual(self.tell({'server': 'maybelle', 'who': 'justin'}).status_code, 201)
        events = self.client.get('/api/moods/general/turns/').json()['events']
        self.assertEqual([(e['server'], e.get('as'), e.get('key')) for e in events][:2], [('maybelle', 'root', None), ('delivery-kid', 'root', fp)])

    def test_a_key_must_look_like_a_fingerprint(self):
        self.assertEqual(self.tell({'server': 'maybelle', 'who': 'someone', 'key': '<script>'}).status_code, 400)
        self.assertEqual(self.tell({'server': 'maybelle', 'who': 'someone', 'as': 'Root!'}).status_code, 400)
