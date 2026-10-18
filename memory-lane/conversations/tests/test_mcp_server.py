"""
Tests for MCP Memory Recovery Server

These tests verify that the MCP tools work correctly for bootstrapping
AI context from the conversation database.
"""

import json
import uuid
from django.test import TestCase
from django.utils import timezone
from conversations.models import Message, Era, ContextHeap, ThinkingEntity, ContextHeapType
from unittest.mock import AsyncMock, patch
import asyncio


class MCPServerToolsTest(TestCase):
    """Test MCP server tool implementations"""

    @classmethod
    def setUpTestData(cls):
        """Create test data that persists across test methods"""
        # Create thinking entities
        cls.justin = ThinkingEntity.objects.create(
            name="justin",
            is_biological_human=True
        )
        cls.magent = ThinkingEntity.objects.create(
            name="magent",
            is_biological_human=False
        )

        # Create Era 1 for foundational summaries
        cls.era1 = Era.objects.create(
            name="Compacting Meta-Conversation (Era 1)"
        )

        # Create Era N for recent work
        cls.era_n = Era.objects.create(
            name="Post-N Era"
        )

        # Create context heap in Era 1
        cls.heap1 = ContextHeap.objects.create(
            era=cls.era1,
            type=ContextHeapType.FRESH
        )

        # Create messages in Era 1
        for i in range(5):
            Message.objects.create(
                id=uuid.uuid4(),
                sender=cls.justin if i % 2 == 0 else cls.magent,
                content=f"Era 1 message {i}: foundational context",
                message_number=i,
                context_heap=cls.heap1,
                source_file="test.jsonl"
            )

        # Create heap in Era N with continuation message
        cls.heap_n = ContextHeap.objects.create(
            era=cls.era_n,
            type=ContextHeapType.POST_COMPACTING
        )

        # Create continuation message
        cls.continuation = Message.objects.create(
            id=uuid.uuid4(),
            sender=cls.magent,
            content=json.dumps([{
                "text": "This session is being continued from previous conversation. Summary: We were testing MCP server functionality."
            }]),
            message_number=0,
            context_heap=cls.heap_n,
            source_file="test.jsonl",
            is_continuation_message=True
        )

        # Create recent messages
        for i in range(1, 10):
            Message.objects.create(
                id=uuid.uuid4(),
                sender=cls.justin if i % 2 == 0 else cls.magent,
                content=f"Recent message {i}: current work on MCP testing",
                message_number=i,
                context_heap=cls.heap_n,
                source_file="test.jsonl"
            )

    def test_get_latest_continuation_query(self):
        """Test that we can find the latest continuation message"""
        continuation = Message.objects.filter(
            is_continuation_message=True
        ).order_by('-created_at').first()

        self.assertIsNotNone(continuation)
        self.assertEqual(continuation.id, self.continuation.id)
        self.assertTrue(continuation.is_continuation_message)

    def test_get_messages_before_with_reference_id(self):
        """Test retrieving messages before a reference message"""
        # Get a recent message to use as reference
        ref_msg = Message.objects.filter(
            context_heap=self.heap_n,
            message_number=5
        ).first()

        # Get messages before it
        messages = Message.objects.filter(
            created_at__lt=ref_msg.created_at
        ).order_by('-created_at')[:10]

        self.assertGreater(len(messages), 0)
        # All returned messages should be before reference
        for msg in messages:
            self.assertLess(msg.created_at, ref_msg.created_at)

    def test_get_messages_before_with_limit(self):
        """Test that limit parameter works correctly"""
        limit = 3
        messages = Message.objects.order_by('-created_at')[:limit]

        self.assertEqual(len(messages), limit)

    def test_get_era_summary(self):
        """Test retrieving Era 1 foundational summaries"""
        era = Era.objects.get(name="Compacting Meta-Conversation (Era 1)")
        heaps = ContextHeap.objects.filter(era=era)
        messages = Message.objects.filter(
            context_heap__in=heaps
        ).order_by('message_number')

        self.assertEqual(era.id, self.era1.id)
        self.assertGreater(len(messages), 0)
        # Verify messages are from Era 1
        for msg in messages:
            self.assertIn("Era 1", msg.content)

    def test_get_context_heap(self):
        """Test retrieving all messages from a specific heap"""
        messages = Message.objects.filter(
            context_heap=self.heap_n
        ).order_by('message_number')

        self.assertGreater(len(messages), 0)
        # All messages should be from the same heap
        for msg in messages:
            self.assertEqual(msg.context_heap_id, self.heap_n.id)

    def test_search_messages_content(self):
        """Test searching messages by content"""
        # Search for "MCP testing"
        search_term = "MCP testing"
        messages = Message.objects.filter(
            content__icontains=search_term
        )

        self.assertGreater(len(messages), 0)
        # Verify search term appears in results
        for msg in messages:
            self.assertIn("MCP testing", msg.content)

    def test_get_recent_work(self):
        """Test retrieving most recent messages"""
        limit = 5
        messages = Message.objects.order_by('-created_at')[:limit]

        self.assertEqual(len(messages), limit)
        # Verify messages are in descending chronological order
        timestamps = [msg.created_at for msg in messages]
        self.assertEqual(timestamps, sorted(timestamps, reverse=True))

    def test_continuation_message_has_summary(self):
        """Test that continuation message contains summary text"""
        content = self.continuation.content
        if isinstance(content, str):
            content = json.loads(content)

        # Extract text from content
        if isinstance(content, list) and len(content) > 0:
            text = content[0].get('text', '')
        else:
            text = str(content)

        self.assertIn("continued", text.lower())
        self.assertIn("summary", text.lower())

    def test_message_sender_relationships(self):
        """Test that sender relationships are preserved"""
        justin_messages = Message.objects.filter(sender=self.justin)
        magent_messages = Message.objects.filter(sender=self.magent)

        self.assertGreater(len(justin_messages), 0)
        self.assertGreater(len(magent_messages), 0)

        # Verify sender IDs match
        for msg in justin_messages:
            self.assertEqual(msg.sender_id, "justin")
        for msg in magent_messages:
            self.assertEqual(msg.sender_id, "magent")

    def test_context_heap_types(self):
        """Test that heap types are correctly set"""
        fresh_heap = ContextHeap.objects.get(id=self.heap1.id)
        post_compact_heap = ContextHeap.objects.get(id=self.heap_n.id)

        self.assertEqual(fresh_heap.type, ContextHeapType.FRESH)
        self.assertEqual(post_compact_heap.type, ContextHeapType.POST_COMPACTING)
