"""Releases on Tidings, among the pictures (Justin, 2 Nov 2026): what the wiki's page-creation log and the
Release pages' YAML say, newest first; a track, a deleted or an unpinned release left out; a wiki that doesn't
answer is no releases, never a broken page; and the page shows the two as one stream."""

from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.utils import timezone

from conversations.services import tidings
from conversations.tests.tidings_helpers import TidingsTests


class FakeAnswer:
    def __init__(self, body):
        self.body = body

    def json(self):
        return self.body


class FakeWiki:
    """PickiPedia's api.php: the creation log, then the pages' YAML."""

    def __init__(self, made, pages):
        self.made, self.pages, self.asked = made, pages, []

    def get(self, url, params=None, **kw):
        self.asked.append(params)
        if params.get('list') == 'logevents':
            return FakeAnswer({'query': {'logevents': self.made}})
        titles = params['titles'].split('|')
        return FakeAnswer({'query': {'pages': [
            {'title': t, 'revisions': [{'slots': {'main': {'content': self.pages[t]}}}]} for t in titles if t in self.pages]}})


VIDEO = 'title: Cory Walkers Birthday Banjo Cake\nrelease_type: video\nfile_type: video/mov\nuploaded_by: wiki:SkymanJenkins\n'
RECORD = 'title: Some Record\nrelease_type: record\nuploaded_by: wiki:JMyles\n'
TRACK = 'title: A Track Of It\nrelease_type: track\n'
GONE = 'title: Taken Down\nrelease_type: video\ndelete: true\n'


def wiki():
    return FakeWiki(
        made=[{'title': 'Release:QmTrack', 'timestamp': '2026-11-02T04:00:00Z', 'user': 'Blue Railroad Imports'},
              {'title': 'Release:QmVideo', 'timestamp': '2026-11-02T03:00:00Z', 'user': 'Blue Railroad Imports'},
              {'title': 'Release:QmGone', 'timestamp': '2026-11-01T03:00:00Z', 'user': 'Blue Railroad Imports'},
              {'title': 'Release:QmVideo/diagnostics', 'timestamp': '2026-11-01T02:00:00Z', 'user': 'x'},
              {'title': 'Release:Bafybeigdyrzt', 'timestamp': '2026-10-30T03:00:00Z', 'user': 'Blue Railroad Imports'}],
        pages={'Release:QmTrack': TRACK, 'Release:QmVideo': VIDEO, 'Release:QmGone': GONE,
               'Release:Bafybeigdyrzt': RECORD})


class ReleaseScanTests(TidingsTests):

    def test_newest_first_with_who_and_poster_and_no_tracks_or_deleted(self):
        found = tidings.release_scan(http=wiki())
        self.assertEqual([r['title'] for r in found], ['Cory Walkers Birthday Banjo Cake', 'Some Record'])
        video, record = found
        self.assertEqual(video['by'], 'SkymanJenkins')
        self.assertTrue(video['video'])
        self.assertTrue(video['poster'].endswith('/ipfs/QmVideo/poster.jpg'))
        self.assertTrue(video['page'].endswith('/wiki/Release:QmVideo'))
        self.assertEqual(video['at'].isoformat(), '2026-11-02T03:00:00+00:00')
        self.assertEqual(record['cid'], 'bafybeigdyrzt')  # base32: lowercase again, as the gateway wants it
        self.assertEqual(record['poster'], '')  # a record has no poster: a plain tile
        self.assertEqual(record['kind'], 'record')

    def test_the_yaml_is_asked_for_once_for_them_all(self):
        fake = wiki()
        tidings.release_scan(http=fake)
        self.assertEqual(len(fake.asked), 2)

    def test_a_wiki_that_fails_is_no_releases_and_is_not_asked_again_at_once(self):
        cache.delete('tidings:releases')
        broken = mock.Mock()
        broken.get.side_effect = TimeoutError('slow wiki')
        self.assertEqual(tidings.release_tidings(http=broken), [])
        self.assertEqual(tidings.release_tidings(http=broken), [])
        self.assertEqual(broken.get.call_count, 1)

    def test_kept_until_a_new_release_clears_it(self):
        cache.delete('tidings:releases')
        fake = wiki()
        tidings.release_tidings(http=fake)
        tidings.release_tidings(http=fake)
        self.assertEqual(len(fake.asked), 2)  # one scan
        tidings.releases_changed()
        tidings.release_tidings(http=fake)
        self.assertEqual(len(fake.asked), 4)


class ReleaseStreamTests(TidingsTests):

    def release(self, title, at, poster='https://gw/ipfs/QmX/poster.jpg'):
        return {'cid': 'QmX', 'title': title, 'kind': 'video', 'video': bool(poster), 'by': 'SkymanJenkins', 'at': at,
                'page': 'https://pickipedia.xyz/wiki/Release:QmX', 'poster': poster}

    def test_pictures_and_releases_interleave_by_time(self):
        now = timezone.now()
        pictures = [{'sha256': 'b', 'at': now - timedelta(hours=1)}, {'sha256': 'd', 'at': now - timedelta(hours=3)}]
        releases = [self.release('a', now), self.release('c', now - timedelta(hours=2)), self.release('e', None)]
        order = [e['item'].get('sha256') or e['item']['title'] for e in tidings.stream(pictures, releases)]
        self.assertEqual(order, ['a', 'b', 'c', 'd', 'e'])  # one with no time goes last

    def test_the_page_shows_a_release_tile_among_the_pictures(self):
        self.image(1, added_by=self.justin)
        cache.set('tidings:releases', [self.release('Upright and Down Low', timezone.now() + timedelta(minutes=5))], 600)
        html = self.client.get('/moods/tidings/?embed=1').content.decode()
        self.assertIn('Pictures and releases', html)
        self.assertIn('class="pic release"', html)
        self.assertIn('Upright and Down Low', html)
        self.assertIn('href="https://pickipedia.xyz/wiki/Release:QmX" target="_top"', html)
        self.assertIn('src="https://gw/ipfs/QmX/poster.jpg"', html)
        self.assertLess(html.index('Upright and Down Low'), html.index('data-sha='))  # newer: first

    def test_api_tidings_carries_the_releases(self):
        cache.set('tidings:releases', [self.release('Upright and Down Low', timezone.now())], 600)
        body = self.client.get('/api/tidings/').json()
        self.assertEqual([r['title'] for r in body['releases']], ['Upright and Down Low'])
