"""A memo posted without its words, heard again by whoever sent it (voice.hear_again).

Justin, 20 Oct: ElevenLabs ran out of credit mid-memo; once topped up there was
no way to ask again. Its words now take the place of "(not transcribed: ...)",
as an edit by its sender.
"""

import json
from unittest import mock

from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from conversations.models import Message, MessageChange, Mood, ThinkingEntity
from conversations.services import mood_auth, sealing, voice
from conversations.tests.test_voice import WEBM

INLINE = mock.patch('conversations.services.voice._in_background', lambda fn, *a: fn(*a))
HEARD = {'text': 'Magent, bring the capo to soundcheck.', 'seconds': 4.0, 'language': 'eng'}
_, PUBLIC = sealing.new_pair()

OUT_OF_CREDIT = voice.VoiceError('ElevenLabs answered 401: This request exceeds your quota of 30000.')


@override_settings(ELEVENLABS_API_KEY='test-eleven-key', MOOD_RECOVERY_PUBLIC_KEY=PUBLIC)
class HearAgainTest(TestCase):

    def setUp(self):
        cache.clear()
        self.justin = ThinkingEntity.objects.create(name='justin', is_biological_human=True)
        self.skyler = ThinkingEntity.objects.create(name='skyler', is_biological_human=True)
        ThinkingEntity.objects.create(name='magent', is_biological_human=False)
        Mood.objects.create(slug='general', title='general')

    def client_for(self, who, tier='key'):
        _, token = mood_auth.enrol_device(who, 'phone', tier=tier)
        client = Client()
        client.cookies[mood_auth.COOKIE] = token
        return client

    def record(self, client, transcribe=None):
        with mock.patch('conversations.services.voice.transcribe', transcribe or mock.Mock(return_value=dict(HEARD))), INLINE:
            return client.post('/api/moods/general/memo/', WEBM, content_type='audio/webm').json()['url']

    def send(self, client, text):
        return client.post('/api/moods/general/say/', json.dumps({'text': text}), content_type='application/json')

    def unheard(self, client):
        url = self.record(client, transcribe=mock.Mock(side_effect=OUT_OF_CREDIT))
        self.assertEqual(self.send(client, f'🎙 [voice memo · 6:27]({url})\n\nfrom the bike').status_code, 201)
        return url, Message.objects.get(source_file='mood-web')

    def again(self, client, message, transcribe=None):
        with mock.patch('conversations.services.voice.transcribe', transcribe or mock.Mock(return_value=dict(HEARD))), INLINE:
            return client.post(f'/api/moods/general/messages/{message.id}/hear-again/')

    def test_its_words_take_the_place_of_the_note_as_an_edit_by_its_sender(self):
        client = self.client_for(self.justin)
        url, message = self.unheard(client)
        self.assertIn('(not transcribed: ElevenLabs answered 401', message.content)
        self.assertEqual(self.again(client, message).status_code, 202)
        message.refresh_from_db()
        self.assertEqual(message.content, f'🎙 [voice memo · 6:27]({url})\n\n@magent, bring the capo to soundcheck.\n\nfrom the bike')
        self.assertEqual(MessageChange.objects.get(message=message).kind, 'edited')
        self.assertEqual(client.get(f'/api/moods/general/messages/{message.id}/hear-again/').json(), {'done': True})

    def test_still_out_of_credit_it_says_so_and_the_note_stays(self):
        client = self.client_for(self.justin)
        _, message = self.unheard(client)
        self.assertEqual(self.again(client, message, transcribe=mock.Mock(side_effect=OUT_OF_CREDIT)).status_code, 202)
        message.refresh_from_db()
        self.assertIn('(not transcribed:', message.content)
        said = client.get(f'/api/moods/general/messages/{message.id}/hear-again/').json()
        self.assertIn('exceeds your quota', said['error'])
        self.assertEqual(self.again(client, message).status_code, 202)  # and may be asked again

    def test_only_its_sender_and_only_a_memo_without_words(self):
        justin = self.client_for(self.justin)
        _, message = self.unheard(justin)
        self.assertEqual(self.again(self.client_for(self.skyler), message).status_code, 403)
        self.assertEqual(self.again(Client(), message).status_code, 401)
        self.again(justin, message)
        self.assertEqual(self.again(justin, message).status_code, 400)  # its words are in now
        self.send(justin, 'just words')
        plain = Message.objects.filter(source_file='mood-web').order_by('-created_at').first()
        self.assertEqual(self.again(justin, plain).status_code, 400)

    def posted_as_it_was(self, client, words=''):
        """A file uploaded rather than recorded (api_memo?hear=no): stored, and sent as it is."""
        url = client.post('/api/moods/general/memo/?hear=no', WEBM, content_type='audio/webm').json()['url']
        said = f'🎙 [audio · Soulbinding inflation.m4a · 16:24]({url})' + (f'\n\n{words}' if words else '')
        self.assertEqual(self.send(client, said).status_code, 201)
        return url, Message.objects.filter(source_file='mood-web').order_by('-created_at').first()

    def test_a_file_posted_as_it_was_is_transcribed_after_the_fact(self):
        """Justin, 28 Nov 2026: he uploaded 16 minutes and sent it, and the composer's button went
        with the send. The words go under the link, where there is no note to take the place of."""
        client = self.client_for(self.justin)
        url, message = self.posted_as_it_was(client)
        self.assertIsNotNone(voice.untranscribed(message.content))
        self.assertEqual(self.again(client, message).status_code, 202)
        message.refresh_from_db()
        self.assertEqual(message.content,
                         f'🎙 [audio · Soulbinding inflation.m4a · 16:24]({url})\n\n@magent, bring the capo to soundcheck.')
        self.assertEqual(MessageChange.objects.get(message=message).kind, 'edited')
        self.assertIsNone(voice.untranscribed(message.content))   # and not offered again
        self.assertEqual(self.again(client, message).status_code, 400)

    def test_audio_with_words_beside_it_is_left_alone(self):
        """A transcript is prose under the link, and so is anything its sender typed: nothing in the
        text tells them apart, so audio sent with words keeps them."""
        client = self.client_for(self.justin)
        _, message = self.posted_as_it_was(client, words='from the barn, the bit I meant')
        self.assertIsNone(voice.untranscribed(message.content))
        self.assertEqual(self.again(client, message).status_code, 400)

    def test_only_whoever_posted_it_may_ask(self):
        client = self.client_for(self.justin)
        _, message = self.posted_as_it_was(client)
        self.assertEqual(self.again(self.client_for(self.skyler), message).status_code, 403)
        self.assertEqual(self.again(Client(), message).status_code, 401)

    def test_the_page_offers_it_to_its_sender_only(self):
        justin = self.client_for(self.justin)
        _, message = self.unheard(justin)
        mine = next(t for t in justin.get('/api/moods/general/turns/').json()['turns'] if t['id'] == str(message.id))
        self.assertIsNotNone(voice.not_heard(mine['text']))
        page = justin.get('/moods/general/').content.decode()
        self.assertIn('class="hearagain"', page)  # the button's markup, drawn by the page's script
        self.assertIn('>transcribe</button>', page)    # Justin's word for it, 28 Nov 2026
