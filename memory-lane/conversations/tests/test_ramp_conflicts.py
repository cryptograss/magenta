"""A conflicting candidate wakes its author in its Mood, once per head (services/ramp_conflicts.py)."""

from django.core.cache import cache
from django.test import TestCase

from conversations.models import Message, Mood, ThinkingEntity
from conversations.services import ramp, ramp_conflicts, tasks

TASK = ('{{Emergent task|cap=3|tools=full}}\n'
        '{ref} ({branch}) conflicts: with {base} in {conflicts}; with other candidates: {clashes}. See {page}.')


def item(branch='re/foo', head='a' * 40, **more):
    return {'repo': 'magenta', 'number': 7, 'branch': branch, 'base': 'main', 'state': 'open', 'head': head,
            'ref': 'magenta#7', 'title': 'the foo work', 'moods': ['interfaces'], 'worked_on_by': '', 'author': '',
            'conflicts': ['todo.py'], 'clashes': [], **more}


class ConflictWakeTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        for name, human in (('re', False), ('magent', False), ('skyler', True)):
            ThinkingEntity.objects.get_or_create(name=name, defaults={'is_biological_human': human})
        for slug in ('interfaces', 'ramp', 'general'):
            Mood.objects.create(slug=slug, title=slug)

    def setUp(self):
        cache.clear()
        self.pages = {tasks.title('re', 'Conflict'): [(90, 'JMyles', TASK)]}

    def run_with(self, *items, pages=None):
        cache.set(ramp.KEY, {'open': list(items)}, None)
        return ramp_conflicts.due(get=lambda t: (self.pages if pages is None else pages).get(t, []))

    def test_its_author_is_woken_in_its_mood_once_per_head(self):
        [(ref, line)] = self.run_with(item())
        self.assertEqual((ref, line.mood_slug, line.content['for'], line.content['state']),
                         ('magenta#7', 'interfaces', 're', 'asked'))
        self.assertEqual(line.content['text'], 'magenta#7 (re/foo) conflicts: with main in todo.py; with other '
                                               'candidates: none. See https://magenta.cryptograss.live/ramp/magenta/re/foo/.')
        self.assertEqual(self.run_with(item()), [])                       # the same commit: told already
        [(_, again)] = self.run_with(item(head='b' * 40))                 # a new commit, still conflicting
        self.assertEqual(again.content['state'], 'asked')

    def test_a_clash_with_another_candidate_wakes_too(self):
        [(_, line)] = self.run_with(item(conflicts=[], clashes=[{'ref': 'magenta#9', 'branch': 'magent/x', 'files': ['a.py']}]))
        self.assertIn('with other candidates: magenta#9 in a.py', line.content['text'])

    def test_who_and_where(self):
        self.assertEqual(ramp_conflicts.author_of(item(branch='skyler/foo', worked_on_by='magent')), None)  # a person's
        self.assertEqual(ramp_conflicts.author_of(item(branch='servers-menu-mobile', worked_on_by='magent, skyler')), 'magent')
        self.assertEqual(ramp_conflicts.mood_of(item(moods=[], mentions=[{'mood': 'general'}, {'mood': 'ramp'}])), 'ramp')
        self.assertEqual(ramp_conflicts.mood_of(item(moods=[])), 'ramp')

    def test_nothing_without_a_conflict_a_trusted_page_or_an_emergent(self):
        self.assertEqual(self.run_with(item(conflicts=[])), [])
        self.assertEqual(self.run_with(item(branch='skyler/foo')), [])
        [(_, why)] = self.run_with(item(), pages={tasks.title('re', 'Conflict'): [(91, 'Re', TASK)]})
        self.assertIn('no revision by a trusted editor', why)
        self.assertFalse(Message.objects.filter(source_file=tasks.SOURCE).exists())
