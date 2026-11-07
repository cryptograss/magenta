"""Merged, read from githouse's copy of a repository (services/githouse.py; Cryptograss:Moods/ramp/Plan, section 4):
a pull request by its head's ancestry, a mergeable by its name in refs/merged/. Real bare repositories, made here."""

import subprocess
import tempfile
from pathlib import Path
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings

from conversations.services import githouse

ENV = {'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@t', 'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@t',
       'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_CONFIG_NOSYSTEM': '1'}


def git(where, *args):
    return subprocess.run(['git', '-C', str(where), *args], capture_output=True, text=True, check=True, env=ENV).stdout.strip()


class Repo:
    """A work tree, and the bare copy githouse would hold of it at <mount>/<name>.git."""

    def __init__(self, mount, name='magenta'):
        self.work = mount / f'{name}-work'
        self.bare = mount / f'{name}.git'
        git(mount, 'init', '-q', '-b', 'main', str(self.work))
        self.commit('one')
        git(mount, 'clone', '-q', '--bare', str(self.work), str(self.bare))

    def commit(self, message):
        git(self.work, 'commit', '-q', '--allow-empty', '-m', message)
        return git(self.work, 'rev-parse', 'HEAD')

    def push(self):
        git(self.work, 'push', '-q', str(self.bare), '+refs/heads/*:refs/heads/*')

    def ref(self, name, sha):
        """A ref in the bare copy, with its objects (githouse fetched it: refs/pull/<n>/head, refs/merged/...)."""
        git(self.work, 'push', '-q', str(self.bare), f'+{sha}:{name}')


class GithouseTest(TestCase):

    def setUp(self):
        cache.clear()
        self.mount = Path(tempfile.mkdtemp())
        self.settings = override_settings(GITHOUSE_DIR=str(self.mount))
        self.settings.enable()
        self.addCleanup(self.settings.disable)

    def test_a_pull_request_is_merged_when_its_head_is_in_a_deploy_branch(self):
        repo = Repo(self.mount)
        git(repo.work, 'checkout', '-q', '-b', 're/foo')
        head = repo.commit('the fix')
        repo.ref('refs/pull/7/head', head)
        self.assertEqual(githouse.repositories(), ['magenta'])
        self.assertIs(githouse.pull_merged('magenta', 7), False)
        git(repo.work, 'checkout', '-q', 'main')
        git(repo.work, 'merge', '-q', '--no-ff', '-m', 'Merge pull request #7 from cryptograss/re/foo', 're/foo')
        repo.push()
        cache.clear()
        self.assertIs(githouse.pull_merged('magenta', 7), True)
        self.assertIs(githouse.commit_merged('magenta', head), True)

    def test_merged_into_production_counts_and_a_squash_does_not(self):
        repo = Repo(self.mount)
        git(repo.work, 'checkout', '-q', '-b', 'production')
        git(repo.work, 'checkout', '-q', '-b', 're/a')
        a = repo.commit('a')
        git(repo.work, 'checkout', '-q', 'production')
        git(repo.work, 'checkout', '-q', '-b', 're/b')
        b = repo.commit('b')
        repo.ref('refs/pull/1/head', a)
        repo.ref('refs/pull/2/head', b)
        git(repo.work, 'checkout', '-q', 'production')
        git(repo.work, 'merge', '-q', '--no-ff', '-m', 'merge a', 're/a')
        git(repo.work, 'merge', '-q', '--squash', 're/b')
        repo.commit('squashed b')
        repo.push()
        self.assertIs(githouse.pull_merged('magenta', 1), True)
        self.assertIs(githouse.pull_merged('magenta', 2), False)  # merge commits only, on every repository here

    def test_what_githouse_does_not_hold_is_none(self):
        repo = Repo(self.mount)
        self.assertIsNone(githouse.pull_merged('magenta', 99))      # no such pull request fetched
        self.assertIsNone(githouse.pull_merged('arthel', 1))        # no such repository here
        self.assertIsNone(githouse.pull_merged('../etc', 1))        # not a name
        self.assertIsNone(githouse.commit_merged('magenta', 'nonesuch'))
        self.assertIsNone(githouse.branch_merged('arthel', 're/x'))
        with override_settings(GITHOUSE_DIR=str(self.mount / 'nowhere')):
            self.assertEqual(githouse.repositories(), [])
            self.assertIsNone(githouse.pull_merged('magenta', 1))
        del repo

    def test_a_mergeable_is_merged_when_its_name_moved_to_refs_merged(self):
        repo = Repo(self.mount)
        sha = repo.commit('two')
        repo.ref('refs/heads/re/foo', sha)
        self.assertIs(githouse.branch_merged('magenta', 're/foo'), False)   # live
        self.assertIs(githouse.branch_merged('magenta', 're/never'), False)  # never pushed
        repo.ref('refs/closed/re/gone', sha)
        self.assertIs(githouse.branch_merged('magenta', 're/gone'), False)  # closed, not merged
        cache.clear()
        git(repo.bare, 'update-ref', '-d', 'refs/heads/re/foo')
        repo.ref('refs/merged/re/foo', sha)
        self.assertIs(githouse.branch_merged('magenta', 're/foo'), True)

    def test_the_merged_answer_is_kept_and_not_asked_again_within_the_day(self):
        repo = Repo(self.mount)
        head = repo.commit('two')
        repo.ref('refs/pull/3/head', head)
        repo.push()
        self.assertIs(githouse.pull_merged('magenta', 3), True)
        with mock.patch.object(githouse, 'commit_merged', side_effect=AssertionError('asked again')):
            self.assertIs(githouse.pull_merged('magenta', 3), True)

    def test_the_todo_lists_ask_githouse_before_github(self):
        from conversations.services.todo import _merged
        repo = Repo(self.mount)
        head = repo.commit('two')
        repo.ref('refs/pull/5/head', head)
        repo.push()

        class NoGitHub:
            def get(self, *a, **k):
                raise AssertionError('GitHub was asked')
        self.assertIs(_merged('cryptograss', 'magenta', 5, NoGitHub()), True)
        self.assertIs(_merged('magent-cryptograss', 'magenta', 5, NoGitHub()), True)  # the moved name is the same repo
        # A repository githouse doesn't hold: GitHub, as before.
        with mock.patch('conversations.services.todo._github', lambda path, http, **p: {'merged_at': 'now'}):
            self.assertIs(_merged('cryptograss', 'blue-railroad-import', 1, NoGitHub()), True)
