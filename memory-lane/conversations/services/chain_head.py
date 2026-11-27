"""The chain's head, block by block, as it lands (Justin, 17 Nov, #interfaces: "have blocks get pushed
to magenta / pickipedia all the time, every 12ish seconds").

`manage.py watch_head` keeps a newHeads subscription open on a public node, so each block is pushed
to us as it lands. When the socket drops it asks for the newest block every 12 s instead, and tries
the socket again now and then. Each block is a ChainHead row, marked with how it came: 'socket' or
'polling'. That mark is the indicator Justin asked for: pages show which one is feeding them.

The rows are contiguous: a block skipped (a reconnect, a poll that straddled two blocks) is fetched
and filled in. So a moment's block is a lookup, not an estimate, and "15 blocks in" is a count.
The last day is kept; older rows go.

Nothing here is the record's clock. eth_blocks' BlockAnchor rows are finalized, so a reorg can't
change them; these are the newest blocks. Where they can't answer (the watcher down, a moment older
than a day), callers fall back to counting 12 s a block, as before.
"""

import json
import logging
import os
import time

import requests

logger = logging.getLogger(__name__)

SECONDS_PER_SLOT = 12
KEEP_BLOCKS = 7200          # a day of blocks
FRESH_SECONDS = 60          # a head older than this: the watcher isn't feeding us
FILL_AT_MOST = 64           # a gap wider than this is left unfilled (the watcher was down; nothing to count across)
DEFAULT_WS_URL = 'wss://ethereum-rpc.publicnode.com'


def ws_url():
    return os.environ.get('ETH_WS_URL', DEFAULT_WS_URL)


def http_urls():
    """The nodes to ask, in order: ETH_RPC_URL (eth_blocks' default, the same public node), then
    ETH_RPC_FALLBACK_URL if there's one (a keyed provider; never logged, as it carries its key)."""
    from .eth_blocks import rpc_url
    urls = [rpc_url()]
    fallback = os.environ.get('ETH_RPC_FALLBACK_URL', '').strip()
    if fallback:
        urls.append(fallback)
    return urls


def host_of(url):
    """A node's host and nothing else: what a log line may say about it."""
    from urllib.parse import urlsplit
    return urlsplit(url).hostname or '?'


# --- writing ---

def record(number, timestamp, via, now=None):
    """Keep one block. A number seen again (a reorg, or the socket and a fill both bringing it) is
    rewritten. Prunes what's older than a day."""
    from django.utils import timezone
    from conversations.models import ChainHead
    now = now or timezone.now()
    ChainHead.objects.update_or_create(number=number, defaults={'timestamp': timestamp, 'seen_at': now, 'via': via})
    if number % 100 == 0:
        ChainHead.objects.filter(number__lt=number - KEEP_BLOCKS).delete()


def newest_number():
    from conversations.models import ChainHead
    return ChainHead.objects.order_by('-number').values_list('number', flat=True).first()


# --- reading ---

def latest(now=None):
    """{'height', 'at', 'seconds', 'via', 'seen'} for the newest block, if the watcher heard of it in the
    last minute; else None. 'at' is the block's own time, ISO; 'seen' when we heard of it."""
    from datetime import datetime, timezone as tz
    from django.utils import timezone
    from conversations.models import ChainHead
    now = now or timezone.now()
    head = ChainHead.objects.order_by('-number').first()
    if head is None or (now - head.seen_at).total_seconds() > FRESH_SECONDS:
        return None
    return {'height': head.number, 'at': datetime.fromtimestamp(head.timestamp, tz.utc).isoformat().replace('+00:00', 'Z'),
            'seconds': SECONDS_PER_SLOT, 'via': head.via, 'seen': head.seen_at.isoformat()}


def block_at(ts):
    """The block at the head at unix time ts: the newest block whose time is at or before it. Only
    where the rows can say for sure, a block after it on hand and none missing between; else None."""
    from conversations.models import ChainHead
    before = ChainHead.objects.filter(timestamp__lte=ts).order_by('-number').values_list('number', 'timestamp').first()
    if before is None:
        return None
    if before[1] == ts:  # the moment it landed: no later block shares its time
        return before[0]
    after = ChainHead.objects.filter(timestamp__gt=ts).order_by('number').values_list('number', flat=True).first()
    return before[0] if after == before[0] + 1 else None


def height_at(now=None):
    """The chain's height at `now`, from the rows: the block itself for a moment they cover, else the
    newest block and 12 s slots after it, while the watcher is feeding us. None when it isn't."""
    from django.utils import timezone
    from conversations.models import ChainHead
    now = now or timezone.now()
    if latest(now) is None:
        return None
    number, at = ChainHead.objects.order_by('-number').values_list('number', 'timestamp').first()
    ts = int(now.timestamp())
    if ts < at:
        return block_at(ts)
    return number + (ts - at) // SECONDS_PER_SLOT


def blocks_since(ts, now=None):
    """How many blocks have landed since unix time ts, counted, not estimated; None if it can't be
    counted (the watcher not feeding us, or ts outside the day kept)."""
    head = latest(now)
    if head is None:
        return None
    start = block_at(ts)
    if start is None:
        return None
    return max(0, head['height'] - start)


# --- the node ---

def _rpc(url, payload, http=requests):
    response = http.post(url, json=payload, timeout=10, headers={'User-Agent': 'memory-lane'})
    response.raise_for_status()
    return response.json()


def fetch_latest(urls, http=requests):
    """(number, timestamp) of the newest block, from the first node that answers; None if none does."""
    for url in urls:
        try:
            reply = _rpc(url, {'jsonrpc': '2.0', 'id': 1, 'method': 'eth_getBlockByNumber', 'params': ['latest', False]}, http)
            block = reply.get('result') if isinstance(reply, dict) else None
            if block:
                return int(block['number'], 16), int(block['timestamp'], 16)
            logger.warning(f"head: {host_of(url)} gave no block: {str(reply)[:200]}")
        except Exception as e:  # noqa: BLE001 -- the next node, then the next poll
            logger.warning(f"head: {host_of(url)}: {type(e).__name__}: {str(e)[:200]}")
    return None


def fetch_range(urls, numbers, http=requests):
    """[(number, timestamp)] for the given blocks, from the first node that answers the batch."""
    payload = [{'jsonrpc': '2.0', 'id': n, 'method': 'eth_getBlockByNumber', 'params': [hex(n), False]} for n in numbers]
    for url in urls:
        try:
            replies = _rpc(url, payload, http)
            if isinstance(replies, list):
                return [(int(r['result']['number'], 16), int(r['result']['timestamp'], 16))
                        for r in replies if isinstance(r, dict) and r.get('result')]
        except Exception as e:  # noqa: BLE001
            logger.warning(f"head: filling from {host_of(url)}: {type(e).__name__}: {str(e)[:200]}")
    return []


class Watcher:
    """Keeps the head: on the socket while it holds, polling while it doesn't.

    `connect` opens a websocket (websockets.sync.client.connect, or a stand-in in tests); `http` posts
    (requests, or a stand-in); `sleep` and `clock` are time's, or a test's."""

    POLL_SECONDS = SECONDS_PER_SLOT
    QUIET_SECONDS = 60          # no head on the socket for this long: it's dropped, whatever it says
    RETRY_FIRST = 60            # back to the socket after a minute of polling, then
    RETRY_MOST = 600            # less and less often, ten minutes at most

    def __init__(self, connect=None, http=requests, sleep=time.sleep, clock=time.monotonic,
                 ws=None, urls=None):
        self.connect = connect
        self.http = http
        self.sleep = sleep
        self.clock = clock
        self.ws = ws or ws_url()
        self.urls = urls or http_urls()
        self.last = None
        self.retry = self.RETRY_FIRST

    def take(self, number, timestamp, via):
        """A block heard of: kept, and any skipped since the last one fetched and kept with it."""
        from django.db import close_old_connections
        close_old_connections()  # a long-lived process: a connection the database dropped is reopened, not reused
        last = self.last if self.last is not None else newest_number()
        if last is not None and 1 < number - last <= FILL_AT_MOST:
            for n, t in fetch_range(self.urls, list(range(last + 1, number)), self.http):
                record(n, t, via)
        record(number, timestamp, via)  # a reorg's block, or one arriving late, too: kept, rewriting its row
        self.last = number if last is None else max(last, number)

    def on_socket(self):
        """Hold the subscription until it drops or goes quiet (raising, either way)."""
        with self.connect(self.ws) as sock:
            sock.send(json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'eth_subscribe', 'params': ['newHeads']}))
            reply = json.loads(sock.recv(timeout=10))
            if not reply.get('result'):
                raise RuntimeError(f"subscription refused: {str(reply)[:200]}")
            logger.info(f"head: on the socket at {host_of(self.ws)}")
            while True:
                message = json.loads(sock.recv(timeout=self.QUIET_SECONDS))
                head = (message.get('params') or {}).get('result') or {}
                if 'number' in head:
                    self.take(int(head['number'], 16), int(head['timestamp'], 16), 'socket')
                    self.retry = self.RETRY_FIRST

    def poll_for(self, seconds):
        """Ask for the newest block every 12 s, for about `seconds`."""
        until = self.clock() + seconds
        while self.clock() < until:
            got = fetch_latest(self.urls, self.http)
            try:
                if got:
                    self.take(got[0], got[1], 'polling')
            except Exception as e:  # noqa: BLE001 -- the database away (a deploy, its migration not run yet): the next poll
                logger.warning(f"head: couldn't keep {got[0]}: {type(e).__name__}: {str(e)[:200]}")
            self.sleep(self.POLL_SECONDS)

    def run(self, rounds=None):
        """Socket, and when it fails, polling until it's time to try the socket again. Forever, or `rounds` times."""
        n = 0
        while rounds is None or n < rounds:
            n += 1
            try:
                self.on_socket()
            except Exception as e:  # noqa: BLE001 -- whatever broke it, polling carries on
                logger.warning(f"head: socket dropped ({type(e).__name__}: {str(e)[:200]}); polling for {self.retry} s")
            self.poll_for(self.retry)
            self.retry = min(self.retry * 2, self.RETRY_MOST)

