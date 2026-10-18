# Generated manually for Era.is_current field

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('conversations', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='era',
            name='is_current',
            field=models.BooleanField(default=False),
        ),
        migrations.AddConstraint(
            model_name='era',
            constraint=models.UniqueConstraint(
                condition=models.Q(is_current=True),
                fields=['is_current'],
                name='unique_current_era'
            ),
        ),
    ]
