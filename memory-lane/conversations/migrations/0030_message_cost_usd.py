from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('conversations', '0029_face_looks'),
    ]

    operations = [
        migrations.AddField(
            model_name='message',
            name='cost_usd',
            field=models.FloatField(blank=True, null=True),
        ),
    ]
