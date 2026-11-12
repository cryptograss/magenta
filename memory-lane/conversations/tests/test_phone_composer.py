"""On a phone the box grows with its words to a quarter of what's on screen, then scrolls; with words in it,
? and 🎙 step aside (Skyler, 8 and 12 Nov: the box "takes up most of the screen")."""

from pathlib import Path

from django.test import SimpleTestCase

PAGE = Path(__file__).resolve().parent.parent / 'templates' / 'conversations' / 'moods.html'


class PhoneComposerTest(SimpleTestCase):

    def fit(self):
        html = PAGE.read_text()
        body = html[html.index('  function fitSay() {'):]
        return body[:body.index('\n  }\n') + 4]

    def test_measured_against_what_is_on_screen_not_the_whole_screen(self):
        # With an iPhone's keyboard up, vh is still the whole screen; the visual viewport is what's left.
        fit = self.fit()
        self.assertIn('window.visualViewport?.height', fit)
        self.assertIn('Math.min(sayBox.scrollHeight + 2, most)', fit)
        self.assertIn("window.visualViewport?.addEventListener('resize', fitSay)", PAGE.read_text())

    def test_pulled_up_the_cap_lets_go(self):
        html = PAGE.read_text()
        expand = html[html.index('  const expandSay = on => {'):]
        expand = expand[:expand.index('  };')]
        self.assertIn("sayBox.style.maxHeight = ''", expand)
        self.assertIn('if (!on) fitSay();', expand)

    def test_with_words_in_it_help_and_memo_step_aside_but_not_a_memo_under_way(self):
        html = PAGE.read_text()
        self.assertIn('#composer .field:has(textarea:not(:placeholder-shown)) #sayHelp,', html)
        self.assertIn('#composer .field:has(textarea:not(:placeholder-shown)) #memoBtn:not(.recording):not(.busy) '
                      '{ display: none; }', html)
