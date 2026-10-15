"""Anything in a Mood can be replied to: a redeploy, a merge, a release, a change on the wiki."""

import uuid

from django.test import TestCase

from conversations.models import ConversationParticipant, Message, Mood, ThinkingEntity
from conversations.services.mood_view import event_line, replied


class ReplyToEventsTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.justin = ThinkingEntity.objects.create(name='justin', is_biological_human=True)
        cls.system = ConversationParticipant.objects.create(name='system', participant_type='system')
        cls.mood = Mood.objects.create(slug='m26')

    def event(self, **content):
        return Message.objects.create(id=uuid.uuid4(), sender=self.system, mood=self.mood, content=content,
                                      source_file=content['type'])

    def test_each_event_says_what_happened(self):
        self.assertEqual(event_line({'type': 'deploy', 'server': 'maybelle', 'state': 'finished', 'commit': 'd5a526b9ffff', 'by': 'JMyles'}),
                         '⚙ maybelle redeployed · d5a526b9 by JMyles')
        self.assertEqual(event_line({'type': 'merged', 'pull': 'memory-lane#155', 'commit': '103d158c', 'by': 'jMyles',
                                     'title': 'A run of messages'}),
                         '✓ memory-lane#155 · 103d158c merged by jMyles: A run of messages')
        self.assertEqual(event_line({'type': 'release', 'title': 'Water Tower', 'by': 'Kenny'}), '💿 a new release: Water Tower by Kenny')
        self.assertEqual(event_line({'type': 'wiki', 'user': 'JMyles', 'title': 'Maybelle', 'kind': 'edit'}),
                         '✎ JMyles edited Maybelle on PickiPedia')
        self.assertEqual(event_line({'type': 'access', 'kind': 'ssh', 'who': 'justin', 'server': 'maybelle', 'as': 'root'}),
                         "🖥 justin ssh'd into maybelle as root")
        self.assertEqual(event_line({'type': 'something-new'}), 'something new')

    def test_a_reply_to_an_event_quotes_it_and_addresses_nobody(self):
        deploy = self.event(type='deploy', server='hunter', state='failed', commit='abcdef12')
        self.assertEqual(replied(deploy.id), {'id': str(deploy.id), 'sender': '', 'snippet': '⚙ hunter failed to redeploy · abcdef12',
                                              'is_human': False, 'event': True})
        Message.objects.create(id=uuid.uuid4(), sender=self.justin, mood=self.mood, source_file='mood-web',
                               content=f'↩ #m-{deploy.id}\nwhy did this fail?')
        [turn] = self.client.get('/api/moods/m26/turns/').json()['turns']
        self.assertEqual((turn['reply']['event'], turn['reply']['snippet']), (True, '⚙ hunter failed to redeploy · abcdef12'))
        self.assertEqual(turn['mentions'], [])

    def test_the_agent_reads_the_quote_not_just_the_link(self):
        from poller.mood_poller import transcript_of
        [line] = transcript_of([{'id': 'a', 'sender': 'justin', 'created_at': '2026-10-14T00:30:00',
                                 'text': '↩ #m-11111111-2222-3333-4444-555555555555\nwhy so slow?',
                                 'reply': {'id': '11111111-2222-3333-4444-555555555555', 'sender': '',
                                           'snippet': '⚙ maybelle redeployed · d5a526b9'}}])
        self.assertEqual(line, '[justin, 2026-10-14T00:30Z] (↩ to “⚙ maybelle redeployed · d5a526b9”, '
                               '#m-11111111-2222-3333-4444-555555555555) why so slow?')
