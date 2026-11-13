"""The ramp page (/ramp/; services/ramp.py): open work from the cards and githouse, its mentions, asks and
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
        page = self.client.get('/ramp/?embed=1')
        self.assertEqual(page.status_code, 200)
        text = page.content.decode()
        # Every candidate by its name, repo:branch; a pull request only as a small link to GitHub's page for it.
        for seen in ('magenta:re/foo', 'PR #7 ↗', 'the re/foo work', 'magenta:magent/bar', '#jams', 'magent/old', 'second'):
            self.assertIn(seen, text)

    def test_a_branch_is_measured_against_where_its_repository_deploys_from(self):
        # Measured against where it merges (merge_orders.DEPLOYS_FROM): magenta's old production, dead a year,
        # showed a year of commits (9 Nov); every repository runs a live production since 13 Nov (#radicle).
        refs = {'refs/heads/production': None, 'refs/heads/main': None}
        self.assertEqual(ramp._base(refs, '', 'magenta'), 'production')
        self.assertEqual(ramp._base({'refs/heads/main': None}, '', 'elsewhere'), 'main')
        self.assertEqual(ramp._base(refs, '', 'maybelle-config'), 'production')
        self.assertEqual(ramp._base(refs, 'production', 'magenta'), 'production')  # a pull request's own target
        self.assertEqual(ramp._base(refs, '', 'elsewhere'), 'production')

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
        page = self.client.get('/ramp/', {'embed': 1}, HTTP_SEC_FETCH_DEST='iframe').content.decode()
        self.assertIn('class="askdot" href="/moods/jams/#m-', page)
        self.assertIn('style="--who: #ff3c00"', page)
        self.assertNotIn('🪢 asked', page)

    def test_an_ask_carries_what_it_names_not_merged_yet(self):
        # Justin, 9 Nov: an ask naming #451 and #449 kept a Merge for #449 after #449 merged.
        from conversations.services import tools_ask
        ramp.board(now=self.now)  # the ramp has been drawn: it says #7's branch
        cache.delete_many([tools_ask.KEY, tools_ask.SCAN_KEY])  # drawing it asked for the asks: none, then
        self.say('re', '<merge to="magenta#7 and magenta:magent/bar"/>')
        [ask] = tools_ask.open_requests()['jams']
        self.assertEqual(ask['works'], [{'repo': 'magenta', 'number': 7, 'branch': 're/foo', 'thumbs': []},
                                        {'repo': 'magenta', 'branch': 'magent/bar', 'thumbs': []}])
        git(self.repo.work, 'merge', '-q', '--no-ff', '-m', 'Merge pull request #7', 're/foo')
        self.repo.push()
        cache.clear()
        [ask] = tools_ask.open_requests()['jams']
        self.assertEqual(ask['works'], [{'repo': 'magenta', 'branch': 'magent/bar', 'thumbs': []}])

    def test_an_ask_carries_the_thumbs_on_what_it_names(self):
        # Justin, 10 Nov: people's 👍s in the Mood, beside the emergents' dots.
        from conversations.models import Thumb
        from conversations.services import tools_ask
        Thumb.objects.create(repo='magenta', branch='magent/bar', commit='0' * 40, by='skyler')   # an older commit
        Thumb.objects.create(repo='magenta', branch='magent/bar', commit=self.bar, by='justin')  # its head
        self.say('re', '<merge to="magenta:magent/bar"/>')
        [ask] = tools_ask.open_requests()['jams']
        [work] = ask['works']
        self.assertEqual([(t['by'], t['current']) for t in work['thumbs']], [('skyler', False), ('justin', True)])
        self.assertTrue(work['thumbs'][0]['at'].startswith('20'))

    def test_the_live_poll_carries_the_asks(self):
        self.say('re', '<merge to="magenta#7"/>')
        live = self.client.get('/api/moods/live/').json()['moods']
        self.assertEqual([a['emergent'] for a in live['jams']['asked']], ['re'])

    def test_a_card_is_read_from_the_last_board_at_once(self):
        # Justin, 9 Nov: the merge sheet sat on "Looking at githouse's copy…" while the board was built (4.8 s on
        # maybelle). A caller that only needs a card gets the last board, however old; an old one is rebuilt.
        import time as clock
        old = clock.time() - 3600
        cache.set(ramp.KEY, {**ramp.board(), 'built': old}, None)   # an hour old
        built = []
        with mock.patch.object(ramp, '_rebuild', lambda: built.append(1)):
            got = [ramp.board(stale_ok=True)['built'] for _ in range(3)]
        self.assertEqual(got, [old] * 3)                     # the old one, at once, every time
        self.assertEqual(built, [1])                         # one rebuild started, not one a call
        self.assertGreater(ramp.board()['built'], clock.time() - 60)   # the page itself still gets a fresh one

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


class ClashTest(TestCase):
    """Skyler and Justin, 10 Nov: the ramp says when a candidate would conflict -- with its base, and with another
    open candidate -- before anyone presses Merge."""

    def setUp(self):
        import os
        import subprocess
        cache.clear()
        self.mount = Path(tempfile.mkdtemp())
        self.repo = Repo(self.mount)
        w = self.repo.work

        def change(branch, text, start='main', name='a.txt'):
            git(w, 'checkout', '-q', start)
            git(w, 'checkout', '-q', '-b', branch)
            (w / name).write_text(text)
            git(w, 'add', name)
            git(w, 'commit', '-q', '-m', branch)
            return git(w, 'rev-parse', 'HEAD')
        (w / 'a.txt').write_text('one\n')
        git(w, 'add', 'a.txt')
        git(w, 'commit', '-q', '-m', 'a')
        self.heads = {'re/left': change('re/left', 'left\n'), 'magent/right': change('magent/right', 'right\n'),
                      'blu/elsewhere': change('blu/elsewhere', 'b\n', name='b.txt')}
        git(w, 'checkout', '-q', 'main')
        (w / 'a.txt').write_text('main moved\n')
        git(w, 'commit', '-q', '-am', 'main moves')
        self.heads['re/later'] = change('re/later', 'main moved\nand more\n')
        self.repo.push()
        # githouse's copy is mounted read-only: the check makes its objects elsewhere.
        for root, dirs, files in os.walk(self.repo.bare):
            for n in dirs + files:
                os.chmod(os.path.join(root, n), 0o555 if n in dirs else 0o444)
        self.addCleanup(subprocess.run, ['chmod', '-R', 'u+w', str(self.mount)])

    def items(self):
        return [{'repo': 'magenta', 'number': None, 'branch': b, 'base': 'main', 'state': 'pushed', 'head': h}
                for b, h in self.heads.items()]

    def test_with_its_base_and_with_each_other(self):
        refs = {'magenta': ramp._refs(self.repo.bare)}
        got = {i['branch']: i for i in ramp.clashes(self.items(), {'magenta': self.repo.bare}, refs)}
        self.assertEqual(got['re/left']['conflicts'], ['a.txt'])      # main changed the same line since
        self.assertEqual(got['blu/elsewhere']['conflicts'], [])       # another file
        self.assertEqual(got['re/later']['conflicts'], [])            # built on main as it is
        # re/left and magent/right are each at odds with main already: that's their own mark, not a clash between them.
        self.assertEqual(got['re/left']['clashes'], [])
        self.assertEqual([c['ref'] for c in got['re/later']['clashes']], [])
        self.assertEqual(got['blu/elsewhere']['clashes'], [])

    def test_two_candidates_at_odds_with_each_other_only(self):
        w = self.repo.work
        import subprocess
        subprocess.run(['chmod', '-R', 'u+w', str(self.mount)], check=True)
        git(w, 'checkout', '-q', 'main')
        git(w, 'checkout', '-q', '-b', 'magent/also-later')
        (w / 'a.txt').write_text('main moved\nbut otherwise\n')
        git(w, 'commit', '-q', '-am', 'also')
        self.heads['magent/also-later'] = git(w, 'rev-parse', 'HEAD')
        self.repo.push()
        refs = {'magenta': ramp._refs(self.repo.bare)}
        got = {i['branch']: i for i in ramp.clashes(self.items(), {'magenta': self.repo.bare}, refs)}
        self.assertEqual(got['magent/also-later']['conflicts'], [])
        self.assertEqual(got['re/later']['clashes'], [{'ref': 'magenta:magent/also-later', 'branch': 'magent/also-later',
                                                       'files': ['a.txt']}])
        self.assertEqual([c['ref'] for c in got['magent/also-later']['clashes']], ['magenta:re/later'])

    def test_the_row_says_so(self):
        from django.template.loader import render_to_string
        item = {'repo': 'magenta', 'branch': 're/left', 'base': 'main', 'state': 'pushed', 'title': 'left', 'author': 're',
                'worked_on_by': '', 'where': [], 'mentions': [], 'people': [], 'size': {}, 'held': True, 'deploy': '',
                'conflicts': ['a.txt'], 'clashes': [{'ref': 'magenta#7', 'branch': 're/foo', 'files': ['b.txt']}]}
        page = render_to_string('conversations/mood_pulls.html', {'open': [item], 'board': {'merged': [], 'names': []}})
        self.assertIn('⚠ conflicts with main</span>', page)
        self.assertIn('conflicts in: a.txt', page)
        self.assertIn('⇄ magenta#7</span>', page)
        self.assertIn('would conflict with this in: b.txt', page)


class RampMoodFilterTest(TestCase):
    """Justin, 10 Nov: a Mood's name on the ramp filters the list to that Mood's candidates, open and merged."""

    def test_mood_names_filter_rather_than_go(self):
        from pathlib import Path
        page = (Path(__file__).resolve().parent.parent / 'templates' / 'conversations' / 'mood_pulls.html').read_text()
        self.assertIn('data-moods="{{ i.where|join:\' \' }}"', page)
        self.assertIn('<tr data-moods="{{ m.moods|join:\' \' }}">', page)
        self.assertEqual(page.count('data-filter-mood="'), 2)  # open and merged
        self.assertIn('class="moodfilter" hidden', page)
