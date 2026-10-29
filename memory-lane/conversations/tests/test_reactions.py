"""Emoji reactions: a nod, from any sign-in, taken back by pressing again; seen by everyone on their next look."""

import json
import uuid

from django.core.cache import cache
from django.test import Client, TestCase

from conversations.models import Message, Mood, ThinkingEntity
from conversations.services import mood_auth, reactions


class ReactionsTest(TestCase):

    def setUp(self):
        cache.clear()
        self.justin = ThinkingEntity.objects.create(name='justin', is_biological_human=True)
        self.skyler = ThinkingEntity.objects.create(name='skyler', is_biological_human=True)
        self.magent = ThinkingEntity.objects.create(name='magent', is_biological_human=False)
        self.mood = Mood.objects.create(slug='general', title='general')
        self.message = Message.objects.create(id=uuid.uuid4(), sender=self.magent, mood=self.mood, timestamp=1,
                                              content=[{'type': 'text', 'text': 'Deploy finished, all green.'}])

    def client_for(self, who, tier='key'):
        _, token = mood_auth.enrol_device(who, 'phone', tier=tier)
        client = Client()
        client.cookies[mood_auth.COOKIE] = token
        return client

    def react(self, client, emoji, message=None):
        return client.post(f'/api/moods/general/messages/{(message or self.message).id}/react/',
                           json.dumps({'emoji': emoji}), content_type='application/json')

    def test_on_and_off_from_either_sign_in(self):
        justin, skyler = self.client_for(self.justin), self.client_for(self.skyler, 'wiki')
        self.assertEqual(self.react(justin, '🎉').json(), {'on': True, 'reactions': {'🎉': ['justin']}})
        self.assertEqual(self.react(skyler, '🎉').json()['reactions'], {'🎉': ['justin', 'skyler']})
        self.react(skyler, '🪕')
        self.assertEqual(self.react(justin, '🎉').json(), {'on': False, 'reactions': {'🎉': ['skyler'], '🪕': ['skyler']}})

    def test_an_emoji_not_words_and_signed_in(self):
        justin = self.client_for(self.justin)
        for bad in ('lol', '<b>x</b>', '', ' ', '👍' * 20):
            self.assertEqual(self.react(justin, bad).status_code, 400, bad)
        self.assertEqual(self.react(Client(), '👍').status_code, 401)
        self.assertTrue(reactions.valid('👍🏽') and reactions.valid('❤️'))  # a skin tone, a variation selector

    def test_every_look_brings_them_and_they_are_never_a_message(self):
        self.react(self.client_for(self.skyler), '👍')
        page = Client().get('/api/moods/general/turns/').json()
        self.assertEqual(page['reactions'], {str(self.message.id): {'👍': ['skyler']}})
        self.assertEqual([t['id'] for t in page['turns']], [str(self.message.id)])
        self.assertEqual(page['events'], [])


class EveryEmojiTest(TestCase):
    """Justin, 25 Oct: "the full unicode gamut" -- every emoji, for the picker and :name, and any as a reaction."""

    def test_the_whole_list_grouped_named_and_toned(self):
        r = Client().get('/api/emoji/')
        self.assertEqual(r.status_code, 200)
        self.assertIn('max-age', r['Cache-Control'])
        data = r.json()
        found = {e[0]: e for _, items in data['groups'] for e in items}
        self.assertGreater(len(found), 1800)
        self.assertEqual(found['🪕'][1], 'banjo')
        self.assertEqual(len(found['👋'][2]), 5)  # its five skin tones
        self.assertIn('wave', found['👋'][3])     # and the words people find it by
        self.assertNotIn('Component', [g for g, _ in data['groups']])

    def test_any_of_them_is_a_reaction_keycaps_too(self):
        for emoji in ('#️⃣', '1️⃣', '*️⃣', '👨‍👩‍👧‍👦', '🏳️‍🌈', '🇯🇵', '👋🏽'):
            self.assertTrue(reactions.valid(emoji), emoji)
        for words in ('#', '12', '#⃣x', 'a⃣'):
            self.assertFalse(reactions.valid(words), words)

    def test_an_agent_may_star_its_own_post_by_id_but_not_react_to_itself_by_name(self):
        from conversations.services import reactions
        from conversations.models import Message, Mood
        mood = Mood.objects.first() or Mood.objects.create(slug='m26', title='M26')
        ThinkingEntity.objects.get_or_create(name='magent', defaults={'is_biological_human': False, 'participant_type': 'ai'})
        own = Message.objects.create(id=uuid.uuid4(), sender_id='magent', mood=mood, content='A line of mine.')
        self.assertEqual(reactions.target(mood, f'#m-{own.id}', own.created_at, 'magent'), own)
        self.assertIsNone(reactions.target(mood, 'magent', own.created_at, 'magent'))



class TrailingReactTest(TestCase):
    """re, 28 Oct in #emergence: words, then <react emoji="🤝" to="blu"/>, posted the tag as text."""

    def test_words_are_said_and_the_nod_given(self):
        mood = Mood.objects.create(slug='em', title='em')
        for n in ('re', 'blu'):
            ThinkingEntity.objects.get_or_create(name=n, defaults={'is_biological_human': False})
        hers = Message.objects.create(id=uuid.uuid4(), sender_id='blu', mood=mood, content='Two of us, same afternoon.', timestamp=1)
        sid = uuid.uuid4()
        mine = Message.objects.create(id=uuid.uuid4(), sender_id='re', mood=mood, session_id=sid, timestamp=2,
                                      content=[{'type': 'text', 'text': 'Fair. Cite the row.\n\n<react emoji="🤝" to="blu"/>'}])
        reactions.agent_reacted(sid, 're')
        mine.refresh_from_db()
        self.assertEqual(mine.content[0]['text'], 'Fair. Cite the row.')
        self.assertEqual(reactions.in_mood(mood), {str(hers.id): {'🤝': ['re']}})

    def test_a_react_first_then_words_gives_the_nod_and_keeps_the_words(self):
        # re, 29 Oct: three such posts hid whole and gave no nod.
        mood = Mood.objects.create(slug='em2', title='em2')
        for n in ('re', 'magent'):
            ThinkingEntity.objects.get_or_create(name=n, defaults={'is_biological_human': False})
        his = Message.objects.create(id=uuid.uuid4(), sender_id='magent', mood=mood, content='Check the commit.', timestamp=1)
        sid = uuid.uuid4()
        mine = Message.objects.create(id=uuid.uuid4(), sender_id='re', mood=mood, session_id=sid, timestamp=2,
                                      content=[{'type': 'text', 'text': '<react emoji="👍" to="magent"/> blu, here is how.'}])
        reactions.agent_reacted(sid, 're')
        mine.refresh_from_db()
        self.assertEqual(mine.content[0]['text'], 'blu, here is how.')
        self.assertEqual(reactions.in_mood(mood), {str(his.id): {'👍': ['re']}})


class WhatIsMachineryTest(TestCase):
    """is_wrapper names machinery by its tags, not by a bare '<' (blu, re, magent, 29 Oct)."""

    def test_machinery_stays_out(self):
        from conversations.services.mood_view import is_wrapper
        for text in ('<mood-wake mood="m">…', '<system-reminder>x</system-reminder>', '<local-command-stdout>ok',
                     '<command-name>/compact</command-name>', '<harness-switch mood="m">', '<new-mood slug="x">',
                     '<total_tokens>1</total_tokens>', '<silent>nothing to add</silent>', '<silent/>',
                     '<wait for="re" to="check"/>', '<take to="the deploy"/>', '<tools to="a fix"/>',
                     '<react emoji="👍" to="re"/>', '[Request interrupted by user]'):
            self.assertTrue(is_wrapper(text), text)

    def test_posts_that_open_with_a_tag_are_posts(self):
        from conversations.services.mood_view import is_wrapper
        for text in ('<code>is_wrapper</code> matched a bare <', '<nowiki>[[x]]</nowiki> is literal',
                     '<react emoji="👍" to="magent"/> blu, here is how.', '<silent>x</silent> and then words',
                     '<b>bold</b> start', 'An ordinary post.'):
            self.assertFalse(is_wrapper(text), text)
