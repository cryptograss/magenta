"""Agents on Tidings (Justin, 23 Oct): asked by name in a comment, an agent is woken in the link's Mood,
and its answer comes back under the comment that asked -- routed by magenta, not left to the agent. Only
when asked; what an agent says there doesn't raise a link."""

import json
import uuid
from datetime import datetime, timezone
from unittest import mock

from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from conversations.models import Message, Mood, ThinkingEntity, TidingComment
from conversations.services import links, mood_auth, tiding_links

KEY = 'k' * 64
AUTH = {'HTTP_AUTHORIZATION': f'Bearer {KEY}'}
CARD = mock.patch('conversations.services.unfurl.card', return_value={'title': 'Barlow lineup'})
PUBLIC = mock.patch('conversations.services.unfurl.public', return_value=True)


@override_settings(MOOD_RUNNER_KEYS={'magent': KEY})
class AgentsOnTidingsTest(TestCase):

    def setUp(self):
        cache.clear()
        links._memo.update(at=0.0, index=None)
        self.justin = ThinkingEntity.objects.create(name='justin', is_biological_human=True)
        self.skyler = ThinkingEntity.objects.create(name='skyler', is_biological_human=True)
        self.magent = ThinkingEntity.objects.create(name='magent', is_biological_human=False)
        self.re, _ = ThinkingEntity.objects.get_or_create(name='re', defaults={'is_biological_human': False})
        self.mood = Mood.objects.create(slug='jams', title='jams')
        self.n = 0
        self.said = self.say(self.skyler, 'https://example.org/lineup')
        self.tiding = tiding_links.promote(self.said, self.justin)

    def say(self, who, text, **fields):
        self.n += 1
        return Message.objects.create(id=uuid.uuid4(), sender=who, mood=self.mood, content=text, timestamp=self.n,
                                      source_file='mood-web', **fields)

    def client_for(self, who, tier='key'):
        _, token = mood_auth.enrol_device(who, 'laptop', tier=tier)
        client = Client()
        client.cookies[mood_auth.COOKIE] = token
        return client

    def comment(self, text, who=None, tier='key', reply_to=None):
        with CARD, PUBLIC:
            r = self.client_for(who or self.justin, tier).post(
                f'/api/tidings/{self.tiding.id}/comments/',
                json.dumps({'text': text, 'reply_to': str(reply_to) if reply_to else None,
                            **({'reply_kind': 'comment'} if reply_to else {})}),
                content_type='application/json')
        self.assertEqual(r.status_code, 201)
        return r.json()['comment']

    def mentions(self, agent='magent'):
        return [m for m in self.client.get(f'/api/mentions/{agent}/').json()['mentions']]

    def turn_ends(self, reply, asked, session=None):
        """A turn of magent's, streamed by its runner, saying what woke it."""
        session = session or str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        said = {'type': 'assistant', 'session_id': session, 'uuid': str(uuid.uuid4()), 'timestamp': now,
                'parent_tool_use_id': None,
                'message': {'role': 'assistant', 'model': 'claude-opus-5-5', 'id': 'msg_x', 'type': 'message',
                            'stop_reason': None, 'usage': {'input_tokens': 3, 'output_tokens': 40},
                            'content': [{'type': 'text', 'text': reply}]}}
        done = {'type': 'result', 'subtype': 'success', 'is_error': False, 'result': reply, 'stop_reason': 'end_turn',
                'session_id': session, 'uuid': str(uuid.uuid4())}
        body = {'harness': 'claude-code', 'session_id': session, 'events': [said, done], 'answers': asked}
        r = self.client.post('/api/moods/jams/stream/', json.dumps(body), content_type='application/json', **AUTH)
        self.assertEqual(r.status_code, 200)
        return session

    # --- asked by name, woken -------------------------------------------------------

    def test_a_comment_asking_an_agent_wakes_it_in_the_links_mood(self):
        above = self.comment('Who is headlining Saturday?', who=self.skyler)
        asked = self.comment('@magent can you check?', reply_to=above['id'])
        [m] = self.mentions()
        turn = m['turn']
        self.assertEqual(m['mood'], 'jams')
        self.assertEqual((turn['sender'], turn['is_human'], turn['via'], turn['tier']), ('justin', True, 'web', None))
        self.assertEqual(turn['tiding'], {'id': str(self.tiding.id), 'comment': asked['id']})
        self.assertIn('“example.org/lineup” (https://example.org/lineup)', turn['text'])  # the link
        self.assertIn('@magent can you check?', turn['text'])               # what was asked
        self.assertIn('Who is headlining Saturday?', turn['text'])          # and what it answers
        self.assertIn(f"/moods/tidings/{self.tiding.id}/#c-{asked['id']}", turn['text'])
        self.assertEqual(self.mentions('re'), [])                            # only who was asked

    def test_a_comment_answering_the_moods_reply_carries_it_without_a_link_to_read_from(self):
        from poller.mood_poller import linked_message
        reply = self.say(self.justin, f'↩ #m-{self.said.id}\nCamping passes sold out last year.')
        links._memo.update(at=0.0, index=None)
        with CARD, PUBLIC:
            r = self.client_for(self.skyler).post(
                f'/api/tidings/{self.tiding.id}/comments/',
                json.dumps({'text': '@magent and this year?', 'reply_to': str(reply.id), 'reply_kind': 'message'}),
                content_type='application/json')
        self.assertEqual(r.status_code, 201)
        turn = self.mentions()[0]['turn']
        self.assertIn('[justin, in #jams] Camping passes sold out last year.', turn['text'])
        self.assertIsNone(linked_message([turn]))  # the wake reads the Mood as usual, not from that reply

    def test_mention_only(self):
        self.comment('Great lineup this year.')
        self.assertEqual(self.mentions(), [])

    def test_a_pickipedia_sign_in_asks_nobody_and_an_agent_asks_nobody(self):
        # As in a Mood, a PickiPedia sign-in's @agent is just text.
        tiding_links.comment(self.tiding, self.skyler, '@magent thoughts?', client_version='magenta-web/wiki')
        self.assertEqual(self.mentions(), [])
        tiding_links.comment(self.tiding, self.re, '@magent over to you')  # an agent's words on Tidings
        self.assertEqual(self.mentions(), [])

    def test_muted_in_its_mood_an_agent_asked_still_hears(self):
        self.tiding.muted_in_mood = True
        self.tiding.save()
        self.comment('Nobody in the Mood needs this one.')
        self.comment('@magent but you do')
        self.assertEqual(len(self.mentions()), 1)
        self.assertEqual(Message.objects.filter(source_file=tiding_links.COMMENT_SOURCE).count(), 1)

    def test_a_deleted_comment_asks_nothing(self):
        asked = self.comment('@magent hello?')
        TidingComment.objects.filter(id=asked['id']).delete()
        self.assertEqual(self.mentions(), [])

    # --- the answer, routed back ------------------------------------------------------

    def test_the_answer_goes_under_the_comment_that_asked(self):
        asked = self.comment('@magent can you check?')
        notice = self.mentions()[0]['turn']['id']
        session = self.turn_ends('Molly Tuttle headlines Saturday.', [notice])
        [answer] = TidingComment.objects.filter(by=self.magent)
        self.assertEqual((str(answer.parent_id), answer.text), (asked['id'], 'Molly Tuttle headlines Saturday.'))
        # In the Mood too, as what magent said there: read aloud there as any of its words are.
        self.assertTrue(Message.objects.filter(session_id=session, sender_id='magent').exists())
        # The stream again, or the transcript after it: once only.
        self.turn_ends('Molly Tuttle headlines Saturday.', [notice], session=session)
        self.assertEqual(TidingComment.objects.filter(by=self.magent).count(), 1)
        page = self.client.get(f'/moods/tidings/{self.tiding.id}/?embed=1').content.decode()
        self.assertIn('Molly Tuttle headlines Saturday.', page)

    def test_a_silence_or_a_wake_about_something_else_goes_nowhere(self):
        self.comment('@magent can you check?')
        notice = self.mentions()[0]['turn']['id']
        self.turn_ends('<silent>nothing to add</silent>', [notice])
        plain = self.say(self.justin, '@magent unrelated question')
        self.turn_ends('An answer about something else.', [str(plain.id)])
        self.turn_ends('Nothing said what woke me.', [])
        self.assertFalse(TidingComment.objects.filter(by=self.magent).exists())

    def test_asked_by_a_reply_in_the_mood_in_a_links_discussion(self):
        reply = self.say(self.justin, f'↩ #m-{self.said.id}\n@magent is camping sold out?')
        links._memo.update(at=0.0, index=None)
        self.turn_ends('Not yet: about half left.', [str(reply.id)])
        [answer] = TidingComment.objects.filter(by=self.magent)
        self.assertEqual((str(answer.parent_message_id), answer.tiding_id), (str(reply.id), self.tiding.id))

    # --- what an agent says doesn't raise a link ----------------------------------------

    def test_an_agents_comments_are_shown_but_dont_count_toward_points(self):
        asked = self.comment('@magent can you check?')
        before = next(l for l in tiding_links.listed() if l['id'] == str(self.tiding.id))
        self.turn_ends('Checked: Saturday.', [self.mentions()[0]['turn']['id']])
        after = next(l for l in tiding_links.listed() if l['id'] == str(self.tiding.id))
        self.assertEqual((before['comments'], after['comments']), (1, 2))  # shown
        self.assertEqual((before['talk'], after['talk']), (1, 1))          # not counted
        self.assertAlmostEqual(before['rank'], after['rank'], places=3)
        self.assertTrue(asked)
