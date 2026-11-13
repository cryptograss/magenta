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
| roger || https://magenta.cryptograss.live/clips/11111111-1111-4111-8111-111111111111 || Copy that.
|}
"""


class PageRows(SimpleTestCase):
    def test_a_new_clip_is_a_row_last_in_the_table_with_no_use_yet(self):
        new = clips.page_with(PAGE, 'all-individuals', 'https://magenta.cryptograss.live/clips/33333333-3333-4333-8333-333333333333')
        self.assertTrue(new.endswith('| roger || https://magenta.cryptograss.live/clips/11111111-1111-4111-8111-111111111111 || Copy that.\n|-\n'
                                     '| all-individuals || https://magenta.cryptograss.live/clips/33333333-3333-4333-8333-333333333333 || \n|}\n'))
        self.assertEqual(clips.page_rows(new)['all-individuals'], {'link': 'https://magenta.cryptograss.live/clips/33333333-3333-4333-8333-333333333333', 'for': ''})

    def test_saved_again_its_link_changes_and_its_use_stays(self):
        new = clips.page_with(PAGE, 'roger', 'https://magenta.cryptograss.live/clips/22222222-2222-4222-8222-222222222222')
        self.assertEqual(clips.page_rows(new)['roger'], {'link': 'https://magenta.cryptograss.live/clips/22222222-2222-4222-8222-222222222222', 'for': 'Copy that.'})
        self.assertEqual(new.count('roger'), 1)

    def test_the_same_link_changes_nothing(self):
        self.assertEqual(clips.page_with(PAGE, 'roger', 'https://magenta.cryptograss.live/clips/11111111-1111-4111-8111-111111111111'), PAGE)

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
        self.assertEqual(clips.write_page('all-individuals', 'https://magenta.cryptograss.live/clips/33333333-3333-4333-8333-333333333333', 'justin', bot=bot), 'written')
        title, text, summary, revision = bot.edit.call_args.args
        self.assertEqual(title, 'Magenta:Clips')
        self.assertIn('| all-individuals || https://magenta.cryptograss.live/clips/33333333-3333-4333-8333-333333333333 || ', text)
        self.assertIn('justin saved "all-individuals"', summary)

    def test_already_there_no_edit(self):
        bot = self.bot()
        self.assertEqual(clips.write_page('roger', 'https://magenta.cryptograss.live/clips/11111111-1111-4111-8111-111111111111', 'justin', bot=bot), 'same')
        bot.edit.assert_not_called()

    def test_a_forget_takes_it_off(self):
        bot = self.bot()
        self.assertEqual(clips.write_page('roger', None, 'justin', bot=bot), 'written')
        self.assertNotIn('roger', bot.edit.call_args.args[1])

    def test_only_saves_and_forgets_go_to_the_page(self):
        with mock.patch('conversations.services.mood_cache.spawn') as spawn:
            clips.page_later('/clips save roger https://magenta.cryptograss.live/clips/22222222-2222-4222-8222-222222222222', 'justin')
            clips.page_later('/clips forget roger', 'justin')
            clips.page_later('/clips roger https://magenta.cryptograss.live/clips/22222222-2222-4222-8222-222222222222', 'justin')
        self.assertEqual([c.args[1:] for c in spawn.call_args_list],
                         [('roger', 'https://magenta.cryptograss.live/clips/22222222-2222-4222-8222-222222222222', 'justin'), ('roger', None, 'justin')])


class PageIsCanonical(SimpleTestCase):
    """With a wiki bot set up, the page's rows are what plays (Justin, 13 Nov: "I want that to be canonical")."""
    HUMAN = PAGE
    BOT = PAGE.replace('|}', '|-\n| all-individuals || https://magenta.cryptograss.live/clips/33333333-3333-4333-8333-333333333333 || \n|}')

    def setUp(self):
        clips.cache.delete(clips.USES_KEY)
        clips.cache.delete(clips.USES_KEY + ':last')

    def rows(self, history):
        with self.settings(PICKIPEDIA_TODO_BOT_USER='Magenta-bot@ticks', PICKIPEDIA_TODO_BOT_PASSWORD='x'):
            return clips.page(get=lambda title: history)

    def test_the_bots_row_plays_and_the_use_is_a_trusted_editors(self):
        rows = self.rows([(3, 'Magenta-bot', self.BOT), (2, 'JMyles', self.HUMAN)])
        self.assertEqual(rows['all-individuals']['link'], 'https://magenta.cryptograss.live/clips/33333333-3333-4333-8333-333333333333')
        self.assertEqual(rows['huh']['for'], 'Tony Clifton\'s "huh".')

    def test_a_strangers_revision_counts_for_nothing(self):
        rows = self.rows([(4, 'Stranger', self.BOT.replace('Copy that.', 'pwned')), (2, 'JMyles', self.HUMAN)])
        self.assertNotIn('all-individuals', rows)
        self.assertEqual(rows['roger']['for'], 'Copy that.')

    def test_only_the_page_plays_once_it_can_be_read(self):
        self.rows([(2, 'JMyles', self.HUMAN)])
        saved = {'roger': {'clip': 'old', 'by': 'justin'}, 'not-on-page': {'clip': 'x', 'by': 'justin'}}
        with mock.patch.object(clips, 'library', return_value=saved), \
                self.settings(PICKIPEDIA_TODO_BOT_USER='Magenta-bot', PICKIPEDIA_TODO_BOT_PASSWORD='x'):
            playing = clips.every()
        self.assertEqual(set(playing), {'huh', 'roger'})
        self.assertEqual(playing['roger']['by'], 'justin')
        self.assertEqual(playing['roger']['link'], 'https://magenta.cryptograss.live/clips/11111111-1111-4111-8111-111111111111')

    def test_with_no_page_the_record_stands_in(self):
        saved = {'not-on-page': {'clip': 'x', 'by': 'justin'}}
        with mock.patch.object(clips, 'page', return_value={}), mock.patch.object(clips, 'library', return_value=saved), \
                self.settings(PICKIPEDIA_TODO_BOT_USER='Magenta-bot', PICKIPEDIA_TODO_BOT_PASSWORD='x'):
            self.assertEqual(set(clips.every()), {'not-on-page'})
