"""Images in Moods: uploads, serving, rendering, and images lifted out of transcripts."""

import base64
import json
import uuid
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings

from conversations.models import Era, Media, Message, Mood, ThinkingEntity, ToolResult, ToolUse
from conversations.services import media
from conversations.services.mood_view import render_html
from conversations.services.redaction import redact_line
from importers_and_parsers.claude_code_v2 import import_line_from_claude_code_v2

PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 32 + b'pixels'
JPEG = b'\xff\xd8\xff\xe0' + b'jpeg bytes'


class SniffAndStoreTest(TestCase):

    def test_only_raster_images_by_their_bytes(self):
        self.assertEqual(media.sniff(PNG), 'image/png')
        self.assertEqual(media.sniff(JPEG), 'image/jpeg')
        self.assertEqual(media.sniff(b'GIF89a...'), 'image/gif')
        self.assertEqual(media.sniff(b'RIFF\x00\x00\x00\x00WEBPVP8 '), 'image/webp')
        self.assertIsNone(media.sniff(b'<svg onload="alert(1)">'))
        self.assertIsNone(media.sniff(b'hello'))

    def test_the_same_image_is_stored_once(self):
        first, again = media.store(PNG), media.store(PNG)
        self.assertEqual(first.pk, again.pk)
        self.assertEqual(Media.objects.count(), 1)
        self.assertRegex(first.url, r'^/moods/media/[0-9a-f]{64}\.png$')

    def test_too_big_to_take_in_at_all_or_not_an_image_is_refused(self):
        with mock.patch.object(media, 'MAX_SENT_BYTES', 10):
            self.assertIsNone(media.store(PNG))
        with mock.patch.object(media, 'MAX_BYTES', 10):   # over what we keep, and Pillow can't read it
            self.assertIsNone(media.store(PNG))
        self.assertIsNone(media.store(b'not an image at all'))


def drawn(width, height, form='JPEG', alpha=False, frames=1, blocks=False, **how):
    """A picture of that size, as bytes: a gradient (or, with blocks, flat bands, which compress as a
    drawing does rather than as noise)."""
    import io
    from PIL import Image
    made = Image.new('RGBA' if alpha else 'RGB', (width, height))
    if blocks:
        made.putdata([(40 * (x // 160 % 6), 30 * (y // 120 % 8), 90, 128 if alpha else 255)
                      for y in range(height) for x in range(width)])
    else:
        made.putdata([((x * 7) % 256, (y * 5) % 256, (x + y) % 256, 128 if alpha else 255)
                      for y in range(height) for x in range(width)])
    out = io.BytesIO()
    if frames > 1:
        # Each frame different from the last: a GIF whose frames are the same picture is written as one.
        rest = [made.rotate(90 * (i + 1)).convert('P') for i in range(frames - 1)]
        made.convert('P').save(out, form, save_all=True, append_images=rest, **how)
    else:
        made.save(out, form, **how)
    return out.getvalue()


class PicturesInTheThreadTest(TestCase):
    """Justin, 27 Nov 2026: a picture is shown fairly small and opens in the lightbox; several in a row,
    in one message or across messages, are a gallery; and how big one goes up can be chosen first."""

    def test_the_page_carries_the_gallery_and_the_size_choice(self):
        page = self.client.get('/moods/').content.decode()
        self.assertIn('.turn .body img { display: block; max-width: 100%; max-height: 260px;', page)  # small
        self.assertIn('.turn .body .gallery {', page)                      # several in a message: a strip
        self.assertIn('.turn.picsonly + .turn.picsonly.cont', page)        # several messages in a row: a grid
        self.assertIn('function galleryise(el)', page)
        self.assertIn('function picturesAround(a)', page)                  # the lightbox steps through them
        self.assertIn("id=\"picSize\"", page)                             # how big it goes up, chosen first
        self.assertIn('async function atMost(file, edge)', page)
        self.assertIn("localStorage.getItem('pic-max-edge')", page)


class ShrinkToFitTest(TestCase):
    """A picture bigger than we keep is shrunk on the way in, not refused (Justin, 27 Nov 2026:
    "images larger than 8mb not allowed? They oughts be")."""

    def test_a_photo_too_big_to_keep_is_shrunk_and_kept(self):
        big = drawn(2400, 1800, quality=98)
        with mock.patch.object(media, 'MAX_BYTES', len(big) // 3):
            stored = media.store(big)
        self.assertIsNotNone(stored)
        self.assertEqual(stored.mime, 'image/jpeg')
        self.assertLess(stored.size, len(big) // 3)
        from PIL import Image
        import io
        with Image.open(io.BytesIO(stored.data)) as back:
            self.assertLessEqual(max(back.size), 2400)          # smaller, and still the same picture
            self.assertGreater(min(back.size), 200)

    def test_one_that_already_fits_is_kept_byte_for_byte(self):
        small = drawn(60, 40, quality=90)
        self.assertEqual(media.fit_bytes(small, 'image/jpeg'), small)

    def test_a_transparent_png_keeps_its_transparency(self):
        see_through = drawn(2000, 1500, form='PNG', alpha=True, blocks=True)
        fitted = media.fit_bytes(see_through, 'image/png', limit=len(see_through) // 2)
        self.assertIsNotNone(fitted)
        self.assertLessEqual(len(fitted), len(see_through) // 2)
        from PIL import Image
        import io
        with Image.open(io.BytesIO(fitted)) as back:
            self.assertIn(back.format, ('PNG', 'WEBP'))
            self.assertIn('A', back.mode)                        # not flattened onto a background

    def test_a_small_picture_that_is_still_too_heavy_is_shrunk_too(self):
        # The steps are of the picture's own size: one already under any fixed width still comes down.
        heavy = drawn(900, 700, quality=100)
        fitted = media.fit_bytes(heavy, 'image/jpeg', limit=len(heavy) // 4)
        self.assertIsNotNone(fitted)
        self.assertLessEqual(len(fitted), len(heavy) // 4)

    def test_a_moving_picture_is_left_alone(self):
        moving = drawn(400, 300, form='GIF', frames=3)
        self.assertIsNone(media.fit_bytes(moving, 'image/gif', limit=10))
        with mock.patch.object(media, 'MAX_BYTES', 10):
            self.assertIsNone(media.store(moving))


class ShownSizesTest(TestCase):
    """Justin, #interfaces, 27 Nov 2026: "photos in the mood to max out around 100k, and then let the
    user click on them and get a lightbox variant that maxes out at like 800k, and then have a button
    there for full size"."""

    def setUp(self):
        cache.clear()

    def test_a_photo_is_served_under_each_budget(self):
        photo = drawn(3000, 2000, quality=95)
        self.assertGreater(len(photo), 800 * 1024)
        kept = Media.objects.create(sha256='c' * 64, mime='image/jpeg', data=photo, size=len(photo))
        sizes = {}
        for kb in (100, 800):
            answer = self.client.get(f'{kept.url}/fit/{kb}')
            self.assertEqual(answer.status_code, 200)
            self.assertLessEqual(len(answer.content), kb * 1024)
            self.assertEqual(answer['Cache-Control'], 'public, max-age=31536000, immutable')
            sizes[kb] = len(answer.content)
        self.assertLess(sizes[100], sizes[800])         # each budget is spent, not undercut
        self.assertGreater(sizes[800], 100 * 1024)
        self.assertEqual(len(self.client.get(kept.url).content), len(photo))  # the stored bytes untouched

    def test_only_the_sizes_we_ask_for(self):
        kept = Media.objects.create(sha256='d' * 64, mime='image/png', data=PNG, size=len(PNG))
        self.assertEqual(self.client.get(f'{kept.url}/fit/400').status_code, 404)
        self.assertEqual(self.client.get(f'{kept.url}/fit/0').status_code, 404)
        self.assertEqual(self.client.get(f'/moods/media/{"e" * 64}.png/fit/100').status_code, 404)

    def test_a_sound_has_no_size_to_be_shown_at(self):
        sound = Media.objects.create(sha256='f' * 64, mime='audio/webm', data=b'\x1a\x45\xdf\xa3x',
                                     size=5)
        self.assertEqual(self.client.get(f'{sound.url}/fit/100').status_code, 404)

    def test_what_already_fits_and_what_moves_are_served_as_they_are(self):
        small = drawn(120, 90, quality=85)
        self.assertEqual(media.shown_at(small, 'image/jpeg', 100), (small, 'image/jpeg'))
        moving = drawn(500, 400, form='GIF', frames=3)   # re-encoding would still it: left moving
        self.assertEqual(media.shown_at(moving, 'image/gif', 100), (moving, 'image/gif'))
        # Served from its own address, so it isn't kept a second time under another name.
        kept = Media.objects.create(sha256='9' * 64, mime='image/gif', data=moving, size=len(moving))
        answer = self.client.get(f'{kept.url}/fit/100')
        self.assertEqual(answer.status_code, 302)
        self.assertEqual(answer['Location'], kept.url)

    def test_the_lightbox_offers_full_size(self):
        page = self.client.get('/moods/').content.decode()
        self.assertIn("'full size on PickiPedia ↗'", page)   # where it went up
        self.assertIn("'full size ↗'", page)                 # else the file itself
        self.assertIn('original: a.dataset.original', page)


class UploadAndServeTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.sky = ThinkingEntity.objects.create(name='skyler', is_biological_human=True)
        Mood.objects.create(slug='m26')

    def setUp(self):
        cache.clear()

    def post(self, body):
        return self.client.post('/api/moods/m26/media/', body, content_type='application/octet-stream')

    def as_sky(self):
        return mock.patch('conversations.services.mood_auth.device_for',
                          return_value=mock.Mock(entity=self.sky, pk=1, entity_id='skyler'))

    def test_writing_an_image_needs_a_device(self):
        self.assertEqual(self.post(PNG).status_code, 401)

    def test_a_big_photo_goes_in_shrunk_and_says_so(self):
        """Justin, 27 Nov 2026: a picture over the cap is taken and shrunk, and the composer is told."""
        big = drawn(2400, 1800, quality=98)
        with self.as_sky(), mock.patch.object(media, 'MAX_BYTES', len(big) // 3):
            response = self.post(big)
        self.assertEqual(response.status_code, 201)
        said = response.json()
        self.assertEqual((said['shrunk'], said['sent']), (True, len(big)))
        self.assertLess(said['size'], len(big) // 3)
        self.assertEqual(Media.objects.get().size, said['size'])

    def test_one_that_already_fits_is_not_called_shrunk(self):
        with self.as_sky():
            said = self.post(PNG).json()
        self.assertEqual((said['shrunk'], said['sent'], said['size']), (False, len(PNG), len(PNG)))

    def test_bigger_than_we_will_even_take_in(self):
        with self.as_sky(), mock.patch.object(media, 'MAX_SENT_BYTES', 64):
            response = self.post(b'x' * 100)
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()['error'], 'larger than 0 MB')

    def test_a_moving_picture_too_big_to_keep_says_why(self):
        moving = drawn(400, 300, form='GIF', frames=3)
        with self.as_sky(), mock.patch.object(media, 'MAX_BYTES', 10):
            response = self.post(moving)
        self.assertEqual(response.status_code, 413)
        self.assertIn('a moving picture is kept as it is', response.json()['error'])

    def test_an_image_is_stored_and_served_back_safely(self):
        with self.as_sky():
            response = self.post(PNG)
        self.assertEqual(response.status_code, 201)
        url = response.json()['url']
        self.assertEqual(response.json()['markdown'], f'![image]({url})')
        self.assertEqual(Media.objects.get().added_by, self.sky)

        served = self.client.get(url)
        self.assertEqual(served.status_code, 200)
        self.assertEqual(served['Content-Type'], 'image/png')
        self.assertEqual(served['X-Content-Type-Options'], 'nosniff')
        self.assertIn('sandbox', served['Content-Security-Policy'])
        self.assertEqual(served.content, PNG)
        self.assertEqual(self.client.get(url.replace('.png', '.gif')).status_code, 404)
        self.assertEqual(self.client.get('/moods/media/' + '0' * 64 + '.png').status_code, 404)

    def test_what_it_claims_to_be_does_not_matter(self):
        with self.as_sky():
            response = self.client.post('/api/moods/m26/media/', b'<svg onload="x">', content_type='image/png')
        self.assertEqual(response.status_code, 400)


class RenderImagesTest(TestCase):

    def test_stored_media_and_trusted_hosts_embed_others_link(self):
        sha = 'a' * 64
        out = render_html(f'look ![the stage](/moods/media/{sha}.png) here')
        # Shown at SHOWN_KB, opened at LARGE_KB, the file itself behind "full size".
        self.assertIn(f'<img src="/moods/media/{sha}.png/fit/100" alt="the stage" loading="lazy">', out)
        self.assertIn(f'<a class="img" href="/moods/media/{sha}.png" '
                      f'data-full="/moods/media/{sha}.png/fit/800" data-original="/moods/media/{sha}.png">', out)
        with override_settings(MOOD_IMAGE_HOSTS={'pickipedia.xyz'}):
            self.assertIn('<img src="https://pickipedia.xyz/images/a/ab/Banjo.jpg"',
                          render_html('![banjo](https://pickipedia.xyz/images/a/ab/Banjo.jpg)'))
            other = render_html('![pixel](https://tracker.example/p.gif)')
        self.assertNotIn('<img', other)
        self.assertIn('<a href="https://tracker.example/p.gif" target="_blank" rel="noopener">pixel</a>', other)

    def test_an_image_cannot_carry_markup(self):
        from conversations.tests.test_mood_view import RenderHtmlTest
        check = RenderHtmlTest()
        sha = 'b' * 64
        for attack in (f'![x" onerror="alert(1)](/moods/media/{sha}.png)',
                       f'![a](/moods/media/{sha}.png" onerror="alert(1))',
                       '![a](https://pickipedia.xyz/x.png" onerror="alert(1))',
                       '![[[Page]]](https://pickipedia.xyz/a.png)'):
            with self.subTest(attack=attack):
                check.assertSafe(render_html(attack))


def user_line(session, content, role='user', **extra):
    return json.dumps({'type': role, 'userType': 'external', 'uuid': str(uuid.uuid4()), 'parentUuid': None,
                       'sessionId': str(session),
                       'timestamp': '2026-10-01T18:00:00.000Z', 'message': {'role': role, 'content': content},
                       **extra})


class LiftImagesTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.era = Era.objects.create(name='Test Era')
        cls.justin = ThinkingEntity.objects.create(name='justin', is_biological_human=True)
        ThinkingEntity.objects.create(name='magent', is_biological_human=False)
        cls.mood = Mood.objects.create(slug='m26')
        cls.session = uuid.uuid4()
        cls.mood.claim(cls.session)

    def image_block(self, data=PNG):
        return {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png',
                                            'data': base64.b64encode(data).decode()}}

    def test_a_pasted_image_becomes_stored_media_in_the_message(self):
        line = user_line(self.session, [{'type': 'text', 'text': 'look at this'}, self.image_block()])
        msg, _ = import_line_from_claude_code_v2(line, self.era, 'a.jsonl', 'justin')
        stored = Media.objects.get()
        self.assertEqual(stored.added_by, self.justin)
        text = json.dumps(Message.objects.get(id=msg.id).content)
        self.assertIn(stored.url, text)
        self.assertNotIn(base64.b64encode(PNG).decode()[:20], text)

    @override_settings(TOOL_RESULT_CONTENT_CHARS=20000)  # production's; the default keeps no tool output
    def test_a_screenshot_a_tool_returned_shows_on_its_step(self):
        use = json.dumps({'type': 'assistant', 'userType': 'external', 'uuid': str(uuid.uuid4()), 'parentUuid': None,
                          'sessionId': str(self.session), 'timestamp': '2026-10-01T18:00:00.000Z',
                          'message': {'role': 'assistant', 'content': [
                              {'type': 'tool_use', 'id': 'toolu_shot', 'name': 'mcp__playwright__browser_take_screenshot',
                               'input': {'filename': 'x.png'}}]}})
        result = user_line(self.session, [{'type': 'tool_result', 'tool_use_id': 'toolu_shot', 'content': [
            {'type': 'text', 'text': 'Took the screenshot'}, {'type': 'image', 'data': base64.b64encode(PNG).decode(),
                                                               'mimeType': 'image/png'}]}])
        import_line_from_claude_code_v2(use, self.era, 'a.jsonl', 'justin')
        import_line_from_claude_code_v2(result, self.era, 'a.jsonl', 'justin')
        stored = Media.objects.get()
        self.assertIsNone(stored.added_by)
        steps = self.client.get('/api/moods/m26/turns/').json()['steps']
        self.assertEqual(steps[0]['images'], [stored.url])

    def test_redaction_leaves_image_bytes_alone(self):
        data = base64.b64encode(PNG).decode() + 'AKIAABCDEFGHIJKLMNOP'  # a token shape inside the bytes
        line = user_line(self.session, [{'type': 'image', 'source': {'type': 'base64', 'data': data}}])
        self.assertIn('AKIAABCDEFGHIJKLMNOP', redact_line(line))
