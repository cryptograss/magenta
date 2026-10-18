"""
Inspect messages without parents to understand what they are.
"""

from django.core.management.base import BaseCommand
from conversations.parsers.claude_code_v2 import ClaudeCodeV2Parser
from pathlib import Path
import json


class Command(BaseCommand):
    help = 'Inspect messages without parents in Claude Code V2 JSONL files'

    def add_arguments(self, parser):
        parser.add_argument(
            'path',
            type=str,
            help='Path to JSONL file or directory containing JSONL files'
        )

    def handle(self, *args, **options):
        path = Path(options['path'])

        # Get list of JSONL files
        if path.is_dir():
            jsonl_files = sorted(path.glob('*.jsonl'))
        else:
            jsonl_files = [path]

        # Track parentless messages with their raw data
        parentless = []

        for jsonl_file in jsonl_files:
            with open(jsonl_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue

                    try:
                        record = json.loads(line)

                        # Skip summaries
                        if record.get('type') == 'summary':
                            continue

                        # Check if parentUuid is None or missing
                        parent_uuid = record.get('parentUuid')
                        if parent_uuid is None:
                            msg_uuid = record.get('uuid')
                            msg_type = record.get('type')
                            session_id = record.get('sessionId')

                            # Extract content preview
                            message_obj = record.get('message', {})
                            content_raw = message_obj.get('content', [])
                            if isinstance(content_raw, str):
                                preview = content_raw[:80]
                            elif isinstance(content_raw, list):
                                text_parts = []
                                for block in content_raw:
                                    if isinstance(block, dict) and block.get('type') == 'text':
                                        text_parts.append(block.get('text', ''))
                                preview = '\n'.join(text_parts)[:80]
                            else:
                                preview = ''

                            parentless.append({
                                'uuid': msg_uuid,
                                'type': msg_type,
                                'session_id': session_id,
                                'preview': preview,
                                'file': jsonl_file.name
                            })

                    except json.JSONDecodeError:
                        continue

        # Report findings
        self.stdout.write(f'\nFound {len(parentless)} messages without parents\n')

        # Group by session
        sessions = {}
        for msg in parentless:
            sid = msg['session_id']
            if sid not in sessions:
                sessions[sid] = []
            sessions[sid].append(msg)

        self.stdout.write(f'Across {len(sessions)} different sessions\n')

        # Show first 10
        self.stdout.write('\nFirst 10 parentless messages:')
        for msg in parentless[:10]:
            preview = msg['preview'].replace('\n', ' ')
            self.stdout.write(
                f"\n  Type: {msg['type']}\n"
                f"  Session: {msg['session_id'][:8]}...\n"
                f"  File: {msg['file']}\n"
                f"  Preview: {preview}...\n"
            )

        # Check if they're all first messages
        self.stdout.write('\nChecking if these are first messages in their sessions...')
        first_messages = 0
        for session_id, msgs in sessions.items():
            if len(msgs) == 1:  # Only one parentless message per session
                first_messages += 1

        self.stdout.write(f'{first_messages}/{len(sessions)} sessions have exactly 1 parentless message')
