"""On a touch screen a message's heading line drops below the floated play buttons rather than squeeze
between them (Skyler, 8 Nov: time and model badge in a column a word wide, under the ⏩)."""

from pathlib import Path

from django.test import SimpleTestCase

PAGE = Path(__file__).resolve().parent.parent / 'templates' / 'conversations' / 'moods.html'


class PhoneHeaderWrapsTest(SimpleTestCase):

    def test_heading_line_wraps_by_part_and_keeps_a_minimum_width_on_touch(self):
        html = PAGE.read_text()
        coarse = html[html.index('.turn.cont { display: flow-root; }'):]
        coarse = coarse[:coarse.index('}\n  }') + 1]  # to the end of that @media (pointer: coarse) block
        self.assertIn('.turn .who { flex-wrap: wrap; row-gap: 2px; min-width: 60%; }', coarse)
        self.assertIn('.turn .who > * { white-space: nowrap; }', coarse)

    def test_the_floated_buttons_heading_of_a_runs_first_message_is_exempt(self):
        html = PAGE.read_text()
        rule = next(l for l in html.splitlines()
                    if '@media (pointer: coarse) { .abody > article.turn:first-of-type > .who {' in l)
        self.assertIn('min-width: 0;', rule)
