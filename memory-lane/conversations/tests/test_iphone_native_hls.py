"""An iPhone plays a release with Safari's own HLS, not hls.js (Skyler, iPhone 17, 8 Nov: through hls.js on
ManagedMediaSource, sound over a black picture, then "can't play" and the video gone)."""

from pathlib import Path

from django.test import SimpleTestCase

TEMPLATES = Path(__file__).resolve().parent.parent / 'templates' / 'conversations'


class IphoneNativeHlsTest(SimpleTestCase):

    def test_only_classic_media_source_sends_a_release_to_hlsjs(self):
        for page in ('moods.html', 'mood_tiding.html'):
            html = (TEMPLATES / page).read_text()
            self.assertIn("const mse = 'MediaSource' in window;", html, page)
            self.assertNotIn("'ManagedMediaSource' in window", html, page)
