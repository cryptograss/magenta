"""The shared announcement bar under the nav (services/ticker.py; Justin, #ramp, 14 Nov 2026):
everyone's githouse activity and everyone's newly completed to-dos (excluding merges and deploys)."""

import json
import os
import time
from pathlib import Path
import tempfile
from unittest import mock, skipUnless

from django.core.cache import cache
from django.test import TestCase, override_settings

from conversations.models import Device, MergeOrder, Mood, ThinkingEntity
from conversations.services import merge_orders, mood_auth, ticker, todo, todo_types as tt, wiki_hooks
from conversations.tests.test_mood_auth import HAS_SSH_KEYGEN, make_key, sign


def make_order(**change):
    fields = {'repo': 'magenta', 'branch': 're/foo', 'commit': 'a' * 40, 'into': 'main', 'by': 'skyler',
              'nonce': os.urandom(16).hex(), 'expires': str(int(time.time()) + 3600), **change}
    return merge_orders.HEADER + '\n' + ''.join(f'{k}: {fields[k]}\n' for k in merge_orders.FIELDS)


@skipUnless(HAS_SSH_KEYGEN, 'needs ssh-keygen')
class SharedGithouseTickerTest(TestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.dir = Path(tempfile.mkdtemp())
        cls.keys = {name: make_key(str(cls.dir), name) for name in ('justin', 'skyler')}
        cls.signers = cls.dir / 'allowed_signers'
        cls.signers.write_text(''.join(
            f'{name} namespaces="magenta-moods" {Path(key + ".pub").read_text().strip()}\n'
            for name, key in cls.keys.items()))
        cls.merge_key = cls.dir / 'memory-lane-merge'
        import subprocess
        subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(cls.merge_key)], check=True)
        cls.merge_key.chmod(0o400)

    def setUp(self):
        cache.clear()
        self.githouse = Path(tempfile.mkdtemp())
        (self.githouse / '.merges' / 'mailed').mkdir(parents=True)
        for patch in (override_settings(GITHOUSE_DIR=str(self.githouse)),
                      override_settings(MOOD_ALLOWED_SIGNERS=str(self.signers)),
                      override_settings(GITHOUSE_MERGERS=('justin', 'skyler')),
                      override_settings(MERGE_KEY_PATH=str(self.merge_key))):
            patch.enable()
            self.addCleanup(patch.disable)
        for name in ('justin', 'skyler', 'rj'):
            person = ThinkingEntity.objects.create(name=name, is_biological_human=True)
            Device.objects.create(entity=person, label='phone', token_hash=mood_auth.digest(name),
                                  tier='wiki' if name == 'rj' else 'key')
        Mood.objects.create(slug='ramp', title='ramp')

    def test_every_viewer_gets_the_bar_and_only_mergers_get_the_confirm_sheet(self):
        # Anonymous visitor: gets the shared ticker bar, never the merge confirm template.
        anon_html = self.client.get('/moods/').content.decode()
        self.assertIn('window.magentaTicker', anon_html)
        self.assertIn('magentaTicker.feed(', anon_html)
        self.assertNotIn('id="mergeSheetTpl"', anon_html)

        # PickiPedia-tier non-merger: gets the shared ticker bar, never the merge confirm template.
        self.client.cookies[mood_auth.COOKIE] = 'rj'
        rj_html = self.client.get('/moods/').content.decode()
        self.assertIn('window.magentaTicker', rj_html)
        self.assertNotIn('id="mergeSheetTpl"', rj_html)

        # Merger: gets both the shared ticker bar and the confirm sheet.
        self.client.cookies[mood_auth.COOKIE] = 'justin'
        merger_html = self.client.get('/moods/').content.decode()
        self.assertIn('window.magentaTicker', merger_html)
        self.assertIn('id="mergeSheetTpl"', merger_html)

    def test_everyone_sees_another_person_s_githouse_order_from_waiting_to_merged(self):
        with mock.patch('conversations.services.wiki_auth.names',
                        return_value={'justin': 'JMyles', 'skyler': 'SkymanJenkins'}):
            # Prime moods-live cache before skyler sends a merge order.
            self.client.cookies.clear()
            self.assertEqual(self.client.get('/api/moods/live/').json()['ticker'], [])

            # Skyler signs a merge order from CLI (`magenta.sh merge`).
            text = make_order(by='skyler', repo='magenta', branch='re/foo')
            sig = sign(self.keys['skyler'], text, namespace=merge_orders.NAMESPACE)
            posted = self.client.post('/api/merge-orders/', json.dumps({'order': text, 'signature': sig, 'mood': 'ramp'}),
                                      content_type='application/json')
            self.assertEqual(posted.status_code, 201)
            oid = posted.json()['id']

            # Anonymous visitor immediately sees Skyler's in-flight merge (moods-live cache was cleared).
            live = self.client.get('/api/moods/live/').json()['ticker']
            self.assertEqual(len(live), 1)
            self.assertEqual((live[0]['id'], live[0]['state'], live[0]['by']),
                             (f'merge:{oid}', 'going', 'SkymanJenkins'))
            self.assertIn('Merging magenta:re/foo into main (SkymanJenkins)…', live[0]['words'])

            # Githouse carries out the merge and polls pending orders.
            nonce = merge_orders.parse(text)['nonce']
            now = int(time.time())
            (self.githouse / '.merges' / f'{nonce}.json').write_text(json.dumps({
                'outcome': 'merged', 'merge': 'b' * 40, 'at': now, 'order': text, 'signature': sig,
            }))
            self.client.get('/api/merge-orders/pending/')

            # Everyone sees the green ✓ with who merged it and its candidate link.
            live = self.client.get('/api/moods/live/').json()['ticker']
            self.assertEqual(len(live), 1)
            self.assertEqual((live[0]['state'], live[0]['merged'], live[0]['link']),
                             ('ok', {'repo': 'magenta', 'branch': 're/foo'}, '/ramp/magenta/re/foo/'))
            self.assertEqual(live[0]['words'], 'magenta:re/foo merged into main by SkymanJenkins')

            # After OK_FOR seconds, it ages out of the live feed so new visitors aren't shown old history.
            self.assertEqual(ticker.items(now=now + ticker.OK_FOR + 1), [])

    def test_promotions_and_refusals_show_for_everyone(self):
        now = int(time.time())
        promo = MergeOrder.objects.create(
            order=make_order(repo='maybelle-config', branch='production', into='main', by='justin'),
            signature='s', nonce='n' * 32, repo='maybelle-config', branch='production',
            by='memory-lane', pressed='justin', expires=now + 3600)
        clash = MergeOrder.objects.create(
            order=make_order(repo='magenta', branch='magent/clash', into='main', by='skyler'),
            signature='s2', nonce='c' * 32, repo='magenta', branch='magent/clash',
            by='memory-lane', pressed='skyler', expires=now + 3600)
        (self.githouse / '.merges' / f'{"n" * 32}.json').write_text(json.dumps({
            'outcome': 'merged', 'merge': 'd' * 40, 'at': now, 'order': promo.order, 'signature': 's',
        }))
        (self.githouse / '.merges' / f'{"c" * 32}.json').write_text(json.dumps({
            'outcome': 'refused', 'why': 'magent/clash conflicts with main', 'at': now,
            'order': clash.order, 'signature': 's2',
        }))
        got = {it['id']: it for it in ticker.items(now=now + 5)}
        self.assertEqual(got[f'merge:{promo.id}']['state'], 'ok')
        self.assertEqual(got[f'merge:{promo.id}']['words'], 'maybelle-config: main promoted to production by justin')
        self.assertEqual(got[f'merge:{clash.id}']['state'], 'err')
        self.assertIn('Refused: magent/clash conflicts with main', got[f'merge:{clash.id}']['words'])
        self.assertEqual(ticker.items(now=now + ticker.ERR_FOR + 5), [])


class SharedTodoTickerTest(TestCase):

    def setUp(self):
        cache.clear()
        self.ramp = Mood.objects.create(slug='ramp', title='ramp')
        self.conn = Mood.objects.create(slug='connectome', title='connectome')
        ThinkingEntity.objects.get_or_create(name='magent', defaults={'is_biological_human': False})
        ThinkingEntity.objects.get_or_create(name='re', defaults={'is_biological_human': False})
        ThinkingEntity.objects.get_or_create(name='justin', defaults={'is_biological_human': True})

    def _http_raw(self, pages):
        def get(url, params=None, **kw):
            params = params or {}
            if params.get('action') == 'raw':
                title = params.get('title', '')
                text = pages.get(title)
                return mock.Mock(status_code=200 if text is not None else 404, text=text or '')
            if params.get('prop') == 'revisions':
                title = params.get('titles', '')
                hist = pages.get(('history', title)) or []
                revs = [{'slots': {'main': {'content': t}}} for t in hist]
                return mock.Mock(status_code=200, json=lambda: {'query': {'pages': [{'revisions': revs}]}})
            return mock.Mock(status_code=404, text='', json=lambda: {})
        return mock.Mock(get=get)

    def test_cold_first_sight_never_floods_with_historical_done_todos(self):
        yaml_text = """<todo>
- task: "Old finished work"
  who: magent
  kind: work
  done: true
- task: "Write runbook for Radicle seed"
  who: re
  kind: docs
</todo>"""
        http = self._http_raw({'Cryptograss:Moods/ramp/todo': yaml_text})
        todo.for_mood(self.ramp, http=http)
        self.assertEqual(ticker.items(), [])

        # Now re marks the open work item done (and adds a note and link at the same time).
        updated = """<todo>
- task: "Old finished work"
  who: magent
  kind: work
  done: true
- task: "Write runbook for Radicle seed"
  who: re
  kind: docs
  done: true
  link: https://pickipedia.xyz/wiki/Cryptograss:Runbooks/Radicle
  note: "Added section 3."
</todo>"""
        todo.forget(self.ramp)
        todo.for_mood(self.ramp, http=self._http_raw({'Cryptograss:Moods/ramp/todo': updated}), by='Re')
        items = ticker.items()
        self.assertEqual(len(items), 1)
        self.assertEqual((items[0]['kind'], items[0]['state'], items[0]['by']), ('todo', 'ok', 're'))
        self.assertEqual(items[0]['words'], '#ramp: Write runbook for Radicle seed (re)')
        self.assertEqual(items[0]['what'], '🤖 To-do done in #ramp')
        self.assertEqual(items[0]['sub'], 'work · by re · for re')
        self.assertIn('Added section 3.', items[0]['full'])
        self.assertEqual(items[0]['link'], 'https://pickipedia.xyz/wiki/Cryptograss:Runbooks/Radicle')

    def test_merges_and_deploys_are_excluded_from_todo_ticker(self):
        before = """<todo>
- task: "Merge magenta:magent/foo"
  kind: merge
  link: https://magenta.cryptograss.live/ramp/magenta/magent/foo/
- task: "Redeploy maybelle"
  kind: deploy
- task: "Review candidate branch"
  kind: review
  link: https://magenta.cryptograss.live/ramp/magenta/magent/bar/
- task: "Settle footer schema"
  kind: decision
  who: justin
</todo>"""
        todo.for_mood(self.ramp, http=self._http_raw({'Cryptograss:Moods/ramp/todo': before}))
        self.assertEqual(ticker.items(), [])

        after = """<todo>
- task: "Merge magenta:magent/foo"
  kind: merge
  link: https://magenta.cryptograss.live/ramp/magenta/magent/foo/
  done: true
- task: "Redeploy maybelle"
  kind: deploy
  done: true
- task: "Review candidate branch"
  kind: review
  link: https://magenta.cryptograss.live/ramp/magenta/magent/bar/
- task: "Settle footer schema"
  kind: decision
  who: justin
  done: true
</todo>"""
        todo.forget(self.ramp)
        with mock.patch('conversations.services.githouse.branch_merged', lambda repo, branch: branch == 'magent/bar'), \
                mock.patch('conversations.services.wiki_auth.names', return_value={'justin': 'JMyles'}):
            todo.for_mood(self.ramp, http=self._http_raw({'Cryptograss:Moods/ramp/todo': after}), by='JMyles')
        items = ticker.items()
        # Only the decision appears; merge, deploy, and the review closed via githouse merge are all left out.
        self.assertEqual([i['words'] for i in items], ['#ramp: Settle footer schema (JMyles)'])
        self.assertEqual(items[0]['what'], '🤔 To-do done in #ramp')

    def test_cold_cache_wiki_hook_reads_parent_revision_and_announces_only_new_tick(self):
        parent = """<todo>
- task: "Already done weeks ago"
  kind: hands
  done: true
- task: "Put Radicle key in vault"
  kind: secret
  who: justin
</todo>"""
        newest = parent + '  done: true\n'
        newest = parent.replace('  who: justin\n', '  who: justin\n  done: true\n')
        http = self._http_raw({
            'Cryptograss:Moods/ramp/todo': newest,
            ('history', 'Cryptograss:Moods/ramp/todo'): [newest, parent],
        })
        with mock.patch('requests.get', side_effect=http.get), \
                mock.patch('conversations.services.wiki_auth.names', return_value={'justin': 'JMyles'}):
            wiki_hooks.run('Cryptograss:Moods/ramp/todo', 'JMyles', 501, via='push')
        items = ticker.items()
        self.assertEqual([i['words'] for i in items], ['#ramp: Put Radicle key in vault (JMyles)'])
        self.assertEqual(items[0]['what'], '🔧 To-do done in #ramp')

    @override_settings(PICKIPEDIA_TODO_BOT_USER='TodoBot', PICKIPEDIA_TODO_BOT_PASSWORD='secret')
    def test_verifying_a_linked_wiki_page_immediately_pushes_to_the_shared_ticker_once(self):
        page_yaml = """<todo>
- task: "Verify Kyle Tuttle"
  kind: verify
  who: justin
  link: https://pickipedia.xyz/wiki/Kyle_Tuttle
</todo>"""
        http = self._http_raw({'Cryptograss:Moods/connectome/todo': page_yaml})
        with mock.patch('requests.get', side_effect=http.get), \
                mock.patch.object(tt, 'verified', lambda titles: {'Kyle Tuttle': False}), \
                mock.patch('conversations.services.todo_ticks.tick_later'):
            todo.for_mood(self.conn)
        self.assertEqual(ticker.items(), [])

        # JMyles verifies Kyle Tuttle on PickiPedia; wiki_hooks.run fires and immediately refreshes #connectome's list.
        cache.set('moods-live', {'stale': True}, 60)
        with mock.patch('requests.get', side_effect=http.get), \
                mock.patch.object(tt, 'verified', lambda titles: {'Kyle Tuttle': True}), \
                mock.patch('conversations.services.todo_ticks.tick_later'), \
                mock.patch('conversations.services.wiki_auth.names', return_value={'justin': 'JMyles'}):
            wiki_hooks.run('Kyle_Tuttle', 'JMyles', 888, via='push')

        self.assertIsNone(cache.get('moods-live'))
        items = ticker.items()
        self.assertEqual([i['words'] for i in items], ['#connectome: Verify Kyle Tuttle (JMyles)'])
        self.assertEqual(items[0]['what'], '✅ To-do done in #connectome')

        # When TodoBot writes `done: true` on the wiki two seconds later, nothing is announced a second time.
        bot_yaml = page_yaml.replace('</todo>', '  done: true\n</todo>')
        bot_http = self._http_raw({'Cryptograss:Moods/connectome/todo': bot_yaml})
        cache.delete('ticker:todos')
        with mock.patch('requests.get', side_effect=bot_http.get), \
                mock.patch.object(tt, 'verified', lambda titles: {'Kyle Tuttle': True}):
            wiki_hooks.run('Cryptograss:Moods/connectome/todo', 'TodoBot', 889, via='push')
        self.assertEqual(ticker.items(), [])
