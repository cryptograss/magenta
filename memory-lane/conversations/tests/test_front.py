"""magenta's front page (Justin, 22 Oct): the mark and who it's for, the top of Tidings, and who has
spoken lately and where -- names, never what was said."""

import uuid
from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from conversations.models import ConversationParticipant, Message, Mood, ThinkingEntity, Thought, TidingVote
from conversations.services import front, links, tiding_links


class FrontPageTest(TestCase):

    def setUp(self):
        cache.clear()
        links._memo.update(at=0.0, index=None)
        self.justin = ThinkingEntity.objects.create(name='justin', is_biological_human=True)
        self.skyler = ThinkingEntity.objects.create(name='skyler', is_biological_human=True)
        self.magent = ThinkingEntity.objects.create(name='magent', is_biological_human=False)
        self.poller = ConversationParticipant.objects.create(name='mood-poller')
        self.jams = Mood.objects.create(slug='jams', title='jams')
        self.dev = Mood.objects.create(slug='magenta-interface', title='magenta interface')
        self.n = 0

    def say(self, who, mood, text, model=Message, **fields):
        self.n += 1
        row = model.objects.create(id=uuid.uuid4(), sender=who, mood=mood, content=text, timestamp=self.n,
                                   source_file='mood-web', **fields)
        Message.objects.filter(id=row.id).update(created_at=timezone.now() + timedelta(seconds=self.n))
        return row

    def test_it_is_what_the_bare_address_shows_to_anyone(self):
        page = self.client.get('/')
        self.assertEqual(page.status_code, 200)
        html = page.content.decode()
        self.assertIn('A place for hackers, bluegrassers, deadheads, and free folk of middle internet.', html)
        self.assertIn('/moods/mark-192.png', html)
        self.assertIn('href="/moods/"', html)

    def test_the_top_three_links_most_upvoted_first(self):
        made = []
        for i in range(4):
            said = self.say(self.skyler, self.jams, f'https://example.org/{i}')
            made.append(tiding_links.promote(said, self.justin, title=f'Link {i}'))
        TidingVote.objects.create(tiding=made[1], by=self.skyler)
        TidingVote.objects.create(tiding=made[1], by=self.justin)
        TidingVote.objects.create(tiding=made[0], by=self.justin)
        self.assertEqual([t['title'] for t in front.top_tidings()], ['Link 1', 'Link 0', 'Link 3'])
        html = self.client.get('/').content.decode()
        self.assertIn(f'/moods/tidings/{made[1].id}/', html)
        self.assertNotIn('Link 2', html)

    def test_who_spoke_lately_and_where_not_what(self):
        self.say(self.justin, self.jams, 'a secret-ish thing said in jams')
        self.say(self.poller, self.dev, '<mood-wake mood="magenta-interface">...</mood-wake>')  # the poller's wake
        self.say(self.magent, self.dev, [{'type': 'thinking', 'thinking': 'hmm'}], model=Thought)  # thinking
        self.say(self.magent, self.dev, [{'type': 'text', 'text': 'Built it.'}])
        self.say(self.magent, self.dev, [{'type': 'text', 'text': 'And tested it.'}])  # the same turn: one line
        self.say(self.magent, self.dev, [{'type': 'text', 'text': '<silent>nothing to add</silent>'}])  # a silence
        self.say(self.skyler, self.jams, 'see you there')
        self.assertEqual([(s['who'], s['mood']) for s in front.lately()],
                         [('skyler', 'jams'), ('magent', 'magenta-interface'), ('justin', 'jams')])
        html = self.client.get('/').content.decode()
        self.assertIn('#magenta interface', html)
        self.assertIn('--agent: #ff67fd', html)  # magent in its own colour
        self.assertNotIn('secret-ish', html)
        self.assertNotIn('Built it', html)

    def test_an_archived_mood_is_not_shown(self):
        from conversations.services import settings as knobs
        self.say(self.justin, self.jams, 'old news')
        with mock.patch.object(knobs, 'archived_slugs', return_value={'jams'}):
            self.assertEqual(front.lately(), [])

    def test_a_links_address_opens_the_one_page_at_its_discussion(self):
        said = self.say(self.skyler, self.jams, 'https://example.org/x')
        tiding = tiding_links.promote(said, self.justin, title='X')
        page = self.client.get(f'/moods/tidings/{tiding.id}/').content.decode()
        self.assertIn('tidingAt', page)  # the one page, which opens the discussion in its Tidings frame
