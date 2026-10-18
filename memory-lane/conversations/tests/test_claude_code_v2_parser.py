"""
Tests for Claude Code V2 JSONL parser.

Tests parsing of all message types: user, assistant, thinking, system, and summary.
Parser returns dictionaries that import scripts use to create model instances.
"""

import json
import tempfile
from pathlib import Path


def test_parse_all_message_types():
    """Test parsing a JSONL file with all message types."""

    # Import here to avoid Django setup issues
    from conversations.parsers.claude_code_v2 import ClaudeCodeV2Parser

    # Sample data from actual JSONL files
    sample_jsonl = [
        # Summary message (appears first in file)
        {
            "type": "summary",
            "summary": "Ticket Stub Merger: Prague 2025 GitHub Integration",
            "leafUuid": "final-msg-uuid-123"
        },

        # Parent message (user message, no parent)
        {
            "parentUuid": None,
            "isSidechain": False,
            "userType": "external",
            "cwd": "/home/jmyles/projects/JustinHolmesMusic/arthel",
            "sessionId": "test-session-uuid",
            "version": "1.0.113",
            "gitBranch": "bluegrass-bacon",
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Hey, how's it going?"}
                ]
            },
            "uuid": "parent-msg-uuid-456",
            "timestamp": "2025-09-15T18:58:48.890Z"
        },

        # Child message (assistant response with thinking)
        {
            "parentUuid": "parent-msg-uuid-456",
            "isSidechain": False,
            "userType": "external",
            "cwd": "/home/jmyles/projects/JustinHolmesMusic/arthel",
            "sessionId": "test-session-uuid",
            "version": "1.0.113",
            "gitBranch": "bluegrass-bacon",
            "type": "assistant",
            "timestamp": "2025-09-15T18:59:00.000Z",
            "message": {
                "id": "msg_01xyz",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-4-20250514",
                "content": [
                    {
                        "type": "thinking",
                        "thinking": "The user is asking how I'm doing. I should respond warmly and check what they need help with.",
                        "signature": "signature-data-here"
                    },
                    {
                        "type": "text",
                        "text": "Hey Justin! Things are going well. What are you working on?"
                    }
                ],
                "stop_reason": "end_turn",
                "usage": {
                    "input_tokens": 1000,
                    "cache_creation_input_tokens": 5000,
                    "cache_read_input_tokens": 2000,
                    "output_tokens": 50,
                    "service_tier": "standard"
                }
            },
            "uuid": "child-msg-uuid-789",
            "requestId": "req_xyz"
        },

        # System message (compact boundary)
        {
            "parentUuid": None,
            "logicalParentUuid": "child-msg-uuid-789",
            "isSidechain": False,
            "userType": "external",
            "cwd": "/home/jmyles/projects/JustinHolmesMusic/arthel",
            "sessionId": "test-session-uuid",
            "version": "1.0.113",
            "gitBranch": "bluegrass-bacon",
            "type": "system",
            "subtype": "compact_boundary",
            "content": "Conversation compacted",
            "isMeta": False,
            "timestamp": "2025-09-15T19:00:00.000Z",
            "uuid": "system-msg-uuid-abc",
            "level": "info",
            "compactMetadata": {
                "trigger": "manual",
                "preTokens": 140330
            }
        }
    ]

    # Write to temporary JSONL file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
        for record in sample_jsonl:
            f.write(json.dumps(record) + '\n')
        temp_path = f.name

    try:
        # Parse the file
        messages, metadata = ClaudeCodeV2Parser.parse_file(temp_path)

        # Verify summary metadata
        assert 'summary' in metadata
        assert metadata['summary'] == "Ticket Stub Merger: Prague 2025 GitHub Integration"
        assert metadata['leaf_uuid'] == "final-msg-uuid-123"

        # Should have 3 messages (parent, child, system - no summary message)
        assert len(messages) == 3

        # Verify parent message (user message with no parent)
        parent_msg = messages[0]
        assert parent_msg['id'] == "parent-msg-uuid-456"
        assert parent_msg['parent_uuid'] is None
        assert parent_msg['logical_parent_uuid'] is None
        assert parent_msg['from_person'] == 'justin'
        assert parent_msg['to_person'] == 'magent'
        assert parent_msg['content'] == "Hey, how's it going?"
        assert parent_msg['message_type'] == 'user'
        assert parent_msg['is_thinking'] is False
        assert parent_msg['session_id'] == 'test-session-uuid'
        assert parent_msg['git_branch'] == 'bluegrass-bacon'
        assert parent_msg['cwd'] == '/home/jmyles/projects/JustinHolmesMusic/arthel'
        assert parent_msg['claude_code_version'] == '1.0.113'
        assert parent_msg['is_sidechain'] is False

        # Verify child message (assistant with thinking)
        child_msg = messages[1]
        assert child_msg['id'] == "child-msg-uuid-789"
        assert child_msg['parent_uuid'] == "parent-msg-uuid-456"
        assert child_msg['from_person'] == 'magent'
        assert child_msg['to_person'] == 'magent'  # thinking messages are magent→magent
        assert child_msg['is_thinking'] is True
        assert child_msg['message_type'] == 'assistant'
        assert "The user is asking how I'm doing" in child_msg['content']
        assert "Hey Justin! Things are going well" in child_msg['content']
        assert child_msg['model_backend'] == 'claude-sonnet-4-20250514'
        assert child_msg['input_tokens'] == 1000
        assert child_msg['output_tokens'] == 50
        assert child_msg['cache_creation_input_tokens'] == 5000
        assert child_msg['cache_read_input_tokens'] == 2000
        assert child_msg['stop_reason'] == 'end_turn'

        # Verify system message (compact boundary)
        system_msg = messages[2]
        assert system_msg['id'] == "system-msg-uuid-abc"
        assert system_msg['parent_uuid'] is None
        assert system_msg['logical_parent_uuid'] == "child-msg-uuid-789"
        assert system_msg['from_person'] == 'magent'
        assert system_msg['to_person'] == 'magent'
        assert system_msg['content'] == "Conversation compacted"
        assert system_msg['message_type'] == 'system'
        assert system_msg['message_subtype'] == 'compact_boundary'
        assert system_msg['compact_metadata'] is not None
        assert system_msg['compact_metadata']['trigger'] == 'manual'
        assert system_msg['compact_metadata']['preTokens'] == 140330

        print("✓ All parser tests passed!")
        print(f"\nParsed {len(messages)} messages:")
        print(f"  1. Parent (user): {parent_msg['content'][:50]}...")
        print(f"  2. Child (assistant/thinking): {child_msg['content'][:50]}...")
        print(f"  3. System (compact boundary): {system_msg['content']}")
        print(f"\nMetadata:")
        print(f"  Summary: {metadata['summary']}")
        print(f"  Leaf UUID: {metadata['leaf_uuid']}")

    finally:
        # Clean up temp file
        Path(temp_path).unlink()


if __name__ == '__main__':
    test_parse_all_message_types()
