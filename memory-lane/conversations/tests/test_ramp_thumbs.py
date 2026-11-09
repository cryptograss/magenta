"""The 👍 on the ramp (Plan, section 8): any signed-in person's "I like this" of a commit; a signal, never a
merge, and shown as on an older commit once the branch moves."""

import json
import tempfile
from pathlib import Path
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings

from conversations.models import Device, ThinkingEntity, Thumb
from conversations.services import mood_auth, ramp, thumbs
from conversations.tests.test_githouse import Repo, git
from conversations.tests.test_ramp import card


class ThumbTest(TestCase):

    def setUp(self):
        cache.clear()
        self.mount = Path(tempfile.mkdtemp())
        patch = override_settings(GITHOUSE_DIR=str(self.mount))
        patch.enable()
        self.addCleanup(patch.disable)
        self.repo = Repo(self.mount)
        git(self.repo.work, 'checkout', '-q', '-b', 're/foo')
        self.foo = self.repo.commit('the foo work')
        self.repo.ref('refs/heads/re/foo', self.foo)
        self.repo.ref('refs/pull/7/head', self.foo)
        git(self.repo.work, 'checkout', '-q', 'main')
        self.cards = {'open': [card('magenta', 7, 're/foo')], 'merged': []}
        for p in (mock.patch.object(ramp, 'cards', lambda: self.cards),
                  mock.patch.object(ramp, 'BUILD_IN_BACKGROUND', False),
                  mock.patch.object(ramp, 'test_runs', lambda: {})):
            p.start()
            self.addCleanup(p.stop)
        for name, human, tier in (('justin', True, 'key'), ('skyler', True, 'wiki'), ('re', False, 'key')):
            who, _ = ThinkingEntity.objects.get_or_create(name=name, defaults={'is_biological_human': human})
            Device.objects.create(entity=who, label='phone', tier=tier, token_hash=mood_auth.digest(name))

    def as_(self, name):
        self.client.cookies[mood_auth.COOKIE] = name

    def press(self, **change):
        body = {'repo': 'magenta', 'branch': 're/foo', 'commit': self.foo, 'number': 7, **change}
        return self.client.post('/api/ramp/thumb/', json.dumps(body), content_type='application/json')

    def test_a_press_likes_the_head_and_a_second_takes_it_back(self):
        self.as_('justin')
        got = self.press().json()
        self.assertEqual((got['on'], got['liked']), (True, ['justin']))
        self.assertEqual(Thumb.objects.get().commit, self.foo)
        got = self.press().json()
        self.assertEqual((got['on'], got['liked']), (False, []))
        self.assertFalse(Thumb.objects.exists())

    def test_any_signed_in_person_at_either_tier_but_not_an_emergent_or_nobody(self):
        self.as_('skyler')
        self.assertEqual(self.press().status_code, 200)
        self.as_('re')
        self.assertEqual(self.press().status_code, 403)
        self.client.cookies.clear()
        self.assertEqual(self.press().status_code, 401)
        self.assertEqual(list(Thumb.objects.values_list('by', flat=True)), ['skyler'])

    def test_only_the_head_githouse_has_now(self):
        self.as_('justin')
        moved = self.press(commit='0' * 40)
        self.assertEqual(moved.status_code, 409)
        self.assertEqual(moved.json()['head'], self.foo)
        self.assertEqual(self.press(branch='re/nothing', number=None).status_code, 400)
        self.assertEqual(self.press(repo='nowhere').status_code, 400)
        self.assertEqual(self.press(commit=None, branch=None).status_code, 400)
        self.assertEqual(self.press(branch='re/other').status_code, 400)   # #7's head, under another name: no
        self.assertFalse(Thumb.objects.exists())

    def test_a_branch_only_github_has_goes_by_its_pull_request(self):
        git(self.repo.bare, 'update-ref', '-d', 'refs/heads/re/foo')
        self.as_('justin')
        self.assertEqual(self.press().json()['liked'], ['justin'])

    def test_once_the_branch_moves_a_thumb_is_on_an_older_commit(self):
        self.as_('justin')
        self.press()
        git(self.repo.work, 'checkout', '-q', 're/foo')
        newer = self.repo.commit('more foo')
        self.repo.ref('refs/heads/re/foo', newer)
        self.repo.ref('refs/pull/7/head', newer)
        self.as_('skyler')
        self.press(commit=newer)
        found = thumbs.of('magenta', 're/foo', newer)
        self.assertEqual([(t['by'], t['current']) for t in found], [('justin', False), ('skyler', True)])
        # A person who likes the newer commit too shows once, on it.
        self.as_('justin')
        got = self.press(commit=newer).json()
        self.assertEqual(got['liked'], ['skyler', 'justin'])
        self.assertEqual(len(got['thumbs']), 2)

    def test_the_ramp_shows_them(self):
        Thumb.objects.create(repo='magenta', branch='re/foo', commit=self.foo, by='skyler')
        self.as_('justin')
        page = self.client.get('/moods/pulls/', {'embed': 1}, HTTP_SEC_FETCH_DEST='iframe').content.decode()
        self.assertIn('class="thumb" aria-pressed="false"', page)
        self.assertIn('👍 1', page)
        self.assertIn('>skyler</span>', page)
        self.client.cookies.clear()
        page = self.client.get('/moods/pulls/', {'embed': 1}, HTTP_SEC_FETCH_DEST='iframe').content.decode()
        self.assertNotIn('class="thumb"', page)   # signed out: shown, not pressable
        self.assertIn('👍 1', page)

    def test_a_thumb_merges_nothing(self):
        from conversations.models import MergeOrder
        self.as_('justin')
        self.press()
        self.assertFalse(MergeOrder.objects.exists())
