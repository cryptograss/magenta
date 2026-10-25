"""📝 Add to my sprint notes (services/sprint_notes.py; Justin, 24 Oct; the shape settled with re, 25 Oct)."""

import json
import uuid
from unittest import mock

from django.test import Client, TestCase

from conversations.models import Message, MessageChange, Mood, ThinkingEntity
from conversations.services import mood_auth, sprint_notes


class FakeBot:
    def __init__(self):
        self.pages, self.edits = {}, []

    def login(self):
        return True

    def read(self, title):
        text = self.pages.get(title)
        return None if text is None else {'revid': len(self.edits), 'timestamp': 't', 'slots': {'main': {'content': text}}}

    def edit(self, title, text, summary, revision=None):
        self.pages[title] = text
        self.edits.append((title, summary))
        return {'edit': {'result': 'Success'}}


class SprintNotesTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.justin, _ = ThinkingEntity.objects.update_or_create(name='justin', defaults={'is_biological_human': True})
        cls.re, _ = ThinkingEntity.objects.update_or_create(name='re', defaults={'is_biological_human': False})
        cls.mood = Mood.objects.create(slug='magenta-interface', title='magenta-interface')

    def setUp(self):
        self.bot = FakeBot()
        names = mock.patch('conversations.services.wiki_auth.names', return_value={'justin': 'JMyles'})
        sprint = mock.patch.object(sprint_notes, 'current', return_value=('Jellyfish', 'Cryptograss/Jellyfish'))
        names.start(), sprint.start()
        self.addCleanup(names.stop), self.addCleanup(sprint.stop)

    def say(self, sender, text):
        return Message.objects.create(id=uuid.uuid4(), sender=sender, mood=self.mood, timestamp=1,
                                      content=text if sender.is_biological_human else [{'type': 'text', 'text': text}])

    def test_the_current_sprint_is_what_the_pointer_names(self):
        self.assertEqual(sprint_notes.sprint_from("<!-- move me -->\n'''[[Cryptograss/Jellyfish]]'''"),
                         ('Jellyfish', 'Cryptograss/Jellyfish'))
        self.assertEqual(sprint_notes.sprint_from('* RH beta\n'), ('RH beta', None))
        self.assertEqual(sprint_notes.sprint_from('<!-- nothing yet -->'), (None, None))

    def test_a_message_into_my_notes_words_and_link_once(self):
        said = self.say(self.re, 'Ship the bar first.\n<script>x</script> and [[Not a link]]')
        done = sprint_notes.add(said, 'justin', bot=self.bot)
        self.assertEqual((done['sprint'], done['page'], done['already']),
                         ('Jellyfish', 'Magenta:Sprints/Jellyfish/JMyles', False))
        page = self.bot.pages['Magenta:Sprints/Jellyfish/JMyles']
        self.assertIn('Notes for the [[Cryptograss/Jellyfish|Jellyfish]] sprint, kept by [[User:JMyles|JMyles]]', page)
        self.assertIn(f'https://magenta.cryptograss.live/messages/{said.id} ', page)
        self.assertIn('#magenta-interface', page)
        self.assertIn('<nowiki>Ship the bar first.</nowiki><br><nowiki>&lt;script&gt;x&lt;/script&gt; and [[Not a link]]</nowiki>', page)
        self.assertIn("A note for justin, from #magenta-interface", self.bot.edits[0][1])
        again = sprint_notes.add(said, 'justin', bot=self.bot)
        self.assertTrue(again['already'])
        self.assertEqual(len(self.bot.edits), 1)

    def test_a_reply_noted_by_its_words(self):
        first = self.say(self.justin, 'What first?')
        reply = self.say(self.justin, f'↩ #m-{first.id}\nThe bar.')
        sprint_notes.add(reply, 'justin', bot=self.bot)
        page = self.bot.pages['Magenta:Sprints/Jellyfish/JMyles']
        self.assertIn('<nowiki>The bar.</nowiki>', page)
        self.assertNotIn('↩', page)

    def test_from_the_menu(self):
        _, token = mood_auth.enrol_device(self.justin, 'laptop')
        client = Client()
        client.cookies[mood_auth.COOKIE] = token
        said = self.say(self.re, 'Worth keeping.')
        url = f'/api/moods/magenta-interface/messages/{said.id}/sprint-note/'
        with mock.patch('conversations.services.todo_ticks.enabled', return_value=True), \
                mock.patch('conversations.services.todo_ticks.Bot', return_value=self.bot):
            done = client.post(url, '{}', content_type='application/json')
            self.assertEqual(done.status_code, 200, done.content)
            self.assertEqual(done.json()['page'], 'Magenta:Sprints/Jellyfish/JMyles')
            MessageChange.objects.create(message_id=said.id, mood=self.mood, kind='deleted', by=self.justin)
            self.assertEqual(client.post(url, '{}', content_type='application/json').status_code, 410)
        self.assertEqual(Client().post(url, '{}', content_type='application/json').status_code, 401)
