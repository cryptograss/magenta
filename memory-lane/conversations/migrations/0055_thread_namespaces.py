from django.db import migrations, models


def namespace_threads(apps, schema_editor):
    """Give every existing thread its namespaced slug (<parent>.<thread>), keeping its old slug as an alias."""
    Mood = apps.get_model('conversations', 'Mood')
    MoodAlias = apps.get_model('conversations', 'MoodAlias')
    queue = list(Mood.objects.filter(thread_of__isnull=True).order_by('id'))
    seen = {m.pk for m in queue}
    while queue:
        parent = queue.pop(0)
        for thread in Mood.objects.filter(thread_of_id=parent.pk).order_by('id'):
            if thread.pk in seen:
                continue
            seen.add(thread.pk)
            if not thread.slug.startswith(f'{parent.slug}.'):
                local = thread.slug.rsplit('.', 1)[-1]
                base = f'{parent.slug}.{local}'
                slug, n = base, 2
                while (Mood.objects.filter(slug=slug).exclude(pk=thread.pk).exists()
                       or MoodAlias.objects.filter(slug=slug).exclude(mood_id=thread.pk).exists()):
                    slug, n = f'{base}-{n}', n + 1
                MoodAlias.objects.update_or_create(slug=thread.slug, defaults={'mood_id': thread.pk})
                MoodAlias.objects.filter(slug=slug, mood_id=thread.pk).delete()
                Mood.objects.filter(pk=thread.pk).update(slug=slug)
                thread.slug = slug
            queue.append(thread)


class Migration(migrations.Migration):

    dependencies = [
        ('conversations', '0054_chainhead'),
    ]

    operations = [
        migrations.AlterField(
            model_name='mood',
            name='slug',
            field=models.CharField(max_length=200, unique=True),
        ),
        migrations.AlterField(
            model_name='moodalias',
            name='slug',
            field=models.CharField(max_length=200, unique=True),
        ),
        migrations.AlterField(
            model_name='sealedcopy',
            name='mood_slug',
            field=models.CharField(max_length=200),
        ),
        migrations.AlterField(
            model_name='bundlerecord',
            name='mood',
            field=models.CharField(blank=True, db_index=True, default='', max_length=200),
        ),
        migrations.AlterField(
            model_name='mergeorder',
            name='mood',
            field=models.CharField(blank=True, default='', max_length=200),
        ),
        migrations.RunPython(namespace_threads, migrations.RunPython.noop),
    ]
