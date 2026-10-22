"""Going to a Mood by voice (Justin, 22 Oct): "go to jams", heard and matched, never posted."""

from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase, TestCase, override_settings

from conversations.models import Message, Mood, ThinkingEntity
from conversations.services import mood_auth, mood_switch
from conversations.tests.test_voice import Answer, FakeEleven

MOODS = [SimpleNamespace(slug=s, title=t) for s, t in (
    ('general', 'general'), ('jams', 'jams'), ('magenta-interface', 'magenta-interface'),
    ('magent-self', 'magent-self'), ('the-summer-festival', 'The summer festival setlists, carpools and camping'),
    ('gospels-and-project-30m', 'Gospels and Project 30M'))]


class MatchTest(SimpleTestCase):

    def go(self, heard):
        return mood_switch.match(heard, MOODS)

    def test_said_as_people_say_it(self):
        self.assertEqual(self.go('Go to jams.'), 'jams')
        self.assertEqual(self.go('switch to magenta interface'), 'magenta-interface')
        self.assertEqual(self.go('Okay, take me to pound general, please.'), 'general')
        self.assertEqual(self.go('go to the jams mood'), 'jams')
        self.assertEqual(self.go('Gospels and project thirty M'), 'gospels-and-project-30m')  # numbers said as words
        self.assertEqual(self.go('gospels and project 30m'), 'gospels-and-project-30m')

    def test_a_little_misheard_is_still_found(self):
        self.assertEqual(self.go('go to magenta interfaces'), 'magenta-interface')
        self.assertEqual(self.go('go to genral'), 'general')

    def test_nothing_like_a_mood_or_two_alike_goes_nowhere(self):
        self.assertIsNone(self.go('go to lunch'))
        self.assertIsNone(self.go(''))
        self.assertIsNone(self.go('go to'))
        self.assertIsNone(self.go('magenta'))  # magent-self or magenta-interface: rather ask again


class GoByVoiceTest(TestCase):
    """POST /api/voice/go/: the audio heard, the Mood matched, the narrator saying where -- never a post."""

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.justin = ThinkingEntity.objects.create(name='justin', is_biological_human=True)
        for slug in ('general', 'jams', 'magenta-interface'):
            Mood.objects.create(slug=slug, title=slug)
        _, token = mood_auth.enrol_device(self.justin, 'phone', tier='key')
        self.client.cookies[mood_auth.COOKIE] = token

    def go(self, heard, mood='general'):
        class Heard(FakeEleven):
            def post(self, url, **kw):
                if url.endswith('/speech-to-text'):
                    self.asked.append(('POST', url, kw))
                    return Answer(body={'text': heard, 'audio_duration_secs': 2.0, 'language_code': 'eng'})
                return super().post(url, **kw)
        fake = Heard()
        with override_settings(ELEVENLABS_API_KEY='test-eleven-key'), \
                mock.patch('requests.get', fake.get), mock.patch('requests.post', fake.post):
            answer = self.client.post(f'/api/voice/go/?mood={mood}', b'\x1aE\xdf\xa3 a few seconds',
                                      content_type='audio/webm;codecs=opus')
        said = [kw['json']['text'] for method, url, kw in fake.asked if method == 'POST' and 'json' in kw]
        return answer, said

    def test_heard_matched_and_said_back(self):
        before = Message.objects.filter(sender=self.justin).count()
        answer, said = self.go('Go to jams.')
        self.assertEqual((answer.status_code, answer.json()['slug']), (200, 'jams'))
        self.assertEqual(said, ['Now in pound jams.'])
        self.assertEqual(Message.objects.filter(sender=self.justin).count(), before)  # nothing posted

    def test_not_caught_stays_put_and_says_where(self):
        answer, said = self.go('go to lunch', mood='magenta-interface')
        self.assertIsNone(answer.json()['slug'])
        self.assertEqual(said, ["I didn't catch which Mood. Still in pound magenta interface."])

    def test_signed_in_only(self):
        from django.test import Client
        self.assertEqual(Client().post('/api/voice/go/', b'x', content_type='audio/webm').status_code, 401)
