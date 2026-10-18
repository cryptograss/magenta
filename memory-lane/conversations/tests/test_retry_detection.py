"""
Tests for retry detection logic.
"""

from django.test import TestCase
from conversations.utils.retry_detection import RetryDetector


class RetryDetectorTest(TestCase):
    """Test retry detection logic."""

    def test_normalize_content_collapses_whitespace(self):
        """Whitespace normalization handles newlines and multiple spaces."""
        detector = RetryDetector()

        # Newlines and extra spaces
        content1 = "Great.  Let's go in a bit\nof a weird direction"
        content2 = "Great.  Let's go in a bit  \n  of a weird direction"

        self.assertEqual(
            detector.normalize_content(content1),
            detector.normalize_content(content2)
        )

    def test_first_message_not_retry(self):
        """First message from a sender is never a retry."""
        detector = RetryDetector()

        is_retry = detector.is_retry(
            sender="justin",
            content="Hello world"
        )

        self.assertFalse(is_retry)

    def test_different_content_not_retry(self):
        """Different content is not a retry."""
        detector = RetryDetector()

        detector.is_retry(sender="justin", content="First message")
        is_retry = detector.is_retry(sender="justin", content="Second message")

        self.assertFalse(is_retry)

    def test_same_content_is_retry(self):
        """Same content from same sender is a retry."""
        detector = RetryDetector()

        detector.is_retry(sender="justin", content="Hello world")
        is_retry = detector.is_retry(sender="justin", content="Hello world")

        self.assertTrue(is_retry)

    def test_same_content_whitespace_variations_is_retry(self):
        """Same content with whitespace differences is still a retry."""
        detector = RetryDetector()

        detector.is_retry(sender="justin", content="Hello world")
        is_retry = detector.is_retry(
            sender="justin",
            content="Hello\n  world"  # Newline and extra spaces
        )

        self.assertTrue(is_retry)

    def test_synthetic_errors_dont_break_retry_chain(self):
        """Synthetic errors between retries don't break the chain."""
        detector = RetryDetector()

        # User message
        detector.is_retry(sender="justin", content="Hello world")

        # Synthetic error (doesn't update state)
        detector.is_retry(
            sender="magent",
            content="No response requested.",
            is_synthetic_error=True
        )

        # Another user message with same content - should still be retry
        is_retry = detector.is_retry(sender="justin", content="Hello world")

        self.assertTrue(is_retry)

    def test_real_message_breaks_retry_chain(self):
        """Real message between attempts breaks the retry chain."""
        detector = RetryDetector()

        # First attempt
        detector.is_retry(sender="justin", content="Hello world")

        # Synthetic error
        detector.is_retry(
            sender="magent",
            content="Error",
            is_synthetic_error=True
        )

        # Real different message from user
        detector.is_retry(sender="justin", content="Help command")

        # Same content as first attempt - NOT a retry because real message intervened
        is_retry = detector.is_retry(sender="justin", content="Hello world")

        self.assertFalse(is_retry)

    def test_multiple_consecutive_retries(self):
        """Can detect multiple consecutive retries of same content."""
        detector = RetryDetector()

        # Original
        detector.is_retry(sender="justin", content="Hello")

        # First retry
        is_retry1 = detector.is_retry(sender="justin", content="Hello")
        self.assertTrue(is_retry1)

        # Second retry
        is_retry2 = detector.is_retry(sender="justin", content="Hello")
        self.assertTrue(is_retry2)

        # Third retry
        is_retry3 = detector.is_retry(sender="justin", content="Hello")
        self.assertTrue(is_retry3)

    def test_per_sender_tracking(self):
        """Retry detection is per-sender."""
        detector = RetryDetector()

        # Justin sends message
        detector.is_retry(sender="justin", content="Hello")

        # Magent sends same content - not a retry (different sender)
        is_retry = detector.is_retry(sender="magent", content="Hello")
        self.assertFalse(is_retry)

        # Justin sends same content again - IS a retry
        is_retry = detector.is_retry(sender="justin", content="Hello")
        self.assertTrue(is_retry)

    def test_realistic_timeout_scenario(self):
        """Test realistic timeout/retry scenario with multiple attempts."""
        detector = RetryDetector()

        messages = [
            ("justin", "Great. Let's build something cool.", False),
            ("magent", "No response requested.", True),  # Synthetic error
            ("justin", "Great.  Let's build\n something cool.", False),  # Retry (whitespace diff)
            ("magent", "No response requested.", True),  # Synthetic error
            ("justin", "Great. Let's build something cool.", False),  # Retry (exact match)
            ("magent", "API Error: Connection error.", True),  # Synthetic error
            ("justin", "/help", False),  # Real new message
            ("magent", "No response requested.", True),  # Synthetic error
            ("justin", "Great. Let's build something cool.", False),  # NOT retry (real message intervened)
            ("magent", "Sounds great! Let's do it.", False),  # Real response
        ]

        results = []
        for sender, content, is_synthetic in messages:
            is_retry = detector.is_retry(sender, content, is_synthetic)
            results.append((sender, content[:20], is_retry, is_synthetic))

        # Messages 2 and 4 should be retries (same content with/without whitespace)
        # Message 8 should NOT be retry (real message 6 intervened)
        self.assertFalse(results[0][2])  # Original
        self.assertFalse(results[1][2])  # Synthetic (never counted as retry)
        self.assertTrue(results[2][2])   # Retry #1
        self.assertFalse(results[3][2])  # Synthetic
        self.assertTrue(results[4][2])   # Retry #2
        self.assertFalse(results[5][2])  # Synthetic
        self.assertFalse(results[6][2])  # Real new message
        self.assertFalse(results[7][2])  # Synthetic
        self.assertFalse(results[8][2])  # NOT retry (different content now)
        self.assertFalse(results[9][2])  # Real response
