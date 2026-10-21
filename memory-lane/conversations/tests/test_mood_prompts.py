"""Tests for /moods/prompts/ and /api/prompts/: each person's last 20 prompts
(a post that addresses an agent, or the post right before a very short one
that does), with the tokens and cost of what answered each. The page moved
here from Tidings, where the pictures stayed (test_mood_tidings)."""

from datetime import timedelta

from conversations.models import ConversationParticipant
from conversations.services import tidings
from conversations.tests.tidings_helpers import TidingsTests


class PromptsPageTests(TidingsTests):

    # --- the page -----------------------------------------------------------

    def test_prompts_page_renders_and_is_linked_from_moods(self):
        self.post(self.justin, self.mood_a, 'please look at this @magent')
        response = self.client.get('/moods/prompts/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('prompts · magenta', response.content.decode())
        self.assertIn('By each person', response.content.decode())
        self.assertIn('please look at this', response.content.decode())

    def test_api_prompts_lists_every_human_even_with_no_prompts(self):
        body = self.client.get('/api/prompts/').json()
        names = {human['name'] for human in body['prompts']}
        self.assertEqual(names, {'justin', 'skyler'})
        body['prompts'] = sorted(body['prompts'], key=lambda h: h['name'])
        self.assertEqual(body['prompts'][0]['prompts'], [])
        self.assertEqual(set(body), {'prompts'})  # pictures stay on /api/tidings/

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
        body = self.client.get('/api/prompts/').json()['prompts']
        self.assertEqual(body[0]['name'], 'justin')
        self.assertEqual(body[1]['name'], 'skyler')

    # --- Justin's 20 Oct round: time-links, metrics ---------------------------

    def test_prompt_head_links_the_time_not_the_mood(self):
        post = self.post(self.justin, self.mood_a, '@magent please')
        html = self.client.get('/moods/prompts/').content.decode()
        self.assertIn(f'href="/moods/magenta-interface/#m-{post.id}"', html)
        self.assertNotIn(f'<a href="/moods/magenta-interface/#m-{post.id}">#magenta-interface</a>', html)
        self.assertIn(f'>{self.clock:%b %-d, %-I:%M %p}</a>', html)  # the time is what is linked

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

    def test_a_turn_answers_the_latest_prompt_before_it(self):
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
        html = self.client.get('/moods/prompts/').content.decode()
        self.assertNotIn('tokens in', html)

    def test_api_prompts_includes_turn_metrics(self):
        self.post(self.justin, self.mood_a, 'how high is the water there @magent')
        self.agent_row(self.magent, self.mood_a, tokens=(100, 8), cost=0.002)
        self.post(self.skyler, self.mood_a, 'closing the turn')
        body = self.client.get('/api/prompts/').json()
        justin = next(human for human in body['prompts'] if human['name'] == 'justin')
        [turn] = justin['prompts'][0]['turns']
        self.assertEqual((turn['agent'], turn['in'], turn['out'], turn['usd'], turn['tokens'], turn['cost']),
                         ('magent', 100, 8, 0.002, '100→8', '<$0.01'))

    def test_a_model_with_no_price_shows_no_cost(self):
        self.post(self.justin, self.mood_a, 'over to you @re')
        self.agent_row(self.re, self.mood_a, tokens=(120_900, 400))  # no harness cost, and no list price for it
        [turn] = self.prompts()['justin'][0]['turns']
        self.assertEqual((turn['tokens'], turn['cost'], turn['priced']), ('121k→400', '', False))
        html = self.client.get('/moods/prompts/').content.decode()
        self.assertIn('no price known for this model', html)
        self.assertNotIn('<$0.01', html)

    def test_tokens_and_cost_written_as_the_page_writes_them(self):
        self.post(self.justin, self.mood_a, 'a big one @magent')
        self.agent_row(self.magent, self.mood_a, tokens=(535_000, 2_100), cost=0.0512)
        self.assertIn('@magent 535k→2.1k · $0.05', self.client.get('/moods/prompts/').content.decode())

class PromptsQueriesTests(TidingsTests):
    """How many questions the prompts ask of the database doesn't grow with the record (re, 21 Oct: the page
    took 17 s live -- a query for every agent row read, and two for every reply)."""

    def asked(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        with CaptureQueriesContext(connection) as queries:
            tidings.prompts_state()
        return len(queries)

    def say_a_while(self, rounds):
        last = None
        for i in range(rounds):
            last = self.post(self.justin, self.mood_a, (f'↩ #m-{last.id}\n' if last else '') + f'@magent round {i}')
            self.agent_row(self.magent, self.mood_a, tokens=(100 + i, 8))
            self.agent_row(self.magent, self.mood_a, tokens=(200 + i, 9))

    def test_the_same_few_questions_however_much_was_said(self):
        self.say_a_while(3)
        few = self.asked()
        self.say_a_while(30)
        self.assertEqual(self.asked(), few)
