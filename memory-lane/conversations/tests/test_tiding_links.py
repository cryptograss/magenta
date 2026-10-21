"""Links on Tidings: a message's link promoted by someone who thinks it's news; what's said about it
is the message's replies (Justin, 20 Oct)."""

import json
import uuid
from unittest import mock

from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from conversations.models import Message, Mood, ThinkingEntity, Tiding
from conversations.services import links, mood_auth, tiding_links

CARD = mock.patch('conversations.services.unfurl.card', return_value={'title': 'The Bluegrass Situation: a festival list'})
PUBLIC = mock.patch('conversations.services.unfurl.public', return_value=True)


@override_settings(MOOD_ADMINS=('justin',))
class TidingLinksTest(TestCase):

    def setUp(self):
        cache.clear()
        links._memo.update(at=0.0, index=None)
        self.justin = ThinkingEntity.objects.create(name='justin', is_biological_human=True)
        self.skyler = ThinkingEntity.objects.create(name='skyler', is_biological_human=True)
        self.magent = ThinkingEntity.objects.create(name='magent', is_biological_human=False)
        self.mood = Mood.objects.create(slug='jams', title='jams')
        self.n = 0

    def say(self, who, text, mood=None, **fields):
        self.n += 1
        return Message.objects.create(id=uuid.uuid4(), sender=who, mood=mood or self.mood, content=text,
                                      timestamp=self.n, source_file='mood-web', **fields)

    def client_for(self, who, tier='key'):
        _, token = mood_auth.enrol_device(who, 'laptop', tier=tier)
        client = Client()
        client.cookies[mood_auth.COOKIE] = token
        return client

    def promote(self, client, message, **body):
        with CARD, PUBLIC:
            return client.post(f'/api/moods/jams/messages/{message.id}/promote/', json.dumps(body),
                               content_type='application/json')

    def test_a_messages_first_link_goes_on_tidings_titled_as_the_page_titles_itself(self):
        said = self.say(self.skyler, 'Look: https://thebluegrasssituation.com/fests/ and https://example.org/b')
        done = self.promote(self.client_for(self.justin), said)
        self.assertEqual(done.status_code, 201)
        tiding = Tiding.objects.get()
        self.assertEqual((tiding.url, tiding.title, tiding.by_id), ('https://thebluegrasssituation.com/fests/',
                                                                     'The Bluegrass Situation: a festival list', 'justin'))
        self.assertEqual(self.promote(self.client_for(self.justin), said).status_code, 200)  # each link once
        self.assertEqual(Tiding.objects.count(), 1)
        second = self.promote(self.client_for(self.skyler), said, url='https://example.org/b', title='  Mine  ')
        self.assertEqual((second.status_code, second.json()['title'], Tiding.objects.count()), (201, 'Mine', 2))
        self.assertEqual(self.promote(self.client_for(self.skyler), said, url='https://elsewhere.test/').status_code, 400)

    def test_whoever_promotes_picks_which_of_its_links(self):
        # Justin, 21 Oct: several links in a message, more than one of them news; the promoter chooses.
        said = self.say(self.skyler, 'Three: https://a.test/one https://b.test/two https://c.test/three')
        done = self.promote(self.client_for(self.justin), said, urls=['https://c.test/three', 'https://a.test/one'])
        self.assertEqual(done.status_code, 201)
        self.assertEqual([t['url'] for t in done.json()['tidings']], ['https://c.test/three', 'https://a.test/one'])
        again = self.promote(self.client_for(self.skyler), said, urls=['https://a.test/one', 'https://b.test/two'])
        self.assertEqual([t['new'] for t in again.json()['tidings']], [False, True])
        self.assertEqual(Tiding.objects.filter(message=said).count(), 3)
        turn = next(t for t in self.client.get('/api/moods/jams/turns/').json()['turns'] if t['id'] == str(said.id))
        self.assertEqual([t['url'] for t in turn['tidings']], ['https://c.test/three', 'https://a.test/one', 'https://b.test/two'])
        self.assertEqual(len(tiding_links.listed()), 3)
        self.assertEqual(self.promote(self.client_for(self.justin), said, urls=['https://not.in/it']).status_code, 400)

    def test_only_a_message_with_a_link_and_only_signed_in(self):
        plain = self.say(self.justin, 'no link here, just `https://in.code/` and a picture ![image](/moods/media/'
                         + 'a' * 64 + '.png) and https://magenta.cryptograss.live/messages/x')
        self.assertEqual(self.promote(self.client_for(self.justin), plain).status_code, 400)
        linked = self.say(self.justin, 'https://example.org/')
        self.assertEqual(self.promote(Client(), linked).status_code, 401)

    def test_whoever_promoted_it_or_an_admin_retitles_or_withdraws_it(self):
        said = self.say(self.justin, 'https://example.org/')
        tid = self.promote(self.client_for(self.skyler), said).json()['id']
        url = f'/api/tidings/{tid}/'
        post = lambda client, body: client.post(url, json.dumps(body), content_type='application/json')  # noqa: E731
        self.assertEqual(post(self.client_for(self.magent), {'title': 'x'}).status_code, 403)
        self.assertEqual(post(self.client_for(self.skyler), {'title': 'Better'}).json()['title'], 'Better')
        self.assertEqual(post(self.client_for(self.justin, tier='wiki'), {'withdraw': True}).status_code, 403)  # admin needs a key
        self.assertTrue(post(self.client_for(self.justin), {'withdraw': True}).json()['withdrawn'])
        self.assertFalse(Tiding.objects.exists())

    def test_what_is_said_about_it_is_its_reply_tree(self):
        said = self.say(self.skyler, 'https://example.org/fest')
        first = self.say(self.justin, f'↩ #m-{said.id}\nGoing?')
        self.say(self.skyler, f'↩ #m-{first.id}\nYes, Friday.')
        self.say(self.justin, f'See #m-{said.id}, not a reply')  # a link to it isn't a comment
        self.say(self.justin, f'↩ #m-{said.id}\nAnd camping?')
        self.promote(self.client_for(self.justin), said)
        [link] = tiding_links.listed()
        self.assertEqual((link['host'], link['by'], link['mood'], link['comments']), ('example.org', 'justin', 'jams', 3))
        tree = tiding_links.thread(said.id)
        self.assertEqual([len(node['replies']) for node in tree], [1, 0])

    def test_the_page_lists_it_and_the_message_carries_its_star(self):
        said = self.say(self.skyler, 'https://www.example.org/fest')
        tid = self.promote(self.client_for(self.justin), said).json()['id']
        page = self.client.get('/moods/tidings/?embed=1').content.decode()
        self.assertIn('The Bluegrass Situation: a festival list</a><span class="host">(example.org)</span>', page)
        self.assertIn('0 comments', page)
        turn = next(t for t in self.client.get('/api/moods/jams/turns/').json()['turns'] if t['id'] == str(said.id))
        self.assertEqual((turn['links'], [t['id'] for t in turn['tidings']]), (['https://www.example.org/fest'], [tid]))

    def test_a_deleted_message_leaves_tidings_and_comes_back_if_put_back(self):
        said = self.say(self.skyler, 'https://example.org/')
        self.promote(self.client_for(self.justin), said)
        Message.objects.filter(id=said.id).update(content='[deleted]')
        self.assertEqual(tiding_links.listed(), [])
        Message.objects.filter(id=said.id).update(content='https://example.org/')  # put back (services/retract.put_back)
        self.assertEqual(len(tiding_links.listed()), 1)


@override_settings(MOOD_ADMINS=('justin',))
class TidingTalkTests(TidingLinksTest):
    """What's said about a link, and voted for, on its Tidings page (Justin, 21 Oct: comments there too,
    not just the Mood; upvotes, more like HN). One record: a comment is a reply in the Mood."""

    def promoted(self):
        said = self.say(self.skyler, 'https://example.org/')
        return said, tiding_links.promote(said, self.justin)

    def comment(self, client, tiding, text, reply_to=None):
        with CARD, PUBLIC:
            return client.post(f'/api/tidings/{tiding.id}/comments/',
                               json.dumps({'text': text, 'reply_to': reply_to}),
                               content_type='application/json')

    def vote(self, client, tiding, message=None):
        url = f'/api/tidings/{tiding.id}/comments/{message.id}/vote/' if message else f'/api/tidings/{tiding.id}/vote/'
        return client.post(url)

    def test_a_comment_is_a_reply_in_the_mood_and_a_comment_on_the_page(self):
        said, tiding = self.promoted()
        done = self.comment(self.client_for(self.justin), tiding, 'yes, that one')
        self.assertEqual(done.status_code, 201)
        reply = Message.objects.exclude(id=said.id).get()
        self.assertEqual((reply.content, reply.sender_id, reply.mood_id), (f'↩ #m-{said.id}\nyes, that one', 'justin', self.mood.id))
        tree = tiding_links.thread(said.id)
        self.assertEqual([n['snippet'] for n in tree], ['yes, that one'])
        body = self.client.get(f'/api/tidings/{tiding.id}/').json()
        self.assertEqual(body['comments'][0]['snippet'], 'yes, that one')

    def test_a_comment_can_answer_another_comment(self):
        said, tiding = self.promoted()
        self.comment(self.client_for(self.justin), tiding, 'first')
        node = tiding_links.thread(said.id)[0]
        second = self.comment(self.client_for(self.skyler), tiding, 'second', reply_to=node['id'])
        self.assertEqual(second.status_code, 201)
        tree = tiding_links.thread(said.id)
        self.assertEqual((tree[0]['snippet'], [n['snippet'] for n in tree[0]['replies']]), ('first', ['second']))
        self.assertEqual(Message.objects.exclude(id=said.id).count(), 2)

    def test_a_comment_on_no_such_comment_or_a_deleted_link_is_refused(self):
        said, tiding = self.promoted()
        ghost = self.comment(self.client_for(self.justin), tiding, 'x', reply_to=str(uuid.uuid4()))
        self.assertEqual(ghost.status_code, 404)
        Message.objects.filter(id=said.id).update(content='[deleted]')
        self.assertEqual(self.comment(self.client_for(self.justin), tiding, 'x').status_code, 404)

    def test_saying_it_needs_a_sign_in_and_nothing_said_is_refused(self):
        said, tiding = self.promoted()
        self.assertEqual(self.comment(Client(), tiding, 'x').status_code, 401)
        self.assertEqual(self.comment(self.client_for(self.justin), tiding, '   ').status_code, 400)

    def test_an_upvote_goes_on_and_off_and_only_votes_once_each(self):
        said, tiding = self.promoted()
        justin, skyler = self.client_for(self.justin), self.client_for(self.skyler)
        self.assertEqual(self.vote(justin, tiding).json(), {'score': 1, 'mine': True})
        self.assertEqual(self.vote(justin, tiding).json(), {'score': 0, 'mine': False})  # again takes it back
        self.assertEqual(self.vote(justin, tiding).json()['score'], 1)
        self.assertEqual(self.vote(skyler, tiding).json()['score'], 2)
        body = skyler.get(f'/api/tidings/{tiding.id}/').json()
        self.assertEqual((body['score'], body['mine']), (2, True))
        body = justin.get(f'/api/tidings/{tiding.id}/').json()
        self.assertEqual((body['score'], body['mine']), (2, True))
        [link] = tiding_links.listed(viewer='justin')
        self.assertEqual((link['score'], link['mine']), (2, True))
        self.assertEqual(self.vote(Client(), tiding).status_code, 401)

    def test_a_comment_can_be_upvoted_and_only_under_its_link(self):
        said, tiding = self.promoted()
        self.comment(self.client_for(self.skyler), tiding, 'agreed')
        node = tiding_links.thread(said.id)[0]
        justin = self.client_for(self.justin)
        self.assertEqual(self.vote(justin, tiding, message=Message.objects.get(id=node['id'])).json(),
                         {'score': 1, 'mine': True})
        tree = tiding_links.thread(said.id, viewer='justin')
        self.assertEqual((tree[0]['score'], tree[0]['mine']), (1, True))
        elsewhere = self.say(self.skyler, 'not about this')
        self.assertEqual(self.vote(justin, tiding, message=elsewhere).status_code, 404)

    def test_the_page_says_who_said_and_who_promoted_and_ranks(self):
        said, tiding = self.promoted()
        self.vote(self.client_for(self.skyler), tiding)
        [link] = tiding_links.listed()
        self.assertEqual((link['said_by'], link['by'], link['score']), ('skyler', 'justin', 1))
        page = self.client.get('/moods/tidings/?embed=1').content.decode()
        self.assertIn('posted by skyler · promoted by justin', page)
        self.assertIn('class="rank"', page)  # numbered, HN-style
        said2 = self.say(self.justin, 'https://example.org/two')
        tiding_links.promote(said2, self.justin)
        page = self.client.get('/moods/tidings/?embed=1').content.decode()
        self.assertIn('posted and promoted by justin', page)

    def test_the_discussion_page_renders_inside_the_one_page(self):
        said, tiding = self.promoted()
        self.comment(self.client_for(self.justin), tiding, 'looking forward')
        page = self.client.get(f'/moods/tidings/{tiding.id}/?embed=1')
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page['X-Frame-Options'], 'SAMEORIGIN')  # else the frame stays blank
        html = page.content.decode()
        self.assertIn('looking forward', html)
        self.assertIn('say it', html)
        self.assertIn('sign in', html)  # nobody signed in here: the box is closed but says so
        gone = self.client.get(f'/moods/tidings/{uuid.uuid4()}/?embed=1')
        self.assertEqual(gone.status_code, 404)

    def test_an_agents_reply_is_a_comment_too(self):
        # An agent's turn is kept as content blocks, not a string: the discussion reads its words.
        said, tiding = self.promoted()
        self.say(self.magent, [{'type': 'text', 'text': f'↩ #m-{said.id}\nI read it: worth a listen.'}])
        self.assertEqual([n['snippet'] for n in tiding_links.thread(said.id)], ['I read it: worth a listen.'])
        self.assertIn('worth a listen', self.client.get(f'/moods/tidings/{tiding.id}/?embed=1').content.decode())

    def test_a_secret_said_in_a_comment_is_kept_out_of_the_record(self):
        said, tiding = self.promoted()
        key = 'ghp_' + 'a1B2' * 9
        self.assertEqual(self.comment(self.client_for(self.justin), tiding, f'oops {key}').status_code, 201)
        self.assertNotIn(key, Message.objects.exclude(id=said.id).get().content)

    def test_no_such_link_page_is_a_404_not_a_crash(self):
        self.assertEqual(self.client.get('/moods/tidings/not-a-uuid/?embed=1').status_code, 404)
        self.assertEqual(self.client.get('/api/tidings/not-a-uuid/').status_code, 404)

    def test_the_listing_counts_comments_without_reading_each(self):
        for n in range(3):
            said = self.say(self.skyler, f'https://example.org/{n}')
            tiding_links.promote(said, self.justin)
            self.say(self.justin, f'↩ #m-{said.id}\nnice {n}')
        tiding_links.listed()  # the links index, built once
        with self.assertNumQueries(2):  # the links and their scores: nothing per link
            self.assertEqual([link['comments'] for link in tiding_links.listed()], [1, 1, 1])
