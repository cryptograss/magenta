"""magenta's front page (Justin, 22 Oct): the mark and who it's for, the top of Tidings, and who has
spoken lately and where -- names, never what was said."""

import uuid
from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from conversations.models import ConversationParticipant, Message, Mood, ThinkingEntity, Thought, TidingVote
from conversations.services import front, links, tiding_links


class FrontBase(TestCase):

    def setUp(self):
        cache.clear()
        cache.set('front:slogans', [front.SLOGAN], 600)  # the wiki's page, as if read: no test asks the real one
        links._memo.update(at=0.0, index=None)
        self.justin = ThinkingEntity.objects.create(name='justin', is_biological_human=True)
        self.skyler = ThinkingEntity.objects.create(name='skyler', is_biological_human=True)
        self.magent = ThinkingEntity.objects.create(name='magent', is_biological_human=False)
        self.poller = ConversationParticipant.objects.create(name='mood-poller')
        self.jams = Mood.objects.create(slug='jams', title='jams')
        self.dev = Mood.objects.create(slug='magenta-interface', title='magenta interface')
        self.n = 0

    def say(self, who, mood, text, model=Message, **fields):
        self.n += 1
        row = model.objects.create(id=uuid.uuid4(), sender=who, mood=mood, content=text, timestamp=self.n,
                                   source_file='mood-web', **fields)
        Message.objects.filter(id=row.id).update(created_at=timezone.now() + timedelta(seconds=self.n))
        return row


class FrontPageTest(FrontBase):

    def test_it_is_what_the_bare_address_shows_to_anyone(self):
        page = self.client.get('/')
        self.assertEqual(page.status_code, 200)
        html = page.content.decode()
        self.assertIn(front.SLOGAN, html)
        self.assertIn('/moods/mark-192.png', html)
        self.assertIn('href="/moods/"', html)

    def test_the_top_three_links_most_upvoted_first(self):
        made = []
        for i in range(4):
            said = self.say(self.skyler, self.jams, f'https://example.org/{i}')
            made.append(tiding_links.promote(said, self.justin, title=f'Link {i}'))
        TidingVote.objects.create(tiding=made[1], by=self.skyler)
        TidingVote.objects.create(tiding=made[1], by=self.justin)
        TidingVote.objects.create(tiding=made[0], by=self.justin)
        self.assertEqual([t['title'] for t in front.top_tidings()], ['Link 1', 'Link 0', 'Link 3'])
        html = self.client.get('/').content.decode()
        self.assertIn(f'/moods/tidings/{made[1].id}/', html)
        self.assertNotIn('Link 2', html)

    def test_who_spoke_lately_and_where_not_what(self):
        self.say(self.justin, self.jams, 'a secret-ish thing said in jams')
        self.say(self.poller, self.dev, '<mood-wake mood="magenta-interface">...</mood-wake>')  # the poller's wake
        self.say(self.magent, self.dev, [{'type': 'thinking', 'thinking': 'hmm'}], model=Thought)  # thinking
        self.say(self.magent, self.dev, [{'type': 'text', 'text': 'Built it.'}])
        self.say(self.magent, self.dev, [{'type': 'text', 'text': 'And tested it.'}])  # the same turn: one line
        self.say(self.magent, self.dev, [{'type': 'text', 'text': '<silent>nothing to add</silent>'}])  # a silence
        self.say(self.skyler, self.jams, 'see you there')
        self.assertEqual([(s['who'], s['mood']) for s in front.lately()],
                         [('skyler', 'jams'), ('magent', 'magenta-interface'), ('justin', 'jams')])
        html = self.client.get('/').content.decode()
        self.assertIn('#magenta interface', html)
        self.assertIn('--agent: #ff67fd', html)  # magent in its own colour
        self.assertNotIn('secret-ish', html)
        self.assertNotIn('Built it', html)

    def test_an_archived_mood_is_not_shown(self):
        from conversations.services import settings as knobs
        self.say(self.justin, self.jams, 'old news')
        with mock.patch.object(knobs, 'archived_slugs', return_value={'jams'}):
            self.assertEqual(front.lately(), [])

    def test_a_links_address_opens_the_one_page_at_its_discussion(self):
        said = self.say(self.skyler, self.jams, 'https://example.org/x')
        tiding = tiding_links.promote(said, self.justin, title='X')
        page = self.client.get(f'/moods/tidings/{tiding.id}/').content.decode()
        self.assertIn('tidingAt', page)  # the one page, which opens the discussion in its Tidings frame


class RankingAndPinsTest(FrontBase):
    """Justin, 22 Oct: rank Tidings by votes, talk and age -- HN's gravity on a half-day clock, "pretty
    dynamic" -- pin to the top for anyone signed in with an SSH key, and the front page's clicks."""

    WEIGHTS = {'tidings_age_hours': 12.0, 'tidings_gravity': 1.5, 'tidings_comment_weight': 0.5}

    def test_the_rank_is_hns_on_a_half_day_clock(self):
        now = timezone.now()
        rank = lambda votes, comments, hours: tiding_links.rank(votes, comments, now - timedelta(hours=hours), now, self.WEIGHTS)  # noqa: E731
        self.assertAlmostEqual(rank(0, 0, 0), 1.0)
        self.assertAlmostEqual(rank(3, 0, 12), 4 / 2 ** 1.5)       # half a day, three votes
        self.assertGreater(rank(3, 0, 12), rank(0, 0, 0))          # three votes outlast half a day
        self.assertLess(rank(3, 0, 24), rank(0, 0, 0))             # but not a day
        self.assertAlmostEqual(rank(0, 2, 0), 2.0)                 # two comments, a vote's worth

    def test_a_fresh_link_passes_an_old_favourite(self):
        from conversations.models import Tiding
        old = tiding_links.promote(self.say(self.skyler, self.jams, 'https://example.org/old'), self.justin, title='Old')
        Tiding.objects.filter(id=old.id).update(at=timezone.now() - timedelta(days=2))
        for who in (self.skyler, self.justin, self.magent):
            TidingVote.objects.create(tiding=old, by=who)
        tiding_links.promote(self.say(self.skyler, self.jams, 'https://example.org/new'), self.justin, title='New')
        self.assertEqual([t['title'] for t in tiding_links.listed()], ['New', 'Old'])
        self.assertEqual([t['title'] for t in tiding_links.listed(order='new')], ['New', 'Old'])

    def test_pinned_first_by_an_ssh_key_only(self):
        import json
        from django.test import Client
        from conversations.services import mood_auth
        first = tiding_links.promote(self.say(self.skyler, self.jams, 'https://example.org/1'), self.justin, title='One')
        tiding_links.promote(self.say(self.skyler, self.jams, 'https://example.org/2'), self.justin, title='Two')

        def client(who, tier):
            _, token = mood_auth.enrol_device(who, 'laptop', tier=tier)
            c = Client()
            c.cookies[mood_auth.COOKIE] = token
            return c
        front.state()  # the front page, drawn and kept before the pin
        pin = lambda c, on: c.post(f'/api/tidings/{first.id}/', json.dumps({'pin': on}), content_type='application/json')  # noqa: E731
        self.assertEqual(pin(client(self.skyler, 'wiki'), True).status_code, 403)
        self.assertEqual(pin(client(self.skyler, 'key'), True).json()['pinned'], [str(first.id)])
        self.assertEqual([t['title'] for t in tiding_links.listed()], ['One', 'Two'])
        self.assertEqual([t['title'] for t in front.state()['tidings']][:1], ['One'])  # at once, not after the cache
        page = client(self.skyler, 'key').get('/moods/tidings/?embed=1').content.decode()
        self.assertIn('class="pinit"', page)
        self.assertIn('📌', page)
        self.assertNotIn('class="pinit"', client(self.skyler, 'wiki').get('/moods/tidings/?embed=1').content.decode())
        pin(client(self.justin, 'key'), False)
        self.assertEqual([t['title'] for t in tiding_links.listed()], ['Two', 'One'])

    def test_the_front_pages_clicks(self):
        tiding = tiding_links.promote(self.say(self.skyler, self.jams, 'https://example.org/x'), self.justin, title='X')
        self.say(self.justin, self.jams, 'first')
        self.say(self.skyler, self.dev, 'elsewhere')
        self.say(self.justin, self.jams, 'again')  # justin in jams again: still one line, at its latest
        cache.delete('front:state')
        self.assertEqual([(s['who'], s['mood']) for s in front.lately()],
                         [('justin', 'jams'), ('skyler', 'magenta-interface'), ('skyler', 'jams')])
        html = self.client.get('/').content.decode()
        self.assertIn('class="enter" href="/moods/tidings/"', html)                      # come in: Tidings
        self.assertIn(f'class="it" href="/moods/tidings/{tiding.id}/"', html)            # a link: its discussion
        self.assertIn('<li><a href="/moods/magenta-interface/">', html)                 # who spoke: that Mood


class SlogansTest(TestCase):
    """The front page's line: one of PickiPedia's Magenta:Slogans, taking turns (Justin, 22 Oct)."""

    PAGE = ("Slogans for magenta's front page. Add yours as a line starting with *.\n\n"
            f"* {front.SLOGAN}\n"
            "* where the '''[[Bill Monroe|bluegrass]]''' meets the [https://example.org wire]\n"
            "** <b>tea</b> and tunes\n"
            "not a slogan: no star\n"
            "* where the bluegrass meets the wire\n")

    def setUp(self):
        cache.clear()

    def answer(self, code, text=''):
        class Http:
            asked = 0

            def get(self, url, **kw):
                Http.asked += 1
                return type('Answer', (), {'status_code': code, 'text': text})()
        return Http()

    def test_each_starred_line_as_plain_words_once(self):
        self.assertEqual(front.slogans_in(self.PAGE),
                         [front.SLOGAN, 'where the bluegrass meets the wire', 'tea and tunes'])

    def test_read_from_the_wiki_and_kept(self):
        http = self.answer(200, self.PAGE)
        self.assertEqual(len(front.slogans(http)), 3)
        front.slogans(http)
        self.assertEqual(type(http).asked, 1)  # kept: not asked again

    def test_no_page_yet_is_justins_line_and_a_wiki_away_the_last_good_ones(self):
        self.assertEqual(front.slogans(self.answer(404)), [front.SLOGAN])
        cache.clear()
        front.slogans(self.answer(200, self.PAGE))
        cache.delete('front:slogans')
        self.assertEqual(len(front.slogans(self.answer(503))), 3)  # what it said last
        cache.clear()
        self.assertEqual(front.slogans(self.answer(503)), [front.SLOGAN])

    def test_the_page_shows_one_and_carries_them_all(self):
        cache.set('front:slogans', ['one line', 'another line'], 600)
        html = self.client.get('/').content.decode()
        self.assertTrue('>one line</p>' in html or '>another line</p>' in html)
        self.assertIn('"another line"', html)  # for the turns
