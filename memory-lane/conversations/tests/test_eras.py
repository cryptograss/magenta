"""Eras come from Cryptograss:Eras, by block; nothing new is labelled with one; and an emergent's
arrival is when its first conversation was said, not when it was imported."""

import uuid
from datetime import datetime, timedelta, timezone
from unittest import mock

from django.core.cache import cache
from django.test import TestCase

from conversations.models import ContextHeap, Era as Label, Message, ThinkingEntity
from conversations.services import eras, recall

PAGE = """
== The eras ==
{{Era
|number=0
|name=Foundation
|start=21050000
|approximate=yes
|began=The first Cursor chat. With a [[link|piped]] word.
|declared_by=justin
|declared_at=26338699
}}

{{Era
|number=1
|name=The era of proto-compaction
|start=21411357
|declared_by=justin
|declared_at=26338699
}}

{{Era
|number=4
|name=The first magenta era
|start=26049707
|declared_by=justin
|declared_at=26338699
}}

<proposed by="Magent">
{{Era
|number=5
|name=The era of the emergents
|start=26342441
|declared_by=justin
|declared_at=26342441
}}
</proposed>

* {{Bot_proposes|{{Era|number=6|name=Not yet|start=27000000|declared_by=magent|declared_at=27000000}}|by=Magent}}
"""


class ReadingThePageTest(TestCase):

    def test_verified_calls_are_eras_and_proposed_ones_wait(self):
        found, waiting = eras.parse(PAGE)
        self.assertEqual([e.number for e in found], [0, 1, 4])
        self.assertEqual(waiting, 2)
        self.assertEqual(found[0].start, 21050000)
        self.assertEqual(found[2].as_dict(), {'number': 4, 'name': 'The first magenta era', 'start': 26049707,
                                              'declared_by': 'justin', 'declared_at': 26338699})

    def test_a_piped_link_in_a_value_doesnt_split_the_call(self):
        params, _ = eras.template_calls(PAGE)[0]
        self.assertEqual(params['declared_by'], 'justin')

    def test_an_era_runs_from_its_start_to_the_block_before_the_next(self):
        found, _ = eras.parse(PAGE)
        self.assertIsNone(eras.era_of_block(21049999, found))
        self.assertEqual(eras.era_of_block(21411356, found).number, 0)
        self.assertEqual(eras.era_of_block(21411357, found).number, 1)
        self.assertEqual(eras.era_of_block(26049707, found).number, 4)
        # Era 5 waits for verification, so its blocks still read as Era 4.
        self.assertEqual(eras.era_of_block(26344372, found).number, 4)
        self.assertIsNone(eras.era_of_block(None, found))

    def test_an_unreadable_page_means_no_eras_not_a_guess(self):
        cache.delete(eras.CACHE_KEY)
        with mock.patch.object(eras, 'fetch', side_effect=OSError('down')):
            self.assertEqual(eras.eras(), [])
        self.assertIsNone(cache.get(eras.CACHE_KEY))  # tried again next time

    def test_the_page_is_read_once_per_cache(self):
        cache.delete(eras.CACHE_KEY)
        with mock.patch.object(eras, 'fetch', return_value=PAGE) as fetch:
            eras.eras()
            eras.eras()
        self.assertEqual(fetch.call_count, 1)
        cache.delete(eras.CACHE_KEY)


class MessagesTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        ThinkingEntity.objects.update_or_create(name='magent', defaults={'is_biological_human': False,
                                                                          'participant_type': 'ai'})
        cls.found, _ = eras.parse(PAGE)

    def message(self, block=None, label=None):
        heap = ContextHeap.objects.create(era=Label.objects.get_or_create(name=label)[0] if label else None)
        return Message.objects.create(id=uuid.uuid4(), sender_id='magent', content='x', context_heap=heap,
                                      eth_blockheight=block)

    def test_a_message_with_a_block_takes_the_era_that_holds_it(self):
        self.assertEqual(eras.era_of(self.message(block=26100000), self.found).number, 4)

    def test_the_block_wins_over_an_import_label(self):
        message = self.message(block=26100000, label='Current Working Era (Era N)')
        self.assertEqual(eras.era_of(message, self.found).number, 4)

    def test_a_cursor_message_takes_its_era_from_its_label(self):
        self.assertEqual(eras.era_of(self.message(label='Compacting Meta-Conversation (Era 1)'), self.found).number, 1)
        self.assertEqual(eras.era_of(self.message(label='Foundation (Era 0)'), self.found).number, 0)

    def test_no_block_and_no_era_label_is_unknown(self):
        self.assertIsNone(eras.era_of(self.message(label='Post-N Era'), self.found))
        self.assertIsNone(eras.era_of(self.message(), self.found))


T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def millis(at):
    return int(at.timestamp() * 1000)


class ArrivalTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        for name in ('justin', 'blu'):
            ThinkingEntity.objects.update_or_create(name=name, defaults={'is_biological_human': name == 'justin',
                                                                          'participant_type': 'ai'})

    def say(self, sender, said, imported, session=None, heap=None):
        m = Message.objects.create(id=uuid.uuid4(), sender_id=sender, content='x', session_id=session,
                                   timestamp=millis(said) if said else None, context_heap=heap)
        Message.objects.filter(id=m.id).update(created_at=imported)
        return m

    def test_a_conversation_imported_late_still_comes_first(self):
        late, early = uuid.uuid4(), uuid.uuid4()
        self.say('blu', T0 + timedelta(days=5), T0 + timedelta(days=5), session=late)
        # Said first, but reached the record ten days later (a backfill).
        self.say('justin', T0, T0 + timedelta(days=15), session=early)
        self.say('blu', T0 + timedelta(minutes=1), T0 + timedelta(days=15), session=early)
        at, _ = recall.arrival('blu')
        self.assertEqual(at, T0)

    def test_cursor_messages_are_older_than_anything_with_a_time(self):
        cursor = ContextHeap.objects.create(era=Label.objects.create(name='Foundation (Era 0)'))
        self.say('blu', T0 - timedelta(days=300), T0, session=uuid.uuid4())
        first = self.say('blu', None, T0 + timedelta(days=2), heap=cursor)
        first.refresh_from_db()
        at, _ = recall.arrival('blu')
        self.assertEqual(at, first.created_at)

    def test_an_untimed_row_that_isnt_cursor_comes_after_the_timed_ones(self):
        self.say('blu', None, T0 - timedelta(days=2))
        self.say('blu', T0, T0, session=uuid.uuid4())
        at, _ = recall.arrival('blu')
        self.assertEqual(at, T0)


class NoNewLabelsTest(TestCase):

    def test_import_lines_labels_nothing_unless_asked(self):
        from conversations.views import import_lines
        import json
        line = json.dumps({'type': 'user', 'uuid': str(uuid.uuid4()), 'sessionId': str(uuid.uuid4()),
                           'timestamp': '2026-11-04T22:00:00.000Z', 'parentUuid': None,
                           'message': {'role': 'user', 'content': 'which era is this?'}})
        imported, _, errors = import_lines([line], source='test', username='justin')
        self.assertEqual((imported, errors), (1, []))
        self.assertFalse(Label.objects.filter(name='Current Working Era (Era N)').exists())
        self.assertIsNone(Message.objects.get(content__icontains='which era').context_heap.era)

    def test_a_turn_labels_nothing(self):
        from importers_and_parsers.events_v1 import Turn
        turn = Turn(agent='blu', mood=None, session_id=str(uuid.uuid4()))
        self.assertIsNone(turn.era)
        self.assertFalse(Label.objects.filter(name='Current Working Era (Era N)').exists())
