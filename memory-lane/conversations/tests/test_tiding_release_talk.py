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


class _Release(TestCase):

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


@override_settings(MOOD_ADMINS=('justin',), PICKIPEDIA_URL='https://pickipedia.xyz')
class ReleaseTalkTest(_Release):

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
        mock.patch('conversations.services.tidings.release_info', return_value={}).start()
        self.addCleanup(mock.patch.stopall)
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


YAML = f"""blockheight: 26319913
performers:
- Justin Myles Holmes
release_type: video
title: 'Do you use AI subtitiles?  '
description: Asked at the kitchen table.
commentary:
  - by: SkymanJenkins
    role: uploader
    text: The question that started it.
transcode:
  output_width: 1080
  output_height: 1920
upload_blockheight: 26323190
uploaded_by: wiki:JMyles
"""
SRT = """1
00:00:01,600 --> 00:00:04,000
<v Justin>Do you use AI subtitles?
"""


class FakeWiki:
    def __init__(self, pages):
        self.pages, self.asked = pages, []

    def get(self, url, params=None, **kw):
        self.asked.append(params)
        titles = params['titles'].split('|')
        found = [{'title': t, 'revisions': [{'slots': {'main': {'content': self.pages[t]}}}]} if t in self.pages
                 else {'title': t, 'missing': True} for t in titles]
        return mock.Mock(json=lambda: {'query': {'pages': found}})


@override_settings(MOOD_ADMINS=('justin',), PICKIPEDIA_URL='https://pickipedia.xyz')
class OneTidingPerReleaseTest(_Release):
    """However a release is promoted -- its tile, its feed line, its link said in any Mood -- it's one Tiding,
    with one page and one set of comments (Justin, 3 Nov)."""

    def setUp(self):
        super().setUp()
        self.elsewhere = Mood.objects.create(slug='elsewhere', title='elsewhere')
        self.said = Message.objects.create(id=uuid.uuid4(), sender=self.justin, mood=self.elsewhere,
                                           timestamp=2, content=f'look at this {PAGE}')
        mock.patch('conversations.services.tidings.release_info', return_value={}).start()
        self.addCleanup(mock.patch.stopall)

    def test_its_link_said_elsewhere_lands_on_the_same_tiding(self):
        first, new = tiding_links.promote_release(CID, self.justin)
        again, new_again = tiding_links.promote(self.said, self.justin, url=PAGE, with_news=True)
        self.assertTrue(new)
        self.assertEqual((again.id, new_again), (first.id, False))
        self.assertEqual(Tiding.objects.count(), 1)

    def test_its_link_said_first_is_what_the_tile_finds(self):
        Message.objects.filter(id=self.line.id).delete()  # older than the feed: no line at all
        said, new = tiding_links.promote(self.said, self.justin, url=PAGE, with_news=True)
        found, new_again = tiding_links.promote_release(CID, self.justin)
        self.assertEqual((found.id, new, new_again), (said.id, True, False))
        self.assertEqual(tiding_links.released(), {CID: str(said.id)})

    def test_a_cidv1_line_is_found_from_its_pinned_form(self):
        v1 = 'bafybeigdyrzt5sfp7udm7hu76uh7y26nf3efuylqabf3oclgtqy55fbzdi'
        titled = 'B' + v1[1:]  # as the wiki titles it
        line = Message.objects.create(id=uuid.uuid4(), sender=self.line.sender, mood=self.mood, source_file='release',
                                      timestamp=3, content={'type': 'release', 'cid': titled, 'page': f'Release:{titled}',
                                                            'title': 'v1', 'by': 'JMyles'})
        tiding, new = tiding_links.promote_release(v1, self.justin)
        self.assertEqual((tiding.message_id, new), (line.id, True))
        self.assertEqual(tiding_links.release_tiding(titled).id, tiding.id)
        self.assertEqual(tiding_links.released()[v1], str(tiding.id))


class ReleaseInfoTest(TestCase):

    def setUp(self):
        cache.clear()

    @override_settings(PICKIPEDIA_URL='https://pickipedia.xyz')
    def test_what_its_release_page_says(self):
        from conversations.services import tidings
        wiki = FakeWiki({f'Release:{CID}': YAML, f'Release:{CID}/captions': SRT})
        info = tidings.release_info(CID, http=wiki)
        self.assertEqual(info['title'], 'Do you use AI subtitiles?')
        self.assertEqual((info['by'], info['upload_block_s'], info['portrait']), ('JMyles', '26,323,190', True))
        self.assertEqual(info['performers'][0]['name'], 'Justin Myles Holmes')
        self.assertEqual(info['description'], 'Asked at the kitchen table.')
        self.assertEqual(info['commentary'], [{'by': 'SkymanJenkins', 'role': 'uploader', 'text': 'The question that started it.'}])
        self.assertTrue(info['captions'].startswith('WEBVTT\n\n1\n00:00:01.600 --> 00:00:04.000'))
        self.assertEqual(tidings.release_info(CID, http=FakeWiki({})), info)  # kept
        self.assertEqual(len(wiki.asked), 1)

    def test_no_wiki_no_details_but_it_plays(self):
        from conversations.services import tidings

        class Down:
            def get(self, *a, **k):
                raise OSError('down')
        self.assertEqual(tidings.release_info(CID, http=Down()), {})
        self.assertEqual(tidings.to_webvtt('no cues here'), '')
        self.assertEqual(tidings.to_webvtt('WEBVTT\n\n00:01.000 --> 00:02.000\nhi'), 'WEBVTT\n\n00:01.000 --> 00:02.000\nhi\n')


@override_settings(MOOD_ADMINS=('justin',), PICKIPEDIA_URL='https://pickipedia.xyz')
class ReleaseStageTest(_Release):

    def test_its_page_is_a_stage(self):
        from conversations.services import tidings
        info = tidings._release_info(CID, http=FakeWiki({f'Release:{CID}': YAML, f'Release:{CID}/captions': SRT}))
        client = self.client_for(self.justin)
        with mock.patch('conversations.services.tidings.release_info', return_value=info):
            tid = self.post(client, {'cid': CID}).json()['id']
            page = client.get(f'/moods/tidings/{tid}/?embed=1').content.decode()
        self.assertIn('class="stage portrait"', page)
        self.assertIn('id="captionsVtt"', page)
        self.assertIn('Justin Myles Holmes</a>', page)
        self.assertIn('Asked at the kitchen table.', page)
        self.assertIn('The question that started it.', page)
        self.assertIn('at block 26,323,190', page)
        self.assertIn('class="momentsec"', page)
        self.assertIn('captions on · <a', page)

    def test_its_row_on_tidings_shows_its_still(self):
        client = self.client_for(self.justin)
        with mock.patch('conversations.services.tidings.release_tidings', return_value=[]), \
             mock.patch('conversations.services.tidings.picture_tidings', return_value=[]):
            tid = self.post(client, {'cid': CID}).json()['id']
            listing = client.get('/moods/tidings/?embed=1').content.decode()
        self.assertIn(f'<a class="rthumb" href="/moods/tidings/{tid}/?embed=1"', listing)
        self.assertIn(f'src="https://ipfs.delivery-kid.cryptograss.live/ipfs/{CID}/poster.jpg"', listing)


@override_settings(MOOD_ADMINS=('justin',), PICKIPEDIA_URL='https://pickipedia.xyz')
class FollowAndTidyTest(_Release):
    """Justin, 3 Nov: a deleted comment nothing answered is gone, not '[deleted]'; a quality row like the
    Moods'; and the video follows you out of the page (the one page's small player)."""

    def setUp(self):
        super().setUp()
        mock.patch('conversations.services.tidings.release_info', return_value={}).start()
        self.addCleanup(mock.patch.stopall)
        self.tiding, _ = tiding_links.promote_release(CID, self.justin)

    def say(self, text, parent=None, deleted=False):
        from conversations.models import TidingComment
        return TidingComment.objects.create(tiding=self.tiding, by=self.justin, text='[deleted]' if deleted else text,
                                            parent=parent, changed='deleted' if deleted else '')

    def test_a_deleted_comment_nothing_answers_is_gone(self):
        self.say('1:00 alone, then deleted', deleted=True)
        answered = self.say('0:30 answered, then deleted', deleted=True)
        self.say('0:31 the answer', parent=answered)
        self.say('0:40 kept')
        tree = tiding_links.discussion(self.tiding.id)['comments']
        self.assertEqual([(n['changed'], [r['text'] for r in n['replies']]) for n in tree],
                         [('deleted', ['0:31 the answer']), ('', [])])
        self.assertEqual(tiding_links.listed()[0]['comments'], 2)  # the two that still say something

    def test_its_page_has_a_quality_row_and_hands_off(self):
        page = self.client_for(self.justin).get(f'/moods/tidings/{self.tiding.id}/?embed=1').content.decode()
        self.assertIn('<div class="quality" aria-label="Quality" hidden></div>', page)
        self.assertIn('id="tidingTitle"', page)
        self.assertIn('window.magentaHandoff', page)
        self.assertIn('mini.take(tidingId)', page)

    def test_the_one_page_has_the_small_player(self):
        from django.template.loader import get_template
        source = get_template('conversations/moods.html').template.source
        self.assertIn('window.magentaMini = {', source)
        self.assertIn('w.magentaHandoff()', source)  # leaving Tidings hands it over
        self.assertIn("if (name === 'tidings' && left !== 'tidings') miniHome();", source)
