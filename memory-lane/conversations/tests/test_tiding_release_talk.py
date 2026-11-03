"""A release on Tidings, talked about by the moment (Justin, 3 Nov: "timecoded comments first ... get that
new video posted as a tiding"): its line in a feed Mood is what's promoted; its page plays it."""

import json
import uuid
from unittest import mock

from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from conversations.models import ConversationParticipant, Message, Mood, ThinkingEntity, Tiding
from conversations.services import links, mood_auth, tiding_links

CID = 'QmbYnW2BpVxwvJZDE3e4iaJcH3xn1QDugRZuofuv1JDkJg'
PAGE = f'https://pickipedia.xyz/wiki/Release:{CID}'


@override_settings(MOOD_ADMINS=('justin',), PICKIPEDIA_URL='https://pickipedia.xyz')
class ReleaseTalkTest(TestCase):

    def setUp(self):
        cache.clear()
        links._memo.update(at=0.0, index=None)
        self.justin = ThinkingEntity.objects.create(name='justin', is_biological_human=True)
        self.mood = Mood.objects.create(slug='general', title='general')
        system, _ = ConversationParticipant.objects.get_or_create(name='system', defaults={'participant_type': 'system'})
        self.line = Message.objects.create(
            id=uuid.uuid4(), sender=system, mood=self.mood, source_file='release', timestamp=1,
            content={'type': 'release', 'rcid': 9, 'cid': CID, 'page': f'Release:{CID}',
                     'title': 'Do you use AI subtitiles?', 'by': 'JMyles', 'kind': 'video', 'at': ''})

    def client_for(self, who):
        _, token = mood_auth.enrol_device(who, 'laptop', tier='key')
        client = Client()
        client.cookies[mood_auth.COOKIE] = token
        return client

    def post(self, client, body):
        return client.post('/api/tidings/release/', json.dumps(body), content_type='application/json')

    def test_a_release_line_says_its_title_and_page(self):
        self.assertEqual(tiding_links.said_of(self.line), f'Do you use AI subtitiles? {PAGE}')
        self.assertEqual(tiding_links.links_in(tiding_links.said_of(self.line)), [PAGE])

    def test_a_release_page_plays_as_a_release(self):
        embed = tiding_links.embed_of(PAGE)
        self.assertEqual(embed['kind'], 'release')
        self.assertEqual(embed['hls'], f'https://ipfs.delivery-kid.cryptograss.live/ipfs/{CID}/master.m3u8')
        self.assertEqual(tiding_links.embed_of('https://pickipedia.xyz/wiki/Release:Bafybeigdyrzt5sfp7udm7hu76uh7y26nf3efuylqabf3oclgtqy55fbzdi')['cid'],
                         'bafybeigdyrzt5sfp7udm7hu76uh7y26nf3efuylqabf3oclgtqy55fbzdi')
        self.assertIsNone(tiding_links.release_cid('https://pickipedia.xyz/wiki/Some_Page'))
        self.assertIsNone(tiding_links.embed_of('https://pickipedia.xyz/wiki/Release:<script>'))

    def test_promoting_a_release_by_its_cid_once_signed_in(self):
        self.assertEqual(self.post(Client(), {'cid': CID}).status_code, 401)
        client = self.client_for(self.justin)
        first = self.post(client, {'cid': CID})
        self.assertEqual(first.status_code, 201)
        tiding = Tiding.objects.get()
        self.assertEqual((tiding.message_id, tiding.url, tiding.title), (self.line.id, PAGE, 'Do you use AI subtitiles?'))
        self.assertEqual(self.post(client, {'cid': CID}).status_code, 200)  # once
        self.assertEqual(Tiding.objects.count(), 1)
        self.assertEqual(self.post(client, {'cid': 'QmNotAReleaseWeHaveALineFor000000000000000000'}).status_code, 404)
        self.assertEqual(self.post(client, {'cid': '../etc'}).status_code, 400)
        self.assertEqual(tiding_links.released(), {CID: str(tiding.id)})
        listed = tiding_links.listed()
        self.assertEqual((len(listed), listed[0]['release']), (1, CID))

    def test_its_page_plays_it_and_talks_by_the_moment(self):
        client = self.client_for(self.justin)
        tid = self.post(client, {'cid': CID}).json()['id']
        said = client.post(f'/api/tidings/{tid}/comments/', json.dumps({'text': '0:12 the subtitles question lands'}),
                           content_type='application/json')
        self.assertIn(said.status_code, (200, 201))
        page = client.get(f'/moods/tidings/{tid}/?embed=1').content.decode()
        self.assertIn(f'data-hls="https://ipfs.delivery-kid.cryptograss.live/ipfs/{CID}/master.m3u8"', page)
        self.assertIn('class="moments"', page)
        self.assertIn('class="stamp"', page)
        self.assertIn('0:12 the subtitles question lands', page)
        self.assertNotIn('<div class="post">', page)  # a release line has no words besides its link
        self.assertIn('uploaded by JMyles · promoted by justin', page)  # its uploader's, not the system's

    def test_tidings_tiles_offer_it_then_point_at_its_talk(self):
        release = {'cid': CID, 'title': 'Do you use AI subtitiles?', 'kind': 'video', 'video': True, 'by': 'JMyles',
                   'at': '2026-11-02T03:08:23Z', 'page': PAGE, 'poster': ''}
        client = self.client_for(self.justin)
        with mock.patch('conversations.services.tidings.release_tidings', return_value=[release]), \
             mock.patch('conversations.services.tidings.picture_tidings', return_value=[]):
            before = client.get('/moods/tidings/?embed=1').content.decode()
            self.assertIn(f'data-cid="{CID}"', before)
            tid = self.post(client, {'cid': CID}).json()['id']
            after = client.get('/moods/tidings/?embed=1').content.decode()
        self.assertNotIn(f'class="chip torel" data-cid="{CID}"', after)
        self.assertIn(f'/moods/tidings/{tid}/?embed=1" title="Its page on Tidings', after)
