"""A candidate branch named in a message links to its page in the ramp (Justin, 10 Nov: "I'd like for all candidate
branch names within messages to become links"): magenta:re/foo, `magenta:re/foo`, or magenta#7. Only a work whose
page opens: one open on the ramp, or one with a page already. Anything else stays as written."""

from unittest import mock

from django.core.cache import cache

from conversations.models import WorkTiding
from conversations.services import ramp, work_pages as work
from conversations.services.mood_view import render_html
from conversations.tests.test_work_pages import WorkRepo

LINK = '<a class="branchlink" href="/ramp/magenta/re/foo/" title="Its page, in the ramp">'


class BranchLinksTest(WorkRepo):

    def setUp(self):
        super().setUp()
        ramp.board()   # re/foo (#7) open on it, as the ramp has it
        cache.delete(work.LINKS_KEY)

    def test_a_bare_name_links_to_its_page(self):
        html = render_html('Merge magenta:re/foo.')
        self.assertIn(f'{LINK}magenta:re/foo</a>.', html)   # the full stop outside

    def test_a_name_in_code_links_in_codes_type(self):
        html = render_html('Built on `magenta:re/foo` (`abc1234`).')
        self.assertIn(f'<code>{LINK}magenta:re/foo</a></code>', html)
        self.assertIn('<code>abc1234</code>', html)

    def test_a_pull_requests_number_links_to_its_branchs_page(self):
        html = render_html('magenta#7 is ready')
        self.assertIn(f'{LINK}magenta#7</a>', html)

    def test_names_of_nothing_open_stay_as_written(self):
        for said in ('magenta:re/nope', 'magenta#99', 'rabbithole:re/foo', 'Cryptograss:Moods/ramp/todo',
                     're: model sonnet', 'at 04:53', 'C#7', 'see `magenta:re/nope`'):
            with self.subTest(said=said):
                self.assertNotIn('branchlink', render_html(said))

    def test_not_inside_a_url_or_a_longer_name(self):
        for said in ('https://github.com/cryptograss/magenta/tree/magenta:re/foo',
                     'x/magenta:re/foo', 'magenta:re/foo-bar', 'magenta:re/foo/more', 'magenta#7x'):
            with self.subTest(said=said):
                self.assertNotIn('branchlink', render_html(said))

    def test_a_branch_merged_off_the_ramp_still_links_if_it_has_a_page(self):
        self.as_('justin')
        self.open_()
        self.cards['open'] = []
        cache.delete(ramp.KEY)   # no board kept at all: never built just to draw a message
        with mock.patch.object(ramp, 'board', side_effect=AssertionError('built')):
            cache.delete(work.LINKS_KEY)
            self.assertIn(f'{LINK}magenta:re/foo</a>', render_html('magenta:re/foo merged'))
            self.assertIn(f'{LINK}magenta#7</a>', render_html('magenta#7 merged'))
            self.assertNotIn('branchlink', render_html('magenta:re/bar'))

    def test_a_page_opened_links_at_once(self):
        board = cache.get(ramp.KEY)
        cache.delete(ramp.KEY)
        cache.delete(work.LINKS_KEY)
        self.assertNotIn('branchlink', render_html('magenta:re/foo'))   # nothing known, and that's kept a while
        cache.set(ramp.KEY, board, None)
        self.assertNotIn('branchlink', render_html('magenta:re/foo'))
        self.as_('justin')
        self.assertEqual(self.open_().status_code, 200)
        self.assertIn(f'{LINK}magenta:re/foo</a>', render_html('magenta:re/foo'))
