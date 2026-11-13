"""The colours every page shares are in one place, _theme.html, light and dark at once (light-dark()), so the
pages can't drift apart: before, eighteen pages each carried their own copy (phase 4 of
Cryptograss:Magenta/Modern web plan)."""

import re
from pathlib import Path

from django.test import SimpleTestCase

HERE = Path(__file__).resolve().parent.parent / 'templates' / 'conversations'
SHARED = ('--bg', '--panel', '--ink', '--muted', '--line', '--accent', '--accent-soft', '--code')


class ThemeTest(SimpleTestCase):

    def pages(self):
        # Whole pages, not the pieces they include (mood_tiding_tools, mood_tiding_comment).
        return [p for p in sorted(HERE.glob('mood*.html')) if p.read_text().startswith('<!DOCTYPE') and '<style>' in p.read_text()]

    def test_the_shared_colours_are_light_and_dark_at_once(self):
        theme = (HERE / '_theme.html').read_text()
        self.assertIn('color-scheme: light dark;', theme)
        for token in SHARED:
            self.assertRegex(theme, re.escape(token) + r': light-dark\(#[0-9a-f]{6}, #[0-9a-f]{6}\);')

    def test_every_page_takes_them_and_none_keeps_its_own_copy(self):
        self.assertGreaterEqual(len(self.pages()), 18)
        for page in self.pages():
            html = page.read_text()
            with self.subTest(page.name):
                self.assertIn('{% include "conversations/_theme.html" %}\n<style>', html)
                for token in SHARED:
                    self.assertNotRegex(html, r'(?<![\w-])' + re.escape(token) + r'\s*:')

    def test_no_page_swaps_its_colours_by_media_query(self):
        # Only what light-dark() can't say stays in one: which of a server's two portraits shows (display), and a
        # portrait drawn for the light given a light tile in the dark (padding, a corner).
        for page in self.pages():
            with self.subTest(page.name):
                for block in re.findall(r'@media \(prefers-color-scheme: dark\) \{(.*?)\n  \}', page.read_text(), re.S):
                    self.assertNotIn(':root', block)
