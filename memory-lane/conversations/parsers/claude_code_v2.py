"""
Parser for Claude Code V2 JSONL conversation format.

This format is used by Claude Code starting around June 2025 (block ~22,700,000).
Each line is a JSON object with:
- parentUuid: Links to previous message for threading
- type: 'user' or 'assistant' or 'summary'
- message.content: Array of content blocks (text, tool_use, tool_result)
- timestamp: ISO 8601 timestamp
- sessionId: Conversation session UUID
- uuid: Message UUID

Supports extended thinking mode (messages with is_thinking flag).
"""

import json
from typing import Dict, List, Optional, Any
from datetime import datetime


class   ClaudeCodeV2Parser:
    """Parse Claude Code V2 JSONL conversation files."""

    @staticmethod
    def parse_file(file_path: str) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """
        Parse a Claude Code V2 JSONL file into normalized message dictionaries.

        Args:
            file_path: Path to .jsonl file

        Returns:
            Tuple of (messages, metadata) where:
            - messages: List of message dictionaries
            - metadata: Dict with 'summary' and 'leaf_uuid' if summary exists
                - id: UUID
                - parent_uuid: Parent message UUID or None
                - from_person: 'justin' or 'magent'
                - to_person: 'justin' or 'magent'
                - content: Extracted text content
                - timestamp: Unix timestamp (seconds)
                - session_id: Session UUID
                - is_thinking: Boolean
                - model_backend: Model name
                - input_tokens: Token count
                - output_tokens: Token count
                - cache_creation_input_tokens: Cache tokens
                - cache_read_input_tokens: Cache tokens
                - stop_reason: Stop reason string
        """
        messages = []
        metadata = {}

        with open(file_path, 'r') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue

                try:
                    record = json.loads(line)

                    # Handle summaries separately (not messages)
                    if record.get('type') == 'summary':
                        metadata['summary'] = record.get('summary', '')
                        metadata['leaf_uuid'] = record.get('leafUuid')
                        continue

                    # Parse all other message types (user, assistant, system)
                    parsed = ClaudeCodeV2Parser._parse_message(record)
                    if parsed:
                        messages.append(parsed)

                except json.JSONDecodeError as e:
                    print(f"Warning: Skipping malformed JSON line: {e}")
                    continue

        return messages, metadata

    @staticmethod
    def _parse_message(record: Dict) -> Optional[Dict[str, Any]]:
        """Parse a single message record (user, assistant, or system)."""

        # Get basic fields
        msg_type = record.get('type')  # 'user', 'assistant', 'system'

        # Handle system messages differently
        if msg_type == 'system':
            return ClaudeCodeV2Parser._parse_system(record)

        # User and assistant messages (standard structure)
        msg_uuid = record.get('uuid')
        parent_uuid = record.get('parentUuid')
        logical_parent_uuid = record.get('logicalParentUuid')
        session_id = record.get('sessionId')

        if not msg_uuid:
            return None

        # Parse timestamp
        timestamp_str = record.get('timestamp')
        timestamp = None
        if timestamp_str:
            try:
                dt = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                timestamp = int(dt.timestamp())
            except ValueError:
                pass

        # Extract message content and check if thinking
        message_obj = record.get('message', {})
        content, is_thinking = ClaudeCodeV2Parser._extract_content(message_obj)

        # Determine from/to
        if msg_type == 'user':
            from_person = 'justin'
            to_person = 'magent'
        elif is_thinking:
            # Thinking messages are from magent to magent
            from_person = 'magent'
            to_person = 'magent'
        else:  # assistant (non-thinking)
            from_person = 'magent'
            to_person = 'justin'

        # Extract model and usage info
        model = message_obj.get('model', '')
        usage = message_obj.get('usage', {})
        stop_reason = message_obj.get('stop_reason')

        # Extract environment metadata
        git_branch = record.get('gitBranch')
        cwd = record.get('cwd')
        version = record.get('version')
        is_sidechain = record.get('isSidechain', False)

        return {
            'id': msg_uuid,
            'parent_uuid': parent_uuid,
            'logical_parent_uuid': logical_parent_uuid,
            'from_person': from_person,
            'to_person': to_person,
            'content': content,
            'timestamp': timestamp,
            'session_id': session_id,
            'message_type': msg_type,
            'message_subtype': None,
            'is_thinking': is_thinking,
            'is_sidechain': is_sidechain,
            'model_backend': model,
            'git_branch': git_branch,
            'cwd': cwd,
            'claude_code_version': version,
            'input_tokens': usage.get('input_tokens'),
            'output_tokens': usage.get('output_tokens'),
            'cache_creation_input_tokens': usage.get('cache_creation_input_tokens'),
            'cache_read_input_tokens': usage.get('cache_read_input_tokens'),
            'stop_reason': stop_reason,
            'compact_metadata': None,
        }

    @staticmethod
    def _parse_system(record: Dict) -> Optional[Dict[str, Any]]:
        """Parse a system message (compact boundaries, etc.)."""

        msg_uuid = record.get('uuid')
        parent_uuid = record.get('parentUuid')
        logical_parent_uuid = record.get('logicalParentUuid')
        session_id = record.get('sessionId')
        subtype = record.get('subtype')
        content = record.get('content', '')

        if not msg_uuid:
            return None

        # Parse timestamp
        timestamp_str = record.get('timestamp')
        timestamp = None
        if timestamp_str:
            try:
                dt = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                timestamp = int(dt.timestamp())
            except ValueError:
                pass

        # Extract compact metadata if present
        compact_metadata = record.get('compactMetadata')

        # Extract environment metadata
        git_branch = record.get('gitBranch')
        cwd = record.get('cwd')
        version = record.get('version')
        is_sidechain = record.get('isSidechain', False)

        # System messages don't have from/to in traditional sense
        # We'll mark them as system→system
        return {
            'id': msg_uuid,
            'parent_uuid': parent_uuid,
            'logical_parent_uuid': logical_parent_uuid,
            'from_person': 'magent',  # System events are about AI state
            'to_person': 'magent',
            'content': content,
            'timestamp': timestamp,
            'session_id': session_id,
            'message_type': 'system',
            'message_subtype': subtype,
            'is_thinking': False,
            'is_sidechain': is_sidechain,
            'model_backend': None,
            'git_branch': git_branch,
            'cwd': cwd,
            'claude_code_version': version,
            'input_tokens': None,
            'output_tokens': None,
            'cache_creation_input_tokens': None,
            'cache_read_input_tokens': None,
            'stop_reason': None,
            'compact_metadata': compact_metadata,
        }

    @staticmethod
    def _extract_content(message_obj: Dict) -> tuple[str, bool]:
        """
        Extract text content from message object.

        Handles both string content (user messages) and array content (assistant messages).
        Combines multiple content blocks, skipping tool_use and tool_result blocks.

        Returns:
            Tuple of (content_string, is_thinking)
        """
        content_raw = message_obj.get('content', [])

        # Handle string content (some user messages)
        if isinstance(content_raw, str):
            return content_raw, False

        # Handle array content
        if not isinstance(content_raw, list):
            return '', False

        text_parts = []
        is_thinking = False

        for block in content_raw:
            if isinstance(block, dict):
                block_type = block.get('type', '')

                # Extract text blocks
                if block_type == 'text':
                    text = block.get('text', '')
                    if text:
                        text_parts.append(text)

                # Extract thinking blocks
                elif block_type == 'thinking':
                    is_thinking = True
                    thinking_text = block.get('thinking', '')
                    if thinking_text:
                        text_parts.append(thinking_text)

                # Skip tool_use and tool_result - they're meta, not conversation content
                # (We could optionally include them in a separate field if needed)

        return '\n'.join(text_parts), is_thinking
