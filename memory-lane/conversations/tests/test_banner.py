"""Tests for the emergency disk-backed banner and BannerMiddleware."""

import os
import tempfile
from unittest import mock

from django.http import HttpResponse, JsonResponse
from django.test import RequestFactory, SimpleTestCase, override_settings

from conversations.middleware import BannerMiddleware
from conversations.services import banner


class BannerServiceTest(SimpleTestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.banner_path = os.path.join(self.dir, 'banner.json')

    def tearDown(self):
        banner.clear_banner()

    def test_banner_lifecycle_on_disk(self):
        with override_settings(MAGENTA_BANNER_PATH=self.banner_path):
            with mock.patch.dict(os.environ, {'MAGENTA_BANNER_PATH': self.banner_path}):
                self.assertIsNone(banner.get_banner())

                data = banner.set_banner('Scheduled maintenance in 10m', bg='orange', fg='black', by='justin')
                self.assertIsNotNone(data)
                self.assertEqual(data['message'], 'Scheduled maintenance in 10m')
                self.assertEqual(data['bg'], 'orange')
                self.assertEqual(data['fg'], 'black')
                self.assertEqual(data['by'], 'justin')
                self.assertTrue(os.path.exists(self.banner_path))

                # Reads back from disk
                read_back = banner.get_banner()
                self.assertEqual(read_back['message'], 'Scheduled maintenance in 10m')

                # Render HTML escapes message
                html = banner.render_banner_html(read_back)
                self.assertIn('class="banner"', html)
                self.assertIn('Scheduled maintenance in 10m', html)
                self.assertIn('background: orange', html)
                self.assertIn('color: black', html)

                # Clear banner removes file
                banner.clear_banner()
                self.assertIsNone(banner.get_banner())
                self.assertFalse(os.path.exists(self.banner_path))

    def test_set_banner_clear_keywords(self):
        with mock.patch.dict(os.environ, {'MAGENTA_BANNER_PATH': self.banner_path}):
            banner.set_banner('Active', 'red', 'white')
            self.assertIsNotNone(banner.get_banner())

            # 'clear' removes it
            banner.set_banner('clear')
            self.assertIsNone(banner.get_banner())

            banner.set_banner('Active', 'red', 'white')
            # 'off' removes it
            banner.set_banner('off')
            self.assertIsNone(banner.get_banner())


class BannerMiddlewareTest(SimpleTestCase):

    def setUp(self):
        self.rf = RequestFactory()
        self.dir = tempfile.mkdtemp()
        self.banner_path = os.path.join(self.dir, 'banner.json')

    def tearDown(self):
        banner.clear_banner()

    def test_middleware_injects_into_html_after_body(self):
        with mock.patch.dict(os.environ, {'MAGENTA_BANNER_PATH': self.banner_path}):
            banner.set_banner('Emergency Alert', 'red', 'white')

            req = self.rf.get('/')
            middleware = BannerMiddleware(lambda r: HttpResponse('<!DOCTYPE html><html><body class="test"><main>Hello</main></body></html>', content_type='text/html'))
            resp = middleware(req)

            body = resp.content.decode('utf-8')
            self.assertIn('<body class="test"><div id="magenta-banner" class="banner"', body)
            self.assertIn('Emergency Alert', body)

    def test_middleware_does_not_touch_json_or_empty_banner(self):
        with mock.patch.dict(os.environ, {'MAGENTA_BANNER_PATH': self.banner_path}):
            banner.set_banner('Emergency Alert', 'red', 'white')

            req = self.rf.get('/api/test/')
            middleware = BannerMiddleware(lambda r: JsonResponse({'status': 'ok'}))
            resp = middleware(req)
            self.assertEqual(resp['Content-Type'], 'application/json')
            self.assertNotIn('magenta-banner', resp.content.decode('utf-8'))

            # When banner is cleared, HTML is untouched
            banner.clear_banner()
            req2 = self.rf.get('/')
            middleware2 = BannerMiddleware(lambda r: HttpResponse('<html><body>No banner</body></html>', content_type='text/html'))
            resp2 = middleware2(req2)
            self.assertNotIn('class="banner"', resp2.content.decode('utf-8'))

    def test_middleware_does_not_inject_duplicate_if_banner_already_in_content(self):
        with mock.patch.dict(os.environ, {'MAGENTA_BANNER_PATH': self.banner_path}):
            banner.set_banner('Emergency Alert', 'red', 'white')

            req = self.rf.get('/')
            # Case 1: Template rendered with id="magenta-banner"
            resp1 = BannerMiddleware(lambda r: HttpResponse('<html><body><div id="magenta-banner" class="banner">Existing</div></body></html>', content_type='text/html'))(req)
            self.assertEqual(resp1.content.decode('utf-8').count('id="magenta-banner"'), 1)
            self.assertNotIn('Emergency Alert', resp1.content.decode('utf-8'))

            # Case 2: Template rendered with class="banner"
            resp2 = BannerMiddleware(lambda r: HttpResponse('<html><body><div class="banner">Existing</div></body></html>', content_type='text/html'))(req)
            self.assertEqual(resp2.content.decode('utf-8').count('class="banner"'), 1)
            self.assertNotIn('Emergency Alert', resp2.content.decode('utf-8'))

    def test_middleware_does_not_inject_into_embedded_frames(self):
        with mock.patch.dict(os.environ, {'MAGENTA_BANNER_PATH': self.banner_path}):
            banner.set_banner('Emergency Alert', 'red', 'white')

            # ?embed=1 is used by section iframes (settings, prompts, status, etc.)
            req_embed = self.rf.get('/moods/settings/?embed=1')
            resp_embed = BannerMiddleware(lambda r: HttpResponse('<html><body><main>Settings</main></body></html>', content_type='text/html'))(req_embed)
            self.assertNotIn('class="banner"', resp_embed.content.decode('utf-8'))
            self.assertNotIn('id="magenta-banner"', resp_embed.content.decode('utf-8'))

            # Sec-Fetch-Dest: iframe
            req_iframe = self.rf.get('/moods/settings/', HTTP_SEC_FETCH_DEST='iframe')
            resp_iframe = BannerMiddleware(lambda r: HttpResponse('<html><body><main>Settings</main></body></html>', content_type='text/html'))(req_iframe)
            self.assertNotIn('class="banner"', resp_iframe.content.decode('utf-8'))
