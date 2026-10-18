"""
Repair broken parent chains in conversation data.

This script fixes the issue where `local_command` events (like "Status dialog dismissed")
were not being imported, breaking the parent chain. When a user message has a local_command
as its parent, and we don't import that local_command, the user message ends up orphaned
with parent_id=None, which triggers a new FRESH heap.

Strategy:
1. Find messages that are first-in-heap with parent_id=None but shouldn't be orphaned
2. Look up their parentUuid from the source JSONL files
3. Import missing parent messages (local_command events)
4. Link the orphaned messages to their parents
5. Merge consecutive FRESH heaps that are now connected
"""

import json
import uuid as uuid_lib
from pathlib import Path
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from conversations.models import Message, ContextHeap, ContextHeapType, Era
from importers_and_parsers.claude_code_v2 import (
    import_line_from_claude_code_v2,
    get_or_create_participant,
    extract_timestamp,
)


class Command(BaseCommand):
    help = 'Repair broken parent chains by importing missing local_command messages'

    def add_arguments(self, parser):
        parser.add_argument(
            '--jsonl-dir',
            type=str,
            default='/home/magent/.claude/projects/-home-magent',
            help='Directory containing JSONL files',
        )
        parser.add_argument(
            '--era-id',
            type=str,
            help='Only repair specific era (UUID)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be done without making changes',
        )
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Verbose output',
        )

    def handle(self, *args, **options):
        jsonl_dir = Path(options['jsonl_dir'])
        era_id = options.get('era_id')
        dry_run = options.get('dry_run', False)
        verbose = options.get('verbose', False)

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - no changes will be saved'))

        self.stdout.write('=' * 70)
        self.stdout.write('Parent Chain Repair Utility')
        self.stdout.write('=' * 70)

        # Step 1: Build index of all JSONL events by UUID
        self.stdout.write('\n📂 Building JSONL index...')
        jsonl_index = self.build_jsonl_index(jsonl_dir)
        self.stdout.write(f'   Indexed {len(jsonl_index)} events from JSONL files')

        # Step 2: Find orphaned first-in-heap messages
        self.stdout.write('\n🔍 Finding orphaned first-in-heap messages...')
        orphaned_messages = self.find_orphaned_messages(era_id)
        self.stdout.write(f'   Found {len(orphaned_messages)} orphaned messages')

        if not orphaned_messages:
            self.stdout.write(self.style.SUCCESS('\nNo orphaned messages to repair!'))
            return

        # Step 3: For each orphaned message, trace back to find missing parents
        self.stdout.write('\n🔗 Tracing parent chains...')
        repair_plan = self.create_repair_plan(orphaned_messages, jsonl_index, verbose)

        # Step 4: Execute repairs
        self.stdout.write(f'\n🔧 Executing repairs ({len(repair_plan)} operations)...')
        self.execute_repairs(repair_plan, jsonl_index, dry_run, verbose, era_id)

        # Step 5: Merge heaps that should now be connected
        self.stdout.write('\n🔀 Checking for heaps to merge...')
        self.merge_connected_heaps(era_id, dry_run, verbose)

        self.stdout.write('=' * 70)
        self.stdout.write(self.style.SUCCESS('Repair complete'))

    def build_jsonl_index(self, jsonl_dir):
        """Build an index of all events by UUID from JSONL files."""
        index = {}

        if not jsonl_dir.exists():
            self.stdout.write(self.style.WARNING(f'   JSONL directory not found: {jsonl_dir}'))
            return index

        jsonl_files = list(jsonl_dir.glob('*.jsonl'))
        self.stdout.write(f'   Found {len(jsonl_files)} JSONL files')

        for jsonl_file in jsonl_files:
            try:
                with open(jsonl_file, 'r') as f:
                    for line_num, line in enumerate(f, 1):
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            event = json.loads(line)
                            event_uuid = event.get('uuid')
                            if event_uuid:
                                index[event_uuid] = {
                                    'event': event,
                                    'line': line,
                                    'file': str(jsonl_file),
                                    'line_num': line_num,
                                }
                        except json.JSONDecodeError:
                            continue
            except Exception as e:
                self.stdout.write(self.style.WARNING(f'   Error reading {jsonl_file}: {e}'))

        return index

    def find_orphaned_messages(self, era_id=None):
        """Find messages that are first in their heap with no parent but should have one."""
        orphaned = []

        # Get all heaps
        filters = {'type': ContextHeapType.FRESH}
        if era_id:
            filters['era_id'] = era_id

        heaps = ContextHeap.objects.filter(**filters).prefetch_related('messages')

        for heap in heaps:
            # Get first message in heap (by message_number or created_at)
            first_msg = heap.messages.order_by('message_number', 'created_at').first()
            if not first_msg:
                continue

            # Check if it needs a parent:
            # 1. No parent_id AND no looking_for_parent_id (never tried to link)
            # 2. No parent_id BUT has looking_for_parent_id (tried but parent missing)
            if first_msg.parent_id is None:
                # Skip continuation messages - they're supposed to be parentless
                if getattr(first_msg, 'is_continuation_message', False):
                    continue

                orphaned.append({
                    'message': first_msg,
                    'heap': heap,
                    'looking_for': first_msg.looking_for_parent_id,
                })

        return orphaned

    def create_repair_plan(self, orphaned_messages, jsonl_index, verbose):
        """Create a plan for repairing parent chains."""
        plan = []

        for item in orphaned_messages:
            msg = item['message']
            msg_uuid = str(msg.id)
            looking_for = item.get('looking_for')

            # First check if we already know the parent UUID from looking_for_parent_id
            if looking_for:
                parent_uuid = str(looking_for)
            else:
                # Look up this message in JSONL to find its parentUuid
                if msg_uuid not in jsonl_index:
                    if verbose:
                        self.stdout.write(f'   ⚠ Message {msg_uuid[:8]} not found in JSONL index')
                    continue

                jsonl_data = jsonl_index[msg_uuid]
                event = jsonl_data['event']
                parent_uuid = event.get('parentUuid')

            if not parent_uuid:
                if verbose:
                    self.stdout.write(f'   ⚠ Message {msg_uuid[:8]} has no parentUuid in JSONL')
                continue

            # Check if parent already exists in DB
            try:
                parent_msg = Message.objects.get(id=parent_uuid)
                # Parent exists! Just need to link them
                plan.append({
                    'action': 'link',
                    'message': msg,
                    'parent_uuid': parent_uuid,
                    'parent_exists': True,
                    'heap': item['heap'],
                })
                if verbose:
                    self.stdout.write(f'   ✓ Message {msg_uuid[:8]} → parent {parent_uuid[:8]} (exists)')
            except Message.DoesNotExist:
                # Parent doesn't exist - need to import it
                if parent_uuid in jsonl_index:
                    plan.append({
                        'action': 'import_and_link',
                        'message': msg,
                        'parent_uuid': parent_uuid,
                        'parent_jsonl': jsonl_index[parent_uuid],
                        'heap': item['heap'],
                    })
                    if verbose:
                        parent_event = jsonl_index[parent_uuid]['event']
                        parent_type = parent_event.get('type', 'unknown')
                        parent_subtype = parent_event.get('subtype', '')
                        self.stdout.write(
                            f'   ⊕ Message {msg_uuid[:8]} → parent {parent_uuid[:8]} '
                            f'(needs import: {parent_type}/{parent_subtype})'
                        )
                else:
                    if verbose:
                        self.stdout.write(
                            f'   ⚠ Message {msg_uuid[:8]} → parent {parent_uuid[:8]} not in JSONL'
                        )

        return plan

    def execute_repairs(self, plan, jsonl_index, dry_run, verbose, era_id=None):
        """Execute the repair plan."""
        imported = 0
        linked = 0
        errors = 0

        # Get the era for importing new messages
        era = None
        if era_id:
            try:
                era = Era.objects.get(id=era_id)
            except Era.DoesNotExist:
                pass
        if not era:
            era = Era.objects.order_by('-created_at').first()

        for item in plan:
            try:
                if item['action'] == 'import_and_link':
                    # First import the missing parent
                    parent_jsonl = item['parent_jsonl']
                    parent_line = parent_jsonl['line']
                    parent_file = parent_jsonl['file']
                    parent_event = parent_jsonl['event']
                    parent_type = parent_event.get('type', '')
                    parent_subtype = parent_event.get('subtype', '')

                    if not dry_run:
                        with transaction.atomic():
                            # Check if this is a compact_boundary - these need special handling
                            # because import_line_from_claude_code_v2 creates CompactingAction, not Message
                            if parent_type == 'system' and parent_subtype == 'compact_boundary':
                                # Create a Message stub for this compact_boundary
                                # This allows child messages to link to it
                                parent_uuid = uuid_lib.UUID(item['parent_uuid'])
                                sender = get_or_create_participant('system', 'system')
                                timestamp = extract_timestamp(parent_event)

                                message, created = Message.objects.get_or_create(
                                    id=parent_uuid,
                                    defaults={
                                        'sender': sender,
                                        'source_file': parent_file,
                                        'content': {'type': 'compact_boundary', 'raw': parent_event.get('compactMetadata', {})},
                                        'timestamp': timestamp,
                                        'created_at': timezone.now(),
                                    }
                                )
                                if created:
                                    imported += 1
                                    if verbose:
                                        self.stdout.write(f'      Created Message stub for compact_boundary {item["parent_uuid"][:8]}')

                                # Also import as CompactingAction for proper tracking
                                import_line_from_claude_code_v2(parent_line, era, parent_file)
                            else:
                                # Normal import - creates a Message
                                result, created = import_line_from_claude_code_v2(
                                    parent_line, era, parent_file
                                )

                                if created:
                                    imported += 1
                                    if verbose:
                                        self.stdout.write(f'      Imported parent {item["parent_uuid"][:8]}')

                            # Now link the child to parent
                            msg = item['message']
                            msg.set_parent_id(item['parent_uuid'])
                            linked += 1
                    else:
                        imported += 1
                        linked += 1

                elif item['action'] == 'link':
                    # Just link - parent already exists
                    if not dry_run:
                        msg = item['message']
                        msg.set_parent_id(item['parent_uuid'])
                    linked += 1

            except Exception as e:
                errors += 1
                self.stdout.write(
                    self.style.ERROR(f'   Error repairing {str(item["message"].id)[:8]}: {e}')
                )

        self.stdout.write(f'   Imported: {imported} messages')
        self.stdout.write(f'   Linked: {linked} parent relationships')
        if errors:
            self.stdout.write(self.style.ERROR(f'   Errors: {errors}'))

    def merge_connected_heaps(self, era_id, dry_run, verbose):
        """
        Merge consecutive FRESH heaps that are now connected via parent chains.

        After repairing parent links, some heaps that were separate should now be
        merged because their first message now has a parent in the previous heap.
        """
        merged = 0

        filters = {'type': ContextHeapType.FRESH}
        if era_id:
            filters['era_id'] = era_id

        # Get all FRESH heaps ordered by creation time
        heaps = list(
            ContextHeap.objects.filter(**filters)
            .prefetch_related('messages')
            .order_by('created_at')
        )

        heaps_to_delete = []

        for heap in heaps:
            # Get first message
            first_msg = heap.messages.order_by('message_number', 'created_at').first()
            if not first_msg:
                continue

            # Check if first message now has a parent
            if first_msg.parent_id:
                # Find parent's heap
                try:
                    parent_msg = Message.objects.get(id=first_msg.parent_id)
                    parent_heap = parent_msg.context_heap

                    if parent_heap and parent_heap.id != heap.id:
                        # Different heap - should we merge?
                        # Only merge if parent heap is FRESH (not POST_COMPACTING)
                        if parent_heap.type == ContextHeapType.FRESH:
                            if verbose:
                                self.stdout.write(
                                    f'   🔀 Merge heap {str(heap.id)[:8]} into {str(parent_heap.id)[:8]}'
                                )

                            if not dry_run:
                                # Get the highest message number in parent heap
                                from django.db.models import Max
                                max_num = parent_heap.messages.aggregate(Max('message_number'))['message_number__max'] or 0

                                # Move messages one by one, renumbering to avoid conflicts
                                for msg in heap.messages.order_by('message_number', 'created_at'):
                                    max_num += 1
                                    msg.context_heap = parent_heap
                                    msg.message_number = max_num
                                    msg.save(update_fields=['context_heap', 'message_number'])

                                heaps_to_delete.append(heap)
                                merged += 1
                            else:
                                merged += 1

                except Message.DoesNotExist:
                    pass

        # Delete empty heaps
        if not dry_run:
            for heap in heaps_to_delete:
                heap.delete()

        self.stdout.write(f'   Merged: {merged} heaps')
