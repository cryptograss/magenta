"""
Django management command to import messages from Claude Code V2 JSONL files.

These are the conversation backups from the Claude Code era (June 2025+)
with full parent-child threading, timestamps, and session tracking.

Usage:
    python manage.py import_from_claude_code_v2_jsonl /path/to/backups/
    python manage.py import_from_claude_code_v2_jsonl /path/to/file.jsonl
    python manage.py import_from_claude_code_v2_jsonl --dry-run /path/to/backups/
"""

from django.core.management.base import BaseCommand
from conversations.models import Message
from conversations.parsers.claude_code_v2 import ClaudeCodeV2Parser
from pathlib import Path


class Command(BaseCommand):
    help = 'Import messages from Claude Code V2 JSONL files'

    def add_arguments(self, parser):
        parser.add_argument(
            'path',
            type=str,
            help='Path to JSONL file or directory containing JSONL files'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be imported without actually importing',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        path = Path(options['path'])

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - no data will be imported'))

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

        self.stdout.write(f'Found {len(jsonl_files)} JSONL file(s)')

        # Parse all files
        all_messages = []
        for jsonl_file in jsonl_files:
            self.stdout.write(f'  Parsing {jsonl_file.name}...')
            try:
                messages = ClaudeCodeV2Parser.parse_file(str(jsonl_file))
                all_messages.extend(messages)
                self.stdout.write(f'    -> {len(messages)} messages')
            except Exception as e:
                self.stderr.write(self.style.ERROR(f'    Error parsing {jsonl_file.name}: {e}'))

        total = len(all_messages)
        self.stdout.write(f'\nTotal parsed: {total} messages')

        if dry_run:
            # Show preview
            self.stdout.write('\nPreview of first 5 messages:')
            for msg in all_messages[:5]:
                preview = msg['content'][:60].replace('\n', ' ')
                self.stdout.write(f"  {msg['from_person']}→{msg['to_person']}: {preview}...")
            return

        # Import messages
        imported = 0
        skipped = 0
        orphans = []  # Track messages with missing parents

        # First pass: create all messages without parent relationships
        for msg_data in all_messages:
            msg_id = msg_data['id']

            # Check if already exists
            if Message.objects.filter(id=msg_id).exists():
                skipped += 1
                continue

            # Create message without parent (will link in second pass)
            Message.objects.create(
                id=msg_id,
                parent=None,  # Will link in second pass
                from_person=msg_data['from_person'],
                to_person=msg_data['to_person'],
                content=msg_data['content'],
                timestamp=msg_data['timestamp'],
                session_id=msg_data['session_id'],
                is_thinking=msg_data['is_thinking'],
                model_backend=msg_data['model_backend'],
                input_tokens=msg_data.get('input_tokens'),
                output_tokens=msg_data.get('output_tokens'),
                cache_creation_input_tokens=msg_data.get('cache_creation_input_tokens'),
                cache_read_input_tokens=msg_data.get('cache_read_input_tokens'),
                stop_reason=msg_data.get('stop_reason'),
            )

            imported += 1

            if imported % 100 == 0:
                self.stdout.write(f'  Imported {imported}/{total}...')

        # Second pass: link parent relationships
        self.stdout.write('\nLinking parent relationships...')
        linked = 0

        for msg_data in all_messages:
            parent_uuid = msg_data.get('parent_uuid')
            if not parent_uuid:
                continue

            try:
                msg = Message.objects.get(id=msg_data['id'])
                parent = Message.objects.get(id=parent_uuid)
                msg.parent = parent
                msg.save(update_fields=['parent'])
                linked += 1
            except Message.DoesNotExist:
                orphans.append({
                    'id': msg_data['id'],
                    'parent_uuid': parent_uuid,
                    'content_preview': msg_data['content'][:60]
                })

        self.stdout.write(self.style.SUCCESS(
            f'\n✓ Import complete:\n'
            f'  {imported} messages imported\n'
            f'  {skipped} skipped (already exist)\n'
            f'  {linked} parent relationships linked\n'
            f'  {len(orphans)} orphan messages (parent not found)'
        ))

        if orphans:
            self.stdout.write(self.style.WARNING(f'\nOrphan messages (first 5):'))
            for orphan in orphans[:5]:
                self.stdout.write(f"  {orphan['id'][:8]}... parent={orphan['parent_uuid'][:8]}...")
