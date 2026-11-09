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

from conversations.models import MergeOrder, Message, Mood, ThinkingEntity, Thumb, Tiding, TidingComment
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

    def test_its_work_line_says_which_work(self):
        work = next(e for e in self.events() if e['type'] == 'work')
        self.assertEqual(work['ref'], 'magenta#7')

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
        return self.client.get('/moods/pulls/', {'embed': 1}, HTTP_SEC_FETCH_DEST='iframe').content.decode()

    def test_the_works_name_opens_its_page(self):
        self.as_('justin')
        self.assertIn('class="ref openpage" href="https://github.com/cryptograss/magenta/pull/7" data-repo="magenta" '
                      'data-branch="re/foo"', self.ramp_page())
        tid = self.open_().json()['id']
        page = self.ramp_page()
        self.assertIn(f'<a class="ref" href="/moods/tidings/{tid}/"', page)
        self.assertIn('<a class="gh" href="https://github.com/cryptograss/magenta/pull/7"', page)

    def test_signed_out_it_is_still_githubs(self):
        page = self.ramp_page()
        self.assertIn('<a class="ref" href="https://github.com/cryptograss/magenta/pull/7" target="_blank"', page)
        self.assertNotIn('class="ref openpage"', page)

    def test_merged_work_links_its_page(self):
        self.as_('justin')
        tid = self.open_().json()['id']
        self.cards['merged'] = [dict(self.cards['open'][0])]
        self.assertIn(f'<a class="ref" href="/moods/tidings/{tid}/" title="Its page: the work, its diff, and what was said">'
                      'magenta#7</a>', self.ramp_page())

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
