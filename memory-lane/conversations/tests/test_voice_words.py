"""The voice's words on PickiPedia (services/voice_words.py; Justin, 24 Oct): Magenta:Voice's Heard as,
Expected words and Said as, as a trusted editor last left them."""

import uuid

from django.core.cache import cache
from django.test import TestCase, override_settings

from conversations.models import Message, Mood, ThinkingEntity
from conversations.services import voice, voice_words

from .test_voice import FakeEleven

PAGE = """Words the voice uses. Anyone can read this; only a trusted editor's revision is used.

== Heard as ==
* magnet → magent
* Ree -> re
* not a pair
<!-- * hidden → never -->

== Expected words ==
* banjo
* Old Crow Medicine Show
* {{Bot proposes|dobro}}

== Said as ==
* PickiPedia → picky pedia
* re => ray
<nowiki>* example → never read</nowiki>

== Something else ==
* ignored
"""


@override_settings(ELEVENLABS_API_KEY="test-eleven-key")
class VoiceWordsTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.magent = ThinkingEntity.objects.create(name='magent', is_biological_human=False)
        ThinkingEntity.objects.create(name='justin', is_biological_human=True)
        cls.mood = Mood.objects.create(slug='m26', title='magenta-interface')

    def setUp(self):
        cache.clear()

    def test_the_page_read(self):
        found = voice_words.parse(PAGE)
        self.assertEqual(found['heard_as'], {'magnet': 'magent', 'ree': 're'})
        self.assertEqual(found['keyterms'], ['banjo', 'Old Crow Medicine Show'])  # a template line is no word
        self.assertEqual(found['said_as'], {'PickiPedia': 'picky pedia', 're': 'ray'})

    def test_the_page_or_the_last_read_or_this_files(self):
        self.assertEqual(voice_words.words(fetch=lambda: PAGE)['said_as']['re'], 'ray')
        cache.delete('voice-words')  # ten minutes on: the wiki away
        self.assertEqual(voice_words.words(fetch=lambda: 1 / 0)['said_as']['re'], 'ray')  # the last good read
        cache.clear()
        self.assertEqual(voice_words.words(fetch=lambda: None), voice_words.defaults())  # no trusted revision, ever

    def test_said_as_whole_words_any_case(self):
        said_as = {'PickiPedia': 'picky pedia', 're': 'ray'}
        self.assertEqual(voice_words.said('Ask re on PICKIPEDIA, not here or real-time.', said_as),
                         'Ask ray on Picky pedia, not here or real-time.')

    def test_the_voice_says_it_so(self):
        cache.set('voice-words', voice_words.parse(PAGE), 600)
        fake = FakeEleven()
        message = Message.objects.create(id=uuid.uuid4(), sender=self.magent, mood=self.mood, timestamp=1,
                                         content=[{'type': 'text', 'text': 'Look it up on PickiPedia, re.'}])
        voice.speak(message, 'justin', http=fake)
        self.assertEqual(fake.asked[-1][2]['json']['text'], 'Look it up on Picky pedia, ray.')
        voice.narrate_thinking(self.mood, 're', 'justin', http=fake)
        self.assertEqual(fake.asked[-1][2]['json']['text'], 'Ray is thinking.')

    def test_a_memo_heard_and_expected_by_the_page(self):
        cache.set('voice-words', voice_words.parse(PAGE), 600)
        self.assertEqual(voice.spoken_mentions('Ree, are you there?', {'re', 'justin'}), '@re, are you there?')
        self.assertIn('Old Crow Medicine Show', voice.keyterms())
        self.assertNotIn('mandolin', voice.keyterms())  # the page's list is the list
