"""The deploy queue: one box for each server, and what has merged and waits for it (services/deploys.py).

Justin, #ramp, 25 Nov 2026: "I still show 7 deploy tasks, and they don't seem aware of each other. When will I
see a new interface, with deploys separated, and at-most one for each box? Show which PRs are already merged and
awaiting deploy?"
"""

import tempfile
from pathlib import Path
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings

from conversations.services import deploys
from conversations.tests.test_githouse import Repo, git


def commit_file(repo, path, text, message):
    """A commit that touches a path, so a merge can be read for whose box it is."""
    where = repo.work / path
    where.parent.mkdir(parents=True, exist_ok=True)
    where.write_text(text)
    git(repo.work, 'add', str(path))
    git(repo.work, 'commit', '-q', '-m', message)
    return git(repo.work, 'rev-parse', 'HEAD')


def merge_branch(repo, branch, base, path, text, message=''):
    """A candidate branch merged into base the way githouse merges one: a merge commit saying what it brought."""
    git(repo.work, 'checkout', '-q', base)
    git(repo.work, 'checkout', '-q', '-b', branch)
    head = commit_file(repo, path, text, f'work on {branch}')
    git(repo.work, 'checkout', '-q', base)
    name = repo.bare.name[:-4]
    git(repo.work, 'merge', '-q', '--no-ff', '-m', message or f'Merge {name}:{branch} ({head[:8]})', branch)
    repo.push()
    return head, git(repo.work, 'rev-parse', base)


def deploy_repo(mount, name='magenta'):
    """A repository githouse holds, on the branch its deploys come from. Every repository runs production
    since 13 Nov (Justin, #radicle: merge to production, run it, promote to main), so a fixture that merges
    into main is testing a world we no longer have."""
    repo = Repo(mount, name=name)
    git(repo.work, 'checkout', '-q', '-b', 'production')
    repo.push()
    return repo


class QueueTest(TestCase):

    def setUp(self):
        cache.clear()
        self.mount = Path(tempfile.mkdtemp())
        patch = override_settings(GITHOUSE_DIR=str(self.mount))
        patch.enable()
        self.addCleanup(patch.disable)
        self.addCleanup(cache.clear)


class NamesTest(TestCase):

    def test_a_box_is_named_by_its_own_name_or_what_the_lists_call_it(self):
        self.assertEqual(deploys.servers_named('Redeploy pickipedia-vps, for rabbithole:blu/fx-instruments'),
                         ['pickipedia'])
        self.assertEqual(deploys.servers_named('Redeploy maybelle, hunter, and pickipedia'),
                         ['hunter', 'maybelle', 'pickipedia'])

    def test_a_candidate_branch_is_not_a_box(self):
        # The pickipedia box is named here by the word "pickipedia" alone -- not by pickipedia:magent/invite-night.
        self.assertEqual(deploys.servers_named('Merge pickipedia:magent/invite-night'), [])
        self.assertEqual(deploys.servers_named('Redeploy hunter once pickipedia:magent/invite-night is merged'),
                         ['hunter'])


class OwedTest(QueueTest):

    def test_what_a_box_has_not_deployed_is_what_it_is_owed(self):
        repo = deploy_repo(self.mount)
        _, first = merge_branch(repo, 'magent/one', 'production', 'memory-lane/a.py', 'a')
        _, second = merge_branch(repo, 'magent/two', 'production', 'memory-lane/b.py', 'b')
        runs = {'magenta': {'maybelle': first}}
        owed = deploys.owed('maybelle', runs=runs, finished={'at_ts': 0})
        self.assertEqual([(m['repo'], m['branch'], m['commit']) for m in owed],
                         [('magenta', 'magent/two', second)])
        self.assertFalse(owed[0]['by_time'])      # read from the commit the box reported, not from a clock
        self.assertEqual(owed[0]['link'], '/ramp/magenta/magent/two/')
        # Caught up: nothing waiting.
        self.assertEqual(deploys.owed('maybelle', runs={'magenta': {'maybelle': second}}, finished={'at_ts': 0}), [])

    def test_a_box_whose_deploys_name_no_commit_is_read_from_its_last_deploy(self):
        repo = deploy_repo(self.mount, name='rabbithole')
        merge_branch(repo, 'blu/one-bar', 'production', 'src/bar.js', 'bar')
        when = int(git(repo.work, 'log', '-1', '--format=%ct', 'production'))
        owed = deploys.owed('pickipedia', runs={}, finished={'at_ts': when - 60})
        self.assertIn('blu/one-bar', [m['branch'] for m in owed])
        self.assertTrue(owed[0]['by_time'])       # read from the clock, and it says so
        after = deploys.owed('pickipedia', runs={}, finished={'at_ts': when + 60})
        self.assertEqual(after, [])               # the deploy finished after the merge: it carried it

    def test_a_deploy_that_started_before_a_merge_is_not_read_as_carrying_it(self):
        # re, 26 Nov: the pickipedia box clones mid-playbook, so a deploy that began before a merge landed may
        # finish after it, carrying nothing of it. The clock reads its start (at_ts - took), not its finish.
        repo = deploy_repo(self.mount, name='rabbithole')
        merge_branch(repo, 'blu/one-bar', 'production', 'src/bar.js', 'bar')
        when = int(git(repo.work, 'log', '-1', '--format=%ct', 'production'))
        straddled = deploys.owed('pickipedia', runs={}, finished={'at_ts': when + 300, 'took': 600})
        self.assertIn('blu/one-bar', [m['branch'] for m in straddled])  # started at when - 300: still owed
        carried = deploys.owed('pickipedia', runs={}, finished={'at_ts': when + 300, 'took': 100})
        self.assertEqual(carried, [])                                  # started at when + 200: carried it

    def test_a_maybelle_config_merge_is_the_box_s_only_if_it_touched_that_box(self):
        repo = deploy_repo(self.mount, name='maybelle-config')
        merge_branch(repo, 'magent/hunter-thing', 'production', 'hunter/Dockerfile', 'FROM ubuntu')
        merge_branch(repo, 'magent/a-script', 'production', 'scripts/tidy.py', 'print(1)')
        _, head = merge_branch(repo, 'magent/everyone', 'production', 'shared/ansible/roles/caddy/x.yml', 'x')
        base = git(repo.work, 'rev-parse', 'production~3')
        runs = {'maybelle-config': {name: base for name in ('hunter', 'maybelle', 'pickipedia', 'delivery-kid')}}
        for box, wanted in (('hunter', ['magent/everyone', 'magent/hunter-thing']),
                            ('maybelle', ['magent/everyone']),
                            ('pickipedia', ['magent/everyone']),
                            ('delivery-kid', ['magent/everyone'])):
            owed = deploys.owed(box, runs=runs, finished={'at_ts': 0})
            self.assertEqual(sorted(m['branch'] for m in owed), sorted(wanted), box)
        self.assertEqual(deploys.owed('hunter', runs={'maybelle-config': {'hunter': head}}, finished={'at_ts': 0}), [])


class PinnedTest(QueueTest):

    def pin(self, config, commit):
        (config.work / 'hunter').mkdir(exist_ok=True)
        (config.work / 'hunter' / 'Dockerfile').write_text(f'FROM ubuntu\nARG PICKIPEDIA_MCP_COMMIT={commit}\n')
        git(config.work, 'add', 'hunter/Dockerfile')
        git(config.work, 'commit', '-q', '-m', 'pin the mcp')
        config.push()
        return git(config.work, 'rev-parse', 'production')

    def test_a_merge_is_not_on_its_way_until_the_pin_moves(self):
        mcp = deploy_repo(self.mount, name='pickipedia-mcp')
        head, _ = merge_branch(mcp, 'magent/yaml-pages', 'production', 'src/index.ts', 'yaml')
        config = deploy_repo(self.mount, name='maybelle-config')
        at = self.pin(config, head)
        runs = {'maybelle-config': {'hunter': at}}
        # The pin names the branch's own head, which the merge that landed it is built on: nothing is waiting.
        held = deploys.pinned('hunter', runs=runs)
        self.assertEqual([(p['repo'], p['pin'], p['behind']) for p in held], [('pickipedia-mcp', head, 0)])
        # Another merge on master: hunter still builds the pin, so the queue says so rather than claiming a
        # deploy would pick it up.
        merge_branch(mcp, 'magent/deps-2', 'production', 'package-lock.json', '{}')
        cache.clear()
        held = deploys.pinned('hunter', runs=runs)
        self.assertEqual(held[0]['behind'], 1)
        self.assertEqual([m['branch'] for m in held[0]['merges']], ['magent/deps-2'])
        self.assertEqual(held[0]['pin_in'], 'maybelle-config')
        self.assertEqual(deploys.pinned('maybelle', runs=runs), [])    # nobody else pins it

    def test_no_pin_in_the_file_says_nothing(self):
        deploy_repo(self.mount, name='pickipedia-mcp')
        config = deploy_repo(self.mount, name='maybelle-config')
        config.push()
        self.assertEqual(deploys.pinned('hunter', runs={}), [])


class AsksTest(QueueTest):

    def items(self):
        return [
            {'type': 'deploy', 'done': False, 'task': 'Redeploy pickipedia-vps, for rabbithole:blu/fx-instruments',
             'note': '', 'mood': 'connectome', 'mood_title': 'connectome', 'who': ['justin']},
            {'type': 'deploy', 'done': False, 'task': 'Redeploy pickipedia, so the soloist card goes live',
             'note': 'After rabbithole:blu/soloist-card merges.', 'mood': 'connectome',
             'mood_title': 'connectome', 'who': ['justin']},
            {'type': 'deploy', 'done': False, 'task': 'Redeploy maybelle and hunter', 'note': '',
             'mood': 'interfaces', 'mood_title': 'interfaces', 'who': ['justin']},
            {'type': 'deploy', 'done': True, 'task': 'Redeploy hunter, done last week', 'note': '',
             'mood': 'ramp', 'mood_title': 'ramp', 'who': []},
            {'type': 'merge', 'done': False, 'task': 'Merge magenta:magent/x', 'note': '', 'mood': 'ramp',
             'mood_title': 'ramp', 'who': []},
        ]

    def test_each_ask_folds_into_every_box_it_names(self):
        repo = deploy_repo(self.mount, name='rabbithole')
        merged, _ = merge_branch(repo, 'blu/fx-instruments', 'production', 'src/fx.js', 'fx')
        repo.ref('refs/merged/blu/fx-instruments', merged)
        git(repo.work, 'checkout', '-q', 'production')
        git(repo.work, 'checkout', '-q', '-b', 'blu/soloist-card')
        commit_file(repo, 'src/card.js', 'card', 'the card')
        repo.push()
        found = deploys.asks(self.items())
        self.assertEqual(sorted(found), ['hunter', 'maybelle', 'pickipedia'])
        self.assertEqual(len(found['pickipedia']), 2)
        self.assertEqual(found['maybelle'][0]['boxes'], ['hunter', 'maybelle'])   # one ask, both boxes
        self.assertIs(found['maybelle'][0], found['hunter'][0])
        merged_ask, waiting_ask = found['pickipedia']
        self.assertEqual([w['state'] for w in merged_ask['waiting']], ['merged'])
        self.assertEqual(merged_ask['held'], [])
        # The branch named in the note counts: the item is held until githouse merges it.
        self.assertEqual([(w['branch'], w['state']) for w in waiting_ask['waiting']],
                         [('blu/soloist-card', 'open')])
        self.assertEqual([w['branch'] for w in waiting_ask['held']], ['blu/soloist-card'])

    def test_an_item_that_is_done_or_is_not_a_deploy_is_left_out(self):
        deploy_repo(self.mount, name='rabbithole')
        found = deploys.asks(self.items())
        tasks = [a['task'] for box in found.values() for a in box]
        self.assertNotIn('Redeploy hunter, done last week', tasks)
        self.assertNotIn('Merge magenta:magent/x', tasks)


class PageTest(QueueTest):

    def test_the_queue_is_one_box_for_each_server(self):
        from conversations.services import servers
        with mock.patch.object(deploys, 'asks', lambda items=None: {}):
            found = deploys.queue(fresh=True)
        self.assertEqual([b['name'] for b in found['boxes']], servers.NAMES)
        self.assertEqual(found['owed'], 0)
        self.assertEqual(found['ready'], 0)

    def test_a_box_that_is_owed_something_comes_first(self):
        repo = deploy_repo(self.mount)
        _, first = merge_branch(repo, 'magent/one', 'production', 'memory-lane/a.py', 'a')
        merge_branch(repo, 'magent/two', 'production', 'memory-lane/b.py', 'b')
        # magenta runs on maybelle and hunter; only maybelle says which commit it has, so only it is owed.
        with mock.patch('conversations.services.ramp.running', lambda: {'magenta': {'maybelle': first}}), \
                mock.patch.object(deploys, 'asks', lambda items=None: {}), \
                mock.patch('conversations.services.servers.last_finished', lambda name: {'at_ts': 10 ** 10}):
            found = deploys.queue(fresh=True)
        self.assertEqual(found['boxes'][0]['name'], 'maybelle')
        self.assertEqual(found['ready'], 1)

    def test_the_todo_page_carries_the_boxes_for_the_deploy_type(self):
        with mock.patch.object(deploys, 'asks', lambda items=None: {}):
            html = self.client.get('/todo/?embed=1').content.decode()
        self.assertIn('id="deployStrip"', html)
        self.assertIn('href="/deploys/#hunter"', html)

    def test_the_page_shows_each_box_and_what_waits_for_it(self):
        repo = deploy_repo(self.mount)
        _, first = merge_branch(repo, 'magent/one', 'production', 'memory-lane/a.py', 'a')
        _, second = merge_branch(repo, 'magent/two', 'production', 'memory-lane/b.py', 'b')
        with mock.patch('conversations.services.ramp.running', lambda: {'magenta': {'maybelle': first}}), \
                mock.patch.object(deploys, 'asks', lambda items=None: {}):
            html = self.client.get('/deploys/?embed=1').content.decode()
        self.assertIn('id="maybelle"', html)
        self.assertIn('/ramp/magenta/magent/two/', html)
        self.assertIn('merged, waiting for a deploy', html)
        self.assertIn('Nothing merged is waiting.', html)       # the other boxes
        self.assertNotIn(second[:12] + 'x', html)

    def test_a_finished_deploy_and_a_merge_are_read_again(self):
        from conversations.services import todo
        with mock.patch.object(deploys, 'asks', lambda items=None: {}):
            deploys.queue(fresh=True)
            self.assertIsNotNone(cache.get(deploys.KEY))
            todo.forget_all()        # servers.record_deploy and merge_orders both call this
            self.assertIsNone(cache.get(deploys.KEY))
