"""magenta.sh merge (tools/mood_merge.py) against a stand-in GitHub and a stand-in memory-lane.

re's test (7 Nov): what the signer is shown is the log and diffstat the tool computed from the commits it
fetched, never text a service handed it, and the commit it signs for is the one it showed."""

import importlib.util
import io
import os
import subprocess
import tempfile
import urllib.parse
from pathlib import Path
from unittest import TestCase, skipUnless

from conversations.tests.test_mood_auth import HAS_SSH_KEYGEN, make_key

spec = importlib.util.spec_from_file_location('mood_merge', 'tools/mood_merge.py')
mood_merge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mood_merge)

ENV = {**os.environ, 'GIT_AUTHOR_NAME': 're', 'GIT_AUTHOR_EMAIL': 're@x', 'GIT_COMMITTER_NAME': 're',
       'GIT_COMMITTER_EMAIL': 're@x', 'GIT_CONFIG_GLOBAL': '/dev/null'}
LIES = 'A SERVICE SAID THIS'


def run(*args, cwd=None):
    return subprocess.run(args, cwd=cwd, env=ENV, capture_output=True, text=True, check=True).stdout


@skipUnless(HAS_SSH_KEYGEN, 'needs ssh-keygen')
class MergeToolTest(TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.github = self.tmp / 'cryptograss' / 'magenta.git'
        run('git', 'init', '--quiet', '--bare', '-b', 'main', str(self.github))
        work = self.tmp / 'work'
        run('git', 'clone', '--quiet', str(self.github), str(work))
        (work / 'a.txt').write_text('one\n')
        run('git', 'add', '.', cwd=work)
        run('git', 'commit', '--quiet', '-m', 'one', cwd=work)
        run('git', 'push', '--quiet', 'origin', 'HEAD:main', 'HEAD:production', cwd=work)
        (work / 'fix.py').write_text('print("the fix")\n')
        run('git', 'add', '.', cwd=work)
        run('git', 'commit', '--quiet', '-m', 'the real fix', cwd=work)
        run('git', 'push', '--quiet', 'origin', 'HEAD:re/foo', cwd=work)
        self.head = run('git', 'rev-parse', 'HEAD', cwd=work).strip()
        self.key = make_key(str(self.tmp), 'justin')
        self.sent, self.answers = [], []
        # memory-lane, played here: it offers a log and a diff of its own, which must never be shown.
        def call(url, payload=None):
            self.sent.append((url, payload))
            if '/whose/' in url:
                return {'name': 'justin', 'log': LIES, 'stat': LIES}
            if payload is not None:
                return {'id': '0123456789abcdef', 'status': 'waiting', 'log': LIES, 'diff': LIES}
            return self.answers.pop(0) if self.answers else {'status': 'merged', 'merge': 'f' * 40, 'log': LIES}
        original = mood_merge.call
        mood_merge.call = call
        self.addCleanup(setattr, mood_merge, 'call', original)

    def merge(self, *args, answer='y'):
        out, asked = io.StringIO(), []
        def ask(question):
            asked.append(out.getvalue())  # everything shown before the question
            return answer
        code = mood_merge.main(['magenta:re/foo', '--key', self.key, '--base', 'http://ml', *args], out=out, ask=ask,
                               source=str(self.tmp / 'cryptograss') + '/{}.git', pause=lambda s: None)
        return code, out.getvalue(), (asked[0] if asked else '')

    def test_it_shows_what_it_computed_and_signs_that_commit(self):
        code, said, shown = self.merge()
        self.assertEqual(code, 0, said)
        self.assertIn('re: the real fix', shown)
        self.assertIn('fix.py | 1 +', shown)
        self.assertIn('into main', shown)  # magenta deploys from main; its production branch is long dead
        self.assertNotIn(LIES, said)
        url, posted = self.sent[1]
        self.assertEqual(url, 'http://ml/api/merge-orders/')
        fields = dict(line.split(': ', 1) for line in posted['order'].splitlines()[1:])
        self.assertEqual((fields['commit'], fields['into'], fields['by'], fields['repo'], fields['branch']),
                         (self.head, 'main', 'justin', 'magenta', 're/foo'))
        # A real signature, in the merge namespace, as githouse will check it.
        signers = self.tmp / 'signers'
        signers.write_text(f'justin namespaces="magenta-merge" {Path(self.key + ".pub").read_text().strip()}\n')
        sig = self.tmp / 'order.sig'
        sig.write_text(posted['signature'])
        checked = subprocess.run(['ssh-keygen', '-Y', 'verify', '-f', str(signers), '-I', 'justin', '-n',
                                  'magenta-merge', '-s', str(sig)], input=posted['order'], capture_output=True, text=True)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertIn('Merged', said)

    def test_no_means_nothing_signed_or_sent(self):
        code, said, _ = self.merge(answer='n')
        self.assertEqual(code, 1)
        public = Path(self.key + '.pub').read_text().strip()
        self.assertEqual([url for url, _ in self.sent],
                         ['http://ml/api/merge-orders/whose/?' + urllib.parse.urlencode({'key': public})])
        self.assertIn('Nothing signed', said)

    def test_githouse_s_refusal_is_said(self):
        self.answers = [{'status': 'waiting'}, {'status': 'refused', 'why': 'justin: banned'}]
        code, said, _ = self.merge('--yes', '--as', 'justin')
        self.assertEqual(code, 1)
        self.assertIn('Refused: justin: banned', said)

    def test_into_another_branch_and_what_isn_t_there(self):
        code, said, shown = self.merge('--into', 'production')
        self.assertIn('into production', shown)
        code, said, _ = self.merge('--into', 'nonesuch')
        self.assertIn('no branch nonesuch', said)
        self.assertEqual(code, 1)

    def test_nothing_in_a_commit_or_from_memory_lane_can_redraw_the_screen(self):
        work = self.tmp / 'work'
        (work / 'more.py').write_text('x = 1\n')
        run('git', 'add', '.', cwd=work)
        subprocess.run(['git', 'commit', '--quiet', '-m', 'tidy\x1b[2A\x1b[2K\x9b8m'], cwd=work, check=True,
                       env={**ENV, 'GIT_AUTHOR_NAME': 'evil\x1b[1A'})
        run('git', 'push', '--quiet', 'origin', 'HEAD:re/foo', cwd=work)
        code, said, shown = self.merge(answer='n')
        self.assertEqual(code, 1, said)
        self.assertIn('tidy?[2A?[2K?8m', shown)
        self.assertNotRegex(said, '[\x1b\x9b]')
        original = mood_merge.call
        mood_merge.call = lambda url, payload=None: {'name': 'justin\x1b[3A'}
        try:
            code, said, _ = self.merge()
        finally:
            mood_merge.call = original
        self.assertEqual(code, 1)
        self.assertIn('--as', said)
        self.assertNotIn('\x1b', said)
