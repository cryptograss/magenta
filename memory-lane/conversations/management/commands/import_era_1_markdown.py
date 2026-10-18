"""
Django management command to import Era 1 markdown files.

Usage:
    python manage.py import_era_1_markdown --file tab_2.md
"""

from django.core.management.base import BaseCommand
from conversations.models import Era, ContextWindow, ContextWindowType, Message, ThinkingEntity
from django.utils import timezone
import re
import uuid
from pathlib import Path


class Command(BaseCommand):
    help = 'Import Era 1 conversation from markdown file'

    def add_arguments(self, parser):
        parser.add_argument(
            '--file',
            type=str,
            required=True,
            help='Markdown file to import',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Parse and show what would be imported without saving',
        )

    def parse_markdown(self, filepath):
        """Parse markdown file into messages."""
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()

        # Split on ## User: and ## AI (claude-3.5-sonnet):
        # Pattern matches the delimiters
        sections = re.split(r'\n## (User|AI \(claude-3\.5-sonnet\)):\n', content)

        messages = []
        message_num = 0

        # sections[0] is the header, then alternating role/content pairs
        for i in range(1, len(sections), 2):
            if i + 1 >= len(sections):
                break

            role = sections[i]
            content = sections[i + 1].strip()

            # Skip empty or error messages
            if not content or content == '[text]' or '[ERROR' in content:
                continue

            # Remove [text] prefix if present
            if content.startswith('[text]'):
                content = content[6:].strip()

            sender_name = 'justin' if 'User' in role else 'magent'
            recipient_name = 'magent' if sender_name == 'justin' else 'justin'

            messages.append({
                'sender': sender_name,
                'recipient': recipient_name,
                'content': content,
                'number': message_num
            })
            message_num += 1

        return messages

    def handle(self, *args, **options):
        filepath = options['file']
        dry_run = options['dry_run']

        if not Path(filepath).exists():
            self.stdout.write(self.style.ERROR(f'File not found: {filepath}'))
            return

        # Parse messages
        messages = self.parse_markdown(filepath)
        self.stdout.write(self.style.SUCCESS(f'Parsed {len(messages)} messages from {filepath}'))

        if dry_run:
            for i, msg in enumerate(messages[:5]):  # Show first 5
                self.stdout.write(f"\nMessage {i}:")
                self.stdout.write(f"  From: {msg['sender']} → {msg['recipient']}")
                self.stdout.write(f"  Content preview: {msg['content'][:100]}...")
            if len(messages) > 5:
                self.stdout.write(f"\n... and {len(messages) - 5} more messages")
            return

        # Get entities
        justin = ThinkingEntity.objects.get(name='justin')
        magent = ThinkingEntity.objects.get(name='magent')

        # Get or create Era 1
        era1, created = Era.objects.get_or_create(
            name='Era 1',
            defaults={'created_at': timezone.now()}
        )
        if created:
            self.stdout.write(self.style.SUCCESS(f'Created Era 1: {era1.id}'))
        else:
            self.stdout.write(f'Using existing Era 1: {era1.id}')

        # Create first message
        filename = Path(filepath).name
        first_msg_data = messages[0]
        first_sender = justin if first_msg_data['sender'] == 'justin' else magent
        first_recipient = magent if first_sender == justin else justin

        first_msg = Message.objects.create(
            id=uuid.uuid4(),
            message_number=0,
            content=first_msg_data['content'],
            context_window=None,  # Set after creating context window
            parent=None,
            sender=first_sender,
            source_file=filename
        )
        first_msg.recipients.add(first_recipient)

        # Create context window
        context_window = ContextWindow.objects.create(
            era=era1,
            first_message=first_msg,
            type=ContextWindowType.FRESH,
            created_at=timezone.now()
        )

        # Update first message with context window
        first_msg.context_window = context_window
        first_msg.save()

        self.stdout.write(self.style.SUCCESS(f'Created context window: {context_window.id}'))

        # Create remaining messages
        parent = first_msg
        for msg_data in messages[1:]:
            sender = justin if msg_data['sender'] == 'justin' else magent
            recipient = magent if sender == justin else justin

            msg = Message.objects.create(
                id=uuid.uuid4(),
                message_number=msg_data['number'],
                content=msg_data['content'],
                context_window=context_window,
                parent=parent,
                sender=sender,
                source_file=filename
            )
            msg.recipients.add(recipient)
            parent = msg

        self.stdout.write(self.style.SUCCESS(
            f'Successfully imported {len(messages)} messages into Era 1, context window {context_window.id}'
        ))
