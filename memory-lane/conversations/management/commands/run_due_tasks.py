"""Start each agent's task whose schedule has come round (services/tasks.py). Run every minute, from cron."""

from django.core.management.base import BaseCommand

from conversations.models import ThinkingEntity
from conversations.services import tasks


class Command(BaseCommand):
    help = "Start the agents' tasks that are due (their PickiPedia pages' schedules)"

    def handle(self, *args, **options):
        agents = ThinkingEntity.objects.filter(is_biological_human=False).values_list('name', flat=True)
        for agent, task, what in tasks.due(sorted(agents)):
            said = what if isinstance(what, str) else f"{what.content['state']} in {what.mood_slug}"
            self.stdout.write(f"{agent} {task or ''}: {said}")
