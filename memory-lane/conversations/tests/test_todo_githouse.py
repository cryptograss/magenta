"""Justin, 10 Nov (#ramp): "I merged magenta:magent/branch-links and it didn't get removed from the todo ... We
need to push the merged / wontfixed status from githouse to the moods, so that todos and asks get resolved."
A to-do item linking a candidate branch's page ticks itself when githouse merges the branch, or closes it; an ask
naming a closed branch is answered; a merge githouse announces drops every kept list at once."""

import tempfile
import time
from pathlib import Path
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings

from conversations.models import MergeOrder, Mood
from conversations.services import githouse, todo
from conversations.tests.test_githouse import Repo, git
from conversations.tests.test_todo import FakeWiki

TITLE = 'Cryptograss:Moods/ramp/todo'
PAGE = """<todo>
- task: "Merge magenta:re/merged-one"
  who: justin
  kind: merge
  link: https://magenta.cryptograss.live/ramp/magenta/re/merged-one/
- task: "Merge magenta:re/closed-one"
  who: justin
  kind: merge
  link: https://magenta.cryptograss.live/ramp/magenta/re/closed-one/
- task: "Merge magenta:re/live-one"
  who: justin
  kind: merge
  link: https://magenta.cryptograss.live/ramp/magenta/re/live-one/
- task: "Merge arthel:re/elsewhere"
  who: justin
  kind: merge
  link: https://magenta.cryptograss.live/ramp/arthel/re/elsewhere/
- task: "Redeploy hunter: magenta:re/merged-one changes the poller"
  who: justin
  kind: deploy
  link: https://magenta.cryptograss.live/ramp/
</todo>"""


class GithouseRefsTest(TestCase):

    def setUp(self):
        cache.clear()
        self.mount = Path(tempfile.mkdtemp())
        patch = override_settings(GITHOUSE_DIR=str(self.mount))
        patch.enable()
        self.addCleanup(patch.disable)
        self.repo = Repo(self.mount)
        sha = self.repo.commit('work')
        self.repo.ref('refs/merged/re/merged-one', sha)
        self.repo.ref('refs/closed/re/closed-one', sha)
        self.repo.ref('refs/heads/re/live-one', sha)


class BranchClosedTest(GithouseRefsTest):

    def test_closed_is_its_name_in_refs_closed(self):
        self.assertIs(githouse.branch_closed('magenta', 're/closed-one'), True)
        self.assertIs(githouse.branch_closed('magenta', 're/merged-one'), False)
        self.assertIs(githouse.branch_closed('magenta', 're/live-one'), False)
        self.assertIsNone(githouse.branch_closed('arthel', 're/x'))          # a repository githouse doesn't hold

    def test_closed_is_final_and_kept_like_merged(self):
        # githouse never takes a used name again, so a closed answer isn't asked of git again within the day.
        self.assertIs(githouse.branch_closed('magenta', 're/closed-one'), True)
        git(self.repo.bare, 'update-ref', '-d', 'refs/closed/re/closed-one')
        self.assertIs(githouse.branch_closed('magenta', 're/closed-one'), True)
        self.assertIs(githouse.branch_closed('magenta', 're/live-one'), False)   # a not-yet: asked again in a minute


class TodoTicksByBranchTest(GithouseRefsTest):

    def setUp(self):
        super().setUp()
        self.mood = Mood.objects.create(slug='ramp', title='ramp')
        self.ticks = []
        patch = mock.patch('conversations.services.todo_ticks.tick_later', lambda title, link: self.ticks.append(link))
        patch.start()
        self.addCleanup(patch.stop)

    def items(self):
        return {i['task']: i for i in todo.for_mood(self.mood, http=FakeWiki({TITLE: PAGE}))['items']}

    def test_merged_and_closed_tick_live_and_unknown_stand(self):
        items = self.items()
        merged, closed = items['Merge magenta:re/merged-one'], items['Merge magenta:re/closed-one']
        self.assertEqual((merged['done'], merged.get('merged'), merged.get('closed')), (True, True, None))
        self.assertEqual((closed['done'], closed.get('closed'), closed.get('merged')), (True, True, None))
        self.assertFalse(items['Merge magenta:re/live-one']['done'])
        self.assertFalse(items['Merge arthel:re/elsewhere']['done'])   # githouse holds no arthel here: as written
        # Naming a merged branch in its words isn't linking its page: a redeploy isn't done by a merge.
        self.assertFalse(items['Redeploy hunter: magenta:re/merged-one changes the poller']['done'])

    def test_both_are_ticked_on_the_wiki_too(self):
        self.items()
        self.assertEqual(sorted(self.ticks), ['https://magenta.cryptograss.live/ramp/magenta/re/closed-one/',
                                              'https://magenta.cryptograss.live/ramp/magenta/re/merged-one/'])

    def test_a_githouse_merge_announced_drops_the_kept_lists(self):
        from conversations.services import merge_orders
        self.assertFalse(self.items()['Merge magenta:re/live-one']['done'])
        # githouse merges re/live-one: its name moves to refs/merged/ ...
        sha = git(self.repo.work, 'rev-parse', 'HEAD')
        git(self.repo.bare, 'update-ref', '-d', 'refs/heads/re/live-one')
        self.repo.ref('refs/merged/re/live-one', sha)
        # githouse's "not yet" from the look above is still kept (a minute): the list is the kept one too.
        self.assertFalse(self.items()['Merge magenta:re/live-one']['done'])
        # ... and memory-lane announces it: the list is asked again at once.
        MergeOrder.objects.create(repo='magenta', branch='re/live-one', by='justin', mood='ramp', order='o',
                                  signature='s', nonce='n1', expires=int(time.time()) + 600)
        with mock.patch.object(merge_orders, 'outcome', lambda o: {'status': 'merged', 'merge': 'a' * 40}), \
             mock.patch.object(merge_orders, 'parse', lambda text: {'into': 'main'}), \
             mock.patch('conversations.services.ramp.changed', lambda: None):
            self.assertEqual(merge_orders.announce_merged(), 1)
        self.assertTrue(self.items()['Merge magenta:re/live-one']['merged'])


class AskClosedTest(GithouseRefsTest):

    def test_an_ask_for_a_closed_branch_is_answered_and_shows_no_merge(self):
        from conversations.services.tools_ask import _merged_elsewhere, unmerged
        self.assertTrue(_merged_elsewhere('magenta:re/closed-one'))
        self.assertTrue(_merged_elsewhere('magenta:re/merged-one and magenta:re/closed-one'))
        self.assertFalse(_merged_elsewhere('magenta:re/closed-one and magenta:re/live-one'))
        self.assertEqual(unmerged('magenta:re/closed-one, then magenta:re/live-one'),
                         [{'repo': 'magenta', 'branch': 're/live-one', 'thumbs': []}])
