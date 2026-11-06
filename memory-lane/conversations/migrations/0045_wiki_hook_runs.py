from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('conversations', '0044_heap_era_optional'),
    ]

    operations = [
        migrations.CreateModel(
            name='WikiHookRun',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(max_length=300)),
                ('revid', models.IntegerField(blank=True, null=True)),
                ('user', models.CharField(blank=True, default='', max_length=120)),
                ('hook', models.CharField(max_length=40)),
                ('via', models.CharField(max_length=10)),
                ('took_ms', models.IntegerField(default=0)),
                ('result', models.CharField(blank=True, default='', max_length=300)),
                ('failed', models.BooleanField(default=False)),
                ('at', models.DateTimeField(auto_now_add=True, db_index=True)),
            ],
            options={'db_table': 'wiki_hook_runs', 'ordering': ['-at']},
        ),
    ]
