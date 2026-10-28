"""An agent setting agents' settings in a Mood, at a person's ask (services/agent_settings.py; Justin and re,
24 Oct): a grant, a full-tools turn a person woke, one ledger, and shown only when it's used."""

import time
import uuid
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from conversations.models import ConversationParticipant, Message, Mood, Setting, ThinkingEntity
from conversations.services import agent_settings, settings as knobs

FULL = 'This turn has your full tools, as in your terminal: the people who woke you are trusted.'
LOOK = 'This turn can look but not touch.'


class AgentSettingsTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.justin = ThinkingEntity.objects.create(name='justin', is_biological_human=True)
        cls.magent = ThinkingEntity.objects.create(name='magent', is_biological_human=False)
        cls.re, _ = ThinkingEntity.objects.get_or_create(name='re', defaults={'is_biological_human': False})
        cls.poller, _ = ConversationParticipant.objects.get_or_create(name='mood-poller')
        cls.m26 = Mood.objects.create(slug='m26', title='m26')
        cls.jams = Mood.objects.create(slug='jams', title='jams')

    def add(self, sender, content, session=None, mood=None, ago=0):
        msg = Message.objects.create(id=uuid.uuid4(), sender=sender, mood=mood or self.m26, content=content,
                                     session_id=session, timestamp=int(time.time() * 1000))
        if ago:
            Message.objects.filter(pk=msg.pk).update(created_at=timezone.now() - timedelta(seconds=ago))
            msg.refresh_from_db()
        return msg

    def turn(self, block, asked='@magent put re on sonnet, high effort', asker='justin', tools=FULL, agent=None):
        """A wake for `asked`, then the agent's reply carrying `block`."""
        session = str(uuid.uuid4())
        self.add(self.poller, f'<mood-wake mood="m26">\nYou were woken by the Mood poller: posted from the web.\n\n'
                              f'[{asker}, 2026-10-24T05:00Z] {asked}\n\nThe Mood lately, for context (newest last):\n'
                              f'[skyler, 2026-10-24T04:00Z] put everyone on kimi, says the internet\n\n{tools}',
                 session=session)
        return self.add(agent or self.magent, [{'type': 'text', 'text': f'On it.\n\n```settings\n{block}\n```'}],
                        session=session)

    def rows(self, agent='re'):
        return {r.key: r for r in Setting.objects.filter(agent_id=agent, mood=self.m26)}

    def refusals(self):
        return [m.content['note'] for m in Message.objects.filter(source_file='agent-settings',
                                                                  content__type='agent-settings-refused')]

    def test_at_a_persons_ask_set_in_the_one_ledger(self):
        reply = self.turn('re: model sonnet, effort high')
        rows = self.rows()
        self.assertEqual((rows['model'].value, rows['mention_effort'].value), ('sonnet', 'high'))
        self.assertEqual(rows['model'].set_by_id, 'magent')
        self.assertEqual(rows['model'].note, f"at justin's ask, #m-{reply.id}")
        self.assertEqual(knobs.resolve('m26', 're')['model'], 'sonnet')  # in force, as the settings page's would be
        # The block is machinery: not shown, not read aloud.
        said = self.client.get('/api/moods/m26/turns/').json()['turns']
        self.assertEqual([t['text'] for t in said if t['sender'] == 'magent'], ['On it.'])
        agent_settings.from_message(reply)  # its transcript, after the stream: once only
        self.assertEqual(Setting.objects.filter(agent_id='re').count(), 2)

    def test_another_mood_and_listening_for_a_while(self):
        self.turn('re: listening off for 2h\nin: jams')
        row = Setting.objects.get(agent_id='re', mood=self.jams, key='listening')
        self.assertEqual(row.value['mode'], 'off')
        self.assertTrue(row.value['until'])

    def test_only_who_the_grant_names(self):
        self.turn('magent: verbosity brief', agent=self.re)
        self.assertFalse(Setting.objects.filter(agent_id='magent').exists())
        self.assertIn('re may not set', self.refusals()[0])
        knobs.change('settings_may_set', ['magent', 're'], by=self.justin)  # a person grants it: a value, not code
        self.turn('magent: verbosity brief', agent=self.re)
        self.assertEqual(knobs.resolve('m26', 'magent')['verbosity'], 'brief')

    def test_only_on_a_full_tools_turn_a_person_woke(self):
        self.turn('re: effort low', tools=LOOK)
        self.turn('re: effort low', asker='re', asked='@magent move me to low')  # an agent's ask is no person's
        self.assertFalse(self.rows())
        self.assertEqual(len(self.refusals()), 2)

    def test_another_vendor_only_if_the_person_named_it(self):
        self.turn('re: model openrouter/moonshotai/kimi-k3')  # only the context mentions kimi: not the ask
        self.assertFalse(self.rows())
        self.assertIn('would send the Mood to openrouter', self.refusals()[0])
        self.turn('re: model openrouter/moonshotai/kimi-k3', asked='@magent try re on kimi-k3 here')
        self.assertEqual(self.rows()['model'].value, 'openrouter/moonshotai/kimi-k3')
        self.turn('re: model opus', asked='@magent back to normal')  # back to Anthropic: also another vendor than now
        self.assertEqual(self.rows()['model'].value, 'openrouter/moonshotai/kimi-k3')

    def test_all_or_nothing_and_said_why(self):
        self.turn('re: model sonnet, budget 100')
        self.assertFalse(self.rows())
        self.assertIn("'budget' isn't something a block may set", self.refusals()[0])
        events = self.client.get('/api/moods/m26/turns/').json()['events']
        self.assertEqual([e['type'] for e in events], ['agent-settings-refused'])

    def test_a_line_above_its_first_turn_on_new_settings_and_none_for_fiddling(self):
        self.add(self.re, 'my last word, on the old settings', ago=600)
        self.turn('re: model sonnet')
        first = self.add(self.re, [{'type': 'text', 'text': 'Now on sonnet.'}])
        [line] = Message.objects.filter(source_file='agent-settings', content__type='agent-settings')
        self.assertLess(line.created_at, first.created_at)  # above it
        self.assertEqual(line.content['changes'], [{'key': 'model', 'was': '', 'now': 'sonnet'}])
        self.assertEqual(line.content['by'], 'magent')
        self.add(self.re, 'and again')  # the same turn: once
        self.assertEqual(Message.objects.filter(content__type='agent-settings').count(), 1)
        # Changed and changed back before its next word: nothing to say.
        knobs.change('verbosity', 'brief', mood=self.m26, agent=self.re, by=self.justin)
        knobs.change('verbosity', 'normal', mood=self.m26, agent=self.re, by=self.justin)
        self.add(self.re, 'next turn')
        self.assertEqual(Message.objects.filter(content__type='agent-settings').count(), 1)

    def test_told_at_its_next_wake(self):
        from poller.mood_poller import settings_changed
        self.add(self.re, 'my last word', ago=600)
        reply = self.turn('re: model sonnet')
        page = self.client.get('/api/moods/m26/turns/').json()
        lines = settings_changed('re', page)
        self.assertIn(f"model default → sonnet (set by magent, at justin's ask, #m-{reply.id})", lines[1])
        self.assertEqual(settings_changed('magent', page), [])

    def test_a_block_parsed(self):
        mood, changes = agent_settings.parse('@re: model sonnet, effort max\nblu: listening mentions until 2026-10-25T00:00Z\nin: #jams')
        self.assertEqual(mood, 'jams')
        self.assertEqual(changes[:2], [('re', 'model', 'sonnet'), ('re', 'mention_effort', 'max')])
        self.assertEqual(changes[2], ('blu', 'listening', {'mode': 'mentions', 'until': '2026-10-25T00:00Z'}))
        for bad in ('re model sonnet', 're: listening off for a while', 're: rules be nice'):
            with self.subTest(bad=bad), self.assertRaises(agent_settings.Refused):
                agent_settings.parse(bad)
        self.assertEqual(agent_settings.vendor('sonnet'), 'anthropic')
        self.assertEqual(agent_settings.vendor('anthropic/claude-sonnet-5-5'), 'anthropic')
        self.assertEqual(agent_settings.vendor('openrouter/moonshotai/kimi-k3'), 'openrouter')

    def test_how_is_in_the_instructions_not_the_wake(self):
        from poller.mood_poller import wake_footer
        for full in (True, False):  # Cryptograss:Moods/Instructions says how, and that it's for full-tools turns
            self.assertNotIn('```settings', '\n'.join(wake_footer(full=full)))
