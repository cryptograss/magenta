"""Tests for /moods/status/ and /api/status/, and removing Recent changes / Open work from /moods/."""

import os
import tempfile
import time
import uuid
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from conversations.models import Device, HarnessModels, Message, Mood, ReadMark, Setting, ThinkingEntity
from conversations.services import mood_auth, servers, status


class StatusPageTests(TestCase):

    def setUp(self):
        cache.clear()
        self.magent, _ = ThinkingEntity.objects.get_or_create(name='magent', defaults={'is_biological_human': False})
        self.re, _ = ThinkingEntity.objects.get_or_create(name='re', defaults={'is_biological_human': False})
        self.justin, _ = ThinkingEntity.objects.get_or_create(name='justin', defaults={'is_biological_human': True})
        self.skyler, _ = ThinkingEntity.objects.get_or_create(name='skyler', defaults={'is_biological_human': True})
        self.mood_a = Mood.objects.create(slug='magenta-interface', title='#magenta-interface')
        self.mood_b = Mood.objects.create(slug='docs-tests-qa', title='#docs-tests-qa')

        cards = mock.patch('conversations.services.servers.wiki_card',
                           side_effect=lambda server: {'url': 'u', 'role': '', 'art': ''})
        cards.start()
        self.addCleanup(cards.stop)

    def test_recent_changes_and_open_work_removed_from_moods_nav(self):
        html = self.client.get('/moods/').content.decode()
        self.assertNotIn('id="recent"', html)
        self.assertNotIn('id="work"', html)
        self.assertNotIn('Recent changes', html)
        self.assertNotIn('Open work', html)
        self.assertIn('href="/moods/status/"', html)

    def test_status_page_renders_and_shows_viewer(self):
        anon = self.client.get('/moods/status/')
        self.assertEqual(anon.status_code, 200)
        self.assertIn('status · magenta', anon.content.decode())

        _, token = mood_auth.enrol_device(self.justin, 'laptop', tier='key')
        signed = Client()
        signed.cookies[mood_auth.COOKIE] = token
        resp = signed.get('/moods/status/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('justin', resp.content.decode())

    def test_api_status_returns_agent_settings_in_every_mood_and_vendors(self):
        Setting.objects.create(key='discretion', mood=self.mood_a, agent=self.re, value='reserved', set_by=self.justin)
        HarnessModels.objects.create(
            agent=self.re,
            harness='pi 1.1.0',
            vendors=[
                {'name': 'openrouter', 'usable': True, 'models': [['deepseek/deepseek-v4-pro', 200000]]},
                {'name': 'openai', 'usable': False, 'models': [['gpt-5', 128000], ['gpt-5-mini', 128000]]},
            ],
        )
        with mock.patch('conversations.services.servers.probe', return_value=(True, 4)), \
             mock.patch('conversations.services.status._fetch_pickipedia_mcp_forge', return_value={
                 'fork_repo': 'cryptograss/pickipedia-mcp',
                 'upstream_repo': 'ProfessionalWiki/MediaWiki-MCP-Server',
                 'head_sha': 'ef685da',
                 'head_at': '2026-10-18T05:36:05Z',
                 'head_message': 'Merge pull request #30',
                 'upstream_tag': 'v0.19.0',
                 'ahead_of_tag': 29,
                 'behind_tag': 0,
                 'changed_files': 12,
             }):
            data = self.client.get('/api/status/').json()

        moods = {m['slug']: m for m in data['moods']}
        self.assertEqual(set(moods.keys()), {'magenta-interface', 'docs-tests-qa'})
        self.assertEqual(moods['magenta-interface']['agents']['re']['discretion'], 'reserved')
        self.assertEqual(moods['magenta-interface']['agents']['re']['catalog'], 'pi 1.1.0')
        self.assertIn('magent', data['everywhere'])
        self.assertEqual(len(data['harnesses']), 1)
        self.assertEqual(data['harnesses'][0]['vendors'], [
            {'name': 'openrouter', 'usable': True, 'model_count': 1},
            {'name': 'openai', 'usable': False, 'model_count': 2},
        ])

    def test_servers_uptime_and_last_redeploy(self):
        servers.record_deploy('maybelle', 'started', by='jmyles')
        servers.record_deploy('maybelle', 'finished', commit='145ef6e00000', by='jmyles',
                              note='https://pickipedia.xyz/wiki/Cryptograss:Maybelle/deploy_logs/26220847')
        with mock.patch('conversations.services.servers.probe', return_value=(True, 3)), \
             mock.patch('conversations.services.status.local_uptime_seconds', return_value=86400):
            srv = {s['name']: s for s in status.servers_with_uptime()}
        self.assertEqual(srv['maybelle']['uptime_seconds'], 86400)
        self.assertEqual(srv['maybelle']['deploy']['by'], 'jmyles')
        self.assertEqual(srv['maybelle']['deploy']['commit'], '145ef6e00000')
        self.assertIsNotNone(srv['maybelle']['deploy']['at'])

    def test_backup_status_reads_latest_and_daily_dumps(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            daily = root / 'daily'
            daily.mkdir()
            older = root / 'magenta_memory_26219000.dump'
            newer = root / 'magenta_memory_26221000.dump'
            daily_dump = daily / 'magenta_memory_26218000.dump'
            older.write_bytes(b'x' * 100)
            newer.write_bytes(b'y' * 250)
            daily_dump.write_bytes(b'z' * 500)
            now = time.time()
            os.utime(older, (now - 7200, now - 7200))
            os.utime(newer, (now - 600, now - 600))
            os.utime(daily_dump, (now - 3600, now - 3600))

            b = status.backup_status(backup_dir=tmp)
            self.assertTrue(b['mounted'])
            self.assertEqual(b['latest']['filename'], 'magenta_memory_26221000.dump')
            self.assertEqual(b['latest']['size_bytes'], 250)
            self.assertEqual(b['latest']['block'], 26221000)
            self.assertEqual(b['latest_count'], 2)
            self.assertEqual(b['daily']['filename'], 'magenta_memory_26218000.dump')
            self.assertEqual(b['daily']['size_bytes'], 500)
            self.assertEqual(b['daily']['block'], 26218000)
            self.assertEqual(b['daily_count'], 1)

        unmounted = status.backup_status(backup_dir='/nonexistent/magenta/backups/path')
        self.assertFalse(unmounted['mounted'])
        self.assertIsNone(unmounted['latest'])

    def test_last_seen_humans_orders_newest_first_and_caps_at_ten(self):
        now = timezone.now()
        # ReadMark for skyler (most recent)
        ReadMark.objects.create(entity=self.skyler, mood=self.mood_a, seen_at=now - timedelta(minutes=2))
        # Message for justin (5 minutes ago)
        Message.objects.create(id=uuid.uuid4(), sender=self.justin, mood=self.mood_b, content='hello', timestamp=1000)
        Message.objects.filter(sender=self.justin).update(created_at=now - timedelta(minutes=5))
        # Agent message (more recent, must not appear in humans list)
        Message.objects.create(id=uuid.uuid4(), sender=self.magent, mood=self.mood_a, content='agent turn', timestamp=2000)

        # Add 10 more humans with older device timestamps to verify the cap of 10
        for i in range(10):
            p = ThinkingEntity.objects.create(name=f'person{i:02d}', is_biological_human=True)
            Device.objects.create(entity=p, label='browser', token_hash=f'{i:064x}',
                                  last_used_at=now - timedelta(hours=i + 1))

        seen = status.last_seen_humans(limit=10)
        self.assertEqual(len(seen), 10)
        self.assertEqual(seen[0]['name'], 'skyler')
        self.assertEqual(seen[0]['how'], 'read #magenta-interface')
        self.assertEqual(seen[1]['name'], 'justin')
        self.assertEqual(seen[1]['how'], 'spoke in #docs-tests-qa')
        self.assertNotIn('magent', [h['name'] for h in seen])

    def test_magenta_version_includes_page_digest_and_deploy(self):
        servers.record_deploy('maybelle', 'finished', commit='bc455f28884a', by='jmyles')
        with mock.patch.dict(os.environ, {'MAGENTA_COMMIT': '145ef6e'}):
            v = status.magenta_version()
        self.assertEqual(v['commit'], '145ef6e')
        self.assertRegex(v['page_version'], r'^[0-9a-f]{12}$')
        self.assertEqual(v['maybelle_deploy']['by'], 'jmyles')


class StatusPrivacyAndLastSeenTests(TestCase):
    """Who was last seen where, and where the backups are, are for an SSH-key sign-in (as readers'
    addresses are); and each person's own newest activity counts, however it's stored."""

    def setUp(self):
        cache.clear()
        self.justin = ThinkingEntity.objects.create(name='justin', is_biological_human=True)
        self.skyler = ThinkingEntity.objects.create(name='skyler', is_biological_human=True)
        self.mood = Mood.objects.create(slug='general', title='general')
        for name in ('wiki_card', 'probe'):
            patch = mock.patch(f'conversations.services.servers.{name}',
                               **({'side_effect': lambda server: {'url': 'u', 'role': '', 'art': ''}}
                                  if name == 'wiki_card' else {'return_value': (True, 4)}))
            patch.start()
            self.addCleanup(patch.stop)
        forge = mock.patch('conversations.services.status._fetch_pickipedia_mcp_forge', return_value={'head_sha': 'x'})
        forge.start()
        self.addCleanup(forge.stop)

    def get(self, tier=None):
        client = Client()
        if tier:
            _, token = mood_auth.enrol_device(self.justin, 'laptop', tier=tier)
            client.cookies[mood_auth.COOKIE] = token
        return client.get('/api/status/').json()

    def test_last_seen_and_backups_only_for_an_ssh_key_sign_in(self):
        Message.objects.create(id=uuid.uuid4(), sender=self.skyler, mood=self.mood, content='hi', timestamp=1)
        for tier in (None, 'wiki'):
            shown = self.get(tier)
            self.assertTrue(shown['private'])
            self.assertIsNone(shown['humans'])
            self.assertIsNone(shown['backup'])
            self.assertIn('servers', shown)  # the rest, to anyone
        shown = self.get('key')
        self.assertFalse(shown['private'])
        self.assertIn('skyler', [h['name'] for h in shown['humans']])
        self.assertIn('mounted', shown['backup'])

    def test_a_device_never_used_doesnt_hide_one_used_since(self):
        now = timezone.now()
        Device.objects.create(entity=self.justin, label='old phone', token_hash='a' * 64, last_used_at=None)
        Device.objects.filter(label='old phone').update(created_at=now - timedelta(days=30))
        Device.objects.create(entity=self.justin, label='laptop', token_hash='b' * 64,
                              last_used_at=now - timedelta(minutes=1))
        seen = {h['name']: h for h in status.last_seen_humans()}
        self.assertEqual(seen['justin']['how'], 'device (laptop)')

    def test_whoever_spoke_least_lately_still_counts_their_words(self):
        now = timezone.now()
        Message.objects.bulk_create([Message(id=uuid.uuid4(), sender=self.justin, mood=self.mood, content=str(i),
                                             timestamp=i) for i in range(205)])
        Message.objects.create(id=uuid.uuid4(), sender=self.skyler, mood=self.mood, content='long ago', timestamp=0)
        Message.objects.filter(sender=self.skyler).update(created_at=now - timedelta(days=3))
        seen = {h['name']: h for h in status.last_seen_humans()}
        self.assertEqual(seen['skyler']['how'], 'spoke in #general')


class PickipediaMcpForgeTest(TestCase):
    """Ours isn't a GitHub fork of upstream and has none of its tags (both 404'd, 18 Oct): it's
    compared with the commit upstream's release names, on our own default branch (master)."""

    ANSWERS = {
        'repos/cryptograss/pickipedia-mcp': {'default_branch': 'master'},
        'repos/cryptograss/pickipedia-mcp/commits/master': {
            'sha': 'ef685da0000', 'commit': {'committer': {'date': '2026-10-18T05:36:05Z'}, 'message': 'Merge #30\n\nmore'}},
        'repos/ProfessionalWiki/MediaWiki-MCP-Server': {'default_branch': 'master'},
        'repos/ProfessionalWiki/MediaWiki-MCP-Server/releases/latest': {'tag_name': 'v0.19.0'},
        'repos/ProfessionalWiki/MediaWiki-MCP-Server/git/ref/tags/v0.19.0': {'object': {'type': 'tag', 'sha': 'tagobj'}},
        'repos/ProfessionalWiki/MediaWiki-MCP-Server/git/tags/tagobj': {'object': {'sha': '0b5e84d0000'}},
        'repos/cryptograss/pickipedia-mcp/compare/0b5e84d0000...master': {'ahead_by': 29, 'behind_by': 0, 'files': [{}] * 12},
        'repos/ProfessionalWiki/MediaWiki-MCP-Server/compare/0b5e84d0000...master': {'ahead_by': 17},
    }

    def test_against_the_release_commit_and_how_far_upstream_has_gone_since(self):
        class Answer:
            def __init__(self, body):
                self.body = body

            def raise_for_status(self):
                if self.body is None:
                    raise OSError('404')

            def json(self):
                return self.body

        asked = []

        def fake_get(url, headers=None, timeout=None):
            path = url.replace('https://api.github.com/', '')
            asked.append(path)
            return Answer(self.ANSWERS.get(path))

        with mock.patch('requests.get', fake_get):
            got = status._fetch_pickipedia_mcp_forge()
        self.assertEqual((got['head_sha'], got['head_message']), ('ef685da', 'Merge #30'))
        self.assertEqual((got['ahead_of_tag'], got['behind_tag'], got['changed_files']), (29, 0, 12))
        self.assertEqual((got['upstream_tag'], got['upstream_tag_sha'], got['upstream_since_tag']), ('v0.19.0', '0b5e84d', 17))
        self.assertEqual(len(asked), 8)
