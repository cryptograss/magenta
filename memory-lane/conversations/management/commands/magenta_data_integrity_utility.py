"""
Magenta data integrity utility.

Performs various integrity checks and fixes on imported conversation data:
- Links continuation messages to their CompactingActions
- (Future: check message numbering, parent chains, heap relationships, etc.)
"""

from django.core.management.base import BaseCommand
from conversations.models import Message, CompactingAction, ContextHeap


class Command(BaseCommand):
    help = 'Check and fix integrity of magenta conversation data'

    def add_arguments(self, parser):
        parser.add_argument(
            '--era-id',
            type=str,
            help='Only check specific era (UUID)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be done without making changes',
        )

    def handle(self, *args, **options):
        era_id = options.get('era_id')
        dry_run = options.get('dry_run', False)

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - no changes will be saved'))

        self.stdout.write('=' * 60)
        self.stdout.write('Magenta Data Integrity Utility')
        self.stdout.write('=' * 60)

        # Check 1: Link orphaned CompactingActions to heaps
        self.link_orphaned_compacting_actions(era_id, dry_run)

        # Check 2: Link continuation messages to CompactingActions
        self.link_continuation_messages(era_id, dry_run)

        self.stdout.write('=' * 60)
        self.stdout.write(self.style.SUCCESS('Integrity check complete'))

    def link_continuation_messages(self, era_id, dry_run):
        """
        Link continuation messages to CompactingActions.

        Strategy:
        1. Find all continuation messages (is_continuation_message=True)
        2. For each, find the CompactingAction that should point to it
        3. Match by:
           - Timestamp: continuation should be shortly after CA
           - Session context: look at nearby sessions
           - Summary content: CA summary might reference what continuation discusses
        """
        self.stdout.write('\n📋 Checking continuation message links...\n')

        # Get all continuation messages
        filters = {'is_continuation_message': True}
        if era_id:
            filters['context_heap__era_id'] = era_id

        cont_messages = Message.objects.filter(**filters).order_by('timestamp')
        total_cont = cont_messages.count()

        self.stdout.write(f'Found {total_cont} continuation messages')

        # Get all CompactingActions
        ca_filters = {}
        if era_id:
            ca_filters['context_heap__era_id'] = era_id

        all_cas = CompactingAction.objects.filter(**ca_filters).select_related('context_heap')
        total_cas = all_cas.count()

        self.stdout.write(f'Found {total_cas} CompactingActions')

        # Track stats
        already_linked = 0
        newly_linked = 0
        no_match_found = 0

        for cont_msg in cont_messages:
            # Check if already linked
            existing_ca = CompactingAction.objects.filter(continuation_message=cont_msg).first()
            if existing_ca:
                already_linked += 1
                self.stdout.write(
                    f'  ✓ {str(cont_msg.id)[:8]} already linked to CA {str(existing_ca.id)[:8]}'
                )
                continue

            # Find matching CA
            # Strategy: Look for CA that ended shortly before this continuation started
            # Continuation messages should be message #0 in their heap
            # Look for CA with timestamp close to but before this message

            cont_timestamp = cont_msg.timestamp
            if not cont_timestamp:
                self.stdout.write(
                    self.style.WARNING(f'  ⚠ {str(cont_msg.id)[:8]} has no timestamp, cannot match')
                )
                no_match_found += 1
                continue

            # Find CAs with timestamps before this continuation
            # Look within reasonable window (30 days = 2592000000 ms)
            # Continuation messages can reference sessions from weeks earlier
            time_window = 2592000000  # 30 days

            candidate_cas = []
            for ca in all_cas:
                if ca.continuation_message_id:
                    continue  # Already linked to something else

                # Get timestamp of CA's ending message
                if ca.ending_message_id:
                    try:
                        ending_msg = Message.objects.get(id=ca.ending_message_id)
                        if ending_msg.timestamp:
                            time_diff = cont_timestamp - ending_msg.timestamp
                            # CA should end before continuation starts
                            if 0 < time_diff < time_window:
                                candidate_cas.append((ca, time_diff, ending_msg))
                    except Message.DoesNotExist:
                        pass

            if not candidate_cas:
                self.stdout.write(
                    self.style.WARNING(
                        f'  ⚠ {str(cont_msg.id)[:8]} - no matching CA found within time window'
                    )
                )
                no_match_found += 1
                continue

            # Sort by time difference (closest match first)
            candidate_cas.sort(key=lambda x: x[1])
            best_ca, time_diff, ending_msg = candidate_cas[0]

            self.stdout.write(
                f'  🔗 {str(cont_msg.id)[:8]} → CA {str(best_ca.id)[:8]} '
                f'(time_diff: {time_diff/1000:.1f}s)'
            )

            if not dry_run:
                best_ca.continuation_message = cont_msg
                best_ca.save()
                newly_linked += 1
            else:
                newly_linked += 1  # Count for dry run stats

        # Summary
        self.stdout.write('\nSummary:')
        self.stdout.write(f'  Already linked: {already_linked}')
        self.stdout.write(f'  Newly linked: {newly_linked}')
        self.stdout.write(f'  No match found: {no_match_found}')
        self.stdout.write(f'  Total: {total_cont}')
