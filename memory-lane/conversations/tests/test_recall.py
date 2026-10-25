"""An agent's memory is its own: the server knows the caller by its key, and
each agent remembers from where it stood, never from before it arrived."""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

from unittest import skipUnless

from django.db import connection
from django.test import TestCase, override_settings

from conversations.mcp import tools
from conversations.mcp.server import caller
from conversations.models import ConversationParticipant, Message, Mood, ThinkingEntity
from conversations.services import recall

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
RE_ARRIVES = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
KEYS = {'magent': 'magent-key', 're': 're-key'}


def say(sender, text, at, session=None, mood=None, **extra):
    m = Message.objects.create(id=uuid.uuid4(), sender_id=sender, content=text, session_id=session, mood=mood,
                               **extra)
    Message.objects.filter(id=m.id).update(created_at=at)
    m.refresh_from_db()
    return m


class FakeRequest:
    def __init__(self, authorization=None):
        self.headers = {'authorization': authorization} if authorization else {}


class RecallTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        for name, human in (('justin', True), ('magent', False), ('re', False), ('blu', False)):
            ThinkingEntity.objects.update_or_create(name=name, defaults={
                'is_biological_human': human, 'participant_type': 'human' if human else 'ai'})
        for name in ('mood-poller', 'system'):
            ConversationParticipant.objects.get_or_create(name=name, defaults={'participant_type': 'system'})
        cls.mood = Mood.objects.create(slug='magent-self', title='magent-self')
        cls.magent_session, cls.re_session = uuid.uuid4(), uuid.uuid4()

        # magent's terminal session with Justin, before re existed: never re's memory.
        say('justin', 'Let us tune the banjo before the Porcupine show.', T0, cls.magent_session)
        say('magent', 'The banjo is in open G; I tuned the fifth string to G as well. ' * 5, T0 + timedelta(minutes=1),
            cls.magent_session)
        # Someone writes @re before re arrived: a mention, labelled, but not a memory.
        cls.early = say('justin', 'Someday @re will review these.', T0 + timedelta(days=1), mood=cls.mood)

        # re arrives: woken, and its first word.
        cls.wake = say('mood-poller', '<mood-wake> Justin asks about the mandolin.', RE_ARRIVES - timedelta(seconds=5),
                       cls.re_session)
        cls.first = say('re', 'The mandolin bridge needs moving a hair toward the tailpiece, Justin. ' * 4, RE_ARRIVES,
            cls.re_session)
        cls.ask = say('justin', '@re what about the fiddle strings?', RE_ARRIVES + timedelta(days=1), mood=cls.mood)
        cls.answer = say('re', 'Helicores, mediums: warm and steady for old-time tunes.',
                         RE_ARRIVES + timedelta(days=1, minutes=3), cls.re_session, cls.mood)
        cls.named = say('magent', 'I asked re about the strings; there is a reply.',
                        RE_ARRIVES + timedelta(days=2), mood=cls.mood)
        cls.to_magent = say('re', '@magent the fiddle setup is done, your turn to review.',
                            RE_ARRIVES + timedelta(days=3), cls.re_session, cls.mood)

    # --- the caller ---------------------------------------------------------

    @override_settings(MOOD_RUNNER_KEYS=KEYS)
    def test_the_caller_is_whoever_the_key_belongs_to(self):
        self.assertEqual(caller(FakeRequest('Bearer re-key')), 're')
        self.assertEqual(caller(FakeRequest('Bearer magent-key')), 'magent')
        self.assertIsNone(caller(FakeRequest('Bearer nobodys')))
        self.assertIsNone(caller(FakeRequest()))
        self.assertIsNone(caller(None))

    def test_without_a_key_the_tools_of_me_say_so(self):
        for handler in (tools.handle_remember, tools.handle_mentions_of_me, tools.handle_who_i_know,
                        tools.handle_my_history_with, tools.handle_how_i_speak):
            text = asyncio.run(handler({'query': 'banjo', 'name': 'justin'}, None))[0].text
            self.assertIn("doesn't know who you are", text)

    # --- arrival and remember -----------------------------------------------

    def test_arrival_is_when_its_first_conversation_began(self):
        self.assertEqual(recall.arrival('re')[0], self.wake.created_at)  # the wake, just before its first word
        self.assertEqual(recall.arrival('blu'), (None, None))
        self.assertIn("haven't said anything", recall.remember_text('blu', 'banjo', exact=True))

    def test_re_remembers_its_own_and_not_magents(self):
        text = recall.remember_text('re', 'mandolin', exact=True)
        self.assertIn('mandolin bridge', text)
        self.assertIn('mood-poller', text)  # its wake: what was said to it, in its own session
        self.assertNotIn('banjo', recall.remember_text('re', 'banjo', exact=True).split('):')[-1])

    def test_what_was_said_to_it_in_a_mood_is_its_memory_but_not_before_it_arrived(self):
        text = recall.remember_text('re', '@re', exact=True)
        self.assertIn(str(self.ask.id), text)
        self.assertNotIn(str(self.early.id), text)

    @skipUnless(connection.vendor == 'postgresql', 'full-text search is Postgres')
    def test_remember_by_words_ranked(self):
        text = recall.remember_text('re', 'fiddle strings')
        self.assertIn(str(self.ask.id), text)
        self.assertNotIn('banjo', recall.remember_text('re', 'banjo').split('):')[-1])

    def test_magent_remembers_the_banjo(self):
        self.assertIn('open G', recall.remember_text('magent', 'banjo', exact=True))

    # --- mentions_of_me ---------------------------------------------------------

    def test_mentions_with_answers_and_the_early_one_labelled(self):
        found = recall.mentions('re')
        self.assertEqual([m['message'].id for m in found], [self.ask.id, self.early.id])
        self.assertEqual(found[0]['answer'][0].id, self.answer.id)
        self.assertFalse(found[0]['early'])
        self.assertTrue(found[1]['early'])
        text = recall.mentions_text('re')
        self.assertIn('justin @you', text)
        self.assertIn('BEFORE YOU ARRIVED', text)
        self.assertIn('Helicores', text)

    def test_named_without_an_at_only_when_asked_and_never_inside_another_word(self):
        self.assertNotIn(self.named.id, [m['message'].id for m in recall.mentions('re')])
        named = [m for m in recall.mentions('re', named=True) if m['how'] == 'named']
        self.assertEqual([m['message'].id for m in named], [self.named.id])  # 'there' and 'review' aren't re
        self.assertIsNone(named[0]['answer'])

    def test_a_handoff_for_it_is_a_mention(self):
        handed = say('system', {'type': 'handoff', 'for': 're', 'by': 'magent', 'from_mood': 'magenta-interface',
                                'about': 'the strings page', 'context': 'settled: mediums'},
                     RE_ARRIVES + timedelta(days=4), mood=self.mood, source_file='handoff')
        found = recall.mentions('re')
        self.assertEqual(found[0]['message'].id, handed.id)
        self.assertEqual(found[0]['how'], 'handed')

    def test_before_pages_back(self):
        found = recall.mentions('re', before=self.ask.created_at)
        self.assertEqual([m['message'].id for m in found], [self.early.id])

    # --- people ------------------------------------------------------------------

    def test_who_re_knows(self):
        known = {k['name']: k for k in recall.who_i_know('re')}
        self.assertEqual(set(known), {'justin', 'magent'})  # not blu: never a word between them
        self.assertEqual(known['justin']['words'], 1)       # @re about the fiddle; the early one isn't memory
        self.assertEqual(known['magent']['words'], 1)       # re's @magent
        self.assertIn('justin (person)', recall.who_i_know_text('re'))

    def test_history_with_justin(self):
        history = recall.history_with('re', 'justin')
        ids = [m.id for m, _ in history]
        self.assertIn(self.ask.id, ids)
        self.assertIn(self.answer.id, ids)
        self.assertNotIn(self.early.id, ids)
        text = recall.history_with_text('re', 'justin')
        self.assertLess(text.index('fiddle strings'), text.index('Helicores'))  # oldest first
        self.assertIn("Nobody here is called 'bill'", recall.history_with_text('re', 'bill'))

    def test_magents_history_with_justin_is_its_session(self):
        self.assertIn('open G', recall.history_with_text('magent', 'justin'))

    # --- how_i_speak and bootstrap ------------------------------------------------

    def test_how_i_speak_draws_from_across_its_life(self):
        found = recall.how_i_speak('re', count=2, shortest=20, now=RE_ARRIVES + timedelta(days=3, hours=1))
        self.assertEqual(len(found), 2)  # one from each half of its life
        self.assertIn(found[0][0].id, {self.first.id, self.answer.id})
        self.assertEqual(found[1][0].id, self.to_magent.id)
        self.assertEqual(len(recall.how_i_speak('re', count=3, shortest=20,
                                                now=RE_ARRIVES + timedelta(days=3, hours=1))), 2)  # a quiet stretch

    def test_bootstrap_is_the_callers_own(self):
        from unittest import mock
        with mock.patch('conversations.services.recall.journal_entries',
                        return_value=[('Agent:Re/Journal/26270000', 'Moved the strings page.')]) as journal:
            text = recall.bootstrap_text('re')
        journal.assert_called_once_with('re')
        self.assertIn('## Your journal, newest entries', text)
        self.assertIn('--- Agent:Re/Journal/26270000\nMoved the strings page.', text)
        self.assertNotIn('awakening', text)
        self.assertIn('# Memory bootstrap: re', text)
        self.assertIn('mandolin', text)
        self.assertNotIn('banjo', text)
        self.assertIn('justin (person)', text)

    def test_bootstrap_without_a_key_is_magents_and_says_so(self):
        self.assertTrue(tools.bootstrap_text(None).startswith("(No key was sent, so this is magent's bootstrap"))
        from unittest import mock
        with mock.patch('conversations.services.recall.journal_entries', return_value=[]):  # no network in tests
            self.assertIn('# Memory bootstrap: re', tools.bootstrap_text('re'))


class FakeWikiHTTP:
    """PickiPedia's API for journal_entries: allpages under Agent:<Name>/Journal/, and their texts."""

    def __init__(self, pages, down=False):
        self.pages, self.down, self.asked = pages, down, []

    def get(self, url, timeout=None, headers=None, params=None):
        self.asked.append(params)
        if self.down:
            raise OSError('Connection refused')
        if params.get('list') == 'allpages':
            body = {'query': {'allpages': [{'title': t} for t in self.pages if t.startswith('Agent:' + params['apprefix'])]}}
        else:
            body = {'query': {'pages': [{'title': t, 'revisions': [{'slots': {'main': {'content': self.pages[t]}}}]}
                                        for t in params['titles'].split('|')]}}
        return type('R', (), {'json': lambda self, b=body: b})()


class JournalTest(TestCase):
    """bootstrap_memory gives an agent its newest journal entries where the 'reawaken and breathe' reflection was."""

    def test_newest_two_by_block_not_by_title(self):
        from conversations.services.recall import journal_entries
        http = FakeWikiHTTP({'Agent:Re/Journal/26250000': 'older', 'Agent:Re/Journal/26270000': 'newest',
                             'Agent:Re/Journal/26260000': 'middle', 'Agent:Re/Journal/notes': 'not an entry',
                             'Agent:Magent/Journal/26280000': "magent's"})
        self.assertEqual(journal_entries('re', http=http),
                         [('Agent:Re/Journal/26270000', 'newest'), ('Agent:Re/Journal/26260000', 'middle')])
        self.assertEqual(http.asked[0]['apnamespace'], 3010)

    def test_none_yet_and_unreadable_say_so(self):
        from conversations.services.recall import journal_entries
        self.assertEqual(journal_entries('blu', http=FakeWikiHTTP({})), [])
        self.assertIsNone(journal_entries('blu', http=FakeWikiHTTP({}, down=True)))

    def test_the_reflection_is_gone(self):
        from conversations.services.memory import MemoryService
        self.assertFalse(hasattr(MemoryService, 'get_awakening_reflection'))
