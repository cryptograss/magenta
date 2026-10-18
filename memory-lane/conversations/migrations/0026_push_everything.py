from django.db import migrations, models


class Migration(migrations.Migration):
    """A bell that tells of every message from people, or from agents, not only mentions and answers."""

    dependencies = [
        ('conversations', '0025_harness_models'),
    ]

    operations = [
        migrations.AddField(model_name='pushsubscription', name='from_people', field=models.BooleanField(default=False)),
        migrations.AddField(model_name='pushsubscription', name='from_agents', field=models.BooleanField(default=False)),
    ]
