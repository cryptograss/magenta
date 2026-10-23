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

    def test_its_own_address_as_a_qr_code_at_the_foot(self):
        # Justin, 23 Oct: held up for a phone across the table. The address is whatever served it.
        html = self.client.get('/', secure=True).content.decode()
        self.assertIn('<svg viewBox=', html)
        self.assertIn('class="qr"', html)
        self.assertIn('https://testserver/', html)
        self.assertNotIn('<svg viewBox=', html.split('<footer')[0])  # at the foot, not before

    def test_a_qr_code_says_its_address(self):
        svg = front.qr_svg('https://example.test/')
        self.assertTrue(svg.startswith('<svg viewBox="0 0 '))
        self.assertIn('fill="#ece7e1"', svg)  # light behind dark: every camera reads that way round
        self.assertIs(front.qr_svg('https://example.test/'), svg)  # drawn once

    def test_a_phone_is_offered_the_app_on_both_pages(self):
        for path in ('/', '/moods/'):
            with self.subTest(path=path):
                html = self.client.get(path).content.decode()
                self.assertIn('id="installNudge"', html)
                self.assertIn('beforeinstallprompt', html)

    def test_the_top_links_most_upvoted_first(self):
        made = []
        for i in range(4):
            said = self.say(self.skyler, self.jams, f'https://example.org/{i}')
            made.append(tiding_links.promote(said, self.justin, title=f'Link {i}'))
        TidingVote.objects.create(tiding=made[1], by=self.skyler)
        TidingVote.objects.create(tiding=made[1], by=self.justin)
        TidingVote.objects.create(tiding=made[0], by=self.justin)
        self.assertEqual([t['title'] for t in front.top_tidings()], ['Link 1', 'Link 0', 'Link 3', 'Link 2'])  # five at most
        html = self.client.get('/').content.decode()
        self.assertIn(f'/moods/tidings/{made[1].id}/', html)
        self.assertIn('Link 2', html)

    def test_who_spoke_lately_where_and_its_first_words(self):
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
        self.assertIn('a secret-ish thing said in jams', html)  # its first 80 characters (Justin, 23 Oct)
        self.assertIn('>latest</h2>', html)
        self.assertNotIn('what is this?', html)  # no links at the foot, for now
        self.say(self.justin, self.jams, 'x' * 100)
        cache.delete('front:state')
        self.assertEqual(front.lately()[0]['said'], 'x' * 80 + '…')

    def test_a_name_as_pickipedia_has_it_in_its_own_art(self):
        from conversations.services import settings as knobs
        knobs.change('name_art', '░ JMyles ░', agent=self.skyler, by=self.skyler)
        knobs.change('name_colours', '.dddddd', agent=self.skyler, by=self.skyler)
        with mock.patch('conversations.services.wiki_auth.names', return_value={'justin': 'JMyles', 'skyler': 'SkymanJenkins'}):
            self.say(self.justin, self.jams, 'hello')
            self.say(self.skyler, self.dev, 'hi')
            names = {s['who']: s['name'] for s in front.lately()}
        self.assertEqual(names['justin'], 'JMyles')  # his PickiPedia name, not the one here
        self.assertIn('class="art" title="SkymanJenkins"', names['skyler'])
        self.assertIn('<span class="ad">J</span>', names['skyler'])  # each letter in its colour

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

    def test_an_agents_line_waits_for_a_person_and_templates_are_not_words(self):
        # PickiPedia wraps what a bot writes in {{Bot_proposes}} until a person verifies it (22 Oct: magent's
        # first copy of Justin's line came back so, and would have been shown as markup).
        page = ("{{Bot_proposes|Add yours: each * line.|by=Magent}}\n"
                "* {{Bot_proposes|a line magent wrote|by=Magent}}\n"
                "* a line a person wrote{{citation needed}}\n"
                "* {{bot proposes|another of magent's|by=Magent}}\n")
        self.assertEqual(front.slogans_in(page), ['a line a person wrote'])
        self.assertEqual(front.slogans(self.answer(200, page[:page.index('* a line a person')])), [front.SLOGAN])
