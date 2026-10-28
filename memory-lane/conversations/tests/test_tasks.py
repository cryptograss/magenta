"""An agent's task: a PickiPedia page, started by its schedule or a person's call, as a line in its
Mood that wakes the agent there -- or says, waking nobody, why it didn't start."""

import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from django.test import TestCase

from conversations.models import ConversationParticipant, Message, Mood, ThinkingEntity
from conversations.services import mood_auth, tasks

CONNECT = """{{Agent task|mood=connectome|schedule=mondays 09:00|cap=2|tools=full}}
<!-- for people: what this task is for -->
Connect these pickers to the people they have played with: {names}.
Link each connection to its source."""

WEEKLY = """{{Agent task|mood=connectome|schedule=mondays 09:00, daily 18:30|cap=2}}
Look over the week's new pages for pickers with no connections."""

JOURNAL = """{{Agent task|mood=connectome|schedule=every 3600 blocks|cap=1|tools=full|shared=Cryptograss:Emergents/Tasks/Journal}}
<!-- for people: only what this agent needs beyond the shared text -->
Note anything only blu would need."""

JOURNAL_SHARED = """Write the next entry in your journal.
<!-- for people: why this text is shared -->
Read the record since your last entry."""

MONDAY = datetime(2026, 10, 26, 9, 0, tzinfo=timezone.utc)  # a Monday


def wiki(pages):
    """A get() like WikiTexts'; pages: {title: [(revision, editor, wikitext)], newest first}."""
    return lambda title: pages.get(title, [])


class TaskPageTest(TestCase):

    def test_the_template_holds_its_settings_and_the_rest_is_the_prompt(self):
        task = tasks.parse('blu', 'Connect', 41, 'JMyles', CONNECT)
        self.assertEqual((task.mood, task.schedule, task.cap, task.tools), ('connectome', 'mondays 09:00', 2.0, 'full'))
        self.assertEqual(task.title, 'Cryptograss:Emergents/Blu/Tasks/Connect')
        self.assertTrue(task.prompt.startswith('Connect these pickers'))
        self.assertNotIn('for people', task.prompt)  # comments aren't sent
        self.assertEqual(task.blanks, ['names'])
        self.assertEqual(tasks.parse('blu', 'X', 1, 'JMyles', 'Just this.').tools, 'look')
        renamed = tasks.parse('blu', 'Connect', 42, 'JMyles', CONNECT.replace('{{Agent task|', '{{Emergent task|'))
        self.assertEqual((renamed.mood, renamed.schedule, renamed.cap, renamed.tools),
                         ('connectome', 'mondays 09:00', 2.0, 'full'))

    def test_only_a_trusted_revision_counts(self):
        pages = {tasks.title('blu', 'Connect'): [(43, 'Blu', CONNECT.replace('tools=full', 'tools=full|cap=500')),
                                                 (41, 'JMyles', CONNECT)]}
        self.assertEqual(tasks.read('blu', 'Connect', wiki(pages)).revision, 41)
        with self.assertRaisesRegex(tasks.Refused, 'no revision by a trusted editor'):
            tasks.read('blu', 'Connect', wiki({tasks.title('blu', 'Connect'): [(43, 'Blu', CONNECT)]}))
        with self.assertRaisesRegex(tasks.Refused, 'there is no page'):
            tasks.read('blu', 'Connect', wiki({}))
        with self.assertRaisesRegex(tasks.Refused, "isn't a task name"):
            tasks.read('blu', '../Rules', wiki({}))

    def test_shared_instructions_are_read_once_and_the_pages_own_text_follows(self):
        pages = {tasks.title('blu', 'Journal'): [(51, 'JMyles', JOURNAL)],
                 'Cryptograss:Emergents/Tasks/Journal': [(60, 'JMyles', JOURNAL_SHARED)]}
        task = tasks.read('blu', 'Journal', wiki(pages))
        self.assertEqual(task.shared, 'Cryptograss:Emergents/Tasks/Journal')
        self.assertEqual(task.shared_revision, 60)
        self.assertEqual(task.prompt, 'Write the next entry in your journal.\n\nRead the record since your last entry.'
                                      '\n\nNote anything only blu would need.')
        self.assertNotIn('for people', task.prompt)  # comments on either page aren't sent

    def test_a_shared_task_with_nothing_of_its_own_is_all_shared(self):
        bare = '{{Agent task|mood=connectome|shared=Cryptograss:Emergents/Tasks/Journal}}'
        pages = {tasks.title('blu', 'Journal'): [(51, 'JMyles', bare)],
                 'Cryptograss:Emergents/Tasks/Journal': [(60, 'JMyles', JOURNAL_SHARED)]}
        task = tasks.read('blu', 'Journal', wiki(pages))
        self.assertEqual(task.prompt, 'Write the next entry in your journal.\n\nRead the record since your last entry.')
        self.assertEqual(task.blanks, [])

    def test_a_blank_in_the_shared_text_is_still_a_blank(self):
        pages = {tasks.title('blu', 'Journal'): [(51, 'JMyles', JOURNAL)],
                 'Cryptograss:Emergents/Tasks/Journal': [(60, 'JMyles', JOURNAL_SHARED + '\nCover {since}.')]}
        self.assertEqual(tasks.read('blu', 'Journal', wiki(pages)).blanks, ['since'])

    def test_a_shared_page_needs_a_trusted_revision_and_to_exist(self):
        page = tasks.title('blu', 'Journal')
        with self.assertRaisesRegex(tasks.Refused, 'no revision by a trusted editor'):
            tasks.read('blu', 'Journal', wiki({page: [(51, 'JMyles', JOURNAL)],
                                              'Cryptograss:Emergents/Tasks/Journal': [(60, 'Blu', JOURNAL_SHARED)]}))
        with self.assertRaisesRegex(tasks.Refused, 'there is no page Cryptograss:Emergents/Tasks/Journal'):
            tasks.read('blu', 'Journal', wiki({page: [(51, 'JMyles', JOURNAL)]}))

    def test_blanks_are_filled_and_none_left_or_made_up(self):
        task = tasks.parse('blu', 'Connect', 41, 'JMyles', CONNECT)
        self.assertIn('pickers to the people they have played with: Peter Rowan, 4masks.',
                      tasks.fill(task, {'names': ['Peter Rowan', '4masks']}))
        with self.assertRaisesRegex(tasks.Refused, r'\{names\} need filling'):
            tasks.fill(task, {})
        with self.assertRaisesRegex(tasks.Refused, 'no blank for mood_of_day'):
            tasks.fill(task, {'names': 'x', 'mood_of_day': 'y'})
        # A template's braces are wikitext, not blanks.
        self.assertEqual(tasks.parse('blu', 'X', 1, 'JMyles', 'See {{Src|a}} and {x}.').blanks, ['x'])

    def test_schedules(self):
        at = lambda schedule, now: tasks.last_slot(schedule, now)[1]
        self.assertEqual(at('mondays 09:00', MONDAY + timedelta(minutes=5)), MONDAY)
        self.assertEqual(tasks.last_slot('mondays 09:00', MONDAY)[0], MONDAY.isoformat())
        self.assertEqual(at('mondays 09:00', MONDAY - timedelta(minutes=1)), MONDAY - timedelta(days=7))
        self.assertEqual(at('mondays 09:00, daily 18:30', MONDAY + timedelta(hours=10)),
                         MONDAY.replace(hour=18, minute=30))
        self.assertEqual(at('weekdays 08:00', MONDAY - timedelta(hours=2)),  # Sunday 07:00
                         MONDAY - timedelta(days=3, hours=1))  # Friday 08:00
        for bad in ('someday 09:00', 'mondays 9am', 'daily 25:00', 'every 3 blocks', 'every blocks'):
            with self.assertRaises(tasks.Refused):
                tasks.slots(bad)

    def test_every_n_blocks(self):
        # At height 24_007_210 the last multiple of 7200 is 24_004_800: 2410 blocks, 8h02m ago.
        key, when = tasks.last_slot('every 7_200 blocks', MONDAY, block=24_007_210)
        self.assertEqual(key, 'block 24004800')
        self.assertEqual(when, MONDAY - timedelta(seconds=2410 * 12))
        self.assertEqual(tasks.last_slot('every 7200 blocks', MONDAY, block=24_004_810)[0], 'block 24004800')
        # Mixed with a time, the newer slot wins.
        self.assertEqual(tasks.last_slot('every 7200 blocks, mondays 09:00', MONDAY, block=24_007_210)[0],
                         MONDAY.isoformat())
        with self.assertRaisesRegex(tasks.Refused, 'block clock here is more than an hour stale'):
            tasks.last_slot('every 7200 blocks', MONDAY)

    def test_the_height_now_from_the_newest_anchor(self):
        ts = int(MONDAY.timestamp())
        anchors = [(24_000_000, ts - 600), (24_000_025, ts - 300)]
        self.assertEqual(tasks.head(MONDAY, anchors), 24_000_050)  # five minutes of 12-second slots on
        self.assertIsNone(tasks.head(MONDAY + timedelta(hours=2), anchors))  # the anchors stopped
        self.assertIsNone(tasks.head(MONDAY, []))


class StartingTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.justin, _ = ThinkingEntity.objects.update_or_create(name='justin', defaults={'is_biological_human': True})
        ThinkingEntity.objects.update_or_create(name='blu', defaults={'is_biological_human': False})
        ThinkingEntity.objects.update_or_create(name='magent', defaults={'is_biological_human': False})
        cls.mood = Mood.objects.create(slug='connectome', title='connectome')
        cls.pages = {tasks.title('blu', 'Connect'): [(41, 'JMyles', CONNECT)],
                     tasks.title('blu', 'Weekly'): [(42, 'JMyles', WEEKLY)],
                     tasks.title('blu', 'Proposed'): [(44, 'Blu', WEEKLY)]}

    def task(self, name='Connect'):
        return tasks.read('blu', name, wiki(self.pages))

    def mentions(self, name='blu'):
        return self.client.get(f'/api/mentions/{name}/').json()['mentions']

    def test_started_it_wakes_the_agent_with_the_filled_prompt_and_the_pages_tools(self):
        line = tasks.start(self.task(), 'justin', params={'names': ['Peter Rowan']})
        self.assertEqual((line.content['state'], line.mood_slug, line.source_file), ('asked', 'connectome', 'task'))
        [m] = self.mentions()
        turn = m['turn']
        self.assertEqual((m['mood'], turn['via'], turn['sender'], turn['is_human'], turn['full_tools']),
                         ('connectome', 'task', 'task', False, True))
        self.assertIn("your task Connect, at justin's call", turn['text'])
        self.assertIn('played with: Peter Rowan.', turn['text'])
        self.assertEqual(self.mentions('magent'), [])  # it's blu's, nobody else's

    def test_shared_instructions_are_named_in_the_wake_and_the_record(self):
        self.pages[tasks.title('blu', 'Journal')] = [(51, 'JMyles', JOURNAL)]
        self.pages['Cryptograss:Emergents/Tasks/Journal'] = [(60, 'JMyles', JOURNAL_SHARED)]
        line = tasks.start(tasks.read('blu', 'Journal', wiki(self.pages)), 'justin')
        self.assertEqual(line.content['shared'],
                         {'page': 'Cryptograss:Emergents/Tasks/Journal', 'revision': 60})
        [m] = self.mentions()
        self.assertIn('instructions from Cryptograss:Emergents/Tasks/Journal revision 60', m['turn']['text'])
        self.assertIn('Read the record since your last entry.', m['turn']['text'])
        self.assertIn('Note anything only blu would need.', m['turn']['text'])

    def test_refused_it_says_why_and_wakes_nobody(self):
        line = tasks.start(self.task(), 'justin', params={})
        self.assertEqual(line.content['state'], 'refused')
        self.assertIn('{names} need filling', line.content['reason'])
        self.assertEqual(self.mentions(), [])
        events = self.client.get('/api/moods/connectome/turns/').json()['events']
        self.assertEqual([(e['type'], e['state'], e['task']) for e in events], [('task', 'refused', 'Connect')])

    def test_over_its_cap_it_doesnt_start(self):
        system, _ = ConversationParticipant.objects.get_or_create(name='system', defaults={'participant_type': 'system'})
        for cost in (1.25, 0.80):
            Message.objects.create(id=uuid.uuid4(), sender=system, mood=self.mood, source_file='ingest-mood-runner',
                                   content={'type': 'turn-result', 'agent': 'blu', 'cost_usd': cost})
        line = tasks.start(self.task(), 'justin', params={'names': 'x'})
        self.assertEqual(line.content['state'], 'refused')
        self.assertIn('has spent $2.05 here in the past day; its cap is $2.00', line.content['reason'])

    def test_no_mood_nowhere_to_say_it(self):
        task = tasks.parse('blu', 'X', 1, 'JMyles', 'Do this.')
        with self.assertRaisesRegex(tasks.Refused, 'names no mood'):
            tasks.start(task, 'justin')
        with self.assertRaisesRegex(tasks.Refused, "no Mood 'nowhere'"):
            tasks.start(task, 'justin', mood='nowhere')

    def test_its_schedule_starts_it_once_a_slot_and_lets_a_long_missed_one_go(self):
        search = lambda prefix: [t for t in self.pages if t.startswith(prefix)]
        get = wiki(self.pages)
        done = tasks.due(['blu'], now=MONDAY + timedelta(minutes=1), get=get, search=search)
        started = [(name, what.content['state']) for _, name, what in done if not isinstance(what, str)]
        # Connect has a blank, so on its own schedule it can't start: it says so.
        self.assertEqual(sorted(started), [('Connect', 'refused'), ('Weekly', 'asked')])
        self.assertIn(('blu', 'Proposed', f"{tasks.title('blu', 'Proposed')} has no revision by a trusted editor "
                                          "(JMyles, SkymanJenkins) yet"), done)
        again = tasks.due(['blu'], now=MONDAY + timedelta(minutes=2), get=get, search=search)
        self.assertEqual([d for d in again if not isinstance(d[2], str)], [])  # once a slot
        late = tasks.due(['blu'], now=MONDAY.replace(hour=20), get=get, search=search)  # 18:30's slot, 90 min on
        self.assertEqual([d for d in late if not isinstance(d[2], str)], [])
        weekly = Message.objects.filter(source_file='task', content__task='Weekly').get()
        self.assertEqual((weekly.content['by'], weekly.content['slot']), ('schedule', MONDAY.isoformat()))
        self.assertIn('on its schedule', tasks.mention(weekly, 'blu')['text'])
        self.assertFalse(tasks.mention(weekly, 'blu')['full_tools'])  # its page says nothing: look-only

    def test_a_schedule_in_blocks_runs_once_a_slot_and_says_so_when_the_clock_is_stale(self):
        pages = {tasks.title('blu', 'Daily'): [(45, 'JMyles', WEEKLY.replace('mondays 09:00, daily 18:30',
                                                                                'every 7200 blocks'))]}
        search = lambda prefix: list(pages)
        ts = int(MONDAY.timestamp())
        anchors = [(24_004_790, ts - 300)]  # the head now: 24_004_815, 15 blocks past a slot
        [(_, _, line)] = tasks.due(['blu'], now=MONDAY, get=wiki(pages), search=search, anchors=anchors)
        self.assertEqual((line.content['state'], line.content['slot']), ('asked', 'block 24004800'))
        self.assertEqual(tasks.due(['blu'], now=MONDAY + timedelta(minutes=1), get=wiki(pages), search=search,
                                   anchors=anchors), [])
        [(_, _, why)] = tasks.due(['blu'], now=MONDAY + timedelta(hours=3), get=wiki(pages), search=search,
                                  anchors=anchors)
        self.assertIn('block clock here is more than an hour stale', why)


class TaskApiTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.justin, _ = ThinkingEntity.objects.update_or_create(name='justin', defaults={'is_biological_human': True})
        ThinkingEntity.objects.update_or_create(name='blu', defaults={'is_biological_human': False})
        Mood.objects.create(slug='connectome', title='connectome')
        Mood.objects.create(slug='blu-practice', title='blu-practice')

    def call(self, body, tier='key', agent='blu', task='Connect'):
        _, token = mood_auth.enrol_device(self.justin, 'laptop', tier=tier)
        self.client.cookies[mood_auth.COOKIE] = token
        pages = {tasks.title('blu', 'Connect'): [(41, 'JMyles', CONNECT)]}
        with patch.object(tasks, 'history', wiki(pages)):
            return self.client.post(f'/api/agents/{agent}/tasks/{task}/', json.dumps(body),
                                    content_type='application/json')

    def test_a_person_with_their_key_starts_it(self):
        r = self.call({'params': {'names': ['Peter Rowan']}, 'mood': 'blu-practice'})
        self.assertEqual(r.status_code, 201)
        self.assertEqual((r.json()['state'], r.json()['mood'], r.json()['revision']), ('asked', 'blu-practice', 41))
        self.assertEqual(Message.objects.get(source_file='task').content['by'], 'justin')

    def test_refused_there_is_still_a_line_and_said_back(self):
        r = self.call({'params': {}})
        self.assertEqual((r.status_code, r.json()['state']), (201, 'refused'))
        self.assertIn('need filling', r.json()['reason'])

    def test_not_with_a_pickipedia_sign_in_nor_for_a_person_nor_an_unknown_task(self):
        self.assertEqual(self.call({'params': {'names': 'x'}}, tier='wiki').status_code, 403)
        self.assertEqual(self.call({'params': {'names': 'x'}}, agent='justin').status_code, 400)
        self.assertIn('there is no page', self.call({}, task='Nothing').json()['error'])
        self.assertEqual(Message.objects.filter(source_file='task').count(), 0)
