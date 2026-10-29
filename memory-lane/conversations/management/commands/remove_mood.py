"""Take a Mood out: one opened by mistake (a double click, Justin, 29 Oct), so its name is free again.

Shows what it would do, and does it only with --yes. Refused when anyone said anything there or it has threads
of its own, unless --with-posts: then what was said stays in the record, out of any Mood (Message.mood is
SET_NULL). Its own system lines go with it (created, settings changed), and so does the line in the Mood it
was a thread of that says it was opened. --give-name-to <slug> then renames that Mood to its own title, so its
slug follows it into the name just freed (how_i_speak-2 -> how_i_speak).
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from conversations.models import Message, Mood


class Command(BaseCommand):
    help = "Delete a Mood opened by mistake, freeing its name."

    def add_arguments(self, parser):
        parser.add_argument('slug')
        parser.add_argument('--with-posts', action='store_true',
                            help='Even when something was said there: it stays in the record, in no Mood')
        parser.add_argument('--give-name-to', metavar='SLUG', help='Then rename this Mood to its title')
        parser.add_argument('--yes', action='store_true', help='Do it; without, only say what it would do')

    @transaction.atomic
    def handle(self, *args, **options):
        mood = Mood.by_slug(options['slug'])
        if mood is None:
            raise CommandError(f"no Mood #{options['slug']}")
        heir = None
        if options['give_name_to']:
            heir = Mood.by_slug(options['give_name_to'])
            if heir is None or heir.pk == mood.pk:
                raise CommandError(f"no other Mood #{options['give_name_to']}")
        posts = Message.objects.filter(mood=mood).exclude(sender_id='system')
        threads = list(mood.threads.values_list('slug', flat=True))
        if (posts.exists() or threads) and not options['with_posts']:
            raise CommandError(f'#{mood.slug} has {posts.count()} posts and threads {threads or "none"}; '
                               f'--with-posts to take it out anyway')
        own = Message.objects.filter(mood=mood, sender_id='system')
        opened = Message.objects.filter(mood_id=mood.thread_of_id, sender_id='system', content__type='thread',
                                        content__to_mood=mood.slug) if mood.thread_of_id else Message.objects.none()
        self.stdout.write(f'#{mood.slug} ({mood.title!r}): {own.count()} system lines and {opened.count()} '
                          f'"opened a thread" lines go; {posts.count()} posts stay in the record, in no Mood'
                          + (f'; then #{heir.slug} is renamed to {heir.title!r}' if heir else ''))
        if not options['yes']:
            self.stdout.write('(nothing done: --yes to do it)')
            return
        opened.delete()
        own.delete()
        mood.delete()
        if heir:
            heir.rename(heir.title)
            self.stdout.write(f'now #{heir.slug}')
        self.stdout.write('done')
