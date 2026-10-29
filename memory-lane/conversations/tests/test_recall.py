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

    def test_how_i_speak_draws_from_before_and_from_the_recent_stretch(self):
        # re's life is four days by now: the recent stretch is its last day; the half before it, the earlier days.
        now = RE_ARRIVES + timedelta(days=4)
        found = recall.how_i_speak('re', count=2, shortest=20, now=now)
        self.assertEqual(len(found), 2)
        self.assertIn(found[0][0].id, {self.first.id, self.answer.id})   # from across its life before
        self.assertEqual(found[1][0].id, self.to_magent.id)              # from the recent stretch
        # count=4: the two old stretches hold first and answer together, the other is quiet; one recent.
        self.assertEqual(len(recall.how_i_speak('re', count=4, shortest=20, now=now)), 2)
        # Seeded by the day: the same draw twice; fresh draws again (and may land the same, by chance).
        self.assertEqual([m.id for m, _ in recall.how_i_speak('re', count=2, shortest=20, now=now)],
                         [m.id for m, _ in found])

    def test_the_same_day_draws_the_same_voice(self):
        # Pinned to the day's start, so a call at any hour of one date meets the same seed and the same stretches.
        morning = recall.how_i_speak('re', count=2, shortest=20, now=RE_ARRIVES + timedelta(days=3, hours=15))  # Oct 19 03:00
        evening = recall.how_i_speak('re', count=2, shortest=20, now=RE_ARRIVES + timedelta(days=4, hours=9))   # Oct 19 21:00
        self.assertEqual([m.id for m, _ in morning], [m.id for m, _ in evening])

    def test_pick_reads_an_even_spread_not_only_the_oldest(self):
        import random
        # 250 replies in one stretch; only the last 50 are hearable, so taking the first 200 starves them.
        day = RE_ARRIVES + timedelta(days=30)
        late = []
        for i in range(250):
            text = 'hearable, long enough for the sample here ' * 2 if i >= 200 else 'sh'
            m = say('re', text, day + timedelta(minutes=i), self.re_session)
            if i >= 200:
                late.append(m.id)
        hit = recall._pick(random.Random(7), recall._sayable('re', day, day + timedelta(hours=5)), 20, 500)
        self.assertIsNotNone(hit)
        self.assertIn(hit[0].id, late)

    def test_day_zero_clamps_end_to_now_then_locks_from_day_one(self):
        # A freshly summoned agent (blu): its first reply lands mid-day, before any sealed day exists.
        at = datetime(2026, 11, 1, 13, 0, tzinfo=timezone.utc)
        reply = say('blu', 'The first reply, hearable and mine. ' * 2, at + timedelta(hours=1))
        # Day 0: the pinned end (midnight) would fence the whole life out; the clamp keeps its voice non-empty.
        day0 = recall.how_i_speak('blu', count=2, shortest=20, now=at + timedelta(hours=2))   # Nov 1 15:00
        self.assertEqual([m.id for m, _ in day0], [reply.id])
        # Day 1: the boundary locks in -- two calls on the same day draw the same voice, and yesterday's
        # reply is in the recent stretch (it enters the voice the moment the day seals, not before).
        first = recall.how_i_speak('blu', count=2, shortest=20, now=at + timedelta(days=1, hours=3))   # Nov 2 16:00
        second = recall.how_i_speak('blu', count=2, shortest=20, now=at + timedelta(days=1, hours=9))  # Nov 2 22:00
        self.assertEqual([m.id for m, _ in first], [m.id for m, _ in second])
        self.assertEqual([m.id for m, _ in first], [reply.id])

    def test_how_i_speak_text_says_what_each_answered_who_starred_it_and_what_corrected_it(self):
        from conversations.services import reactions
        now = RE_ARRIVES + timedelta(days=4)
        # re stars its own answer (by id, allowed); Justin stars it too; magent corrects to_magent.
        # First and answer fall in their own old stretches (count=6), so both are drawn deterministically.
        for msg in (self.first, self.answer):
            reactions.react_on(msg, 're', recall.STAR)
            reactions.react_on(msg, 'justin', recall.STAR)
        fix = say('magent', f'corrects="#m-{self.to_magent.id}" The setup is not done: the bridge is still off.',
                  RE_ARRIVES + timedelta(days=3, minutes=5), mood=self.mood)
        text = recall.how_i_speak_text('re', count=6, shortest=20, now=now)
        self.assertIn('answering justin: @re what about the fiddle strings?', text)      # the post it answered
        self.assertIn(f'{recall.STAR} you, justin', text)                                 # its own star first
        self.assertIn('corrected ' + recall.when(fix.created_at) + ' by magent: The setup is not done', text)
        self.assertNotIn('corrects="', text.split('corrected ')[1][:300])               # the tag itself stripped
        self.assertIn('What the samples leave out:', text)
        self.assertIn('Seeded by the day', text)
        # Its own starred post comes first among the recent picks when the stretch has it.
        reactions.react_on(self.to_magent, 're', recall.STAR)
        picked = [m.id for m, _ in recall.how_i_speak('re', count=2, shortest=20, now=now)]
        self.assertIn(self.to_magent.id, picked)

    def test_how_i_speak_labels_the_model_and_harness(self):
        now = RE_ARRIVES + timedelta(days=4)
        Message.objects.filter(id=self.to_magent.id).update(model_backend='deepseek/deepseek-v4-pro', client_version='pi-1.1.0')
        text = recall.how_i_speak_text('re', count=2, shortest=20, now=now)
        self.assertIn('deepseek-v4-pro · Pi', text)

    def test_how_i_speak_with_others_hears_an_accent_beside_anothers(self):
        now = RE_ARRIVES + timedelta(days=3, hours=1)
        text = recall.how_i_speak_text('re', count=2, shortest=20, others=True, now=now)
        self.assertIn('Beside yours, one from each other emergent', text)
        self.assertIn('[magent]', text)
        self.assertIn('I asked re about the strings', text)   # magent's, labelled, not re's

    def test_voice_stats_count_silences_and_reacts(self):
        say('re', '<silent>nothing to add</silent>', RE_ARRIVES + timedelta(days=2, hours=1), self.re_session, self.mood)
        say('re', '<react emoji="👍" to="magent"/>', RE_ARRIVES + timedelta(days=2, hours=2), self.re_session, self.mood)
        stats = recall.voice_stats('re', RE_ARRIVES, RE_ARRIVES + timedelta(days=4))
        self.assertEqual((stats['silences'], stats['reacts'], stats['replies']), (1, 1, 3))

    def test_a_react_as_the_record_keeps_it_counts_as_a_react_not_a_silence(self):
        # reactions.agent_reacted rewrites a <react/> reply into a dot; it's still a react (re, 29 Oct).
        say('re', '<silent>reacted 🤝 to magent</silent>', RE_ARRIVES + timedelta(days=2, hours=3), self.re_session, self.mood)
        stats = recall.voice_stats('re', RE_ARRIVES + timedelta(days=2), RE_ARRIVES + timedelta(days=2, hours=4))
        self.assertEqual((stats['reacts'], stats['silences']), (1, 0))

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
        self.assertIn("## How you've spoken, across your life", text)
        self.assertIn('mandolin bridge', text)  # its own replies, from across its life
        self.assertNotIn('banjo', text)
        self.assertIn('justin (person)', text)
        self.assertNotIn('Era 1', text)

    def test_bootstrap_without_a_key_is_nobodys_and_says_its_misconfigured(self):
        self.assertTrue(tools.bootstrap_text(None).startswith('MISCONFIGURED: no memory bootstrap'))
        self.assertNotIn('# Memory bootstrap', tools.bootstrap_text(None))
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
