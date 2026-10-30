"""Every section page's top bar names every section (Skyler, 29 Oct: polls was missing beside tidings, prompts and status)."""

from pathlib import Path

from django.test import SimpleTestCase

TEMPLATES = Path(__file__).resolve().parent.parent / 'templates' / 'conversations'
SECTIONS = ('status', 'tidings', 'polls', 'prompts', 'todo', 'settings')


class SectionNavTest(SimpleTestCase):

    def test_each_section_page_links_every_section(self):
        for page in ('status', 'tidings', 'polls', 'prompts', 'todo'):
            html = (TEMPLATES / f'mood_{page}.html').read_text()
            nav = html[html.index('<nav>'):html.index('</nav>')]
            for section in SECTIONS:
                self.assertIn(f'href="/moods/{section}/"', nav, f'{page} has no {section}')
