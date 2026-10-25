"""Polls: asked with /poll in any Mood, voted on by name -- people and agents -- and closed by whoever asked."""

import json
import uuid

from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from conversations.models import Message, Mood, ThinkingEntity
from conversations.services import mood_auth, polls
from poller.mood_poller import _SILENT_REPLY, open_polls


class ParseTest(TestCase):

    def test_choices_after_bars_or_as_a_list_and_yes_no_without(self):
        self.assertEqual(polls.parse('/poll Ship it tonight? | yes | no | wait a day'),
                         {'question': 'Ship it tonight?', 'detail': '', 'choices': ['yes', 'no', 'wait a day']})
        self.assertEqual(polls.parse('/poll Which tune opens?\nfor Saturday\n- Old Joe Clark\n- Salt Creek\n2. Whiskey Before Breakfast'),
                         {'question': 'Which tune opens?', 'detail': 'for Saturday',
                          'choices': ['Old Joe Clark', 'Salt Creek', 'Whiskey Before Breakfast']})
        self.assertEqual(polls.parse('/poll @core Beta tonight?')['choices'], ['yes', 'no'])
        self.assertEqual(polls.parse('↩ #m-0123abcd-0000-4000-8000-000000000000\n/poll Agreed? | aye | nay')['choices'], ['aye', 'nay'])

    def test_not_a_poll_and_polls_that_cannot_be(self):
        for text in ('a poll would be fun', '/polling', '/clips tony', ''):
            self.assertIsNone(polls.parse(text), text)
        for text in ('/poll', '/poll | a | b', '/poll Q? | one', '/poll Q? | a | A',
                     '/poll Q? | ' + ' | '.join(str(i) for i in range(13)), '/poll Q? | a | ' + 'b' * 81):
            with self.assertRaises(polls.Refused, msg=text):
                polls.parse(text)


class PollsTest(TestCase):

    def setUp(self):
        cache.clear()
        self.skyler = ThinkingEntity.objects.update_or_create(name='skyler', defaults={'is_biological_human': True})[0]
        self.justin = ThinkingEntity.objects.update_or_create(name='justin', defaults={'is_biological_human': True})[0]
        self.magent = ThinkingEntity.objects.update_or_create(name='magent', defaults={'is_biological_human': False})[0]
        self.re = ThinkingEntity.objects.update_or_create(name='re', defaults={'is_biological_human': False})[0]
        self.mood = Mood.objects.create(slug='general', title='general')

    def client_for(self, who, tier='key'):
        _, token = mood_auth.enrol_device(who, 'phone', tier=tier)
        client = Client()
        client.cookies[mood_auth.COOKIE] = token
        return client

    def ask(self, client, text='/poll Beta tonight? | yes | no | wait'):
        r = client.post('/api/moods/general/say/', json.dumps({'text': text}), content_type='application/json')
        return r

    def vote(self, client, poll_id, choice):
        return client.post(f'/api/moods/general/messages/{poll_id}/vote/', json.dumps({'choice': choice}),
                           content_type='application/json')

    def close(self, client, poll_id):
        return client.post(f'/api/moods/general/messages/{poll_id}/close-poll/', '{}', content_type='application/json')

    def tally(self, state):
        return {c['text']: c['votes'] for c in state['choices']}

    def test_asked_voted_changed_taken_back(self):
        skyler, justin = self.client_for(self.skyler), self.client_for(self.justin, 'wiki')
        poll = self.ask(skyler).json()['id']
        self.assertEqual(self.vote(skyler, poll, 'yes').json()['mine'], 'yes')
        state = self.vote(justin, poll, 'wait').json()['poll']
        self.assertEqual(self.tally(state), {'yes': ['skyler'], 'no': [], 'wait': ['justin']})
        state = self.vote(justin, poll, 'yes').json()['poll']  # changed: one vote each
        self.assertEqual(self.tally(state), {'yes': ['skyler', 'justin'], 'no': [], 'wait': []})
        r = self.vote(skyler, poll, 'yes').json()  # pressed again: taken back
        self.assertIsNone(r['mine'])
        self.assertEqual(self.tally(r['poll'])['yes'], ['justin'])
        self.assertEqual(self.vote(skyler, poll, 'maybe').status_code, 400)
        self.assertEqual(self.vote(Client(), poll, 'yes').status_code, 401)

    def test_a_malformed_poll_is_refused_not_posted(self):
        r = self.ask(self.client_for(self.skyler), '/poll Q? | only one')
        self.assertEqual(r.status_code, 400)
        self.assertIn('two', r.json()['error'])
        self.assertFalse(Message.objects.filter(mood=self.mood).exists())

    def test_only_the_asker_closes_it_then_no_votes_and_it_reopens(self):
        skyler, justin = self.client_for(self.skyler), self.client_for(self.justin)
        poll = self.ask(skyler).json()['id']
        self.vote(justin, poll, 'no')
        self.assertEqual(self.close(justin, poll).status_code, 403)
        r = self.close(skyler, poll).json()
        self.assertTrue(r['closed'])
        self.assertEqual(r['poll']['closed']['by'], 'skyler')
        self.assertEqual(self.vote(justin, poll, 'yes').status_code, 409)
        self.assertFalse(self.close(skyler, poll).json()['closed'])
        self.assertEqual(self.vote(justin, poll, 'yes').status_code, 200)

    @override_settings(MOOD_ADMINS=('justin',))
    def test_an_admin_may_close_anyones(self):
        poll = self.ask(self.client_for(self.skyler)).json()['id']
        self.assertTrue(self.close(self.client_for(self.justin), poll).json()['closed'])

    def test_every_look_brings_them_and_votes_are_never_messages(self):
        skyler = self.client_for(self.skyler)
        poll = self.ask(skyler).json()['id']
        self.vote(skyler, poll, 'wait')
        page = Client().get('/api/moods/general/turns/').json()
        self.assertEqual([t['id'] for t in page['turns']], [poll])
        turn = page['turns'][0]
        self.assertEqual(turn['poll']['choices'], ['yes', 'no', 'wait'])
        self.assertIn('Beta tonight?', turn['poll']['question_html'])
        self.assertEqual(self.tally(page['polls'][poll]), {'yes': [], 'no': [], 'wait': ['skyler']})
        self.assertIsNone(page['polls'][poll]['closed'])

    def test_edited_it_changes_deleted_it_goes(self):
        skyler = self.client_for(self.skyler)
        poll = self.ask(skyler).json()['id']
        self.vote(skyler, poll, 'wait')
        msg = Message.objects.get(id=poll)
        msg.content = '/poll Beta tonight? | yes | no'
        msg.save()
        self.assertEqual(self.tally(polls.in_mood(self.mood)[poll]), {'yes': [], 'no': []})  # its vote's choice is gone
        msg.content = 'message deleted'
        msg.save()
        self.assertEqual(polls.in_mood(self.mood), {})
        self.assertEqual(self.vote(skyler, poll, 'yes').status_code, 410)

    def test_polls_and_plebiscites_has_every_moods_open_first(self):
        skyler = self.client_for(self.skyler)
        Mood.objects.create(slug='jams', title='jams')
        first = self.ask(skyler).json()['id']
        second = skyler.post('/api/moods/jams/say/', json.dumps({'text': '/poll Salt Creek next?'}),
                             content_type='application/json').json()['id']
        self.close(skyler, first)
        page = skyler.get('/api/polls/').json()
        self.assertEqual([(p['id'], p['mood']) for p in page['polls']], [(second, 'jams'), (first, 'general')])
        self.assertEqual(page['viewer'], 'skyler')
        self.assertIn('skyler', page['humans'])
        self.assertNotIn('magent', page['humans'])
        self.assertEqual(Client().get('/moods/polls/?embed=1').status_code, 200)

    # --- agents ---------------------------------------------------------------------------------------

    def agent_says(self, agent, text, session=None):
        return Message.objects.create(id=uuid.uuid4(), sender=agent, mood=self.mood, timestamp=2,
                                      session_id=session or uuid.uuid4(), content=[{'type': 'text', 'text': text}])

    def test_an_agent_votes_with_a_tag_and_says_why(self):
        poll = self.ask(self.client_for(self.skyler)).json()['id']
        said = self.agent_says(self.re, 'Tonight works for me.\n<vote choice="Yes"/>\nThe tests are green.')
        polls.agent_voted(said.session_id, 're')
        polls.agent_voted(said.session_id, 're')  # read again with the next post: still one vote, never taken back
        said.refresh_from_db()
        self.assertEqual(said.content[0]['text'], f'Tonight works for me.\n🗳 voted **yes** on #m-{poll}\nThe tests are green.')
        self.assertEqual(self.tally(polls.in_mood(self.mood)[poll])['yes'], ['re'])

    def test_only_a_vote_is_a_dot_and_a_number_names_the_choice(self):
        poll = self.ask(self.client_for(self.skyler)).json()['id']
        said = self.agent_says(self.magent, '<vote choice="3"/>')
        polls.agent_voted(said.session_id, 'magent')
        said.refresh_from_db()
        self.assertEqual(said.content[0]['text'], '<silent>voted “wait” on “Beta tonight?”</silent>')
        self.assertEqual(self.tally(polls.in_mood(self.mood)[poll])['wait'], ['magent'])
        self.assertTrue(_SILENT_REPLY.match('<vote choice="3"/>'))

    def test_which_poll_newest_open_or_named(self):
        skyler = self.client_for(self.skyler)
        older = self.ask(skyler, '/poll Fiddle or banjo? | fiddle | banjo').json()['id']
        newer = self.ask(skyler).json()['id']
        said = self.agent_says(self.re, f'<vote choice="banjo" poll="#m-{older[:8]}"/>\n<vote choice="no"/>')
        polls.agent_voted(said.session_id, 're')
        mood = polls.in_mood(self.mood)
        self.assertEqual(self.tally(mood[older])['banjo'], ['re'])
        self.assertEqual(self.tally(mood[newer])['no'], ['re'])
        tune = self.ask(skyler, '/poll Which tune? | Salt Creek | Old Joe Clark').json()['id']
        said = self.agent_says(self.magent, '<vote choice="wait"/>')  # the newest open poll with that choice
        polls.agent_voted(said.session_id, 'magent')
        self.assertEqual(self.tally(polls.in_mood(self.mood)[newer])['wait'], ['magent'])
        self.assertEqual(sum(len(c['votes']) for c in polls.in_mood(self.mood)[tune]['choices']), 0)
        self.close(skyler, newer)
        lost = self.agent_says(self.magent, '<vote choice="no" poll="#m-nothing1"/>')
        polls.agent_voted(lost.session_id, 'magent')
        lost.refresh_from_db()
        self.assertIn('found no open poll', lost.content[0]['text'])

    def test_an_agents_poll_is_a_poll(self):
        said = self.agent_says(self.magent, '/poll Which tune next? | Salt Creek | Old Joe Clark')
        polls.polls_posted(said.session_id, 'magent')
        polls.polls_posted(said.session_id, 'magent')
        self.assertEqual(list(polls.in_mood(self.mood)), [str(said.id)])
        self.assertEqual(Message.objects.filter(source_file=polls.POLL).count(), 1)

    def test_a_woken_agent_is_told_of_open_polls(self):
        skyler = self.client_for(self.skyler)
        poll = self.ask(skyler).json()['id']
        self.vote(skyler, poll, 'yes')
        said = self.agent_says(self.re, '<vote choice="wait"/>')
        polls.agent_voted(said.session_id, 're')
        page = Client().get('/api/moods/general/turns/').json()
        lines = open_polls('re', page)
        self.assertIn(f'#m-{poll} "Beta tonight?", asked by skyler -- yes: skyler; no: nobody yet; wait: re (your vote: wait)', lines)
        self.assertTrue(any('<vote choice="…"/>' in line for line in lines))
        self.close(skyler, poll)
        self.assertEqual(open_polls('re', Client().get('/api/moods/general/turns/').json()), [])
