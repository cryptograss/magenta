"""A person's page (Justin, 5 Nov): their last posts in the Moods and their last edits on PickiPedia as one
stream, pictures shown, sounds and Releases playing (services/person_feed.py, /people/<name>/)."""

import uuid
from datetime import datetime, timedelta, timezone
from unittest import mock

from django.core.cache import cache
from django.test import Client, TestCase

from conversations.models import Message, Mood, ThinkingEntity
from conversations.services import person_feed

T0 = datetime(2026, 11, 5, 20, 0, tzinfo=timezone.utc)


def at(minutes):
    return T0 + timedelta(minutes=minutes)


def wiki_at(minutes):
    return at(minutes).strftime('%Y-%m-%dT%H:%M:%SZ')


class FakeWiki:
    """PickiPedia's api.php: a person's contribs, the files among them, the Release log and pages."""

    def __init__(self, contribs=(), files=None, releases=()):
        self.contribs, self.files, self.releases = list(contribs), files or {}, list(releases)
        self.asked = []

    def get(self, url, params=None, **kw):
        self.asked.append(params)
        if params.get('list') == 'usercontribs':
            body = {'query': {'usercontribs': self.contribs}}
        elif params.get('prop') == 'imageinfo':
            body = {'query': {'pages': [{'title': t, 'imageinfo': [self.files[t]]} for t in params['titles'].split('|') if t in self.files]}}
        elif params.get('list') == 'logevents':
            body = {'query': {'logevents': [{'title': r['title'], 'timestamp': r['at']} for r in self.releases]}}
        elif params.get('prop') == 'revisions' and params.get('titles', '').startswith('Release:'):
            body = {'query': {'pages': [{'title': r['title'], 'revisions': [{'slots': {'main': {'content': r['yaml']}}}]}
                                        for r in self.releases]}}
        else:
            body = {'query': {'pages': [{'missing': True}]}}
        return mock.Mock(json=lambda: body)


class PersonFeedTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.justin = ThinkingEntity.objects.create(name='justin', is_biological_human=True)
        ThinkingEntity.objects.create(name='magent', is_biological_human=False)
        cls.jams = Mood.objects.create(slug='jams', title='jams')

    def setUp(self):
        cache.clear()

    def said(self, text, minutes):
        m = Message.objects.create(id=uuid.uuid4(), sender=self.justin, mood=self.jams, content=text)
        Message.objects.filter(id=m.id).update(created_at=at(minutes))
        return m

    def test_posts_and_edits_are_one_stream_newest_first(self):
        self.said('first', 0)
        self.said('third', 20)
        wiki = FakeWiki(contribs=[{'ns': 0, 'title': 'Shady Grove', 'timestamp': wiki_at(30), 'comment': 'verses', 'sizediff': 40},
                                  {'ns': 0, 'title': 'Cryptograss:Eras', 'timestamp': wiki_at(10), 'new': True, 'sizediff': 900}])
        with mock.patch.object(person_feed.embeds, 'release', return_value=None):
            feed = person_feed.feed('justin', http=wiki)
        self.assertEqual([(i['kind'], i.get('text') or i.get('title')) for i in feed['items']],
                         [('edit', 'Shady Grove'), ('message', 'third'), ('edit', 'Cryptograss:Eras'), ('message', 'first')])
        made = feed['items'][2]
        self.assertTrue(made['made'])
        self.assertEqual(made['url'], 'https://pickipedia.xyz/wiki/Cryptograss:Eras')
        self.assertEqual(feed['items'][0]['comment'], 'verses')

    def test_a_message_is_cut_to_240_characters(self):
        self.said('word ' * 100, 0)
        with mock.patch.object(person_feed.embeds, 'release', return_value=None):
            [item] = person_feed.feed('justin', http=FakeWiki())['items']
        self.assertEqual(len(item['text']), 240)
        self.assertTrue(item['text'].endswith('…'))

    def test_a_picture_they_uploaded_is_shown_and_a_sound_plays(self):
        wiki = FakeWiki(
            contribs=[{'ns': 6, 'title': 'File:Dragonmobile.jpg', 'timestamp': wiki_at(5), 'new': True},
                      {'ns': 6, 'title': 'File:August-notification.flac', 'timestamp': wiki_at(3), 'new': True}],
            files={'File:Dragonmobile.jpg': {'url': 'https://pickipedia.xyz/images/d/d.jpg', 'mime': 'image/jpeg',
                                             'thumburl': 'https://pickipedia.xyz/images/thumb/d.jpg/640px-d.jpg'},
                   'File:August-notification.flac': {'url': 'https://pickipedia.xyz/images/a/a.flac', 'mime': 'audio/flac'}})
        with mock.patch.object(person_feed.embeds, 'release', return_value=None):
            picture, sound = person_feed.feed('justin', http=wiki)['items']
        self.assertEqual((picture['kind'], picture['media']['kind'], picture['media']['src']),
                         ('file', 'image', 'https://pickipedia.xyz/images/thumb/d.jpg/640px-d.jpg'))
        self.assertEqual((sound['media']['kind'], sound['media']['full'], sound['name']),
                         ('audio', 'https://pickipedia.xyz/images/a/a.flac', 'August-notification.flac'))
        # Every file in the stream was asked about in one ask.
        [ask] = [p for p in wiki.asked if p.get('prop') == 'imageinfo']
        self.assertEqual(ask['titles'], 'File:Dragonmobile.jpg|File:August-notification.flac')

    def test_a_release_they_uploaded_plays_though_a_bot_made_its_page(self):
        cid = 'QmbYnW2BpVxwvJZDE3e4iaJcH3xn1QDugRZuofuv1JDkJg'
        wiki = FakeWiki(releases=[
            {'title': f'Release:{cid}', 'at': wiki_at(7),
             'yaml': f'title: Blue Railroad Train\nrelease_type: video\nuploaded_by: wiki:JMyles\n'},
            {'title': 'Release:QmOther', 'at': wiki_at(6), 'yaml': 'title: Not mine\nrelease_type: video\nuploaded_by: wiki:Skyler\n'},
        ])
        player = {'cid': cid, 'kind': 'video', 'src': f'https://g/ipfs/{cid}', 'hls': f'https://g/ipfs/{cid}/master.m3u8',
                  'poster': '', 'title': 'Blue Railroad Train', 'page': ''}
        with mock.patch.object(person_feed, 'wiki_name', return_value='JMyles'), \
                mock.patch.object(person_feed.embeds, 'release', return_value=player):
            [release] = person_feed.feed('justin', http=wiki)['items']
        self.assertEqual((release['kind'], release['title'], release['player']['hls']),
                         ('release', 'Blue Railroad Train', f'https://g/ipfs/{cid}/master.m3u8'))
        self.assertEqual(release['poster'], f'{person_feed.tidings.RELEASE_GATEWAY}/{cid}/poster.jpg')

    def test_a_release_edited_and_uploaded_is_shown_once(self):
        cid = 'QmbYnW2BpVxwvJZDE3e4iaJcH3xn1QDugRZuofuv1JDkJg'
        wiki = FakeWiki(
            contribs=[{'ns': 3004, 'title': f'Release:{cid}', 'timestamp': wiki_at(9), 'comment': 'a better title'},
                      {'ns': 3004, 'title': f'Release:{cid}/Metadata', 'timestamp': wiki_at(8)}],
            releases=[{'title': f'Release:{cid}', 'at': wiki_at(7), 'yaml': 'title: Blue Railroad Train\nrelease_type: video\nuploaded_by: wiki:JMyles\n'}])
        with mock.patch.object(person_feed, 'wiki_name', return_value='JMyles'), \
                mock.patch.object(person_feed.embeds, 'release', return_value=None):
            items = person_feed.feed('justin', http=wiki)['items']
        self.assertEqual([(i['kind'], i['title']) for i in items],
                         [('edit', f'Release:{cid}/Metadata'), ('release', 'Blue Railroad Train')])

    def test_a_wiki_that_does_not_answer_is_an_empty_list_not_a_broken_page(self):
        self.said('still here', 0)

        class Down:
            def get(self, *a, **kw):
                raise OSError('no route')
        with mock.patch.object(person_feed.embeds, 'release', return_value=None):
            feed = person_feed.feed('justin', http=Down())
        self.assertEqual([i['kind'] for i in feed['items']], ['message'])

    def test_only_people_have_pages(self):
        self.assertIsNone(person_feed.feed('magent', http=FakeWiki()))
        self.assertEqual(Client().get('/people/magent/').status_code, 404)
        self.assertEqual(Client().get('/api/people/magent/feed/').status_code, 404)

    def test_the_page_draws_the_stream(self):
        self.said('Shady Grove, my little love', 0)
        with mock.patch.object(person_feed, '_wiki', side_effect=OSError), \
                mock.patch('conversations.services.wiki_preview.preview', return_value=None), \
                mock.patch.object(person_feed.tidings, 'release_scan', return_value=[]), \
                mock.patch.object(person_feed.embeds, 'release', return_value=None):
            page = Client().get('/people/justin/')
            api = Client().get('/api/people/justin/feed/').json()
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Shady Grove, my little love')
        self.assertContains(page, '/moods/jams/#m-')
        self.assertEqual([i['kind'] for i in api['items']], ['message'])
        self.assertEqual(api['items'][0]['at'], at(0).isoformat())
