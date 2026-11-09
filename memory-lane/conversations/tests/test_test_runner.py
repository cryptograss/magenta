"""The test runner's own promises (memory_viewer/test_runner.py)."""

import os

from django.conf import settings
from django.core.cache import cache
from django.test import SimpleTestCase


class OwnCacheTest(SimpleTestCase):

    def test_the_cache_is_this_runs_own(self):
        # Not the machine's /tmp/memory-lane-cache, which every memory-lane process shares and any of them clears:
        # a directory made for this run (re, 9 Nov).
        where = os.path.realpath(cache._dir)
        self.assertNotEqual(where, os.path.realpath(os.getenv('MEMORY_LANE_CACHE_DIR', '/tmp/memory-lane-cache')))
        self.assertIn('memory-lane-test-cache-', os.path.basename(where))
        self.assertEqual(os.path.realpath(settings.CACHES['default']['LOCATION']), where)
        cache.set('re:own', 1)
        self.assertEqual(cache.get('re:own'), 1)
