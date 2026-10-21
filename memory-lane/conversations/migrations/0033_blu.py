"""blu, the blue agent: the record's third agent, for the connectome (Justin,
magent-self, 2026-10-19: "a blue agent ... whose job it will be to process
connectome, find connectome metadata"). Its rules: Cryptograss:Agents/Blu/Rules.

It has to exist before its first turn: mentions resolve against the thinking
entities, so until it does nobody can @blu, and the runner refuses its posts.
"""

from django.db import migrations


def add_blu(apps, schema_editor):
    ThinkingEntity = apps.get_model('conversations', 'ThinkingEntity')
    if not ThinkingEntity.objects.filter(name='blu').exists():
        ThinkingEntity.objects.create(name='blu', participant_type='ai', is_biological_human=False)


class Migration(migrations.Migration):

    dependencies = [('conversations', '0032_tiding_per_link')]

    operations = [migrations.RunPython(add_blu, migrations.RunPython.noop)]
