"""The emergent rename's last step (PickiPedia: User:Magent/Emergent rename): the fields that name one of
the emergents say so. Columns renamed in place; no row changes."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('conversations', '0042_search_vectors'),
    ]

    operations = [
        # The settings index names the column, so it is made again over the new one (a small table).
        migrations.RemoveIndex(model_name='setting', name='settings_key_3e4024_idx'),
        migrations.RenameField(model_name='setting', old_name='agent', new_name='emergent'),
        migrations.RenameField(model_name='harnessmodels', old_name='agent', new_name='emergent'),
        migrations.RenameField(model_name='bundlerecord', old_name='agent', new_name='emergent'),
        migrations.RenameField(model_name='planwindow', old_name='agent', new_name='emergent'),
        migrations.RenameField(model_name='pushsubscription', old_name='from_agents', new_name='from_emergents'),
        migrations.AddIndex(
            model_name='setting',
            index=models.Index(fields=['key', 'mood', 'emergent', 'created_at'], name='settings_key_77ae78_idx'),
        ),
    ]
