"""Magenta:Interface: the words people read where they're settable on PickiPedia (services/interface_words.py)."""

from unittest import mock
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
        self.assertEqual(interface_words.words(fetch=lambda: None), {**interface_words.DEFAULTS, **interface_words.SYSTEM_DEFAULTS, **interface_words.SOUND_DEFAULTS})
        cache.clear()
        self.assertEqual(interface_words.words(fetch=lambda: '* fork: kept')['fork'], 'kept')
        cache.delete('interface-words')

        def away():
            raise OSError('no wiki')
        self.assertEqual(interface_words.words(fetch=away)['fork'], 'kept')
        cache.delete('interface-words')
        self.assertEqual(interface_words.words(fetch=lambda: None), {**interface_words.DEFAULTS, **interface_words.SYSTEM_DEFAULTS, **interface_words.SOUND_DEFAULTS})  # no page: never a stale read

    def test_system_messages_have_their_own_page_and_room(self):
        # Justin, 3 Nov: Magenta:System Messages, not a page each; longer than an interface label.
        long = 'x' * 600
        found = interface_words.parse(f'* wiki-mention: {long}\n* fork: not this page\'s',
                                      interface_words.SYSTEM_DEFAULTS, interface_words.SYSTEM_MAX_LENGTH)
        self.assertEqual(found['wiki-mention'], long)
        self.assertNotIn('fork', found)
        cache.clear()
        with mock.patch.object(interface_words, '_fetch', side_effect=lambda page: '* wiki-mention: from the wiki'
                               if page == interface_words.SYSTEM_PAGE else None):
            words = interface_words.words()
        self.assertEqual(words['wiki-mention'], 'from the wiki')
        self.assertEqual(words['fork'], interface_words.DEFAULTS['fork'])

    def test_the_page_carries_them(self):
        from django.test import override_settings
        with override_settings(INTERFACE_WORDS_FROM_WIKI=False):
            page = self.client.get('/moods/').content.decode()
        self.assertIn('close-ask', page)
        self.assertIn('its emergents go on from where they are there', page)


class SoundDefaultsTest(TestCase):
    """Magenta:Sounds: the team's defaults for the bell's sounds, and a message's sound file (Justin, 5 Nov)."""

    def test_the_page_sets_them_and_leaves_the_rest(self):
        from conversations.services import interface_words
        page = "* sound-person: all\n* chime-person: File:Mandolin chop.flac\n* nonsense: x"
        found = interface_words.parse(page, interface_words.SOUND_DEFAULTS)
        self.assertEqual(found['sound-person'], 'all')
        self.assertEqual(found['chime-person'], 'File:Mandolin chop.flac')
        self.assertEqual(found['sound-agent'], 'off')
        self.assertNotIn('nonsense', found)
