"""The stage shared by the tidings and prompts tests: the people, two Moods,
a clock, and the ways of posting a message, a picture, or an agent's answer.

Not itself a test module (the runner looks for test*.py); it only holds what
both of those suites build on."""

import uuid
from datetime import timedelta

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from conversations.models import Media, Message, Mood, ThinkingEntity
from conversations.services import tidings


def fake_sha(index):
    return f'{index:064x}'


class TidingsTests(TestCase):

    def setUp(self):
        cache.clear()
        self.magent, _ = ThinkingEntity.objects.get_or_create(name='magent', defaults={'is_biological_human': False})
        self.re, _ = ThinkingEntity.objects.get_or_create(name='re', defaults={'is_biological_human': False})
        self.justin, _ = ThinkingEntity.objects.get_or_create(name='justin', defaults={'is_biological_human': True})
        self.skyler, _ = ThinkingEntity.objects.get_or_create(name='skyler', defaults={'is_biological_human': True})
        self.mood_a = Mood.objects.create(slug='magenta-interface', title='#magenta-interface')
        self.mood_b = Mood.objects.create(slug='docs-tests-qa', title='#docs-tests-qa')
        self.clock = timezone.now()
        self.nsha = 100

    def post(self, sender, mood, text, sidechain=False, source='mood-web'):
        self.clock += timedelta(seconds=1)
        message = Message.objects.create(
            id=uuid.uuid4(), sender=sender, mood=mood, content=text, is_sidechain=sidechain,
            source_file=source, timestamp=1_000 + int(self.clock.timestamp()),
        )
        Message.objects.filter(id=message.id).update(created_at=self.clock)  # auto_now_add ignores it given
        message.refresh_from_db()
        return message

    def prompts(self):
        return {human['name']: human['prompts'] for human in tidings.prompt_tidings()}

    def image(self, index=None, mime='image/png', mood=None, posted=True, **extra):
        """A picture, and (unless not `posted`) a post in a Mood showing it."""
        at = extra.pop('at', None)
        if index is None:
            self.nsha += 1
            index = self.nsha
        media = Media.objects.create(
            sha256=fake_sha(index), mime=mime, data=b'', size=0,
            created_at=at or timezone.now(), **extra,
        )
        if posted:
            self.post(media.added_by or self.justin, mood or self.mood_a, f'look ![image]({media.url})')
        return media

    def agent_row(self, agent, mood, tokens=(100, 8), cost=None, minutes=1):
        """An agent's answered turn step, with its measured usage."""
        self.clock += timedelta(minutes=minutes)
        message = self.post(agent, mood, 'an answer of some kind')
        Message.objects.filter(id=message.id).update(
            input_tokens=tokens[0], output_tokens=tokens[1], cost_usd=cost)
        message.refresh_from_db()
        return message

    def small_png(self, index, size=(640, 320)):
        import io

        from PIL import Image
        out = io.BytesIO()
        Image.new('RGB', size, (200, 40, 40)).save(out, format='PNG')
        data = out.getvalue()
        return Media.objects.create(sha256=fake_sha(index), mime='image/png', data=data,
                                    size=len(data), added_by=self.justin)