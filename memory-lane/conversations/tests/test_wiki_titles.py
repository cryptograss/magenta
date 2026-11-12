"""[[ suggestions match anywhere in a title (Justin, 12 Nov): the titles, once, from services/wiki_titles."""

from pathlib import Path
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings

from conversations.services import wiki_changes, wiki_titles

TEMPLATES = Path(__file__).resolve().parent.parent / 'templates' / 'conversations'


def answers(pages):
    """A wiki that answers allpages for namespace 0 in two pieces, and nothing for the others."""
    def get(url, params=None, **kw):
        got = mock.Mock()
        if params['apnamespace'] != 0:
            got.json.return_value = {'query': {'allpages': []}}
        elif 'apcontinue' not in params:
            got.json.return_value = {'query': {'allpages': [{'title': t} for t in pages[:2]]},
                                     'continue': {'apcontinue': pages[2], 'continue': '-||'}}
        else:
            got.json.return_value = {'query': {'allpages': [{'title': t} for t in pages[2:]]}}
        return got
    return mock.Mock(get=mock.Mock(side_effect=get))


@override_settings(PICKIPEDIA_URL='https://pickipedia.xyz')
class WikiTitlesTest(TestCase):

    def setUp(self):
        cache.clear()

    def test_every_title_followed_through_its_pieces_and_kept(self):
        http = answers(['Reuben', "Reuben's Train", 'Tony Rice'])
        self.assertEqual(wiki_titles.titles(http), ['Reuben', "Reuben's Train", 'Tony Rice'])
        asked = http.get.call_count
        self.assertEqual(asked, len(wiki_titles.NAMESPACES) + 1)
        wiki_titles.titles(http)
        self.assertEqual(http.get.call_count, asked)

    def test_a_silent_wiki_is_an_empty_list_kept_briefly(self):
        http = mock.Mock(get=mock.Mock(side_effect=OSError('down')))
        self.assertEqual(wiki_titles.titles(http), [])
        self.assertEqual(cache.get(wiki_titles.KEY), [])

    def test_a_page_written_drops_the_list_an_edit_does_not(self):
        wiki_titles.titles(answers(['Reuben', "Reuben's Train", 'Tony Rice']))
        wiki_changes.forget("Reuben's_Train")
        self.assertIsNotNone(cache.get(wiki_titles.KEY))
        wiki_changes.forget('Kuba Hejhal')
        self.assertIsNone(cache.get(wiki_titles.KEY))

    def test_the_page_asks_for_them(self):
        cache.set(wiki_titles.KEY, ['Kuba Hejhal'], 60)
        got = self.client.get('/api/wiki/titles/')
        self.assertEqual(got.json(), {'titles': ['Kuba Hejhal']})
        self.assertIn('max-age=300', got['Cache-Control'])

    def test_both_composers_match_anywhere(self):
        for page in ('moods.html', 'mood_tiding.html'):
            html = (TEMPLATES / page).read_text()
            self.assertIn('{% include "conversations/wiki_titles_js.html" %}', html)
            self.assertIn('wikiMatch(await wikiTitleList(), prefix)', html)
