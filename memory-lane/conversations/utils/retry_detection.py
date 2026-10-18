"""
Retry detection logic for conversation imports.

Detects when messages are retries (duplicates due to timeouts/errors) vs. legitimate
repeated content.
"""

import re
from typing import Dict, Optional


class RetryDetector:
    """
    Detects retry messages in conversation imports.

    A message is considered a retry if:
    1. It has the same normalized content as the previous non-synthetic message
       from the same sender
    2. Only synthetic error messages occurred between them

    This distinguishes retries (same message sent again due to timeout) from
    legitimate repeated content (e.g., saying "yes" twice in different contexts).
    """

    def __init__(self):
        # Track last real (non-synthetic) message per sender
        # sender_name -> (normalized_content, message_id)
        self.last_real_by_sender: Dict[str, tuple[str, str]] = {}

    @staticmethod
    def normalize_content(content: str) -> str:
        """
        Normalize message content for comparison.

        Collapses whitespace (spaces, newlines, tabs) to handle terminal wrapping
        and formatting differences in retried messages.

        Args:
            content: Raw message content

        Returns:
            Normalized content with whitespace collapsed
        """
        return re.sub(r'\s+', ' ', str(content).strip())

    def is_retry(
        self,
        sender: str,
        content: str,
        is_synthetic_error: bool = False
    ) -> bool:
        """
        Check if this message is a retry of a previous message.

        Args:
            sender: Message sender name
            content: Message content
            is_synthetic_error: Whether this is a synthetic error response

        Returns:
            True if this is a retry, False otherwise

        Side effects:
            Updates internal state tracking last real message per sender
        """
        content_normalized = self.normalize_content(content)

        # Synthetic errors don't count as real messages and don't break retry chains
        if is_synthetic_error:
            return False

        # Check if this matches the last real message from this sender
        if sender in self.last_real_by_sender:
            last_content, last_msg_id = self.last_real_by_sender[sender]
            if content_normalized == last_content:
                # This is a retry - don't update last_real so we can detect
                # multiple consecutive retries of the same content
                return True

        # Not a retry - update last real message for this sender
        self.last_real_by_sender[sender] = (content_normalized, "")
        return False
