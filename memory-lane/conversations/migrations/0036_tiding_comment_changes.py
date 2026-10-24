from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('conversations', '0035_tiding_comments'),
    ]

    operations = [
        migrations.AddField(
            model_name='tidingcomment',
            name='changed',
            field=models.CharField(blank=True, default='', max_length=10),
        ),
        migrations.AddField(
            model_name='tidingcomment',
            name='changed_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
