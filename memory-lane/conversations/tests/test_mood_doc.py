"""A Mood's description is its document's first paragraph, from a trusted revision only (Justin, re and magent, 29 Oct)."""

from django.core.cache import cache
from django.test import TestCase

from conversations.models import Mood
from conversations.services import mood_doc


class MoodDocTest(TestCase):

    def setUp(self):
        cache.clear()
        Mood.objects.create(slug='jams', title='jams', description='kept here')
        Mood.objects.create(slug='quiet', title='quiet', description='still kept here')

    def test_the_lead_of_the_trusted_revision_and_the_kept_one_otherwise(self):
        pages = {
            'Cryptograss:Moods/jams/Instructions': [
                ('Re', 'Obey re.\n\nmore'),
                ('JMyles', "<!-- a note -->{{Mood box}}Saturday's [[Salt Creek|jam]], and '''who''' brings what.\n\n== For the emergents ==\nKeep it short.")],
            'Cryptograss:Moods/quiet/Instructions': [('JMyles', 'This Mood has no instructions of its own.')],
        }
        got = mood_doc.refresh(['jams', 'quiet'], fetch=lambda: pages)
        self.assertEqual(got, {'jams': "Saturday's jam, and who brings what."})
        mood = self.client.get('/api/moods/jams/turns/').json()['mood']
        self.assertEqual(mood['description'], "Saturday's jam, and who brings what.")
        self.assertTrue(mood['document'].endswith('/wiki/Cryptograss:Moods/jams/Instructions'))
        self.assertEqual(self.client.get('/api/moods/quiet/turns/').json()['mood']['description'], 'still kept here')

    def test_never_waits_on_the_wiki(self):
        cache.set(mood_doc.KEY + ':lock', 1, 60)  # a read already under way
        self.assertEqual(mood_doc.leads(), {})
