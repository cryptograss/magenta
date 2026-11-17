"""Keep the chain's head, block by block (conversations/services/chain_head.py).

Runs forever, as its own container beside memory-lane (maybelle-config's compose file): a newHeads
subscription on the public node, and polling every 12 s whenever that drops.
"""

import logging

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Keep the chain's head as it lands: pushed by a websocket, or asked for every 12 s when that drops"

    def handle(self, *args, **options):
        from websockets.sync.client import connect
        from conversations.services.chain_head import Watcher
        logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
        # No proxy from the environment, and no user agent naming the Python: the node needn't know more.
        Watcher(connect=lambda url: connect(url, open_timeout=10, proxy=None, user_agent_header='memory-lane')).run()
