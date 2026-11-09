"""The ramp page (/moods/pulls/; services/ramp.py): open work from the cards and githouse, its mentions, asks and
temperature, the last merged and where they run. Real bare repositories; the wiki's cards stood in for."""

import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings

from conversations.models import Message, Mood, ThinkingEntity
from conversations.services import front, ramp
from conversations.tests.test_githouse import Repo, git


def card(repo, number, branch, state='open', **more):
    return {'repo': repo, 'number': str(number), 'title': f'the {branch} work', 'url': f'https://github.com/cryptograss/{repo}/pull/{number}',
            'author': 'magent-cryptograss', 'state': state, 'base': 'main', 'branch': branch, 'opened': '2026-11-06 10:00:00',
            'pushed': '2026-11-06 10:00:00', 'merged': more.pop('merged', ''), 'worked_on_by': 'magent, re', 'moods': 'ramp, ops',
            'deploy': 'redeploy maybelle', **more}


class RampTest(TestCase):

    def setUp(self):
        cache.clear()
        self.mount = Path(tempfile.mkdtemp())
        self.settings = override_settings(GITHOUSE_DIR=str(self.mount))
        self.settings.enable()
        self.addCleanup(self.settings.disable)
        self.repo = Repo(self.mount)
        git(self.repo.work, 'checkout', '-q', '-b', 're/foo')
        self.foo = self.repo.commit('the foo work')
        self.repo.ref('refs/pull/7/head', self.foo)
        git(self.repo.work, 'checkout', '-q', 'main')
        git(self.repo.work, 'checkout', '-q', '-b', 'magent/bar')
        self.bar = self.repo.commit('bar, pushed with no pull request')
        git(self.repo.work, 'checkout', '-q', 'main')
        self.repo.ref('refs/heads/magent/bar', self.bar)
        self.repo.ref('refs/closed/magent/old', self.bar)
        (self.mount / 'githouse.log').write_text('2026-11-07T01:00:00Z first\n2026-11-07T02:00:00Z second\n')
        self.cards = {'open': [card('magenta', 7, 're/foo')], 'merged': []}
        for patch in (mock.patch.object(ramp, 'cards', lambda: self.cards),
                      mock.patch.object(ramp, 'BUILD_IN_BACKGROUND', False)):
            patch.start()
            self.addCleanup(patch.stop)
        ThinkingEntity.objects.get_or_create(name='re', defaults={'is_biological_human': False})
        ThinkingEntity.objects.get_or_create(name='mood-poller', defaults={'is_biological_human': False})
        ThinkingEntity.objects.get_or_create(name='justin', defaults={'is_biological_human': True})
        self.mood = Mood.objects.create(slug='jams', title='jams')
        self.now = datetime.now(timezone.utc)

    def say(self, who, text):
        return Message.objects.create(id=uuid.uuid4(), sender_id=who, mood=self.mood, session_id=uuid.uuid4(),
                                      timestamp=int(self.now.timestamp() * 1000),
                                      content=[{'type': 'text', 'text': text}])

    def test_how_a_piece_of_work_is_named(self):
        pattern = ramp._pattern({'repo': 'magenta', 'number': 415, 'branch': 'magent/asks-from-githouse'})
        for said in ('magenta#415', 'merge #415 please', 'PR #415,', 'https://github.com/cryptograss/magenta/pull/415',
                     'magenta:magent/asks-from-githouse', 'on magent/asks-from-githouse now'):
            self.assertTrue(pattern.search(said), said)
        for said in ('pickipedia#415', '#4150', 'magenta#41', 'magent/asks-from-githouse-2', 'cryptograss/pickipedia/pull/415'):
            self.assertFalse(pattern.search(said), said)
        maybelle = ramp._pattern({'repo': 'maybelle-config', 'number': 250, 'branch': 'githouse'})
        self.assertTrue(maybelle.search('maybelle#250'))
        self.assertTrue(maybelle.search('maybelle-config#250'))
        self.assertFalse(maybelle.search('#250'))  # bare, a number is magenta's

    def test_open_work_from_cards_and_githouse(self):
        board = ramp.board(now=self.now)
        by = {(i['repo'], i['number'], i['branch']): i for i in board['open']}
        pull = by[('magenta', 7, 're/foo')]
        self.assertEqual(pull['state'], 'open')
        self.assertEqual(pull['head'], self.foo)
        self.assertEqual(pull['size']['commits'], 1)
        self.assertEqual(pull['moods'], ['ramp', 'ops'])
        self.assertGreater(pull['pushed'], datetime(2026, 11, 6, 10, 1, tzinfo=timezone.utc))  # githouse's, not the card's
        pushed = by[('magenta', None, 'magent/bar')]  # a branch on githouse with no pull request
        self.assertEqual((pushed['state'], pushed['author'], pushed['title'], pushed['base']),
                         ('pushed', 'magent', 'bar, pushed with no pull request', 'main'))
        self.assertNotIn(('magenta', None, 'main'), by)  # GitHub's own branches aren't work
        self.assertEqual([(n['branch'], n['kind']) for n in board['names']], [('magent/old', 'closed')])
        self.assertEqual(board['log'], ['2026-11-07T02:00:00Z second', '2026-11-07T01:00:00Z first'])
        self.assertEqual(board['repos'], ['magenta'])

    def test_githouse_knows_a_merge_before_the_card_does(self):
        git(self.repo.work, 'merge', '-q', '--no-ff', '-m', 'Merge pull request #7', 're/foo')
        self.repo.push()
        pull = next(i for i in ramp.board(now=self.now)['open'] if i['number'] == 7)
        self.assertEqual(pull['state'], 'merged')
        self.assertEqual(ramp.ordered(ramp.board(now=self.now)['open'], '')[-1]['number'], 7)  # merged sink

    def test_the_last_two_mentions_and_the_temperature(self):
        self.say('justin', 'what about magenta#7?')
        self.say('mood-poller', '<mood-wake> [justin] what about magenta#7?')  # a wake quoting it isn't a mention
        self.say('re', 'Reviewed #7: two bugs.')
        self.say('re', 'and magent/bar is next')
        board = ramp.board(now=self.now)
        pull = next(i for i in board['open'] if i['number'] == 7)
        self.assertEqual([m['who'] for m in pull['mentions']], ['re', 'justin'])
        self.assertEqual(pull['temperature'], 'hot')
        bar = next(i for i in board['open'] if i['branch'] == 'magent/bar')
        self.assertEqual([m['who'] for m in bar['mentions']], ['re'])
        self.assertEqual(ramp.temperature({'pushed': self.now - timedelta(days=2), 'mentions': []}, self.now), 'warm')
        self.assertEqual(ramp.temperature({'pushed': self.now - timedelta(days=9), 'mentions': []}, self.now), 'cool')
        self.assertEqual(ramp.temperature({'pushed': None, 'mentions': []}, self.now), 'cold')

    def test_what_a_post_names(self):
        owners = {'re', 'magent', 'justin'}
        self.assertEqual(ramp.refs_in('merge magenta#415 and #416, then maybelle#250; PR #347', owners),
                         {('pull', 'magenta', 415), ('pull', 'magenta', 416), ('pull', 'maybelle-config', 250),
                          ('pull', 'magenta', 347)})
        self.assertEqual(ramp.refs_in('https://github.com/cryptograss/pickipedia/pull/172#issuecomment-1', owners),
                         {('pull', 'pickipedia', 172)})
        self.assertEqual(ramp.refs_in('magenta:magent/ramp-view. And re/fix-a, then memory-lane/conversations', owners),
                         {('branch', 'magent/ramp-view'), ('branch', 're/fix-a')})
        for nothing in ('&#124; a pipe', 'colour #4a90c8', 'someone#4', 'where/when', '#m-1234abcd', 'issue-#12'):
            self.assertEqual(ramp.refs_in(nothing, owners), set(), nothing)

    def test_the_index_is_kept_up_from_new_posts_only(self):
        self.say('re', 'magenta#7 first')
        self.assertEqual(len(ramp.mention_index()[('pull', 'magenta', 7)]), 1)
        self.say('justin', 'and #7 again, on re/foo')
        with mock.patch.object(ramp, 'FRESH_EVERY', -1), \
                mock.patch.object(ramp, '_build', side_effect=AssertionError('built again')):
            index = ramp.mention_index()
        self.assertEqual([e['who'] for e in index[('pull', 'magenta', 7)]], ['justin', 're'])
        self.assertEqual([e['who'] for e in index[('branch', 're/foo')]], ['justin'])

    def test_a_failed_build_waits_ten_minutes_not_one_page(self):
        tries = []

        def times_out(since):
            tries.append(since)
            raise RuntimeError('canceling statement due to statement timeout')
        with mock.patch.object(ramp, '_posts', times_out):
            for _ in range(3):
                self.assertIsNone(ramp.mention_index())
        self.assertEqual(len(tries), 1)
        cache.delete(ramp.REFS_BUILDING)  # ten minutes on
        self.say('re', 'magenta#7 again')
        self.assertEqual(len(ramp.mention_index()[('pull', 'magenta', 7)]), 1)

    def test_while_the_index_is_first_built_the_page_says_so(self):
        self.say('re', 'magenta#7 again')
        with mock.patch.object(ramp, 'BUILD_IN_BACKGROUND', True), \
                mock.patch('conversations.services.todo._in_background', lambda fn, *a: None):
            board = ramp.board(now=self.now)
        self.assertTrue(board['mentions_missing'])
        self.assertEqual(next(i for i in board['open'] if i['number'] == 7)['mentions'], [])
        cache.clear()
        board = ramp.board(now=self.now)
        self.assertFalse(board['mentions_missing'])
        self.assertEqual(len(next(i for i in board['open'] if i['number'] == 7)['mentions']), 1)

    def test_an_open_merge_ask_is_shown(self):
        self.say('re', '<merge to="magenta#7, the foo fix"/>')
        pull = next(i for i in ramp.board(now=self.now)['open'] if i['number'] == 7)
        self.assertEqual((pull['asked']['by'], pull['asked']['mood']), ('re', 'jams'))

    def test_where_a_merged_pull_request_runs(self):
        git(self.repo.work, 'merge', '-q', '--no-ff', '-m', 'Merge pull request #7', 're/foo')
        self.repo.push()
        running = git(self.repo.work, 'rev-parse', 'main')
        self.cards = {'open': [], 'merged': [card('magenta', 7, 're/foo', state='merged', merged='2026-11-07 03:00:00')]}
        with mock.patch.object(ramp, 'running', lambda: {'magenta': {'maybelle': running}}):
            self.assertEqual(ramp.board(now=self.now)['merged'][0]['live_on'], ['maybelle'])
        with mock.patch.object(ramp, 'running', lambda: {'magenta': {'maybelle': self.repo_first()}}):
            self.assertEqual(ramp.board(now=self.now)['merged'][0]['live_on'], [])  # not yet
        with mock.patch.object(ramp, 'running', lambda: {}):
            self.assertIsNone(ramp.board(now=self.now)['merged'][0]['live_on'])  # its deploys don't say

    def repo_first(self):
        return git(self.repo.work, 'rev-list', '--max-parents=0', 'main')

    def test_the_page(self):
        self.say('re', 'magenta#7 looks good')
        page = self.client.get('/moods/pulls/?embed=1')
        self.assertEqual(page.status_code, 200)
        text = page.content.decode()
        for seen in ('magenta#7', 'the re/foo work', 'magenta:magent/bar', 'no PR yet', '#jams', 'magent/old', 'second'):
            self.assertIn(seen, text)

    def test_with_no_githouse_and_no_wiki(self):
        self.cards = {'open': [], 'merged': [], 'error': "PickiPedia didn't answer: the cards are missing"}
        with override_settings(GITHOUSE_DIR=str(self.mount / 'nowhere')):
            board = ramp.board(now=self.now)
        self.assertEqual((board['open'], board['repos'], board['log']), ([], [], []))
        self.assertIn('cards are missing', board['error'])


class AskDotsTest(RampTest):
    """A dot of its colour for each emergent asking for a merge (Justin, 9 Nov), on the ramp and its sheet."""

    def setUp(self):
        super().setUp()
        ThinkingEntity.objects.get_or_create(name='magent', defaults={'is_biological_human': False})

    def test_a_dot_for_each_emergent_asking(self):
        self.say('re', '<merge to="magenta#7, the foo fix"/>')
        self.say('magent', '<merge to="magenta#7 too"/>')
        pull = next(i for i in ramp.board(now=self.now)['open'] if i['number'] == 7)
        self.assertEqual(sorted((a['by'], a['colour']) for a in pull['asks']),
                         [('magent', '#ff67fd'), ('re', '#ff3c00')])
        self.assertEqual(pull['asked'], pull['asks'][-1])

    def test_an_emergent_asking_twice_is_one_dot(self):
        self.say('re', '<merge to="magenta#7"/>')
        self.say('re', '<merge to="magenta#7, again"/>')
        pull = next(i for i in ramp.board(now=self.now)['open'] if i['number'] == 7)
        self.assertEqual([a['by'] for a in pull['asks']], ['re'])

    def test_the_ramp_draws_the_dots(self):
        self.say('re', '<merge to="magenta#7"/>')
        page = self.client.get('/moods/pulls/', {'embed': 1}, HTTP_SEC_FETCH_DEST='iframe').content.decode()
        self.assertIn('class="askdot" href="/moods/jams/#m-', page)
        self.assertIn('style="--who: #ff3c00"', page)
        self.assertNotIn('🪢 asked', page)

    def test_any_emergents_colour(self):
        self.assertEqual(front.agent_colour('blu'), '#1976d2')
        self.assertIn(front.agent_colour('someone-new'), front.AGENT_PALETTE)
        self.assertEqual(front.agent_colour('someone-new'), front.agent_colour('someone-new'))


class BranchTestsTest(TestCase):
    """The ramp's red and green: the newest branch-tests run of each head, from Jenkins (Plan, section 11)."""

    def setUp(self):
        from django.core.cache import cache
        cache.delete(ramp.TESTS_KEY)

    def runs(self, builds, status=200):
        import requests
        answer = mock.Mock(status_code=status)
        answer.json.return_value = {'builds': builds}
        answer.raise_for_status = mock.Mock(side_effect=None if status == 200 else requests.HTTPError(str(status)))
        with mock.patch('requests.get', return_value=answer) as got:
            found = ramp.test_runs()
        return found, got

    @staticmethod
    def build(number, repo, commit, result=None, building=False):
        return {'number': number, 'result': result, 'building': building,
                'url': f'https://maybelle.cryptograss.live/job/branch-tests/{number}/',
                'actions': [{}, {'parameters': [{'name': 'REPO', 'value': repo}, {'name': 'BRANCH', 'value': 're/x'},
                                                {'name': 'COMMIT', 'value': commit}]}]}

    def test_the_newest_run_of_each_commit(self):
        found, _ = self.runs([self.build(3, 'magenta', 'a' * 40, building=True),
                              self.build(2, 'magenta', 'a' * 40, 'FAILURE'),
                              self.build(1, 'maybelle-config', 'b' * 40, 'SUCCESS'),
                              self.build(0, 'arthel', 'c' * 40, 'NOT_BUILT')])
        self.assertEqual(found[('magenta', 'a' * 40)]['state'], 'testing')
        self.assertEqual(found[('maybelle-config', 'b' * 40)],
                         {'state': 'passed', 'number': 1, 'url': 'https://maybelle.cryptograss.live/job/branch-tests/1/'})
        self.assertEqual(found[('arthel', 'c' * 40)]['state'], 'no ci/test')

    def test_kept_a_while_and_jenkins_down_is_no_runs(self):
        found, got = self.runs([self.build(1, 'magenta', 'a' * 40, 'SUCCESS')])
        self.assertEqual(ramp.test_runs(), found)  # from the cache, not asked again
        self.assertEqual(got.call_count, 1)
        from django.core.cache import cache
        cache.delete(ramp.TESTS_KEY)
        self.assertEqual(self.runs([], status=503)[0], {})

    def test_items_get_their_head_s_run(self):
        self.runs([self.build(1, 'magenta', 'a' * 40, 'FAILURE')])
        items = ramp.with_tests([{'repo': 'magenta', 'head': 'a' * 40}, {'repo': 'magenta', 'head': 'd' * 40}])
        self.assertEqual([i['tests'] and i['tests']['state'] for i in items], ['failed', None])
