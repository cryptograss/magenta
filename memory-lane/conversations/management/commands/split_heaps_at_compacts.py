"""
Management command to split context heaps at CompactingAction boundaries.

This is the second pass of the import process. After importing all JSONL files,
this command finds CompactingActions and splits heaps where messages exist
after the compact boundary.
"""

from django.core.management.base import BaseCommand
from conversations.models import CompactingAction


class Command(BaseCommand):
    help = 'Split context heaps at CompactingAction boundaries (second pass after import)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be split without actually splitting',
        )

    def handle(self, *args, **options):
        from conversations.models import Message

        dry_run = options['dry_run']

        # First pass: Link orphaned CAs where leaf messages now exist
        orphaned_cas = CompactingAction.objects.filter(
            context_heap__isnull=True,
            compact_boundary_message_id__isnull=False
        )

        self.stdout.write(f'\nPass 1: Linking {orphaned_cas.count()} orphaned CompactingActions...')
        linked_count = 0

        for ca in orphaned_cas:
            try:
                leaf_msg = Message.objects.get(id=ca.compact_boundary_message_id)
                if leaf_msg.context_heap:
                    # Check if the heap already has a CA (would violate unique constraint)
                    existing_ca = CompactingAction.objects.filter(context_heap=leaf_msg.context_heap).first()
                    if existing_ca and existing_ca.id != ca.id:
                        self.stdout.write(self.style.WARNING(
                            f'  Heap {str(leaf_msg.context_heap_id)[:8]} already has CA {str(existing_ca.id)[:8]}, '
                            f'skipping orphaned CA {str(ca.id)[:8]}'
                        ))
                        # Delete the duplicate orphaned CA
                        if not dry_run:
                            ca.delete()
                        continue

                    if not dry_run:
                        ca.context_heap = leaf_msg.context_heap
                        ca.ending_message_id = ca.compact_boundary_message_id
                        ca.save()
                        self.stdout.write(self.style.SUCCESS(
                            f'  Linked CA {str(ca.id)[:8]} to heap {str(leaf_msg.context_heap_id)[:8]}'
                        ))
                    else:
                        self.stdout.write(self.style.WARNING(
                            f'  Would link CA {str(ca.id)[:8]} to heap {str(leaf_msg.context_heap_id)[:8]}'
                        ))
                    linked_count += 1
            except Message.DoesNotExist:
                pass

        self.stdout.write(f'Pass 1 complete: {linked_count} CAs linked\n')

        # Second pass: Find all CompactingActions that are linked to heaps
        compacting_actions = CompactingAction.objects.filter(
            context_heap__isnull=False,
            compact_boundary_message_id__isnull=False
        ).select_related('context_heap')

        self.stdout.write(f'Pass 2: Splitting {compacting_actions.count()} heaps at compact boundaries...')

        split_count = 0
        skip_count = 0

        for ca in compacting_actions:
            boundary_msg = ca.get_boundary_message()
            if not boundary_msg:
                self.stdout.write(self.style.WARNING(
                    f'CA {str(ca.id)[:8]}: Boundary message not found, skipping'
                ))
                skip_count += 1
                continue

            if ca.has_post_compact_messages():
                heap_id = str(ca.context_heap_id)[:8]
                boundary_id = str(ca.compact_boundary_message_id)[:8]

                # Count messages that will be moved
                post_compact_messages = ca.context_heap.messages.filter(
                    message_number__gt=boundary_msg.message_number
                )
                msg_count = post_compact_messages.count()

                if dry_run:
                    self.stdout.write(self.style.WARNING(
                        f'Would split heap {heap_id} at message {boundary_id} '
                        f'(moving {msg_count} messages)'
                    ))
                else:
                    new_heap = ca.split_heap()
                    if new_heap:
                        self.stdout.write(self.style.SUCCESS(
                            f'Split heap {heap_id} at message {boundary_id} → '
                            f'new heap {str(new_heap.id)[:8]} ({msg_count} messages)'
                        ))
                        split_count += 1
                    else:
                        self.stdout.write(self.style.WARNING(
                            f'Failed to split heap {heap_id}'
                        ))
                        skip_count += 1
            else:
                skip_count += 1

        if dry_run:
            self.stdout.write(self.style.SUCCESS(
                f'\nDry run complete: {split_count} heaps would be split, {skip_count} skipped'
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                f'\nSplit complete: {split_count} heaps split, {skip_count} skipped'
            ))
