"""The test runner: Django's, with the network shut and no background thread left writing into a test's database
(magenta#104: a full run on 21 Oct failed 4 tests and errored 4, and never again; the likeliest cause was a
Yarn fetch's thread writing into whichever test was running when Yarn answered).

- Every outgoing connection and name lookup is refused, but for this machine and the database's host: a test
  that needs the network fails every time, saying where it reached, instead of now and then. Set
  MEMORY_LANE_TEST_NETWORK=1 to let it out (to see what reaches it, say).
- A Yarn clip said in a post is kept in the background (yarn_kept.keep_later): not in tests, whose own
  tests call yarn_kept.keep with a fake Yarn.
"""

import os
import socket

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
        from conversations.services import yarn_kept
        self._keep_later = yarn_kept.keep_later
        yarn_kept.keep_later = lambda text: None

    def teardown_test_environment(self, **kwargs):
        from conversations.services import yarn_kept
        yarn_kept.keep_later = self._keep_later
        if self._reopen:
            self._reopen()
        super().teardown_test_environment(**kwargs)
