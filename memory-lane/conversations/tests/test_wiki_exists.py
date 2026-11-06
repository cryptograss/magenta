"""Whether a [[link]]'s page exists, for red and blue links (services/wiki_exists.py)."""

from unittest import mock

from django.core.cache import cache
from django.test import TestCase

from conversations.services import wiki_changes, wiki_exists


def wiki(pages, normalized=(), redirects=()):
    return mock.Mock(get=mock.Mock(return_value=mock.Mock(json=lambda: {'query': {
        'pages': pages, 'normalized': list(normalized), 'redirects': list(redirects)}})))


class ExistsTest(TestCase):
    def setUp(self):
        cache.clear()

    def test_asked_once_for_many_then_kept(self):
        http = wiki([{'title': 'Shady Grove', 'missing': True}, {'title': 'Band No. 7'}])
        self.assertEqual(wiki_exists.exists(['Shady_Grove', 'Band No. 7'], http=http), {'Shady Grove': False, 'Band No. 7': True})
        self.assertEqual(wiki_exists.exists(['Shady Grove', 'Band No. 7'], http=http), {'Shady Grove': False, 'Band No. 7': True})
        self.assertEqual(http.get.call_count, 1)
        self.assertEqual(http.get.call_args.kwargs['params']['titles'], 'Shady Grove|Band No. 7')

    def test_a_normalised_or_redirected_title_answers_as_asked(self):
        http = wiki([{'title': 'Table Mountain String Band'}], normalized=[{'from': 'table Mountain String Band', 'to': 'Table Mountain String Band'}],
                    redirects=[{'from': 'TMSB', 'to': 'Table Mountain String Band'}])
        self.assertEqual(wiki_exists.exists(['table Mountain String Band'], http=http), {'table Mountain String Band': True})
        self.assertEqual(wiki_exists.exists(['TMSB'], http=wiki([{'title': 'Table Mountain String Band'}], redirects=[{'from': 'TMSB', 'to': 'Table Mountain String Band'}])),
                         {'TMSB': True})

    def test_the_wiki_away_says_nothing(self):
        http = mock.Mock(get=mock.Mock(side_effect=OSError('down')))
        self.assertEqual(wiki_exists.exists(['Shady Grove'], http=http), {})

    def test_a_page_written_turns_its_links_blue_within_the_minute(self):
        wiki_exists.exists(['Shady Grove'], http=wiki([{'title': 'Shady Grove', 'missing': True}]))
        wiki_changes.forget('Shady_Grove')  # the sweep or the push saw it made
        self.assertEqual(wiki_exists.exists(['Shady Grove'], http=wiki([{'title': 'Shady Grove'}])), {'Shady Grove': True})

    def test_the_endpoint(self):
        with mock.patch('requests.get', wiki([{'title': 'Shady Grove', 'missing': True}, {'title': 'Band No. 7'}]).get):
            got = self.client.get('/api/wiki/exists/', {'titles': 'Shady Grove|Band No. 7'}).json()
        self.assertEqual(got, {'exists': {'Shady Grove': False, 'Band No. 7': True}})
        self.assertEqual(self.client.get('/api/wiki/exists/').json(), {'exists': {}})
