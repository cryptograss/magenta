"""A PickiPedia page on Tidings, drawn as an article's opening (Justin, 11 Nov, #interfaces: "pickipedia pages
posted as tidings need to have a special look ... the first couple paragraphs or so ... as well as a pic")."""

import uuid
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings

from conversations.models import Message, Mood, ThinkingEntity, Tiding
from conversations.services.wiki_lead import lead_of, title_of

BASE = 'https://pickipedia.xyz'
URL = f"{BASE}/wiki/Kuba's_Last_Merged_Commits:_the_story_of_arthel159"

# As the wiki parses "Kuba's Last Merged Commits" (11 Nov), cut down, with what the lead must leave out.
PAGE = '''<div class="mw-content-ltr mw-parser-output" lang="en" dir="ltr"><p><a rel="nofollow" class="external text" href="https://pickipedia.xyz/index.php?title=Template:Latest_News&amp;action=edit">edit</a>
</p><p><i>11 Nov 2026</i>
</p><p>Today, we merge the last open commits of <a href="/wiki/Kuba_Hejhal" title="Kuba Hejhal">Kuba Hejhal</a>, the finest hacker-fiddler of the early bluegrass-as-cypherpunk age.  Kuba left this world two years ago at 27 years of age, and left us with his code and his music, but most of all, with stories that feel timeless and larger-than-life.<sup class="reference"><a href="#cite_note-1">[1]</a></sup>
</p>
<figure class="mw-default-size" typeof="mw:File/Thumb"><a href="/wiki/File:Arthel159_merge_graph.png" class="mw-file-description"><img src="/images/thumb/3/3b/Arthel159_merge_graph.png/300px-Arthel159_merge_graph.png" width="300" height="46" class="mw-file-element" data-file-width="1376" data-file-height="212" /></a><figcaption>the merge graph</figcaption></figure>
<figure class="mw-halign-right" typeof="mw:File/Thumb"><a href="/wiki/File:Kuba_Hejhal_with_his_fiddle_on_a_boat.jpeg" class="mw-file-description"><img src="/images/thumb/b/b5/Kuba.jpeg/200px-Kuba.jpeg" decoding="async" width="200" height="314" class="mw-file-element" srcset="/images/thumb/b/b5/Kuba.jpeg/300px-Kuba.jpeg 1.5x, /images/thumb/b/b5/Kuba.jpeg/400px-Kuba.jpeg 2x" data-file-width="750" data-file-height="1177" /></a><figcaption>Kuba Hejhal 1997-2024</figcaption></figure>
<p>His stature as one of the preeminent European bluegrass fiddlers was and is well-known around the world, but his off-the-damn-charts skill at software engineering was a <b>well-kept open secret</b> &lt;script&gt;alert(1)&lt;/script&gt; <a href="javascript:alert(1)">not a link</a> <a href="/index.php?title=Nope&amp;action=edit&amp;redlink=1">a red link</a>.
</p><p>Kuba was the life of the party everywhere he went; few dared keep up with him in his unending quest to find the main nerve.
</p>
<ul><li>What if we showed the entire ensemble of every bluegrass track, right in the player?</li></ul>
<div class="mw-heading mw-heading2"><h2 id="The_player">The player</h2></div>
<p>Further down.</p></div>'''


class TitleTest(TestCase):

    def test_an_article_and_not_the_rest(self):
        self.assertEqual(title_of(URL, BASE), "Kuba's Last Merged Commits: the story of arthel159")
        self.assertEqual(title_of(f'{BASE}/wiki/Kuba_Hejhal#Music', BASE), 'Kuba Hejhal')
        self.assertEqual(title_of(f'{BASE}/wiki/Doc%20Watson', BASE), 'Doc Watson')
        for url in (f'{BASE}/wiki/Special:Search', f'{BASE}/wiki/File:Kuba.jpeg', f'{BASE}/wiki/Release:Qmabc',
                    f'{BASE}/wiki/Kuba_Hejhal?action=edit', 'https://example.com/wiki/Kuba_Hejhal', f'{BASE}/wiki/'):
            self.assertIsNone(title_of(url, BASE), url)


class LeadTest(TestCase):

    def setUp(self):
        self.lead = lead_of(PAGE, BASE)

    def test_the_first_couple_paragraphs_whole(self):
        texts = self.lead['paragraphs']
        self.assertEqual(len(texts), 3)  # the dateline, and two of substance: past 500 characters, it stops
        self.assertEqual(texts[0], '<i>11 Nov 2026</i>')
        self.assertTrue(texts[1].startswith('Today, we merge'))
        self.assertTrue(texts[2].startswith('His stature'))
        self.assertTrue(self.lead['more'])

    def test_its_furniture_left_out(self):
        words = ' '.join(self.lead['paragraphs'])
        self.assertNotIn('edit', words.replace('well-kept', ''))  # a template's edit link
        self.assertNotIn('[1]', words)                             # a footnote's mark

    def test_only_links_bold_and_italics_and_the_rest_escaped(self):
        words = ' '.join(self.lead['paragraphs'])
        self.assertIn('<a class="wiki" href="https://pickipedia.xyz/wiki/Kuba_Hejhal" target="_blank" rel="noopener">Kuba Hejhal</a>', words)
        self.assertIn('<b>well-kept open secret</b>', words)
        self.assertIn('&lt;script&gt;', words)
        self.assertNotIn('<script', words)
        self.assertNotIn('javascript:', words)
        self.assertIn(' not a link ', words)
        self.assertNotIn('redlink', words)

    def test_blue_for_a_page_red_for_one_not_written(self):
        # Justin, 12 Nov: "let's show them as blue or red in the tidings preview".
        words = ' '.join(self.lead['paragraphs'])
        self.assertIn('<a class="wiki" href="https://pickipedia.xyz/wiki/Kuba_Hejhal"', words)
        self.assertIn('<a class="wiki new" href="https://pickipedia.xyz/wiki/Nope" target="_blank" rel="noopener">a red link</a>.', words)
        lead = lead_of('<div class="mw-parser-output"><p>An opening that names <a href="/wiki/Bill_Monroe" class="new" '
                       'title="Bill Monroe (page does not exist)">Bill Monroe</a> and <a class="external text" '
                       'href="https://example.com/x">elsewhere</a>, long enough to be counted.</p></div>', BASE)
        self.assertIn('<a class="wiki new" href="https://pickipedia.xyz/wiki/Bill_Monroe"', lead['paragraphs'][0])
        self.assertIn('<a class="ext" href="https://example.com/x"', lead['paragraphs'][0])

    def test_its_picture_not_its_banner(self):
        picture = self.lead['picture']
        self.assertEqual(picture['src'], f'{BASE}/images/thumb/b/b5/Kuba.jpeg/400px-Kuba.jpeg')  # the largest made
        self.assertEqual(picture['caption'], 'Kuba Hejhal 1997-2024')
        self.assertEqual(picture['page'], f'{BASE}/wiki/File:Kuba_Hejhal_with_his_fiddle_on_a_boat.jpeg')
        self.assertEqual((picture['width'], picture['height']), (750, 1177))

    def test_a_short_page_stops_at_its_heading(self):
        lead = lead_of('<div class="mw-parser-output"><p>One paragraph of the opening, long enough to count as one.</p>'
                       '<div class="mw-heading mw-heading2"><h2>Next</h2></div><p>The next section.</p></div>', BASE)
        self.assertEqual(len(lead['paragraphs']), 1)
        self.assertTrue(lead['more'])
        self.assertIsNone(lead['picture'])

    def test_only_a_dateline_is_nothing_to_read(self):
        self.assertEqual(lead_of('<div class="mw-parser-output"><p><i>11 Nov</i></p></div>', BASE)['paragraphs'], [])


# Justin, 12 Nov: "not to show unverified content at all", and "a fully rendered button to play on rabbithole, as
# the first bit of content here". As the wiki renders {{Listen in Rabbithole}}, <proposed> and Bot_proposes.
LISTEN = ('<span class="pp-listen-stack plainlinks" style="float:right"><span class="pp-listen-rh" style="background:#4a7c59">'
          '<a rel="nofollow" class="external text" href="https://pickipedia.xyz/rabbithole/?track=QmTrack">'
          '<span style="color:#fff">▶&#160;Hear Kuba on Reuben\'s Train</span></a><span class="pp-listen-chase">'
          '<span class="pp-rabbit">🐰</span><span class="pp-carrot">🥕</span></span></span><span class="pp-trail-rh">'
          '<a rel="nofollow" class="external text" href="https://pickipedia.xyz/rabbithole/?track=QmTrack&amp;action=trail">'
          '<span>🐇&#160;find a trail here</span></a></span></span>')
CHECKED = ('<div class="mw-parser-output"><p>' + LISTEN + '<i>11 Nov 2026</i></p>'
           '<p>Kuba played with <span class="pv-proposed pv-proposed--inline" title="proposed by Blu">Billy Strings '
           '<sup class="pv-proposed__badge">[bot]</sup></span>everyone, a fiddler from Roudnice who never stopped playing.</p>'
           '<div class="pv-proposed pv-proposed--block"><p>A whole proposed paragraph.</p>'
           '<figure><img src="/images/Proposed.jpg" class="mw-file-element" data-file-width="800" data-file-height="600"></figure></div>'
           '<p><span class="bot-proposal">A claim nobody checked.</span></p>'
           '<p>He toured Europe in 2024 <span class="pv-proposed pv-proposed--inline pv-proposed--removal">with the band'
           '<sup class="pv-proposed__badge">[remove?]</sup></span>, and it was legendary.</p>'
           '<figure><img src="/images/Real.jpg" class="mw-file-element" data-file-width="800" data-file-height="600"></figure></div>')


class VerifiedAndListenTest(TestCase):

    def setUp(self):
        self.lead = lead_of(CHECKED, BASE)
        self.words = ' '.join(self.lead['paragraphs'])

    def test_nothing_unverified(self):
        for proposed in ('Billy Strings', '[bot]', 'A whole proposed paragraph', 'A claim nobody checked', 'Proposed.jpg'):
            self.assertNotIn(proposed, self.words + str(self.lead['picture']))
        self.assertIn('Kuba played with everyone, a fiddler from Roudnice', self.words)
        self.assertEqual(self.lead['picture']['src'], f'{BASE}/images/Real.jpg')

    def test_a_proposed_removal_reads_as_the_page_does_now(self):
        self.assertIn('He toured Europe in 2024 with the band, and it was legendary.', self.words)
        self.assertNotIn('remove?', self.words)

    def test_the_listen_button_is_its_own_not_words(self):
        self.assertEqual(self.lead['listen'], {
            'play': {'href': 'https://pickipedia.xyz/rabbithole/?track=QmTrack', 'text': "Hear Kuba on Reuben's Train"},
            'trail': {'href': 'https://pickipedia.xyz/rabbithole/?track=QmTrack&action=trail', 'text': 'find a trail here'}})
        self.assertNotIn('Reuben', self.words)
        self.assertNotIn('🐰', self.words)
        self.assertEqual(self.lead['paragraphs'][0], '<i>11 Nov 2026</i>')

    def test_a_listen_button_in_unverified_words_is_left_out_too(self):
        lead = lead_of('<div class="mw-parser-output"><p><span class="bot-proposal">' + LISTEN + '</span>'
                       'An opening long enough to be counted as a paragraph of the page.</p></div>', BASE)
        self.assertIsNone(lead['listen'])

    def test_no_listen_on_a_page_without_one(self):
        self.assertIsNone(lead_of(PAGE, BASE)['listen'])


@override_settings(PICKIPEDIA_URL=BASE)
class PageTest(TestCase):

    def setUp(self):
        cache.clear()
        self.justin = ThinkingEntity.objects.create(name='justin', is_biological_human=True)
        mood = Mood.objects.create(slug='general', title='general')
        said = Message.objects.create(id=uuid.uuid4(), sender=self.justin, mood=mood, content=URL,
                                      source_file='mood-web', timestamp=1)
        self.tiding = Tiding.objects.create(message=said, url=URL, by=self.justin,
                                            title="Kuba's Last Merged Commits: the story of arthel159")

    def wiki(self, text=PAGE):
        answer = mock.Mock()
        answer.json.return_value = {'parse': {'title': 'x', 'text': text}}
        return mock.patch('requests.get', return_value=answer)

    def test_the_page_opens_as_the_article(self):
        with self.wiki() as got:
            html = self.client.get(f'/tidings/{self.tiding.id}/?embed=1').content.decode()
        self.assertEqual(got.call_args.kwargs['params']['page'], "Kuba's Last Merged Commits: the story of arthel159")
        self.assertIn('<article class="wikilead">', html)
        self.assertIn('Today, we merge the last open commits of', html)
        self.assertIn(f'src="{BASE}/images/thumb/b/b5/Kuba.jpeg/400px-Kuba.jpeg"', html)
        self.assertIn('<figcaption>Kuba Hejhal 1997-2024</figcaption>', html)
        self.assertIn('Read the rest on PickiPedia →', html)
        self.assertNotIn('Further down', html)

    def test_the_listen_button_comes_first(self):
        with self.wiki(CHECKED):
            html = self.client.get(f'/tidings/{self.tiding.id}/?embed=1').content.decode()
        article = html[html.index('<article class="wikilead">'):]
        self.assertLess(article.index('class="play"'), article.index('<p>'))
        self.assertIn('<a class="play" href="https://pickipedia.xyz/rabbithole/?track=QmTrack" target="_blank" rel="noopener">'
                      '<span class="tri" aria-hidden="true">▶</span><span class="words">Hear Kuba on Reuben&#x27;s Train</span>', html)
        self.assertIn('🐇 find a trail here</a>', html)
        self.assertNotIn('Billy Strings', html)

    def test_kept_a_while_and_a_silent_wiki_is_no_lead(self):
        with self.wiki() as got:
            self.client.get(f'/tidings/{self.tiding.id}/?embed=1')
            self.client.get(f'/tidings/{self.tiding.id}/?embed=1')
        self.assertEqual(got.call_count, 1)
        cache.clear()
        with mock.patch('requests.get', side_effect=OSError('down')):
            html = self.client.get(f'/tidings/{self.tiding.id}/?embed=1').content.decode()
        self.assertNotIn('<article class="wikilead">', html)
        self.assertIn("Kuba&#x27;s Last Merged Commits", html)  # the link's page, as before

    def test_an_edit_on_the_wiki_shows_at_once(self):
        from conversations.services import wiki_changes
        with self.wiki() as got:
            self.client.get(f'/tidings/{self.tiding.id}/?embed=1')
            wiki_changes.forget("Kuba's_Last_Merged_Commits:_the_story_of_arthel159")
            self.client.get(f'/tidings/{self.tiding.id}/?embed=1')
        self.assertEqual(got.call_count, 2)

    def test_another_sites_link_asks_the_wiki_nothing(self):
        Tiding.objects.filter(id=self.tiding.id).update(url='https://example.com/a')
        with self.wiki() as got:
            html = self.client.get(f'/tidings/{self.tiding.id}/?embed=1').content.decode()
        self.assertEqual(got.call_count, 0)
        self.assertNotIn('<article class="wikilead">', html)


@override_settings(PICKIPEDIA_URL=BASE)
class CardTest(PageTest):
    """Justin, 11 Nov: "what will it take for that link to have a special card on telegram, social media, etc?"
    The link's own address (no ?embed) carries the Open Graph tags a sharing app reads, before any script."""

    def test_a_wiki_pages_card_is_its_opening_and_its_picture(self):
        with self.wiki():
            html = self.client.get(f'/tidings/{self.tiding.id}/').content.decode()
        self.assertIn("<title>Kuba&#x27;s Last Merged Commits: the story of arthel159 · magenta</title>", html)
        self.assertIn('<meta property="og:title" content="Kuba&#x27;s Last Merged Commits: the story of arthel159">', html)
        self.assertIn('<meta property="og:description" content="11 Nov 2026 Today, we merge the last open commits of Kuba Hejhal,', html)
        self.assertIn(f'<meta property="og:image" content="{BASE}/images/thumb/b/b5/Kuba.jpeg/400px-Kuba.jpeg">', html)
        self.assertIn('<meta name="twitter:card" content="summary_large_image">', html)
        self.assertIn(f'<meta property="og:url" content="http://testserver/tidings/{self.tiding.id}/">', html)
        description = html.split('og:description" content="')[1].split('"')[0]
        self.assertLessEqual(len(description), 300)
        self.assertTrue(description.endswith('…'))

    def test_another_link_says_what_was_said_with_it_and_shows_magenta(self):
        Tiding.objects.filter(id=self.tiding.id).update(url='https://example.com/a')
        Message.objects.filter(id=self.tiding.message_id).update(content='https://example.com/a what a find')
        with self.wiki() as got:
            html = self.client.get(f'/tidings/{self.tiding.id}/').content.decode()
        self.assertEqual(got.call_count, 0)
        self.assertIn('<meta property="og:description" content="what a find">', html)
        self.assertIn('<meta property="og:image" content="http://testserver/moods/icon-512.png">', html)
        self.assertIn('<meta name="twitter:card" content="summary">', html)

    def test_the_moods_and_a_missing_link_have_no_card(self):
        self.assertNotIn('og:title', self.client.get(f'/tidings/{uuid.uuid4()}/').content.decode())
        self.assertNotIn('og:title', self.client.get('/tidings/').content.decode())


@override_settings(PICKIPEDIA_URL=BASE)
class PreviewerCardTest(PageTest):
    """Justin, 13 Nov: Telegram drew PickiPedia's cards but not magenta's. A previewer is sent the card alone,
    not the whole page (most of a megabyte), and may ask HEAD first."""

    def test_a_previewer_gets_the_card_alone(self):
        with self.wiki():
            got = self.client.get(f'/tidings/{self.tiding.id}/', HTTP_USER_AGENT='TelegramBot (like TwitterBot)')
        html = got.content.decode()
        self.assertIn('<meta property="og:title" content="Kuba&#x27;s Last Merged Commits: the story of arthel159">', html)
        self.assertIn(f'<meta property="og:image" content="{BASE}/images/thumb/b/b5/Kuba.jpeg/400px-Kuba.jpeg">', html)
        self.assertLess(len(html), 4000)
        self.assertNotIn('<script', html)

    def test_a_person_gets_the_page(self):
        with self.wiki():
            html = self.client.get(f'/tidings/{self.tiding.id}/', HTTP_USER_AGENT='Mozilla/5.0').content.decode()
        self.assertIn('og:title', html)
        self.assertGreater(len(html), 50000)

    def test_head_is_answered(self):
        with self.wiki():
            self.assertEqual(self.client.head(f'/tidings/{self.tiding.id}/').status_code, 200)
        self.assertEqual(self.client.head('/').status_code, 200)
