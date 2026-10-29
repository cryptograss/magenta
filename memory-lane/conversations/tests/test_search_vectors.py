"""Search narrowing, post-0042: case-insensitive, index-shaped.

api_search narrows through lower(content::text) (pg_trgm GIN serves that
LIKE on PostgreSQL; SQLite exercises the same SQL). search_messages falls
back to a scan on non-PostgreSQL. The GIN indexes themselves are
PostgreSQL-only (guarded in 0042_search_vectors) and are verified on the
deployed database; this module pins the query shapes that must survive.
"""

import uuid

from django.db import connection
from django.test import TestCase

from conversations.models import (
    Message,
    Mood,
    ThinkingEntity,
)
from conversations.services.memory import MemoryService


def add_message(mood, sender, text):
    return Message.objects.create(
        id=uuid.uuid4(),
        mood=mood,
        sender=sender,
        content={'type': 'text', 'text': text},
    )


class SearchNarrowingTest(TestCase):
    """The lower(...) + contains shape api_search now narrows with."""

    @classmethod
    def setUpTestData(cls):
        cls.mood = Mood.objects.create(slug='search-test')
        cls.justin = ThinkingEntity.objects.update_or_create(
            name='justin', defaults={'is_biological_human': True})[0]
        cls.msg = add_message(cls.mood, cls.justin, 'Salt Creek. Played it twice.')

    def test_case_insensitive_narrowing(self):
        """The index shape lower(content::text) LIKE '%term%' still finds the
        message from a different casing."""
        from django.db.models import TextField
        from django.db.models.functions import Cast, Lower

        rows = (Message.objects.filter(mood=self.mood)
                .annotate(ltxt=Lower(Cast('content', TextField())))
                .filter(ltxt__contains='salt'))
        self.assertEqual(list(rows), [self.msg])


class SearchMessagesFallbackTest(TestCase):
    """search_messages on non-PostgreSQL is a bounded scan, not a crash."""

    def test_scan_fallback(self):
        if connection.vendor == 'postgresql':
            self.skipTest('postgres uses the stored-vector path')
        mood = Mood.objects.create(slug='search-fallback')
        sender = ThinkingEntity.objects.update_or_create(
            name='search-fallback-writer', defaults={'is_biological_human': False})[0]
        add_message(mood, sender, 'The vector build scans here; it must not raise.')
        hits = MemoryService.search_messages('vector')
        self.assertTrue(hits)
        self.assertEqual(hits[0].content['text'], 'The vector build scans here; it must not raise.')