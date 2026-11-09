"""A long address in a turn's code or link breaks where it must (Skyler, 8 Nov: a master.m3u8 address in
inline code made the Mood wider than an iPhone and slid the page sideways)."""

from pathlib import Path

from django.test import SimpleTestCase

PAGE = Path(__file__).resolve().parent.parent / 'templates' / 'conversations' / 'moods.html'


class LongCodeWrapsTest(SimpleTestCase):

    def test_inline_code_and_links_in_a_turn_may_break_anywhere(self):
        html = PAGE.read_text()
        code = next(l for l in html.splitlines() if l.strip().startswith('.turn .body code {'))
        self.assertIn('overflow-wrap: anywhere', code)
        self.assertIn('.turn .body a { overflow-wrap: anywhere; }', html)
