"""
Django management command to analyze Claude Code V2 JSONL files without importing.

Shows statistics about the messages:
- Total messages
- Messages with parents
- Thinking messages
- Thinking messages with parents

Usage:
    python manage.py analyze_claude_code_v2_jsonl /path/to/backups/
"""

from django.core.management.base import BaseCommand
from conversations.parsers.claude_code_v2 import ClaudeCodeV2Parser
from pathlib import Path


class Command(BaseCommand):
    help = 'Analyze Claude Code V2 JSONL files and show statistics'

    def add_arguments(self, parser):
        parser.add_argument(
            'path',
            type=str,
            help='Path to JSONL file or directory containing JSONL files'
        )

    def handle(self, *args, **options):
        path = Path(options['path'])

        # Get list of JSONL files
        if path.is_file():
            jsonl_files = [path]
        elif path.is_dir():
            jsonl_files = sorted(path.glob('*.jsonl'))
        else:
            self.stderr.write(self.style.ERROR(f'Path not found: {path}'))
            return

        if not jsonl_files:
            self.stderr.write(self.style.ERROR('No JSONL files found'))
            return

        self.stdout.write(f'Found {len(jsonl_files)} JSONL file(s)\n')

        # Parse all files
        all_messages = []
        for jsonl_file in jsonl_files:
            try:
                messages = ClaudeCodeV2Parser.parse_file(str(jsonl_file))
                all_messages.extend(messages)
            except Exception as e:
                self.stderr.write(self.style.ERROR(f'Error parsing {jsonl_file.name}: {e}'))

        # Analyze
        total = len(all_messages)
        with_parent = sum(1 for m in all_messages if m.get('parent_uuid'))
        thinking = [m for m in all_messages if m.get('is_thinking')]
        thinking_with_parent = sum(1 for m in thinking if m.get('parent_uuid'))

        # Show statistics
        self.stdout.write(self.style.SUCCESS('Statistics:'))
        self.stdout.write(f'  Total messages: {total:,}')
        self.stdout.write(f'  Messages with parent: {with_parent:,} ({100*with_parent/total:.1f}%)')
        self.stdout.write(f'  Thinking messages: {len(thinking):,} ({100*len(thinking)/total:.1f}%)')
        self.stdout.write(f'  Thinking messages with parent: {thinking_with_parent:,} ({100*thinking_with_parent/len(thinking) if thinking else 0:.1f}%)')

        # Show some examples
        self.stdout.write(f'\nFirst 3 thinking messages:')
        for msg in thinking[:3]:
            preview = msg['content'][:80].replace('\n', ' ')
            parent = msg.get('parent_uuid', 'None')[:8] if msg.get('parent_uuid') else 'None'
            self.stdout.write(f'  ID: {msg["id"][:8]}... Parent: {parent}... "{preview}..."')
