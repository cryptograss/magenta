"""Blue Railroads beside people's names, counted from PickiPedia (services/tokens.py)."""

from unittest import mock

from django.core.cache import cache
from django.test import TestCase

from conversations.services import tokens

JUSTIN, SKYLER, STRANGER = '0x4f84b3650Dbf651732a41647618E7fF94A633F09', '0x5da9E9c29365959f1DE138Ba62c19274F7eccF4F', '0x9e53'


def wiki(answers):
    """A stand-in for embeds._ask: the SMW results for each query, as the wiki gives them."""
    def ask(params):
        q = params['query']
        for key, results in answers.items():
            if q.startswith(key):
                return {'query': {'results': results}}
        raise AssertionError(q)
    return ask


def token(owner):
    return {'printouts': {'Owner Address': [{'fulltext': owner}]}}


TOKENS = {f'Blue Railroad Token {i}': token(o) for i, o in enumerate([JUSTIN] * 8 + [SKYLER] * 3 + [STRANGER])}


class BlueRailroadsTest(TestCase):

    def setUp(self):
        cache.clear()

    def count(self, users, tokens_=TOKENS):
        with mock.patch('conversations.services.embeds._ask', wiki({'[[Category:Blue Railroad Tokens]]': tokens_,
                                                                     '[[Ethereum Address::+]]': users})), \
             mock.patch('conversations.services.wiki_auth.names', return_value={'justin': 'JMyles', 'skyler': 'SkymanJenkins'}):
            return tokens.blue_railroads()

    def test_each_person_is_counted_across_their_wallets_by_their_name_here(self):
        users = {'User:JMyles': {'printouts': {'Ethereum Address': [{'fulltext': JUSTIN.lower()}]}},  # any case
                 'User:SkymanJenkins': {'printouts': {'Ethereum Address': [{'fulltext': SKYLER}, {'fulltext': '0xempty'}]}},
                 'Justin Myles Holmes': {'printouts': {'Ethereum Address': [{'fulltext': JUSTIN}]}}}  # not a user page
        self.assertEqual(self.count(users), {'justin': 8, 'skyler': 3})

    def test_someone_holding_none_shows_nothing(self):
        users = {'User:Fibonacci': {'printouts': {'Ethereum Address': [{'fulltext': '0xnothing'}]}}}
        self.assertEqual(self.count(users), {})

    def test_the_wiki_away_keeps_the_last_count(self):
        users = {'User:JMyles': {'printouts': {'Ethereum Address': [{'fulltext': JUSTIN}]}}}
        self.assertEqual(self.count(users), {'justin': 8})
        cache.delete(tokens.BLUE_RAILROADS)
        with mock.patch('conversations.services.embeds._ask', side_effect=OSError('down')):
            self.assertEqual(tokens.blue_railroads(), {'justin': 8})

    def test_the_endpoint_serves_them(self):
        cache.set(tokens.BLUE_RAILROADS, {'justin': 8}, 60)
        self.assertEqual(self.client.get('/api/tokens/').json(), {'blue_railroad': {'justin': 8}})
