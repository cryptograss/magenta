"""Voice memos whose microphone drops or goes to digital zero partway through
(Justin, #connectome -> #interfaces, 16 Nov 2026):
- screen wake lock held while recording or in voice mode so 15-30 s auto-lock
  doesn't zero out mobile microphone capture mid-sentence;
- background silent audio and 3 s wait ticks silenced while recording;
- micWatch clears stale mute latches when the track is unmuted or delivering sound;
- immediate audible drop chime and a warning bar inside #voiceMode (not just #composer),
  with 1-tap record-again and discard."""

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import skipUnless

from django.core.cache import cache
from django.test import TestCase, override_settings

from conversations.models import Mood, ThinkingEntity
from conversations.services import mood_auth

HAS_NODE = shutil.which('node') is not None


@override_settings(ELEVENLABS_API_KEY='test-eleven-key')
class MemoCutoffTest(TestCase):

    def setUp(self):
        cache.clear()
        self.justin = ThinkingEntity.objects.create(name='justin', is_biological_human=True)
        Mood.objects.create(slug='interfaces', title='interfaces')
        _, token = mood_auth.enrol_device(self.justin, 'phone')
        self.client.cookies[mood_auth.COOKIE] = token

    def rendered(self):
        resp = self.client.get('/moods/interfaces/')
        self.assertEqual(resp.status_code, 200)
        return resp.content.decode()

    def test_wake_lock_drop_chime_and_voice_mode_quiet_warning_are_wired(self):
        html = self.rendered()
        # Screen wake lock prevents idle phone lock from zeroing the mic stream.
        self.assertIn("navigator.wakeLock.request('screen')", html)
        self.assertIn('syncScreenWake()', html)
        # Background loop and wait ticks stay silent while recording a memo.
        self.assertIn('if (on) { keepAwake(false); reader.pause();', html)
        self.assertIn('if (readNow === now && !readHeld && !readRecording)', html)
        # Audible alert chime and #voiceMode warning bar with record-again & discard.
        self.assertIn('drop: [[587.3, .13]', html)
        self.assertIn('alertDrop()', html)
        self.assertIn("pendingHere.state === 'quiet'", html)
        self.assertIn("pendingHere.state === 'ready'", html)
        self.assertIn('data-v="again"', html)
        self.assertIn('data-v="drop-memo"', html)

    @skipUnless(HAS_NODE, 'needs node')
    def test_rendered_scripts_parse_and_mic_watch_handles_mute_glitches_and_cutoffs(self):
        html = self.rendered()
        scripts = re.findall(r'<script(?:\s[^>]*)?>(.*?)</script>', html, flags=re.S)
        self.assertGreaterEqual(len(scripts), 2)
        with tempfile.TemporaryDirectory() as tmp:
            for idx, body in enumerate(scripts):
                if not body.strip():
                    continue
                path = Path(tmp) / f's{idx}.js'
                path.write_text(body)
                proc = subprocess.run(['node', '--check', str(path)], capture_output=True, text=True)
                self.assertEqual(proc.returncode, 0, proc.stderr)

        m = re.search(r'(const QUIET_STOP_S = 10.*?function micWatch\(stream\)\s*\{.*?\n  \})', html, flags=re.S)
        self.assertIsNotNone(m)
        driver = m.group(1) + r"""
let fakeNow = 100000;
Date.now = () => fakeNow;
let samplePeak = 0.25;
globalThis.window = {
  AudioContext: class {
    constructor() { this.state = 'running'; }
    resume() { this.state = 'running'; return Promise.resolve(); }
    createAnalyser() {
      return {
        fftSize: 2048,
        getFloatTimeDomainData(buf) { buf.fill(0); buf[0] = samplePeak; },
      };
    }
    createMediaStreamSource() { return { connect() {} }; }
    close() {}
  },
};
function makeStream() {
  const listeners = {};
  const track = {
    muted: false,
    readyState: 'live',
    addEventListener(ev, fn) { (listeners[ev] ||= []).push(fn); },
    fire(ev) { (listeners[ev] || []).forEach(fn => fn()); },
  };
  return { stream: { getAudioTracks: () => [track] }, track };
}

// 1. Missed unmute event while real audio is still flowing must NOT stop at 10 s.
const a = makeStream();
const wa = micWatch(a.stream);
a.track.muted = true; a.track.fire('mute');
fakeNow += 100;
a.track.muted = false; // unmute event never fired, or audio resumed
samplePeak = 0.2;
for (let i = 0; i < 25; i++) { fakeNow += 500; }
const missedUnmute = wa.quietSince();

// 2. Digital zero (0.0) for >= 10 s DOES return the timestamp when sound stopped.
const b = makeStream();
samplePeak = 0.3;
const wb = micWatch(b.stream);
fakeNow += 500; wb.quietSince();
const cutAt = fakeNow;
samplePeak = 0.0;
let stoppedAt = null;
for (let i = 0; i < 22; i++) {
  fakeNow += 500;
  stoppedAt = wb.quietSince();
  if (stoppedAt !== null) break;
}

// 3. Track ending stops immediately without waiting 10 s.
const c = makeStream();
samplePeak = 0.3;
const wc = micWatch(c.stream);
fakeNow += 500; wc.quietSince();
a_end = fakeNow;
c.track.readyState = 'ended'; c.track.fire('ended');
fakeNow += 500;
const endedAt = wc.quietSince();

console.log(JSON.stringify({ missedUnmute, cutAt, stoppedAt, endedAt }));
"""
        proc = subprocess.run(['node', '-e', driver], capture_output=True, text=True, check=True)
        out = json.loads(proc.stdout.strip())
        self.assertIsNone(out['missedUnmute'])
        self.assertEqual(out['stoppedAt'], out['cutAt'])
        self.assertIsNotNone(out['endedAt'])
