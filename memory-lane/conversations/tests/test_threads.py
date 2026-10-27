"""Threads (Justin, 25 Oct): a Mood opened from another, forked -- its agents go on from where they were there,
remembering it all -- or fresh. The first question (magent and re, 25 Oct): when an agent is woken in a forked
thread, does what it says land in the thread, or back in the Mood it was forked from?

A wake forks the agent's session (claude --resume <it> --fork-session --session-id <new>), and the fork's file
repeats the history it copied under that history's own message ids (checked with Claude Code 2.1.302, 25 Oct).
Those ids are in the parent Mood; the watcher, importing the fork's file, files a session it hasn't seen where
its history is (MoodSession.claim_by_history). So unless the thread claims the new session first, the agent's
words go back to the parent: the trap, reproduced below. The runner now claims it before launching
(StreamPoster.claim), and then they land in the thread.
"""

import json
import uuid
from datetime import timedelta
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from conversations.models import Message, Mood, MoodSession, ThinkingEntity
from conversations.views import import_lines
from poller.mood_poller import StreamPoster

KEY = 'k' * 64
AUTH = {'HTTP_AUTHORIZATION': f'Bearer {KEY}'}


def line(kind, text, session, uuid_, parent=None, at='2026-10-25T16:00:00.000Z'):
    """A transcript line as Claude Code writes it, which the watcher sends."""
    message = {'role': kind, 'content': [{'type': 'text', 'text': text}]}
    if kind == 'assistant':
        message.update({'model': 'claude-opus-5-5', 'id': f'msg_{uuid_[:8]}', 'type': 'message',
                        'stop_reason': 'end_turn', 'usage': {'input_tokens': 3, 'output_tokens': 5}})
    return json.dumps({'type': kind, 'uuid': uuid_, 'parentUuid': parent, 'sessionId': session, 'timestamp': at,
                       'userType': 'external', 'isSidechain': False, 'cwd': '/home/magent/workspace', 'message': message})


@override_settings(MOOD_RUNNER_KEYS={'magent': KEY})
class ForkedThreadTest(TestCase):

    def setUp(self):
        ThinkingEntity.objects.update_or_create(name='justin', defaults={'is_biological_human': True})
        ThinkingEntity.objects.update_or_create(name='magent', defaults={'is_biological_human': False})
        self.jams = Mood.objects.create(slug='jams')
        # magent spoke in #jams, in session `before`: two lines, ids a and b.
        self.before, self.a, self.b = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
        self.jams.claim(self.before)
        import_lines([line('user', 'which tune opens?', self.before, self.a),
                      line('assistant', 'Salt Creek.', self.before, self.b, parent=self.a)],
                     source='hunter-watcher', username='justin')
        self.thread = Mood.objects.create(slug='jams-setlist', thread_of=self.jams, forked_at=timezone.now())

    def fork_file(self, session):
        """The fork's file: the history copied under its own ids (a, b), then the wake and the new reply."""
        wake, reply = str(uuid.uuid4()), str(uuid.uuid4())
        return [line('user', 'which tune opens?', session, self.a),
                line('assistant', 'Salt Creek.', session, self.b, parent=self.a),
                line('user', '<mood-wake mood="jams-setlist">and second?</mood-wake>', session, wake, parent=self.b),
                line('assistant', 'Old Joe Clark, I think.', session, reply, parent=wake)], reply

    def test_a_forked_thread_goes_on_from_the_parents_session_as_it_was(self):
        r = self.client.get('/api/moods/jams-setlist/sessions/?sender=magent').json()
        self.assertEqual([s['session_id'] for s in r['sessions']], [self.before])
        self.assertEqual(r['sessions'][0]['from'], 'jams')
        # Spoken in #jams after the fork: not what the thread goes on from.
        later = str(uuid.uuid4())
        self.jams.claim(later)
        Message.objects.filter(id=self.b).update(created_at=self.thread.forked_at - timedelta(seconds=5))
        import_lines([line('assistant', 'Later, in jams.', later, str(uuid.uuid4()), at='2026-10-26T10:00:00.000Z')],
                     source='hunter-watcher', username='justin')
        r = self.client.get('/api/moods/jams-setlist/sessions/?sender=magent').json()
        self.assertEqual([s['session_id'] for s in r['sessions']], [self.before])

    def test_the_trap_unclaimed_a_fork_lands_back_in_the_parent(self):
        fork = str(uuid.uuid4())
        lines, reply = self.fork_file(fork)
        import_lines(lines, source='hunter-watcher', username='justin')
        self.assertEqual(Message.objects.get(id=reply).mood, self.jams)  # not the thread

    def test_claimed_first_it_lands_in_the_thread(self):
        fork = str(uuid.uuid4())
        # What the runner does before launching: an empty post to the thread's stream.
        r = self.client.post('/api/moods/jams-setlist/stream/', json.dumps(
            {'harness': 'claude-code', 'session_id': fork, 'events': []}), content_type='application/json', **AUTH)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(MoodSession.mood_for(fork), self.thread)
        lines, reply = self.fork_file(fork)
        import_lines(lines, source='hunter-watcher', username='justin')
        self.assertEqual(Message.objects.get(id=reply).mood, self.thread)
        # The copied history stays where it was said, once.
        self.assertEqual(Message.objects.get(id=self.b).mood, self.jams)
        self.assertEqual(Message.objects.filter(id=self.b).count(), 1)
        # And from now on the thread's own session is the one it goes on from.
        r = self.client.get('/api/moods/jams-setlist/sessions/?sender=magent').json()
        self.assertEqual(r['sessions'][0]['session_id'], fork)
        self.assertNotIn('from', r['sessions'][0])

    def test_a_fresh_thread_starts_empty(self):
        Mood.objects.create(slug='jams-fresh', thread_of=self.jams)  # no forked_at: fresh
        self.assertEqual(self.client.get('/api/moods/jams-fresh/sessions/?sender=magent').json()['sessions'], [])

    def test_the_runner_claims_before_it_launches(self):
        http = mock.Mock()
        http.post.return_value.status_code = 200
        poster = StreamPoster('http://ml', KEY, 'jams-setlist', 'f0f0f0f0-0000-4000-8000-000000000000', http=http)
        self.assertTrue(poster.claim())
        body = http.post.call_args.kwargs['json']
        self.assertEqual((body['session_id'], body['events']), ('f0f0f0f0-0000-4000-8000-000000000000', []))
        self.assertEqual(http.post.call_args.args[0], 'http://ml/api/moods/jams-setlist/stream/')
        poster.close(wait=1)


@override_settings(MOOD_RUNNER_KEYS={'magent': KEY})
class OpenThreadTest(TestCase):
    """Opening a thread from the page: forked or fresh, its Mood's settings carried, a line in both, and the
    thread's emergents given its Mood's instructions until it has its own."""

    def setUp(self):
        from conversations.services import mood_auth
        from conversations.services import settings as knobs
        self.justin = ThinkingEntity.objects.update_or_create(name='justin', defaults={'is_biological_human': True})[0]
        self.magent = ThinkingEntity.objects.update_or_create(name='magent', defaults={'is_biological_human': False})[0]
        self.jams = Mood.objects.create(slug='jams', title='jams')
        knobs.change('mention_effort', 'low', mood=self.jams, agent=self.magent, by=self.justin)
        knobs.change('archived', True, mood=self.jams, by=self.justin)  # the Mood's own flag: never carried
        _, token = mood_auth.enrol_device(self.justin, 'laptop', tier='key')
        self.client.cookies[mood_auth.COOKIE] = token

    def open(self, **body):
        return self.client.post('/api/moods/new/', json.dumps({'title': 'Setlist', **body}), content_type='application/json')

    def test_forked_by_default_with_its_settings_and_a_line_in_both(self):
        from conversations.services import settings as knobs
        made = self.open(thread_of='jams').json()
        self.assertEqual((made['thread_of'], made['kind']), ('jams', 'fork'))
        thread = Mood.objects.get(slug=made['slug'])
        self.assertEqual(thread.thread_of, self.jams)
        self.assertIsNotNone(thread.forked_at)
        self.assertEqual(knobs.resolve(thread.slug, 'magent')['mention_effort'], 'low')
        self.assertNotIn(thread.slug, knobs.archived_slugs())
        here = self.client.get(f'/api/moods/{thread.slug}/turns/').json()
        self.assertEqual((here['mood']['thread_of'], bool(here['mood']['forked_at'])), ('jams', True))
        self.assertEqual([(e['type'], e.get('from_mood'), e.get('kind')) for e in here['events']], [('created', 'jams', 'fork')])
        there = self.client.get('/api/moods/jams/turns/').json()
        self.assertEqual([(e['type'], e.get('to_mood')) for e in there['events']], [('thread', thread.slug)])
        self.assertEqual(there['mood']['threads'], [{'slug': thread.slug, 'title': 'Setlist'}])

    def test_fresh_and_nowhere(self):
        fresh = Mood.objects.get(slug=self.open(thread_of='jams', fork=False).json()['slug'])
        self.assertIsNone(fresh.forked_at)
        self.assertEqual(self.open(thread_of='nowhere').status_code, 404)
        plain = self.open().json()
        self.assertNotIn('thread_of', plain)

    def test_its_mood_instructions_until_it_has_its_own(self):
        from poller.instructions import bundle
        pages = {'Cryptograss:Moods/jams/Instructions': [(7, 'JMyles', 'Keep it to the tunes.')]}
        get = lambda title: pages.get(title, [(1, 'JMyles', 'Be kind.')] if 'Moods/' not in title or title.endswith('Moods/Instructions') else [])
        import tempfile
        b = bundle('magent', 'setlist', get=get, state_dir=tempfile.mkdtemp(), parent='jams')
        self.assertIn('Keep it to the tunes.', b.text)
        self.assertIn('Cryptograss:Moods/jams/Instructions', json.dumps(b.record()))
        pages['Cryptograss:Moods/setlist/Instructions'] = [(9, 'JMyles', 'Only the setlist.')]
        b = bundle('magent', 'setlist', get=get, state_dir=tempfile.mkdtemp(), parent='jams')
        self.assertIn('Only the setlist.', b.text)
        self.assertNotIn('Keep it to the tunes.', b.text)


@override_settings(MOOD_RUNNER_KEYS={'magent': KEY})
class IncorporateThreadTest(TestCase):
    """Closing a thread hands it to its Mood: "/incorporate #<it>" there, for magent alone; then a line in it."""

    def setUp(self):
        from conversations.services import mood_auth
        self.justin = ThinkingEntity.objects.update_or_create(name='justin', defaults={'is_biological_human': True})[0]
        ThinkingEntity.objects.update_or_create(name='magent', defaults={'is_biological_human': False})
        ThinkingEntity.objects.update_or_create(name='re', defaults={'is_biological_human': False})
        self.jams = Mood.objects.create(slug='jams', title='jams')
        self.thread = Mood.objects.create(slug='jams-setlist', thread_of=self.jams, forked_at=timezone.now())
        Mood.objects.create(slug='elsewhere')
        _, token = mood_auth.enrol_device(self.justin, 'laptop', tier='key')
        self.client.cookies[mood_auth.COOKIE] = token

    def say(self, slug, text):
        return self.client.post(f'/api/moods/{slug}/say/', json.dumps({'text': text}), content_type='application/json')

    def test_incorporate_wakes_magent_alone_and_only_for_its_own_thread(self):
        self.assertEqual(self.say('jams', '/incorporate #jams-setlist the key, mostly').status_code, 201)
        turn = self.client.get('/api/moods/jams/turns/').json()['turns'][-1]
        self.assertEqual(turn['mentions'], ['magent'])
        for slug, text in (('elsewhere', '/incorporate #jams-setlist'), ('jams', '/incorporate #nowhere'), ('jams', '/incorporate')):
            with self.subTest(text=text):
                self.assertEqual(self.say(slug, text).status_code, 400)

    def test_closed_it_is_archived_with_a_line_saying_where_it_went(self):
        from conversations.services import settings as knobs
        r = self.client.post('/api/moods/jams-setlist/close-thread/', json.dumps({'incorporated': True}),
                             content_type='application/json')
        self.assertEqual(r.json(), {'slug': 'jams-setlist', 'archived': True, 'incorporated': True})
        self.assertIn('jams-setlist', knobs.archived_slugs())
        events = self.client.get('/api/moods/jams-setlist/turns/').json()['events']
        self.assertEqual([(e['type'], e.get('into')) for e in events if e['type'] == 'incorporated'], [('incorporated', 'jams')])
        self.assertEqual(self.client.post('/api/moods/jams/close-thread/', '{}', content_type='application/json').status_code, 400)

    def test_handoff_to_another_mood_wakes_magent_alone(self):
        self.assertEqual(self.say('jams', '/handoff #elsewhere the setlist').status_code, 201)
        self.assertEqual(self.client.get('/api/moods/jams/turns/').json()['turns'][-1]['mentions'], ['magent'])
        for text in ('/handoff #nowhere', '/handoff #jams', '/handoff'):
            with self.subTest(text=text):
                self.assertEqual(self.say('jams', text).status_code, 400)
