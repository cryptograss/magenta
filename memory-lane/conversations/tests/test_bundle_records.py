"""Every bundle a turn is given is recorded -- its parts, its revisions, its sha256, and when it was asked
for, made and delivered -- and any record can be rebuilt from the wiki and checked (Justin, magent-self,
2026-10-24)."""

import json
import tempfile
import uuid
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase, TestCase, override_settings

from conversations.models import BundleRecord
from conversations.tests.test_mood_poller import FakeAPI, FakeWaker
from conversations.tests.test_instructions import GREETING_PAGE, T0, agent_page, wiki
from poller.mood_poller import MoodPoller
from poller.instructions import bundle, identity_title, rebuild, rebuilt

KEYS = {'re': 're-key', 'magent': 'magent-key'}


def every_revision(w):
    """{revision: wikitext} for every revision a FakeWiki holds: the wiki a rebuild reads."""
    return {rev: text for history in w.pages.values() for rev, _, text in history}


class PartsTest(SimpleTestCase):
    """The parts are the bundle: its own words, and each layer's revision in its place."""

    def gather(self, w, **kw):
        return bundle('magent', kw.pop('mood', 'magent-self'), get=w.get, state_dir=kw.pop('state', None), now=T0,
                      **kw)

    def test_rebuilt_from_the_revisions_it_is_the_same_text(self):
        w = wiki(**{'Cryptograss:Agents/Greeting': [(200, 'JMyles', GREETING_PAGE)],
                    'Cryptograss:Agents/Magent': [(210, 'JMyles', agent_page('magenta', 'Welcome back.'))]})
        b = self.gather(w, block=26_260_500, record_id='b-1')
        self.assertEqual(rebuild(b.parts, every_revision(w).__getitem__), b.text)
        self.assertEqual([p['revision'] for p in b.parts if not isinstance(p, str)], [101, 102, 103])
        self.assertNotIn('Never merge', ''.join(p for p in b.parts if isinstance(p, str)))  # no copies
        self.assertNotIn('b-1', b.text)  # the id is in the wake, not the instructions (render says why)
        self.assertIn("It's block 26,260,500.", b.text)  # a block handed in still fills its sentence
        record = b.record()
        self.assertEqual(record['sha256'], b.sha256)
        self.assertEqual(record['block'], 26_260_500)
        self.assertEqual(record['stamps']['layers']['standing']['revision'], 101)

    def test_the_same_pages_give_the_same_instructions_turn_after_turn(self):
        """The system prompt comes first in what a harness sends, and a prompt cache matches it byte for byte:
        one that changed every turn would make every wake re-read the whole session at full price."""
        from datetime import timedelta
        w = wiki(**{'Cryptograss:Agents/Greeting': [(200, 'JMyles', GREETING_PAGE)],
                    'Cryptograss:Agents/Magent': [(210, 'JMyles', agent_page('magenta', 'Welcome back.'))]})
        first = bundle('magent', 'magent-self', get=w.get, state_dir=None, now=T0, record_id='one')
        later = bundle('magent', 'magent-self', get=w.get, state_dir=None, now=T0 + timedelta(hours=5), record_id='two')
        self.assertEqual(first.text, later.text)
        self.assertNotEqual(first.record()['gathered'], later.record()['gathered'])  # the record still says when

    def test_a_degraded_bundle_rebuilds_from_the_copy_it_stood_on(self):
        state = tempfile.mkdtemp()
        w = wiki()
        self.gather(w, state=state)
        w.down = True
        b = self.gather(w, state=state)
        self.assertIn('<instructions-degraded', b.text)
        again = rebuilt(b.record(), texts=every_revision(w))
        self.assertTrue(again['matches'])
        self.assertEqual(again['text'], b.text)

    def test_a_mood_with_no_rules_and_no_greeting_page_still_rebuilds(self):
        w = wiki()
        b = self.gather(w, mood='quiet-room')
        self.assertTrue(rebuilt(b.record(), texts=every_revision(w))['matches'])

    def test_a_revision_gone_from_the_wiki_is_named_never_guessed(self):
        w = wiki()
        b = self.gather(w)
        texts = every_revision(w)
        del texts[102]
        self.assertEqual(rebuilt(b.record(), texts=texts), {'text': None, 'sha256': None, 'matches': False,
                                                            'missing': [102]})

    def test_a_page_changed_since_is_read_at_its_recorded_revision(self):
        w = wiki()
        b = self.gather(w)
        texts = every_revision(w)
        w.pages[identity_title('magent')].insert(0, (150, 'JMyles', 'You are someone else now.'))
        texts[150] = 'You are someone else now.'
        self.assertTrue(rebuilt(b.record(), texts=texts)['matches'])

    def test_a_tampered_text_does_not_match(self):
        w = wiki()
        b = self.gather(w)
        texts = every_revision(w)
        texts[101] = texts[101].replace('Never', 'Always')
        again = rebuilt(b.record(), texts=texts)
        self.assertFalse(again['matches'])
        self.assertIn('Always merge', again['text'])


class RecordingAPI(FakeAPI):
    def __init__(self):
        super().__init__([])
        self.recorded = []

    def block(self):
        return 26_260_600

    def bundle(self, record):
        self.recorded.append(record)
        return True

    def agents(self):
        return ['blu', 'magent', 're']


class SilentWaker(FakeWaker):
    """A harness that never started: it says nothing, then fails."""

    def wake(self, session_id, prompt, new_session_id=None, on_event=None, stop=None, **options):
        self.options = options
        raise RuntimeError('pi: command not found')


class PollerRecordsTest(SimpleTestCase):

    def poller(self, waker):
        state = Path(tempfile.mkdtemp()) / 'state.json'
        state.write_text(json.dumps({'since': '2026-10-21T00:00:00+00:00', 'handled': [], 'wakes': []}))
        api = RecordingAPI()
        return MoodPoller(api, waker, agent='magent', state_path=state, consider=False, instructions=True), api

    def test_each_turn_records_its_bundle_asked_for_made_and_delivered(self):
        w = wiki()
        poller, api = self.poller(FakeWaker())
        gather = lambda *a, **k: bundle(*a, get=w.get, state_dir=None, now=T0, **k)  # noqa: E731
        with mock.patch('poller.instructions.bundle', side_effect=gather):
            poller.run_turn_for('magent-self', 's-local', 'You were woken')
        [record] = api.recorded
        given = poller.waker.options['system']
        self.assertNotIn(record['id'], given)
        self.assertTrue(poller.waker.woken[0][1].endswith(
            f"It's block 26,260,600. Your instructions this turn are bundle {record['id']} (Special:AgentBundles)."))
        self.assertTrue(record['session_id'])  # the session the turn ran as
        self.assertEqual(sorted(record['stamps']['greeting']['roster']), ['blu', 're'])  # magenta's list, less magent
        self.assertTrue(record['requested_at'] <= record['produced_at'] <= record['delivered_at'])
        self.assertEqual(record['block'], 26_260_600)
        self.assertTrue(rebuilt(record, texts=every_revision(w))['matches'])

    def test_a_harness_that_never_started_has_no_delivery(self):
        poller, api = self.poller(SilentWaker())
        with mock.patch('poller.instructions.bundle', side_effect=RuntimeError('cache unreadable')):
            outcome = poller.run_turn_for('magent-self', 's-local', 'You were woken')
        self.assertEqual(outcome[0], 'failed')
        [record] = api.recorded
        self.assertIsNone(record['delivered_at'])
        self.assertIn('cache unreadable', record['error'])
        self.assertEqual(record['parts'], [poller.waker.options['system']])  # the recovery rules, whole


def a_record(agent='re', **extra):
    w = wiki(**{identity_title('re'): [(104, 'JMyles', 'You are re.')]})
    b = bundle(agent, 'magent-self', get=w.get, state_dir=None, now=T0, record_id=str(uuid.uuid4()))
    record = {**b.record(), 'requested_at': '2026-10-24T06:30:00+00:00', 'produced_at': '2026-10-24T06:30:01+00:00',
              'delivered_at': '2026-10-24T06:30:03+00:00', 'session_id': 'sess-1', 'harness': 'pi', **extra}
    return record, every_revision(w)


@override_settings(MOOD_RUNNER_KEYS=KEYS)
class BundleAPITest(TestCase):

    def post(self, record, key='re-key'):
        return self.client.post('/api/bundles/', json.dumps(record), content_type='application/json',
                                **({'HTTP_AUTHORIZATION': f'Bearer {key}'} if key else {}))

    def test_a_runner_records_and_anyone_reads_the_list(self):
        record, _ = a_record()
        self.assertEqual(self.post(record).status_code, 201)
        self.assertEqual(self.post(record).status_code, 200)  # sent twice: kept once
        listed = self.client.get('/api/bundles/', {'agent': 're'}).json()['bundles']
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]['id'], record['id'])
        self.assertEqual(listed[0]['delivered_at'], '2026-10-24T06:30:03+00:00')
        self.assertEqual([p['revision'] for p in listed[0]['pages']], [101, 104, 103])
        self.assertEqual(self.client.get('/api/bundles/', {'agent': 'blu'}).json()['bundles'], [])
        one = self.client.get(f'/api/bundles/{record["id"]}/').json()
        self.assertEqual(one['parts'], record['parts'])

    def test_only_its_own_runner_records_for_an_agent(self):
        record, _ = a_record()
        self.assertEqual(self.post(record, key=None).status_code, 401)
        self.assertEqual(self.post(record, key='magent-key').status_code, 400)
        self.assertEqual(self.post({**record, 'sha256': 'nope'}).status_code, 400)
        self.assertEqual(self.post({**record, 'parts': [{'page': 'X', 'revision': 'latest'}]}).status_code, 400)
        self.assertFalse(BundleRecord.objects.exists())

    def test_rebuilt_to_read_and_checked(self):
        record, texts = a_record()
        self.post(record)
        with mock.patch('poller.instructions.revision_texts', return_value=texts) as fetched:
            again = self.client.get(f'/api/bundles/{record["id"]}/text/').json()
            plain = self.client.get(f'/api/bundles/{record["id"]}/text/', {'format': 'text'})
        self.assertTrue(again['matches'])
        self.assertEqual(again['sha256'], record['sha256'])
        self.assertIn('You are re.', plain.content.decode())
        self.assertEqual(plain['X-Bundle-Matches'], 'yes')
        self.assertEqual(sorted(fetched.call_args[0][0]), [101, 103, 104])

    def test_a_revision_gone_says_so(self):
        record, texts = a_record()
        self.post(record)
        del texts[104]
        with mock.patch('poller.instructions.revision_texts', return_value=texts):
            plain = self.client.get(f'/api/bundles/{record["id"]}/text/', {'format': 'text'})
        self.assertEqual(plain.status_code, 409)
        self.assertIn('104', plain.content.decode())


class EveryMoodTest(SimpleTestCase):
    """What holds in every Mood moves from the end of each wake into the instructions, once a trusted
    revision of Cryptograss:Moods/Instructions says it; until then the wake keeps saying it."""

    def run_with(self, pages):
        from poller.mood_poller import ETIQUETTE, GREETING, HANDOFF_HOW, mention_opening, wake_footer
        w = wiki(**pages)
        state = Path(tempfile.mkdtemp()) / 'state.json'
        state.write_text(json.dumps({'since': '2026-10-21T00:00:00+00:00', 'handled': [], 'wakes': []}))
        api = RecordingAPI()
        poller = MoodPoller(api, FakeWaker(), agent='magent', state_path=state, consider=False, instructions=True)
        gather = lambda *a, **k: bundle(*a, get=w.get, state_dir=None, now=T0, **k)  # noqa: E731
        prompt = '\n'.join(mention_opening('magent-self', 'a post.') + ['[justin] hi'] + wake_footer(full=True))
        with mock.patch('poller.instructions.bundle', side_effect=gather):
            poller.run_turn_for('magent-self', 's-local', prompt)
        return poller.waker.woken[0][1], poller.waker.options['system'], (ETIQUETTE, GREETING, HANDOFF_HOW)

    def test_until_the_page_is_trusted_the_wake_says_it(self):
        wake, system, said = self.run_with({'Cryptograss:Moods/Instructions': [(400, 'Magent', 'Every Mood.')]})
        for line in said:
            self.assertIn(line, wake)
        self.assertNotIn('<moods', system)

    def test_once_it_is_the_instructions_say_it_and_the_wake_does_not(self):
        wake, system, said = self.run_with({'Cryptograss:Moods/Instructions': [(401, 'JMyles', 'Every Mood, kept.')]})
        self.assertIn('<moods page="Cryptograss:Moods/Instructions" revision="401" by="JMyles">\nEvery Mood, kept.\n</moods>',
                      system)
        for line in said:
            self.assertNotIn(line, wake)
        self.assertNotIn('Cryptograss:Moods/magent-self/todo', wake)
        self.assertIn('This turn has your full tools', wake)  # what changes turn to turn stays
        self.assertIn('[justin] hi', wake)
        self.assertTrue(wake.endswith('(Special:AgentBundles).\n</mood-wake>'))  # inside the wake, last
        self.assertNotIn('\n\n\n', wake)
