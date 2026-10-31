"""The verify section (Justin, 31 Oct): every page the emergents are told from, and which wait for a verify."""

import uuid

from django.test import TestCase, override_settings
from django.utils import timezone

from conversations.models import BundleRecord
from conversations.services import verify


def bundle(**layers):
    BundleRecord.objects.create(id=uuid.uuid4(), agent='re', mood='jams', requested_at=timezone.now(), sha256='0' * 64,
                                stamps={'greeting': {'page': 'Cryptograss:Emergents/Greeting',
                                                     'agent_page': {'page': 'Cryptograss:Emergents/Re'}},
                                        'layers': layers})


@override_settings(MOOD_WIKI_RULES_EDITORS=['JMyles'])
class VerifyTest(TestCase):

    def test_pages_come_from_the_bundles_and_the_wake(self):
        bundle(standing={'page': 'Cryptograss:Emergents/GeneralOrders'},
               mood={'page': 'Cryptograss:Moods/jams/Instructions', 'absent': True})  # no page: not listed
        self.assertEqual(verify.watched(), sorted([
            'Cryptograss:Emergents/Greeting', 'Cryptograss:Emergents/Re', 'Cryptograss:Emergents/GeneralOrders',
            'Cryptograss:Emergents/Wake', 'Cryptograss:Moods/Dials', 'Cryptograss:Pronouns']))

    def test_each_page_says_whether_it_waits_and_where_to_verify(self):
        bundle(standing={'page': 'Cryptograss:Emergents/GeneralOrders'})
        histories = {
            'Cryptograss:Emergents/GeneralOrders': [(30, 'Magent', ''), (29, 'Re', ''), (20, 'JMyles', '')],
            'Cryptograss:Emergents/Wake': [(12, 'Magent', '')],
            'Cryptograss:Moods/Dials': [],
        }

        def history(title):
            if title == 'Cryptograss:Pronouns':
                raise OSError('down')
            return histories.get(title, [(5, 'JMyles', '')])

        found = verify.state(history=history)
        rows = {r['page']: r for r in found['pages']}
        orders = rows['Cryptograss:Emergents/GeneralOrders']
        self.assertEqual((orders['state'], orders['trusted'], orders['edits'], orders['editors']),
                         ('waiting', 20, 2, ['Magent', 'Re']))
        self.assertIn('index.php?diff=30&oldid=20', orders['diff'])
        self.assertEqual(rows['Cryptograss:Emergents/Wake']['state'], 'untrusted')
        self.assertEqual(rows['Cryptograss:Moods/Dials']['state'], 'missing')
        self.assertEqual(rows['Cryptograss:Pronouns']['state'], 'unread')
        self.assertEqual(rows['Cryptograss:Emergents/Re']['state'], 'verified')
        self.assertIn("PickiPedia couldn't be read", found['error'])
        self.assertEqual(found['waiting'], 3)
        states = [r['state'] for r in found['pages']]  # what needs a person first, then the rest
        self.assertEqual((sorted(states[:2]), states[2], states[-1]), (['missing', 'untrusted'], 'waiting', 'verified'))

    def test_the_section_renders(self):
        verify.cache.set(verify.KEY, {'pages': [{'page': 'Cryptograss:Moods/Dials', 'url': 'https://x/wiki/D',
                                                 'state': 'waiting', 'trusted': 1, 'edits': 1, 'editors': ['Re'],
                                                 'diff': 'https://x/index.php?diff=2&oldid=1'}],
                                      'editors': ['JMyles'], 'waiting': 1, 'error': ''}, 60)
        html = self.client.get('/moods/verify/?embed=1').content.decode()
        self.assertIn('see the change and verify', html)
        self.assertIn('1 needs a look', html)
        self.assertEqual(self.client.get('/api/verify/').json()['waiting'], 1)
