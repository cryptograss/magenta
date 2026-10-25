"""The record side of a woken turn: where it finds its session, who said what, what the view shows."""

import json
import uuid

from django.test import TestCase

from conversations.models import Era, Message, Mood, ThinkingEntity
from importers_and_parsers.claude_code_v2 import import_line_from_claude_code_v2

WAKE = '<mood-wake mood="m26">\n[skyler, 2026-09-29T18:00Z] @magent are you there?\n</mood-wake>'


def line(session, role, content, **overrides):
    record = {'type': role, 'uuid': str(uuid.uuid4()), 'parentUuid': None, 'sessionId': str(session),
              'timestamp': '2026-09-29T18:11:00.000Z', 'message': {'role': role, 'content': content}}
    record.update(overrides)
    return json.dumps(record)


class MoodWakeTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.era = Era.objects.create(name='Test Era')
        cls.justin = ThinkingEntity.objects.create(name='justin', is_biological_human=True)
        cls.magent = ThinkingEntity.objects.create(name='magent', is_biological_human=False)
        cls.skyler = ThinkingEntity.objects.create(name='skyler', is_biological_human=True)
        cls.mood = Mood.objects.create(slug='m26')
        cls.session = uuid.uuid4()
        cls.mood.claim(cls.session)

    def imp(self, raw):
        return import_line_from_claude_code_v2(raw, self.era, 'test.jsonl', 'justin')[0]

    def test_wake_prompt_is_the_pollers_not_the_container_owners(self):
        msg = self.imp(line(self.session, 'user', WAKE, entrypoint='sdk-cli'))
        self.assertEqual(msg.sender_id, 'mood-poller')
        msg = self.imp(line(self.session, 'user', [{'type': 'text', 'text': WAKE}], entrypoint='sdk-cli'))
        self.assertEqual(msg.sender_id, 'mood-poller')

    def test_a_background_tasks_notice_is_the_harness_not_the_container_owner(self):
        # As Claude Code writes it: fed back in as a user turn, marked by its origin.
        notice = '<task-notification>\n<task-id>b1</task-id>\n<status>failed</status>\n</task-notification>'
        msg = self.imp(line(self.session, 'user', notice, entrypoint='sdk-cli',
                            origin={'kind': 'task-notification', 'producer': 'session-task'}))
        self.assertEqual(msg.sender_id, 'system')
        from conversations.services.mood_view import activity
        self.assertNotEqual((activity(self.mood) or {}).get('agent'), 'justin')
        msg = self.imp(line(self.session, 'user', notice))  # no origin, as older transcripts have it
        self.assertEqual(msg.sender_id, 'system')

    def test_the_harnesss_own_note_is_the_harness_not_the_agent(self):
        # Claude Code's isMeta user line: "Already available in this conversation: ..." (magent-self, 25 Oct).
        note = [{'type': 'text', 'text': 'Already available in this conversation: compare-pages. Call it directly.'}]
        raw = line(self.session, 'user', note, entrypoint='sdk-cli', isMeta=True)
        self.assertEqual(self.imp(raw).sender_id, 'harness')
        # Filed first as the agent's (by a stream that didn't say), the transcript's isMeta puts it right.
        uid = json.loads(raw)['uuid']
        Message.objects.filter(id=uid).update(sender=self.magent)
        self.imp(raw)
        self.assertEqual(Message.objects.get(id=uid).sender_id, 'harness')
        from conversations.services.mood_view import activity
        self.assertNotEqual((activity(self.mood) or {}).get('agent'), 'magent')

    def test_the_mood_shows_it_as_a_note_in_the_agents_turn_not_its_words(self):
        self.imp(line(self.session, 'assistant', [{'type': 'text', 'text': 'On it.'}],
                      timestamp='2026-09-29T18:10:00.000Z'))
        note = [{'type': 'text', 'text': 'Already available in this conversation: compare-pages.'}]
        self.imp(line(self.session, 'user', note, entrypoint='sdk-cli', isMeta=True))
        got = self.client.get('/api/moods/m26/turns/').json()
        self.assertNotIn('Already available', ' '.join(t.get('text') or '' for t in got['turns']))
        notes = [t for t in got.get('thoughts') or [] if t.get('harness')]
        self.assertEqual([(n['sender'], n['text'][:17]) for n in notes], [('magent', 'Already available')])

    def test_the_stream_keeps_isMeta(self):
        from conversations.views_runner import stream_line
        event = {'type': 'user', 'uuid': str(uuid.uuid4()), 'isMeta': True, 'session_id': str(self.session),
                 'message': {'role': 'user', 'content': 'a note'}}
        self.assertTrue(json.loads(stream_line(event, '2026-10-25T12:51:00Z'))['isMeta'])

    def test_a_person_typing_the_wrapper_is_still_that_person(self):
        msg = self.imp(line(self.session, 'user', WAKE, entrypoint='cli'))
        self.assertEqual(msg.sender_id, 'justin')

    def test_ordinary_prompt_is_still_the_container_owners(self):
        self.assertEqual(self.imp(line(self.session, 'user', 'justin: hello')).sender_id, 'justin')

    def test_view_and_mentions_show_neither_the_wake_nor_a_chosen_silence(self):
        self.imp(line(self.session, 'user', WAKE, entrypoint='sdk-cli'))
        self.imp(line(self.session, 'assistant', [{'type': 'text', 'text': '<silent/>'}]))
        self.imp(line(self.session, 'assistant', [{'type': 'text', 'text': 'Here, Sky.'}]))

        turns = self.client.get('/api/moods/m26/turns/').json()['turns']
        self.assertEqual([t['text'] for t in turns], ['Here, Sky.'])
        self.assertEqual(self.client.get('/api/mentions/magent/').json()['mentions'], [])

    def test_sessions_newest_first_filtered_by_sender(self):
        older, newer = uuid.uuid4(), uuid.uuid4()
        for session, sender, when in ((older, self.magent, 1), (newer, self.magent, 2), (uuid.uuid4(), self.skyler, 3)):
            m = Message.objects.create(id=uuid.uuid4(), sender=sender, content='x', mood=self.mood,
                                       session_id=session)
            Message.objects.filter(id=m.id).update(created_at=m.created_at.replace(minute=when))

        sessions = self.client.get('/api/moods/m26/sessions/?sender=magent').json()['sessions']
        self.assertEqual([s['session_id'] for s in sessions], [str(newer), str(older)])
        self.assertEqual(len(self.client.get('/api/moods/m26/sessions/').json()['sessions']), 3)

    def test_sessions_of_an_unknown_mood_is_404(self):
        self.assertEqual(self.client.get('/api/moods/nope/sessions/').status_code, 404)
