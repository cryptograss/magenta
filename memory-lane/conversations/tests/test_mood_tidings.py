"""Tests for /moods/tidings/ and /api/tidings/: the last 20 pictures and each
person's last 20 prompts (a post that addresses an agent, or the post right
before a very short one that does)."""

import uuid
from datetime import timedelta

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from conversations.models import FaceLook, FaceName, Media, Message, Mood, ThinkingEntity
from conversations.services import tidings


def fake_sha(index):
    return f'{index:064x}'


class TidingsPageTests(TestCase):

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
        return Message.objects.create(
            id=uuid.uuid4(), sender=sender, mood=mood, content=text, is_sidechain=sidechain,
            source_file=source, timestamp=1_000 + int(self.clock.timestamp()),
            created_at=self.clock,
        )

    def prompts(self):
        return {human['name']: human['prompts'] for human in tidings.prompt_tidings()}

    # --- the page -----------------------------------------------------------

    def test_tidings_page_renders_and_is_linked_from_moods(self):
        response = self.client.get('/moods/tidings/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('tidings · magenta', response.content.decode())
        self.assertIn('Pictures', response.content.decode())
        self.assertIn('Prompts', response.content.decode())

        moods_html = self.client.get('/moods/').content.decode()
        self.assertIn('href="/moods/tidings/"', moods_html)

    def test_api_tidings_lists_every_human_even_with_no_prompts(self):
        body = self.client.get('/api/tidings/').json()
        names = {human['name'] for human in body['prompts']}
        self.assertEqual(names, {'justin', 'skyler'})
        body['prompts'] = sorted(body['prompts'], key=lambda h: h['name'])
        self.assertEqual(body['prompts'][0]['prompts'], [])
        self.assertIn('pictures', body)

    # --- what a prompt is -----------------------------------------------------

    def test_a_post_that_mentions_an_agent_is_a_prompt(self):
        post = self.post(self.justin, self.mood_a, 'please look at this @magent')
        prompts = self.prompts()['justin']
        self.assertEqual(len(prompts), 1)
        entry = prompts[0]
        self.assertEqual(entry['kind'], 'mention')
        self.assertEqual(entry['id'], str(post.id))
        self.assertEqual(entry['agents'], ['magent'])
        self.assertEqual(entry['mood'], 'magenta-interface')
        self.assertEqual(entry['text'], 'please look at this @magent')

    def test_two_agents_in_one_post(self):
        self.post(self.justin, self.mood_a, 'both of you, now: @magent @re')
        [entry] = self.prompts()['justin']
        self.assertEqual(sorted(entry['agents']), ['magent', 're'])

    def test_a_slash_command_names_the_agents_it_commands(self):
        self.post(self.justin, self.mood_a, '/compact magent keep the status page work in view')
        [entry] = self.prompts()['justin']
        self.assertEqual(entry['agents'], ['magent'])

    def test_a_short_post_pairs_with_the_same_persons_post_right_before_it(self):
        context = self.post(self.justin, self.mood_a, 'the header sits over the text and hides the link')
        trigger = self.post(self.justin, self.mood_a, '@magent')
        prompts = self.prompts()['justin']
        self.assertEqual(len(prompts), 1)
        entry = prompts[0]
        self.assertEqual(entry['kind'], 'paired')
        self.assertEqual(entry['id'], str(context.id))
        self.assertEqual(entry['agents'], ['magent'])
        self.assertEqual(entry['trigger']['id'], str(trigger.id))
        self.assertEqual(entry['trigger']['text'], '@magent')

    def test_the_post_before_a_short_mention_needs_the_same_author(self):
        self.post(self.justin, self.mood_a, 'the header sits over the text')
        self.post(self.skyler, self.mood_a, '@magent')
        self.assertEqual(self.prompts()['justin'], [])
        prompts = self.prompts()['skyler']
        self.assertEqual(len(prompts), 1)
        self.assertEqual(prompts[0]['kind'], 'mention')

    def test_a_short_mention_only_pairs_with_what_comes_right_before_in_the_same_mood(self):
        self.post(self.justin, self.mood_a, 'the header sits over the text')
        self.post(self.skyler, self.mood_a, 'me too, same thing')
        self.post(self.justin, self.mood_b, 'unrelated, another Mood')
        self.post(self.justin, self.mood_a, '@magent')
        prompts = self.prompts()['justin']
        self.assertEqual(len(prompts), 1)
        self.assertEqual(prompts[0]['kind'], 'paired')  # skyler sits between, but the pair looks at justin's own last post
        self.assertEqual(prompts[0]['text'], 'the header sits over the text')

    def test_when_the_post_before_already_mentions_an_agent_each_stands_alone(self):
        first = self.post(self.justin, self.mood_a, 'look here @re')
        second = self.post(self.justin, self.mood_a, '@magent')
        prompts = self.prompts()['justin']  # newest first
        self.assertEqual(len(prompts), 2)
        self.assertTrue(all(entry['kind'] == 'mention' for entry in prompts))
        self.assertEqual(prompts[0]['id'], str(second.id))
        self.assertEqual(prompts[1]['id'], str(first.id))

    def test_very_short_is_under_eighty_characters(self):
        at79 = 'x' * 71 + ' @magent'  # 79 characters: pairs
        at80 = 'x' * 72 + ' @magent'  # 80 characters: stands alone
        self.post(self.justin, self.mood_a, 'give it a look here')
        self.post(self.justin, self.mood_a, at79)
        self.post(self.skyler, self.mood_b, 'give it a look here too')
        self.post(self.skyler, self.mood_b, at80)
        self.assertEqual(self.prompts()['justin'][0]['kind'], 'paired')
        self.assertEqual(self.prompts()['skyler'][0]['kind'], 'mention')

    def test_a_post_that_addresses_no_agent_is_never_a_prompt(self):
        self.post(self.justin, self.mood_a, 'plain talk, no one named')
        self.post(self.justin, self.mood_a, 'short plain note too')
        self.assertEqual(self.prompts()['justin'], [])

    def test_agent_posts_and_sidechains_are_not_prompts(self):
        self.post(self.magent, self.mood_a, '@re check this')
        self.post(self.justin, self.mood_a, '@magent quietly', sidechain=True)
        self.assertEqual(self.prompts()['justin'], [])
        names = {human['name'] for human in tidings.prompt_tidings()}
        self.assertNotIn('magent', names)

    # --- cap and order ---------------------------------------------------------

    def test_only_the_last_twenty_per_human_newest_first(self):
        for index in range(25):
            self.post(self.justin, self.mood_a, f'@magent number {25 - index}')
        prompts = self.prompts()['justin']
        self.assertEqual(len(prompts), 20)
        self.assertEqual(prompts[0]['text'], '@magent number 1')   # created last, newest first
        self.assertEqual(prompts[-1]['text'], '@magent number 20')

    def test_humans_with_recent_prompts_come_first(self):
        self.post(self.skyler, self.mood_a, '@magent two')
        self.post(self.justin, self.mood_b, '@magent four')
        body = self.client.get('/api/tidings/').json()['prompts']
        self.assertEqual(body[0]['name'], 'justin')
        self.assertEqual(body[1]['name'], 'skyler')

    # --- pictures ---------------------------------------------------------------

    def image(self, index=None, mime='image/png', mood=None, **extra):
        at = extra.pop('at', None)
        if index is None:
            self.nsha += 1
            index = self.nsha
        return Media.objects.create(
            sha256=fake_sha(index), mime=mime, data=b'', size=0,
            created_at=at or timezone.now(), **extra,
        )

    def test_a_picture_shows_who_pushed_faces_and_wiki(self):
        picture = self.image(1, added_by=self.justin)
        FaceLook.objects.create(media=picture, model='faces 1', width=10, height=10,
                                faces=[{}, {}, {}])
        FaceName.objects.create(media=picture, box=[0, 0, 1, 1], person='Justin Holmes',
                                by=self.justin, at=timezone.now())
        Message.objects.create(
            id=uuid.uuid4(), sender=self.re, mood=self.mood_b, source_file='wiki-upload',
            timestamp=1_000,
            content={'type': 'wiki-upload', 'by': 'skyler', 'file': 'Test.jpg',
                     'page': 'File:Test.jpg', 'sha': picture.sha256},
        )
        [entry] = tidings.picture_tidings()
        self.assertEqual(entry['sha256'], picture.sha256)
        self.assertEqual(entry['added_by'], 'justin')
        self.assertTrue(entry['faces']['looked'])
        self.assertEqual(entry['faces']['count'], 3)
        self.assertEqual(entry['faces']['named'], 1)
        self.assertEqual(entry['wiki']['page'], 'File:Test.jpg')
        self.assertEqual(entry['wiki']['mood'], 'docs-tests-qa')

    def test_an_untouched_picture_says_so(self):
        self.image(2, added_by=self.skyler)
        [entry] = tidings.picture_tidings()
        self.assertEqual(entry['faces'], {'looked': False})
        self.assertIsNone(entry['wiki'])

    def test_only_images_count_and_the_newest_twenty_are_shown(self):
        clock = timezone.now()
        for index in range(25):
            clock += timedelta(seconds=1)
            self.image(index, at=clock)
        # A voice memo and a HEIC original are newer, but they are not pictures.
        clock += timedelta(seconds=1)
        self.image(at=clock, mime='audio/webm')
        clock += timedelta(seconds=1)
        self.image(at=clock, mime='image/heic')
        pictures = tidings.picture_tidings()
        self.assertEqual(len(pictures), 20)
        self.assertEqual(pictures[0]['sha256'], fake_sha(24))
        self.assertEqual(pictures[-1]['sha256'], fake_sha(5))

    def test_pictures_appear_on_the_page(self):
        self.image(3, added_by=self.justin)
        html = self.client.get('/moods/tidings/').content.decode()
        self.assertIn('not on PickiPedia', html)
        self.assertIn('no faces run', html)
        self.assertIn('justin', html)