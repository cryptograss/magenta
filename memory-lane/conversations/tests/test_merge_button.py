"""The merge button (Plan, section 10, 8 Nov): a press on a signed-in device, previewed from githouse's copy,
signed with memory-lane's own key for whoever pressed, and left in the mailbox for githouse."""

import base64
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from unittest import skipUnless

from django.core.cache import cache
from django.test import TestCase, override_settings

from conversations.models import Device, MergeOrder, Setting, ThinkingEntity
from conversations.services import merge_orders, mood_auth
from conversations.tests.test_mood_auth import HAS_SSH_KEYGEN

ENV = {**os.environ, 'GIT_AUTHOR_NAME': 're', 'GIT_AUTHOR_EMAIL': 're@x', 'GIT_COMMITTER_NAME': 're',
       'GIT_COMMITTER_EMAIL': 're@x', 'GIT_CONFIG_GLOBAL': '/dev/null'}


def git(*args, cwd=None):
    return subprocess.run(['git', *args], cwd=cwd, env=ENV, capture_output=True, text=True, check=True).stdout.strip()


@skipUnless(HAS_SSH_KEYGEN, 'needs ssh-keygen')
class MergeButtonTest(TestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.dir = Path(tempfile.mkdtemp())
        # githouse's copy of magenta: main, and re/foo one commit ahead of it.
        cls.githouse = cls.dir / 'srv'
        bare = cls.githouse / 'magenta.git'
        git('init', '--quiet', '--bare', '-b', 'main', str(bare))
        work = cls.dir / 'work'
        git('clone', '--quiet', str(bare), str(work))
        (work / 'a.txt').write_text('one\n')
        git('add', '.', cwd=work)
        git('commit', '--quiet', '-m', 'one', cwd=work)
        git('push', '--quiet', 'origin', 'HEAD:main', cwd=work)
        (work / 'fix.py').write_text('print("fix")\n')
        git('add', '.', cwd=work)
        git('commit', '--quiet', '-m', 'the fix', cwd=work)
        git('push', '--quiet', 'origin', 'HEAD:re/foo', cwd=work)
        cls.head = git('rev-parse', 'HEAD', cwd=work)
        cls.key = cls.dir / 'memory-lane-merge'
        subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-C', 'memory-lane-merge', '-f', str(cls.key)],
                       check=True)
        cls.key.chmod(0o400)

    def setUp(self):
        cache.clear()
        (self.githouse / '.merges').mkdir(exist_ok=True)
        for patch in (override_settings(GITHOUSE_DIR=str(self.githouse)),
                      override_settings(GITHOUSE_MERGERS=('justin', 'skyler')),
                      override_settings(MERGE_KEY_PATH=str(self.key))):
            patch.enable()
            self.addCleanup(patch.disable)
        for name in ('justin', 'skyler', 'rj'):
            person = ThinkingEntity.objects.create(name=name, is_biological_human=True)
            Device.objects.create(entity=person, label='phone', token_hash=mood_auth.digest(name))
        Device.objects.filter(entity_id='rj').update(tier='wiki')

    def as_(self, name):
        self.client.cookies[mood_auth.COOKIE] = name

    def preview(self, **q):
        return self.client.get('/api/merge-orders/preview/', {'repo': 'magenta', 'branch': 're/foo', **q})

    def press(self, **change):
        body = {'repo': 'magenta', 'branch': 're/foo', 'commit': self.head, 'into': 'main', 'mood': 'ramp', **change}
        return self.client.post('/api/merge-orders/press/', json.dumps(body), content_type='application/json')

    def test_the_sheet_shows_githouse_s_copy(self):
        self.as_('justin')
        seen = self.preview().json()
        self.assertEqual((seen['commit'], seen['into'], seen['can']), (self.head, 'main', True))
        self.assertIn('re: the fix', seen['log'])
        self.assertIn('fix.py | 1 +', seen['stat'])

    def test_a_press_is_an_order_signed_by_memory_lane_for_the_presser(self):
        self.as_('justin')
        said = self.press()
        self.assertEqual(said.status_code, 201, said.content)
        kept = MergeOrder.objects.get()
        self.assertEqual((kept.by, kept.pressed, kept.mood), ('memory-lane', 'justin', 'ramp'))
        fields = dict(line.split(': ', 1) for line in kept.order.splitlines()[1:])
        self.assertEqual((fields['for'], fields['by'], fields['commit'], fields['into']),
                         ('justin', 'memory-lane', self.head, 'main'))
        # As githouse will check it: memory-lane's public key, in magenta-merge.
        signers = self.dir / 'signers'
        signers.write_text(f'memory-lane namespaces="magenta-merge" {Path(str(self.key) + ".pub").read_text().strip()}\n')
        sig = self.dir / 'press.sig'
        sig.write_text(kept.signature)
        checked = subprocess.run(['ssh-keygen', '-Y', 'verify', '-f', str(signers), '-I', 'memory-lane', '-n',
                                  'magenta-merge', '-s', str(sig)], input=kept.order, capture_output=True, text=True)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertEqual([o['order'] for o in self.client.get('/api/merge-orders/pending/').json()['orders']],
                         [kept.order])

    def test_only_a_merger_on_a_key_device(self):
        self.client.cookies.clear()
        self.assertEqual(self.press().status_code, 401)
        self.as_('rj')  # signed in through PickiPedia: chat, not machinery
        self.assertEqual(self.press().status_code, 403)
        with override_settings(GITHOUSE_MERGERS=('justin',)):
            self.as_('skyler')
            self.assertFalse(self.preview().json()['can'])
            said = self.press()
            self.assertEqual((said.status_code, said.json()['error']), (403, 'skyler may not merge'))
        self.assertEqual(MergeOrder.objects.count(), 0)

    def test_no_key_no_button(self):
        self.as_('justin')
        with override_settings(MERGE_KEY_PATH=str(self.dir / 'nonesuch')):
            self.assertFalse(self.preview().json()['can'])
            self.assertEqual(self.press().status_code, 403)

    def test_a_key_the_vault_kept_in_base64(self):
        b64 = self.dir / 'merge_key.b64'
        b64.write_text(base64.b64encode(self.key.read_bytes()).decode() + '\n')
        self.as_('justin')
        with override_settings(MERGE_KEY_PATH=str(b64)):
            self.assertEqual(self.press().status_code, 201)

    def test_a_press_for_what_moved_since_the_sheet(self):
        self.as_('justin')
        said = self.press(commit='0' * 40)
        self.assertEqual(said.status_code, 409)
        self.assertEqual(self.press(into='production').status_code, 409)
        self.assertEqual(MergeOrder.objects.count(), 0)

    def test_a_banned_presser_and_az5(self):
        self.as_('justin')
        Setting.objects.create(key='banned', emergent_id='justin', value=True, note='test')
        self.assertIn('banned', self.press().json()['error'])
        Setting.objects.create(key='banned', emergent_id='justin', value=False, note='test')
        Setting.objects.create(key='scram', value=True, note='AZ5')
        self.assertEqual(self.press().status_code, 423)

    def test_nobody_can_mail_an_order_that_names_a_presser(self):
        # Only press() writes 'for': a person's own order with it, or one claiming memory-lane, is refused.
        text = merge_orders.HEADER + '\n' + ''.join(f'{k}: {v}\n' for k, v in (
            ('repo', 'magenta'), ('branch', 're/foo'), ('commit', self.head), ('into', 'main'), ('by', 'skyler'),
            ('nonce', 'a' * 32), ('expires', '9999999999'), ('for', 'justin')))
        with self.assertRaises(merge_orders.Invalid):
            merge_orders.parse(text)
        as_lane = text.replace('by: skyler', 'by: memory-lane').replace('9999999999', str(int(time.time()) + 60))
        with self.assertRaises(merge_orders.Invalid):
            merge_orders.receive(as_lane, '-----BEGIN SSH SIGNATURE-----\nx\n-----END SSH SIGNATURE-----\n')

    def test_what_isn_t_there(self):
        self.as_('justin')
        self.assertEqual(self.preview(branch='re/nonesuch').status_code, 400)
        self.assertEqual(self.preview(repo='nonesuch').status_code, 400)
        self.assertIn('not one that is merged into', self.preview(branch='main').json()['error'])
        self.assertIn('not an open pull request', self.preview(branch='', number='7').json()['error'])

    def test_a_pull_request_by_its_number(self):
        from unittest import mock
        card = {'repo': 'magenta', 'number': 7, 'branch': 're/foo', 'base': 'main'}
        self.as_('justin')
        with mock.patch('conversations.services.ramp.board', return_value={'open': [card]}):
            seen = self.preview(branch='', number='7').json()
        self.assertEqual((seen['branch'], seen['commit'], seen['into'], seen['number']), ('re/foo', self.head, 'main', 7))


    def test_one_stacked_on_a_branch_that_merged_says_to_rebase(self):
        # Justin, 10 Nov: #453 was built on #451's branch; once #451 merged, the sheet said only
        # "magenta has no magent/candidates here".
        from unittest import mock
        bare = str(self.githouse / 'magenta.git')
        git('update-ref', 'refs/merged/re/under', self.head, cwd=bare)
        self.addCleanup(git, 'update-ref', '-d', 'refs/merged/re/under', cwd=bare)
        card = {'repo': 'magenta', 'number': 8, 'branch': 're/foo', 'base': 're/under'}
        self.as_('justin')
        with mock.patch('conversations.services.ramp.board', return_value={'open': [card]}):
            said = self.preview(branch='', number='8').json()['error']
        self.assertEqual(said, 'magenta#8 is built on re/under, which has merged since, so there is nothing to merge it '
                               'into. Its author rebases it onto main and points its pull request at main; then it can merge.')

    def test_the_pages_offer_it_only_to_a_merger(self):
        from unittest import mock
        card = {'repo': 'magenta', 'number': 7, 'branch': 're/foo', 'base': 'main', 'state': 'open', 'title': 'x',
                'url': '', 'author': 're', 'worked_on_by': '', 'moods': [], 'size': {}, 'pushed': None, 'active': None,
                'mentions': [], 'deploy': '', 'temperature': 'cold', 'asked': None, 'held': True}
        board = {'open': [card], 'merged': [], 'names': [], 'log': [], 'repos': ['magenta'], 'error': ''}
        with mock.patch('conversations.services.ramp.board', return_value=board), \
                mock.patch('conversations.services.ramp.ordered', side_effect=lambda items, key: items):
            self.as_('justin')
            ramp = self.client.get('/ramp/', {'embed': '1'}).content.decode()
            self.assertIn('data-repo="magenta" data-branch="re/foo" data-number="7"', ramp)
            self.assertIn('id="mergeSheetTpl"', ramp)
            self.assertIn('id="mergeSheetTpl"', self.client.get('/moods/').content.decode())
            self.as_('rj')
            self.assertNotIn('class="merge"', self.client.get('/ramp/', {'embed': '1'}).content.decode())
            self.assertNotIn('id="mergeSheetTpl"', self.client.get('/moods/').content.decode())


    def test_what_merged_is_said_in_the_mood_it_was_pressed_in_once(self):
        from conversations.models import Message, Mood
        from conversations.services.mood_view import event_line
        Mood.objects.create(slug='ramp')
        self.as_('justin')
        kept = MergeOrder.objects.get(id=self.press().json()['id'])
        pending = lambda: self.client.get('/api/merge-orders/pending/')
        pending()  # githouse looks; nothing merged yet: nothing said
        self.assertFalse(Message.objects.filter(mood__slug='ramp', source_file='merged').exists())
        (self.githouse / '.merges' / f'{kept.nonce}.json').write_text(json.dumps(
            {'outcome': 'merged', 'merge': 'c' * 40, 'order': kept.order, 'signature': kept.signature}))
        pending()
        pending()  # and again: said once
        said = Message.objects.filter(mood__slug='ramp', source_file='merged')
        self.assertEqual(said.count(), 1)
        self.assertEqual(event_line(said.get().content), '✓ magenta:re/foo · cccccccc merged by justin: magenta:re/foo into main')

    def test_a_refused_one_or_one_from_no_mood_says_nothing(self):
        from conversations.models import Message, Mood
        Mood.objects.create(slug='ramp')
        self.as_('justin')
        refused = MergeOrder.objects.get(id=self.press().json()['id'])
        (self.githouse / '.merges' / f'{refused.nonce}.json').write_text(json.dumps(
            {'outcome': 'refused', 'why': 'x', 'order': refused.order, 'signature': refused.signature}))
        quiet = MergeOrder.objects.get(id=self.press(mood='').json()['id'])
        (self.githouse / '.merges' / f'{quiet.nonce}.json').write_text(json.dumps(
            {'outcome': 'merged', 'merge': 'd' * 40, 'order': quiet.order, 'signature': quiet.signature}))
        self.client.get('/api/merge-orders/pending/')
        self.assertFalse(Message.objects.filter(source_file='merged').exists())
