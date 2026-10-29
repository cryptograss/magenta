"""Magenta:Interface: the words people read where they're settable on PickiPedia (services/interface_words.py)."""

from django.core.cache import cache
from django.test import TestCase

from conversations.services import interface_words


class InterfaceWordsTest(TestCase):

    def setUp(self):
        cache.clear()

    def test_the_page_sets_what_it_names_and_nothing_else(self):
        page = ('== Threads ==\n* fork: they carry on, memories and all\n* fresh:   a clean slate  \n'
                '* nonsense: ignored\n<!-- * fresh: a comment -->\n* close-button: ' + 'x' * 301 + '\n'
                '<nowiki>* fork-label: an example</nowiki>\n* fresh-label: {{tpl}}')
        words = interface_words.parse(page)
        self.assertEqual(words['fork'], 'they carry on, memories and all')
        self.assertEqual(words['fresh'], 'a clean slate')
        self.assertNotIn('nonsense', words)
        self.assertEqual(words['close-button'], interface_words.DEFAULTS['close-button'])  # too long: kept as it was
        self.assertEqual(words['fork-label'], 'Fork')
        self.assertEqual(words['fresh-label'], 'Fresh')  # a template alone leaves nothing to say
        self.assertNotIn('letter', interface_words.DEFAULTS['new-hint'])  # the letter was retired

    def test_no_page_the_defaults_and_the_wiki_away_the_last_good_read(self):
        self.assertEqual(interface_words.words(fetch=lambda: None), interface_words.DEFAULTS)
        cache.clear()
        self.assertEqual(interface_words.words(fetch=lambda: '* fork: kept')['fork'], 'kept')
        cache.delete('interface-words')

        def away():
            raise OSError('no wiki')
        self.assertEqual(interface_words.words(fetch=away)['fork'], 'kept')
        cache.delete('interface-words')
        self.assertEqual(interface_words.words(fetch=lambda: None), interface_words.DEFAULTS)  # no page: never a stale read

    def test_the_page_carries_them(self):
        from django.test import override_settings
        with override_settings(INTERFACE_WORDS_FROM_WIKI=False):
            page = self.client.get('/moods/').content.decode()
        self.assertIn('close-ask', page)
        self.assertIn('its emergents go on from where they are there', page)
