"""At a glance (services/glance.py; R.J., 13 Nov): who's on what, what needs people, what each person means to do."""

import json
import time
import uuid
from datetime import timedelta

from django.core.cache import cache
from django.test import Client, TestCase
from django.utils import timezone

from conversations.models import Message, Mood, Setting, ThinkingEntity
from conversations.services import glance, mood_auth, todo
from conversations.services import settings as knobs


class GlanceTest(TestCase):

    def setUp(self):
        cache.clear()
        self.people = {n: ThinkingEntity.objects.create(name=n, is_biological_human=True)
                       for n in ('justin', 'skyler', 'rj')}
        ThinkingEntity.objects.get_or_create(name='magent', defaults={'is_biological_human': False})
        ThinkingEntity.objects.get_or_create(name='blu', defaults={'is_biological_human': False})
        self.connectome = Mood.objects.create(slug='connectome', title='connectome')
        self.interfaces = Mood.objects.create(slug='interfaces', title='interfaces')
        self.thread = Mood.objects.create(slug='at-a-glance', title='at-a-glance', thread_of=self.interfaces)
        self.old = Mood.objects.create(slug='old', title='old')

    def say(self, who, mood, text='words', ago_blocks=10):
        at = timezone.now() - timedelta(seconds=ago_blocks * glance.BLOCK_SECONDS)
        m = Message.objects.create(id=uuid.uuid4(), sender_id=who, mood=mood, source_file='mood-web',
                                   content=text, timestamp=int(time.time() * 1000))
        Message.objects.filter(id=m.id).update(created_at=at)
        return m

    def look(self, window='day', items=(), asks=None):
        return glance.glance(window, now=timezone.now(), todos={'items': list(items)}, asks=asks or {})

    def test_who_said_how_much_where_most_talked_of_first(self):
        for _ in range(3):
            self.say('justin', self.connectome)
        self.say('blu', self.connectome)
        self.say('skyler', self.interfaces)
        self.say('justin', self.old, ago_blocks=20000)        # before the day, inside the week
        d = self.look()
        self.assertEqual([p['slug'] for p in d['projects']], ['connectome', 'interfaces'])
        self.assertEqual(d['projects'][0]['said'], [{'name': 'justin', 'n': 3}, {'name': 'blu', 'n': 1}])
        self.assertEqual(d['projects'][0]['last_by'], 'blu')
        self.assertEqual([(w['name'], w['human'], w['n']) for w in d['people']],
                         [('justin', True, 3), ('blu', False, 1), ('skyler', True, 1)])
        self.assertEqual(d['people'][0]['moods'], [{'slug': 'connectome', 'n': 3}])
        self.assertEqual(d['blocks'], 7200)
        week = self.look('week')
        self.assertEqual([p['slug'] for p in week['projects']], ['connectome', 'interfaces', 'old'])
        self.assertEqual(week['blocks'], 50400)

    def test_a_threads_talk_counts_with_its_mood(self):
        self.say('rj', self.thread)
        self.say('magent', self.thread)
        self.say('justin', self.interfaces)
        (p,) = self.look()['projects']
        self.assertEqual((p['slug'], p['total'], p['threads']), ('interfaces', 3, 1))

    def test_not_a_silence_a_thought_or_the_pollers_own(self):
        self.say('magent', self.connectome, '<silent>nothing to add</silent>')
        ThinkingEntity.objects.get_or_create(name='mood-poller', defaults={'is_biological_human': False})
        self.say('mood-poller', self.connectome, 'a wake')
        self.assertEqual(self.look()['projects'], [])

    def test_an_archived_mood_is_left_out(self):
        self.say('justin', self.old)
        knobs.change('archived', True, mood=self.old)
        self.assertEqual(self.look()['projects'], [])

    def test_needs_asks_first_then_priority_then_what_holds_others_up(self):
        items = [
            {'task': 'write the song', 'who': ['skyler', 'magent'], 'kind': 'music', 'type': 'make', 'mood': 'connectome'},
            {'task': 'merge the graph', 'who': ['jMyles'], 'kind': 'merge', 'type': 'merge', 'mood': 'connectome',
             'link': '/ramp/pickipedia/blu/graph/'},
            {'task': 'name the pane', 'who': ['rj'], 'kind': 'decide', 'type': 'decide', 'mood': 'interfaces',
             'priority': 2},
            {'task': 'done already', 'who': ['justin'], 'kind': 'merge', 'type': 'merge', 'mood': 'connectome',
             'done': True},
            {'task': "blu's own sweep", 'who': ['blu'], 'kind': 'work', 'type': 'work', 'mood': 'connectome'},
            {'task': 'tidy a branch', 'who': ['magent'], 'kind': 'review', 'type': 'review', 'mood': 'connectome'},
            {'task': 'in a closed room', 'who': ['justin'], 'kind': 'merge', 'type': 'merge', 'mood': 'old',
             'archived': True},
        ]
        asks = {'interfaces': [{'kind': 'merge', 'emergent': 'magent', 'for': 'magenta:magent/at-a-glance',
                                'id': 'abc', 'at': ''}]}
        self.say('justin', self.connectome)
        d = self.look(items=items, asks=asks)
        self.assertEqual([n['task'] for n in d['needs']],
                         ['magent asks for a merge: magenta:magent/at-a-glance', 'name the pane', 'merge the graph',
                          'write the song'])
        self.assertEqual(d['needs_total'], 4)
        ask, decide, merge, _ = d['needs']
        self.assertEqual((ask['ask'], ask['who'], ask['link']), (True, [], '/moods/interfaces/#m-abc'))
        self.assertEqual((decide['priority'], decide['who']), (2, ['rj']))
        self.assertEqual(merge['who'], ['justin'])            # jMyles, as he's called here
        (p,) = d['projects']
        self.assertEqual((p['needs'], p['waits_on']), (2, [{'name': 'justin', 'n': 1}, {'name': 'skyler', 'n': 1}]))
        # How many wait on each person ('' is nobody named; never an emergent named beside one), and each one's own
        self.assertEqual(d['needs_by'], [{'name': '', 'n': 1}, {'name': 'justin', 'n': 1}, {'name': 'rj', 'n': 1},
                                         {'name': 'skyler', 'n': 1}])
        self.assertEqual([n['task'] for n in d['needs_for']['rj']], ['name the pane'])
        self.assertEqual([n['ask'] for n in d['needs_for']['']], [True])

    def test_each_persons_own_few_are_sent_whatever_the_top_holds(self):
        items = [{'task': f'merge {n}', 'who': ['justin'], 'kind': 'merge', 'type': 'merge', 'mood': 'connectome'}
                 for n in range(20)]
        items.append({'task': 'chart a song', 'who': ['skyler'], 'kind': 'music', 'type': 'make', 'mood': 'connectome'})
        d = self.look(items=items)
        self.assertEqual((len(d['needs']), d['needs_total']), (glance.NEEDS_TOP, 21))
        self.assertNotIn('chart a song', [n['task'] for n in d['needs']])
        self.assertEqual([n['task'] for n in d['needs_for']['skyler']], ['chart a song'])
        self.assertEqual(len(d['needs_for']['justin']), glance.NEEDS_EACH)
        self.assertEqual(d['needs_by'][0], {'name': 'justin', 'n': 20})

    def test_a_todo_item_may_say_its_priority(self):
        items = todo.parse('<todo>\n- task: first\n  priority: 1\n- task: none said\n- task: not one\n  priority: 9\n'
                           '- task: nor this\n  priority: yes\n</todo>')
        self.assertEqual([i.get('priority') for i in items], [1, None, None, None])
        self.assertNotIn('priority', items[1])

    def test_intentions_the_newest_from_each_while_its_in_the_window(self):
        glance.intend(self.people['justin'], 'a graph on MusicianInfo')
        glance.intend(self.people['justin'], '* a graph on MusicianInfo\n* the copyright essay\n')
        glance.intend(self.people['skyler'], 'chart the first song')
        glance.intend(self.people['skyler'], '')              # cleared
        row = glance.intend(self.people['rj'], 'look around')
        Setting.objects.filter(id=row.id).update(created_at=timezone.now() - timedelta(days=3))
        d = self.look()
        self.assertEqual([(i['name'], i['text']) for i in d['intentions']],
                         [('justin', 'a graph on MusicianInfo\nthe copyright essay')])
        self.assertEqual([i['name'] for i in self.look('week')['intentions']], ['justin', 'rj'])
        self.assertEqual(Setting.objects.filter(key='intention', emergent_id='justin').count(), 2)  # history stays

    def test_an_intention_too_long_is_refused(self):
        with self.assertRaises(knobs.Invalid):
            glance.intend(self.people['rj'], 'x' * 401)
        with self.assertRaises(knobs.Invalid):
            glance.intend(self.people['rj'], '\n'.join('line' for _ in range(7)))


class GlanceEndpointsTest(TestCase):

    def setUp(self):
        cache.clear()
        self.rj = ThinkingEntity.objects.create(name='rj', is_biological_human=True)
        Mood.objects.create(slug='general', title='general')
        cache.set('todo:all_moods', {'items': []}, 60)        # PickiPedia isn't asked

    def signed_in(self, entity):
        client = Client()
        _, token = mood_auth.enrol_device(entity, 'phone', tier='wiki')
        client.cookies[mood_auth.COOKIE] = token
        return client

    def test_the_pane_reads_without_a_sign_in(self):
        d = Client().get('/api/glance/?window=week').json()
        self.assertEqual((d['window'], d['projects'], d['needs'], d['intentions']), ('week', [], [], []))
        self.assertEqual(Client().get('/api/glance/?window=fortnight').json()['window'], 'day')

    def test_saying_what_you_mean_to_do_takes_a_sign_in_and_is_yours(self):
        post = lambda c, body: c.post('/api/glance/intention/', json.dumps(body), content_type='application/json')
        self.assertEqual(post(Client(), {'text': 'x'}).status_code, 401)
        r = post(self.signed_in(self.rj), {'text': 'dig in and see what everyone is on'})
        self.assertEqual((r.status_code, r.json()['name']), (200, 'rj'))
        self.assertEqual(Client().get('/api/glance/').json()['intentions'][0]['text'], 'dig in and see what everyone is on')
        self.assertEqual(post(self.signed_in(self.rj), {'text': 'x' * 500}).status_code, 400)
        self.assertEqual(post(self.signed_in(self.rj), {'text': ''}).status_code, 200)
        self.assertEqual(Client().get('/api/glance/').json()['intentions'], [])

    def test_the_moods_page_carries_the_pane(self):
        """Its leaf beside the to-do list, opening first in #general (Justin, 13 Nov: "instead of 'todo'")."""
        html = Client().get('/moods/general/').content.decode()
        for part in ('id="glanceTab"', 'id="glanceBody"', 'id="glanceIntend"', "const GLANCE_FIRST = ['general']",
                     "fetch('/api/glance/?window='"):
            self.assertIn(part, html)
