"""Magenta's own pages at /<name>/ (services/documents.py; Justin, 9 Nov, #interfaces)."""

from unittest import mock

from django.core.cache import cache
from django.test import TestCase

from conversations.services import documents

ABOUT_HTML = '<div class="mw-parser-output"><p>magenta is where we talk.</p></div>'


def fake_wiki(members, pages):
    """A stand-in for PickiPedia's API: the category's members, and each page's parse."""
    def get(params):
        if params.get('list') == 'categorymembers':
            return {'query': {'categorymembers': [{'ns': 3012, 'title': t} for t in members]}}
        title = params['page']
        html, cats = pages[title]
        return {'parse': {'title': title, 'displaytitle': title, 'text': html, 'revid': 7,
                          'categories': [{'category': c.replace(' ', '_')} for c in cats]}}
    return get


class DocumentsTest(TestCase):
    def setUp(self):
        cache.clear()

    def test_names_are_addresses(self):
        self.assertEqual(documents.name_of('Magenta:About'), 'about')
        self.assertEqual(documents.name_of('Magenta:Signing in'), 'signing-in')

    def test_a_listed_page_is_served_as_a_document(self):
        wiki = fake_wiki(['Magenta:About'], {'Magenta:About': (ABOUT_HTML, ['Magenta top-level documents'])})
        with mock.patch.object(documents, '_get', side_effect=wiki):
            page = self.client.get('/about/')
        self.assertEqual(page.status_code, 200)
        body = page.content.decode()
        self.assertIn('magenta is where we talk.', body)
        self.assertIn('<title>About · magenta</title>', body)
        self.assertIn('data-slides=""', body)
        self.assertIn('href="https://pickipedia.xyz/wiki/Magenta:About"', body)

    def test_the_slideshow_category_makes_it_slides(self):
        wiki = fake_wiki(['Magenta:Tour notes'], {'Magenta:Tour notes': (
            '<ul><li>One?<ul><li>Yes.</li></ul></li></ul>', ['Magenta top-level documents', 'Magenta slideshows'])})
        with mock.patch.object(documents, '_get', side_effect=wiki):
            body = self.client.get('/tour-notes/').content.decode()
        self.assertIn('data-slides="1"', body)

    def test_an_unlisted_name_is_not_found(self):
        with mock.patch.object(documents, '_get', side_effect=fake_wiki([], {})):
            self.assertEqual(self.client.get('/about/').status_code, 404)

    def test_routes_above_win(self):
        # A page named after a section never takes its address.
        wiki = fake_wiki(['Magenta:Status'], {'Magenta:Status': (ABOUT_HTML, ['Magenta top-level documents'])})
        with mock.patch.object(documents, '_get', side_effect=wiki):
            body = self.client.get('/status/').content.decode()
        self.assertNotIn('magenta is where we talk.', body)

    def test_the_wiki_down_keeps_the_last_copy(self):
        wiki = fake_wiki(['Magenta:About'], {'Magenta:About': (ABOUT_HTML, ['Magenta top-level documents'])})
        with mock.patch.object(documents, '_get', side_effect=wiki):
            documents.listed()
            documents.read('Magenta:About')
        cache.delete(documents.LIST_KEY)
        cache.delete('documents:page:Magenta:About')
        with mock.patch.object(documents, '_get', side_effect=OSError('down')):
            self.assertEqual(documents.listed(), {'about': 'Magenta:About'})
            self.assertIn('where we talk', documents.read('Magenta:About')['html'])

    def test_the_faq_stays_slides_at_faq(self):
        from conversations.services import faq
        with mock.patch.object(faq, 'state', return_value={'html': '<ul><li>Q?</li></ul>', 'revid': 1, 'error': ''}):
            body = self.client.get('/faq/').content.decode()
        self.assertIn('data-slides="1"', body)
        self.assertIn('data-cover="frequently asked questions"', body)
