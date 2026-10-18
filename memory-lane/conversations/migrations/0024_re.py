"""re, the red agent: the record's second agent (hunter/CONTAINERS.md in maybelle-config).

It has to exist before its first turn: mentions resolve against the thinking
entities, so until it does nobody can @re, and its poller's look for mentions
answers 404.
"""

from django.db import migrations


def add_re(apps, schema_editor):
    ThinkingEntity = apps.get_model('conversations', 'ThinkingEntity')
    if not ThinkingEntity.objects.filter(name='re').exists():
        ThinkingEntity.objects.create(name='re', participant_type='ai', is_biological_human=False)


class Migration(migrations.Migration):

    dependencies = [('conversations', '0023_task_notices_are_not_people')]

    operations = [migrations.RunPython(add_re, migrations.RunPython.noop)]
