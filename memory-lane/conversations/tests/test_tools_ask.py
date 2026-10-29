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

    def test_a_full_tools_wake_in_another_mood_answers_it(self):
        """Tools are the emergent's, not the Mood's (re, 29 Oct)."""
        self.say('re', '<tools to="re-stamp the Memory page"/>', 2)
        self.assertTrue(self.client.get('/api/moods/').json()['moods'][0]['tools_asked'])
        ops, sid = Mood.objects.create(slug='ops', title='ops'), uuid.uuid4()
        for who, text, n in (('re', 'earlier, in ops', 1),
                             ('mood-poller', '<mood-wake mood="ops"> ... This turn has your full tools, as in your terminal ...', 3)):
            Message.objects.create(id=uuid.uuid4(), sender_id=who, mood=ops, session_id=sid, timestamp=n,
                                   content=[{'type': 'text', 'text': text}])
        cache.clear()
        self.assertFalse(any(m.get('tools_asked') for m in self.client.get('/api/moods/').json()['moods']))
