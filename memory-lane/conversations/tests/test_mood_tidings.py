"""Tests for /moods/tidings/ and /api/tidings/: the last 20 pictures released
into the Moods, each with its tiny preview and its wiki and faces marks.
The prompt half of the old page now lives at /moods/prompts/ (test_mood_prompts)."""

import uuid
from datetime import timedelta

from django.utils import timezone

from conversations.models import FaceLook, FaceName, Media, Message
from conversations.services import tidings
from conversations.tests.tidings_helpers import TidingsTests, fake_sha


class TidingsPageTests(TidingsTests):

    # --- the page -----------------------------------------------------------

    def test_tidings_page_renders_and_is_linked_from_moods(self):
        response = self.client.get('/moods/tidings/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('tidings · magenta', response.content.decode())
        self.assertIn('Pictures', response.content.decode())
        self.assertNotIn('Prompts', response.content.decode())  # its own tab since Justin's 20 Oct memo

        moods_html = self.client.get('/moods/').content.decode()
        self.assertIn('href="/moods/tidings/"', moods_html)
        self.assertIn('href="/moods/prompts/"', moods_html)

    def test_api_tidings_lists_the_pictures_and_naught_else(self):
        self.image(3, added_by=self.justin)
        body = self.client.get('/api/tidings/').json()
        self.assertEqual(set(body), {'pictures'})
        self.assertEqual(len(body['pictures']), 1)

    # --- pictures ---------------------------------------------------------------

    def test_a_picture_shows_who_pushed_faces_and_wiki(self):
        picture = self.image(1, added_by=self.justin)
        FaceLook.objects.create(media=picture, model='faces 1', width=10, height=10,
                                faces=[{}, {}, {}])
        FaceName.objects.create(media=picture, box=[0, 0, 1, 1], person='Justin Holmes',
                                by=self.justin, at=timezone.now())
        Message.objects.create(
            id=uuid.uuid4(), sender=self.re, mood=self.mood_b, source_file='wiki-upload',
            timestamp=1_000,
            content={'type': 'wiki-upload', 'by': 'skyler', 'file': 'Test.jpg',
                     'page': 'File:Test.jpg', 'sha': picture.sha256},
        )
        [entry] = tidings.picture_tidings()
        self.assertEqual(entry['sha256'], picture.sha256)
        self.assertEqual(entry['added_by'], 'justin')
        self.assertTrue(entry['faces']['looked'])
        self.assertEqual(entry['faces']['count'], 3)
        self.assertEqual(entry['faces']['named'], 1)
        self.assertEqual(entry['wiki']['page'], 'File:Test.jpg')
        self.assertEqual(entry['wiki']['mood'], 'docs-tests-qa')

    def test_an_untouched_picture_says_so(self):
        self.image(2, added_by=self.skyler)
        [entry] = tidings.picture_tidings()
        self.assertEqual(entry['faces'], {'looked': False})
        self.assertIsNone(entry['wiki'])

    def test_only_images_count_and_the_newest_twenty_are_shown(self):
        clock = timezone.now()
        for index in range(25):
            clock += timedelta(seconds=1)
            self.image(index, at=clock)
        # A voice memo and a HEIC original are newer, but they are not pictures.
        clock += timedelta(seconds=1)
        self.image(at=clock, mime='audio/webm')
        clock += timedelta(seconds=1)
        self.image(at=clock, mime='image/heic')
        pictures = tidings.picture_tidings()
        self.assertEqual(len(pictures), 20)
        self.assertEqual(pictures[0]['sha256'], fake_sha(24))
        self.assertEqual(pictures[-1]['sha256'], fake_sha(5))

    def test_only_pictures_a_mood_shows(self):
        self.image(4, added_by=self.justin, posted=False)  # uploaded, never posted
        pasted = self.image(5, added_by=self.justin, posted=False)
        self.clock += timedelta(seconds=1)
        Message.objects.create(id=uuid.uuid4(), sender=self.justin, content=f'see ![image]({pasted.url})',
                               source_file='session.jsonl', timestamp=1_000)  # a terminal session's
        in_mood = self.image(6, posted=False)
        self.post(self.skyler, self.mood_b, f'![image]({in_mood.url}) from me')
        [entry] = tidings.picture_tidings()
        self.assertEqual((entry['sha256'], entry['added_by'], entry['mood']), (in_mood.sha256, 'skyler', 'docs-tests-qa'))

    def test_pictures_appear_on_the_page(self):
        self.image(3, added_by=self.justin)
        html = self.client.get('/moods/tidings/').content.decode()
        self.assertIn('not on wiki', html)
        self.assertIn('no faces', html)
        self.assertIn('justin', html)

    # --- Justin's 20 Oct round: no intro, thumbnails --------------------------

    def test_no_intro_description_and_no_lede(self):
        html = self.client.get('/moods/tidings/').content.decode()
        self.assertNotIn("record's newest", html)
        self.assertNotIn('class="lede"', html)

    def test_pictures_carry_a_preview_url_and_the_page_uses_it(self):
        picture = self.image(7, added_by=self.justin)
        [entry] = tidings.picture_tidings()
        self.assertEqual(entry['preview'], f'{entry["url"]}/preview')
        html = self.client.get('/moods/tidings/').content.decode()
        self.assertIn(f'src="/moods/media/{picture.sha256}.png/preview"', html)

    # --- the preview endpoint ----------------------------------------------------

    def test_preview_serves_a_smaller_picture(self):
        picture = self.small_png(60)
        response = self.client.get(f'/moods/media/{picture.sha256}.png/preview')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'image/jpeg')  # opaque picture: a JPEG
        self.assertTrue(response.content.startswith(b'\xff\xd8'))
        self.assertLess(len(response.content), picture.size)

    def test_preview_keeps_transparency_when_there_is_any(self):
        import io

        from PIL import Image
        out = io.BytesIO()
        Image.new('RGBA', (300, 300), (0, 0, 0, 0)).save(out, format='PNG')
        picture = Media.objects.create(sha256=fake_sha(61), mime='image/png', data=out.getvalue(),
                                       size=len(out.getvalue()), added_by=self.justin)
        response = self.client.get(f'/moods/media/{picture.sha256}.png/preview')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'image/png')
        self.assertTrue(response.content.startswith(b'\x89PNG'))

    def test_preview_serves_only_pictures_that_exist(self):
        sound = self.image(mime='audio/webm', posted=False)
        self.assertEqual(self.client.get(f'/moods/media/{sound.sha256}.webm/preview').status_code, 404)
        self.assertEqual(self.client.get(f'/moods/media/{fake_sha(99)}.png/preview').status_code, 404)
        self.assertEqual(self.client.get('/moods/media/not-a-sha.png/preview').status_code, 404)