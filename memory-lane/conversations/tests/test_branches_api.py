"""githouse's branches.json, served for the composer's repo:branch autocomplete."""

import json
import tempfile
from pathlib import Path

from django.test import TestCase, override_settings


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
