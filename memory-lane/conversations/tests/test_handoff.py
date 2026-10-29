"""Context taken from one Mood to another: an agent's ```handoff block, a line in each Mood, the context in its next wake."""

import uuid

from django.core.cache import cache
from django.test import Client, TestCase

from conversations.models import Message, Mood, ThinkingEntity
from conversations.services import handoff, voice

BLOCK = ('Taking it over there.\n\n```handoff\nto: #jams-and-events\nabout: the Saturday setlist, as far as we got\n'
         '---\nSettled: Salty Dog first. Open: who sings Wildwood Flower. See #m-0f132ada.\n```\n')


class HandoffTest(TestCase):

    def setUp(self):
        cache.clear()
        self.magent = ThinkingEntity.objects.create(name='magent', is_biological_human=False)
        self.scout = ThinkingEntity.objects.create(name='scout', is_biological_human=False)
        self.justin = ThinkingEntity.objects.create(name='justin', is_biological_human=True)
        self.here = Mood.objects.create(slug='magenta-interface', title='magenta-interface')
        self.there = Mood.objects.create(slug='jams-and-events', title='jams-and-events')

    def said(self, who, text, mood=None):
        content = text if who.is_biological_human else [{'type': 'text', 'text': text}]
        return Message.objects.create(id=uuid.uuid4(), sender=who, mood=mood or self.here, timestamp=1, content=content)

    def events(self, mood):
        return [m.content for m in Message.objects.filter(mood=mood, source_file='handoff')]

    def test_the_block_is_read(self):
        rest, blocks = handoff.split(BLOCK)
        self.assertEqual(rest, 'Taking it over there.')
        self.assertEqual(blocks, [{'to': 'jams-and-events', 'from': '', 'about': 'the Saturday setlist, as far as we got', 'for': '',
                                   'context': 'Settled: Salty Dog first. Open: who sings Wildwood Flower. See #m-0f132ada.'}])

    def test_an_agents_block_leaves_a_line_in_each_mood(self):
        message = self.said(self.magent, BLOCK)  # saved: taken there at once (signals.py)
        [there] = self.events(self.there)
        self.assertEqual((there['type'], there['by'], there['for'], there['from_mood'], there['message']),
                         ('handoff', 'magent', 'magent', 'magenta-interface', str(message.id)))
        self.assertIn('Salty Dog', there['context'])
        [here] = self.events(self.here)
        self.assertEqual((here['type'], here['to_mood'], here['about']),
                         ('handoff-sent', 'jams-and-events', 'the Saturday setlist, as far as we got'))
        self.assertEqual(handoff.from_message(message), [])  # once per message
        self.assertEqual(len(self.events(self.there)), 1)

    def test_a_reply_that_is_only_the_block_is_its_line_not_an_empty_post(self):
        # Justin, 29 Oct: blu's and re's handoffs to #emergence showed as blank posts beside their lines.
        self.said(self.magent, BLOCK.split('\n\n', 1)[1])  # only the block
        self.said(self.magent, BLOCK)                         # words and the block
        page = self.client.get(f'/api/moods/{self.here.slug}/turns/').json()
        self.assertEqual([t['text'] for t in page['turns']], ['Taking it over there.'])
        self.assertEqual([e['type'] for e in page['events']], ['handoff-sent', 'handoff-sent'])

    def test_asked_to_look_in_another_mood_what_it_found_lands_here(self):
        message = self.said(self.magent, BLOCK.replace('to: #jams-and-events', 'from: #jams-and-events'))
        [here] = self.events(self.here)
        self.assertEqual((here['type'], here['from_mood'], here['pulled'], here['message']),
                         ('handoff', 'jams-and-events', True, str(message.id)))
        self.assertIn('Salty Dog', here['context'])
        [there] = self.events(self.there)
        self.assertEqual((there['type'], there['to_mood'], there['pulled']), ('handoff-sent', 'magenta-interface', True))
        page = Client().get('/api/moods/magenta-interface/turns/').json()
        self.assertEqual([(e['type'], e['pulled']) for e in page['events']], [('handoff', True)])
        self.assertNotIn('Salty Dog', str(page['turns']))  # the block, folded into the line, not shown in the message

    def test_for_another_agent_and_for_nobody_known(self):
        self.said(self.magent, BLOCK.replace('about:', 'for: scout\nabout:'))
        self.said(self.magent, BLOCK.replace('about:', 'for: ghost\nabout:'))
        self.assertEqual(sorted(e['for'] for e in self.events(self.there)), ['magent', 'scout'])

    def test_a_person_a_mood_that_isnt_and_this_mood_make_nothing(self):
        self.said(self.justin, BLOCK)
        self.said(self.magent, BLOCK.replace('#jams-and-events', 'nowhere'))
        self.said(self.magent, BLOCK.replace('#jams-and-events', 'magenta-interface'))
        self.assertEqual(Message.objects.filter(source_file='handoff').count(), 0)

    def test_the_block_is_neither_shown_nor_read_aloud_and_the_lines_are(self):
        self.said(self.magent, BLOCK)
        page = Client().get('/api/moods/magenta-interface/turns/').json()
        self.assertNotIn('Salty Dog', str(page['turns']))
        self.assertEqual([e['type'] for e in page['events']], ['handoff-sent'])
        there = Client().get('/api/moods/jams-and-events/turns/').json()['events'][0]
        self.assertEqual((there['about'], there['from_mood']), ('the Saturday setlist, as far as we got', 'magenta-interface'))
        self.assertIn('<p>', there['html'])
        self.assertEqual(voice.script_for(BLOCK)[0], 'Taking it over there.')


class WakeTest(TestCase):

    def test_whats_handed_to_this_agent_and_not_shown_it_yet(self):
        from poller.mood_poller import handoffs_for
        page = {'turns': [{'sender': 'magent', 'created_at': '2026-10-07T21:00:00+00:00'}],
                'events': [
                    {'id': 'e1', 'type': 'handoff', 'for': 'magent', 'by': 'magent', 'from_mood': 'a', 'about': 'old',
                     'context': 'shown already', 'created_at': '2026-10-07T20:00:00+00:00'},
                    {'id': 'e2', 'type': 'handoff', 'for': 'magent', 'by': 'magent', 'from_mood': 'a', 'about': 'the setlist',
                     'context': 'Salty Dog first.', 'created_at': '2026-10-07T21:05:00+00:00'},
                    {'id': 'e3', 'type': 'handoff', 'for': 'scout', 'by': 'magent', 'from_mood': 'a', 'about': 'not yours',
                     'context': '', 'created_at': '2026-10-07T21:06:00+00:00'},
                    {'id': 'e4', 'type': 'handoff', 'for': 'magent', 'by': 'magent', 'from_mood': 'b', 'about': 'brought in',
                     'context': 'it said so itself', 'created_at': '2026-10-07T21:07:00+00:00', 'pulled': True}]}
        [line] = handoffs_for(page, 'magent', shown=['e1'])
        self.assertTrue(line.startswith('[magent, from #a, 2026-10-07T21:05Z] the setlist'))
        self.assertIn('Salty Dog first.', line)

    def test_a_handoff_that_landed_while_it_was_mid_turn_is_still_owed(self):
        # Until 29 Oct the rule was "since it last spoke": a handoff landing at 20:59, answered over by a reply at
        # 21:00 to something else, was never shown. The ledger, not the clock, says what it has seen.
        from poller.mood_poller import handoffs_for
        page = {'turns': [{'sender': 'magent', 'created_at': '2026-10-07T21:00:00+00:00'}],
                'events': [{'id': 'e1', 'type': 'handoff', 'for': 'magent', 'by': 're', 'from_mood': 'a',
                            'about': 'mid-turn', 'context': 'landed as you spoke',
                            'created_at': '2026-10-07T20:59:00+00:00'}]}
        self.assertEqual(len(handoffs_for(page, 'magent')), 1)
        self.assertEqual(handoffs_for(page, 'magent', shown=['e1']), [])

    def test_a_fresh_ledger_does_not_replay_old_handoffs(self):
        from datetime import datetime, timezone
        from poller.mood_poller import handoffs_for
        page = {'turns': [], 'events': [
            {'id': 'e1', 'type': 'handoff', 'for': 'magent', 'by': 're', 'from_mood': 'a', 'about': 'last week',
             'context': 'stale', 'created_at': '2026-10-01T20:00:00+00:00'},
            {'id': 'e2', 'type': 'handoff', 'for': 'magent', 'by': 're', 'from_mood': 'a', 'about': 'today',
             'context': 'fresh', 'created_at': '2026-10-07T20:00:00+00:00'}]}
        now = datetime(2026, 10, 7, 21, tzinfo=timezone.utc)
        self.assertEqual([l.split('] ')[1].split('\n')[0] for l in handoffs_for(page, 'magent', now=now)], ['today'])
        # Only the first look: with a ledger, age is no reason to drop a handoff (re, 29 Oct).
        self.assertEqual(len(handoffs_for(page, 'magent', shown=['e2'], now=now)), 1)

    def test_how_is_in_the_instructions_not_each_wake(self):
        from poller.mood_poller import wake_frames
        for frame in wake_frames('general'):  # Cryptograss:Moods/Instructions says how
            self.assertNotIn('```handoff', frame['text'], frame['kind'])
