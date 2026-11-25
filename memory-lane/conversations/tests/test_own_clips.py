"""Clips of our own, cut from a local file: `magenta.sh clips save <file> <start> <end> "<subtitle>"`
(tools/mood_clip.py; views_auth.api_clip_make; Justin, 24 Oct)."""

import base64
import importlib.util
import json
from pathlib import Path
from unittest import mock

from django.core.cache import cache
from django.test import Client, TestCase

from conversations.models import Message, Mood, ThinkingEntity, YarnClip
from conversations.services import clips, mood_auth
from conversations.services.mood_view import render_html

MP4 = b'\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2' + b'\x00' * 64
JPEG = b'\xff\xd8\xff\xe0' + b'\x00' * 64 + b'\xff\xd9'


class OwnClipsTest(TestCase):

    def setUp(self):
        cache.delete(clips.CACHE_KEY)
        self.addCleanup(cache.delete, clips.CACHE_KEY)
        self.justin = ThinkingEntity.objects.create(name='justin', is_biological_human=True)
        self.skyler = ThinkingEntity.objects.create(name='skyler', is_biological_human=True)
        ThinkingEntity.objects.create(name='magent', is_biological_human=False)
        Mood.objects.create(slug='general')
        Mood.objects.create(slug='jams')

    def make(self, signer='justin', name='whole-point', caption='That is the whole point.', video=MP4, **more):
        challenge = self.client.get('/api/auth/challenge/').json()['challenge']
        body = {'challenge': challenge, 'signature': 'sig', 'name': name, 'caption': caption,
                'video': base64.b64encode(video).decode(), 'still': base64.b64encode(JPEG).decode(), **more}
        with mock.patch.object(mood_auth, 'signer_of', return_value=signer) as signed:
            response = self.client.post('/api/clips/make/', json.dumps(body), content_type='application/json')
        self.signed = signed.call_args
        return response

    def test_made_kept_saved_and_played(self):
        made = self.make(mood='jams')
        self.assertEqual(made.status_code, 201, made.content)
        link = made.json()['link']
        clip = YarnClip.objects.get(made_by=self.justin)
        self.assertEqual((clip.caption, clip.media.mime), ('That is the whole point.', 'video/mp4'))
        self.assertTrue(link.endswith(f'/clips/{clip.clip}'))
        # What was signed: the challenge and server, the name, the video's hash and the caption.
        message = self.signed[0][0]
        self.assertIn('magenta-moods clip whole-point\n', message)
        self.assertTrue(message.endswith('\nThat is the whole point.'))
        # Saved by a post of Justin's in the Mood asked for, shown as the one line a save is.
        saved = Message.objects.get(mood__slug='jams')
        self.assertEqual((saved.sender_id, saved.content), ('justin', f'/clips save whole-point {link}'))
        self.assertEqual(render_html(saved.content), '<p>🎬 saved <strong>whole-point</strong></p>')
        # /clips whole-point plays it, for a person and for an agent's reply alike, as a card played from here.
        self.assertEqual(clips.command('/clips whole-point', 'skyler')[0], f'/clips whole-point {link}')
        self.assertEqual(clips.played_by('/clip whole-point'), f'/clips whole-point {link}')
        card = render_html(f'/clips whole-point {link}')
        self.assertIn(f'data-kept="{clip.media.url}"', card)
        self.assertIn('data-own="1"', card)
        self.assertIn('title="That is the whole point."', card)
        # Its link, opened: the video.
        self.assertRedirects(self.client.get(f'/clips/{clip.clip}/'), clip.media.url, fetch_redirect_response=False)

    def test_with_no_mood_asked_it_is_saved_and_said_nowhere(self):
        """Justin, 7 Nov: "No need to announce in #general when someone saves a clip"."""
        made = self.make(name='quiet-one')
        self.assertEqual(made.status_code, 201, made.content)
        self.assertEqual((made.json()['mood'], made.json()['url']), (None, None))
        saved = Message.objects.get(client_version='magenta-cli/clips')
        self.assertIsNone(saved.mood)  # said in no Mood: #general hears nothing
        self.assertEqual(saved.content, f"/clips save quiet-one {made.json()['link']}")
        self.assertEqual(clips.command('/clips quiet-one', 'skyler')[0], f"/clips quiet-one {made.json()['link']}")
        self.assertEqual(self.make(name='nowhere-one', mood='nowhere').status_code, 404)

    def test_words_under_a_clip_come_with_it(self):
        # Justin, 24 Oct: "if you just add more text in the same message as a clip, on the next line(s)?"
        link = self.make().json()['link']
        posted, _ = clips.command('/clips whole-point\nSaid it in 2019, still true.', 'skyler')
        self.assertEqual(posted, f'/clips whole-point {link}\nSaid it in 2019, still true.')
        html = render_html(posted)
        self.assertIn('data-own="1"', html)                              # the clip,
        self.assertIn('<p>Said it in 2019, still true.</p>', html)      # and the words under it
        self.assertEqual(clips.played_by('/clip whole-point\nand that\'s that'),
                         f'/clips whole-point {link}\nand that\'s that')  # an agent's reply too
        with self.assertRaises(clips.Refused):
            clips.command('/clips nothing-saved\nwords', 'skyler')       # still a name it knows
        # Its last line, after its words (blu, 28 Oct): where it stands, the words above it.
        self.assertEqual(clips.played_by("That's two of us.\n\n/clip whole-point"),
                         f"That's two of us.\n\n/clips whole-point {link}")
        self.assertIsNone(clips.played_by('Use /clip whole-point like that.'))  # mid-sentence: words

    def test_a_clip_in_the_middle_plays_where_it_sits(self):
        # Justin, #connectome, 21 Nov 2026: "maybe we need to enable /clips to work in the middle of a
        # message". blu had ended a write-up with a clip and then a merge ask, and nothing played.
        link = self.make().json()['link']
        said = 'Here it is.\n\n/clip whole-point\n\n<merge to="magenta:blu/thing"/>'
        played = clips.played_by(said)
        self.assertEqual(played, f'Here it is.\n\n/clips whole-point {link}\n\n<merge to="magenta:blu/thing"/>')
        self.assertEqual(clips.command(said, 'skyler')[0], played)  # a person typing it, the same
        html = render_html(played)
        self.assertIn('data-own="1"', html)                                    # the clip, in its place
        self.assertIn('<p>Here it is.</p>', html)                              # the words above it
        self.assertLess(html.index('Here it is.'), html.index('data-own="1"'))
        self.assertIn('magenta:blu/thing', html)                               # and the ask below it

    def test_several_clips_each_play_and_a_fenced_one_does_not(self):
        link = self.make().json()['link']
        said = 'One:\n/clip whole-point\nand two:\n/clip whole-point\n\n```\n/clip whole-point\n```'
        played = clips.played_by(said)
        self.assertEqual(played.count(f'/clips whole-point {link}'), 2)
        self.assertIn('```\n/clip whole-point\n```', played)                   # inside a fence: words
        self.assertEqual(render_html(played).count('data-own="1"'), 2)

    def test_refused(self):
        self.make()
        self.assertEqual(self.make(signer='skyler').status_code, 409)          # Justin's name: first come
        self.assertEqual(self.make(signer=None).status_code, 403)              # no key we know
        self.assertEqual(self.make(name='save').status_code, 400)
        self.assertEqual(self.make(name='other', video=b'GIF89a' + b'\x00' * 40).status_code, 400)  # not an MP4
        self.assertEqual(self.make(name='other', mood='nowhere').status_code, 404)
        self.assertEqual(YarnClip.objects.filter(made_by__isnull=False).count(), 1)

    def test_a_link_to_no_clip_of_ours_is_just_a_link(self):
        html = render_html('https://magenta.cryptograss.live/clips/1ab70c93-fce1-460d-8575-3bac5a666e96')
        self.assertNotIn('data-own', html)
        self.assertEqual(self.client.get('/clips/1ab70c93-fce1-460d-8575-3bac5a666e96/').status_code, 404)


class MoodClipToolTest(TestCase):
    """tools/mood_clip.py's own reckoning: times, and a name from the subtitle."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        path = Path(__file__).resolve().parents[2] / 'tools' / 'mood_clip.py'
        spec = importlib.util.spec_from_file_location('mood_clip', path)
        cls.tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.tool)

    def test_times(self):
        self.assertEqual([self.tool.seconds(t) for t in ('62.5', '1:02.5', '0:01:02.5')], [62.5, 62.5, 62.5])
        with self.assertRaises(ValueError):
            self.tool.seconds('1:2:3:4')

    def test_a_name_from_the_subtitle(self):
        self.assertEqual(self.tool.name_from("That's the whole point, man. Really.", 'x.mp4'), 'thats-the-whole-point-man-really')
        # Whole words, up to the server's 80 (Justin, 7 Nov: it was cut at 35).
        long = self.tool.name_from('the dude abides and i for one take comfort in that knowing hes out there taking her easy for all us sinners', 'x.mp4')
        self.assertEqual(long, 'the-dude-abides-and-i-for-one-take-comfort-in-that-knowing-hes-out-there-taking')
        self.assertLessEqual(len(long), 80)
        self.assertEqual(self.tool.name_from('…', 'Old Crow 2019.mov'), 'old-crow-2019')


class ClipPicturesTest(TestCase):
    """/api/clips/ says each clip's picture, 'still', for the composer's list (Justin, 25 Nov)."""

    def setUp(self):
        cache.delete(clips.CACHE_KEY)
        self.addCleanup(cache.delete, clips.CACHE_KEY)
        ThinkingEntity.objects.create(name='justin', is_biological_human=True)
        Mood.objects.create(slug='general')

    def say(self, text):
        _, token = mood_auth.enrol_device(ThinkingEntity.objects.get(name='justin'), 'test', tier='key')
        client = Client(enforce_csrf_checks=False)
        client.cookies[mood_auth.COOKIE] = token
        return client.post('/api/moods/general/say/', json.dumps({'text': text}), content_type='application/json')

    def clips_api(self):
        return {c['name']: c for c in self.client.get('/api/clips/').json()['clips']}

    def test_our_own_clips_picture_is_the_one_kept_here(self):
        challenge = self.client.get('/api/auth/challenge/').json()['challenge']
        body = {'challenge': challenge, 'signature': 'sig', 'name': 'whole-point', 'caption': 'The whole point.',
                'video': base64.b64encode(MP4).decode(), 'still': base64.b64encode(JPEG).decode()}
        with mock.patch.object(mood_auth, 'signer_of', return_value='justin'):
            self.client.post('/api/clips/make/', json.dumps(body), content_type='application/json')
        kept = YarnClip.objects.get(caption='The whole point.')
        self.assertEqual(self.clips_api()['whole-point']['still'], kept.still.url)

    def test_a_yarn_clips_picture_is_yarns_until_it_is_kept(self):
        clip = 'ffb40a1a-a936-49ee-962a-ef53e0cb7237'
        self.say(f'/clips save tony https://www.yarn.co/yarn-clip/{clip}')
        self.assertEqual(self.clips_api()['tony']['still'], f'https://y.yarn.co/{clip}_text.gif')
