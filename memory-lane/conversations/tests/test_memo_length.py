"""A browser's recording kept with its length written in (Justin, 21 Oct: a phone's player showed what it
had loaded so far as the whole, and its slider couldn't seek)."""

import struct
from unittest import mock

from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from conversations.models import Media, Mood, ThinkingEntity
from conversations.services import media, mood_auth
from conversations.services.media import _ebml_id, _ebml_size, webm_with_duration


def element(eid, body, size_len=None):
    eid_bytes = eid.to_bytes((eid.bit_length() + 7) // 8, 'big')
    size_len = size_len or 8
    return eid_bytes + ((1 << (7 * size_len)) | len(body)).to_bytes(size_len, 'big') + body


def recording(info_extra=b'', before_info=b'', segment_unknown=True):
    """A WebM as MediaRecorder writes it: no Duration, a Segment of unknown size, clusters after."""
    header = element(0x1A45DFA3, element(0x4282, b'webm', 1), 1)
    info = element(0x1549A966, element(0x2AD7B1, (1_000_000).to_bytes(3, 'big'), 1) + element(0x4D80, b'Chrome', 1) + info_extra, 1)
    rest = element(0x1654AE6B, b'\x00' * 20, 1) + element(0x1F43B675, element(0xE7, b'\x00', 1) + b'\xa3\x85\x81\x00\x00\x80\x00', 1)
    body = before_info + info + rest
    segment = b'\x18\x53\x80\x67' + (b'\x01\xff\xff\xff\xff\xff\xff\xff' if segment_unknown else
                                     ((1 << 56) | len(body)).to_bytes(8, 'big')) + body
    return header + segment


def duration_of(data):
    """The Duration in a WebM's Info, in seconds, or None."""
    n = _ebml_id(data, 0)[1]
    size, m, _ = _ebml_size(data, n)
    i = n + m + size + 4
    _, seg_len, _ = _ebml_size(data, i)
    j = i + seg_len
    while j < len(data):
        eid, n = _ebml_id(data, j)
        size, m, _ = _ebml_size(data, j + n)
        if eid == 0x1549A966:
            body, k = data[j + n + m:j + n + m + size], 0
            while k < len(body):
                cid, cn = _ebml_id(body, k)
                csize, cm, _ = _ebml_size(body, k + cn)
                if cid == 0x4489:
                    return struct.unpack('>d', body[k + cn + cm:k + cn + cm + csize])[0] / 1000
                k += cn + cm + csize
            return None
        j += n + m + size
    return None


class DurationTest(TestCase):

    def test_its_length_written_into_its_info_and_nothing_else_moved(self):
        before = recording()
        after = webm_with_duration(before, 144.06)
        self.assertAlmostEqual(duration_of(after), 144.06)
        self.assertEqual(len(after), len(before) + 11)
        self.assertTrue(after.endswith(before[-40:]))  # the clusters as they were
        self.assertEqual(webm_with_duration(after, 1.0), after)  # says its length already: left be

    def test_a_segment_of_known_size_grows_with_it(self):
        after = webm_with_duration(recording(segment_unknown=False), 3.5)
        self.assertAlmostEqual(duration_of(after), 3.5)
        n = _ebml_id(after, 0)[1]
        size, m, _ = _ebml_size(after, n)
        seg_size, seg_len, _ = _ebml_size(after, n + m + size + 4)
        self.assertEqual(n + m + size + 4 + seg_len + seg_size, len(after))

    def test_what_it_cant_safely_change_is_left_as_it_was(self):
        seekhead = element(0x114D9B74, b'\x00' * 6, 1)
        for data in (recording(before_info=seekhead), b'\x1a\x45\xdf\xa3' + b'\x00' * 64, b'ID3\x04 an mp3', b''):
            self.assertEqual(webm_with_duration(data, 10.0), data)


@override_settings(ELEVENLABS_API_KEY='test-eleven-key')
class MemoUploadTest(TestCase):

    def setUp(self):
        cache.clear()
        self.justin = ThinkingEntity.objects.create(name='justin', is_biological_human=True)
        Mood.objects.create(slug='general', title='general')
        _, token = mood_auth.enrol_device(self.justin, 'phone')
        self.client = Client()
        self.client.cookies[mood_auth.COOKIE] = token

    def test_a_memo_is_kept_with_the_length_the_page_says_it_has(self):
        with mock.patch('conversations.services.voice.hear_later'):
            url = self.client.post('/api/moods/general/memo/?seconds=144.06', recording(),
                                   content_type='audio/webm').json()['url']
        kept = Media.objects.get(sha256=url.rsplit('/', 1)[1].split('.')[0])
        self.assertAlmostEqual(duration_of(bytes(kept.data)), 144.06)

    def test_without_one_it_is_kept_as_sent(self):
        with mock.patch('conversations.services.voice.hear_later'):
            url = self.client.post('/api/moods/general/memo/?seconds=nonsense', recording(),
                                   content_type='audio/webm').json()['url']
        self.assertIsNone(duration_of(bytes(Media.objects.get(sha256=url.rsplit('/', 1)[1].split('.')[0]).data)))
