"""To-do types, and the deploy and verify items that tick themselves (services/todo_types.py; Justin, #ramp,
13-14 Nov)."""

from unittest import mock

from django.core.cache import cache
from django.test import TestCase

from conversations.models import Mood
from conversations.services import todo, todo_types as tt

WIKI = 'https://pickipedia.xyz/wiki/'


class TypeTest(TestCase):

    def test_the_kinds_written_are_read_as_eight_types(self):
        for kind, want in (('decision', 'decide'), ('Decide', 'decide'), ('docs', 'work'), ('document', 'work'),
                           ('infra', 'hands'), ('secret', 'hands'), ('writing', 'make'), ('music', 'make'),
                           ('check', 'verify'), ('redeploy', 'deploy'), ('promote', 'merge'), ('whatever', 'work')):
            self.assertEqual(tt.type_of({'kind': kind}), want, kind)

    def test_no_kind_but_a_branch_page_is_a_merge(self):
        self.assertEqual(tt.type_of({'link': 'https://magenta.cryptograss.live/ramp/magenta/magent/foo/'}), 'merge')
        self.assertEqual(tt.type_of({'link': 'https://example.com/'}), 'work')

    def test_summary_counts_open_per_type(self):
        rows = {r['type']: r for r in tt.summary(tt.with_types([
            {'kind': 'deploy', 'done': False}, {'kind': 'deploy', 'done': True}, {'kind': 'decision', 'done': False}]))}
        self.assertEqual(list(rows), list(tt.TYPES))
        self.assertEqual((rows['deploy']['open'], rows['deploy']['total']), (1, 2))
        self.assertEqual(rows['decide']['open'], 1)


class VerifyTest(TestCase):

    def setUp(self):
        cache.clear()

    def ask(self, pages, normalized=()):
        return lambda titles: {'query': {'normalized': list(normalized), 'pages': pages}}

    def test_a_trusted_editor_s_revision_and_no_unverified_claim_is_verified(self):
        got = tt.verified(['Main Page', 'Kyle Tuttle', 'Rabbithole', 'Gone'], ask=self.ask([
            {'title': 'Main Page', 'revisions': [{'user': 'JMyles'}]},
            {'title': 'Kyle Tuttle', 'revisions': [{'user': 'Blu'}]},                        # an emergent's, waiting
            {'title': 'Rabbithole', 'revisions': [{'user': 'JMyles'}],
             'categories': [{'title': tt.UNVERIFIED_CATEGORY}]},                             # a claim still wrapped
            {'title': 'Gone', 'missing': True}]))
        self.assertEqual(got, {'Main Page': True, 'Kyle Tuttle': False, 'Rabbithole': False})

    def test_a_title_written_otherwise_is_followed(self):
        got = tt.verified(['main_page'], ask=self.ask([{'title': 'Main Page', 'revisions': [{'user': 'SkymanJenkins'}]}],
                                                      normalized=[{'from': 'main_page', 'to': 'Main Page'}]))
        self.assertEqual(got, {'main_page': True})

    def test_the_link_names_the_page(self):
        self.assertEqual(tt.wiki_title(WIKI + "Song:Reuben's_Train"), "Song:Reuben's Train")
        self.assertEqual(tt.wiki_title(WIKI + 'Kuba%27s_Last_Merged_Commits#top'), "Kuba's Last Merged Commits")
        self.assertIsNone(tt.wiki_title('https://pickipedia.xyz/stats/'))
        self.assertIsNone(tt.wiki_title(''))

    def test_an_item_ticks_and_the_page_s_change_forgets_its_list(self):
        mood = Mood.objects.create(slug='connectome', title='connectome')
        items = [{'task': 'Verify Kyle Tuttle', 'kind': 'verify', 'link': WIKI + 'Kyle_Tuttle', 'done': False}]
        with mock.patch.object(tt, 'verified', lambda titles: {'Kyle Tuttle': False}):
            self.assertFalse(tt.with_closes('connectome', items)[0]['done'])
        with mock.patch.object(tt, 'verified', lambda titles: {'Kyle Tuttle': True}):
            done = tt.with_closes('connectome', items)[0]
        self.assertEqual((done['done'], done['verified'], done['type']), (True, True, 'verify'))
        cache.set('todo:connectome', {'items': []}, 60)
        self.assertEqual(tt.page_changed('Kyle_Tuttle'), ['connectome'])
        self.assertIsNone(cache.get('todo:connectome'))
        self.assertEqual(tt.page_changed('Some Other Page'), [])
        mood.delete()


class DeployTest(TestCase):

    def setUp(self):
        cache.clear()

    def item(self, task):
        return {'task': task, 'kind': 'deploy', 'done': False, 'type': 'deploy'}

    def test_servers_are_named_apart_from_repositories(self):
        self.assertEqual(tt._servers('Redeploy maybelle once pickipedia:magent/x has merged'), ['maybelle'])
        self.assertEqual(tt._servers('Then redeploy hunter and pickipedia'), ['hunter', 'pickipedia'])
        self.assertEqual(tt._servers('Deploy MemoryCommitments from the cryptograss wallet'), [])

    def test_naming_no_branch_it_counts_from_when_it_was_first_seen(self):
        item = self.item('Redeploy maybelle (migration 0053)')
        tt.note_seen('radicle', [item], now=1000)
        tt.note_seen('radicle', [item], now=5000)  # seen again later: the first sighting stands
        before = lambda name: {'at_ts': 900}
        after = lambda name: {'at_ts': 1100}
        self.assertIsNone(tt.deployed('radicle', item, runs={}, last_finished=before))
        self.assertEqual(tt.deployed('radicle', item, runs={}, last_finished=after), 'deployed')
        self.assertIsNone(tt.deployed('radicle', self.item('Redeploy maybelle, never seen'), runs={}, last_finished=after))

    def test_every_named_server_must_have_run(self):
        item = self.item('Then redeploy hunter and pickipedia')
        tt.note_seen('radicle', [item], now=1000)
        one = lambda name: {'at_ts': 1100} if name == 'hunter' else {'at_ts': 900}
        self.assertIsNone(tt.deployed('radicle', item, runs={}, last_finished=one))

    def test_a_pull_number_or_no_server_waits_for_a_person(self):
        after = lambda name: {'at_ts': 10 ** 10}
        for task in ('Redeploy delivery-kid: #298 is merged', 'Deploy MemoryCommitments from the cryptograss wallet',
                     'Redeploy maybelle, after both merges', 'Redeploy hunter once magent/pi-1.5 is merged'):
            item = self.item(task)
            tt.note_seen('m', [item], now=1)
            self.assertIsNone(tt.deployed('m', item, runs={}, last_finished=after), task)

    def test_naming_a_branch_the_server_must_run_it(self):
        item = self.item('Redeploy maybelle (magenta) once magenta:magent/dialogs-command has merged')
        runs = {'magenta': {'maybelle': 'abc'}}
        with mock.patch('conversations.services.tools_ask.named_branches', lambda t: [('magenta', 'magent/dialogs-command')]), \
                mock.patch.object(tt, '_merge_time', lambda repo, branch: None):
            self.assertIsNone(tt.deployed('m', item, runs=runs))                       # not merged yet
        with mock.patch('conversations.services.tools_ask.named_branches', lambda t: [('magenta', 'magent/dialogs-command')]), \
                mock.patch.object(tt, '_merge_time', lambda repo, branch: ('h', 500)):
            with mock.patch('conversations.services.ramp.live_on', lambda repo, head, runs: []):
                self.assertIsNone(tt.deployed('m', item, runs=runs))                   # merged, maybelle runs older
            with mock.patch('conversations.services.ramp.live_on', lambda repo, head, runs: ['maybelle']):
                self.assertEqual(tt.deployed('m', item, runs=runs), 'deployed')        # maybelle runs it

    def test_a_server_that_doesn_t_report_the_repository_needs_a_deploy_after_the_merge(self):
        item = self.item('Redeploy pickipedia after pickipedia:magent/x merges')
        with mock.patch('conversations.services.tools_ask.named_branches', lambda t: [('pickipedia', 'magent/x')]), \
                mock.patch.object(tt, '_merge_time', lambda repo, branch: ('h', 500)), \
                mock.patch('conversations.services.ramp.live_on', lambda repo, head, runs: None):
            self.assertIsNone(tt.deployed('m', item, runs={}, last_finished=lambda s: {'at_ts': 400}))
            self.assertEqual(tt.deployed('m', item, runs={}, last_finished=lambda s: {'at_ts': 600}), 'deployed')

    def test_a_finished_deploy_forgets_every_list(self):
        from conversations.services import servers
        Mood.objects.create(slug='ops', title='ops')
        cache.set('todo:ops', {'items': []}, 60)
        servers.record_deploy('maybelle', 'started')
        self.assertIsNotNone(cache.get('todo:ops'))       # started: nothing to tick yet
        servers.record_deploy('maybelle', 'finished', commit='a' * 40)
        self.assertIsNone(cache.get('todo:ops'))
        self.assertEqual(servers.last_finished('maybelle')['state'], 'finished')


class PageTest(TestCase):

    def test_the_page_filters_by_type(self):
        cache.clear()
        Mood.objects.create(slug='m1', title='Mood One')
        state = {'items': [{**tt.with_types([{'task': 'Redeploy hunter', 'kind': 'deploy', 'who': ['justin'],
                                              'link': '', 'note': '', 'done': False}])[0], 'mood': 'm1',
                            'mood_title': 'Mood One'}], 'total': 1, 'open': 1, 'done': 0,
                 'moods': [{'slug': 'm1', 'title': 'Mood One', 'count': 1, 'open': 1}]}
        state['types'] = tt.summary(state['items'])
        with mock.patch.object(todo, 'all_todos', lambda http=None: state):
            html = self.client.get('/todo/?embed=1').content.decode()
        self.assertIn('data-type="deploy"', html)
        self.assertIn('class="t-btn" data-type="deploy"', html)
        self.assertIn('class="t-btn none" data-type="merge"', html)   # none open: dimmed
        self.assertIn('🖥️ deploy', html)
