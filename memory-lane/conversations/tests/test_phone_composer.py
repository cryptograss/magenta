"""The box grows with its words (field-sizing, in the stylesheet): on a phone to a quarter of what's on screen, then
it scrolls; with words in it, ? and 🎙 step aside (Skyler, 8 and 12 Nov: the box "takes up most of the screen")."""

from pathlib import Path

from django.test import SimpleTestCase

PAGE = Path(__file__).resolve().parent.parent / 'templates' / 'conversations' / 'moods.html'


class PhoneComposerTest(SimpleTestCase):

    def test_the_box_is_sized_by_the_stylesheet_not_by_measuring(self):
        # field-sizing: content grows the box with its words; no script reads scrollHeight to set a height.
        html = PAGE.read_text()
        self.assertIn('field-sizing: content; min-height: 44px; max-height: 40vh;', html)
        self.assertNotIn('sayBox.scrollHeight', html)
        self.assertNotIn('sayBox.style.height', html)
        self.assertNotIn('fitSay', html)

    def test_measured_against_what_is_on_screen_not_the_whole_screen(self):
        # With an iPhone's keyboard up, vh and dvh are still the whole screen; the visual viewport is what's left.
        # Only the script can read it, so it tells the stylesheet, which stops the box at a quarter of it.
        html = PAGE.read_text()
        self.assertIn("setProperty('--seen', window.visualViewport.height + 'px')", html)
        self.assertIn("window.visualViewport.addEventListener('resize', onScreen)", html)
        self.assertIn('#composer .field textarea { min-height: 38px; padding: 8px 4px; '
                      'max-height: max(80px, calc(var(--seen, 100dvh) * .25)); }', html)

    def test_pulled_up_the_cap_lets_go(self):
        # More specific than the phone's quarter (an id, a class, two elements to its one), so it wins there too.
        self.assertIn('body.say-expanded #composer textarea { height: 60dvh; max-height: 85vh; }', PAGE.read_text())

    def test_the_edit_box_grows_with_its_words_too(self):
        html = PAGE.read_text()
        self.assertIn('field-sizing: content; min-height: 60px; max-height: 400px; }', html)
        self.assertNotIn('box.scrollHeight', html)

    def test_enter_mid_composition_is_the_input_methods(self):
        # An IME's Enter picks a candidate: it doesn't send, save, choose a suggestion or pick an emoji.
        html = PAGE.read_text()
        self.assertIn('const composing = e => e.isComposing || e.keyCode === 229;', html)
        self.assertEqual(html.count("!== 'Enter' || composing("), 2)  # the composer's send, the edit box's save
        self.assertIn("if (!suggest.classList.contains('on') || composing(e)) return;", html)
        self.assertIn("if (e.key === 'Enter' && !composing(e)) { e.preventDefault(); epGrid", html)
        self.assertIn("if (ev.key === 'Enter' && !composing(ev)) { ev.preventDefault(); go(); }", html)

    def test_with_words_in_it_help_and_memo_step_aside_but_not_a_memo_under_way(self):
        html = PAGE.read_text()
        self.assertIn('#composer .field:has(textarea:not(:placeholder-shown)) #sayHelp,', html)
        self.assertIn('#composer .field:has(textarea:not(:placeholder-shown)) #memoBtn:not(.recording):not(.busy) '
                      '{ display: none; }', html)
