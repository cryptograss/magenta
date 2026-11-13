"""Tidings follow the PickiPedia pages that moved before magenta listened for moves (tiding_links.follow_move).

From now on the push and the minute sweep do this as a page moves; this reads the wiki's move log, oldest
first, for the ones already made. Idempotent: run it once after the deploy that adds Tiding.moved_to.
"""

import requests
from django.core.management.base import BaseCommand

from conversations.services.mood_view import pickipedia_url
from conversations.services.tiding_links import follow_move


class Command(BaseCommand):
    help = "Point tidings at the PickiPedia pages they moved to (the wiki's move log, newest LIMIT moves)"

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=500)

    def handle(self, *args, limit=500, **options):
        answer = requests.get(f'{pickipedia_url()}/api.php', timeout=20,
                              headers={'User-Agent': 'memory-lane (magenta; follow moves)'},
                              params={'action': 'query', 'list': 'logevents', 'letype': 'move',
                                      'leprop': 'title|details|timestamp', 'lelimit': min(limit, 500),
                                      'format': 'json'})
        moves = answer.json()['query']['logevents']
        followed = 0
        for move in reversed(moves):
            new = (move.get('params') or {}).get('target_title')
            if move.get('title') and new:
                n = follow_move(move['title'], new)
                if n:
                    self.stdout.write(f"{move['title']} → {new}: {n} tiding(s)")
                followed += n
        self.stdout.write(f'{len(moves)} moves read, {followed} tiding(s) followed')
