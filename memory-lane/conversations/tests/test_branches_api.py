"""githouse's branches.json, served for the composer's repo:branch autocomplete."""

import json
import tempfile
from pathlib import Path
from unittest import mock

from django.test import TestCase, override_settings

NO_BOARD = mock.patch('conversations.services.ramp.board', lambda stale_ok=False: {})


@NO_BOARD
class BranchesApiTest(TestCase):

    def test_what_githouse_wrote(self):
        where = Path(tempfile.mkdtemp())
        (where / 'branches.json').write_text(json.dumps({'magenta': {'live': {'main': 'abc', 're/foo': 'def'}, 'merged': {}, 'closed': {}}}))
        with override_settings(GITHOUSE_DIR=str(where)):
            r = self.client.get('/api/branches/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['magenta']['live']['re/foo'], 'def')
        self.assertIn('max-age=60', r['Cache-Control'])

    def test_nothing_yet_is_an_empty_object(self):
        with override_settings(GITHOUSE_DIR=tempfile.mkdtemp()):
            r = self.client.get('/api/branches/')
        self.assertEqual((r.status_code, r.json()), (200, {}))

    def test_not_a_dict_is_an_empty_object(self):
        where = Path(tempfile.mkdtemp())
        (where / 'branches.json').write_text('[1, 2]')
        with override_settings(GITHOUSE_DIR=str(where)):
            self.assertEqual(self.client.get('/api/branches/').json(), {})


class PullRequestBranchesTest(TestCase):
    """An open pull request's branch that githouse doesn't hold is offered too (Justin, 13 Nov: ~todo-every-pr,
    magenta#436, opened on GitHub before the flip, wasn't)."""

    def get(self, board):
        where = Path(tempfile.mkdtemp())
        (where / 'branches.json').write_text(json.dumps({'magenta': {
            'live': {'main': 'abc', 're/foo': 'def'}, 'merged': {'magent/old': '111'}, 'closed': {}}}))
        with override_settings(GITHOUSE_DIR=str(where)), \
                mock.patch('conversations.services.ramp.board', lambda stale_ok=False: board):
            return self.client.get('/api/branches/').json()

    def test_a_pull_request_githouse_lacks_is_live(self):
        got = self.get({'open': [{'repo': 'magenta', 'number': 436, 'branch': 'magent/todo-every-pr',
                                  'state': 'open', 'head': '7abff29c'}]})
        self.assertEqual(got['magenta']['live']['magent/todo-every-pr'], '7abff29c')
        self.assertEqual(got['magenta']['pulls'], {'magent/todo-every-pr': 436})
        self.assertEqual(got['magenta']['live']['re/foo'], 'def')  # githouse's own, as written

    def test_one_githouse_has_says_what_githouse_says(self):
        got = self.get({'open': [{'repo': 'magenta', 'number': 7, 'branch': 'magent/old', 'state': 'open', 'head': 'x'},
                                 {'repo': 'magenta', 'number': 8, 'branch': 're/foo', 'state': 'open', 'head': 'y'}]})
        self.assertNotIn('magent/old', got['magenta']['live'])  # merged, as githouse has it
        self.assertEqual(got['magenta']['live']['re/foo'], 'def')

    def test_merged_and_branchless_and_unnumbered_are_left_out(self):
        got = self.get({'open': [{'repo': 'magenta', 'number': 9, 'branch': 'magent/gone', 'state': 'merged'},
                                 {'repo': 'magenta', 'number': 10, 'branch': '', 'state': 'open'},
                                 {'repo': 'magenta', 'number': None, 'branch': 're/pushed', 'state': 'pushed'}]})
        self.assertEqual(set(got['magenta']['live']), {'main', 're/foo'})

    def test_a_repository_githouse_lacks_gets_its_own_entry(self):
        got = self.get({'open': [{'repo': 'arthel', 'number': 159, 'branch': 'kuba', 'state': 'open', 'head': 'z'}]})
        self.assertEqual(got['arthel'], {'live': {'kuba': 'z'}, 'pulls': {'kuba': 159}})

    def test_a_board_that_fails_leaves_githouse_list(self):
        def broken(stale_ok=False):
            raise RuntimeError('no board')
        where = Path(tempfile.mkdtemp())
        (where / 'branches.json').write_text(json.dumps({'magenta': {'live': {'re/foo': 'def'}}}))
        with override_settings(GITHOUSE_DIR=str(where)), mock.patch('conversations.services.ramp.board', broken):
            self.assertEqual(self.client.get('/api/branches/').json(), {'magenta': {'live': {'re/foo': 'def'}}})
