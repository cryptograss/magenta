"""PickiPedia's front page, the live bits (services/front_page.py): the top tidings and the last few things
people said, written to Template:Magenta tidings and Template:Magenta latest by the wiki bot (Justin,
#pickipedia-style-and-main-page, 11 Nov 2026)."""

import uuid
from datetime import datetime, timedelta, timezone
from unittest import mock

from django.core.cache import cache
from django.test import TestCase

from conversations.models import Message, Mood, ThinkingEntity
from conversations.services import front_page, todo_ticks

T0 = datetime(2026, 11, 11, 22, 0, tzinfo=timezone.utc)


def at(minutes):
    return T0 + timedelta(minutes=minutes)


class FakeBot(todo_ticks.Bot):
    """Signed in, with pages in memory; write() is the real one."""

    def __init__(self):
        self.pages, self.edits = {}, []

    def login(self):
        return True

    def read(self, title):
        text = self.pages.get(title)
        return None if text is None else {'revid': len(self.edits), 'timestamp': 't', 'slots': {'main': {'content': text}}}

    def edit(self, title, text, summary, revision=None, minor=False):
        self.pages[title] = text
        self.edits.append((title, summary))
        return {'edit': {'result': 'Success'}}


LINKS = [
    {'id': 'kuba', 'pinned': True, 'host': 'pickipedia.xyz', 'by': 'justin', 'at': '2026-11-11T22:12:08+00:00',
     'url': "https://pickipedia.xyz/wiki/Kuba%27s_Last_Merged_Commits:_the_story_of_arthel159",
     'title': "Kuba's Last Merged Commits: the story of arthel159", 'comments': 0, 'talk': 0, 'release': ''},
    {'id': 'stewart', 'pinned': False, 'host': 'bbc.com', 'by': 'justin', 'at': datetime(2026, 10, 29, tzinfo=timezone.utc),
     'url': 'https://www.bbc.com/news/live/x', 'title': 'Sir ]] Patrick }} Stewart |dies', 'comments': 2, 'talk': 1,
     'release': 'QmX'},
]


class FrontPageTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.justin = ThinkingEntity.objects.create(name='justin', is_biological_human=True)
        cls.magent = ThinkingEntity.objects.create(name='magent', is_biological_human=False)
        cls.jams = Mood.objects.create(slug='jams', title='jams')
        cls.old = Mood.objects.create(slug='old', title='old')

    def setUp(self):
        cache.clear()
        archived = mock.patch('conversations.services.settings.archived_slugs', return_value={'old'})
        archived.start()
        self.addCleanup(archived.stop)

    def said(self, sender, text, minutes, mood=None):
        m = Message.objects.create(id=uuid.uuid4(), sender=sender, mood=mood or self.jams, timestamp=1,
                                   content=text if sender.is_biological_human else [{'type': 'text', 'text': text}])
        Message.objects.filter(id=m.id).update(created_at=at(minutes))
        return m

    def test_a_pickipedia_link_is_a_wiki_link_and_the_rest_are_external_with_the_title_verbatim(self):
        text = front_page.tidings_wikitext(LINKS)
        self.assertIn("[[Kuba's Last Merged Commits: the story of arthel159|<nowiki>Kuba's Last Merged Commits: "
                      "the story of arthel159</nowiki>]]", text)
        self.assertIn('<nowiki>📌 pickipedia.xyz · justin · 11 Nov</nowiki>', text)
        self.assertIn('[https://www.bbc.com/news/live/x <nowiki>Sir ]] Patrick }} Stewart |dies</nowiki>]', text)
        self.assertIn('<nowiki>bbc.com · justin · 29 Oct · release</nowiki>', text)
        self.assertIn('/tidings/stewart/ 1 in the talk]', text)
        self.assertNotIn('/tidings/kuba/', text)  # nothing said of it yet
        self.assertIn('/tidings/ All the tidings on magenta →]', text)

    def test_latest_is_people_only_newest_first_with_words_cut_to_240(self):
        self.said(self.justin, 'Shady Grove, my little love', 0)
        self.said(self.magent, 'I am an emergent, not a person', 1)
        self.said(self.justin, '/clips huh', 2)
        self.said(self.justin, 'hidden, the Mood is archived', 3, mood=self.old)
        long = self.said(self.justin, 'x' * 300, 4)
        posts = front_page.latest()
        self.assertEqual([p['by'] for p in posts], ['justin', 'justin'])
        self.assertEqual(posts[0]['id'], str(long.id))
        self.assertEqual(len(posts[0]['text']), 240)
        self.assertTrue(posts[0]['text'].endswith('…'))
        self.assertEqual(posts[1]['text'], 'Shady Grove, my little love')
        self.assertEqual(len(front_page.latest(limit=1)), 1)

    def test_the_cards_keep_the_words_verbatim_and_link_the_post(self):
        text = front_page.latest_wikitext([{'id': 'abc', 'by': 'justin', 'mood': 'jams', 'at': at(5),
                                            'text': "[[Not a link]] and {{not|a template}}\nsecond line"}])
        self.assertIn("'''<nowiki>justin</nowiki>''' · [https://magenta.cryptograss.live/moods/jams/#m-abc #jams] · 11 Nov 22:05Z", text)
        self.assertIn('<nowiki>[[Not a link]] and {{not|a template}} second line</nowiki>', text)
        self.assertIn('Nothing said yet', front_page.latest_wikitext([]))

    def test_look_writes_both_templates_and_only_when_they_change(self):
        self.said(self.justin, 'Shady Grove', 0)
        bot = FakeBot()
        with mock.patch('conversations.services.tiding_links.listed', return_value=LINKS):
            first = front_page.look(bot)
            again = front_page.look(bot)
        self.assertEqual(first, {'Template:Magenta tidings': 'written', 'Template:Magenta latest': 'written'})
        self.assertEqual(again, {'Template:Magenta tidings': 'same', 'Template:Magenta latest': 'same'})
        tidings, latest = bot.pages['Template:Magenta tidings'], bot.pages['Template:Magenta latest']
        self.assertTrue(tidings.startswith('<noinclude>') and tidings.endswith('</includeonly>'))
        self.assertIn('<nowiki>Shady Grove</nowiki>', latest)
        self.assertIn("Written by magenta's wiki bot", latest)
        self.assertEqual(len(bot.edits), 2)

    def test_a_nudge_looks_once_per_look_every_and_never_without_the_bot(self):
        with mock.patch.object(todo_ticks, 'enabled', return_value=False):
            self.assertFalse(front_page.nudge())
        with mock.patch.object(todo_ticks, 'enabled', return_value=True), \
                mock.patch.object(front_page.threading, 'Thread') as thread:
            self.assertTrue(front_page.nudge())
            self.assertFalse(front_page.nudge())
        thread.assert_called_once()
