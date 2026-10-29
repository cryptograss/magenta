"""Asks from a look-only turn (Justin, 28 Oct-29 Oct): <tools/>, <merge/>, <deploy/> are lines in the Mood and
🔨/🔀/🔄 by its name in the list, until their event lands there -- a full-tools wake answers the tools ask, a
merged event the merge ask, a deploy event the deploy ask."""

import uuid

from django.core.cache import cache
from django.test import TestCase

from conversations.models import ConversationParticipant, Message, Mood, ThinkingEntity
from conversations.services.mood_view import quiet_reason
from poller.mood_poller import _SILENT_REPLY, wake_footer


class AskTest(TestCase):

    def setUp(self):
        cache.clear()
        ThinkingEntity.objects.get_or_create(name='re', defaults={'is_biological_human': False})
        ThinkingEntity.objects.get_or_create(name='mood-poller', defaults={'is_biological_human': False})
        self.system, _ = ConversationParticipant.objects.get_or_create(name='system',
                                                                       defaults={'participant_type': 'system'})
        self.mood = Mood.objects.create(slug='jams', title='jams')
        self.sid = uuid.uuid4()

    def say(self, who, text, n, session=None, source=None, content=None):
        return Message.objects.create(id=uuid.uuid4(), sender_id=who, mood=self.mood, session_id=session or self.sid,
                                      timestamp=n,
                                      stop_reason='end_turn' if who == 're' and source is None else None,
                                      source_file=source or '',
                                      content=content if content is not None else [{'type': 'text', 'text': text}])

    def asked(self):
        moods = self.client.get('/api/moods/').json()['moods']
        return next((m.get('asked', []) for m in moods if m['slug'] == 'jams'), [])

    def test_three_kinds_of_ask_are_read_and_silent(self):
        for tag, type_, reason in (('<tools to="push the fix"/>', 'tools', 'requested tools for push the fix'),
                                   ('<merge to="the polls PR"/>', 'merge', 'requested a merge for the polls PR'),
                                   ('<deploy to="hunter"/>', 'deploy', 'requested a redeploy of hunter')):
            self.assertEqual(quiet_reason(tag), {'reason': reason, 'by': '', 'type': type_})
            self.assertTrue(_SILENT_REPLY.match(tag))
        self.assertIn('<tools to=', '\n'.join(wake_footer(full=False)))
        self.assertNotIn('<tools to=', '\n'.join(wake_footer(full=True)))

    def test_each_ask_is_a_line_and_stays_until_its_event_lands(self):
        self.say('mood-poller', '<mood-wake mood="jams"> ... This turn can look but not touch ...', 1)
        self.say('re', '<tools to="push the fix"/>', 2, session=self.sid)
        self.say('re', '<merge to="the polls PR"/>', 3, session=self.sid)
        self.say('re', '<deploy to="hunter"/>', 4, session=self.sid)
        page = self.client.get('/api/moods/jams/turns/').json()
        self.assertEqual([(q['sender'], q['reason'], q['type']) for q in page['quiet']],
                         [('re', 'requested tools for push the fix', 'tools'),
                          ('re', 'requested a merge for the polls PR', 'merge'),
                          ('re', 'requested a redeploy of hunter', 'deploy')])
        got = self.asked()
        self.assertEqual([(t['kind'], t['agent'], t['for']) for t in got],
                         [('tools', 're', 'push the fix'), ('merge', 're', 'the polls PR'),
                          ('deploy', 're', 'hunter')])
        self.assertTrue(all(t['id'] for t in got))  # the notice links to the message
        # A full-tools wake answers only the tools ask.
        self.say('mood-poller', '<mood-wake mood="jams"> ... This turn has your full tools ...', 5, session=self.sid)
        cache.clear()
        self.assertEqual([(t['kind'], t['for']) for t in self.asked()],
                         [('merge', 'the polls PR'), ('deploy', 'hunter')])
        # A merged event answers the merge ask.
        self.say('system', 'merged', 6, session=None, source='merged',
                 content={'type': 'merged', 'title': 'polls', 'by': 'skyler', 'about': ''})
        cache.clear()
        self.assertEqual([(t['kind'], t['for']) for t in self.asked()], [('deploy', 'hunter')])
        # A deploy event answers the deploy ask.
        self.say('system', 'deploy', 7, session=None, source='deploy',
                 content={'type': 'deploy', 'server': 'hunter', 'state': 'finished', 'by': 'justin', 'note': ''})
        cache.clear()
        self.assertEqual(self.asked(), [])

    def test_a_newer_ask_replaces_an_older_ask_of_the_same_kind(self):
        self.say('re', '<tools to="push the fix"/>', 1, session=self.sid)
        self.say('re', '<tools to="rebase first, then push"/>', 2, session=self.sid)
        self.assertEqual([(t['kind'], t['for']) for t in self.asked()],
                         [('tools', 'rebase first, then push')])
    def test_answered_as_its_full_tools_wake_lands_before_it_says_a_word(self):
        """Justin, 29 Oct: the 🔨 stayed until the turn ended. The wake's bundle is the agent's from the
        start, so an ask is answered the moment the wake lands, not when the agent first speaks."""
        from django.utils import timezone
        from conversations.models import BundleRecord
        self.say('re', '<tools to="push the fix"/>', 2, session=self.sid)
        self.assertEqual([(t['kind'], t['for']) for t in self.asked()], [('tools', 'push the fix')])
        bundle, fresh = uuid.uuid4(), uuid.uuid4()
        BundleRecord.objects.create(id=bundle, agent='re', mood='jams', requested_at=timezone.now(), sha256='0' * 64)
        Message.objects.create(id=uuid.uuid4(), sender_id='mood-poller', mood=self.mood, session_id=fresh, timestamp=3,
                               content=[{'type': 'text', 'text': '<mood-wake mood="jams"> ... This turn has your full tools, '
                                         f'as in your terminal ... Your instructions this turn are bundle {bundle} (Special:AgentBundles).'}])
        cache.clear()
        self.assertEqual(self.asked(), [])
