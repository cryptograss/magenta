"""A tiding's PickiPedia page, where it is now when it has moved (Justin, #interfaces, 13 Nov 2026)."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('conversations', '0052_skill_text_is_the_harness'),
    ]

    operations = [
        migrations.AddField(
            model_name='tiding',
            name='moved_to',
            field=models.CharField(blank=True, default='', max_length=300),
        ),
    ]
