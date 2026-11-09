"""A page for each piece of work (Plan, section 9; Justin, 9 Nov: "use the tidings interface"): a Tiding on a
'work' line in its Mood, the work above its comments, each file's diff and the whole file with every line an
address."""

import json
import re
import tempfile
from pathlib import Path
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings

from conversations.models import Device, Message, Mood, ThinkingEntity, Tiding, TidingComment, WorkTiding
from conversations.services import mood_auth, pr_cards, ramp, tiding_links, work_pages as work
from conversations.services.mood_view import event_line
from conversations.tests.test_githouse import Repo, git
from conversations.tests.test_ramp import card


class WorkRepo(TestCase):
    """magenta in githouse's copy: main, and re/foo (#7) changing app.py and adding new.txt."""

    def setUp(self):
        cache.clear()
        self.mount = Path(tempfile.mkdtemp())
        patch = override_settings(GITHOUSE_DIR=str(self.mount))
        patch.enable()
        self.addCleanup(patch.disable)
        self.repo = Repo(self.mount)
        (self.repo.work / 'app.py').write_text('one\ntwo\nthree\n')
        git(self.repo.work, 'add', '.')
        git(self.repo.work, 'commit', '-q', '-m', 'the app')
        self.base = git(self.repo.work, 'rev-parse', 'HEAD')
        self.repo.ref('refs/heads/main', self.base)
        git(self.repo.work, 'checkout', '-q', '-b', 're/foo')
        (self.repo.work / 'app.py').write_text('one\n2\nthree\nfour\n')
        (self.repo.work / 'new.txt').write_text('hello\n')
        git(self.repo.work, 'add', '.')
        git(self.repo.work, 'commit', '-q', '-m', 'the foo work', '-m', 'Why: because.')
        self.head = git(self.repo.work, 'rev-parse', 'HEAD')
        self.repo.ref('refs/heads/re/foo', self.head)
        self.repo.ref('refs/pull/7/head', self.head)
        git(self.repo.work, 'checkout', '-q', 'main')
        self.cards = {'open': [card('magenta', 7, 're/foo', description='The **foo** work.\n\nDeploy: none')],
                      'merged': []}
        for p in (mock.patch.object(ramp, 'cards', lambda: self.cards),
                  mock.patch.object(ramp, 'BUILD_IN_BACKGROUND', False),
                  mock.patch.object(ramp, 'test_runs', lambda: {})):
            p.start()
            self.addCleanup(p.stop)
        for name, human in (('justin', True), ('re', False)):
            who, _ = ThinkingEntity.objects.get_or_create(name=name, defaults={'is_biological_human': human})
            Device.objects.create(entity=who, label='phone', token_hash=mood_auth.digest(name))
        self.ramp_mood = Mood.objects.create(slug='ramp', title='ramp')

    def as_(self, name):
        self.client.cookies[mood_auth.COOKIE] = name

    def open_(self, **change):
        body = {'repo': 'magenta', 'branch': 're/foo', **change}
        return self.client.post('/api/ramp/work/', json.dumps(body), content_type='application/json')

    def page(self, tiding_id, **more):
        return self.client.get(f'/moods/tidings/{tiding_id}/', {'embed': 1}, HTTP_SEC_FETCH_DEST='iframe', **more)


class WorkPageTest(WorkRepo):

    def test_opened_once_on_a_line_in_its_mood(self):
        # The card names #ramp and #ops; #ops doesn't exist here, so the first that does.
        self.as_('justin')
        first = self.open_()
        self.assertEqual(first.status_code, 200)
        tid = first.json()['id']
        self.assertEqual(self.open_().json()['id'], tid)   # the same page again, not a second
        page = WorkTiding.objects.get()
        self.assertEqual((page.repo, page.branch, page.number, str(page.tiding_id)), ('magenta', 're/foo', 7, tid))
        line = page.tiding.message
        self.assertEqual((line.mood.slug, line.source_file, line.content['tiding']), ('ramp', 'work', tid))
        self.assertEqual(event_line(line.content), "💬 justin opened a page to talk about magenta#7: the re/foo work")
        self.assertEqual(page.tiding.tags, ['work'])

    def test_who_may_open_one(self):
        self.assertEqual(self.open_().status_code, 401)
        self.as_('re')
        self.assertEqual(self.open_().status_code, 403)
        self.as_('justin')
        self.assertEqual(self.open_(branch='re/nothing').status_code, 404)
        self.assertEqual(self.open_(branch=None).status_code, 400)
        self.assertFalse(Tiding.objects.exists())

    def test_kept_off_the_tidings_list_and_its_badges(self):
        self.as_('justin')
        tid = self.open_().json()['id']
        TidingComment.objects.create(tiding_id=tid, by_id='justin', text='looks good',
                                     parent_message=Tiding.objects.get().message)
        self.assertEqual([t['id'] for t in tiding_links.listed()], [])
        from datetime import datetime, timezone
        self.assertEqual(tiding_links.badges(datetime(2000, 1, 1, tzinfo=timezone.utc)), {'tidings': 0, 'comments': 0})

    def test_the_page_shows_the_work(self):
        self.as_('justin')
        tid = self.open_().json()['id']
        html = self.page(tid).content.decode()
        self.assertIn('id="work"', html)
        self.assertIn(f'data-head="{self.head}"', html)
        self.assertIn(f'data-base="{self.base}"', html)
        self.assertIn('<strong>foo</strong>', html)          # the description, drawn as the Mood draws words
        self.assertIn('the foo work', html)                   # its commit
        self.assertIn('Why: because.', html)
        self.assertIn('data-path="app.py"', html)
        self.assertIn('data-path="new.txt"', html)
        self.assertIn('‹ the ramp', html)
        self.assertIn('class="thumb"', html)                  # the 👍, for a signed-in person
        self.assertNotIn('class="vote', html)                 # not Tidings' upvote: the 👍 is this page's

    def test_one_file_s_diff_every_line_an_address(self):
        html = self.client.get('/api/ramp/diff/', {'repo': 'magenta', 'base': self.base, 'head': self.head,
                                                   'path': 'app.py'}).content.decode()
        self.assertIn('<tr class="del" id="D:app.py:-2">', html)    # "two", gone
        self.assertIn('<tr class="add" id="D:app.py:2">', html)     # "2", in its place
        self.assertIn('<tr class="add" id="D:app.py:4">', html)     # "four", added
        self.assertIn('<tr class="ctx" id="D:app.py:1">', html)

    def test_the_whole_file_every_line_an_address(self):
        html = self.client.get('/api/ramp/file/', {'repo': 'magenta', 'commit': self.head, 'path': 'app.py'}).content.decode()
        html = re.sub(r'</?span[^>]*>', '', html)   # its colours aside (HighlightTest)
        for n, text in enumerate(['one', '2', 'three', 'four'], 1):
            self.assertIn(f'<tr id="L:app.py:{n}"><td class="n"><a href="#L:app.py:{n}">{n}</a></td><td class="t">{text}</td></tr>', html)

    def test_only_what_githouse_has_and_no_odd_paths(self):
        q = {'repo': 'magenta', 'commit': self.head, 'path': 'app.py'}
        for bad in ({'repo': 'nowhere'}, {'commit': 'HEAD'}, {'commit': '0' * 40}, {'path': '../etc/passwd'},
                    {'path': '-x'}, {'path': 'missing.py'}):
            with self.subTest(bad=bad):
                self.assertIn(self.client.get('/api/ramp/file/', {**q, **bad}).status_code, (400, 404))
        d = {'repo': 'magenta', 'base': self.base, 'head': self.head, 'path': 'app.py'}
        self.assertEqual(self.client.get('/api/ramp/diff/', {**d, 'base': 'main'}).status_code, 404)

    def test_the_ramp_offers_a_page_then_links_it(self):
        self.as_('justin')
        html = self.client.get('/moods/pulls/', {'embed': 1}, HTTP_SEC_FETCH_DEST='iframe').content.decode()
        self.assertIn('<button type="button" class="talk" data-repo="magenta" data-branch="re/foo"', html)
        tid = self.open_().json()['id']
        cache.clear()
        html = self.client.get('/moods/pulls/', {'embed': 1}, HTTP_SEC_FETCH_DEST='iframe').content.decode()
        self.assertIn(f'<a class="talk" href="/moods/tidings/{tid}/"', html)


class CardDescriptionTest(TestCase):

    def test_a_description_is_kept_whole_and_read_back(self):
        body = 'Fixes it.\n|title=not the title\n</pre> & <b>bold</b>\n\nDeploy: none\nWorked on by: re, in #ramp'
        pull = {'base': {'repo': {'full_name': 'cryptograss/magenta'}, 'ref': 'main'}, 'number': 9, 'title': 'The fix',
                'html_url': 'https://github.com/cryptograss/magenta/pull/9', 'user': {'login': 're'}, 'state': 'open',
                'head': {'ref': 're/fix'}, 'created_at': '2026-11-09T10:00:00Z', 'body': body}
        found = ramp._parse(pr_cards.card(pull))
        self.assertEqual(found['title'], 'The fix')
        self.assertEqual(found['description'], body)

    def test_no_description_no_pre(self):
        pull = {'base': {'repo': {'full_name': 'cryptograss/magenta'}, 'ref': 'main'}, 'number': 9, 'title': 'x',
                'head': {'ref': 're/x'}, 'body': None}
        self.assertNotIn('<pre', pr_cards.card(pull))


class HighlightTest(WorkRepo):
    """Syntax colours (Pygments; Justin, 9 Nov): each side's whole file coloured, each line given its own."""

    def setUp(self):
        super().setUp()
        git(self.repo.work, 'checkout', '-q', 're/foo')
        (self.repo.work / 'doc.py').write_text('x = """one\ntwo\n"""\n')
        (self.repo.work / 'notes.zzz').write_text('<b>plain</b>\n')
        git(self.repo.work, 'add', '.')
        git(self.repo.work, 'commit', '-q', '-m', 'more')
        self.head2 = git(self.repo.work, 'rev-parse', 'HEAD')
        self.repo.ref('refs/heads/re/foo', self.head2)
        git(self.repo.work, 'checkout', '-q', 'main')

    def diff(self, path, head=None):
        return self.client.get('/api/ramp/diff/', {'repo': 'magenta', 'base': self.base, 'head': head or self.head2,
                                                   'path': path}).content.decode()

    def test_a_line_inside_a_string_is_coloured_as_the_string(self):
        html = self.diff('doc.py')
        self.assertIn('<tr class="add" id="D:doc.py:2"><td class="n"></td><td class="n"><a href="#D:doc.py:2">2</a></td>'
                      '<td class="t"><span class="s2">two</span></td></tr>', html)

    def test_a_removed_line_is_coloured_from_the_old_file(self):
        html = self.diff('app.py', head=self.head)
        self.assertIn('<td class="t"><span class="n">two</span></td>', html)   # "two", from the base's app.py

    def test_a_file_with_no_lexer_is_plain_and_escaped(self):
        html = self.diff('notes.zzz')
        self.assertIn('<td class="t">&lt;b&gt;plain&lt;/b&gt;</td>', html)
        whole = self.client.get('/api/ramp/file/', {'repo': 'magenta', 'commit': self.head2, 'path': 'notes.zzz'})
        self.assertIn('<td class="t">&lt;b&gt;plain&lt;/b&gt;</td>', whole.content.decode())

    def test_the_whole_file_coloured_and_the_page_has_the_colours(self):
        html = self.client.get('/api/ramp/file/', {'repo': 'magenta', 'commit': self.head2, 'path': 'doc.py'}).content.decode()
        self.assertIn('<a href="#L:doc.py:1">1</a></td><td class="t"><span class="n">x</span>', html)
        css = work.highlight_css()
        self.assertIn('table.code td.t .k {', css)
        self.assertIn('@media (prefers-color-scheme: dark)', css)
        self.assertNotIn('.err', css)

    def test_a_long_file_is_plain(self):
        with mock.patch.object(work, 'MAX_HIGHLIGHT', 5):
            self.assertIsNone(work.highlighted('doc.py', 'x = 1\ny = 2\n'))
