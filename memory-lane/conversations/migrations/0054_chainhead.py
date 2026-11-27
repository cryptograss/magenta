from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('conversations', '0053_tiding_moved_to'),
    ]

    operations = [
        migrations.CreateModel(
            name='ChainHead',
            fields=[
                ('number', models.BigIntegerField(primary_key=True, serialize=False)),
                ('timestamp', models.BigIntegerField(db_index=True, help_text="The block's own time, unix seconds")),
                ('seen_at', models.DateTimeField(help_text='When the watcher heard of it')),
                ('via', models.CharField(help_text="'socket' (pushed to us) or 'polling' (asked for)", max_length=16)),
            ],
            options={
                'db_table': 'chain_heads',
                'ordering': ['number'],
            },
        ),
    ]
