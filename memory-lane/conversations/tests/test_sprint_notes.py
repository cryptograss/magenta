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
        self.assertIn(f'{{{{Magenta message\n|id={said.id}\n|who=re\n|mood=magenta-interface\n', page)  # a card,
        self.assertIn('|chars=all\n', page)                                                          # every word
        self.assertIn('|text=Ship the bar first.\n&lt;script&gt;x&lt;/script&gt; and &#91;&#91;Not a link&#93;&#93;\n}}', page)
        self.assertIn("A note for justin, from #magenta-interface", self.bot.edits[0][1])
        again = sprint_notes.add(said, 'justin', bot=self.bot)
        self.assertTrue(again['already'])
        self.assertEqual(len(self.bot.edits), 1)

    def test_a_reply_noted_by_its_words(self):
        first = self.say(self.justin, 'What first?')
        reply = self.say(self.justin, f'↩ #m-{first.id}\nThe bar.')
        sprint_notes.add(reply, 'justin', bot=self.bot)
        page = self.bot.pages['Magenta:Sprints/Jellyfish/JMyles']
        self.assertIn('|text=The bar.\n}}', page)
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


class WikiEmbedTest(TestCase):
    """⧉ Copy for PickiPedia: {{Magenta message}}, words and all (services/wiki_embed.py; Justin, 25 Oct)."""

    @classmethod
    def setUpTestData(cls):
        cls.justin, _ = ThinkingEntity.objects.update_or_create(name='justin', defaults={'is_biological_human': True})
        cls.mood = Mood.objects.create(slug='rh', title='rh')

    def test_the_call(self):
        said = Message.objects.create(id=uuid.uuid4(), sender=self.justin, mood=self.mood, timestamp=1,
                                      eth_blockheight=26266736, content='a | b {{c}} [[d]] ~~~~ x=y & <i>')
        with mock.patch('conversations.services.wiki_auth.names', return_value={'justin': 'JMyles'}):
            got = Client().get(f'/api/messages/{said.id}/wiki/?chars=120').json()['wikitext']
        self.assertTrue(got.startswith(f'{{{{Magenta message\n|id={said.id}\n|who=justin\n|user=JMyles\n|mood=rh\n|at='))
        self.assertIn('|block=26266736\n|chars=120\n', got)
        self.assertIn('|text=a &#124; b &#123;&#123;c&#125;&#125; &#91;&#91;d&#93;&#93; &#126;&#126;&#126;&#126; '
                      'x&#61;y &amp; &lt;i&gt;\n}}', got)
        self.assertEqual(Client().get(f'/api/messages/{said.id}/wiki/?chars=lots').status_code, 400)
