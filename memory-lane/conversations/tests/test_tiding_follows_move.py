"""A PickiPedia page shared as a tiding, then moved for a better headline (Justin, #interfaces, 13 Nov 2026): the
tiding goes where the page went, its banner with it, and takes the new headline unless someone gave it their own."""

import hashlib
import hmac
import json
import uuid
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings

from conversations.models import Message, Mood, ThinkingEntity, Tiding
from conversations.services import tiding_links, wiki_changes

OLD = 'ICE uses Ferrell/Walker hit "American Dreaming" in propaganda video, draws immediate rebuke from Ferrell'
NEW = ('ICE uses Sierra Ferrell hit "American Dreaming" in propaganda video, draws quick rebuke from Ferrell '
       'and co-writer Melody Walker')
SAID = ('https://pickipedia.xyz/wiki/ICE_uses_Ferrell/Walker_hit_%22American_Dreaming%22_in_propaganda_video,'
        '_draws_immediate_rebuke_from_Ferrell')
SECRET = 'move-secret'


@override_settings(PICKIPEDIA_URL='https://pickipedia.xyz', INTERFACE_WORDS_FROM_WIKI=False, WIKI_HOOK_SECRET=SECRET)
class TidingFollowsMoveTest(TestCase):

    def setUp(self):
        cache.clear()
        self.justin = ThinkingEntity.objects.create(name='justin', is_biological_human=True)
        self.mood = Mood.objects.create(slug='general', title='general')
        Mood.objects.create(slug='ops', title='ops')
        patch = mock.patch('conversations.services.wiki_hooks.HOOKS', [])
        patch.start()
        self.addCleanup(patch.stop)

    def tiding(self, url=SAID, title=OLD):
        said = Message.objects.create(id=uuid.uuid4(), sender=self.justin, mood=self.mood, content=f'look: {url}',
                                      source_file='mood-web', timestamp=1)
        made = Tiding.objects.create(message=said, url=url, by=self.justin, title=title)
        tiding_links._changed()
        return made

    def banner(self, title):
        return self.client.get('/api/wiki/tiding/', {'title': title}).json().get('id')

    def test_the_tiding_its_banner_and_its_headline_follow_the_page(self):
        made = self.tiding()
        self.assertEqual(self.banner(OLD), str(made.id))
        self.assertEqual(tiding_links.follow_move(OLD, NEW), 1)
        made.refresh_from_db()
        self.assertEqual((made.url, made.moved_to, made.title), (SAID, NEW, NEW))  # as said in the Mood, still
        self.assertEqual(made.address, 'https://pickipedia.xyz/wiki/ICE_uses_Sierra_Ferrell_hit_%22American_Dreaming%22'
                                       '_in_propaganda_video,_draws_quick_rebuke_from_Ferrell_and_co-writer_Melody_Walker')
        self.assertEqual(self.banner(NEW), str(made.id))
        self.assertIsNone(self.banner(OLD))
        page = tiding_links.discussion(made.id)
        self.assertEqual((page['url'], page['title']), (made.address, NEW))
        self.assertEqual(tiding_links.by_message()[str(made.message_id)][0]['url'], SAID)  # the message's ★ still knows it

    def test_a_title_of_its_own_stays_and_a_second_move_follows_the_first(self):
        made = self.tiding(title='ICE and American Dreaming')
        tiding_links.follow_move(OLD, NEW)
        tiding_links.follow_move(NEW, 'American Dreaming in an ICE video')
        made.refresh_from_db()
        self.assertEqual((made.moved_to, made.title), ('American Dreaming in an ICE video', 'ICE and American Dreaming'))
        self.assertEqual(tiding_links.follow_move(OLD, NEW), 0)  # where it was isn't where it is

    def test_another_page_and_another_site_are_left_alone(self):
        other = self.tiding(url='https://pickipedia.xyz/wiki/Tony_Rice', title='Tony Rice')
        away = self.tiding(url='https://example.com/wiki/ICE_uses_Ferrell/Walker_hit', title=OLD)
        self.assertEqual(tiding_links.follow_move(OLD, NEW), 0)
        self.assertEqual(Tiding.objects.exclude(moved_to='').count(), 0)
        self.assertEqual((other.address, away.address), (other.url, away.url))

    def test_the_push_of_a_move_follows_it(self):
        made = self.tiding()
        raw = json.dumps({'title': NEW, 'revid': 9, 'user': 'JMyles', 'kind': 'move', 'from': OLD}).encode()
        sig = 'sha256=' + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
        self.assertEqual(self.client.post('/api/wiki/hook/', raw, content_type='application/json',
                                          HTTP_X_MAGENTA_SIGNATURE=sig).status_code, 200)
        made.refresh_from_db()
        self.assertEqual(made.moved_to, NEW)

    def test_the_sweep_follows_a_move_the_push_missed(self):
        made = self.tiding()
        cache.set(wiki_changes.CURSOR, 4, None)
        rows = [{'type': 'log', 'logtype': 'move', 'ns': 0, 'title': OLD, 'rcid': 5, 'revid': 0, 'user': 'JMyles',
                 'timestamp': '2026-11-13T05:58:08Z', 'logparams': {'target_ns': 0, 'target_title': NEW}}]
        http = mock.Mock(get=mock.Mock(return_value=mock.Mock(json=lambda: {'query': {'recentchanges': rows}})))
        wiki_changes.sweep(http)
        made.refresh_from_db()
        self.assertEqual(made.moved_to, NEW)
