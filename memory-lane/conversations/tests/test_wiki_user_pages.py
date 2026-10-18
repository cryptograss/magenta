"""Edits to someone's PickiPedia user page or talk page are said as what they are,
and a talk page edited by someone else is a notice for its owner."""

import uuid
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from conversations.models import ConversationParticipant, Message, Mood, ThinkingEntity
from conversations.services.mood_view import user_page_of, wiki_sentence
from conversations.views_moods import notices_for


class WikiUserPagesTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.justin = ThinkingEntity.objects.create(name='justin', is_biological_human=True)
        cls.skyler = ThinkingEntity.objects.create(name='skymanjenkins', is_biological_human=True)
        cls.general = Mood.objects.create(slug='general', title='general')
        cls.system = ConversationParticipant.objects.get_or_create(name='system', defaults={'participant_type': 'system'})[0]

    def changed(self, title, user, comment=''):
        return Message.objects.create(id=uuid.uuid4(), sender=self.system, mood=self.general, source_file='wiki',
                                      content={'type': 'wiki', 'rcid': 1, 'kind': 'edit', 'title': title, 'user': user,
                                               'comment': comment, 'revid': 2, 'delta': 10})

    def test_said_as_what_they_are(self):
        self.assertEqual(wiki_sentence({'title': 'User:JMyles', 'user': 'SkymanJenkins'}),
                         "✎ SkymanJenkins edited JMyles's user page")
        self.assertEqual(wiki_sentence({'title': 'User:SkymanJenkins/drafts', 'user': 'SkymanJenkins'}),
                         '✎ SkymanJenkins made a user page edit')
        self.assertEqual(wiki_sentence({'title': 'User talk:JMyles', 'user': 'SkymanJenkins'}),
                         "✎ SkymanJenkins edited JMyles's talk page")
        self.assertEqual(wiki_sentence({'title': 'User talk:JMyles', 'user': 'JMyles'}),
                         '✎ JMyles edited their own talk page')
        self.assertEqual(wiki_sentence({'title': 'Tony Rice', 'user': 'JMyles', 'kind': 'new'}),
                         '✎ JMyles started Tony Rice on PickiPedia')
        self.assertIsNone(user_page_of('Cryptograss:User guide'))

    def test_a_talk_page_edited_by_someone_else_is_word_for_its_owner(self):
        since = timezone.now() - timedelta(minutes=1)
        self.changed('User talk:Justin', 'SkymanJenkins', 'about the bus')
        self.changed('User talk:Justin', 'Justin')               # their own: no notice
        self.changed('User:Justin', 'SkymanJenkins')             # the user page: said, not a notice
        [notice] = notices_for('justin', since)
        self.assertEqual((notice['kind'], notice['mood'], notice['turn']['sender']), ('talk', 'general', 'SkymanJenkins'))
        self.assertEqual(notice['turn']['text'], 'edited your talk page on PickiPedia: about the bus')
        self.assertEqual(notices_for('skymanjenkins', since), [])

    def test_the_bell_says_it_too(self):
        from conversations.services.push import notification
        self.changed('User talk:Justin', 'SkymanJenkins')
        [notice] = notices_for('justin', timezone.now() - timedelta(minutes=1))
        self.assertEqual(notification(notice)['title'], 'SkymanJenkins edited your talk page')
