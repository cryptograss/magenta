"""A Mood's own container on hunter: asked for from the Mood, read and reported by hunter (services/containers.py)."""

import json
from unittest import skipUnless

from django.test import TestCase, override_settings

from conversations.models import Mood, MoodAlias, MoodContainer, Setting
from conversations.tests.test_mood_auth import HAS_SSH_KEYGEN
from conversations.tests.test_mood_devices import SignedInCase

KEY = 'b' * 64
AUTH = {'HTTP_AUTHORIZATION': f'Bearer {KEY}'}


@skipUnless(HAS_SSH_KEYGEN, 'needs ssh-keygen')
@override_settings(MOOD_ACCESS_KEY=KEY)
class MoodContainersTest(SignedInCase):

    def post(self, client, url, body):
        return client.post(url, json.dumps(body), content_type='application/json',
                           HTTP_X_CSRFTOKEN=client.cookies['csrftoken'].value)

    def report(self, mood_id, body, auth=AUTH):
        return self.client.post(f'/api/containers/{mood_id}/', json.dumps(body), content_type='application/json', **auth)

    def listed(self):
        return {m['slug']: m.get('container') for m in self.client.get('/api/moods/').json()['moods']}

    def test_asked_for_from_the_mood_then_up_then_let_go(self):
        m26 = Mood.objects.get(slug='m26')
        thread = Mood.objects.create(slug='m26-thread', title='a thread', thread_of=m26)
        deeper = Mood.objects.create(slug='m26-deeper', title='deeper', thread_of=thread)
        MoodAlias.objects.create(slug='m25', mood=m26)
        # Only someone signed in with a key can ask.
        self.assertEqual(self.client.post('/api/moods/m26/container/', '{}', content_type='application/json').status_code, 401)
        client = self.sign_in()
        self.assertEqual(self.post(client, '/api/moods/m26/container/', {'container': 'yes'}).status_code, 400)
        # A thread shares its Mood's: it can't ask for its own.
        self.assertEqual(self.post(client, '/api/moods/m26-thread/container/', {'container': True}).status_code, 400)
        self.assertEqual(self.post(client, '/api/moods/m26/container/', {'container': True}).json(),
                         {'slug': 'm26', 'container': True})
        # Asked for, not up: the list says so, for the Mood and its threads, any depth.
        listed = self.listed()
        self.assertEqual((listed['m26']['state'], listed['m26']['name']), ('asked', f'mood-{m26.pk}'))
        self.assertEqual(listed['m26-deeper']['shared_from'], 'm26')
        # hunter reads what's wanted, with what it needs to name and route it.
        self.assertEqual(self.client.get('/api/containers/').status_code, 401)
        wanted = self.client.get('/api/containers/', **AUTH).json()['moods']
        self.assertEqual(len(wanted), 1)
        self.assertEqual({k: wanted[0][k] for k in ('id', 'slug', 'aliases', 'threads', 'wanted', 'asked_by')},
                         {'id': m26.pk, 'slug': 'm26', 'aliases': ['m25'], 'threads': ['m26-deeper', 'm26-thread'],
                          'wanted': True, 'asked_by': 'justin'})
        # ...and says how it stands.
        self.assertEqual(self.report(m26.pk, {'state': 'running'}, auth={}).status_code, 401)
        self.assertEqual(self.report(m26.pk, {'state': 'dancing'}).status_code, 400)
        self.assertEqual(self.report(m26.pk, {'state': 'running', 'hosts': ['bad host!']}).status_code, 400)
        self.assertEqual(self.report(99999, {'state': 'running'}).status_code, 404)
        self.assertEqual(self.report(m26.pk, {'state': 'running', 'hosts': ['m260.hunter.cryptograss.live'],
                                              'ssh_port': 2240}).status_code, 200)
        listed = self.listed()
        self.assertEqual((listed['m26']['state'], listed['m26']['hosts'], listed['m26']['ssh_port']),
                         ('running', ['m260.hunter.cryptograss.live'], 2240))
        self.assertEqual(listed['m26-thread']['state'], 'running')
        # Let go: still listed for hunter, as unwanted, until it says it's stopped; then gone from both.
        self.post(client, '/api/moods/m26/container/', {'container': False})
        self.assertEqual([(w['slug'], w['wanted']) for w in self.client.get('/api/containers/', **AUTH).json()['moods']],
                         [('m26', False)])
        self.report(m26.pk, {'state': 'stopped'})
        self.assertEqual(self.client.get('/api/containers/', **AUTH).json()['moods'], [])
        self.assertIsNone(self.listed()['m26'])
        # Who asked, and when: kept, like every setting.
        self.assertEqual([r.value for r in Setting.objects.filter(key='container').order_by('created_at')], [True, False])

    def test_an_archived_mood_is_not_wanted(self):
        m26 = Mood.objects.get(slug='m26')
        client = self.sign_in()
        self.post(client, '/api/moods/m26/container/', {'container': True})
        self.post(client, '/api/moods/m26/archive/', {'archived': True})
        self.assertEqual([(w['id'], w['wanted']) for w in self.client.get('/api/containers/', **AUTH).json()['moods']],
                         [(m26.pk, False)])
        self.assertFalse(MoodContainer.objects.exists())

    def test_once_up_the_mood_and_its_threads_say_which_container_answers_them(self):
        m26 = Mood.objects.get(slug='m26')
        Mood.objects.create(slug='m26-thread', title='a thread', thread_of=m26)
        client = self.sign_in()
        self.post(client, '/api/moods/m26/container/', {'container': True})
        said = lambda slug: self.client.get(f'/api/moods/{slug}/turns/').json()['mood'].get('container')
        self.assertIsNone(said('m26'))  # asked for, not up: the poller everywhere else still answers
        self.report(m26.pk, {'state': 'running'})
        self.assertEqual((said('m26'), said('m26-thread')), (f'mood-{m26.pk}', f'mood-{m26.pk}'))


class PollerContainersTest(TestCase):
    """Which poller answers a Mood: a Mood's container its own and its threads'; magent's everywhere else."""

    def poller(self, **kwargs):
        from poller.mood_poller import MoodPoller
        p = MoodPoller.__new__(MoodPoller)
        p.moods, p.known_as = None, {}
        p.container, p.leave_containers = kwargs.get('container'), kwargs.get('leave_containers', False)
        answered = {'m26': 'mood-7', 'm26-thread': 'mood-7', 'general': None}
        p.container_of = lambda slug: answered.get(slug)
        return p

    def test_each_mood_has_one_answerer(self):
        box, everywhere, agent = self.poller(container='mood-7'), self.poller(leave_containers=True), self.poller()
        for slug in ('m26', 'm26-thread', 'general'):
            with self.subTest(slug=slug):
                self.assertEqual(box.mine(slug), slug != 'general')
                self.assertEqual(everywhere.mine(slug), slug == 'general')
                self.assertTrue(agent.mine(slug))  # re and blu answer wherever they are asked
