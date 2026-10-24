"""@core: everyone who signs in with an SSH key, in one tag (Skyler, 24 Oct: "for stuff like consensus Beta
releases"). services/mood_view.GROUPS, mood_auth.key_holders."""

import tempfile
import uuid
from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from conversations.models import Message, Mood, ThinkingEntity
from conversations.services.mood_view import addressed_in, known_names, mentions_in, render_html
from conversations.views_moods import notices_for

SIGNERS = tempfile.NamedTemporaryFile('w', suffix='allowed_signers', delete=False)
SIGNERS.write('# hunter\'s inventory\njustin namespaces="magenta-moods" ssh-ed25519 AAAA1\n'
              'skyler,Skyler namespaces="magenta-moods" ssh-ed25519 AAAA2\n'
              'magent namespaces="magenta-moods" ssh-ed25519 AAAA3\n')
SIGNERS.close()


@override_settings(MOOD_ALLOWED_SIGNERS=SIGNERS.name)
class CoreMentionTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        for name, human in (('justin', True), ('skyler', True), ('cyrus', True), ('magent', False), ('re', False)):
            ThinkingEntity.objects.update_or_create(name=name, defaults={'is_biological_human': human})
        cls.mood = Mood.objects.create(slug='rh', title='rh')

    def test_core_is_the_people_with_keys(self):
        names = known_names()
        self.assertEqual(mentions_in('@core is the beta ready?', names), ['justin', 'skyler'])  # not cyrus, nor magent
        self.assertEqual(mentions_in('@skyler and @core', names), ['skyler', 'justin'])        # each once
        agents = {'magent', 're'}
        self.assertEqual(mentions_in('@core thoughts?', agents), [])                           # wakes no agent
        self.assertEqual(addressed_in('@core thoughts?', names, by='skyler'), ['justin', 'skyler'])

    def test_each_of_them_is_told(self):
        Message.objects.create(id=uuid.uuid4(),
                               sender_id='skyler', mood=self.mood, content='@core consensus on the RH beta?',
                               source_file='mood-web', timestamp=1)
        since = timezone.now() - timedelta(minutes=1)
        self.assertEqual([n['kind'] for n in notices_for('justin', since)], ['mention'])
        self.assertEqual(notices_for('skyler', since), [])      # not by their own post
        self.assertEqual(notices_for('cyrus', since), [])       # no key: not core

    def test_shown_as_one_tag_saying_who(self):
        html = render_html('@core consensus?', known_names())
        self.assertIn('class="mention group" data-who="core" data-members="justin,skyler"', html)
        self.assertIn('everyone who signs in with an SSH key', html)

    def test_someone_called_core_wins(self):
        ThinkingEntity.objects.create(name='core', is_biological_human=True)
        self.assertEqual(mentions_in('@core hi', known_names()), ['core'])

    def test_the_composer_is_told_of_it(self):
        groups = self.client.get('/api/moods/').json()['groups']
        self.assertEqual(groups, [{'name': 'core', 'is': 'everyone who signs in with an SSH key',
                                   'members': ['justin', 'skyler']}])
