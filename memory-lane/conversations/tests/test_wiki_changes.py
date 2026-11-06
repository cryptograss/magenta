"""A change on PickiPedia reaches what's kept of the page within a minute (services/wiki_changes.py)."""

from unittest import mock

from django.core.cache import cache
from django.test import TestCase

from conversations.models import Message, Mood
from conversations.services import interface_words, mood_view, person_card, wiki_changes, wiki_preview

NOW = 1_800_000_000.0


def stamp(seconds_ago):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(NOW - seconds_ago, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def wiki(changes):
    """A wiki whose recent changes are these (newest first, as the API lists them)."""
    rows = [{'type': 'edit', 'ns': 2, 'title': c[0], 'rcid': c[1], 'revid': c[1] * 10, 'user': c[2],
             'timestamp': stamp(c[3])} for c in changes]
    return mock.Mock(get=mock.Mock(return_value=mock.Mock(json=lambda: {'query': {'recentchanges': rows}})))


class SweepTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        Mood.objects.create(slug='ops', title='ops')

    def setUp(self):
        cache.clear()
        # The hooks read the wiki again (services/wiki_hooks.py): not from here.
        patch = mock.patch('conversations.services.wiki_hooks.HOOKS', [])
        patch.start()
        self.addCleanup(patch.stop)

    def test_the_first_sweep_only_takes_its_bearings(self):
        forgotten, missed = wiki_changes.sweep(wiki([('User:Slvrbckt', 5, 'Slvrbckt', 600)]), now=NOW)
        self.assertEqual((forgotten, missed), (['User:Slvrbckt'], []))
        self.assertEqual(cache.get(wiki_changes.CURSOR), 5)

    def test_a_changed_page_is_forgotten_everywhere_it_is_kept(self):
        cache.set(wiki_changes.CURSOR, 4, None)
        cache.set(wiki_preview._key('User:Slvrbckt'), {'title': 'Slvrbckt'}, 3600)
        cache.set(person_card._edits_key('Slvrbckt'), [{'title': 'Shady Grove'}], 300)
        cache.set('interface-words', {'x': 'y'}, 600)
        cache.set('sound-defaults', {'x': 'y'}, 600)
        cache.set('name-frames', {'slvrbckt': '~'}, 600)
        wiki_changes.sweep(wiki([('Magenta:Sounds', 6, 'Magent', 200), ('User:Slvrbckt', 5, 'Slvrbckt', 600)]), now=NOW)
        self.assertIsNone(cache.get(wiki_preview._key('User:Slvrbckt')))
        self.assertIsNone(cache.get(person_card._edits_key('Slvrbckt')))
        self.assertIsNone(cache.get('sound-defaults'))
        self.assertIsNone(cache.get('name-frames'))
        self.assertEqual(cache.get('interface-words'), {'x': 'y'})  # another words page: its own words stay
        self.assertEqual(cache.get(wiki_changes.CURSOR), 6)

    def test_a_change_already_judged_is_left_alone(self):
        cache.set(wiki_changes.CURSOR, 5, None)
        cache.set(wiki_preview._key('User:Slvrbckt'), {'title': 'Slvrbckt'}, 3600)
        forgotten, _ = wiki_changes.sweep(wiki([('User:Slvrbckt', 5, 'Slvrbckt', 600)]), now=NOW)
        self.assertEqual(forgotten, [])
        self.assertIsNotNone(cache.get(wiki_preview._key('User:Slvrbckt')))

    def test_a_change_within_its_grace_is_forgotten_but_not_yet_judged(self):
        cache.set(wiki_changes.CURSOR, 4, None)
        cache.set(wiki_preview._key('Shady Grove'), {'title': 'Shady Grove'}, 3600)
        forgotten, _ = wiki_changes.sweep(wiki([('Shady Grove', 5, 'Slvrbckt', 10)]), now=NOW)
        self.assertEqual(forgotten, ['Shady Grove'])
        self.assertIsNone(cache.get(wiki_preview._key('Shady Grove')))
        self.assertEqual(cache.get(wiki_changes.CURSOR), 4)
        # A minute on: judged, and forgotten only the once.
        forgotten, _ = wiki_changes.sweep(wiki([('Shady Grove', 5, 'Slvrbckt', 100)]), now=NOW)
        self.assertEqual((forgotten, cache.get(wiki_changes.CURSOR)), ([], 5))

    def test_nothing_is_called_missed_before_a_push_was_ever_heard(self):
        cache.set(wiki_changes.CURSOR, 4, None)
        _, missed = wiki_changes.sweep(wiki([('Shady Grove', 5, 'Slvrbckt', 600)]), now=NOW)
        self.assertEqual(missed, [])
        self.assertFalse(Message.objects.filter(source_file=wiki_changes.SOURCE).exists())

    def test_a_change_no_push_told_of_is_a_line_in_ops(self):
        cache.set(wiki_changes.CURSOR, 4, None)
        wiki_changes.note_pushed('Magenta:Sounds', 60, 'Magent')
        _, missed = wiki_changes.sweep(wiki([('Magenta:Sounds', 6, 'Magent', 200), ('Shady Grove', 5, 'Slvrbckt', 600)]), now=NOW)
        self.assertEqual([c['title'] for c in missed], ['Shady Grove'])
        events = self.client.get('/api/moods/ops/turns/').json()['events']
        self.assertEqual([(e['type'], e['title'], e['revid'], e['user']) for e in events],
                         [('wiki-missed', 'Shady Grove', 50, 'Slvrbckt')])
        row = Message.objects.get(source_file=wiki_changes.SOURCE)
        self.assertEqual(mood_view.event_line(row.content),
                         "📡 PickiPedia didn't push rev 50 of [[Shady Grove]] by Slvrbckt: the sweep found it")
        # Judged once: the next sweep says nothing more of it.
        _, missed = wiki_changes.sweep(wiki([('Shady Grove', 5, 'Slvrbckt', 660)]), now=NOW)
        self.assertEqual(missed, [])
        self.assertEqual(Message.objects.filter(source_file=wiki_changes.SOURCE).count(), 1)

    def test_a_push_forgets_the_page_at_once(self):
        cache.set(wiki_preview._key('Magenta:Interface'), {'title': 'Magenta:Interface'}, 3600)
        cache.set('interface-words', {'x': 'y'}, 600)
        wiki_changes.note_pushed('Magenta:Interface', 70)
        self.assertIsNone(cache.get(wiki_preview._key('Magenta:Interface')))
        self.assertIsNone(cache.get('interface-words'))
        self.assertTrue(wiki_changes.was_pushed(70))
        self.assertTrue(cache.get(wiki_changes.PUSH_HEARD))

    def test_nudge_sweeps_once_a_minute(self):
        with mock.patch.object(wiki_changes, 'sweep') as sweep:
            self.assertTrue(wiki_changes.nudge(wait=True))
            self.assertFalse(wiki_changes.nudge(wait=True))
        self.assertEqual(sweep.call_count, 1)


class ForgetTest(TestCase):
    def setUp(self):
        cache.clear()

    def test_each_words_page_forgets_only_its_own(self):
        for key in ('interface-words', 'system-messages', 'sound-defaults'):
            cache.set(key, {'k': 'v'}, 600)
        interface_words.forget('Magenta:System_Messages')
        self.assertEqual([cache.get(k) for k in ('interface-words', 'system-messages', 'sound-defaults')],
                         [{'k': 'v'}, None, {'k': 'v'}])

    def test_a_file_a_release_and_an_instructions_page(self):
        cache.set('embeds:file:august-notification.flac', {'src': 'x'}, 600)
        cache.set('embeds:releases', {'a': 1}, 600)
        cache.set('mood-doc:leads', {'a': 1}, 600)
        wiki_changes.forget('File:August-notification.flac')
        wiki_changes.forget('Release:Some Record')
        wiki_changes.forget('Cryptograss:Moods/interfaces/Instructions')
        self.assertEqual([cache.get(k) for k in ('embeds:file:august-notification.flac', 'embeds:releases', 'mood-doc:leads')],
                         [None, None, None])
