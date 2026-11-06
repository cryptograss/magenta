"""Indexes for what a poll of a Mood asks every few seconds (views_moods.api_mood_turns, Server-Timing on
6 Nov: 1.4 to 5.4 s a poll): an agent's latest row in a Mood, an agent's latest row anywhere, and a Mood's
rows by source (pins, polls, reactions). Built CONCURRENTLY on PostgreSQL, so writes go on while it builds
(the table is large and the poller and watcher write to it all the while); plainly elsewhere (SQLite tests).
"""

from django.db import migrations, models

INDEXES = [
    ('message_mood_sender_at', ['mood', 'sender', 'created_at'], 'mood_id, sender_id, created_at'),
    ('message_sender_at', ['sender', 'created_at'], 'sender_id, created_at'),
    ('message_mood_source', ['mood', 'source_file'], 'mood_id, source_file'),
]


def build(apps, schema_editor):
    connection = schema_editor.connection
    concurrently = ' CONCURRENTLY' if connection.vendor == 'postgresql' else ''
    with connection.cursor() as cursor:
        for name, _, columns in INDEXES:
            cursor.execute(f'CREATE INDEX{concurrently} IF NOT EXISTS {name} ON conversations_message ({columns})')


def drop(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        for name, _, _ in INDEXES:
            cursor.execute(f'DROP INDEX IF EXISTS {name}')


class Migration(migrations.Migration):

    atomic = False  # CREATE INDEX CONCURRENTLY can't run inside a transaction

    dependencies = [
        ('conversations', '0045_wiki_hook_runs'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[migrations.AddIndex(model_name='message', index=models.Index(fields=fields, name=name))
                              for name, fields, _ in INDEXES],
            database_operations=[migrations.RunPython(build, drop)],
        ),
    ]
