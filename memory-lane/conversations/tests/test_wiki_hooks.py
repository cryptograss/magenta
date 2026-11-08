"""What happens here when a page changes on PickiPedia (services/wiki_hooks.py), by the push and by the sweep."""

import hashlib
import hmac
import json
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings

from conversations.models import Message, Mood, WikiHookRun
from conversations.services import mood_view, wiki_changes, wiki_hooks

SECRET = 's' * 32


def signed(body):
    raw = json.dumps(body).encode()
    return raw, 'sha256=' + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()


class RegistryTest(TestCase):
    def test_each_page_that_matters_has_its_hook(self):
        for title, names in (('Magenta:Interface', ['words']), ('Magenta:System_Messages', ['words']),
                             ('Magenta:FAQ', ['faq']), ('Magenta:Clips', ['clips']), ('Magenta:Voice', ['voice']),
                             ('Magenta:Tour', ['tour']), ('Magenta:Slogans', ['slogans']), ('Cryptograss:Eras', ['eras']),
                             ('Magenta:Current sprint', ['sprint']), ('User:Slvrbckt', ['card']),
                             ('User:Slvrbckt/sandbox', []), ('Cryptograss:Emergents/Magent/Instructions', ['emergent']),
                             ('Cryptograss:Moods/interfaces/Instructions', ['mood-doc']),
                             ('Cryptograss:Moods/interfaces/todo', ['todo']), ('File:August-notification.flac', ['file']),
                             ('Release:Some Record', ['release']), ('Shady Grove', [])):
            self.assertEqual([h.name for h in wiki_hooks.hooks_for(title)], names, title)


class RunTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        Mood.objects.create(slug='ops', title='ops')
        Mood.objects.create(slug='interfaces', title='interfaces')

    def setUp(self):
        cache.clear()

    def test_a_words_page_is_read_again_now_and_logged(self):
        cache.set('sound-defaults', {'stale': 'yes'}, 600)
        with mock.patch('conversations.services.interface_words.words', return_value={'a': 1, 'b': 2}) as words:
            rows = wiki_hooks.run('Magenta:Sounds', 'Magent', 14402, via='push')
        self.assertEqual([(r.hook, r.via, r.result, r.failed) for r in rows], [('words', 'push', '2 words read again', False)])
        self.assertIsNone(cache.get('sound-defaults'))
        self.assertEqual(words.call_count, 1)
        logged = WikiHookRun.objects.get()
        self.assertEqual((logged.title, logged.revid, logged.user), ('Magenta:Sounds', 14402, 'Magent'))

    def test_a_page_no_hook_is_for_is_forgotten_and_logged_as_that(self):
        rows = wiki_hooks.run('Shady Grove', 'Slvrbckt', 50)
        self.assertEqual([(r.hook, r.via) for r in rows], [('forget', 'sweep')])

    def test_a_hook_that_fails_is_logged_and_said_in_ops(self):
        with mock.patch('conversations.services.interface_words.words', side_effect=RuntimeError('the wiki is away')):
            rows = wiki_hooks.run('Magenta:Interface', 'Magent', 7, via='sweep')
        self.assertTrue(rows[0].failed)
        self.assertEqual(rows[0].result, 'RuntimeError: the wiki is away')
        row = Message.objects.get(source_file=wiki_hooks.SOURCE)
        self.assertEqual(mood_view.event_line(row.content),
                         '⚠ the words hook failed on [[Magenta:Interface]] (sweep): RuntimeError: the wiki is away')
        events = self.client.get('/api/moods/ops/turns/').json()['events']
        self.assertEqual([(e['type'], e['hook'], e['title'], e['via']) for e in events],
                         [('wiki-hook', 'words', 'Magenta:Interface', 'sweep')])

    def test_the_log_keeps_its_newest_rows(self):
        for i in range(wiki_hooks.KEPT + 250):
            WikiHookRun.objects.create(title=f'P{i}', hook='forget', via='sweep')
        wiki_hooks.prune()
        self.assertEqual(WikiHookRun.objects.count(), wiki_hooks.KEPT)
        self.assertEqual(WikiHookRun.objects.order_by('-at').first().title, f'P{wiki_hooks.KEPT + 249}')

    def test_the_page_and_its_api(self):
        wiki_hooks.run('Cryptograss:Moods/interfaces/todo', 'Re', 9, via='push')
        got = self.client.get('/api/wiki-hooks/').json()
        self.assertEqual([h['name'] for h in got['hooks']][:2], ['words', 'faq'])
        self.assertEqual((got['runs'][0]['hook'], got['runs'][0]['result']),
                         ('todo', '#interfaces: to-do list forgotten; open pages ask for it now'))
        self.assertFalse(got['push']['configured'])
        page = self.client.get('/wiki-hooks/')
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Cryptograss:Moods/interfaces/todo')
        self.assertContains(page, "The push isn't set up here")
        framed = self.client.get('/moods/hooks/', {'embed': '1'})  # a section under the ▾ (Justin, 7 Nov)
        self.assertContains(framed, 'Cryptograss:Moods/interfaces/todo')
        self.assertEqual(framed['X-Frame-Options'], 'SAMEORIGIN')
        self.assertContains(self.client.get('/moods/hooks/'), 'href="/moods/hooks/" data-section="hooks"')

    def test_the_pulse_carries_the_newest_run_and_since_gives_the_runs_after_it(self):
        """hunter's poller keeps its own copies of the wake's pages (poller/wiki_rules): the pulse's seq tells it
        a page changed, and ?since= which, so it forgets them at once rather than five minutes on."""
        self.assertEqual(self.client.get('/api/moods/pulse/').json()['wiki'], {'seq': 0})
        first = wiki_hooks.run('Cryptograss:Emergents/Wake', 'JMyles', 10, via='push')[0]
        second = wiki_hooks.run('Cryptograss:Moods/Dials', 'JMyles', 11, via='sweep')[0]
        self.assertEqual(self.client.get('/api/moods/pulse/').json()['wiki'], {'seq': second.id})
        got = self.client.get('/api/wiki-hooks/', {'since': first.id}).json()
        self.assertEqual([(r['id'], r['title']) for r in got['runs']], [(second.id, 'Cryptograss:Moods/Dials')])
        self.assertEqual(got['seq'], second.id)
        self.assertEqual([r['title'] for r in self.client.get('/api/wiki-hooks/', {'since': 0}).json()['runs']],
                         ['Cryptograss:Emergents/Wake', 'Cryptograss:Moods/Dials'])  # oldest first


@override_settings(WIKI_HOOK_SECRET=SECRET)
class PushTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        Mood.objects.create(slug='ops', title='ops')

    def setUp(self):
        cache.clear()

    def push(self, body, signature=None):
        raw, good = signed(body)
        return self.client.post('/api/wiki/hook/', raw, content_type='application/json',
                                HTTP_X_MAGENTA_SIGNATURE=good if signature is None else signature)

    def test_a_signed_save_runs_its_hooks_now_and_is_remembered_as_pushed(self):
        with mock.patch('conversations.services.interface_words.words', return_value={'a': 1}):
            r = self.push({'title': 'Magenta:Interface', 'revid': 14320, 'user': 'Magent', 'kind': 'edit'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {'ok': True, 'title': 'Magenta:Interface', 'revid': 14320, 'ran': ['words']})
        self.assertTrue(wiki_changes.was_pushed(14320))
        self.assertTrue(cache.get(wiki_changes.PUSH_HEARD))
        self.assertEqual(WikiHookRun.objects.get().via, 'push')
        self.assertEqual(wiki_changes.push_state()['last']['outcome'], 'Magenta:Interface r14320: words')

    def test_a_move_runs_the_hooks_for_its_old_title_too(self):
        """A move's push carries "from": what was kept under the old title is stale too (re, 8 Nov)."""
        with mock.patch('conversations.services.wiki_hooks.run', side_effect=lambda t, u, r, via: [
                mock.Mock(hook=f'h:{t}')]) as run:
            r = self.push({'title': 'Magenta:New', 'revid': 14570, 'user': 'JMyles', 'kind': 'move',
                           'from': 'Magenta:Old'})
        self.assertEqual(r.json()['ran'], ['h:Magenta:New', 'h:Magenta:Old'])
        self.assertEqual([c.args[0] for c in run.call_args_list], ['Magenta:New', 'Magenta:Old'])
        self.assertIn('moved from Magenta:Old', wiki_changes.push_state()['last']['outcome'])

    def test_unsigned_or_wrongly_signed_is_refused_and_kept_apart(self):
        r = self.push({'title': 'Magenta:Interface', 'revid': 1}, signature='sha256=' + 'f' * 64)
        self.assertEqual(r.status_code, 403)
        self.assertFalse(WikiHookRun.objects.exists())
        self.assertFalse(wiki_changes.was_pushed(1))
        self.assertEqual(wiki_changes.push_state()['refused']['outcome'], 'signature not accepted')
        self.assertIsNone(wiki_changes.push_state()['last'])

    def test_a_bad_body_is_400(self):
        raw = b'not json'
        sig = 'sha256=' + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
        r = self.client.post('/api/wiki/hook/', raw, content_type='application/json', HTTP_X_MAGENTA_SIGNATURE=sig)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.push({'revid': 1}).status_code, 400)

    @override_settings(WIKI_HOOK_SECRET='')
    def test_without_a_secret_the_push_is_not_set_up(self):
        self.assertEqual(self.push({'title': 'X', 'revid': 1}).status_code, 503)

    def test_the_sweep_leaves_a_pushed_change_to_its_push_and_judges_it_pushed(self):
        # Pushed: its hooks ran then. The sweep finds it a minute on, runs nothing again, and calls nothing missed.
        with mock.patch('conversations.services.interface_words.words', return_value={'a': 1}):
            self.push({'title': 'Magenta:Interface', 'revid': 500, 'user': 'Magent'})
        cache.set(wiki_changes.CURSOR, 40, None)
        from conversations.tests.test_wiki_changes import NOW, stamp
        rows = [{'type': 'edit', 'title': 'Magenta:Interface', 'rcid': 50, 'revid': 500, 'user': 'Magent', 'timestamp': stamp(600)}]
        http = mock.Mock(get=mock.Mock(return_value=mock.Mock(json=lambda: {'query': {'recentchanges': rows}})))
        forgotten, missed = wiki_changes.sweep(http, now=NOW)
        self.assertEqual((forgotten, missed), ([], []))
        self.assertEqual(WikiHookRun.objects.count(), 1)
