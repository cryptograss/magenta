"""A look-only turn asking for its full tools (Justin, 28 Oct): a line in the Mood, and 🔨 in the list until it has them."""

import uuid

from django.core.cache import cache
from django.test import TestCase

from conversations.models import Message, Mood, ThinkingEntity
from conversations.services.mood_view import quiet_reason
from poller.mood_poller import _SILENT_REPLY, wake_footer


class ToolsAskTest(TestCase):

    def setUp(self):
        cache.clear()
        ThinkingEntity.objects.get_or_create(name='re', defaults={'is_biological_human': False})
        ThinkingEntity.objects.get_or_create(name='mood-poller', defaults={'is_biological_human': False})
        self.mood = Mood.objects.create(slug='jams', title='jams')
        self.sid = uuid.uuid4()

    def say(self, who, text, n):
        return Message.objects.create(id=uuid.uuid4(), sender_id=who, mood=self.mood, session_id=self.sid, timestamp=n,
                                      stop_reason='end_turn' if who == 're' else None, content=[{'type': 'text', 'text': text}])

    def test_asked_shown_and_answered(self):
        self.assertEqual(quiet_reason('<tools to="push the fix"/>'),
                         {'reason': 'requested tools for push the fix', 'by': '', 'type': 'tools'})
        self.assertTrue(_SILENT_REPLY.match('<tools to="push the fix"/>'))
        self.assertIn('<tools to=', '\n'.join(wake_footer(full=False)))
        self.assertNotIn('<tools to=', '\n'.join(wake_footer(full=True)))
        self.say('mood-poller', '<mood-wake mood="jams"> ... This turn can look but not touch ...', 1)
        self.say('re', '<tools to="push the fix"/>', 2)
        page = self.client.get('/api/moods/jams/turns/').json()
        self.assertEqual([(q['sender'], q['reason']) for q in page['quiet']], [('re', 'requested tools for push the fix')])
        jams = self.client.get('/api/moods/').json()['moods'][0]
        self.assertEqual([(t['agent'], t['for']) for t in jams['tools_asked']], [('re', 'push the fix')])
        self.say('mood-poller', '<mood-wake mood="jams"> ... This turn has your full tools, as in your terminal ...', 3)
        cache.clear()
        self.assertNotIn('tools_asked', self.client.get('/api/moods/').json()['moods'][0])

    def test_answered_as_its_full_tools_wake_lands_before_it_says_a_word(self):
        """Justin, 29 Oct: the dot stayed until the turn ended. The wake's bundle is the agent's from the start."""
        from django.utils import timezone
        from conversations.models import BundleRecord
        self.say('re', '<tools to="push the fix"/>', 2)
        self.assertTrue(self.client.get('/api/moods/').json()['moods'][0]['tools_asked'])
        bundle, fresh = uuid.uuid4(), uuid.uuid4()
        BundleRecord.objects.create(id=bundle, agent='re', mood='jams', requested_at=timezone.now(), sha256='0' * 64)
        Message.objects.create(id=uuid.uuid4(), sender_id='mood-poller', mood=self.mood, session_id=fresh, timestamp=3,
                               content=[{'type': 'text', 'text': '<mood-wake mood="jams"> ... This turn has your full tools, '
                                         f'as in your terminal ... Your instructions this turn are bundle {bundle} (Special:AgentBundles).'}])
        cache.clear()
        self.assertNotIn('tools_asked', self.client.get('/api/moods/').json()['moods'][0])
