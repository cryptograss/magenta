"""Voice tag aliases on PickiPedia User pages and Mood-grouped read-aloud drain
(Justin, #interfaces, 16-17 Nov 2026)."""

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import mock, skipUnless

from django.core.cache import cache
from django.test import TestCase, override_settings

from conversations.models import Mood, ThinkingEntity
from conversations.services import mood_auth, voice, voice_aliases, wiki_auth, wiki_hooks

HAS_NODE = shutil.which('node') is not None


class VoiceAliasesTest(TestCase):

    def setUp(self):
        cache.clear()
        for name, human in (('justin', True), ('skyler', True), ('rj', True), ('slvrbckt', True),
                            ('magent', False), ('re', False)):
            ThinkingEntity.objects.get_or_create(name=name, defaults={'is_biological_human': human})
        Mood.objects.create(slug='interfaces', title='interfaces')

    def test_parses_templates_and_ignores_comments_nowiki_and_unverified_proposals(self):
        wikitext = """
{{Magenta person|justin|Justin|voice=J Walk, Myles}}
{{Voice aliases|Nick|Silver Bucket, @Nick Jennings}}
<!-- {{Voice aliases|Shadow}} -->
<nowiki>{{Voice aliases|Example}}</nowiki>
<proposed>{{Voice aliases|Unverified}}</proposed>
{{Bot_proposes|{{Voice aliases|BotNick}}|by=Magent}}
{{Voice aliases|here|everyone|magenta}}
"""
        self.assertEqual(voice_aliases.aliases_in(wikitext),
                         ['Nick', 'Silver Bucket', 'Nick Jennings', 'J Walk', 'Myles'])

    def test_spoken_mentions_tags_aliases_and_dotted_initials_without_touching_prose(self):
        pages = {
            'Slvrbckt': '{{Voice aliases|Nick|Silver Bucket|Shared}}',
            'SkymanJenkins': '{{Voice aliases|Sky|Shared}}',
            'JMyles': '{{Magenta person|justin|Justin|voice=J Walk}}',
        }
        inv = {'justin': 'JMyles', 'skyler': 'SkymanJenkins', 'rj': 'RJPARTINGTONIII', 'slvrbckt': 'Slvrbckt'}
        with mock.patch.object(wiki_auth, 'names', return_value=inv):
            data = voice_aliases.state(fetch=lambda _users: pages)
        # 'shared' was claimed by two people, so neither gets it.
        self.assertNotIn('shared', data['aliases'])
        self.assertEqual(data['aliases']['nick'], 'slvrbckt')
        self.assertEqual(data['aliases']['silver bucket'], 'slvrbckt')
        self.assertEqual(data['aliases']['sky'], 'skyler')
        self.assertEqual(data['aliases']['j walk'], 'justin')

        mentionable = {'justin', 'skyler', 'rj', 'slvrbckt', 'magent', 're'}
        inv_aliases = {v.lower(): k for k, v in inv.items()}
        self.assertEqual(voice.spoken_mentions('Nick, can you check the iPhone build?', mentionable, inv_aliases),
                         '@slvrbckt, can you check the iPhone build?')
        self.assertEqual(voice.spoken_mentions('I asked at Silver Bucket and at Sky.', mentionable, inv_aliases),
                         'I asked @slvrbckt and @skyler.')
        self.assertEqual(voice.spoken_mentions('Hey J Walk what do you think', mentionable, inv_aliases),
                         'Hey @justin what do you think')
        # R.J. works out of the box even with no User:RJPARTINGTONIII page yet!
        self.assertEqual(voice.spoken_mentions('R.J., check the MediaWiki hook.', mentionable, inv_aliases),
                         '@rj, check the MediaWiki hook.')
        self.assertEqual(voice.spoken_mentions('Ask at R.J. about the room.', mentionable, inv_aliases),
                         'Ask @rj about the room.')
        # Ambiguous or plain prose stays untouched.
        self.assertEqual(voice.spoken_mentions('at Shared hello', mentionable, inv_aliases), 'at Shared hello')
        self.assertEqual(voice.spoken_mentions('The silver bucket was full.', mentionable, inv_aliases),
                         'The silver bucket was full.')
        self.assertEqual(voice.spoken_mentions('R.J. built extensions in college.', mentionable, inv_aliases),
                         'R.J. built extensions in college.')

        terms = voice.keyterms()
        self.assertIn('Silver Bucket', terms)
        self.assertIn('Nick', terms)
        self.assertIn('J Walk', terms)

    def test_saving_a_user_page_forgets_the_voice_aliases_cache(self):
        cache.set(voice_aliases.KEY, {'aliases': {'nick': 'slvrbckt'}, 'keyterms': ['Nick']}, 3600)
        with mock.patch('conversations.services.wiki_preview.preview', return_value=None):
            wiki_hooks.run('User:Slvrbckt', user='Slvrbckt', revid=99, via='push')
        self.assertIsNone(cache.get(voice_aliases.KEY))


@override_settings(ELEVENLABS_API_KEY='test-eleven-key')
class MoodGroupedDrainTest(TestCase):

    def setUp(self):
        cache.clear()
        self.justin = ThinkingEntity.objects.create(name='justin', is_biological_human=True)
        Mood.objects.create(slug='interfaces', title='interfaces')
        _, token = mood_auth.enrol_device(self.justin, 'phone')
        self.client.cookies[mood_auth.COOKIE] = token

    @skipUnless(HAS_NODE, 'needs node')
    def test_drains_current_mood_completely_before_advancing_chronologically_to_next_mood(self):
        resp = self.client.get('/moods/interfaces/')
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()

        scripts = re.findall(r'<script(?:\s[^>]*)?>(.*?)</script>', html, flags=re.S)
        with tempfile.TemporaryDirectory() as tmp:
            for idx, body in enumerate(scripts):
                if not body.strip():
                    continue
                path = Path(tmp) / f's{idx}.js'
                path.write_text(body)
                proc = subprocess.run(['node', '--check', str(path)], capture_output=True, text=True)
                self.assertEqual(proc.returncode, 0, proc.stderr)

        m = re.search(r'(function orderReadQueue\(\)\s*\{.*?\n  \})', html, flags=re.S)
        self.assertIsNotNone(m)
        driver = """
let readQueue = [], readSeq = 0, readMode = 'everywhere', readBlocked = false;
let readNow = null, readLast = null, current = 'interfaces';
""" + m.group(1) + r"""
// 1. Currently hearing C1 in #connectome while messages interleave across 3 Moods:
readNow = { item: { id: 'C1', mood: 'connectome', kind: 'said', at: '2026-11-17T06:00:01Z' } };
readQueue = [
  { id: 'T1', mood: 'rabbithole', kind: 'thinking', agent: 'magent' },
  { id: 'R1', mood: 'rabbithole', kind: 'said', at: '2026-11-17T06:00:03Z' },
  { id: 'I1', mood: 'interfaces', kind: 'said', at: '2026-11-17T06:00:05Z' },
  { id: 'C2', mood: 'connectome', kind: 'answered', at: '2026-11-17T06:00:07Z' },
  { id: 'R2', mood: 'rabbithole', kind: 'answered', at: '2026-11-17T06:00:09Z' },
  { id: 'C3', mood: 'connectome', kind: 'said', at: '2026-11-17T06:00:11Z' },
  { id: 'I2', mood: 'interfaces', kind: 'answered', at: '2026-11-17T06:00:13Z' },
];
orderReadQueue();
const firstPass = readQueue.map(x => x.id);

// 2. Drain C2 and C3, then R1 starts playing; while R1 plays, C4 and R3 arrive:
readQueue.shift(); // C2
readQueue.shift(); // C3
const nextUp = readQueue.shift(); // R1
readNow = { item: nextUp };
readLast = { who: 'blu', mood: 'connectome' };
readQueue.push(
  { id: 'C4', mood: 'connectome', kind: 'said', at: '2026-11-17T06:00:20Z' },
  { id: 'R3', mood: 'rabbithole', kind: 'said', at: '2026-11-17T06:00:21Z' },
);
orderReadQueue();
const secondPass = [nextUp.id, ...readQueue.map(x => x.id)];

console.log(JSON.stringify({ firstPass, secondPass }));
"""
        proc = subprocess.run(['node', '-e', driver], capture_output=True, text=True, check=True)
        out = json.loads(proc.stdout.strip())
        self.assertEqual(out['firstPass'], ['C2', 'C3', 'R1', 'R2', 'I1', 'I2', 'T1'])
        self.assertEqual(out['secondPass'], ['R1', 'R2', 'R3', 'I1', 'I2', 'C4', 'T1'])
