"""One page: the Moods, and tidings, prompts, status and settings beside them, each loaded once and kept
(Justin, 21 Oct: going back and forth between them shouldn't load the Moods again)."""

from django.test import TestCase

from conversations.models import Mood


class OnePageTest(TestCase):

    def setUp(self):
        Mood.objects.create(slug='general', title='general')

    def test_a_sections_address_is_the_one_page_showing_it(self):
        for name in ('tidings', 'prompts', 'status', 'settings'):
            with self.subTest(name=name):
                html = self.client.get(f'/{name}/').content.decode()
                self.assertIn(f'const initialSection = "{name}"', html)
                self.assertIn('id="sectionFrames"', html)

    def test_a_sections_old_address_is_sent_to_its_own(self):
        """Justin, 9 Nov: only the Moods and threads are /moods/...; links to the old addresses still land."""
        for old, new in (('/moods/tidings/', '/tidings/'), ('/moods/pulls/', '/ramp/'), ('/moods/settings/?embed=1', '/settings/?embed=1'),
                         ('/moods/tidings/0b6a2c1e-6c1f-4f5e-9d7e-2b8f6a1c3d4e/', '/tidings/0b6a2c1e-6c1f-4f5e-9d7e-2b8f6a1c3d4e/')):
            with self.subTest(old=old):
                response = self.client.get(old)
                self.assertEqual(response.status_code, 301)
                self.assertEqual(response['Location'], new)

    def test_the_ramp_is_beside_polls_in_the_top_bar(self):
        html = self.client.get('/moods/').content.decode()
        bar = html[html.index('aria-label="Sections"'):html.index('id="sectionsMoreMenu"')]
        self.assertIn('href="/ramp/" data-section="ramp"', bar)
        self.assertLess(bar.index('data-section="polls"'), bar.index('data-section="ramp"'))

    def test_a_section_itself_is_framed_only_by_a_page_of_ours(self):
        for name in ('tidings', 'prompts', 'status', 'settings'):
            with self.subTest(name=name):
                response = self.client.get(f'/{name}/?embed=1')
                self.assertEqual(response['X-Frame-Options'], 'SAMEORIGIN')
                self.assertIn('parent.postMessage({ magentaGo:', response.content.decode())  # its links go to the page
        self.assertEqual(self.client.get('/moods/general/')['X-Frame-Options'], 'DENY')  # the page itself: never framed

    def test_the_bar(self):
        html = self.client.get('/moods/general/').content.decode()
        self.assertIn('<a class="brand" href="/"', html)  # the mark alone, to the front page
        self.assertNotIn('<span class="logo" aria-hidden="true"></span>magenta</a>', html)
        self.assertIn('class="gear" href="/settings/" data-section="settings"', html)
        self.assertIn('const initialSection = "" ||', html)

    def test_who_is_at_work_shows_on_tidings_and_the_ramp(self):
        # Justin, 9 Nov (#ramp): the emergents' activity bar on Tidings and the ramp, from every Mood's live data.
        html = self.client.get('/ramp/').content.decode()
        self.assertIn('<div id="sectionActivity" role="status" aria-label="Emergents at work" hidden></div>', html)
        self.assertIn("const ACTIVITY_SECTIONS = new Set(['tidings', 'ramp']);", html)
        self.assertLess(html.index('id="sectionActivity"'), html.index('id="sectionFrames"'))  # above the section
