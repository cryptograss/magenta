"""Which tools an agent's turn had, 👀 or 🔨 by its name: what the last wake before it said."""

import uuid
from datetime import datetime, timedelta, timezone

from django.test import TestCase

from conversations.models import ConversationParticipant, Message, Mood, ThinkingEntity
from conversations.services.mood_view import turn_tools

T0 = datetime(2026, 10, 19, 17, 0, tzinfo=timezone.utc)
FOOTER = {'look': 'This turn can look but not touch: read and search files under ~/workspace, search your memory, '
                  'read PickiPedia. Never repeat secrets or private details.\n</mood-wake>',
          'full': 'This turn has your full tools, as in your terminal: the people who woke you are trusted to ask '
                  'for real work. Never repeat secrets or private details.\n</mood-wake>'}


class TurnToolsTest(TestCase):

    def setUp(self):
        self.justin = ThinkingEntity.objects.get_or_create(name='justin', defaults={'is_biological_human': True})[0]
        self.magent = ThinkingEntity.objects.get_or_create(name='magent', defaults={'is_biological_human': False})[0]
        self.re = ThinkingEntity.objects.get_or_create(name='re', defaults={'is_biological_human': False})[0]
        self.poller = ConversationParticipant.objects.get_or_create(name='mood-poller')[0]
        self.mood = Mood.objects.create(slug='connectome')
        self.session = uuid.uuid4()

    def say(self, sender, content, minutes, session=None):
        m = Message.objects.create(id=uuid.uuid4(), sender=sender, mood=self.mood, content=content,
                                   session_id=session or self.session)
        Message.objects.filter(id=m.id).update(created_at=T0 + timedelta(minutes=minutes))
        m.refresh_from_db()
        return m

    def wake(self, tools, minutes, quoting='', session=None):
        return self.say(self.poller, f'<mood-wake mood="connectome">\n[justin] hello\n{quoting}\n\n{FOOTER[tools]}',
                        minutes, session)

    def test_each_turn_had_what_the_last_wake_before_it_said(self):
        self.wake('look', 0)
        first = self.say(self.magent, 'A look.', 1)
        self.wake('full', 10)
        second = self.say(self.magent, [{'type': 'text', 'text': 'Now with tools.'}], 11)
        third = self.say(self.magent, 'Still the same turn.', 12)
        self.assertEqual(turn_tools([first, second, third]),
                         {str(first.id): 'look', str(second.id): 'full', str(third.id): 'full'})

    def test_only_the_wakes_closing_lines_count_not_what_it_quotes(self):
        # A full-tools wake quoting a message that says "This turn can look but not touch".
        self.wake('full', 0, quoting='[magent] That wake ends with "This turn can look but not touch", as designed. ' * 3)
        said = self.say(self.magent, 'Answering.', 1)
        self.assertEqual(turn_tools([said]), {str(said.id): 'full'})

    def test_a_turn_with_no_wake_in_reach_is_left_unmarked(self):
        self.wake('full', 0)
        late = self.say(self.magent, 'Much later.', 13 * 60)
        alone = self.say(self.magent, 'No wake at all.', 1, session=uuid.uuid4())
        self.assertEqual(turn_tools([late, alone]), {})

    def test_sessions_are_told_apart(self):
        other = uuid.uuid4()
        self.wake('full', 0)
        self.wake('look', 0, session=other)
        mine, theirs = self.say(self.magent, 'Mine.', 1), self.say(self.re, 'Theirs.', 1, session=other)
        self.assertEqual(turn_tools([mine, theirs]), {str(mine.id): 'full', str(theirs.id): 'look'})


class MarksOnThePageTest(TestCase):
    """The turns endpoint carries `tools` on agents' messages; a mention says what it wakes with."""

    def setUp(self):
        ThinkingEntity.objects.get_or_create(name='justin', defaults={'is_biological_human': True})[0]
        self.magent = ThinkingEntity.objects.get_or_create(name='magent', defaults={'is_biological_human': False})[0]
        ThinkingEntity.objects.get_or_create(name='re', defaults={'is_biological_human': False})[0]
        self.poller = ConversationParticipant.objects.get_or_create(name='mood-poller')[0]
        self.mood = Mood.objects.create(slug='connectome')
        self.session = uuid.uuid4()

    def say(self, sender, content, at):
        m = Message.objects.create(id=uuid.uuid4(), sender=sender, mood=self.mood, content=content,
                                   session_id=self.session)
        Message.objects.filter(id=m.id).update(created_at=at)
        return m

    def test_turns_carry_their_tools(self):
        now = datetime.now(timezone.utc)
        self.say(self.poller, f'<mood-wake mood="connectome">\nquiet\n\n{FOOTER["look"]}', now - timedelta(minutes=2))
        said = self.say(self.magent, '@re, one more correction.', now - timedelta(minutes=1))
        turns = self.client.get('/api/moods/connectome/turns/').json()['turns']
        mine = next(t for t in turns if t['id'] == str(said.id))
        self.assertEqual(mine['tools'], 'look')

    def test_a_mention_wakes_with_what_the_turn_that_wrote_it_had_not_the_sessions_latest(self):
        now = datetime.now(timezone.utc)
        self.say(self.poller, f'<mood-wake mood="connectome">\n\n{FOOTER["look"]}', now - timedelta(minutes=5))
        said = self.say(self.magent, '@re, have a look.', now - timedelta(minutes=4))
        self.say(self.poller, f'<mood-wake mood="connectome">\n\n{FOOTER["full"]}', now - timedelta(minutes=1))
        found = self.client.get('/api/mentions/re/').json()['mentions']
        turn = next(f['turn'] for f in found if f['turn']['id'] == str(said.id))
        self.assertIs(turn['full_tools'], False)


class TurnBundleTest(TestCase):
    """Which instructions a turn was given, as its wake's turn line names them, with the bundle's checksum and
    whether it was new: beside the 🔥/🧊 cache mark in its heading (Justin, magent-self, 25 Oct)."""

    setUp, say, wake = TurnToolsTest.setUp, TurnToolsTest.say, TurnToolsTest.wake

    def record(self, rid, sha, minutes, agent='magent', mood='connectome'):
        from conversations.models import BundleRecord
        return BundleRecord.objects.create(id=rid, agent=agent, mood=mood, sha256=sha * 64,
                                           requested_at=T0 + timedelta(minutes=minutes), parts=['x'])

    def test_a_turn_knows_its_bundle_and_whether_it_was_new(self):
        from conversations.services.mood_view import bundle_marks, turn_wakes
        first_id, second_id, third_id = (str(uuid.uuid4()) for _ in range(3))
        self.record(first_id, 'a', 0)
        self.record(second_id, 'a', 10)
        self.record(third_id, 'b', 20)
        self.record(str(uuid.uuid4()), 'c', 15, agent='re')  # another emergent's: not what magent had before
        line = "\n\nIt's block 26,271,000. Your instructions this turn are bundle {} (Special:AgentBundles)."
        self.wake('full', 0, quoting=line.format(first_id))
        one = self.say(self.magent, 'First.', 1)
        self.wake('look', 10, quoting=line.format(second_id))
        two = self.say(self.magent, 'Same instructions.', 11)
        self.wake('full', 20, quoting=line.format(third_id))
        three = self.say(self.magent, 'New ones.', 21)
        wakes = turn_wakes([one, two, three])
        self.assertEqual([wakes[str(m.id)]['bundle'] for m in (one, two, three)], [first_id, second_id, third_id])
        self.assertEqual(wakes[str(two.id)]['tools'], 'look')
        marks = bundle_marks([w['bundle'] for w in wakes.values()])
        self.assertEqual(marks[first_id], {'id': first_id, 'sha': 'aaaaaaaa', 'new': False})  # nothing before it
        self.assertEqual(marks[second_id]['new'], False)
        self.assertEqual(marks[third_id], {'id': third_id, 'sha': 'bbbbbbbb', 'new': True})

    def test_a_wake_without_a_bundle_still_marks_its_tools(self):
        from conversations.services.mood_view import bundle_marks, turn_wakes
        self.wake('full', 0)
        said = self.say(self.magent, 'Before bundles.', 1)
        self.assertEqual(turn_wakes([said]), {str(said.id): {'tools': 'full', 'bundle': ''}})
        self.assertEqual(bundle_marks(['']), {})
