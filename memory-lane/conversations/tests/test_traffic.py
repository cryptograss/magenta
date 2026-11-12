"""The stats section under the ▾ (Justin, #performance, 12 Nov): it links the two public traffic dashboards."""

from django.test import TestCase


class TrafficSectionTest(TestCase):

    def test_framed_it_links_both_dashboards_and_reads_their_sockets(self):
        framed = self.client.get('/traffic/', {'embed': '1'})
        self.assertEqual(framed.status_code, 200)
        self.assertEqual(framed['X-Frame-Options'], 'SAMEORIGIN')
        for host in ('magenta.cryptograss.live', 'pickipedia.xyz'):
            # Opened in a tab of its own: /stats/ is Caddy's, not a section the one page could show.
            self.assertContains(framed, f'href="https://{host}/stats/" target="_blank"')
            self.assertContains(framed, f'data-host="{host}"')
        self.assertContains(framed, 'wss://${host}/stats/ws')

    def test_it_is_a_section_of_the_one_page_under_the_chevron(self):
        page = self.client.get('/traffic/')
        self.assertEqual(page.status_code, 200)
        html = page.content.decode()
        more = html[html.index('id="sectionsMoreMenu"'):]
        more = more[:more.index('</div>')]
        self.assertIn('href="/traffic/" data-section="traffic"', more)
