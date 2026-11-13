"""A PickiPedia page that is a tiding says so atop itself (Justin, #interfaces, 13 Nov 2026): the wiki asks
api/wiki/tiding/ with its title; the words are Magenta:Interface's wiki-tiding-banner."""

import uuid

from django.core.cache import cache
from django.test import TestCase, override_settings

from conversations.models import Message, Mood, ThinkingEntity, Tiding, TidingComment, TidingVote
from conversations.services import tiding_links


@override_settings(PICKIPEDIA_URL='https://pickipedia.xyz', INTERFACE_WORDS_FROM_WIKI=False)
class WikiTidingBannerTest(TestCase):

    def setUp(self):
        cache.clear()
        self.justin = ThinkingEntity.objects.create(name='justin', is_biological_human=True)
        self.mood = Mood.objects.create(slug='general', title='general')

    def tiding(self, url):
        said = Message.objects.create(id=uuid.uuid4(), sender=self.justin, mood=self.mood, content=url,
                                      source_file='mood-web', timestamp=1)
        made = Tiding.objects.create(message=said, url=url, by=self.justin)
        tiding_links._changed()
        return made

    def ask(self, title, **more):
        return self.client.get('/api/wiki/tiding/', {'title': title, **more})

    def test_a_page_on_tidings_says_where_with_the_words(self):
        made = self.tiding("https://pickipedia.xyz/wiki/Kuba%27s_Last_Merged_Commits:_the_story_of_arthel159#Top")
        got = self.ask("Kuba's Last Merged Commits: the story of arthel159")
        self.assertEqual(got['Access-Control-Allow-Origin'], '*')
        self.assertEqual(got.json(), {
            'tiding': f'https://magenta.cryptograss.live/tidings/{made.id}/', 'id': str(made.id),
            'words': 'This article has been shared as a tiding on magenta.  See the discussion there as well.'})

    def test_titles_as_the_wiki_has_them(self):
        made = self.tiding('https://www.pickipedia.xyz/wiki/tony_Rice')
        self.assertEqual(self.ask('Tony Rice').json()['id'], str(made.id))
        release = self.tiding('https://pickipedia.xyz/wiki/Release:bafybeiavz2kvaf4rfv7q46hxse4fk2tcqpzm4mgr67jbdqa2wpdarcie5y')
        self.assertEqual(self.ask('Release:Bafybeiavz2kvaf4rfv7q46hxse4fk2tcqpzm4mgr67jbdqa2wpdarcie5y').json()['id'],
                         str(release.id))

    def test_anything_else_is_no_tiding(self):
        self.tiding('https://example.com/wiki/Tony_Rice')
        self.tiding('https://pickipedia.xyz/index.php?title=Tony_Rice&action=edit')
        self.assertEqual(self.ask('Tony Rice').json(), {'tiding': None})
        self.assertEqual(self.ask('').json(), {'tiding': None})

    def test_withdrawn_it_says_nothing(self):
        made = self.tiding('https://pickipedia.xyz/wiki/Tony_Rice')
        self.assertIsNotNone(self.ask('Tony Rice').json()['tiding'])
        made.delete()
        tiding_links._changed()
        self.assertEqual(self.ask('Tony Rice').json(), {'tiding': None})


    def test_asked_what_was_said_it_says_the_last_of_it(self):
        """A post's page on the wiki shows the end of its thread (Justin, 13 Nov, #pickipedia-style-and-main-page)."""
        made = self.tiding('https://pickipedia.xyz/wiki/User:JMyles/My_Long_Forgotten_Dream')
        Tiding.objects.filter(id=made.id).update(title='My Long Forgotten Dream')
        magent = ThinkingEntity.objects.create(name='magent', is_biological_human=False)
        TidingComment.objects.create(tiding=made, by=self.justin, text='The first thing said.')
        gone = TidingComment.objects.create(tiding=made, by=self.justin, text='[deleted]', changed='deleted')
        TidingComment.objects.create(tiding=made, by=magent, text='An answer.\n\nIn two paragraphs.')
        last = TidingComment.objects.create(tiding=made, by=self.justin, text='The last word.')
        TidingVote.objects.create(tiding=made, by=self.justin)
        got = self.ask('User:JMyles/My Long Forgotten Dream', recent='2').json()
        self.assertEqual(got['tiding'], f'https://magenta.cryptograss.live/tidings/{made.id}/')
        self.assertEqual((got['title'], got['score'], got['comments']), ('My Long Forgotten Dream', 1, 3))
        self.assertEqual([(r['who'], r['kind'], r['colour']) for r in got['recent']],
                         [('magent', 'emergent', '#ff67fd'), ('justin', 'person', '')])
        self.assertEqual(got['recent'][0]['text'], 'An answer.\n\nIn two paragraphs.')
        self.assertEqual(got['recent'][1]['href'], f'https://magenta.cryptograss.live/tidings/{made.id}/#c-{last.id}')
        self.assertRegex(got['recent'][1]['at'], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$')
        self.assertNotIn(str(gone.id), str(got))

    def test_not_asked_it_says_no_more_than_before(self):
        made = self.tiding('https://pickipedia.xyz/wiki/Tony_Rice')
        TidingComment.objects.create(tiding=made, by=self.justin, text='Said.')
        for more in ({}, {'recent': '0'}, {'recent': 'lots'}, {'recent': '-3'}):
            self.assertEqual(sorted(self.ask('Tony Rice', **more).json()), ['id', 'tiding', 'words'], more)
        self.assertEqual(len(self.ask('Tony Rice', recent='99999').json()['recent']), 1)
        self.assertEqual(self.ask('Nobody', recent='5').json(), {'tiding': None})

    def test_words_are_sent_plain(self):
        self.assertEqual(tiding_links.plain_words('<p>One <b>bold</b> &amp; <a href="/x">linked</a>.</p>\n<ul><li>a</li><li>b</li></ul><p>Two<br>lines</p>'),
                         'One bold & linked.\n\na\n\nb\n\nTwo\nlines')
        self.assertEqual(tiding_links.plain_words(''), '')
