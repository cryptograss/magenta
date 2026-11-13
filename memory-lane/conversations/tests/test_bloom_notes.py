"""📝 Add to my bloom notes (services/bloom_notes.py; Justin, 24 Oct; the shape settled with re, 25 Oct)."""

import json
import uuid
from unittest import mock

from django.test import Client, TestCase

from conversations.models import Message, MessageChange, Mood, ThinkingEntity
from conversations.services import mood_auth, bloom_notes


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


class BloomNotesTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.justin, _ = ThinkingEntity.objects.update_or_create(name='justin', defaults={'is_biological_human': True})
        cls.re, _ = ThinkingEntity.objects.update_or_create(name='re', defaults={'is_biological_human': False})
        cls.mood = Mood.objects.create(slug='magenta-interface', title='magenta-interface')

    def setUp(self):
        self.bot = FakeBot()
        names = mock.patch('conversations.services.wiki_auth.names', return_value={'justin': 'JMyles'})
        bloom = mock.patch.object(bloom_notes, 'current', return_value=('Jellyfish', 'Cryptograss/Jellyfish'))
        names.start(), bloom.start()
        self.addCleanup(names.stop), self.addCleanup(bloom.stop)

    def say(self, sender, text):
        return Message.objects.create(id=uuid.uuid4(), sender=sender, mood=self.mood, timestamp=1,
                                      content=text if sender.is_biological_human else [{'type': 'text', 'text': text}])

    def test_the_current_bloom_is_what_the_pointer_names(self):
        self.assertEqual(bloom_notes.bloom_from("<!-- move me -->\n'''[[Cryptograss/Jellyfish]]'''"),
                         ('Jellyfish', 'Cryptograss/Jellyfish'))
        self.assertEqual(bloom_notes.bloom_from('* RH beta\n'), ('RH beta', None))
        self.assertEqual(bloom_notes.bloom_from('<!-- nothing yet -->'), (None, None))

    def test_a_message_into_my_notes_words_and_link_once(self):
        said = self.say(self.re, 'Ship the bar first.\n<script>x</script> and [[Not a link]]')
        done = bloom_notes.add(said, 'justin', bot=self.bot)
        self.assertEqual((done['bloom'], done['page'], done['already']),
                         ('Jellyfish', 'Cryptograss:Blooms/Jellyfish/JMyles', False))
        page = self.bot.pages['Cryptograss:Blooms/Jellyfish/JMyles']
        self.assertIn('Notes for the [[Cryptograss/Jellyfish|Jellyfish]] bloom, kept by [[User:JMyles|JMyles]]', page)
        self.assertIn(f'{{{{Magenta message\n|id={said.id}\n|who=re\n|mood=magenta-interface\n', page)  # a card,
        self.assertIn('|chars=all\n', page)                                                          # every word
        self.assertIn('|text=Ship the bar first.\n&lt;script&gt;x&lt;/script&gt; and &#91;&#91;Not a link&#93;&#93;\n}}', page)
        self.assertIn("A note for justin, from #magenta-interface", self.bot.edits[0][1])
        again = bloom_notes.add(said, 'justin', bot=self.bot)
        self.assertTrue(again['already'])
        self.assertEqual(len(self.bot.edits), 1)

    def test_a_reply_noted_by_its_words(self):
        first = self.say(self.justin, 'What first?')
        reply = self.say(self.justin, f'↩ #m-{first.id}\nThe bar.')
        bloom_notes.add(reply, 'justin', bot=self.bot)
        page = self.bot.pages['Cryptograss:Blooms/Jellyfish/JMyles']
        self.assertIn('|text=The bar.\n}}', page)
        self.assertNotIn('↩', page)

    def test_from_the_menu(self):
        _, token = mood_auth.enrol_device(self.justin, 'laptop')
        client = Client()
        client.cookies[mood_auth.COOKIE] = token
        said = self.say(self.re, 'Worth keeping.')
        url = f'/api/moods/magenta-interface/messages/{said.id}/bloom-note/'
        with mock.patch('conversations.services.todo_ticks.enabled', return_value=True), \
                mock.patch('conversations.services.todo_ticks.Bot', return_value=self.bot):
            done = client.post(url, '{}', content_type='application/json')
            self.assertEqual(done.status_code, 200, done.content)
            self.assertEqual(done.json()['page'], 'Cryptograss:Blooms/Jellyfish/JMyles')
            MessageChange.objects.create(message_id=said.id, mood=self.mood, kind='deleted', by=self.justin)
            self.assertEqual(client.post(url, '{}', content_type='application/json').status_code, 410)
        self.assertEqual(Client().post(url, '{}', content_type='application/json').status_code, 401)

    def test_adds_event_message_in_mood_and_in_general(self):
        general, _ = Mood.objects.get_or_create(slug='general', defaults={'title': 'general'})
        said = self.say(self.re, 'Track this for the bloom.')
        bloom_notes.add(said, 'justin', bot=self.bot)
        # 1. Event line in the message's mood
        in_mood = Message.objects.filter(mood=self.mood, source_file='bloom-note').first()
        self.assertIsNotNone(in_mood)
        self.assertEqual(in_mood.content['by'], 'justin')
        self.assertEqual(in_mood.content['message'], str(said.id))
        self.assertEqual(in_mood.content['bloom'], 'Jellyfish')
        # 2. Event line in #general
        in_gen = Message.objects.filter(mood=general, source_file='bloom-note').first()
        self.assertIsNotNone(in_gen)
        self.assertEqual(in_gen.content['mood'], 'magenta-interface')
        self.assertEqual(in_gen.content['message'], str(said.id))

        # 3. Noted in #general itself: only one event message created
        said_in_gen = Message.objects.create(id=uuid.uuid4(), sender=self.re, mood=general, timestamp=2,
                                             content=[{'type': 'text', 'text': 'General note'}])
        bloom_notes.add(said_in_gen, 'justin', bot=self.bot)
        gen_events = Message.objects.filter(mood=general, source_file='bloom-note', content__message=str(said_in_gen.id))
        self.assertEqual(gen_events.count(), 1)


class BloomShownTest(TestCase):
    """The current bloom in the header's menu (Justin, 3 Nov), with /api/servers/."""

    def setUp(self):
        from django.core.cache import cache
        cache.delete('current-bloom')

    def test_the_old_pointer_is_read_until_the_new_one_is_there(self):
        """A sprint is a bloom now (13 Nov): Magenta:Current sprint names it until the page moves."""
        pages = {'Magenta:Current sprint': '[[Cryptograss/Jackalope]]'}

        def get(url, params=None, **_):
            text = pages.get(params['title'])
            return mock.Mock(status_code=200 if text is not None else 404, text=text or '')
        http = mock.Mock(get=get)
        self.assertEqual(bloom_notes.current(http), ('Jackalope', 'Cryptograss/Jackalope'))
        pages['Magenta:Current bloom'] = '[[Cryptograss/Kinkajou]]'
        self.assertEqual(bloom_notes.current(http), ('Kinkajou', 'Cryptograss/Kinkajou'))
        pages.clear()
        with self.assertRaises(bloom_notes.Refused):
            bloom_notes.current(http)

    def test_named_linked_and_kept_none_when_the_wiki_cant_say(self):
        from django.test import override_settings
        with override_settings(INTERFACE_WORDS_FROM_WIKI=True, PICKIPEDIA_URL='https://wiki.test'):
            with mock.patch.object(bloom_notes, 'current', return_value=('Jellyfish', 'Cryptograss/Jellyfish')):
                self.assertEqual(bloom_notes.shown(), {'name': 'Jellyfish', 'url': 'https://wiki.test/wiki/Cryptograss/Jellyfish'})
            from django.core.cache import cache
            cache.delete('current-bloom')
            with mock.patch.object(bloom_notes, 'current', side_effect=bloom_notes.Refused('none', 409)):
                self.assertIsNone(bloom_notes.shown())
        self.assertIn('bloom', Client().get('/api/servers/').json())


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

    def test_the_inline_citation(self):
        """?inline=1: one line, no words, for citing in prose (Justin, 3 Nov, #docs-tests-qa)."""
        said = Message.objects.create(id=uuid.uuid4(), sender=self.justin, mood=self.mood, timestamp=1,
                                      eth_blockheight=26266736, content='words that stay out of it')
        with mock.patch('conversations.services.wiki_auth.names', return_value={'justin': 'JMyles'}):
            got = Client().get(f'/api/messages/{said.id}/wiki/?inline=1').json()['wikitext']
        self.assertTrue(got.startswith(f'{{{{Magenta message|id={said.id}|who=justin|user=JMyles|mood=rh|at='))
        self.assertTrue(got.endswith('|block=26266736|inline=yes}}'))
        self.assertNotIn('\n', got)
        self.assertNotIn('words', got)
