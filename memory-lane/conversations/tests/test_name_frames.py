"""A name's frame in the Moods list while it's at work (Justin, re and blu, 1 Nov): dots by default, or what its
owner chose on their own User page, {{Name frame|~}} (services/emergent_powers.frames)."""

from django.core.cache import cache
from django.test import SimpleTestCase

from conversations.services import emergent_powers as ep


class NameFrameTest(SimpleTestCase):

    def setUp(self):
        cache.delete(ep.FRAMES_KEY)
        cache.delete(ep.FRAMES_KEY + ':last')

    def test_a_few_marks_only(self):
        self.assertEqual(ep.frame_in('I am re. {{Name frame|~}}'), '~')
        self.assertEqual(ep.frame_in('{{name frame| -+ }}'), '-+')
        for nope in ('{{Name frame|ab}}', '{{Name frame|<b>}}', '{{Name frame|~~~~}}', '{{Name frame|}}', 'no frame'):
            self.assertEqual(ep.frame_in(nope), '', nope)

    def test_read_from_their_own_page(self):
        pages = {'Re': 'She/her. {{Name frame|~}}', 'Blu': 'nothing chosen'}
        found = ep.frames([('re', False), ('blu', False)], fetch=lambda user: pages.get(user, ''))
        self.assertEqual(found, {'re': '~'})

    def test_a_wiki_that_does_not_answer_leaves_what_was_read_last(self):
        ep.frames([('re', False)], fetch=lambda user: '{{Name frame|~}}')
        cache.delete(ep.FRAMES_KEY)

        def down(user):
            raise OSError('no wiki')
        self.assertEqual(ep.frames([('re', False)], fetch=down), {'re': '~'})
