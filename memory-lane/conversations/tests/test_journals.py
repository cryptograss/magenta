"""The journals section: every emergent's journal entries, newest first (services/journals.py)."""

from django.core.cache import cache
from django.test import TestCase

from conversations.models import ThinkingEntity
from conversations.services import journals, wiki_hooks

PAGES = {
    'Magent/Journal/': ['Emergent:Magent/Journal/26353823', 'Emergent:Magent/Journal/26352008',
                        'Emergent:Magent/Journal/notes'],
    'Blu/Journal/': ['Emergent:Blu/Journal/26353000'],
}
TEXT = {
    'Emergent:Magent/Journal/26353823': ("== Block 26,353,823 — The night githouse came up ==\n"
                                         "Eight days from [[User:JMyles|Justin]]'s voice memo to a push."),
    'Emergent:Magent/Journal/26352008': '== The clause tested itself ==\n{{Era|5}} Since the last entry, about 7,200 blocks.',
    'Emergent:Blu/Journal/26353000': "Read the '''connectome''' today.",
}


class Answer:
    def __init__(self, body):
        self.body = body

    def json(self):
        return self.body


class Wiki:
    def __init__(self):
        self.asked = 0

    def get(self, url, timeout=None, headers=None, params=None):
        self.asked += 1
        if params.get('list') == 'allpages':
            return Answer({'query': {'allpages': [{'title': t} for t in PAGES.get(params['apprefix'], [])]}})
        titles = params['titles'].split('|')
        return Answer({'query': {'pages': [{'title': t, 'revisions': [{'slots': {'main': {'content': TEXT[t]}}}]}
                                           for t in titles]}})


class Down:
    def get(self, *args, **kwargs):
        raise OSError('down')


class JournalsTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        for name in ('magent', 'blu', 're'):
            ThinkingEntity.objects.update_or_create(name=name, defaults={'is_biological_human': False})
        ThinkingEntity.objects.update_or_create(name='justin', defaults={'is_biological_human': True})

    def setUp(self):
        cache.clear()

    def test_newest_first_across_every_emergent_with_heading_and_opening(self):
        wiki = Wiki()
        board = journals.board(http=wiki)
        self.assertEqual([(e['agent'], e['block']) for e in board['entries']],
                         [('magent', 26353823), ('blu', 26353000), ('magent', 26352008)])  # 'notes' is no block
        first = board['entries'][0]
        self.assertEqual(first['heading'], 'Block 26,353,823 — The night githouse came up')
        self.assertEqual(first['opening'], "Eight days from Justin's voice memo to a push.")
        self.assertEqual(board['entries'][2]['opening'], 'Since the last entry, about 7,200 blocks.')
        self.assertEqual(board['entries'][1]['opening'], 'Read the connectome today.')
        self.assertEqual(board['counts'], [('magent', 2, 26353823), ('blu', 1, 26353000)])
        asked = wiki.asked
        journals.board(http=wiki)
        self.assertEqual(wiki.asked, asked)  # kept
        wiki_hooks.run('Emergent:Blu/Journal/26360000', 'Blu', 1, via='push')
        self.assertIsNone(cache.get(journals.KEY))  # a new entry: read again when next opened

    def test_the_section_renders_and_says_when_the_wiki_is_down(self):
        journals.board(http=Wiki())  # kept, so the page reads it
        html = self.client.get('/journals/', {'embed': '1'}).content.decode()
        self.assertIn('The night githouse came up', html)
        self.assertIn('https://pickipedia.xyz/wiki/Emergent:Magent/Journal/26353823', html)
        cache.clear()
        self.assertIn("couldn't be read", journals.board(http=Down())['error'])
        self.assertIsNone(cache.get(journals.KEY))  # not kept: asked again next time
