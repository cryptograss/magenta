"""Pull requests' cards on PickiPedia: VersionControl:PRs/<repo>/<number>, one {{PR}} each."""

from datetime import datetime, timezone
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings

from conversations.services import pr_cards
from conversations.tests.test_wiki_ledger import Wiki

BOT = {'PICKIPEDIA_TODO_BOT_USER': 'DrivingThatTrain@magenta-todo', 'PICKIPEDIA_TODO_BOT_PASSWORD': 'not-a-password'}
NOW = datetime(2026, 10, 31, 5, 0, tzinfo=timezone.utc)

FOOT = """Bumps the pin.

Deploy: redeploy hunter
Worked on by: magent, re, Justin, in #ops and #ramp"""


def pull(number, repo='cryptograss/maybelle-config', state='open', body=FOOT, merged_at=None, closed_at=None,
         title='Bump PICKIPEDIA_MCP_COMMIT', draft=False):
    return {'number': number, 'title': title, 'body': body, 'state': state, 'draft': draft,
            'html_url': f'https://github.com/{repo}/pull/{number}', 'user': {'login': 'magent-cryptograss'},
            'created_at': '2026-10-30T22:08:00Z', 'merged_at': merged_at, 'closed_at': closed_at,
            'base': {'ref': 'production', 'repo': {'full_name': repo}},
            'head': {'ref': 'bump-pickipedia-mcp', 'sha': f'sha{number}'}}


class GitHub:
    """GitHub's API, as far as the cards ask it."""

    def __init__(self, repos, pulls):
        self.repos, self.pulls, self.asked = repos, pulls, []

    def get(self, url, params=None, headers=None, timeout=None):
        path = url.replace('https://api.github.com', '')
        self.asked.append(path)
        if path.startswith('/orgs/'):
            found = self.repos
        elif '/commits/' in path:
            found = {'commit': {'committer': {'date': '2026-10-30T22:07:00Z'}}}
        elif path.endswith('/pulls'):
            found = self.pulls.get(path.split('/repos/')[1].rsplit('/pulls')[0], [])
        else:
            number = int(path.rsplit('/', 1)[1])
            found = next((p for ps in self.pulls.values() for p in ps if p['number'] == number), None)
        return mock.Mock(status_code=200 if found is not None else 404, json=lambda: found)


class FootTest(TestCase):

    def test_the_general_orders_foot(self):
        self.assertEqual(pr_cards.foot(FOOT), ('redeploy hunter', ['magent', 're', 'justin'], ['ops', 'ramp']))

    def test_moods_without_hashes_and_one_person(self):
        self.assertEqual(pr_cards.foot('Deploy: none\nWorked on by: re, in ramp'), ('none', ['re'], ['ramp']))
        self.assertEqual(pr_cards.foot('**Deploy:** redeploy maybelle\n**Worked on by:** magent and Justin in #interfaces'),
                         ('redeploy maybelle', ['magent', 'justin'], ['interfaces']))

    def test_no_foot(self):
        self.assertEqual(pr_cards.foot('Just a fix.'), ('', [], []))
        self.assertEqual(pr_cards.foot(None), ('', [], []))

    def test_the_last_foot_is_the_foot(self):
        body = 'Quoting an old one:\n> Worked on by: blu, in #jams\n\nDeploy: none\nWorked on by: re, in #ramp'
        self.assertEqual(pr_cards.foot(body), ('none', ['re'], ['ramp']))


class CardTest(TestCase):

    def test_titles(self):
        self.assertEqual(pr_cards.title('cryptograss', 'magenta', 295), 'VersionControl:PRs/magenta/295')
        self.assertEqual(pr_cards.title('ProfessionalWiki', 'MediaWiki-MCP-Server', 9),
                         'VersionControl:PRs/ProfessionalWiki/MediaWiki-MCP-Server/9')

    def test_a_card_says_what_github_does(self):
        text = pr_cards.card(pull(243, title='Pin | it {{x}}'), pushed='2026-10-30T22:07:00Z')
        self.assertTrue(text.startswith('{{PR\n') and text.endswith('}}\n'))
        for line in ('|repo=maybelle-config', '|number=243', '|title=Pin &#124; it &#123;&#123;x&#125;&#125;', '|state=open', '|base=production',
                     '|pushed=2026-10-30 22:07:00', '|worked_on_by=magent, re, justin', '|moods=ops, ramp',
                     '|deploy=redeploy hunter', '|merged=', '|author=magent-cryptograss'):
            self.assertIn(line + '\n', text)

    def test_states(self):
        self.assertEqual(pr_cards.state(pull(1)), 'open')
        self.assertEqual(pr_cards.state(pull(1, draft=True)), 'draft')
        self.assertEqual(pr_cards.state(pull(1, state='closed', merged_at='2026-10-30T23:00:00Z')), 'merged')
        self.assertEqual(pr_cards.state(pull(1, state='closed', closed_at='2026-10-30T23:00:00Z')), 'closed')


@override_settings(**BOT)
class LookTest(TestCase):

    def setUp(self):
        cache.clear()
        fresh = {'full_name': 'cryptograss/maybelle-config', 'pushed_at': '2026-10-30T22:08:00Z'}
        stale = {'full_name': 'cryptograss/old-thing', 'pushed_at': '2025-01-01T00:00:00Z'}
        self.github = GitHub([fresh, stale], {'cryptograss/maybelle-config': [
            pull(243),
            pull(240, state='closed', merged_at='2026-10-29T00:00:00Z', closed_at='2026-10-29T00:00:00Z'),
            pull(100, state='closed', merged_at='2026-08-01T00:00:00Z', closed_at='2026-08-01T00:00:00Z'),
        ]})

    def test_open_and_lately_closed_get_cards_and_stale_repos_arent_asked(self):
        wiki = Wiki()
        done = pr_cards.look(http=self.github, wiki=wiki, now=NOW)
        self.assertEqual(done, {'VersionControl:PRs/maybelle-config/243': 'written',
                                'VersionControl:PRs/maybelle-config/240': 'written'})
        self.assertNotIn('/repos/cryptograss/old-thing/pulls', self.github.asked)
        self.assertIn('|state=merged\n', wiki.pages['VersionControl:PRs/maybelle-config/240'])
        self.assertTrue(all(e.get('bot') == 1 for e in wiki.edits))

    def test_a_second_look_changes_nothing(self):
        wiki = Wiki()
        pr_cards.look(http=self.github, wiki=wiki, now=NOW)
        edits = len(wiki.edits)
        self.assertEqual(set(pr_cards.look(http=self.github, wiki=wiki, now=NOW).values()), {'same'})
        self.assertEqual(len(wiki.edits), edits)

    def test_an_edit_on_the_wiki_is_written_over(self):
        wiki = Wiki({'VersionControl:PRs/maybelle-config/243': '{{PR|number=243|state=merged}}'})
        pr_cards.look(http=self.github, wiki=wiki, now=NOW)
        self.assertIn('|state=open\n', wiki.pages['VersionControl:PRs/maybelle-config/243'])

    def test_no_login_no_writing(self):
        wiki = Wiki(login='Failed')
        self.assertEqual(pr_cards.look(http=self.github, wiki=wiki, now=NOW), {})
        self.assertEqual(wiki.edits, [])

    def test_one_as_the_webhook_asks(self):
        wiki = Wiki()
        self.assertEqual(pr_cards.one('cryptograss', 'maybelle-config', 243, http=self.github, wiki=wiki), 'written')
        self.assertEqual(list(wiki.pages), ['VersionControl:PRs/maybelle-config/243'])

    @override_settings(PICKIPEDIA_TODO_BOT_USER='')
    def test_no_bot_no_nudge(self):
        self.assertFalse(pr_cards.nudge())
