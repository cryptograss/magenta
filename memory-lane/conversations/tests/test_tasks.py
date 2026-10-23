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

MONDAY = datetime(2026, 10, 26, 9, 0, tzinfo=timezone.utc)  # a Monday


def wiki(pages):
    """A get() like WikiTexts'; pages: {title: [(revision, editor, wikitext)], newest first}."""
    return lambda title: pages.get(title, [])


class TaskPageTest(TestCase):

    def test_the_template_holds_its_settings_and_the_rest_is_the_prompt(self):
        task = tasks.parse('blu', 'Connect', 41, 'JMyles', CONNECT)
        self.assertEqual((task.mood, task.schedule, task.cap, task.tools), ('connectome', 'mondays 09:00', 2.0, 'full'))
        self.assertEqual(task.title, 'Cryptograss:Agents/Blu/Tasks/Connect')
        self.assertTrue(task.prompt.startswith('Connect these pickers'))
        self.assertNotIn('for people', task.prompt)  # comments aren't sent
        self.assertEqual(task.blanks, ['names'])
        self.assertEqual(tasks.parse('blu', 'X', 1, 'JMyles', 'Just this.').tools, 'look')

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
        self.assertEqual(tasks.last_slot('mondays 09:00', MONDAY + timedelta(minutes=5)), MONDAY)
        self.assertEqual(tasks.last_slot('mondays 09:00', MONDAY - timedelta(minutes=1)), MONDAY - timedelta(days=7))
        self.assertEqual(tasks.last_slot('mondays 09:00, daily 18:30', MONDAY + timedelta(hours=10)),
                         MONDAY.replace(hour=18, minute=30))
        self.assertEqual(tasks.last_slot('weekdays 08:00', MONDAY - timedelta(hours=2)),  # Sunday 07:00
                         MONDAY - timedelta(days=3, hours=1))  # Friday 08:00
        for bad in ('someday 09:00', 'mondays 9am', 'daily 25:00'):
            with self.assertRaises(tasks.Refused):
                tasks.slots(bad)


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
