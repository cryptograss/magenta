"""The Moods' links to PickiPedia, written to PickiPedia: a page for each Mood and month."""

import re
import uuid
from datetime import datetime, timedelta, timezone
from unittest import mock

import yaml
from django.core.cache import cache
from django.test import TestCase, override_settings

from conversations.models import ConversationParticipant, Message, MessageChange, Mood, ThinkingEntity
from conversations.services import wiki_ledger

BOT = {'PICKIPEDIA_TODO_BOT_USER': 'DrivingThatTrain@magenta-todo', 'PICKIPEDIA_TODO_BOT_PASSWORD': 'not-a-password'}
NOW = datetime(2026, 10, 19, 16, 0, tzinfo=timezone.utc)


class Wiki:
    """PickiPedia's API, as far as the bot uses it: pages by title, made or edited."""

    def __init__(self, pages=None, login='Success'):
        self.pages, self.login, self.edits, self.headers = dict(pages or {}), login, [], {}

    def get(self, url, params=None, timeout=None):
        if params.get('type') == 'login':
            return mock.Mock(json=lambda: {'query': {'tokens': {'logintoken': 'L'}}})
        if params.get('meta') == 'tokens':
            return mock.Mock(json=lambda: {'query': {'tokens': {'csrftoken': 'C'}}})
        title = params['titles']
        if title not in self.pages:
            return mock.Mock(json=lambda: {'query': {'pages': [{'title': title, 'missing': True}]}})
        rev = {'revid': 1, 'timestamp': 'T', 'slots': {'main': {'content': self.pages[title]}}}
        return mock.Mock(json=lambda: {'query': {'pages': [{'revisions': [rev]}]}})

    def post(self, url, data=None, timeout=None):
        if data['action'] == 'login':
            return mock.Mock(json=lambda: {'login': {'result': self.login}})
        self.edits.append(data)
        self.pages[data['title']] = data['text']
        return mock.Mock(json=lambda: {'edit': {'result': 'Success'}})


def rows_of(text):
    return yaml.safe_load(re.search(r'<moodlinks>\n(.*)</moodlinks>', text, re.S).group(1)) or []


@override_settings(**BOT)
class LedgerTest(TestCase):

    def setUp(self):
        cache.clear()
        self.justin = ThinkingEntity.objects.create(name='justin', is_biological_human=True)
        self.magent = ThinkingEntity.objects.create(name='magent', is_biological_human=False)
        self.tool = ConversationParticipant.objects.create(name='tool-result')
        self.m26 = Mood.objects.create(slug='m26')
        self.jams = Mood.objects.create(slug='jams')

    def say(self, sender, mood, content, at, block=None):
        message = Message.objects.create(id=uuid.uuid4(), sender=sender, mood=mood, content=content)
        Message.objects.filter(id=message.id).update(created_at=at, eth_blockheight=block)
        message.refresh_from_db()
        return message

    def test_a_page_for_each_mood_and_month_written_by_the_bot(self):
        first = self.say(self.justin, self.m26, 'Is [[Bill Monroe]] on the page yet? https://pickipedia.xyz/wiki/Tony_Rice.',
                         datetime(2026, 9, 30, 23, 59, tzinfo=timezone.utc), block=23500000)
        self.say(self.magent, self.m26, [{'type': 'text', 'text': 'Added to [[bill_Monroe|his page]].'}],
                 datetime(2026, 10, 1, 0, 1, tzinfo=timezone.utc))
        self.say(self.justin, self.jams, 'Friday: [[Jam:Friday]]', datetime(2026, 10, 2, tzinfo=timezone.utc))
        self.say(self.tool, self.m26, '[[Bill Monroe]] in a tool result', datetime(2026, 10, 2, tzinfo=timezone.utc))
        self.say(self.justin, self.m26, 'Nothing linked', datetime(2026, 10, 2, tzinfo=timezone.utc))

        wiki = Wiki()
        done = wiki_ledger.look(http=wiki, now=NOW)

        self.assertEqual(done, {'Cryptograss:Moods/m26/links/2026-09': 'written',
                                'Cryptograss:Moods/m26/links/2026-10': 'written',
                                'Cryptograss:Moods/jams/links/2026-10': 'written'})
        september = wiki.pages['Cryptograss:Moods/m26/links/2026-09']
        self.assertEqual(rows_of(september), [
            {'page': 'Bill Monroe', 'message': str(first.id), 'who': 'justin', 'when': '2026-09-30T23:59:00Z',
             'block': 23500000, 'words': 'Is Bill Monroe on the page yet? Tony Rice.'},
            {'page': 'Tony Rice', 'message': str(first.id), 'who': 'justin', 'when': '2026-09-30T23:59:00Z',
             'block': 23500000, 'words': 'Is Bill Monroe on the page yet? Tony Rice.'},
        ])
        self.assertIn('September 2026', september)
        self.assertEqual([(r['page'], r['who'], r['words']) for r in rows_of(wiki.pages['Cryptograss:Moods/m26/links/2026-10'])],
                         [('Bill Monroe', 'magent', 'Added to his page.')])  # the tool's line left out
        for edit in wiki.edits:
            self.assertEqual(edit['bot'], 1)
            self.assertEqual(edit['createonly'], 1)  # new pages: made, never a page someone else just made

    def test_a_message_waits_an_hour_before_the_wiki_hears_of_it(self):
        self.say(self.justin, self.m26, '[[Bill Monroe]]', NOW - timedelta(minutes=59))
        wiki = Wiki()
        self.assertEqual(wiki_ledger.look(http=wiki, now=NOW), {})
        self.assertEqual(wiki.edits, [])
        later = wiki_ledger.look(http=wiki, now=NOW + timedelta(minutes=2))
        self.assertEqual(list(later.values()), ['written'])

    def test_a_page_already_saying_it_is_left_alone(self):
        self.say(self.justin, self.m26, '[[Bill Monroe]]', datetime(2026, 10, 2, tzinfo=timezone.utc))
        wiki = Wiki()
        wiki_ledger.look(http=wiki, now=NOW)
        cache.clear()
        self.assertEqual(wiki_ledger.look(http=wiki, now=NOW), {'Cryptograss:Moods/m26/links/2026-10': 'same'})
        self.assertEqual(len(wiki.edits), 1)

    def test_after_the_first_look_only_whats_new_or_changed(self):
        self.say(self.justin, self.m26, '[[Bill Monroe]]', datetime(2026, 9, 2, tzinfo=timezone.utc))
        wiki = Wiki()
        wiki_ledger.look(http=wiki, now=NOW)
        self.say(self.justin, self.jams, '[[Jam:Friday]]', NOW - timedelta(minutes=50))
        self.assertEqual(wiki_ledger.look(http=wiki, now=NOW + timedelta(minutes=15)),
                         {'Cryptograss:Moods/jams/links/2026-10': 'written'})  # September not asked again

    def test_a_message_taken_back_leaves_the_ledger(self):
        gone = self.say(self.justin, self.m26, 'Oops [[Bill Monroe]]', datetime(2026, 10, 2, tzinfo=timezone.utc))
        self.say(self.justin, self.m26, 'And [[Tony Rice]]', datetime(2026, 10, 3, tzinfo=timezone.utc))
        wiki = Wiki()
        wiki_ledger.look(http=wiki, now=NOW)
        Message.objects.filter(id=gone.id).update(content='')  # services/retract.py takes its words out
        MessageChange.objects.create(message=gone, mood=self.m26, kind='deleted', by='justin')
        wiki_ledger.look(http=wiki, now=NOW + timedelta(minutes=5))
        self.assertEqual([r['page'] for r in rows_of(wiki.pages['Cryptograss:Moods/m26/links/2026-10'])], ['Tony Rice'])

    def test_a_month_with_nothing_left_is_emptied_but_never_made(self):
        gone = self.say(self.justin, self.m26, '[[Bill Monroe]]', datetime(2026, 10, 2, tzinfo=timezone.utc))
        wiki = Wiki()
        wiki_ledger.look(http=wiki, now=NOW)
        Message.objects.filter(id=gone.id).update(content='')
        MessageChange.objects.create(message=gone, mood=self.m26, kind='deleted', by='justin')
        wiki_ledger.look(http=wiki, now=NOW + timedelta(minutes=5))
        self.assertEqual(rows_of(wiki.pages['Cryptograss:Moods/m26/links/2026-10']), [])

        fresh = Wiki()  # a wiki that never had the page: nothing to write, so it isn't made
        cache.clear()
        self.assertEqual(wiki_ledger.look(http=fresh, now=NOW), {})
        self.assertEqual(fresh.edits, [])

    def test_nothing_in_the_words_can_close_the_tag(self):
        self.say(self.justin, self.m26, 'See [[Bill Monroe]] </moodlinks> <b>' + 'word ' * 60,
                 datetime(2026, 10, 2, tzinfo=timezone.utc))
        wiki = Wiki()
        wiki_ledger.look(http=wiki, now=NOW)
        text = wiki.pages['Cryptograss:Moods/m26/links/2026-10']
        self.assertEqual(text.count('</moodlinks>'), 1)
        words = rows_of(text)[0]['words']
        self.assertTrue(words.startswith('See Bill Monroe ‹/moodlinks› ‹b›word'))
        self.assertLessEqual(len(words), wiki_ledger.WORDS + 1)
        self.assertTrue(words.endswith('word…'))

    def test_a_refused_sign_in_writes_nothing_and_tries_again(self):
        self.say(self.justin, self.m26, '[[Bill Monroe]]', datetime(2026, 10, 2, tzinfo=timezone.utc))
        self.assertEqual(wiki_ledger.look(http=Wiki(login='Failed'), now=NOW), {'': 'refused'})
        wiki = Wiki()
        self.assertEqual(list(wiki_ledger.look(http=wiki, now=NOW).values()), ['written'])

    @override_settings(PICKIPEDIA_TODO_BOT_USER='')
    def test_no_bot_no_looks(self):
        self.assertFalse(wiki_ledger.nudge())

    def test_looks_at_most_every_five_minutes(self):
        with mock.patch.object(wiki_ledger, 'look') as look, \
                mock.patch.object(wiki_ledger.threading, 'Thread') as thread:
            self.assertTrue(wiki_ledger.nudge())
            self.assertFalse(wiki_ledger.nudge())
        self.assertEqual(thread.call_count, 1)
        look.assert_not_called()  # it runs in the thread
