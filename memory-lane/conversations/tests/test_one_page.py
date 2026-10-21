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
                html = self.client.get(f'/moods/{name}/').content.decode()
                self.assertIn(f'const initialSection = "{name}"', html)
                self.assertIn('id="sectionFrames"', html)

    def test_a_section_itself_is_framed_only_by_a_page_of_ours(self):
        for name in ('tidings', 'prompts', 'status', 'settings'):
            with self.subTest(name=name):
                response = self.client.get(f'/moods/{name}/?embed=1')
                self.assertEqual(response['X-Frame-Options'], 'SAMEORIGIN')
                self.assertIn('parent.postMessage({ magentaGo:', response.content.decode())  # its links go to the page
        self.assertEqual(self.client.get('/moods/general/')['X-Frame-Options'], 'DENY')  # the page itself: never framed

    def test_the_bar(self):
        html = self.client.get('/moods/general/').content.decode()
        self.assertIn('<a class="brand" href="/"', html)  # the mark alone, to the front page
        self.assertNotIn('<span class="logo" aria-hidden="true"></span>magenta</a>', html)
        self.assertIn('class="gear" href="/moods/settings/" data-section="settings"', html)
        self.assertIn('const initialSection = "" ||', html)
