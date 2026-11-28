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

    def test_forked_again_it_goes_on_from_the_parents_session_as_it_is_now(self):
        fork = str(uuid.uuid4())
        self.client.post('/api/moods/jams-setlist/stream/', json.dumps(
            {'harness': 'claude-code', 'session_id': fork, 'events': []}), content_type='application/json', **AUTH)
        lines, _ = self.fork_file(fork)
        import_lines(lines, source='hunter-watcher', username='justin')
        later = str(uuid.uuid4())  # magent went on in #jams meanwhile
        self.jams.claim(later)
        import_lines([line('assistant', 'Later, in jams.', later, str(uuid.uuid4()), at='2026-10-26T10:00:00.000Z')],
                     source='hunter-watcher', username='justin')
        self.thread.forked_at = timezone.now()  # what api_refork does
        self.thread.save()
        r = self.client.get('/api/moods/jams-setlist/sessions/?sender=magent').json()
        self.assertEqual([s['session_id'] for s in r['sessions']][0], later)  # not its own fork from before
        self.assertNotIn(fork, [s['session_id'] for s in r['sessions']])
        self.assertEqual(r['sessions'][0]['from'], 'jams')

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
        self.assertEqual((made['slug'], made['thread_of'], made['kind']), ('jams.setlist', 'jams', 'fork'))
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
        plain = self.open(title='Encores').json()  # 'Setlist' is had now
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

    def test_handed_back_it_has_an_arrow_until_forked_again(self):
        def listed():
            return next(m for m in self.client.get('/api/moods/').json()['moods'] if m['slug'] == 'jams-setlist')
        self.assertFalse(listed()['incorporated'])
        self.client.post('/api/moods/jams-setlist/close-thread/', json.dumps({'incorporated': True}), content_type='application/json')
        self.assertTrue(listed()['incorporated'])
        was = self.thread.forked_at
        r = self.client.post('/api/moods/jams-setlist/refork/', '{}', content_type='application/json')
        self.assertEqual(r.status_code, 200)
        self.thread.refresh_from_db()
        self.assertGreater(self.thread.forked_at, was)
        self.assertFalse(listed()['incorporated'])
        events = self.client.get('/api/moods/jams-setlist/turns/').json()['events']
        self.assertEqual([(e['type'], e.get('from_mood'), e.get('was')) for e in events if e['type'] == 'reforked'],
                         [('reforked', 'jams', 'fork')])
        self.assertEqual(self.client.post('/api/moods/jams/refork/', '{}', content_type='application/json').status_code, 400)
        # Compacted in #jams since the fork: shown as fresh, until it's forked again.
        self.assertFalse(listed()['compacted'])
        from conversations.models import ConversationParticipant
        magent = ConversationParticipant.objects.get_or_create(name='magent', defaults={'participant_type': 'assistant'})[0]
        Message.objects.create(id=uuid.uuid4(), sender=magent, mood=self.jams, timestamp=1, is_continuation_message=True,
                               content='This session is being continued from a previous conversation...')
        self.assertTrue(listed()['compacted'])
        self.client.post('/api/moods/jams-setlist/refork/', '{}', content_type='application/json')
        self.assertFalse(listed()['compacted'])
        self.client.cookies.clear()
        self.assertEqual(self.client.post('/api/moods/jams-setlist/refork/', '{}', content_type='application/json').status_code, 401)

    def test_handoff_to_another_mood_wakes_magent_alone(self):
        self.assertEqual(self.say('jams', '/handoff #elsewhere the setlist').status_code, 201)
        self.assertEqual(self.client.get('/api/moods/jams/turns/').json()['turns'][-1]['mentions'], ['magent'])
        for text in ('/handoff #nowhere', '/handoff #jams', '/handoff'):
            with self.subTest(text=text):
                self.assertEqual(self.say('jams', text).status_code, 400)


@override_settings(MOOD_RUNNER_KEYS={'magent': KEY}, INTERFACE_WORDS_FROM_WIKI=False)
class MoveThreadTest(TestCase):
    """Moving a thread to another Mood (Justin, emergence, 1 Nov 2026): four boxes, in their order, in one call --
    incorporate into the Mood it leaves, compact, the move (a line in it and both Moods), incorporate into the one
    it joins, and a post telling the thread where it lives now. All or nothing."""

    def setUp(self):
        from conversations.services import mood_auth
        self.justin = ThinkingEntity.objects.update_or_create(name='justin', defaults={'is_biological_human': True})[0]
        ThinkingEntity.objects.update_or_create(name='magent', defaults={'is_biological_human': False})
        ThinkingEntity.objects.update_or_create(name='re', defaults={'is_biological_human': False})
        self.jams = Mood.objects.create(slug='jams', title='jams')
        self.shows = Mood.objects.create(slug='shows', title='shows')
        self.thread = Mood.objects.create(slug='jams.setlist', title='setlist', thread_of=self.jams,
                                          forked_at=timezone.now())
        Message.objects.create(id=uuid.uuid4(), sender_id='re', mood=self.thread, content='Salt Creek opens.',
                               timestamp=1)
        _, token = mood_auth.enrol_device(self.justin, 'laptop', tier='key')
        self.client.cookies[mood_auth.COOKIE] = token

    def move(self, **body):
        return self.client.post('/api/moods/jams.setlist/move-thread/', json.dumps(body), content_type='application/json')

    def said(self, slug):
        return [t['text'] for t in self.client.get(f'/api/moods/{slug}/turns/').json()['turns']
                if t['sender'] == 'justin']

    def test_every_box_in_its_order(self):
        r = self.move(to='shows', incorporate_from=True, incorporate_to=True, compact=True, inform=True, note='the set')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((r.json()['slug'], r.json()['thread_of'], r.json()['from'], len(r.json()['posted'])),
                         ('shows.setlist', 'shows', 'jams', 4))
        self.thread.refresh_from_db()
        self.assertEqual((self.thread.slug, self.thread.thread_of), ('shows.setlist', self.shows))
        self.assertEqual(Mood.by_slug('jams.setlist'), self.thread)  # old namespace kept as alias
        self.assertEqual(self.said('jams'), ['/incorporate #jams.setlist the set'])
        self.assertEqual(self.said('shows'), ['/incorporate #shows.setlist the set'])
        compact, inform = self.said('shows.setlist')
        self.assertEqual(compact, '/compact re the set')  # who has spoken in it; magent hasn't
        self.assertIn('moved from #jams to #shows', inform)
        self.assertIn('/moods/shows/threads/setlist/', inform)
        # A line in it, and in both Moods; the ↩ mark doesn't outlive a move.
        for slug in ('shows.setlist', 'jams', 'shows'):
            events = self.client.get(f'/api/moods/{slug}/turns/').json()['events']
            self.assertEqual([(e['thread'], e['from_mood'], e['to_mood']) for e in events if e['type'] == 'moved'],
                             [('shows.setlist', 'jams', 'shows')])
        listed = next(m for m in self.client.get('/api/moods/').json()['moods'] if m['slug'] == 'shows.setlist')
        self.assertEqual(listed['thread_of'], 'shows')

    def test_no_boxes_is_the_move_alone(self):
        r = self.move(to='shows')
        self.assertEqual((r.status_code, r.json()['slug'], r.json()['posted']), (200, 'shows.setlist', []))
        self.assertEqual(self.said('shows.setlist') + self.said('jams') + self.said('shows'), [])

    def test_its_worktrees_go_under_the_mood_its_turns_run_in(self):
        Mood.objects.create(slug='shows.fall', title='fall', thread_of=self.shows)
        r = self.move(to='shows.fall', inform=True)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['slug'], 'shows.fall.setlist')
        self.assertIn('/moods/shows/threads/setlist/', self.said('shows.fall.setlist')[0])

    def test_refused_and_nothing_done(self):
        from conversations.services import settings as knobs
        Mood.objects.create(slug='jams.setlist.encore', title='encore', thread_of=self.thread)
        knobs.change('archived', True, mood=Mood.objects.create(slug='old-shows'), by=self.justin)
        for to, why in (('jams', 'already'), ('nowhere', 'no Mood'), ('jams.setlist', 'under itself'),
                        ('jams.setlist.encore', 'under itself'), ('old-shows', 'archived')):
            with self.subTest(to=to):
                r = self.move(to=to, incorporate_from=True, inform=True)
                self.assertEqual(r.status_code, 400)
                self.assertIn(why, r.json()['error'])
        # Moving into a Mood that already has a thread of the same name is 409, before posting anything.
        Mood.objects.create(slug='shows.setlist', title='setlist', thread_of=self.shows)
        r = self.move(to='shows', incorporate_from=True, inform=True)
        self.assertEqual(r.status_code, 409)
        self.thread.refresh_from_db()
        self.assertEqual((self.thread.slug, self.thread.thread_of), ('jams.setlist', self.jams))
        self.assertEqual(self.said('jams') + self.said('jams.setlist'), [])
        self.assertEqual(self.client.post('/api/moods/jams/move-thread/', json.dumps({'to': 'shows'}),
                                          content_type='application/json').status_code, 400)  # not a thread

    def test_only_with_a_key(self):
        self.client.cookies.clear()
        self.assertIn(self.move(to='shows').status_code, (401, 403))
        self.thread.refresh_from_db()
        self.assertEqual(self.thread.thread_of, self.jams)


@override_settings(MOOD_RUNNER_KEYS={'magent': KEY})
class ThreadNamespaceTest(TestCase):
    """Threads are namespaced under their Mood (#interfaces.threads: Justin, 18 Nov), not universally unique."""

    def setUp(self):
        from conversations.services import mood_auth
        self.justin = ThinkingEntity.objects.update_or_create(name='justin', defaults={'is_biological_human': True})[0]
        ThinkingEntity.objects.update_or_create(name='magent', defaults={'is_biological_human': False})
        self.interfaces = Mood.objects.create(slug='interfaces', title='interfaces')
        self.ops = Mood.objects.create(slug='ops', title='ops')
        _, token = mood_auth.enrol_device(self.justin, 'laptop', tier='key')
        self.client.cookies[mood_auth.COOKIE] = token

    def open_thread(self, parent, title='threads'):
        return self.client.post('/api/moods/new/', json.dumps({'title': title, 'thread_of': parent}),
                                content_type='application/json')

    def test_same_thread_name_in_different_moods_and_dotted_links(self):
        from conversations.services.mood_view import render_html
        a = self.open_thread('interfaces', 'threads')
        b = self.open_thread('ops', 'threads')
        self.assertEqual((a.status_code, a.json()['slug']), (201, 'interfaces.threads'))
        self.assertEqual((b.status_code, b.json()['slug']), (201, 'ops.threads'))
        # Both URLs serve the page and APIs.
        self.assertEqual(self.client.get('/moods/interfaces.threads/').status_code, 200)
        self.assertEqual(self.client.get('/api/moods/ops.threads/turns/').json()['mood']['slug'], 'ops.threads')
        # #interfaces.threads links cleanly, leaving sentence punctuation outside; #interfaces.Then links #interfaces.
        html = render_html('Take it to #interfaces.threads. We started in #interfaces.Then moved on.')
        self.assertIn('<a class="moodlink" href="/moods/interfaces.threads/" data-mood="interfaces.threads">#interfaces.threads</a>.', html)
        self.assertIn('<a class="moodlink" href="/moods/interfaces/" data-mood="interfaces">#interfaces</a>.Then', html)

    def test_incorporate_and_handoff_accept_namespaced_or_local_thread_refs(self):
        from poller.mood_poller import MoodPoller
        self.open_thread('interfaces', 'threads')
        for text in ('/incorporate #interfaces.threads carry it back', '/incorporate #threads carry it back',
                     '/handoff #interfaces.threads about names', '/handoff #threads about names'):
            r = self.client.post('/api/moods/interfaces/say/', json.dumps({'text': text}),
                                 content_type='application/json')
            self.assertEqual(r.status_code, 201, (text, r.content))
        # Another Mood's thread cannot be incorporated into #ops.
        bad = self.client.post('/api/moods/ops/say/', json.dumps({'text': '/incorporate #interfaces.threads'}),
                               content_type='application/json')
        self.assertEqual(bad.status_code, 400)
        # Poller resolves both #interfaces.threads and shorthand #threads under parent='interfaces'.
        poller = MoodPoller.__new__(MoodPoller)
        poller.texts = None
        poller.api = mock.Mock()
        poller.api.recent.side_effect = lambda s, limit=1000: (
            {'mood': {'slug': 'interfaces.threads'}, 'turns': []} if s == 'interfaces.threads'
            else (_ for _ in ()).throw(RuntimeError('not found'))
        )
        brief = '\n'.join(poller.incorporate_brief('threads', 'the key', parent='interfaces'))
        self.assertIn('#interfaces.threads', brief)

    def test_migration_namespaces_existing_threads_and_keeps_legacy_aliases(self):
        from importlib import import_module
        from django.apps import apps
        from conversations.models import MoodAlias
        from conversations.services.mood_view import render_html
        from conversations.mcp.tools import list_moods_text, read_mood_text
        legacy = Mood.objects.create(slug='threads', title='threads', thread_of=self.interfaces)
        mig = import_module('conversations.migrations.0055_thread_namespaces')
        mig.namespace_threads(apps, None)
        legacy.refresh_from_db()
        self.assertEqual(legacy.slug, 'interfaces.threads')
        self.assertEqual(MoodAlias.objects.filter(mood=legacy).values_list('slug', flat=True)[0], 'threads')
        # Legacy URL redirects to the namespaced address; read_mood finds both.
        self.assertRedirects(self.client.get('/moods/threads/'), '/moods/interfaces.threads/',
                             fetch_redirect_response=False)
        self.assertIn('interfaces.threads -- threads (thread of #interfaces)', list_moods_text())
        self.assertIn('(interfaces.threads)', read_mood_text('interfaces.threads'))
        self.assertIn('(interfaces.threads)', read_mood_text('threads'))
        # Once #ops also opens a 'threads' thread, bare #threads in prose is ambiguous and not hijacked.
        self.assertIn('data-mood="interfaces.threads">#threads</a>', render_html('back in #threads'))
        self.open_thread('ops', 'threads')
        self.assertNotIn('moodlink', render_html('which #threads?'))

    def test_voice_and_mood_switch_with_namespaced_threads(self):
        from conversations.services import mood_switch, voice
        t1 = Mood.objects.get(slug=self.open_thread('interfaces', 'threads').json()['slug'])
        t2 = Mood.objects.get(slug=self.open_thread('ops', 'threads').json()['slug'])
        moods = [self.interfaces, self.ops, t1, t2]
        self.assertIn('pound interfaces dot threads', voice.plain('See #interfaces.threads.'))
        self.assertIsNone(mood_switch.match('go to threads', moods))  # ambiguous across two Moods
        self.assertEqual(mood_switch.match('go to interfaces threads', moods), 'interfaces.threads')
        self.assertEqual(mood_switch.match('go to ops dot threads', moods), 'ops.threads')
