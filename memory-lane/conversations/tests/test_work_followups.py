"""Justin's notes on the work pages (9 Nov, #ramp): a comment on a work's page said whole in its Mood, and a reply
there shown under it on the page; one merged line, by the branch; the ramp's links to each work's page; the dots for each emergent asking for a merge
are in test_ramp; who asked and who 👍'd it, on the merge sheet."""

import time
import json
import uuid
from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from conversations.models import WorkTiding, MergeOrder, Message, Mood, ThinkingEntity, Thumb, Tiding, TidingComment
from conversations.services import links, ramp, tiding_links, todo
from conversations.services.mood_view import event_line
from conversations.tests.test_work_pages import WorkRepo


class WorkCommentInTheMoodTest(WorkRepo):

    def setUp(self):
        super().setUp()
        links._memo.update(at=0.0, index=None)
        self.as_('justin')
        self.tiding = Tiding.objects.get(id=self.open_().json()['id'])
        self.justin = ThinkingEntity.objects.get(name='justin')

    def notice(self):
        return Message.objects.get(source_file=tiding_links.COMMENT_SOURCE)

    def events(self):
        return self.client.get('/api/moods/ramp/turns/').json()['events']

    def test_said_whole_in_the_mood(self):
        tiding_links.comment(self.tiding, self.justin, 'Line one.\n\n**bold** and <script>alert(1)</script>')
        self.assertEqual(self.notice().content['text'], 'Line one.\n\n**bold** and <script>alert(1)</script>')
        said = next(e for e in self.events() if e['type'] == 'tiding-comment')
        self.assertIn('<strong>bold</strong>', said['html'])
        self.assertNotIn('<script>', said['html'])
        self.assertTrue(event_line(self.notice().content).startswith('💬 justin commented on magenta#7'))

    def test_opening_a_page_says_nothing_in_the_mood(self):
        # Justin, 10 Nov: "All I did was go to the CB page in order to look at the full diff." Its line is only what
        # the page's Tiding hangs on: kept, with which work it is, but never drawn. A comment there still shows.
        self.assertEqual([e for e in self.events() if e['type'] == 'work'], [])
        self.assertEqual(self.tiding.message.content['ref'], 'magenta#7')
        tiding_links.comment(self.tiding, self.justin, 'Should the dots be bigger?')
        self.assertEqual([e['type'] for e in self.events()], ['tiding-comment'])

    def test_any_other_tidings_comment_is_still_one_line(self):
        said = Message.objects.create(id=uuid.uuid4(), sender=self.justin, mood=self.ramp_mood, timestamp=1,
                                      content='https://example.org/news', source_file='mood-web')
        other = tiding_links.promote(said, self.justin)
        tiding_links.comment(other, self.justin, 'A long thought about the news.')
        self.assertNotIn('text', self.notice().content)
        self.assertNotIn('html', next(e for e in self.events() if e['type'] == 'tiding-comment'))

    def reply_in_mood(self, sender, to, text):
        reply = Message.objects.create(id=uuid.uuid4(), sender_id=sender, mood=self.ramp_mood, timestamp=2,
                                       content=f'↩ #m-{to}\n{text}', source_file='mood-web')
        links._memo.update(at=0.0, index=None)
        return reply

    def test_a_reply_in_the_mood_shows_under_the_comment(self):
        said = tiding_links.comment(self.tiding, self.justin, 'Should the dots be bigger?')
        reply = self.reply_in_mood('justin', self.notice().id, 'Nah, nine pixels is right.')
        [comment] = tiding_links.tree_of(self.tiding, self.tiding.message_id)
        self.assertEqual(comment['id'], said['id'])
        self.assertEqual([(r['kind'], r['id']) for r in comment['replies']], [('mood', str(reply.id))])
        self.assertIn('Nah, nine pixels is right.', self.page(self.tiding.id).content.decode())
        # ...and it can be answered from the page, as any Mood reply in the discussion can.
        tiding_links.comment(self.tiding, self.justin, 'Fair.', parent=('message', reply.id))

    def test_an_emergents_answer_isnt_shown_twice(self):
        said = tiding_links.comment(self.tiding, self.justin, '@re does this hold?')
        TidingComment.objects.create(tiding=self.tiding, by_id='re', parent_id=said['id'], text='It holds.')
        self.reply_in_mood('re', self.notice().id, 'It holds.')
        [comment] = tiding_links.tree_of(self.tiding, self.tiding.message_id)
        self.assertEqual([r['kind'] for r in comment['replies']], ['comment'])

    def test_edited_or_deleted_the_line_follows(self):
        said = tiding_links.comment(self.tiding, self.justin, 'First words.')
        tiding_links._renotice(said['id'], 'Second words.')
        self.assertEqual((self.notice().content['text'], self.notice().content['changed']), ('Second words.', 'edited'))
        tiding_links._renotice(said['id'], None)
        c = self.notice().content
        self.assertEqual((c.get('text'), c['comment'], c['changed']), (None, '', 'deleted'))


class MergedOnceTest(TestCase):
    """Merged by the button, the Mood says so by the branch's name; GitHub's word of the pull request isn't said
    there again (Justin, 9 Nov: "let's just show the branch")."""

    def setUp(self):
        cache.clear()
        self.ramp = Mood.objects.create(slug='ramp', title='ramp')
        self.ops = Mood.objects.create(slug='ops', title='ops')
        listed = {'items': [{'task': 'merge the foo work', 'link': 'https://github.com/cryptograss/magenta/pull/7'}]}
        patch = mock.patch.object(todo, 'for_mood', lambda mood: listed)
        patch.start()
        self.addCleanup(patch.stop)

    def said(self):
        return sorted(Message.objects.filter(source_file=todo.MERGED_SOURCE).values_list('mood__slug', flat=True))

    def test_not_said_again_where_the_button_merged_it(self):
        # Unanswered yet, as when GitHub's webhook beats githouse's record: the button's merge, most likely.
        MergeOrder.objects.create(order='-', signature='-', nonce='n', repo='magenta', branch='re/foo', by='memory-lane',
                                  pressed='justin', expires=int(time.time()) + 3600, mood='ramp')
        todo.announce_merge('cryptograss', 'magenta', 7, 'the foo work', 'jMyles', 'a' * 40, branch='re/foo')
        self.assertEqual(self.said(), ['ops'])

    def test_said_wherever_listed_otherwise(self):
        todo.announce_merge('cryptograss', 'magenta', 7, 'the foo work', 'jMyles', 'a' * 40, branch='re/foo')
        self.assertEqual(self.said(), ['ops', 'ramp'])

    def test_a_refused_press_doesnt_count(self):
        # A press refused said nothing in #ramp: GitHub's line is its only word of the merge.
        refused = MergeOrder.objects.create(order='-', signature='-', nonce='n', repo='magenta', branch='re/foo',
                                            by='memory-lane', pressed='justin', expires=int(time.time()) + 3600, mood='ramp')
        with mock.patch('conversations.services.merge_orders.outcome',
                        lambda o: {'status': 'refused', 'why': 'moved'} if o.id == refused.id else None):
            todo.announce_merge('cryptograss', 'magenta', 7, 'the foo work', 'jMyles', 'a' * 40, branch='re/foo')
        self.assertEqual(self.said(), ['ops', 'ramp'])

    def test_an_expired_press_doesnt_count(self):
        MergeOrder.objects.create(order='-', signature='-', nonce='n', repo='magenta', branch='re/foo',
                                  by='memory-lane', pressed='justin', expires=0, mood='ramp')  # never answered, past its hour
        todo.announce_merge('cryptograss', 'magenta', 7, 'the foo work', 'jMyles', 'a' * 40, branch='re/foo')
        self.assertEqual(self.said(), ['ops', 'ramp'])

    def test_an_old_order_for_a_name_doesnt_count(self):
        old = MergeOrder.objects.create(order='-', signature='-', nonce='n', repo='magenta', branch='re/foo',
                                        by='memory-lane', expires=0, mood='ramp')
        MergeOrder.objects.filter(id=old.id).update(received_at=timezone.now() - timedelta(days=5))
        todo.announce_merge('cryptograss', 'magenta', 7, 'the foo work', 'jMyles', 'a' * 40, branch='re/foo')
        self.assertEqual(self.said(), ['ops', 'ramp'])


class RampLinksAndSheetTest(WorkRepo):

    def ramp_page(self):
        cache.delete(ramp.KEY)
        return self.client.get('/ramp/', {'embed': 1}, HTTP_SEC_FETCH_DEST='iframe').content.decode()

    def test_the_works_name_opens_its_page(self):
        # Its page is in the ramp, at /ramp/<repo>/<branch>/ (Justin, 9 Nov: "why does it take me to tidings?"),
        # made the first time it's asked for; ± See the diff is the same page at #diff.
        self.as_('justin')
        for _ in range(2):  # without a page yet, then with one
            page = self.ramp_page()
            self.assertIn('<a class="title" href="/ramp/magenta/re/foo/"', page)
            self.assertIn('<a class="see" href="/ramp/magenta/re/foo/#diff"', page)
            self.assertIn('<a class="gh" href="https://github.com/cryptograss/magenta/pull/7" target="_blank" rel="noopener" '
                          'title="GitHub\'s pull request page for it">PR #7 ↗</a>', page)
            self.open_()
        self.assertNotIn('/tidings/', page.split('<table id="open">')[1].split('</table>')[0])

    def test_signed_out_it_is_still_githubs(self):
        page = self.ramp_page()
        self.assertIn('<a class="title" href="https://github.com/cryptograss/magenta/pull/7" target="_blank"', page)
        self.assertIn('<a class="see" href="https://github.com/cryptograss/magenta/pull/7/files" target="_blank"', page)
        self.assertNotIn('/ramp/magenta/', page.split('<table id="open">')[1].split('</table>')[0])

    def test_merged_work_links_its_page(self):
        self.as_('justin')
        self.open_()
        self.cards['merged'] = [dict(self.cards['open'][0])]
        page = self.ramp_page()
        self.assertIn('<a class="title" href="/ramp/magenta/re/foo/" title="Its page: what it changed, its commits, what was said">'
                      'the re/foo work</a>', page)
        self.assertIn('<span class="ref">magenta:re/foo</span>', page)

    def test_the_sheet_says_who_asked_and_who_liked_it(self):
        from conversations.views_merge import _who_wants
        Thumb.objects.create(repo='magenta', branch='re/foo', commit=self.head, by='justin')
        Thumb.objects.create(repo='magenta', branch='re/foo', commit='b' * 40, by='skyler')
        asks = [{'by': 're', 'mood': 'ramp', 'id': 'x', 'at': '2026-11-09T15:00:00', 'colour': '#ff3c00'}]
        with mock.patch.object(ramp, 'asks', lambda items: [i.update(asks=asks) for i in items] and items):
            cache.delete(ramp.KEY)
            got = _who_wants({'repo': 'magenta', 'branch': 're/foo', 'commit': self.head})
        self.assertEqual(got['asks'], [{'by': 're', 'mood': 'ramp', 'id': 'x', 'colour': '#ff3c00'}])
        self.assertEqual(sorted((t['by'], t['current']) for t in got['thumbs']), [('justin', True), ('skyler', False)])
        json.dumps(got)  # as the sheet gets it


class MergedFromAMoodTellsTheRampTest(WorkRepo):
    """Justin, 10 Nov (#ramp): merged magent/mood-list-candidates from #interfaces' sheet, and the ramp kept showing
    it open until he left it and came back. The ✓ reloaded the ramp before announce_merged had marked its board
    old; now the answer that turns the bar ✓ marks it, for a Mood's order too."""

    def test_a_moods_order_merged_marks_the_board_old_once(self):
        order = MergeOrder.objects.create(repo='magenta', branch='re/foo', by='justin', mood='interfaces', order='o',
                                          signature='s', nonce='n2', expires=int(time.time()) + 600)
        told = []
        with mock.patch('conversations.services.merge_orders.status', lambda o: {'status': 'merged'}), \
             mock.patch.object(ramp, 'changed', lambda: told.append(1)):
            for _ in range(3):   # the sheet asks every second or so
                self.assertEqual(self.client.get(f'/api/merge-orders/{order.id}/').json()['status'], 'merged')
        self.assertEqual(told, [1])

    def test_one_still_waiting_tells_it_nothing(self):
        order = MergeOrder.objects.create(repo='magenta', branch='re/foo', by='justin', mood='interfaces', order='o',
                                          signature='s', nonce='n3', expires=int(time.time()) + 600)
        told = []
        with mock.patch('conversations.services.merge_orders.status', lambda o: {'status': 'waiting'}), \
             mock.patch.object(ramp, 'changed', lambda: told.append(1)):
            self.client.get(f'/api/merge-orders/{order.id}/')
        self.assertEqual(told, [])


class SheetFullDiffTest(WorkRepo):
    """The merge sheet's "± See the full diff" (Justin, 9 Nov: "a link from the merge modal to see the full
    diff"): the candidate's page, in the ramp, at #diff."""

    def test_the_sheet_links_to_it_at_the_diff(self):
        from django.template.loader import render_to_string
        sheet = render_to_string('conversations/_merge_sheet.html')
        self.assertIn('<a class="full" href="#" hidden', sheet)
        self.assertIn("`/ramp/${encodeURIComponent(body.repo)}/${body.branch.split('/').map(encodeURIComponent).join('/')}/#diff`", sheet)
        self.assertIn("parent.postMessage({ magentaGo: to.pathname + to.hash }", sheet)  # from a frame: the one page opens it
        self.assertNotIn('/tidings/', sheet)


class CandidatePageTest(WorkRepo):
    """A candidate branch's page has its own address in the ramp (Justin, 9 Nov, #ramp: "why does it take me to
    the tidings page?"; Back to where it was opened from)."""

    def framed(self, path):
        return self.client.get(path, {'embed': 1}, HTTP_SEC_FETCH_DEST='iframe')

    def test_signed_in_it_is_made_when_first_asked_for(self):
        from conversations.models import WorkTiding
        self.as_('justin')
        got = self.framed('/ramp/magenta/re/foo/')
        self.assertEqual(got.status_code, 200)
        page = got.content.decode()
        self.assertIn('<title>magenta#7 · ramp · magenta</title>', page)
        self.assertIn('<a class="back" href="/ramp/">‹ the ramp</a>', page)
        self.assertEqual(WorkTiding.objects.filter(repo='magenta', branch='re/foo').count(), 1)
        self.assertEqual(self.framed('/ramp/magenta/re/foo/').status_code, 200)   # the same page, not a second
        self.assertEqual(WorkTiding.objects.filter(repo='magenta', branch='re/foo').count(), 1)

    def test_signed_out_without_a_page_nothing_is_made(self):
        from conversations.models import WorkTiding
        self.assertEqual(self.framed('/ramp/magenta/re/foo/').status_code, 404)
        self.assertFalse(WorkTiding.objects.exists())
        self.as_('justin')
        self.assertEqual(self.framed('/ramp/magenta/re/nothing/').status_code, 404)   # not open on the ramp

    def test_opened_on_its_own_it_is_the_one_page_on_the_ramp(self):
        got = self.client.get('/ramp/magenta/re/foo/', HTTP_SEC_FETCH_DEST='document')
        self.assertEqual(got.status_code, 200)
        self.assertIn('const initialSection = "ramp"', got.content.decode())

    def test_its_old_tidings_address_is_sent_here(self):
        self.as_('justin')
        tid = self.open_().json()['id']
        got = self.client.get(f'/tidings/{tid}/', HTTP_SEC_FETCH_DEST='document')
        self.assertEqual((got.status_code, got['Location']), (302, '/ramp/magenta/re/foo/'))

    def test_a_comment_line_opens_it_in_the_ramp(self):
        self.as_('justin')
        tiding = Tiding.objects.get(id=self.open_().json()['id'])
        tiding_links.comment(tiding, ThinkingEntity.objects.get(name='justin'), 'Looks right.')
        notice = Message.objects.get(source_file=tiding_links.COMMENT_SOURCE)
        self.assertEqual(notice.content['work'], '/ramp/magenta/re/foo/')

    def test_line_links_are_its_address(self):
        self.as_('justin')
        self.assertIn('`${location.origin}/ramp/magenta/re/foo/#${id}`', self.framed('/ramp/magenta/re/foo/').content.decode())

    def test_the_one_page_keeps_back_its_own(self):
        # The ramp's frame is moved with location.replace, never by a link inside it, so Back returns to where
        # the candidate was opened from: the ramp's list, or the Mood whose merge sheet it was.
        html = self.client.get('/moods/', HTTP_SEC_FETCH_DEST='document').content.decode()
        self.assertIn("try { frame.contentWindow.location.replace(want); } catch (e) { frame.src = want; }", html)
        self.assertIn("if (candidateAt(to.pathname)) { showSection('ramp', true, to.pathname, to.hash); return; }", html)
        self.assertIn("showSection('ramp', false, location.pathname, location.hash);", html)


class WorkPageNeverWaitsTest(WorkRepo):
    """Justin, 9 Nov: "± See the full diff" sat ~8 s on Tidings before the page came. A work's page asked for a
    fresh board, and rebuilding it takes seconds once it's a minute old. Now it reads the last one at once."""

    def setUp(self):
        super().setUp()
        self.as_('justin')
        self.tid = self.open_().json()['id']
        self.rebuilt = []
        p = mock.patch.object(ramp, '_rebuild', lambda: self.rebuilt.append(1))
        p.start()
        self.addCleanup(p.stop)

    def age(self, **item):
        board = ramp.board()
        if item:
            board['open'] = [{**i, **item} for i in board['open']]
        cache.set(ramp.KEY, {**board, 'built': time.time() - 3600}, None)   # an hour old

    def test_an_old_board_is_read_and_rebuilt_behind(self):
        self.age()
        asked = []
        with mock.patch.object(ramp, 'cards', lambda: asked.append(1) or self.cards):
            self.assertEqual(self.page(self.tid).status_code, 200)
        self.assertEqual(asked, [])          # no board was built while the page waited
        self.assertEqual(self.rebuilt, [1])  # one is, behind it

    def test_its_head_is_githouses_now_not_the_old_boards(self):
        self.age(head='0' * 40)              # the board's head, from before a push
        self.assertIn(self.head[:7], self.page(self.tid).content.decode())

    def test_a_branch_the_old_board_hasnt_seen_still_gets_its_page(self):
        self.age()
        old = cache.get(ramp.KEY)
        cache.set(ramp.KEY, {**old, 'open': []}, None)     # built before re/foo was pushed
        WorkTiding.objects.all().delete()
        self.assertEqual(self.open_().status_code, 200)    # asked a fresh board for it


class CandidatesTest(WorkRepo):
    """The open candidate branches, for each wake (Justin, 9 Nov): ramp.candidates and /api/ramp/candidates/."""

    def test_each_open_candidate_with_its_short_description(self):
        self.cards['open'][0]['description'] = ('The **foo** work: it makes [foo](https://example.org) faster.\n\n'
                                                 'More detail.\n\nDeploy: none\nWorked on by: re, in #ramp')
        [c] = ramp.candidates()
        self.assertEqual({k: c[k] for k in ('ref', 'branch', 'number', 'title', 'about', 'state')},
                         {'ref': 'magenta#7', 'branch': 're/foo', 'number': 7, 'title': 'the re/foo work',
                          'about': 'The foo work: it makes foo faster.', 'state': 'open'})

    def test_a_short_description(self):
        self.assertEqual(ramp.short_description('Deploy: none\nWorked on by: x'), '')
        self.assertEqual(ramp.short_description('Worked on by: x\n\nThe point.'), 'The point.')
        long = ramp.short_description('word ' * 100)
        self.assertEqual((len(long), long[-1]), (ramp.CANDIDATE_SAID, '…'))

    def test_only_for_a_runners_key(self):
        from django.test import override_settings
        with override_settings(MOOD_RUNNER_KEYS={'re': 'k' * 40}):
            self.assertEqual(self.client.get('/api/ramp/candidates/').status_code, 401)
            got = self.client.get('/api/ramp/candidates/', HTTP_AUTHORIZATION='Bearer ' + 'k' * 40)
        self.assertEqual(got.status_code, 200)
        self.assertEqual([c['ref'] for c in got.json()['candidates']], ['magenta#7'])


class RampWhoAndWhereTest(WorkRepo):
    """The ramp sorted by Mood, and a button for each person on it (Justin, 9 Nov, #ramp: "sortable by mood",
    "buttons to include/exclude each of the participants")."""

    def test_who_is_read_from_the_card_loosely_and_logins_are_people(self):
        got = ramp.people_of({'worked_on_by': 'magent, with skyler (testing on an iphone 17), justin; #uploads-and-embeds',
                              'author': 'magent-cryptograss'})
        self.assertEqual(got, ['magent', 'skyler', 'justin'])
        got = ramp.people_of({'worked_on_by': 'blu (author), magent (carried the patch, opened the pr), justin (the ask)',
                              'author': 'jMyles'})
        self.assertEqual(got, ['blu', 'magent', 'justin'])
        self.assertEqual(ramp.people_of({'worked_on_by': '', 'author': 'dependabot[bot]'}), ['dependabot[bot]'])
        self.assertEqual(ramp.people_of({'worked_on_by': 're and magent, in #ramp', 'author': 're'}), ['re', 'magent'])

    def test_where_is_its_moods_else_where_it_was_said(self):
        [carded, bare] = ramp.with_people([{'moods': ['ramp'], 'mentions': [{'mood': 'ops'}]},
                                           {'moods': [], 'mentions': [{'mood': 'ops'}, {'mood': 'ramp'}, {'mood': 'ops'}]}])
        self.assertEqual((carded['where'], bare['where']), (['ramp'], ['ops', 'ramp']))

    def test_the_page_has_a_button_each_and_each_row_says_who_and_where(self):
        ThinkingEntity.objects.get_or_create(name='magent', defaults={'is_biological_human': False})
        cache.delete(ramp.KEY)
        page = self.client.get('/ramp/', {'embed': 1}, HTTP_SEC_FETCH_DEST='iframe').content.decode()
        self.assertIn('data-mood="ramp" data-people="magent re"', page)
        self.assertIn('<button type="button" data-person="magent" data-state=""><span class="dot" style="--who: #ff67fd">'
                      '</span>magent<small>1</small></button>', page)
        self.assertIn('<button type="button" data-person="re" data-state=""><span class="dot" style="--who: #ff3c00"></span>'
                      're<small>1</small></button>', page)
        self.assertIn('data-sort="mood"', page)


class RampBlocksAndHeadsTest(WorkRepo):
    """'pushed' in blocks ago, and the column heads sort (Justin, 9 Nov, #ramp)."""

    def page(self):
        cache.delete(ramp.KEY)
        return self.client.get('/ramp/', {'embed': 1}, HTTP_SEC_FETCH_DEST='iframe').content.decode()

    def test_pushed_is_blocks_ago_from_the_anchors(self):
        from conversations.models import BlockAnchor
        now = int(time.time())
        BlockAnchor.objects.create(number=26_380_000, timestamp=now - 7200 * 12 - 60)   # a day and a minute ago
        BlockAnchor.objects.create(number=26_387_205, timestamp=now - 60)                  # a minute ago: fresh
        # re/foo's head was committed just now, after the newest anchor: counted in 12-second slots from it.
        cache.delete(ramp.KEY)
        items = ramp.with_people(ramp.board()['open'])
        top = ramp.with_blocks(items)
        [item] = items
        self.assertEqual(top, 26_387_210)
        self.assertLessEqual(item['pushed_ago'], 6)
        self.assertRegex(self.page(), r'<span title="block 263872\d\d, [^"]+Z">(this block|\d blocks? ago)</span>')
        item['pushed'] = item['pushed'] - timedelta(days=1)                                   # a day earlier
        ramp.with_blocks(items)
        self.assertEqual(item['pushed_ago_said'][:2], '7,')                                    # about 7,200

    def test_without_anchors_the_block_bars_head_is_used(self):
        from datetime import datetime, timezone as tz
        cache.set('eth-head', {'height': 26_380_223, 'at': datetime.now(tz.utc).strftime('%Y-%m-%dT%H:%M:%S.000000Z'),
                               'seconds': 12}, 60)
        items = ramp.with_people([{'pushed': timezone.now() - timedelta(hours=1)}])
        self.assertEqual(ramp.with_blocks(items), 26_380_223)
        self.assertEqual(items[0]['pushed_ago'], 300)

    def test_without_a_fresh_anchor_it_says_the_time(self):
        page = self.page()
        self.assertNotIn('blocks ago', page)
        self.assertRegex(page, r'<td class="num">\d+ \w+ \d\d:\d\dZ</td>')

    def test_the_heads_sort(self):
        page = self.page()
        self.assertNotIn('class="sorts"', page)
        for key, words in (('active', '●'), ('mood', 'where'), ('pushed', 'pushed'), ('mentioned', 'last said')):
            self.assertRegex(page, rf'<button type="button" class="sort" data-sort="{key}" aria-pressed="(true|false)" title="[^"]+">{words}</button>')


class MergeBarAndFreshRampTest(WorkRepo):
    """Justin, 9 Nov, #ramp: merged from the ramp, it "still shows that CB in the list as unmerged"; and Merge now
    sends the sheet away, to a bar below the nav that ends in a green check or a red x."""

    def test_a_merge_marks_the_board_old_and_builds_another(self):
        board = ramp.board()
        self.assertGreater(board['built'], 0)
        built = []
        with mock.patch.object(ramp, '_rebuild', lambda: built.append(1)):
            ramp.changed()
        self.assertEqual(cache.get(ramp.KEY)['built'], 0)   # the ramp page won't take it as fresh
        self.assertEqual(built, [1])
        self.assertEqual(ramp.board(stale_ok=True)['open'], board['open'])   # a stale_ok caller still gets it at once

    def test_announced_merges_tell_the_ramp(self):
        from conversations.services import merge_orders
        order = MergeOrder.objects.create(repo='magenta', branch='re/foo', by='justin', mood='ramp', order='o',
                                          signature='s', nonce='n1', expires=int(time.time()) + 600)
        told = []
        with mock.patch.object(merge_orders, 'outcome', lambda o: {'status': 'merged', 'merge': 'a' * 40}), \
             mock.patch.object(merge_orders, 'parse', lambda text: {'into': 'main'}), \
             mock.patch.object(ramp, 'changed', lambda: told.append(1)):
            self.assertEqual(merge_orders.announce_merged(), 1)
            self.assertEqual(merge_orders.announce_merged(), 0)   # said once; nothing new to tell
        self.assertEqual(told, [1])
        self.assertIsNotNone(MergeOrder.objects.get(id=order.id).announced_at)

    def test_the_sheet_goes_to_the_bar_and_says_merged(self):
        from django.template.loader import render_to_string
        sheet = render_to_string('conversations/_merge_sheet.html')
        self.assertIn(".mergeBar .item.ok .mark::before { content: '✓'; }", sheet)
        self.assertIn(".mergeBar .item.err .mark::before { content: '✗'; }", sheet)
        self.assertIn('s.away = true; s.el.hidden = true;', sheet)   # Merge now: the sheet goes at once
        self.assertIn("window.dispatchEvent(new CustomEvent('magentaMerged', { detail: merged }));", sheet)
        page = self.client.get('/ramp/', {'embed': 1}, HTTP_SEC_FETCH_DEST='iframe').content.decode()
        self.assertIn("window.addEventListener('magentaMerged', e => {", page)
        # On every page (Justin, 10 Nov): a press in a section's frame writes its line in the one page's bar.
        self.assertIn('up.magentaMerge && up.magentaMerge.barLine) return up.magentaMerge;', sheet)
        self.assertIn('const line = (topBar() || { barLine }).barLine(', sheet)


class GithouseMergedTest(WorkRepo):
    """'Merged lately' lists every branch githouse merged, not only pull requests (Justin, 10 Nov)."""

    def merge_by_githouse(self, branch, head):
        from conversations.tests.test_githouse import git
        git(self.repo.work, 'checkout', '-q', 'main')
        git(self.repo.work, 'reset', '-q', '--hard', self.base)
        git(self.repo.work, 'merge', '-q', '--no-ff', '-m', f'Merge magenta:{branch}', head)
        merge = git(self.repo.work, 'rev-parse', 'HEAD')
        self.repo.ref('refs/heads/main', merge)
        self.repo.ref(f'refs/merged/{branch}', head)
        git(self.repo.bare, 'update-ref', '-d', f'refs/heads/{branch}')
        return merge

    def test_a_branch_githouse_merged_is_listed_with_its_merge(self):
        self.cards['open'] = []
        merge = self.merge_by_githouse('re/foo', self.head)
        cache.delete(ramp.KEY)
        [m] = ramp.board()['merged']
        self.assertEqual((m['repo'], m['branch'], m['number'], m['merge'], m['base'], m['title']),
                         ('magenta', 're/foo', None, merge, 'main', 'the foo work'))
        self.assertIsNotNone(m['merged'])
        page = self.client.get('/ramp/', {'embed': 1}, HTTP_SEC_FETCH_DEST='iframe').content.decode()
        merged_table = page.split('<h2>Merged lately</h2>')[1].split('</table>')[0]
        self.assertIn('<span class="ref">magenta:re/foo</span>', merged_table)
        self.assertIn(f'→ main as {merge[:8]}', merged_table)

    def test_one_with_a_pull_request_card_is_listed_once(self):
        self.cards['merged'] = [dict(self.cards['open'][0], merged='2026-11-09 20:00:00')]
        self.cards['open'] = []
        self.merge_by_githouse('re/foo', self.head)
        cache.delete(ramp.KEY)
        merged = ramp.board()['merged']
        self.assertEqual([(m['branch'], m['number']) for m in merged], [('re/foo', 7)])

    def test_its_page_is_found_by_branch(self):
        from conversations.services import work_pages
        self.as_('justin')
        tid = self.open_().json()['id']
        [item] = work_pages.with_merged_pages([{'repo': 'magenta', 'branch': 're/foo', 'number': None}])
        self.assertEqual(item['page'], {'id': tid})


class SheetSaysWhatItIsTest(WorkRepo):
    """Justin, 10 Nov (#ramp): "in the merge modal, show me the short description and the first ~200 characters of
    the full description -- this is more important than the commit messages; save those for the full branch page"."""

    def preview(self, branch):
        self.as_('justin')
        return self.client.get('/api/merge-orders/preview/', {'repo': 'magenta', 'branch': branch}).json()

    def test_its_title_and_the_start_of_its_description(self):
        self.cards['open'][0]['description'] = ('The **foo** work: ' + 'words ' * 60 + '\n\nDeploy: redeploy maybelle\n'
                                                'Worked on by: magent, in #ramp')
        ramp.board()
        seen = self.preview('re/foo')
        self.assertEqual(seen['title'], 'the re/foo work')
        self.assertTrue(seen['about'].startswith('The foo work: words words'))   # plain words, no markup
        self.assertLessEqual(len(seen['about']), 200)
        self.assertTrue(seen['about'].endswith('words…'))                          # cut on a word
        self.assertNotIn('Deploy', seen['about'])

    def test_without_a_description_its_newest_commit_says(self):
        self.cards['open'] = []   # a branch with no pull request: no description of its own
        ramp.board()
        seen = self.preview('re/foo')
        self.assertEqual((seen['title'], seen['about']), ('the foo work', 'Why: because.'))

    def test_the_sheet_shows_them_and_not_the_log(self):
        from django.template.loader import render_to_string
        sheet = render_to_string('conversations/_merge_sheet.html')
        self.assertIn('<div class="about" hidden><b class="title"></b><span class="words"></span></div>', sheet)
        self.assertNotIn('class="log"', sheet)
        self.assertIn('<pre class="stat" hidden></pre>', sheet)   # what it changes, still
