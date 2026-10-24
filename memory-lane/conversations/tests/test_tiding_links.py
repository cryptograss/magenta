"""Links on Tidings: a message's link promoted by someone who thinks it's news; what's said about it
is the message's replies (Justin, 20 Oct)."""

import json
import uuid
from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.utils import timezone

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
        self.assertEqual(post(self.client_for(self.magent, tier='wiki'), {'title': 'x'}).status_code, 403)
        self.assertEqual(post(self.client_for(self.skyler), {'title': 'Better'}).json()['title'], 'Better')
        # Justin, 21 Oct: anyone signed in with an SSH key retitles any of them; taking one off stays the promoter's.
        self.assertEqual(post(self.client_for(self.magent), {'title': 'Best'}).json()['title'], 'Best')
        self.assertEqual(post(self.client_for(self.magent), {'withdraw': True}).status_code, 403)
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
    not just the Mood; upvotes, more like HN). A comment's words stay on Tidings; the Mood gets a short
    'new comment by …' line, unless the link is muted there."""

    def promoted(self):
        said = self.say(self.skyler, 'https://example.org/')
        return said, tiding_links.promote(said, self.justin)

    def comment(self, client, tiding, text, reply_to=None, kind=None):
        with CARD, PUBLIC:
            body = {'text': text, 'reply_to': str(reply_to) if reply_to else None}
            if kind:
                body['reply_kind'] = kind
            return client.post(f'/api/tidings/{tiding.id}/comments/',
                               json.dumps(body), content_type='application/json')

    def vote(self, client, tiding, message=None, comment=None):
        if comment:
            url = f'/api/tidings/{tiding.id}/comment/{comment}/vote/'
        else:
            url = f'/api/tidings/{tiding.id}/comments/{message.id}/vote/' if message else f'/api/tidings/{tiding.id}/vote/'
        return client.post(url)

    def test_a_comment_is_said_on_tidings_and_noted_in_the_mood(self):
        said, tiding = self.promoted()
        done = self.comment(self.client_for(self.justin), tiding, 'yes, that one')
        self.assertEqual(done.status_code, 201)
        notice = Message.objects.exclude(id=said.id).get()
        self.assertEqual(notice.content['type'], 'tiding-comment')  # a line in the Mood, not the comment
        self.assertEqual(notice.content['by'], 'justin')
        # Its link's title and its first words, which open it on Tidings (Justin, 23 Oct); the rest stays there.
        self.assertEqual((notice.content['title'], notice.content['comment']), (tiding.title, 'yes, that one'))
        self.assertEqual(notice.content['tiding'], str(tiding.id))
        long = self.comment(self.client_for(self.justin), tiding, 'word ' * 200)
        self.assertEqual(long.status_code, 201)
        self.assertLessEqual(len(Message.objects.filter(source_file='tiding-comment').order_by('-created_at')
                                 .first().content['comment']), 120)
        body = self.client.get(f'/api/tidings/{tiding.id}/').json()
        self.assertEqual((body['comments'][0]['kind'], body['comments'][0]['snippet']), ('comment', 'yes, that one'))

    def test_a_comment_can_answer_a_mood_reply_or_another_comment(self):
        said, tiding = self.promoted()
        mood_reply = self.say(self.justin, f'↩ #m-{said.id}\nGoing?')
        first = self.comment(self.client_for(self.justin), tiding, 'first', reply_to=mood_reply.id, kind='message')
        self.assertEqual(first.status_code, 201)
        tree = self.client.get(f'/api/tidings/{tiding.id}/').json()['comments']
        going = next(n for n in tree if n['snippet'] == 'Going?')
        first_node = going['replies'][0]
        self.assertEqual((first_node['kind'], first_node['snippet']), ('comment', 'first'))
        second = self.comment(self.client_for(self.skyler), tiding, 'second', reply_to=first_node['id'], kind='comment')
        self.assertEqual(second.status_code, 201)
        tree = self.client.get(f'/api/tidings/{tiding.id}/').json()['comments']
        going = next(n for n in tree if n['snippet'] == 'Going?')
        self.assertEqual([n['snippet'] for n in going['replies']], ['first'])
        self.assertEqual([n['snippet'] for n in going['replies'][0]['replies']], ['second'])

    def test_muting_a_link_keeps_the_notice_out_of_its_mood(self):
        said, tiding = self.promoted()
        self.assertEqual(self.comment(self.client_for(self.justin), tiding, 'one').status_code, 201)
        refused = self.client_for(self.skyler).post(f'/api/tidings/{tiding.id}/', json.dumps({'mute': True}),
                                                   content_type='application/json')
        self.assertEqual(refused.status_code, 403)  # only whoever promoted it, or an admin
        done = self.client_for(self.justin).post(f'/api/tidings/{tiding.id}/', json.dumps({'mute': True}),
                                                content_type='application/json')
        self.assertEqual(done.json()['muted'], True)
        self.assertEqual(self.comment(self.client_for(self.skyler), tiding, 'two').status_code, 201)
        self.assertEqual(Message.objects.exclude(id=said.id).count(), 1)  # only 'one' left a line

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
        mood_reply = self.say(self.justin, f'↩ #m-{said.id}\nGoing?')
        self.comment(self.client_for(self.skyler), tiding, 'agreed', reply_to=mood_reply.id, kind='message')
        justin = self.client_for(self.justin)
        body = justin.get(f'/api/tidings/{tiding.id}/').json()['comments']
        going = next(n for n in body if n['snippet'] == 'Going?')
        agreed = going['replies'][0]
        # a Mood reply and a comment said on Tidings each vote their own way, and only under this link:
        self.assertEqual(self.vote(justin, tiding, message=mood_reply).json(), {'score': 1, 'mine': True})
        self.assertEqual(self.vote(justin, tiding, comment=agreed['id']).json(), {'score': 1, 'mine': True})
        elsewhere = self.say(self.skyler, 'not about this')
        self.assertEqual(self.vote(justin, tiding, message=elsewhere).status_code, 404)
        tree = justin.get(f'/api/tidings/{tiding.id}/').json()['comments']
        going = next(n for n in tree if n['snippet'] == 'Going?')
        self.assertEqual((going['score'], going['replies'][0]['score']), (1, 1))

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
        # Nobody signed in here: no box that can't be typed in, but the way in -- and back to this discussion.
        self.assertNotIn('>add comment</button>', html)
        self.assertNotIn('<textarea id="say"', html)
        self.assertIn('sign in', html)
        with override_settings(PICKIPEDIA_OAUTH_CLIENT_ID='client-1', PICKIPEDIA_OAUTH_CLIENT_SECRET='secret-1'):
            html = self.client.get(f'/moods/tidings/{tiding.id}/?embed=1').content.decode()
        self.assertIn(f'href="/moods/auth/wiki/?next=/moods/tidings/{tiding.id}/" target="_top"', html)
        # Signed in: the box, and [[ suggesting PickiPedia's titles.
        html = self.client_for(self.justin).get(f'/moods/tidings/{tiding.id}/?embed=1').content.decode()
        self.assertIn('>add comment</button>', html)
        self.assertIn('id="wikiSuggest"', html)
        self.assertIn('list=prefixsearch', html)
        gone = self.client.get(f'/moods/tidings/{uuid.uuid4()}/?embed=1')
        self.assertEqual(gone.status_code, 404)

    def test_the_posts_own_words_are_at_the_top_but_not_a_comment(self):
        # Justin, 24 Oct: what the message says besides its link, in full, as the post's own text.
        said = self.say(self.skyler, 'Molly Tuttle is on the **Saturday** bill https://example.org/lineup\n\n- tent\n- capo')
        tiding = tiding_links.promote(said, self.justin)
        html = self.client.get(f'/moods/tidings/{tiding.id}/?embed=1').content.decode()
        post = html.split('<div class="post">')[1].split('<section class="comments">')[0]
        # Right under who posted it, in the link's own block -- not below it, after a rule.
        self.assertLess(html.index('class="meta muted"'), html.index('<div class="post">'))
        self.assertLess(html.index('<div class="post">'), html.index('<section class="comments">'))
        self.assertNotIn('class="comment post"', html)
        self.assertIn('<strong>Saturday</strong>', post)            # drawn as the Mood draws it
        self.assertIn('<li>capo</li>', post)                        # all of it, not 200 characters
        self.assertNotIn('example.org/lineup', post)                # the link is the title already
        # The box right under it, before what's been said (Justin, 24 Oct): a top-level comment by default.
        signed_in = self.client_for(self.justin).get(f'/moods/tidings/{tiding.id}/?embed=1').content.decode()
        self.assertLess(signed_in.index('<div class="post">'), signed_in.index('<form class="say"'))
        self.assertLess(signed_in.index('<form class="say"'), signed_in.index('<section class="comments">'))
        self.assertIn('id="replyTo" name="reply_to" value=""', signed_in)
        self.assertLess(html.index('<p class="say signin">'), html.index('<section class="comments">'))  # signed out: the way in, there
        self.assertNotIn('with the link', html)  # who posted it is said once, in the meta
        [link] = [l for l in tiding_links.listed() if l['id'] == str(tiding.id)]
        self.assertEqual(link['comments'], 0)                       # not counted
        only_link = self.say(self.skyler, 'https://example.org/other')
        tiding2 = tiding_links.promote(only_link, self.justin)
        self.assertNotIn('<div class="post">', self.client.get(f'/moods/tidings/{tiding2.id}/?embed=1').content.decode())

    def test_what_a_message_says_besides_its_link(self):
        url = 'https://example.org/lineup'
        cases = [
            (url, ''),
            (f'{url} !', ''),
            (f'This one: {url}', 'This one'),
            (f'Look at this {url}!', 'Look at this!'),
            (f'{url} — Saturday, both stages', 'Saturday, both stages'),
            (f'Summer reading: {url} and the rest', f'Summer reading: {url} and the rest'),  # part of a sentence: kept
            (f'[the lineup]({url}) is out', 'the lineup is out'),
            (f'<{url}>\nCamping too', 'Camping too'),
            (f'Not this one: {url}-2027', f'Not this one: {url}-2027'),  # another address that starts the same
            (f'↩ #m-{uuid.uuid4()}\nnice {url}', 'nice'),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(tiding_links.post_text(text, url), expected)

    def test_an_agents_reply_is_a_comment_too(self):
        # An agent's turn is kept as content blocks, not a string: the discussion reads its words.
        said, tiding = self.promoted()
        self.say(self.magent, [{'type': 'text', 'text': f'↩ #m-{said.id}\nI read it: worth a listen.'}])
        self.assertEqual([n['snippet'] for n in tiding_links.thread(said.id)], ['I read it: worth a listen.'])
        self.assertIn('worth a listen', self.client.get(f'/moods/tidings/{tiding.id}/?embed=1').content.decode())

    def test_a_secret_said_in_a_comment_is_kept_out_of_the_record(self):
        from conversations.models import TidingComment
        said, tiding = self.promoted()
        key = 'ghp_' + 'a1B2' * 9
        self.assertEqual(self.comment(self.client_for(self.justin), tiding, f'oops {key}').status_code, 201)
        self.assertNotIn(key, TidingComment.objects.get().text)  # and so not in the Mood's line either

    def test_no_such_link_page_is_a_404_not_a_crash(self):
        self.assertEqual(self.client.get('/moods/tidings/not-a-uuid/?embed=1').status_code, 404)
        self.assertEqual(self.client.get('/api/tidings/not-a-uuid/').status_code, 404)

    def test_a_promotion_takes_the_title_and_tags_its_promoter_gives(self):
        said = self.say(self.skyler, 'https://example.org/fest')
        done = self.promote(self.client_for(self.justin), said, title='My own title', tags=['Music', '  festival', 'music'])
        self.assertEqual(done.status_code, 201)
        [link] = tiding_links.listed()
        self.assertEqual((link['title'], link['tags']), ('My own title', ['music', 'festival']))

    def test_the_badges_count_what_is_new_since(self):
        from django.utils import timezone
        said, tiding = self.promoted()
        before = timezone.now()
        self.comment(self.client_for(self.justin), tiding, 'one')
        self.say(self.justin, 'https://example.org/two')
        tiding_links.promote(Message.objects.latest('created_at'), self.justin)
        body = self.client.get(f'/api/tidings/badges/?since={before.isoformat()}').json()
        self.assertEqual(body, {'tidings': 1, 'comments': 1})

    def test_a_tiding_said_as_its_link_says_its_title(self):
        from conversations.services.mood_view import render_html
        said, tiding = self.promoted()
        [link] = tiding_links.listed()
        html = render_html(f'see #t-{tiding.id}')
        self.assertIn('tidinglink', html)
        self.assertIn(link['title'], html)
        self.assertNotIn('tidinglink', render_html('#t-ffffffff'))  # names nothing: as written

    def test_a_video_link_says_how_it_plays(self):
        self.assertEqual(tiding_links.embed_of('https://www.youtube.com/watch?v=dQw4w9WgXcQ'),
                         {'kind': 'iframe', 'src': 'https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ'})
        self.assertEqual(tiding_links.embed_of('https://vimeo.com/76979871'),
                         {'kind': 'iframe', 'src': 'https://player.vimeo.com/video/76979871'})
        self.assertEqual(tiding_links.embed_of('https://example.org/clip.mp4'),
                         {'kind': 'video', 'src': 'https://example.org/clip.mp4'})
        self.assertIsNone(tiding_links.embed_of('https://example.org/page'))
        said, tiding = self.promoted()
        body = self.client.get(f'/api/tidings/{tiding.id}/').json()
        self.assertIsNone(body['embed'])  # example.org isn't a video

    def test_the_listing_counts_comments_without_reading_each(self):
        for n in range(3):
            said = self.say(self.skyler, f'https://example.org/{n}')
            tiding_links.promote(said, self.justin)
            self.say(self.justin, f'↩ #m-{said.id}\nnice {n}')
        tiding_links.listed()  # the links index, built once
        # Its settings (pins, ranking), the links, their votes, their comments, who's an agent: nothing per link.
        with self.assertNumQueries(5):
            self.assertEqual([link['comments'] for link in tiding_links.listed()], [1, 1, 1])

    def test_the_pencil_shows_to_whoever_may_retitle(self):
        said, tiding = self.promoted()  # promoted by justin
        listing = lambda client: client.get('/moods/tidings/?embed=1').content.decode()  # noqa: E731
        self.assertIn('class="retitle"', listing(self.client_for(self.skyler)))  # an SSH key
        self.assertNotIn('class="retitle"', listing(self.client_for(self.skyler, tier='wiki')))
        self.assertNotIn('class="retitle"', listing(Client()))
        self.assertNotIn('class="withdraw"', listing(self.client_for(self.skyler)))
        self.assertIn('class="withdraw"', listing(self.client_for(self.justin)))
        page = self.client_for(self.skyler).get(f'/moods/tidings/{tiding.id}/?embed=1').content.decode()
        self.assertIn('class="retitle"', page)

    def test_a_comment_is_drawn_as_the_mood_draws_it(self):
        said, tiding = self.promoted()
        self.comment(self.client_for(self.justin), tiding, 'Two things.\n\n- `pip` first\n- then [[Old Crow Medicine Show]]')
        html = self.client.get(f'/moods/tidings/{tiding.id}/?embed=1').content.decode()
        self.assertIn('<code>pip</code>', html)
        self.assertIn('<li>', html)
        self.assertIn('/wiki/Old_Crow_Medicine_Show', html)
        self.assertIn('<p>Two things.</p>', html)

    def test_a_wikilink_can_be_promoted_titled_as_its_page(self):
        said = self.say(self.skyler, 'Read [[Bill Monroe]] first, then https://example.org/x')
        self.assertEqual(tiding_links.links_in(said.content),
                         ['https://pickipedia.xyz/wiki/Bill_Monroe', 'https://example.org/x'])
        tiding = tiding_links.promote(said, self.justin)
        self.assertEqual((tiding.url, tiding.title), ('https://pickipedia.xyz/wiki/Bill_Monroe', 'Bill Monroe'))
        self.assertEqual(tiding_links.links_in('a picture [[File:Fest.jpg]], not a link'), [])


class TidingRoundTwoReviewTests(TidingLinksTest):
    """From magent's review of the second round (21 Oct)."""

    def test_tags_typed_in_the_box_are_split_at_commas(self):
        said = self.say(self.skyler, 'https://example.org/')
        done = self.promote(self.client_for(self.justin), said, tags='Music, #security,, festival ')
        self.assertEqual(Tiding.objects.get(id=done.json()['id']).tags, ['music', 'security', 'festival'])

    def test_a_title_given_for_several_links_is_not_put_on_all_of_them(self):
        said = self.say(self.skyler, 'Two: https://a.test/one https://b.test/two')
        with CARD, PUBLIC:
            done = tiding_links.promote_many(said, self.justin, ['https://a.test/one', 'https://b.test/two'], title='Mine')
        self.assertEqual([t.title for t, _ in done], ['The Bluegrass Situation: a festival list'] * 2)
        said = self.say(self.skyler, 'One: https://c.test/three')
        with CARD, PUBLIC:
            [(only, _)] = tiding_links.promote_many(said, self.justin, ['https://c.test/three'], title='Mine')
        self.assertEqual(only.title, 'Mine')

    def test_a_tiding_link_by_its_whole_id_or_its_first_eight(self):
        from conversations.services.mood_view import render_html
        said = self.say(self.skyler, 'https://example.org/')
        with CARD, PUBLIC:
            tiding = tiding_links.promote(said, self.justin, title='Fest list')
        for ref in (str(tiding.id), str(tiding.id)[:8]):
            self.assertIn(f'/moods/tidings/{tiding.id}/', render_html(f'see #t-{ref}'))
        self.assertNotIn('tidinglink', render_html('see #t-00000000'))
        self.assertNotIn('tidinglink', render_html('see #t-0000-000000'))  # neither: left as it was written

    def test_only_a_video_id_is_embedded(self):
        embed = tiding_links.embed_of
        self.assertEqual(embed('https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=3')['src'],
                         'https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ')
        self.assertEqual(embed('https://youtube.com/shorts/dQw4w9WgXcQ')['src'],
                         'https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ')
        self.assertIsNone(embed('https://www.youtube.com/watch?v=x"><script>'))
        self.assertIsNone(embed('https://www.youtube.com/watch?v=../../evil'))

    def test_a_video_is_titled_as_its_site_says(self):
        from conversations.services import unfurl

        class Answer:
            def __init__(self, code, body=None):
                self.status_code, self.body, self.headers = code, body, {}

            def json(self):
                return self.body

            def close(self):
                pass

        class Http:
            asked = []

            def get(self, url, params=None, **kw):
                self.asked.append(url)
                if 'oembed' in url:
                    return Answer(200, {'title': 'Rick Astley - Never Gonna Give You Up', 'provider_name': 'YouTube'})
                return Answer(200)  # a page with nothing in it to read

        with PUBLIC:
            found = unfurl.card('https://www.youtube.com/watch?v=dQw4w9WgXcQ', Http())
        self.assertEqual((found['title'], found['site']), ('Rick Astley - Never Gonna Give You Up', 'YouTube'))
        self.assertEqual(Http.asked[-1], 'https://www.youtube.com/oembed')
        with PUBLIC:
            self.assertIsNone(unfurl.card('https://example.org/nothing', Http()))  # not a video site: no oEmbed asked

    def test_the_badges_count_others_news_not_your_own(self):
        from django.utils import timezone
        from datetime import timedelta
        since = (timezone.now() - timedelta(minutes=1)).isoformat()
        with CARD, PUBLIC:
            tiding_links.promote(self.say(self.skyler, 'https://example.org/1'), self.justin)
            tiding_links.promote(self.say(self.justin, 'https://example.org/2'), self.skyler)
        got = lambda client: client.get('/api/tidings/badges/', {'since': since}).json()  # noqa: E731
        self.assertEqual(got(self.client_for(self.justin)), {'tidings': 1, 'comments': 0})
        self.assertEqual(got(Client()), {'tidings': 2, 'comments': 0})


@override_settings(MOOD_ADMINS=('justin',))
class CommentEditingTests(TestCase):
    """Editing and deleting what's said about a link (Justin, 21 Oct: "How about comment editing?"):
    as in the Mood -- yours, within a day to edit and a week to delete, or anyone's deleted by an admin;
    what goes is sealed to the recovery key first."""

    def setUp(self):
        from conversations.services import sealing
        cache.clear()
        links._memo.update(at=0.0, index=None)
        self.private, public = sealing.new_pair()
        self.keyed = override_settings(MOOD_RECOVERY_PUBLIC_KEY=public)
        self.keyed.enable()
        self.addCleanup(self.keyed.disable)
        self.justin = ThinkingEntity.objects.create(name='justin', is_biological_human=True)
        self.skyler = ThinkingEntity.objects.create(name='skyler', is_biological_human=True)
        self.mood = Mood.objects.create(slug='jams', title='jams')
        said = Message.objects.create(id=uuid.uuid4(), sender=self.skyler, mood=self.mood, content='https://example.org/',
                                      timestamp=1, source_file='mood-web')
        self.tiding = tiding_links.promote(said, self.justin, title='A link')
        self.said = said

    def client_for(self, who, tier='key'):
        _, token = mood_auth.enrol_device(who, 'laptop', tier=tier)
        client = Client()
        client.cookies[mood_auth.COOKIE] = token
        return client

    def change(self, client, kind, node_id, **body):
        return client.post(f'/api/tidings/{self.tiding.id}/said/{kind}/{node_id}/', json.dumps(body),
                           content_type='application/json')

    def test_your_comment_on_tidings_can_be_edited_and_says_so(self):
        from conversations.models import SealedCopy, TidingComment
        skyler = self.client_for(self.skyler)
        cid = skyler.post(f'/api/tidings/{self.tiding.id}/comments/', json.dumps({'text': 'frist'}),
                          content_type='application/json').json()['comment']['id']
        self.assertEqual(self.change(self.client_for(self.justin), 'comment', cid, text='mine now').status_code, 403)
        done = self.change(skyler, 'comment', cid, text='**first**, I meant')
        self.assertEqual(done.status_code, 200)
        row = TidingComment.objects.get(id=cid)
        self.assertEqual((row.text, row.changed), ('**first**, I meant', 'edited'))
        self.assertEqual(SealedCopy.objects.get(message_id=row.id).kind, 'edited')  # the old words, kept sealed
        [node] = tiding_links.tree_of(self.tiding, self.said.id)
        self.assertEqual((node['changed'], '<strong>first</strong>' in node['html']), ('edited', True))
        page = skyler.get(f'/moods/tidings/{self.tiding.id}/?embed=1').content.decode()
        self.assertIn('class="editsaid"', page)
        self.assertIn('edited', page)
        self.assertNotIn('class="editsaid"', self.client_for(self.justin).get(f'/moods/tidings/{self.tiding.id}/?embed=1').content.decode())

    def test_a_day_on_it_stays_as_it_was_said(self):
        from conversations.models import TidingComment
        skyler = self.client_for(self.skyler)
        cid = skyler.post(f'/api/tidings/{self.tiding.id}/comments/', json.dumps({'text': 'old'}),
                          content_type='application/json').json()['comment']['id']
        TidingComment.objects.filter(id=cid).update(at=timezone.now() - timedelta(days=2))
        self.assertEqual(self.change(skyler, 'comment', cid, text='new').status_code, 403)

    def test_deleted_its_replies_stay_under_it_and_an_admin_can(self):
        from conversations.models import TidingComment
        skyler, justin = self.client_for(self.skyler), self.client_for(self.justin)
        cid = skyler.post(f'/api/tidings/{self.tiding.id}/comments/', json.dumps({'text': 'hot take'}),
                          content_type='application/json').json()['comment']['id']
        justin.post(f'/api/tidings/{self.tiding.id}/comments/', json.dumps({'text': 'nah', 'reply_to': cid, 'reply_kind': 'comment'}),
                    content_type='application/json')
        self.assertEqual(self.change(justin, 'comment', cid, delete=True).status_code, 200)  # an admin, with a key
        [node] = tiding_links.tree_of(self.tiding, self.said.id)
        self.assertEqual((node['changed'], node['snippet'], len(node['replies'])), ('deleted', '[deleted]', 1))
        self.assertEqual(TidingComment.objects.get(id=cid).text, '[deleted]')

    def test_a_mood_reply_is_edited_as_in_the_mood_and_stays_a_reply(self):
        reply = Message.objects.create(id=uuid.uuid4(), sender=self.skyler, mood=self.mood, timestamp=2, source_file='mood-web',
                                       content=f'↩ #m-{self.said.id}\nnice one')
        links._memo.update(at=0.0, index=None)
        cache.clear()
        skyler = self.client_for(self.skyler)
        self.assertEqual(self.change(skyler, 'mood', reply.id, text='nicer one').status_code, 200)
        reply.refresh_from_db()
        self.assertEqual(reply.content, f'↩ #m-{self.said.id}\nnicer one')  # still a reply: still a comment here
        [node] = tiding_links.tree_of(self.tiding, self.said.id)
        self.assertEqual((node['snippet'], node['changed']), ('nicer one', 'edited'))
        elsewhere = Message.objects.create(id=uuid.uuid4(), sender=self.skyler, mood=self.mood, timestamp=3,
                                           source_file='mood-web', content='not under this link')
        self.assertEqual(self.change(skyler, 'mood', elsewhere.id, text='x').status_code, 404)

    def test_the_list_draws_with_comments_under_a_link(self):
        skyler = self.client_for(self.skyler)
        skyler.post(f'/api/tidings/{self.tiding.id}/comments/', json.dumps({'text': 'one'}), content_type='application/json')
        page = skyler.get('/moods/tidings/?embed=1')
        self.assertEqual(page.status_code, 200)
        self.assertIn('1 comment', page.content.decode())
