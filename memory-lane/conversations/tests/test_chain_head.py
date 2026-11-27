"""The chain's head as it lands (services/chain_head, manage.py watch_head; Justin, 17 Nov, #interfaces)."""

import json
from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from conversations.models import ChainHead
from conversations.services import chain_head


def block(n, ts):
    return {'number': hex(n), 'timestamp': hex(ts)}


class FakeNode:
    """A node over HTTP: answers eth_getBlockByNumber, one or a batch, from `blocks` {number: ts}."""

    def __init__(self, blocks, down=()):
        self.blocks, self.down, self.asked = dict(blocks), set(down), []

    def post(self, url, json=None, **kw):
        self.asked.append((url, json))
        if url in self.down:
            raise OSError('down')

        def one(req):
            which = req['params'][0]
            n = max(self.blocks) if which == 'latest' else int(which, 16)
            return {'id': req['id'], 'result': block(n, self.blocks[n]) if n in self.blocks else None}
        body = [one(r) for r in json] if isinstance(json, list) else one(json)
        return mock.Mock(json=lambda: body, raise_for_status=lambda: None)


class FakeSocket:
    """A newHeads subscription that delivers `heads`, then goes quiet (recv times out)."""

    def __init__(self, heads, refuse=False):
        self.heads, self.refuse, self.sent = list(heads), refuse, []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def send(self, text):
        self.sent.append(json.loads(text))

    def recv(self, timeout=None):
        if len(self.sent) == 1 and not hasattr(self, 'subscribed'):
            self.subscribed = True
            return json.dumps({'id': 1, 'result': None if self.refuse else '0xabc'})
        if not self.heads:
            raise TimeoutError('quiet')
        n, ts = self.heads.pop(0)
        return json.dumps({'method': 'eth_subscription', 'params': {'subscription': '0xabc', 'result': block(n, ts)}})


class ReadingTest(TestCase):

    def test_the_head_is_fresh_for_a_minute_and_says_how_it_came(self):
        now = timezone.now()
        chain_head.record(26_437_277, 1_763_400_000, 'polling', now=now)
        head = chain_head.latest(now + timedelta(seconds=30))
        self.assertEqual((head['height'], head['via'], head['at']), (26_437_277, 'polling', '2025-11-17T17:20:00Z'))
        self.assertIsNone(chain_head.latest(now + timedelta(seconds=61)))  # the watcher's stopped: no head, not a stale one

    def test_a_moment_is_its_block_only_where_no_block_is_missing(self):
        now = timezone.now()
        for k in (0, 1, 2, 4, 5):  # 103 missing
            ChainHead.objects.create(number=100 + k, timestamp=1000 + 12 * k, seen_at=now, via='socket')
        self.assertEqual(chain_head.block_at(1013), 101)
        self.assertEqual(chain_head.block_at(1024), 102)  # exactly a block's time: that block
        self.assertIsNone(chain_head.block_at(1030))  # 102 or 103? the rows can't say
        self.assertIsNone(chain_head.block_at(999))   # before the rows
        self.assertIsNone(chain_head.block_at(2000))  # after the newest: nothing after it to bound it
        self.assertEqual(chain_head.blocks_since(1013, now), 4)  # 101 to 105

    def test_a_day_is_kept(self):
        now = timezone.now()
        ChainHead.objects.create(number=1000, timestamp=1, seen_at=now, via='socket')
        ChainHead.objects.create(number=3000, timestamp=2, seen_at=now, via='socket')
        chain_head.record(1000 + chain_head.KEEP_BLOCKS + 100, 3, 'socket', now)  # every hundredth block prunes
        self.assertEqual(sorted(ChainHead.objects.values_list('number', flat=True)), [3000, 1000 + chain_head.KEEP_BLOCKS + 100])


class WatcherTest(TestCase):

    def watcher(self, node, sockets=(), urls=('https://a.example/', 'https://b.example/')):
        sockets = list(sockets)

        def connect(url):
            if not sockets:
                raise OSError('refused')
            return sockets.pop(0)
        t = [0.0]

        def sleep(s):
            t[0] += s
        return chain_head.Watcher(connect=connect, http=node, sleep=sleep, clock=lambda: t[0],
                                  ws='wss://a.example/', urls=list(urls))

    def test_on_the_socket_each_block_is_kept_as_pushed_and_a_quiet_socket_counts_as_dropped(self):
        w = self.watcher(FakeNode({}), [FakeSocket([(500, 6000), (501, 6012)])])
        with self.assertRaises(TimeoutError):
            w.on_socket()
        self.assertEqual(list(ChainHead.objects.values_list('number', 'via')), [(500, 'socket'), (501, 'socket')])

    def test_a_refused_subscription_is_a_drop(self):
        w = self.watcher(FakeNode({}), [FakeSocket([], refuse=True)])
        with self.assertRaises(RuntimeError):
            w.on_socket()

    def test_a_skipped_block_is_fetched_so_the_rows_stay_whole(self):
        node = FakeNode({n: 6000 + 12 * (n - 500) for n in range(500, 510)})
        w = self.watcher(node, [FakeSocket([(500, 6000), (504, 6048)])])
        with self.assertRaises(TimeoutError):
            w.on_socket()
        self.assertEqual(list(ChainHead.objects.values_list('number', flat=True)), [500, 501, 502, 503, 504])
        self.assertEqual(chain_head.block_at(6030), 502)

    def test_a_gap_wider_than_an_hour_s_worth_is_left(self):
        node = FakeNode({n: n for n in range(500, 700)})
        w = self.watcher(node, [FakeSocket([(500, 500), (600, 600)])])
        with self.assertRaises(TimeoutError):
            w.on_socket()
        self.assertEqual(list(ChainHead.objects.values_list('number', flat=True)), [500, 600])
        self.assertEqual(node.asked, [])

    def test_with_the_socket_down_it_polls_every_12_s_from_the_first_node_that_answers(self):
        node = FakeNode({700: 9000}, down={'https://a.example/'})
        w = self.watcher(node)
        w.run(rounds=1)
        self.assertEqual(list(ChainHead.objects.values_list('number', 'via')), [(700, 'polling')])
        self.assertEqual(len([a for a in node.asked if a[0] == 'https://b.example/']), 60 // 12)  # a minute, every 12 s
        self.assertEqual(w.retry, 120)  # and the socket tried again after longer each time it fails

    def test_back_on_the_socket_the_head_says_so(self):
        node = FakeNode({700: 9000})  # polling only ever finds 700; 701 comes on the socket
        w = self.watcher(node, [FakeSocket([]), FakeSocket([(701, 9012)])])
        w.run(rounds=2)
        self.assertEqual(dict(ChainHead.objects.values_list('number', 'via')), {700: 'polling', 701: 'socket'})
        self.assertEqual(chain_head.latest()['via'], 'socket')

    def test_polling_outlasts_a_database_that_isn_t_ready(self):
        # A deploy starts this container before memory-lane runs the migration that makes its table.
        node = FakeNode({700: 9000})
        w = self.watcher(node)
        real = chain_head.record
        calls = []

        def flaky(*a, **kw):
            calls.append(a)
            if len(calls) == 1:
                raise RuntimeError('no such table: chain_heads')
            return real(*a, **kw)
        with mock.patch.object(chain_head, 'record', flaky), self.assertLogs('conversations.services.chain_head', 'WARNING'):
            w.poll_for(24)
        self.assertEqual(list(ChainHead.objects.values_list('number', flat=True)), [700])

    def test_a_keyed_node_is_never_named_whole_in_a_log(self):
        node = FakeNode({}, down={'https://eth-mainnet.g.alchemy.com/v2/SECRETKEY'})
        w = self.watcher(node, urls=['https://eth-mainnet.g.alchemy.com/v2/SECRETKEY'])
        with self.assertLogs('conversations.services.chain_head', 'WARNING') as logs:
            w.poll_for(1)
        self.assertNotIn('SECRETKEY', '\n'.join(logs.output))
        self.assertIn('eth-mainnet.g.alchemy.com', '\n'.join(logs.output))


class EndpointTest(TestCase):

    def setUp(self):
        cache.clear()

    def test_the_block_comes_from_the_watcher_and_says_how_and_anyone_may_read_it(self):
        chain_head.record(26_437_300, 1_763_400_000, 'socket')
        with mock.patch('requests.get', side_effect=AssertionError('the explorer is not asked')):
            r = self.client.get('/api/block/')
        self.assertEqual((r.json()['height'], r.json()['via']), (26_437_300, 'socket'))
        self.assertEqual(r['Access-Control-Allow-Origin'], '*')  # PickiPedia's pages read it too

    def test_with_the_watcher_quiet_the_explorer_s(self):
        ChainHead.objects.create(number=26_437_000, timestamp=1, seen_at=timezone.now() - timedelta(minutes=5), via='socket')
        head = mock.Mock(json=lambda: [{'height': 26113794, 'timestamp': '2026-10-03T19:06:23Z'}])
        with mock.patch('requests.get', return_value=head):
            b = self.client.get('/api/block/').json()
        self.assertEqual((b['height'], b['via']), (26113794, 'explorer'))

    def test_every_live_poll_carries_the_head(self):
        chain_head.record(26_437_301, 1_763_400_012, 'polling')
        self.assertEqual(self.client.get('/api/moods/live/').json()['head']['via'], 'polling')


class OneHeadTest(TestCase):
    """Everything in magenta that wants the chain's height now reads the watcher's rows (Justin, 18 Nov:
    "we're not repeating a lookup anywhere, right?"), and falls back only when the watcher is quiet."""

    def rows(self, first, count, at, seen=None):
        seen = seen or timezone.now()
        for k in range(count):
            ChainHead.objects.create(number=first + k, timestamp=at + 12 * k, seen_at=seen, via='socket')

    def test_the_height_now_is_the_newest_block_and_slots_after_it(self):
        now = timezone.now()
        self.rows(500, 3, int(now.timestamp()) - 30)  # 502 landed 6 s ago
        self.assertEqual(chain_head.height_at(now), 502)
        self.assertEqual(chain_head.height_at(now + timedelta(seconds=13)), 503)  # a block on, counted
        self.assertEqual(chain_head.height_at(now - timedelta(seconds=20)), 500)  # a moment the rows cover: 501 lands 18 s ago
        self.assertIsNone(chain_head.height_at(now + timedelta(minutes=5)))  # the watcher quiet: not guessed

    def test_tasks_on_a_block_schedule_take_the_watcher_s_height_before_the_anchors(self):
        from conversations.services import tasks
        now = timezone.now()
        self.rows(26_000_000, 2, int(now.timestamp()) - 12)
        stale_anchor = [(25_000_000, int(now.timestamp()) - 60)]
        self.assertEqual(tasks.head(now, stale_anchor), 26_000_001)
        ChainHead.objects.all().delete()
        self.assertEqual(tasks.head(now, stale_anchor), 25_000_005)  # the watcher's gone: the anchors, as before

    def test_a_video_draft_takes_the_watcher_s_block_without_asking_a_node(self):
        from conversations.services import delivery_kid
        now = timezone.now()
        self.rows(26_442_600, 1, int(now.timestamp()) - 3)
        with mock.patch('conversations.services.eth_blocks.fetch_head', side_effect=AssertionError('asked a node')):
            self.assertEqual(delivery_kid._block_now(), 26_442_600)

    def test_the_ramp_counts_a_recent_push_from_the_rows(self):
        from conversations.services import ramp
        now = timezone.now()
        t = int(now.timestamp())
        self.rows(700, 31, t - 360)  # a block every 12 s for the last six minutes, 730 the newest
        pushed = now - timedelta(seconds=100)
        items = [{'pushed': pushed}]
        top = ramp.with_blocks(items, now)
        self.assertEqual(top, 730)
        self.assertEqual(items[0]['pushed_block'], chain_head.block_at(int(pushed.timestamp())))
        self.assertEqual(items[0]['pushed_ago'], 730 - items[0]['pushed_block'])
