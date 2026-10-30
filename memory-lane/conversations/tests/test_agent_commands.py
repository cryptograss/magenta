"""Commands for agents in their one form (services/agent_commands; Justin, 17 Oct):
"/compact <agent>[,<agent>...] <what to keep>", refused before it's posted otherwise."""

import json
import uuid

from django.test import TestCase

from conversations.models import HarnessModels, Message, Mood, ThinkingEntity
from conversations.services import agent_commands as ac
from conversations.services import mood_auth
from conversations.services.mood_view import addressed_in, command_doing

AGENTS = {'magent', 're'}


class ParseTest(TestCase):

    def test_the_form(self):
        self.assertEqual(ac.parse('/compact magent keep the setlist', AGENTS), ('compact', ['magent'], 'keep the setlist'))
        self.assertEqual(ac.parse('/compact magent, re keep it', AGENTS), ('compact', ['magent', 're'], 'keep it'))
        self.assertEqual(ac.parse('/compact @magent,re keep it', AGENTS), ('compact', ['magent', 're'], 'keep it'))
        self.assertEqual(ac.parse('/context magent', AGENTS), ('context', ['magent'], ''))

    def test_not_a_command_at_all(self):
        for text in ('hello', 'should we /compact soon?', '/clips banjo', '@magent what does /compact keep?',
                     'run `@magent /compact`, @JMyles'):
            with self.subTest(text=text):
                self.assertIsNone(ac.parse(text, AGENTS))

    def test_refused_with_the_form_it_should_take(self):
        cases = {
            '/compact': 'which agent?',
            '/compact skyler keep it': 'skyler: not an agent here',
            '/usage magent of the word banjo': 'takes nothing after its agents',
            '@magent /compact keep the list': '/compact magent [what to keep]',
            'hey @magent /compact - heres what i want': 'commands for agents start the message now',  # Skyler, 17 Oct
        }
        for text, said in cases.items():
            with self.subTest(text=text):
                with self.assertRaises(ac.Invalid) as refused:
                    ac.parse(text, AGENTS)
                self.assertIn(said, str(refused.exception))


class AddressedTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        for name, human in (('justin', True), ('magent', False), ('re', False)):  # re: made by migration 0024
            ThinkingEntity.objects.get_or_create(name=name, defaults={'is_biological_human': human})

    def test_a_command_addresses_the_agents_it_names_and_says_compacting(self):
        names = {'justin', 'magent', 're'}
        self.assertEqual(addressed_in('/compact magent,re keep it', names), ['magent', 're'])
        self.assertEqual(addressed_in('↩ #m-0b9c2a51-1111-4c3e-9d2f-6a7b8c9d0e1f\n/compact re keep it', names), ['re'])
        self.assertEqual(addressed_in('/compact magent', names), ['magent'])  # what to keep is optional (Justin, 30 Oct)
        self.assertEqual(command_doing('/compact magent keep it', 'magent'), 'compacting')
        self.assertEqual(command_doing('/compact magent', 'magent'), 'compacting')
        self.assertIsNone(command_doing('/compact re keep it', 'magent'))


class SayTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.justin = ThinkingEntity.objects.create(name='justin', is_biological_human=True)
        ThinkingEntity.objects.create(name='magent', is_biological_human=False)
        ThinkingEntity.objects.get_or_create(name='re', defaults={'is_biological_human': False})  # migration 0024 made it
        Mood.objects.create(slug='m26', title='m26')

    def setUp(self):
        _, token = mood_auth.enrol_device(self.justin, 'laptop', tier='key')
        self.client.cookies[mood_auth.COOKIE] = token

    def say(self, text):
        return self.client.post('/api/moods/m26/say/', json.dumps({'text': text}), content_type='application/json')

    def test_the_form_is_posted_anything_like_it_is_refused_and_not_posted(self):
        self.assertEqual(self.say('/compact magent keep the setlist').status_code, 201)
        refused = self.say('hey @magent /compact - heres what i want to tackle')
        self.assertEqual(refused.status_code, 400)
        self.assertIn('/compact magent [what to keep]', refused.json()['error'])
        # Words are optional (Justin, 30 Oct: "times when I don't really have anything to say in compact")
        self.assertEqual(self.say('/compact magent').status_code, 201)
        self.assertEqual(Message.objects.filter(mood__slug='m26').count(), 2)
        self.assertEqual(self.say('should we /compact soon, @magent?').status_code, 201)  # talk about one

    def test_an_agent_on_pi_compacts_and_has_none_of_the_others(self):
        HarnessModels.objects.create(agent_id='re', harness='pi 1.1.0', vendors=[])
        self.assertEqual(self.say('/compact re keep it').status_code, 201)
        self.assertEqual(self.say('/compact magent, re keep the list').status_code, 201)
        refused = self.say('/context re')
        self.assertEqual(refused.status_code, 400)
        self.assertIn("Claude Code's; not for re (pi 1.1.0)", refused.json()['error'])
        HarnessModels.objects.filter(agent_id='re').update(harness='goose 2')
        self.assertIn('not for re (goose 2)', self.say('/compact re keep it').json()['error'])


class IncorporateParseTest(TestCase):

    def test_the_form_and_who_it_is_for(self):
        self.assertEqual(ac.parse('/incorporate #jams-setlist', AGENTS), ('incorporate', ['magent'], '#jams-setlist'))
        self.assertEqual(ac.parse('/incorporate #Jams-Setlist the key, mostly', AGENTS),
                         ('incorporate', ['magent'], '#jams-setlist the key, mostly'))
        with self.assertRaises(ac.Invalid):
            ac.parse('/incorporate the setlist', AGENTS)
        self.assertIn('incorporate', [s[0] for s in ac.suggestions()])
        self.assertEqual(ac.parse('/handoff #jams the setlist', AGENTS), ('handoff', ['magent'], '#jams the setlist'))
        with self.assertRaises(ac.Invalid):
            ac.parse('/handoff', AGENTS)
