"""Audio put into a Mood rather than recorded there, and its words written in only if asked for.

Justin, 28 Nov 2026 (#interfaces): "I'd like to be able to upload audio to moods, and then have an
option to transcribe it, and have it work more-or-less exactly like recorded audio. Also,
drag-and-dropping doesn't always seem to work; sometimes it thuds with no error."

- api_memo?hear=no stores a recording and no more: no Scribe, no ElevenLabs key needed.
- api_hear asks for its words afterwards; from there it goes exactly as a memo goes -- they're written
  in when it's sent (voice.memo_in), and a memo that came back without them can be heard again.
- The page sorts what was dropped, picked, pasted or shared into pictures, videos and recordings, and
  names whatever it couldn't take (sortFiles/leftOutSays, run here in node).
"""

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import mock, skipUnless

from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from conversations.models import Media, Message, Mood, ThinkingEntity
from conversations.services import media, mood_auth, sealing, voice

INLINE = mock.patch('conversations.services.voice._in_background', lambda fn, *a: fn(*a))
HEARD = {'text': 'Reuben, Reuben, I been thinking.', 'seconds': 4.0, 'language': 'eng'}
_, PUBLIC = sealing.new_pair()                          # an edit is sealed (retract.edit): a memo heard again
MP3 = b'ID3\x03\x00\x00\x00' + b'\x00' * 64            # a file someone has on disk
WAV = b'RIFF\x24\x00\x00\x00WAVE' + b'\x00' * 64
PNG = (b'\x89PNG\r\n\x1a\n' + bytes.fromhex('0000000d49484452000000010000000108060000001f15c489')
       + bytes.fromhex('0000000a49444154789c6300010000050001') + bytes.fromhex('0d0a2db40000000049454e44ae426082'))


@override_settings(ELEVENLABS_API_KEY='test-eleven-key', MOOD_RECOVERY_PUBLIC_KEY=PUBLIC)
class AudioUploadTest(TestCase):

    def setUp(self):
        cache.clear()
        self.justin = ThinkingEntity.objects.create(name='justin', is_biological_human=True)
        ThinkingEntity.objects.create(name='magent', is_biological_human=False)
        Mood.objects.create(slug='general', title='general')
        self.client = self.client_for(self.justin)

    def client_for(self, who, tier='key'):
        _, token = mood_auth.enrol_device(who, 'laptop', tier=tier)
        client = Client()
        client.cookies[mood_auth.COOKIE] = token
        return client

    def add(self, data=MP3, hear=False, ctype='audio/mpeg'):
        at = '/api/moods/general/memo/' + ('' if hear else '?hear=no')
        return self.client.post(at, data, content_type=ctype)

    def ask_for_words(self, sha, transcribe=None, client=None):
        with mock.patch('conversations.services.voice.transcribe',
                        transcribe or mock.Mock(return_value=dict(HEARD))), INLINE:
            return (client or self.client).post(f'/api/moods/general/memo/{sha}/hear/')

    def send(self, text, client=None):
        return (client or self.client).post('/api/moods/general/say/', json.dumps({'text': text}),
                                            content_type='application/json')

    # --- stored, and nothing more ------------------------------------------------
    def test_an_uploaded_recording_is_stored_and_nobody_listens_to_it(self):
        heard = mock.Mock(return_value=dict(HEARD))
        with mock.patch('conversations.services.voice.transcribe', heard), INLINE:
            r = self.add()
        self.assertEqual(r.status_code, 201)
        self.assertEqual(Media.objects.get(sha256=r.json()['sha']).mime, 'audio/mpeg')
        self.assertTrue(r.json()['url'].endswith('.mp3'))
        heard.assert_not_called()

    def test_it_takes_what_a_recording_takes_and_nothing_else(self):
        self.assertEqual(self.add(WAV, ctype='audio/wav').status_code, 201)
        refused = self.add(b'%PDF-1.7 not a sound at all', ctype='application/pdf')
        self.assertEqual(refused.status_code, 400)
        self.assertIn('not a recording this understands', refused.json()['error'])

    @override_settings(ELEVENLABS_API_KEY='')
    def test_adding_one_needs_no_elevenlabs_key_but_asking_for_its_words_does(self):
        r = self.add()
        self.assertEqual(r.status_code, 201)
        asked = self.client.post(f"/api/moods/general/memo/{r.json()['sha']}/hear/")
        self.assertEqual(asked.status_code, 503)
        self.assertIn('ElevenLabs', asked.json()['error'])

    def test_adding_one_needs_a_signed_in_device(self):
        self.assertEqual(Client().post('/api/moods/general/memo/?hear=no', MP3,
                                       content_type='audio/mpeg').status_code, 401)
        sha = self.add().json()['sha']
        self.assertEqual(Client().post(f'/api/moods/general/memo/{sha}/hear/').status_code, 401)

    def test_sent_without_asking_it_goes_as_written(self):
        url = self.add().json()['url']
        heard = mock.Mock(return_value=dict(HEARD))
        with mock.patch('conversations.services.voice.transcribe', heard), INLINE:
            r = self.send(f'🎙 [audio · reubens-train.mp3 · 2:13]({url})\n\nthe take from Thursday')
        self.assertEqual(r.status_code, 201)
        said = Message.objects.get(source_file='mood-web').content
        self.assertIn('🎙 [audio · reubens-train.mp3 · 2:13](' + url + ')', said)
        self.assertIn('the take from Thursday', said)
        self.assertNotIn(HEARD['text'], said)
        heard.assert_not_called()

    # --- its words, asked for -----------------------------------------------------
    def test_asked_for_its_words_they_are_written_in_when_it_is_sent(self):
        added = self.add().json()
        self.assertEqual(self.ask_for_words(added['sha']).status_code, 202)
        self.assertEqual(voice.heard(added['sha'])['text'], HEARD['text'])
        r = self.send(f"🎙 [audio · banjo-take-3.mp3]({added['url']})")
        self.assertEqual(r.status_code, 201)
        said = Message.objects.get(source_file='mood-web').content
        self.assertIn(HEARD['text'], said)
        self.assertIn(added['url'], said)

    def test_sent_while_it_is_still_being_heard_it_waits_for_its_words(self):
        added = self.add().json()
        cache.set(f"memo:hearing:{added['sha']}", 1, 300)  # Scribe still on it, as it is for a few seconds
        with mock.patch('conversations.services.voice.post_when_heard') as when_heard:
            r = self.send(f"🎙 [audio · banjo-take-3.mp3]({added['url']})")
        self.assertEqual(r.status_code, 202)
        self.assertEqual(r.json()['pending'], added['sha'])          # the page says it's on its way
        self.assertEqual(when_heard.call_args[0][0], added['sha'])   # and it's posted when its words are in
        self.assertFalse(Message.objects.filter(source_file='mood-web').exists())

    def test_its_words_may_name_an_emergent_as_a_memos_may(self):
        added = self.add().json()
        spoken = {'text': 'Magent, that is the take to keep.', 'seconds': 3.0, 'language': 'eng'}
        self.ask_for_words(added['sha'], transcribe=mock.Mock(return_value=spoken))
        self.assertEqual(voice.heard(added['sha'])['text'], '@magent, that is the take to keep.')

    def test_a_wiki_sign_in_cannot_address_an_emergent_with_one(self):
        wiki = self.client_for(ThinkingEntity.objects.create(name='kieran', is_biological_human=True), tier='wiki')
        added = wiki.post('/api/moods/general/memo/?hear=no', MP3, content_type='audio/mpeg').json()
        spoken = {'text': 'Magent, that is the take to keep.', 'seconds': 3.0, 'language': 'eng'}
        self.ask_for_words(added['sha'], transcribe=mock.Mock(return_value=spoken), client=wiki)
        self.assertEqual(voice.heard(added['sha'])['text'], 'Magent, that is the take to keep.')

    def test_only_a_recording_here_can_be_asked_about(self):
        missing = self.client.post('/api/moods/general/memo/' + '0' * 64 + '/hear/')
        self.assertEqual(missing.status_code, 404)
        picture = media.store(PNG, added_by=self.justin)
        self.assertEqual(self.client.post(f'/api/moods/general/memo/{picture.sha256}/hear/').status_code, 404)

    def test_asked_twice_it_is_heard_once(self):
        added = self.add().json()
        heard = mock.Mock(return_value=dict(HEARD))
        self.assertEqual(self.ask_for_words(added['sha'], transcribe=heard).status_code, 202)
        again = self.ask_for_words(added['sha'], transcribe=heard)
        self.assertEqual(again.status_code, 200)
        self.assertTrue(again.json()['heard'])
        self.assertEqual(heard.call_count, 1)

    def test_one_that_came_back_without_words_can_be_heard_again(self):
        added = self.add().json()
        out_of_credit = mock.Mock(side_effect=voice.VoiceError('ElevenLabs answered 401: quota'))
        self.ask_for_words(added['sha'], transcribe=out_of_credit)
        self.assertEqual(self.send(f"🎙 [audio · banjo.mp3]({added['url']})").status_code, 201)
        message = Message.objects.get(source_file='mood-web')
        self.assertIn('(not transcribed: ElevenLabs answered 401', message.content)
        self.assertEqual(voice.not_heard(message.content), added['sha'])  # the page offers "hear it again"
        with mock.patch('conversations.services.voice.transcribe', mock.Mock(return_value=dict(HEARD))), INLINE:
            r = self.client.post(f'/api/moods/general/messages/{message.id}/hear-again/')
        self.assertEqual(r.status_code, 202)
        message.refresh_from_db()
        self.assertIn(HEARD['text'], message.content)
        self.assertNotIn('(not transcribed', message.content)

    def test_a_recorded_memo_still_has_its_words_heard_without_being_asked(self):
        heard = mock.Mock(return_value=dict(HEARD))
        with mock.patch('conversations.services.voice.transcribe', heard), INLINE:
            added = self.add(hear=True).json()
        self.assertEqual(voice.heard(added['sha'])['text'], HEARD['text'])
        heard.assert_called_once()


HAS_NODE = shutil.which('node') is not None
TEMPLATE = Path(__file__).resolve().parent.parent / 'templates' / 'conversations' / 'moods.html'


def page_js(start, end):
    """The page's own code between two markers, to run here."""
    html = TEMPLATE.read_text(encoding='utf-8')
    return html[html.index(start):html.index(end)]


@skipUnless(HAS_NODE, 'node is needed to run the page\'s own code')
class SortingWhatWasDroppedTest(TestCase):
    """The page's sortFiles/leftOutSays, run in node: every file either goes somewhere or is named."""

    def run_js(self, body, dk='true'):
        code = f'let dkUpload = {dk};\n' + page_js('  // <mood-files>', '  // </mood-files>') + '\n' + body
        with tempfile.TemporaryDirectory() as tmp:
            at = Path(tmp) / 'sorting.mjs'
            at.write_text(code, encoding='utf-8')
            done = subprocess.run(['node', str(at)], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def sorted_files(self, files, dk='true'):
        body = ('const sorted = sortFiles(' + json.dumps(files) + ');\n'
                'console.log(JSON.stringify({pictures: sorted.pictures.map(f => f.name), '
                'videos: sorted.videos.map(f => f.name), recordings: sorted.recordings.map(f => f.name), '
                'left: sorted.left.map(f => f.name), says: sorted.left.length ? leftOutSays(sorted.left) : ""}));')
        return self.run_js(body, dk=dk)

    def test_a_recording_goes_in_by_its_type_or_by_its_name(self):
        sorted_files = self.sorted_files([
            {'name': 'banjo.mp3', 'type': 'audio/mpeg'},
            {'name': 'take-3.m4a', 'type': ''},          # a browser that says nothing about it
            {'name': 'memo.wav', 'type': 'audio/wav'},
            {'name': 'soundcheck.ogg', 'type': 'audio/ogg'},
        ])
        self.assertEqual(sorted_files['recordings'], ['banjo.mp3', 'take-3.m4a', 'memo.wav', 'soundcheck.ogg'])
        self.assertEqual(sorted_files['left'], [])

    def test_pictures_videos_and_recordings_each_go_their_own_way(self):
        sorted_files = self.sorted_files([
            {'name': 'foot.png', 'type': 'image/png'},
            {'name': 'station-inn.mov', 'type': 'video/quicktime'},
            {'name': 'reubens-train.mp3', 'type': 'audio/mpeg'},
        ])
        self.assertEqual((sorted_files['pictures'], sorted_files['videos'], sorted_files['recordings']),
                         (['foot.png'], ['station-inn.mov'], ['reubens-train.mp3']))

    def test_an_mp4_is_read_as_a_video_not_a_recording(self):
        sorted_files = self.sorted_files([{'name': 'set.mp4', 'type': ''}])
        self.assertEqual((sorted_files['videos'], sorted_files['recordings']), (['set.mp4'], []))

    def test_what_cannot_go_is_named_rather_than_dropped_in_silence(self):
        sorted_files = self.sorted_files([{'name': 'chart.pdf', 'type': 'application/pdf'},
                                          {'name': 'foot.png', 'type': 'image/png'}])
        self.assertEqual((sorted_files['pictures'], sorted_files['left']), (['foot.png'], ['chart.pdf']))
        self.assertIn('chart.pdf (application/pdf)', sorted_files['says'])
        self.assertIn('recordings', sorted_files['says'])

    def test_where_videos_do_not_go_a_video_is_named_too(self):
        sorted_files = self.sorted_files([{'name': 'station-inn.mov', 'type': 'video/quicktime'}], dk='false')
        self.assertEqual((sorted_files['videos'], sorted_files['left']), ([], ['station-inn.mov']))
        self.assertNotIn('videos', sorted_files['says'])


class TheDropSaysSomethingTest(TestCase):
    """A drop anywhere on the page, and never a silent one (Justin, 28 Nov: "it thuds with no error")."""

    def setUp(self):
        self.page = TEMPLATE.read_text(encoding='utf-8')

    def test_the_whole_page_takes_a_drop_not_only_the_composer(self):
        self.assertIn("window.addEventListener('drop'", self.page)
        self.assertNotIn("composer.addEventListener('drop'", self.page)

    def test_a_drop_that_carries_no_file_is_said_out_loud(self):
        drop = self.page[self.page.index("window.addEventListener('drop'"):]
        drop = drop[:drop.index('\n  });')]
        self.assertIn('nothing came with that drop', drop)
        self.assertIn('takeFiles(files', drop)

    def test_what_was_left_out_and_a_recording_note_outlive_the_next_word_typed(self):
        # Both are .keep notes: the sweep that clears a hint leaves them, and the send takes them away.
        self.assertIn("'#composer .err, #composer .note:not(.keep), dialog > .err'", self.page)
        self.assertIn("note.className = 'note keep audio-note'", self.page)
        self.assertIn("note.className = 'note keep left-out'", self.page)
        self.assertIn("composer.querySelectorAll('.note.memo-note, .note.keep')", self.page)

    def test_a_recording_shared_in_still_has_its_words_written_in_as_it_goes(self):
        self.assertIn("takeFiles(files, 'share', { hear: true })", self.page)

    def test_dragged_words_are_left_to_the_box(self):
        self.assertIn("const dragFiles = e => [...(e.dataTransfer?.types || [])].includes('Files');", self.page)
