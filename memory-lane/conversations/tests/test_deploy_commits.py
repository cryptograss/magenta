"""Deployed is a commit (Cryptograss:Moods/ramp/Plan, section 4). A deploy's report says what it put on the server
({"magenta": "<sha>"}, from maybelle-config's report-deploy.sh), and the ramp shows a merge as live there once it's in
that commit: magenta on hunter, which nothing said before (Justin, #ramp, 10 Nov)."""

import json
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings

from conversations.models import Mood
from conversations.services import ramp, servers

A, B = 'a' * 40, 'b' * 40


@override_settings(MOOD_DEPLOY_KEY='d' * 40)
class DeployCommitsTest(TestCase):

    def setUp(self):
        cache.clear()
        Mood.objects.create(slug='ramp', title='ramp')
        for p in (mock.patch.object(ramp, 'changed', lambda: None),
                  mock.patch('conversations.services.status.magenta_version', lambda: {'commit': B})):
            p.start()
            self.addCleanup(p.stop)

    def deploy(self, **body):
        return self.client.post('/api/deploys/', json.dumps(body), content_type='application/json',
                                HTTP_AUTHORIZATION='Bearer ' + 'd' * 40)

    def test_a_finished_deploy_says_what_runs_on_hunter(self):
        self.assertEqual(self.deploy(server='hunter', state='started').status_code, 201)
        self.assertNotIn('hunter', ramp.running().get('magenta', {}))   # under way: it claims nothing yet
        self.deploy(server='hunter', state='finished', commit='0123456789ab', commits={'magenta': A})
        running = ramp.running()
        self.assertEqual(running['magenta'], {'maybelle': B, 'hunter': A})
        self.assertEqual(running['maybelle-config'], {'hunter': '0123456789ab'})
        self.assertEqual(servers.last_deploy('hunter')['commits'], {'magenta': A})

    def test_maybelle_says_what_runs_on_maybelle_itself(self):
        # Its own deploy's word is what it meant to run; the running memory-lane's is what does.
        self.deploy(server='maybelle', state='finished', commits={'magenta': A})
        self.assertEqual(ramp.running()['magenta']['maybelle'], B)

    def test_a_failed_deploy_claims_nothing(self):
        self.deploy(server='hunter', state='failed', commits={'magenta': A})
        self.assertNotIn('hunter', ramp.running().get('magenta', {}))

    def test_only_names_and_hashes_are_kept(self):
        self.assertEqual(servers.deployed_commits({'magenta': A, 'x y': A, 'pickipedia': 'not-a-sha', 'arthel': 7,
                                                   'rabbithole': 'abc1234'}),
                         {'magenta': A, 'rabbithole': 'abc1234'})
        self.assertEqual(servers.deployed_commits(['magenta', A]), {})
        self.assertEqual(servers.deployed_commits({f'r{i}': A for i in range(20)}).__len__(), 10)
        self.assertEqual(self.deploy(server='hunter', state='finished', commits='magenta').status_code, 201)
        self.assertNotIn('commits', servers.last_deploy('hunter'))

    def test_a_finished_deploy_rebuilds_the_ramp(self):
        told = []
        with mock.patch.object(ramp, 'changed', lambda: told.append(1)):
            self.deploy(server='hunter', state='started')
            self.deploy(server='hunter', state='finished', commits={'magenta': A})
        self.assertEqual(told, [1])
