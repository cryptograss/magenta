"""A save puts its row on Magenta:Clips, and a forget takes it off (services/clips.page_with, write_page)."""
from unittest import mock

from django.test import SimpleTestCase

from conversations.services import clips

PAGE = """Intro.

{| class="wikitable"
! name !! link !! what it's for
|-
| huh || https://www.yarn.co/yarn-clip/ffb40a1a-a936-49ee-962a-ef53e0cb7237 || Tony Clifton's "huh".
|-
| roger || https://magenta.cryptograss.live/clips/old || Copy that.
|}
"""


class PageRows(SimpleTestCase):
    def test_a_new_clip_is_a_row_last_in_the_table_with_no_use_yet(self):
        new = clips.page_with(PAGE, 'all-individuals', 'https://magenta.cryptograss.live/clips/abc')
        self.assertTrue(new.endswith('| roger || https://magenta.cryptograss.live/clips/old || Copy that.\n|-\n'
                                     '| all-individuals || https://magenta.cryptograss.live/clips/abc || \n|}\n'))
        self.assertEqual(clips.page_rows(new)['all-individuals'], {'link': 'https://magenta.cryptograss.live/clips/abc', 'for': ''})

    def test_saved_again_its_link_changes_and_its_use_stays(self):
        new = clips.page_with(PAGE, 'roger', 'https://magenta.cryptograss.live/clips/new')
        self.assertEqual(clips.page_rows(new)['roger'], {'link': 'https://magenta.cryptograss.live/clips/new', 'for': 'Copy that.'})
        self.assertEqual(new.count('roger'), 1)

    def test_the_same_link_changes_nothing(self):
        self.assertEqual(clips.page_with(PAGE, 'roger', 'https://magenta.cryptograss.live/clips/old'), PAGE)

    def test_no_table_no_row(self):
        self.assertIsNone(clips.page_with('No table here.', 'roger', 'x'))

    def test_forgotten_its_row_and_divider_go(self):
        new = clips.page_without(PAGE, 'roger')
        self.assertNotIn('roger', new)
        self.assertIn('Tony Clifton\'s "huh".\n|}', new)
        self.assertEqual(clips.page_without(PAGE, 'nothing'), PAGE)


class WritePage(SimpleTestCase):
    def bot(self, text=PAGE, result='Success'):
        bot = mock.Mock()
        bot.login.return_value = True
        bot.read.return_value = {'revid': 7, 'timestamp': 't', 'slots': {'main': {'content': text}}}
        bot.edit.return_value = {'edit': {'result': result}}
        return bot

    def test_a_save_writes_its_row(self):
        bot = self.bot()
        self.assertEqual(clips.write_page('all-individuals', 'https://magenta.cryptograss.live/clips/abc', 'justin', bot=bot), 'written')
        title, text, summary, revision = bot.edit.call_args.args
        self.assertEqual(title, 'Magenta:Clips')
        self.assertIn('| all-individuals || https://magenta.cryptograss.live/clips/abc || ', text)
        self.assertIn('justin saved "all-individuals"', summary)

    def test_already_there_no_edit(self):
        bot = self.bot()
        self.assertEqual(clips.write_page('roger', 'https://magenta.cryptograss.live/clips/old', 'justin', bot=bot), 'same')
        bot.edit.assert_not_called()

    def test_a_forget_takes_it_off(self):
        bot = self.bot()
        self.assertEqual(clips.write_page('roger', None, 'justin', bot=bot), 'written')
        self.assertNotIn('roger', bot.edit.call_args.args[1])

    def test_only_saves_and_forgets_go_to_the_page(self):
        with mock.patch('conversations.services.mood_cache.spawn') as spawn:
            clips.page_later('/clips save roger https://magenta.cryptograss.live/clips/new', 'justin')
            clips.page_later('/clips forget roger', 'justin')
            clips.page_later('/clips roger https://magenta.cryptograss.live/clips/new', 'justin')
        self.assertEqual([c.args[1:] for c in spawn.call_args_list],
                         [('roger', 'https://magenta.cryptograss.live/clips/new', 'justin'), ('roger', None, 'justin')])
