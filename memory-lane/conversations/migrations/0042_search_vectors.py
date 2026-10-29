"""Search is a full-table scan no more: a stored tsvector (as text, so SQLite's
test harness keeps working), with PostgreSQL-only GIN indexes served from it.

The old queries built to_tsvector('english', content) per row, per query --
over a year of messages that is minutes, and it is why search timed out at
60s on the MCP side (measured 29 Oct). This migration:

  * adds Message.search_vector (plain text in the model),
  * on PostgreSQL only: creates pg_trgm, backfills to_tsvector into the new
    column, and adds two GIN indexes:
      - conversations_message_search_vector_gin on search_vector
        (tsvector, served by search_messages via SearchVectorFromText),
      - conversations_message_content_lower_trgm on lower(content::text)
        (served by api_search's lower + contains narrowing).

The backfill UPDATE on the whole table takes a few minutes; the deploy and
the migration both tolerate that.
"""

from django.db import migrations, models


def seed_vectors(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor != 'postgresql':
        return
    with connection.cursor() as cursor:
        cursor.execute('CREATE EXTENSION IF NOT EXISTS pg_trgm')
        # Backfill once; writers keep the column in step from then on
        # (save hooks populate it; a periodic refresh job can top it up).
        cursor.execute(
            'UPDATE conversations_message'
            ' SET search_vector = to_tsvector(%s, content::text)'
            ' WHERE search_vector IS NULL',
            ['english'],
        )
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS conversations_message_search_vector_gin'
            ' ON conversations_message USING gin (search_vector)'
        )
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS conversations_message_content_lower_trgm'
            ' ON conversations_message USING gin (lower(content::text) gin_trgm_ops)'
        )


def drop_vectors(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor != 'postgresql':
        return
    with connection.cursor() as cursor:
        cursor.execute('DROP INDEX IF EXISTS conversations_message_search_vector_gin')
        cursor.execute('DROP INDEX IF EXISTS conversations_message_content_lower_trgm')


class Migration(migrations.Migration):

    dependencies = [
        ('conversations', '0041_plan_windows'),
    ]

    operations = [
        migrations.AddField(
            model_name='message',
            name='search_vector',
            field=models.TextField(blank=True, null=True),
        ),
        migrations.RunPython(seed_vectors, drop_vectors),
    ]