"""Every section page's top bar names every section (Skyler, 29 Oct: polls was missing beside tidings, prompts and status)."""

from pathlib import Path

from django.test import SimpleTestCase

TEMPLATES = Path(__file__).resolve().parent.parent / 'templates' / 'conversations'
SECTIONS = ('status', 'tidings', 'polls', 'prompts', 'todo', 'ramp', 'tasks', 'journals', 'verify', 'hooks', 'traffic', 'settings')


class SectionNavTest(SimpleTestCase):

    def test_each_section_page_links_every_section(self):
        for page in ('status', 'tidings', 'polls', 'prompts', 'todo', 'pulls', 'tasks', 'journals', 'verify', 'hooks', 'traffic'):
            html = (TEMPLATES / f'mood_{page}.html').read_text()
            nav = html[html.index('<nav>'):html.index('</nav>')]
            for section in SECTIONS:
                self.assertIn(f'href="/{section}/"', nav, f'{page} has no {section}')

    def test_account_menu_has_every_header_section_for_a_phone(self):
        """A phone hides the header's sections; the account menu carries each (Skyler, 8 Nov)."""
        import re
        html = (TEMPLATES / 'moods.html').read_text()
        header = html[html.index('<span class="sections"'):]
        header = header[:header.index('</span></span>')]
        menu = html[html.index('<div id="userMenu"'):]
        menu = menu[:menu.index('<a class="gear"')]
        for section in re.findall(r'data-section="(\w+)"', header):
            self.assertIn(f'data-section="{section}"', menu, f'the account menu has no {section}')
