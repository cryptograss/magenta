"""A frame's own address (?embed=1), opened as a whole page, is sent to the one page showing it (Justin, 4 Nov:
opened on its own, a tiding had no bar, and leaving it for Tidings loaded everything again)."""

import uuid

from django.test import Client, TestCase


class OpenedOnItsOwnTest(TestCase):

    def get(self, path, dest):
        return Client().get(path, **({'HTTP_SEC_FETCH_DEST': dest} if dest else {}))

    def test_a_tiding_opened_on_its_own_goes_to_the_one_page(self):
        tid = uuid.uuid4()
        page = self.get(f'/tidings/{tid}/?embed=1', 'document')
        self.assertEqual((page.status_code, page['Location']), (302, f'/tidings/{tid}/'))

    def test_a_section_keeps_what_else_it_was_asked(self):
        page = self.get('/tidings/?embed=1&order=new', 'document')
        self.assertEqual((page.status_code, page['Location']), (302, '/tidings/?order=new'))
        self.assertEqual(self.get('/polls/?embed=1', 'document')['Location'], '/polls/')

    def test_in_its_frame_or_unsaid_it_draws_as_before(self):
        for dest in ('iframe', None):
            page = self.get('/tidings/?embed=1', dest)
            self.assertEqual(page.status_code, 200, dest)
            self.assertEqual(page['X-Frame-Options'], 'SAMEORIGIN')
        self.assertEqual(self.get('/polls/?embed=1', 'iframe').status_code, 200)

    def test_the_one_page_carries_order_and_comment_into_its_frame(self):
        from pathlib import Path
        html = (Path(__file__).resolve().parent.parent / 'templates' / 'conversations' / 'moods.html').read_text()
        self.assertIn("frameFor('tidings', true, tidingsAt ? `${tidingsAt}?${asked}${location.hash}` : undefined)", html)
