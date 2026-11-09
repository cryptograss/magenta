"""The test runner: Django's, with the network shut and no background thread left writing into a test's database
(magenta#104: a full run on 21 Oct failed 4 tests and errored 4, and never again; the likeliest cause was a
Yarn fetch's thread writing into whichever test was running when Yarn answered).

- Every outgoing connection and name lookup is refused, but for this machine and the database's host: a test
  that needs the network fails every time, saying where it reached, instead of now and then. Set
  MEMORY_LANE_TEST_NETWORK=1 to let it out (to see what reaches it, say).
- A Yarn clip said in a post is kept in the background (yarn_kept.keep_later): not in tests, whose own
  tests call yarn_kept.keep with a fake Yarn.
- githouse's mount (settings.GITHOUSE_DIR) is an empty directory: in a Mood container /srv/git is hunter's
  real mirror, and a test reading it would find #313 merged. A test that wants one makes its own.
- The cache is the run's own: a file cache in a fresh directory, gone at the end. settings.CACHES is a file
  cache at /tmp/memory-lane-cache, shared by every memory-lane process on the machine, and dozens of tests
  cache.clear() it: another run, a runserver or a shell emptied this run's entries mid-test (test_moods'
  cache tests failed now and then on 9 Nov; re reproduced it by emptying the directory beside a run).
"""

import os
import socket
import tempfile

from django.conf import settings
from django.test.runner import DiscoverRunner


class NetworkBlocked(OSError):
    pass


def _allowed_hosts():
    hosts = {'localhost', '127.0.0.1', '::1', '0.0.0.0', ''}
    for db in settings.DATABASES.values():
        if db.get('HOST'):
            hosts.add(db['HOST'])
    return hosts


def shut_network():
    """Refuse every connection and lookup but the allowed hosts'. Returns a function that opens it again."""
    allowed = _allowed_hosts()
    connect, connect_ex, getaddrinfo = socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo

    def check(address):
        host = address[0] if isinstance(address, tuple) else None
        if host is not None and host not in allowed:
            raise NetworkBlocked(f'the network is shut in tests (memory_viewer/test_runner.py): reached {host}')

    def guarded_connect(self, address):
        check(address)
        return connect(self, address)

    def guarded_connect_ex(self, address):
        check(address)
        return connect_ex(self, address)

    def guarded_getaddrinfo(host, *args, **kwargs):
        check((host.decode() if isinstance(host, bytes) else host or '', 0))
        return getaddrinfo(host, *args, **kwargs)

    socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo = (
        guarded_connect, guarded_connect_ex, guarded_getaddrinfo)

    def reopen():
        socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo = connect, connect_ex, getaddrinfo
    return reopen


class Runner(DiscoverRunner):

    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        self._reopen = shut_network() if os.environ.get('MEMORY_LANE_TEST_NETWORK') != '1' else None
        settings.GITHOUSE_DIR = tempfile.mkdtemp(prefix='githouse-none-')
        from django.test.utils import override_settings
        self._cache_dir = tempfile.mkdtemp(prefix='memory-lane-test-cache-')
        own = {**settings.CACHES['default'], 'LOCATION': self._cache_dir}
        self._own_cache = override_settings(CACHES={**settings.CACHES, 'default': own})
        self._own_cache.enable()  # Django's setting_changed resets the cache handlers, so caches['default'] is this
        from conversations.services import yarn_kept
        self._keep_later = yarn_kept.keep_later
        yarn_kept.keep_later = lambda text: None

    def teardown_test_environment(self, **kwargs):
        from conversations.services import yarn_kept
        yarn_kept.keep_later = self._keep_later
        if self._reopen:
            self._reopen()
        self._own_cache.disable()
        import shutil
        shutil.rmtree(self._cache_dir, ignore_errors=True)
        shutil.rmtree(settings.GITHOUSE_DIR, ignore_errors=True)
        super().teardown_test_environment(**kwargs)
