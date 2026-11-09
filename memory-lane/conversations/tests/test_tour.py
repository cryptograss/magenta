"""The guided tour (services/tour.py): Magenta:Tour's steps, read from its trusted revision, against the page's
data-tour anchors, both ways (Justin and re, 4 Nov, #docs-tests-qa)."""

import re
from unittest import mock

from django.core.cache import cache
from django.test import Client, TestCase

from conversations.services import tour

# re's contract (#docs-tests-qa, 4 Nov): the anchors the Tour is written against. A rename here is a rename there.
CONTRACT = ['moods', 'mood-bar', 'pins', 'sign-in', 'composer', 'emergents-bar', 'attach', 'memo', 'message-actions',
            'more-menu', 'todo', 'search', 'jump', 'bell', 'nav-tidings', 'nav-polls', 'faq', 'user-menu', 'help',
            'voice-mode']

PAGE = """Intro for people reading the wiki.
== The Moods ==
{{Tour step|at=moods|text=Every Mood is a room; see [[Magenta:Moods|the Moods]].}}
{{Tour step|at=composer|text=Type here. '''Enter''' sends.|needs=signin}}
{{Tour step|at=search|text=Search what was said.|try=yes}}
{{Tour step|at=nowhere|text=An anchor the page doesn't have.}}
{{Tour step|text=No anchor: left out.}}
{{Tour step|at=bell|text=<script>alert(1)</script> {{verified|by JMyles}}|needs=nonsense}}
"""


def history(text, editor='JMyles', revision=42):
    return lambda title: [(revision + 1, 'SomeoneElse', 'vandalised'), (revision, editor, text)]


class TourPageTest(TestCase):
    def setUp(self):
        cache.clear()

    def test_steps_in_order_with_their_fields(self):
        steps = tour.steps_of(PAGE)
        self.assertEqual([s['at'] for s in steps], ['moods', 'composer', 'search', 'nowhere', 'bell'])
        self.assertIn('<a href="https://pickipedia.xyz/wiki/Magenta:Moods" target="_blank" rel="noopener">the Moods</a>',
                      steps[0]['text'])
        self.assertEqual(steps[1]['text'], 'Type here. <b>Enter</b> sends.')
        self.assertEqual(steps[1]['needs'], 'signin')
        self.assertTrue(steps[2]['try'])
        self.assertFalse(steps[0]['try'])

    def test_words_are_never_markup(self):
        bell = tour.steps_of(PAGE)[-1]
        self.assertNotIn('<script>', bell['text'])
        self.assertIn('&lt;script&gt;', bell['text'])
        self.assertNotIn('verified', bell['text'])  # a verify mark isn't words
        self.assertEqual(bell['needs'], '')         # an unknown need is no need

    def test_only_a_trusted_revision_is_read(self):
        with mock.patch('poller.wiki_rules.EDITORS', ['JMyles']):
            got = tour.state(get=history(PAGE))
        self.assertEqual(got['revision'], 42)
        with mock.patch('poller.wiki_rules.EDITORS', ['JMyles']):
            got = tour.state(get=lambda title: [(9, 'SomeoneElse', PAGE)])
        self.assertEqual(got['steps'], [])
        self.assertTrue(got['error'])

    def test_the_wiki_down_keeps_the_last_good_read(self):
        with mock.patch('poller.wiki_rules.EDITORS', ['JMyles']):
            tour.state(get=history(PAGE))

            def down(title):
                raise OSError('unreachable')
            got = tour.state(get=down)
        self.assertEqual(got['revision'], 42)
        self.assertIn('could not be read', got['error'])

    def test_runnable_leaves_out_a_step_with_no_anchor(self):
        with mock.patch('poller.wiki_rules.EDITORS', ['JMyles']):
            got = tour.runnable(get=history(PAGE))
        self.assertNotIn('nowhere', [s['at'] for s in got['steps']])
        self.assertIn('composer', [s['at'] for s in got['steps']])

    def test_health_both_ways(self):
        with mock.patch('poller.wiki_rules.EDITORS', ['JMyles']):
            got = tour.health(get=history(PAGE))
        self.assertEqual(got['missing'], ['nowhere'])
        self.assertIn('voice-mode', got['unused'])
        self.assertNotIn('composer', got['unused'])


class TourAnchorsTest(TestCase):
    def test_the_page_has_every_anchor_re_writes_against(self):
        self.assertEqual(sorted(set(CONTRACT) - tour.anchors()), [])

    def test_no_anchor_outside_the_contract(self):
        """A new anchor is a new word in the contract: add it to CONTRACT and tell the Tour's writer."""
        self.assertEqual(sorted(tour.anchors() - set(CONTRACT)), [])


class TourViewsTest(TestCase):
    def setUp(self):
        cache.clear()

    def test_api(self):
        with mock.patch('conversations.services.tour.state', return_value={
                'revision': 7, 'error': '', 'steps': [{'at': 'composer', 'text': 'x', 'try': False, 'needs': ''},
                                                      {'at': 'nowhere', 'text': 'y', 'try': False, 'needs': ''}]}):
            got = Client().get('/api/tour/').json()
        self.assertEqual(got['revision'], 7)
        self.assertEqual([s['at'] for s in got['steps']], ['composer'])

    def test_the_vendored_library_is_served_and_nothing_else(self):
        js = Client().get('/moods/tour/driver.js')
        self.assertEqual(js.status_code, 200)
        self.assertEqual(js['Content-Type'], 'text/javascript')
        self.assertIn(b'driver', js.content[:200])
        self.assertEqual(Client().get('/moods/tour/driver.css')['Content-Type'], 'text/css')
        self.assertEqual(Client().get('/moods/tour/LICENSE').status_code, 404)
        self.assertEqual(Client().get('/moods/tour/..%2Fmodels.py').status_code, 404)

    def test_on_the_status_page(self):
        with mock.patch('conversations.services.tour.health', return_value={'missing': ['x'], 'unused': []}):
            from conversations.services import status
            with mock.patch.object(status, 'agents_across_moods', return_value={}), \
                    mock.patch.object(status, 'servers_with_uptime', return_value=[]), \
                    mock.patch.object(status, 'magenta_version', return_value={}), \
                    mock.patch.object(status, 'pickipedia_mcp_status', return_value={}):
                self.assertEqual(status.full_status()['tour'], {'missing': ['x'], 'unused': []})


class HelpMenuTest(TestCase):
    """The help menu (Justin, 7 Nov, #docs-tests-qa): a ? beside the state chevron, and under it the one place each
    answer lives. The user menu no longer carries help, faq or the tour, so there is one place to keep current."""

    PAGE = (tour.TEMPLATES / 'moods.html').read_text()

    def menu(self, element_id):
        start = self.PAGE.index(f'id="{element_id}"')
        return self.PAGE[start:self.PAGE.index('</div>', start)]

    def test_the_seven_lines_in_order(self):
        menu = self.menu('helpMenu')
        hrefs = re.findall(r'<a href="([^"]+)"', menu)
        self.assertEqual(hrefs, ['https://pickipedia.xyz/wiki/Magenta:Help', 'https://pickipedia.xyz/wiki/Cryptograss:Magenta_app', '#', 'https://pickipedia.xyz/wiki/Magenta',
                                 'https://pickipedia.xyz/wiki/Magenta:Humans', 'https://pickipedia.xyz/wiki/Magenta:Emergents', '/faq/'])
        self.assertIn('id="tourLink"', menu)
        self.assertIn('data-tour-open="helpBtn"', self.PAGE)  # the tour opens it for the help and faq steps

    def test_the_tour_anchors_moved_with_the_links(self):
        menu = self.menu('helpMenu')
        self.assertIn('data-tour="help"', menu)
        self.assertIn('data-tour="faq"', menu)
        user_menu = self.menu('userMenu')
        for gone in ('Magenta:Help', '/faq/', 'tourLink'):
            self.assertNotIn(gone, user_menu)

    def test_the_button_sits_right_of_the_state_chevron(self):
        self.assertLess(self.PAGE.index('id="stateBtn"'), self.PAGE.index('id="helpBtn"'))
        self.assertLess(self.PAGE.index('id="helpBtn"'), self.PAGE.index('id="searchBtn"'))
