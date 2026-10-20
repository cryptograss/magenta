"""Tests for /moods/tidings/ and /api/tidings/: the last 20 pictures and each
person's last 20 prompts (a post that addresses an agent, or the post right
before a very short one that does)."""

import uuid
from datetime import timedelta

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from conversations.models import ConversationParticipant, FaceLook, FaceName, Media, Message, Mood, ThinkingEntity
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
        message = Message.objects.create(
            id=uuid.uuid4(), sender=sender, mood=mood, content=text, is_sidechain=sidechain,
            source_file=source, timestamp=1_000 + int(self.clock.timestamp()),
        )
        Message.objects.filter(id=message.id).update(created_at=self.clock)  # auto_now_add ignores it given
        message.refresh_from_db()
        return message

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

    def test_a_short_mention_long_after_stands_alone(self):
        self.post(self.justin, self.mood_a, 'look at this one from the jam last night')
        self.clock += tidings.PAIR_WITHIN
        self.post(self.justin, self.mood_a, '@magent')
        self.assertEqual(self.prompts()['justin'][0]['kind'], 'mention')

    def test_a_command_never_pairs(self):
        self.post(self.justin, self.mood_a, 'look at this one from the jam last night')
        self.post(self.justin, self.mood_a, '/compact magent keep the setlist')
        [entry] = self.prompts()['justin']
        self.assertEqual((entry['kind'], entry['text']), ('mention', '/compact magent keep the setlist'))

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

    def test_only_pictures_a_mood_shows(self):
        self.image(4, added_by=self.justin, posted=False)  # uploaded, never posted
        pasted = self.image(5, added_by=self.justin, posted=False)
        self.clock += timedelta(seconds=1)
        Message.objects.create(id=uuid.uuid4(), sender=self.justin, content=f'see ![image]({pasted.url})',
                               source_file='session.jsonl', timestamp=1_000)  # a terminal session's
        in_mood = self.image(6, posted=False)
        self.post(self.skyler, self.mood_b, f'![image]({in_mood.url}) from me')
        [entry] = tidings.picture_tidings()
        self.assertEqual((entry['sha256'], entry['added_by'], entry['mood']), (in_mood.sha256, 'skyler', 'docs-tests-qa'))

    def test_pictures_appear_on_the_page(self):
        self.image(3, added_by=self.justin)
        html = self.client.get('/moods/tidings/').content.decode()
        self.assertIn('not on wiki', html)
        self.assertIn('no faces', html)
        self.assertIn('justin', html)

    # --- Justin's 20 Oct round: no intro, time-links, thumbnails, metrics -----

    def test_no_intro_description_and_no_lede(self):
        html = self.client.get('/moods/tidings/').content.decode()
        self.assertNotIn("record's newest", html)
        self.assertNotIn('class="lede"', html)

    def test_prompt_head_links_the_time_not_the_mood(self):
        post = self.post(self.justin, self.mood_a, '@magent please')
        html = self.client.get('/moods/tidings/').content.decode()
        self.assertIn(f'href="/moods/magenta-interface/#m-{post.id}"', html)
        self.assertNotIn(f'<a href="/moods/magenta-interface/#m-{post.id}">#magenta-interface</a>', html)
        self.assertIn(f'>{self.clock:%b %-d, %-I:%M %p}</a>', html)  # the time is what is linked

    def test_pictures_carry_a_preview_url_and_the_page_uses_it(self):
        picture = self.image(7, added_by=self.justin)
        [entry] = tidings.picture_tidings()
        self.assertEqual(entry['preview'], f'{entry["url"]}/preview')
        html = self.client.get('/moods/tidings/').content.decode()
        self.assertIn(f'src="/moods/media/{picture.sha256}.png/preview"', html)

    def agent_row(self, agent, mood, tokens=(100, 8), cost=None, minutes=1):
        self.clock += timedelta(minutes=minutes)
        message = self.post(agent, mood, 'an answer of some kind')
        Message.objects.filter(id=message.id).update(
            input_tokens=tokens[0], output_tokens=tokens[1], cost_usd=cost)
        message.refresh_from_db()
        return message

    def test_a_prompt_carries_its_answers_tokens_and_cost(self):
        self.post(self.justin, self.mood_a, 'how high is the water there @magent')
        # One response split over two rows (its thinking, its words) repeats its
        # usage; the next response carries the real growth.
        self.agent_row(self.magent, self.mood_a, tokens=(100, 8), cost=0.002)
        self.agent_row(self.magent, self.mood_a, tokens=(100, 8), cost=0.002)
        self.agent_row(self.magent, self.mood_a, tokens=(140, 12), cost=0.003)
        self.post(self.skyler, self.mood_a, 'thanks!')  # ends the turn
        [entry] = self.prompts()['justin']
        [turn] = entry['turns']
        self.assertEqual(turn['agent'], 'magent')
        self.assertEqual((turn['in'], turn['out']), (240, 20))  # the repeated pair counted once
        self.assertEqual(turn['usd'], 0.005)
        self.assertTrue(turn['paid'])
        self.assertEqual(turn['calls'], 2)

    def test_a_turn_within_the_window_only_answers_its_own_prompt(self):
        self.post(self.justin, self.mood_a, 'first, look at this @magent')
        self.post(self.justin, self.mood_a, 'later, look at that @magent')
        self.agent_row(self.magent, self.mood_a, tokens=(10, 1), cost=0.0001)
        self.post(self.skyler, self.mood_a, 'closing the turn')
        newer, older = self.prompts()['justin']
        self.assertEqual(len(newer['turns']), 1)   # the newest prompt before the run
        self.assertEqual(older['turns'], [])       # the earlier one goes unanswered

    def test_a_turn_after_a_post_naming_no_one_answers_no_prompt(self):
        # magent listening to everything: it answers a post that named no one, not the prompt before it.
        self.post(self.justin, self.mood_a, 'have a look at this @magent')
        self.agent_row(self.magent, self.mood_a, tokens=(100, 8), cost=0.002)
        self.post(self.justin, self.mood_a, 'and something else, said to the room')
        self.agent_row(self.magent, self.mood_a, tokens=(900, 90), cost=0.02)
        [turn] = self.prompts()['justin'][0]['turns']
        self.assertEqual((turn['in'], turn['out']), (100, 8))

    def test_tool_results_and_events_inside_a_turn_leave_it_one(self):
        tool = ConversationParticipant.objects.get_or_create(name='tool-result', defaults={'participant_type': 'tool'})[0]
        self.post(self.justin, self.mood_a, 'look it up @magent')
        self.agent_row(self.magent, self.mood_a, tokens=(100, 8), cost=0.002)
        self.post(tool, self.mood_a, '{"output": "found it"}')
        self.agent_row(self.magent, self.mood_a, tokens=(140, 12), cost=0.003)
        [turn] = self.prompts()['justin'][0]['turns']
        self.assertEqual((turn['in'], turn['out'], turn['calls']), (240, 20, 2))

    def test_an_answer_hours_later_still_answers_it(self):
        self.post(self.justin, self.mood_a, 'whenever you get to it @magent')
        self.clock += timedelta(hours=5)
        self.agent_row(self.magent, self.mood_a, tokens=(10, 1), cost=0.0001)
        self.assertEqual(len(self.prompts()['justin'][0]['turns']), 1)

    def test_an_unanswered_prompt_says_so_with_no_turns(self):
        self.post(self.justin, self.mood_a, 'asking into the dark @magent')
        [entry] = self.prompts()['justin']
        self.assertEqual(entry['turns'], [])
        html = self.client.get('/moods/tidings/').content.decode()
        self.assertNotIn('tokens in', html)

    def test_api_tidings_includes_turn_metrics(self):
        self.post(self.justin, self.mood_a, 'how high is the water there @magent')
        self.agent_row(self.magent, self.mood_a, tokens=(100, 8), cost=0.002)
        self.post(self.skyler, self.mood_a, 'closing the turn')
        body = self.client.get('/api/tidings/').json()
        justin = next(human for human in body['prompts'] if human['name'] == 'justin')
        [turn] = justin['prompts'][0]['turns']
        self.assertEqual((turn['agent'], turn['in'], turn['out'], turn['usd'], turn['tokens'], turn['cost']),
                         ('magent', 100, 8, 0.002, '100→8', '<$0.01'))

    def test_a_model_with_no_price_shows_no_cost(self):
        self.post(self.justin, self.mood_a, 'over to you @re')
        self.agent_row(self.re, self.mood_a, tokens=(120_900, 400))  # no harness cost, and no list price for it
        [turn] = self.prompts()['justin'][0]['turns']
        self.assertEqual((turn['tokens'], turn['cost'], turn['priced']), ('121k→400', '', False))
        html = self.client.get('/moods/tidings/').content.decode()
        self.assertIn('no price known for this model', html)
        self.assertNotIn('<$0.01', html)

    def test_tokens_and_cost_written_as_the_page_writes_them(self):
        self.post(self.justin, self.mood_a, 'a big one @magent')
        self.agent_row(self.magent, self.mood_a, tokens=(535_000, 2_100), cost=0.0512)
        self.assertIn('@magent 535k→2.1k · $0.05', self.client.get('/moods/tidings/').content.decode())

    # --- the preview endpoint ----------------------------------------------------

    def small_png(self, index, size=(640, 320)):
        import io

        from PIL import Image
        out = io.BytesIO()
        Image.new('RGB', size, (200, 40, 40)).save(out, format='PNG')
        data = out.getvalue()
        return Media.objects.create(sha256=fake_sha(index), mime='image/png', data=data,
                                    size=len(data), added_by=self.justin)

    def test_preview_serves_a_smaller_picture(self):
        picture = self.small_png(60)
        response = self.client.get(f'/moods/media/{picture.sha256}.png/preview')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'image/jpeg')  # opaque picture: a JPEG
        self.assertTrue(response.content.startswith(b'\xff\xd8'))
        self.assertLess(len(response.content), picture.size)

    def test_preview_keeps_transparency_when_there_is_any(self):
        import io

        from PIL import Image
        out = io.BytesIO()
        Image.new('RGBA', (300, 300), (0, 0, 0, 0)).save(out, format='PNG')
        picture = Media.objects.create(sha256=fake_sha(61), mime='image/png', data=out.getvalue(),
                                       size=len(out.getvalue()), added_by=self.justin)
        response = self.client.get(f'/moods/media/{picture.sha256}.png/preview')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'image/png')
        self.assertTrue(response.content.startswith(b'\x89PNG'))

    def test_preview_serves_only_pictures_that_exist(self):
        sound = self.image(mime='audio/webm', posted=False)
        self.assertEqual(self.client.get(f'/moods/media/{sound.sha256}.webm/preview').status_code, 404)
        self.assertEqual(self.client.get(f'/moods/media/{fake_sha(99)}.png/preview').status_code, 404)
        self.assertEqual(self.client.get('/moods/media/not-a-sha.png/preview').status_code, 404)
