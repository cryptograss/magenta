"""The stats section under the ▾: each site's traffic in three windows, counted on its own box.

Justin, #performance, 12 Nov (the section) and 22 Nov (the windows, and what they hold).
"""

from unittest import mock

from django.core.cache import cache
from django.test import TestCase

from conversations.services import mood_cache, traffic


def window(people=120, crawlers=300, ours=40, pages=(('/wiki/Doc_Watson', 90),),
           since='2026-11-22T01:00:00+00:00', hours=2, full=False):
    return {
        'since': since, 'hours': hours, 'full': full,
        'requests': {'people': people, 'crawlers': crawlers, 'ours': ours},
        'visits': {'people': 31, 'crawlers': 4, 'ours': 2},
        'bytes': 12345, 'status': {'2xx': 450, '4xx': 10},
        'pages': [{'what': what, 'n': n} for what, n in pages],
        'from': [{'what': 'www.reddit.com/r/bluegrass/comments/1abcd/the_connectome/', 'n': 24}],
        'followed': [{'what': '/wiki/Main_Page', 'n': 12}],
        'missing': [{'what': '/wiki/No_Such_Page', 'n': 3}],
    }


def rollup(site='pickipedia'):
    return {'site': site, 'host': f'{site}.example', 'updated': '2026-11-22T01:40:00+00:00',
            'read': 212, 'since': '2026-11-22T01:00:00+00:00', 'catching_up': 3,
            'windows': {'3h': window(full=True, hours=3), '24h': window(people=900),
                        '7d': window(people=6000)}}


class FakeAnswer:
    def __init__(self, body, status=200):
        self._body, self.status_code = body, status

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f'{self.status_code}')


class FakeHttp:
    """Each host answers as told; `asked` is every URL it was given."""

    def __init__(self, by_host):
        self.by_host, self.asked = by_host, []

    def get(self, url, **kw):
        self.asked.append(url)
        for host, answer in self.by_host.items():
            if f'//{host}/' in url:
                if isinstance(answer, Exception):
                    raise answer
                return answer
        return FakeAnswer({}, 404)


class TrafficServiceTest(TestCase):

    def setUp(self):
        cache.delete(traffic.KEY)

    def test_each_site_is_fetched_once_and_its_windows_carried_whole(self):
        http = FakeHttp({'magenta.cryptograss.live': FakeAnswer(rollup('magenta')),
                         'pickipedia.xyz': FakeAnswer(rollup())})
        state = traffic.state(http=http)
        self.assertEqual(len(http.asked), 2)
        self.assertTrue(all(url.endswith('/stats/traffic.json') for url in http.asked), http.asked)
        self.assertEqual([s['name'] for s in state['sites']], ['magenta', 'PickiPedia'])
        self.assertEqual([k for k, _ in state['windows']], ['3h', '24h', '7d'])
        for site in state['sites']:
            self.assertNotIn('error', site)
            self.assertEqual(site['windows']['24h']['requests']['people'], 900)
            self.assertEqual(site['windows']['24h']['from'][0]['n'], 24)

    def test_how_far_back_each_box_reaches_is_carried_too(self):
        """A window holding an hour reads exactly like a quiet week unless it says so (Justin, 22 Nov:
        "I'm not seeing a difference when I click the timeframes")."""
        http = FakeHttp({'magenta.cryptograss.live': FakeAnswer(rollup('magenta')),
                         'pickipedia.xyz': FakeAnswer(rollup())})
        for site in traffic.state(http=http)['sites']:
            self.assertEqual(site['since'], '2026-11-22T01:00:00+00:00')
            self.assertEqual(site['catching_up'], 3)
            self.assertIs(site['windows']['3h']['full'], True)
            self.assertIs(site['windows']['24h']['full'], False)
            self.assertEqual(site['windows']['24h']['hours'], 2)

    def test_an_older_rollup_that_says_nothing_about_its_reach_is_still_read(self):
        """The boxes are deployed one after the other; a rollup that hasn't been updated yet carries no
        `since`, and the page simply doesn't claim anything about how far back it goes."""
        old = rollup()
        del old['since'], old['catching_up']
        for w in old['windows'].values():
            del w['since'], w['hours'], w['full']
        http = FakeHttp({'magenta.cryptograss.live': FakeAnswer(old), 'pickipedia.xyz': FakeAnswer(old)})
        site = traffic.state(http=http)['sites'][0]
        self.assertEqual(site['since'], '')
        self.assertEqual(site['catching_up'], 0)
        self.assertNotIn('full', site['windows']['24h'])
        self.assertEqual(site['windows']['24h']['requests']['people'], 900)

    def test_a_site_that_cannot_be_read_says_so_rather_than_showing_no_traffic(self):
        http = FakeHttp({'magenta.cryptograss.live': FakeAnswer(rollup('magenta')),
                         'pickipedia.xyz': TimeoutError('slow box')})
        with self.assertLogs('conversations.services.traffic', level='WARNING'):
            state = traffic.state(http=http)
        bad = [s for s in state['sites'] if s['name'] == 'PickiPedia'][0]
        self.assertIn('couldn’t read it', bad['error'])
        self.assertNotIn('windows', bad)   # no windows at all: an empty one would read as no traffic
        self.assertNotIn('error', [s for s in state['sites'] if s['name'] == 'magenta'][0])

    def test_a_box_whose_rollup_has_not_run_yet_says_that(self):
        http = FakeHttp({'magenta.cryptograss.live': FakeAnswer({}, 404), 'pickipedia.xyz': FakeAnswer({}, 404)})
        state = traffic.state(http=http)
        self.assertTrue(all('hasn’t written its first count' in s['error'] for s in state['sites']))

    def test_nobody_waits_on_two_other_boxes_twice(self):
        http = FakeHttp({'magenta.cryptograss.live': FakeAnswer(rollup('magenta')),
                         'pickipedia.xyz': FakeAnswer(rollup())})
        traffic.state(http=http)
        traffic.state(http=http)
        self.assertEqual(len(http.asked), 2)          # kept: the second ask fetched nothing
        spawned = []
        with mock.patch.object(mood_cache, 'spawn', lambda fn, *a: spawned.append((fn, a))):
            traffic.forget()
            self.assertEqual(len(traffic.state(http=http)['sites']), 2)   # the old copy, at once
        self.assertEqual(len(http.asked), 2)          # ...while one thread fetches again
        self.assertEqual(len(spawned), 1)


class TrafficSectionTest(TestCase):

    def setUp(self):
        cache.delete(traffic.KEY)
        self.http = FakeHttp({'magenta.cryptograss.live': FakeAnswer(rollup('magenta')),
                              'pickipedia.xyz': FakeAnswer(rollup())})
        patch = mock.patch.object(traffic, 'requests', self.http)
        patch.start()
        self.addCleanup(patch.stop)

    def test_framed_it_shows_both_sites_windows_and_links_their_dashboards(self):
        framed = self.client.get('/traffic/', {'embed': '1'})
        self.assertEqual(framed.status_code, 200)
        self.assertEqual(framed['X-Frame-Options'], 'SAMEORIGIN')
        for host in ('magenta.cryptograss.live', 'pickipedia.xyz'):
            self.assertContains(framed, f'href="https://{host}/stats/" target="_blank"')
            self.assertContains(framed, f'data-host="{host}"')
        for words in ('the last 3 hours', 'the last 24 hours', 'the last 7 days'):
            self.assertContains(framed, words)
        # Drawn from what the page was rendered with, so the first look waits on nothing.
        self.assertContains(framed, 'id="trafficNow"')
        self.assertContains(framed, 'the_connectome')

    def test_every_description_says_what_it_counts(self):
        """Justin, 22 Nov: "add some tooltips, so it's obvious what the descriptions mean (eg 'our own
        machinery')". The words are in the page, for a screen reader as well as a pointer."""
        html = self.client.get('/traffic/', {'embed': '1'}).content.decode()
        for described in ('visits by people', 'pages they asked for', 'of it crawlers',
                          'our own machinery', 'Most read', 'They came from',
                          'Links they followed here', 'Asked for, not there'):
            self.assertIn(described, html)
        self.assertIn('TIPS = {', html)
        for words in ('half an hour goes by', "Other people's robots", 'each emergent’s',
                      'invisible to a server', 'keeps its title'):
            self.assertIn(words, html)
        self.assertIn('class="tip word"', html)                     # the underlined descriptions
        self.assertEqual(html.count('button type="button" class="tip"'), 3)   # and the three windows

    def test_the_page_can_say_how_far_back_a_box_has_counted(self):
        html = self.client.get('/traffic/', {'embed': '1'}).content.decode()
        self.assertIn('Counted here only since', html)
        self.assertIn('rolled log', html)
        self.assertIn('"catching_up": 3', html)      # rendered with it, so the first look says it too

    def test_the_api_carries_what_each_box_counted(self):
        body = self.client.get('/api/traffic/').json()
        self.assertEqual([s['host'] for s in body['sites']], ['magenta.cryptograss.live', 'pickipedia.xyz'])
        self.assertEqual(body['sites'][0]['windows']['7d']['requests']['people'], 6000)

    def test_it_is_a_section_of_the_one_page_under_the_chevron(self):
        page = self.client.get('/traffic/')
        self.assertEqual(page.status_code, 200)
        html = page.content.decode()
        more = html[html.index('id="sectionsMoreMenu"'):]
        more = more[:more.index('</div>')]
        self.assertIn('href="/traffic/" data-section="traffic"', more)
