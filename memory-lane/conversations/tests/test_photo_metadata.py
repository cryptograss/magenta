"""A JPEG or PNG sent into a Mood is stored without its metadata.

Moods are public, and a phone's JPEG says where it was taken. HEIC photos
already lose it on conversion (test_heic); these are the formats that were
stored as they came.
"""

import io

from PIL import Image, PngImagePlugin
from django.test import TestCase

from conversations.services import media


def gps_exif(orientation=None):
    exif = Image.Exif()
    exif[0x8825] = {1: 'N', 2: (40.0, 26.0, 46.0), 3: 'W', 4: (79.0, 58.0, 56.0)}  # GPSInfo
    exif[0x010F] = 'Apple'  # Make
    if orientation:
        exif[0x0112] = orientation
    return exif


def jpeg(size=(64, 32), exif=None):
    out = io.BytesIO()
    Image.new('RGB', size, (10, 120, 200)).save(out, 'JPEG', quality=90, **({'exif': exif} if exif else {}))
    return out.getvalue()


class PhotoMetadataTest(TestCase):

    def test_a_jpeg_loses_its_gps_and_keeps_its_picture(self):
        sent = jpeg(exif=gps_exif())
        self.assertIn(b'Exif', sent)
        stored = bytes(media.store(sent).data)
        self.assertNotIn(b'Exif', stored)
        with Image.open(io.BytesIO(stored)) as back:
            self.assertEqual(dict(back.getexif()), {})
            self.assertEqual(back.size, (64, 32))
        # the picture itself is copied, not re-encoded: same scan bytes
        self.assertEqual(sent[sent.index(b'\xff\xda'):], stored[stored.index(b'\xff\xda'):])

    def test_a_jpeg_turned_by_its_exif_is_stored_upright(self):
        stored = bytes(media.store(jpeg(size=(64, 32), exif=gps_exif(orientation=6))).data)
        self.assertNotIn(b'Exif', stored)
        with Image.open(io.BytesIO(stored)) as back:
            self.assertEqual(back.size, (32, 64))

    def test_a_png_loses_its_text_and_exif(self):
        info = PngImagePlugin.PngInfo()
        info.add_text('Location', 'somewhere private')
        info.add_itxt('XML:com.adobe.xmp', '<x:xmpmeta/>')
        out = io.BytesIO()
        Image.new('RGB', (8, 8), 'red').save(out, 'PNG', pnginfo=info, exif=gps_exif())
        stored = bytes(media.store(out.getvalue()).data)
        for chunk in (b'tEXt', b'iTXt', b'eXIf', b'somewhere private'):
            self.assertNotIn(chunk, stored)
        with Image.open(io.BytesIO(stored)) as back:
            back.load()
            self.assertEqual(back.getpixel((0, 0)), (255, 0, 0))

    def test_a_clean_picture_is_stored_unchanged(self):
        sent = jpeg()
        self.assertEqual(bytes(media.store(sent).data), sent)

    def test_a_broken_jpeg_is_still_refused_or_kept_as_it_came(self):
        self.assertEqual(media.without_metadata(b'\xff\xd8\xff\xe1\x00', 'image/jpeg'), b'\xff\xd8\xff\xe1\x00')

    def test_unticked_in_the_composer_it_keeps_its_location(self):
        from django.test import Client
        from conversations.models import Mood, ThinkingEntity
        from conversations.services import mood_auth
        sky = ThinkingEntity.objects.create(name='skyler', is_biological_human=True)
        Mood.objects.create(slug='uploads-and-embeds', title='uploads-and-embeds')
        _, token = mood_auth.enrol_device(sky, 'phone')
        client = Client()
        client.cookies[mood_auth.COOKIE] = token
        sent = jpeg(exif=gps_exif())
        url = '/api/moods/uploads-and-embeds/media/'
        kept = client.post(url + '?keep=location', sent, content_type='application/octet-stream').json()
        cut = client.post(url, sent, content_type='application/octet-stream').json()
        self.assertNotEqual(kept['sha'], cut['sha'])
        from conversations.models import Media
        self.assertEqual(bytes(Media.objects.get(sha256=kept['sha']).data), sent)
        self.assertNotIn(b'Exif', bytes(Media.objects.get(sha256=cut['sha']).data))
