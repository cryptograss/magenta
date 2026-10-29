from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('conversations', '0040_threads'),
    ]

    operations = [
        migrations.CreateModel(
            name='PlanWindow',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('window', models.CharField(max_length=40)),
                ('utilization', models.FloatField()),
                ('resets_at', models.DateTimeField(blank=True, null=True)),
                ('status', models.CharField(blank=True, default='', max_length=40)),
                ('agent', models.CharField(blank=True, default='', max_length=60)),
                ('info', models.JSONField(default=dict)),
                ('at', models.DateTimeField(auto_now_add=True, db_index=True)),
            ],
            options={
                'db_table': 'plan_windows',
                'indexes': [models.Index(fields=['window', 'at'], name='plan_window_window_fc1913_idx')],
            },
        ),
    ]
