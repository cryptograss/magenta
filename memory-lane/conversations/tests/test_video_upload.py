"""A video sent from a Mood goes up in pieces, and its panel follows it to its Release page.

Justin, 19 Nov 2026 (#uploads-and-embeds), from a phone: the upload was hard to see, and a ~940 MB
video failed most of the time with "The connection dropped before it was all sent". It went up as one
request, so anything that broke the connection lost all of it.

- videoUploader sends it in pieces to delivery-kid's /draft-content/{id}/upload, and after a drop asks
  how much arrived and carries on from there. Run here in node against a stand-in delivery-kid that
  drops connections, restarts (502), goes quiet, and loses answers.
- #videoPanel replaces the one-line bar and the cards: one block per video, five steps, from sending
  to its Release page.
"""

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

# A stand-in delivery-kid (its routes/upload.py: bytes appended from an offset, 409 with where the file
# ends otherwise) and an XMLHttpRequest that reaches it, with the mishaps a scenario asks for.
HARNESS = r'''
const MB = 1024 * 1024;
function film(size) { const b = Buffer.alloc(size); for (let i = 0; i < size; i++) b[i] = (i * 31 + (i >> 8)) & 255; return b; }
function fileOf(buf, name = 'station-inn.mov') { return { name, size: buf.length, slice: (a, b) => buf.subarray(a, b) }; }

function world(plan = {}) {
  const server = { bytes: Buffer.alloc(0), status: 'awaiting_upload', requests: [], completes: 0 };
  const mishaps = [...(plan.mishaps || [])];   // taken one per request to the server, in order
  let token = 'good';
  class XHR {
    constructor() { this.listeners = {}; this.upload = { addEventListener: (t, f) => { this.up = f; } }; this.headers = {}; }
    addEventListener(t, f) { this.listeners[t] = f; }
    setRequestHeader(k, v) { this.headers[k] = v; }
    open(method, url) { this.method = method; this.url = new URL(url); }
    abort() { if (!this.over) { this.over = true; setImmediate(() => this.listeners.abort && this.listeners.abort()); } }
    answer(status, body) { if (this.over) return; this.over = true; this.status = status; this.responseText = JSON.stringify(body); setImmediate(() => this.listeners.load()); }
    fail() { if (this.over) return; this.over = true; setImmediate(() => this.listeners.error()); }
    send(body) {
      const path = this.url.pathname, q = this.url.searchParams;
      const mishap = mishaps.shift() || {};
      server.requests.push(`${this.method} ${path.replace(/^.*\/upload/, '/upload')}${q.get('offset') != null ? ' @' + q.get('offset') : ''}${mishap.kind ? ' !' + mishap.kind : ''}`);
      if (mishap.kind === 'quiet') return;                                   // never answers: only the stall watch ends it
      if (mishap.kind === 'restarting') return this.answer(502, {});
      if (mishap.kind === 'unreachable') return this.fail();
      if (this.headers['X-Upload-Token'] !== token || mishap.kind === 'expired') {
        if (mishap.kind === 'expired') token = 'renewed';
        return this.answer(401, { detail: { error: 'Invalid or expired token' } });
      }
      if (mishap.kind === 'gone') return this.answer(404, { detail: 'Content draft not found' });
      if (this.method === 'GET') return this.answer(200, { resumable: true, received: server.bytes.length, status: server.status });
      if (path.endsWith('/complete')) {
        if (server.status !== 'uploaded' && server.bytes.length !== plan.size) return this.answer(409, { detail: { error: 'Not all of it is here', received: server.bytes.length } });
        server.status = 'uploaded'; server.completes++;
        if (mishap.kind === 'answer-lost') return this.fail();
        return this.answer(200, { draft_id: 'the-draft', status: 'uploaded' });
      }
      if (server.status === 'uploaded') return this.answer(409, { detail: { error: 'This draft is uploaded; it takes no more bytes', status: 'uploaded' } });
      const offset = Number(q.get('offset'));
      if (offset !== server.bytes.length) return this.answer(409, { detail: { error: 'Not where the file ends', received: server.bytes.length } });
      const take = mishap.kind === 'drop' ? Math.min(body.length, mishap.after) : body.length;   // a drop: only what arrived first is kept
      server.bytes = Buffer.concat([server.bytes, body.subarray(0, take)]);
      if (this.up) this.up({ loaded: take });
      if (mishap.kind === 'drop' || mishap.kind === 'answer-lost') return this.fail();
      this.answer(200, { received: server.bytes.length, size: plan.size });
    }
  }
  return { server, XHR, token: () => token };
}

async function attempt(size, plan = {}, opts = {}) {
  const buf = film(size), w = world({ ...plan, size });
  const said = [], waits = [];
  let renews = 0, headerToken = 'good';
  const up = videoUploader({
    file: fileOf(buf), url: 'https://dk.example/draft-content', draft: 'the-draft', XHR: w.XHR, piece: 8 * MB, stall: 30,
    headers: () => ({ 'X-Upload-Token': headerToken }),
    renew: async () => { renews++; headerToken = w.token(); },
    wait: async ms => { waits.push(ms); if (opts.onWait) opts.onWait(up, waits.length); },
    says: (state, bytes) => { if (said[said.length - 1] !== state) said.push(state); },
    ...(opts.without || {}),
  });
  if (opts.preload) w.server.bytes = Buffer.from(buf.subarray(0, opts.preload));
  let answer = null, error = null;
  try { answer = await up.run(opts.from || 0); } catch (e) { error = { message: e.message, fatal: !!e.fatal }; }
  return { answer, error, same: Buffer.compare(w.server.bytes, buf) === 0, arrived: w.server.bytes.length, requests: w.server.requests,
           completes: w.server.completes, said, waits, renews };
}

(async () => {
  const out = {};
  out.clean = await attempt(20 * MB);
  out.rough = await attempt(30 * MB, { mishaps: [
    { kind: 'drop', after: 3 * MB },          // 3 MB into the first piece, the connection goes
    { kind: 'unreachable' },                  // asking where it got to: still away
    {},                                       // asked again: 3 MB
    { kind: 'restarting' },                   // delivery-kid is restarting: its proxy says 502
    { kind: 'restarting' },
    {}, {},                                   // back: asks where, sends on
    { kind: 'answer-lost' },                  // a whole piece arrived, but its answer didn't
  ] });
  out.quiet = await attempt(10 * MB, { mishaps: [{ kind: 'quiet' }] });
  out.expired = await attempt(10 * MB, { mishaps: [{}, { kind: 'expired' }] });
  out.locked_out = await attempt(10 * MB, { mishaps: [{ kind: 'expired' }, { kind: 'expired' }] });
  out.gone = await attempt(10 * MB, { mishaps: [{}, { kind: 'gone' }] });
  out.complete_lost = await attempt(10 * MB, { mishaps: [{}, {}, { kind: 'answer-lost' }] });
  out.carried_on = await attempt(30 * MB, {}, { from: 12 * MB, preload: 12 * MB });
  out.stale_start = await attempt(30 * MB, {}, { from: 0, preload: 12 * MB });   // this browser thought 0; delivery-kid has 12 MB
  out.cancelled = await attempt(30 * MB, { mishaps: [{ kind: 'drop', after: MB }] }, { onWait: up => up.cancel() });
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e); process.exit(1); });
'''


@override_settings(DELIVERY_KID_UPLOAD_KEY='upload-key', DELIVERY_KID_URL='https://delivery-kid.example',
                   PICKIPEDIA_UPLOAD_CLIENT_ID='client', PICKIPEDIA_UPLOAD_CLIENT_SECRET='secret')
class VideoUploadTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.justin = ThinkingEntity.objects.create(name='justin', is_biological_human=True)
        Mood.objects.create(slug='uploads-and-embeds', title='uploads and embeds')

    def setUp(self):
        cache.clear()
        _, token = mood_auth.enrol_device(self.justin, 'phone')
        self.client.cookies[mood_auth.COOKIE] = token

    def rendered(self):
        resp = self.client.get('/moods/uploads-and-embeds/')
        self.assertEqual(resp.status_code, 200)
        return resp.content.decode()

    def test_the_panel_replaces_the_bar_and_the_cards(self):
        html = self.rendered()
        self.assertIn('<div id="videoPanel" hidden aria-live="polite"></div>', html)
        for gone in ('id="videoBar"', 'id="videoCards"', 'renderVideoCards', 'videoCancel'):
            self.assertNotIn(gone, html)
        # Five steps, to the Release page.
        self.assertIn("const VIDEO_STEPS = ['Send', 'Draft page', 'Finalize', 'Encode', 'Release'];", html)
        # On a phone, the block in front takes the bottom fifth of the screen.
        self.assertRegex(html, r'@media \(max-width: 720px\), \(pointer: coarse\) \{\s*#videoPanel \.vp-item\.first \{ min-height: 20vh;')
        # Its 🎬 line moves the panel on a step, where it used to drop the card; Send still makes the page.
        self.assertIn('videoWaiting.paged(e.draft, e.page, e.title)', html)
        self.assertIn("document.querySelector('#videoPanel .make:not(:disabled)')", html)

    def test_the_screen_stays_on_while_a_video_goes_up(self):
        html = self.rendered()
        self.assertIn("document.body.classList.contains('voice-mode') || videoSending)", html)
        self.assertIn('syncScreenWake();  // the screen stays on: a locked phone stops sending', html)

    def test_an_older_delivery_kid_still_gets_it_as_one_request(self):
        html = self.rendered()
        self.assertIn('pieces = !!(r && r.status === 200 && r.body.resumable);', html)
        self.assertIn('answer = await sendVideoWhole(file, ticket, draft, sending);', html)

    @skipUnless(HAS_NODE, 'needs node')
    def test_rendered_scripts_parse(self):
        scripts = re.findall(r'<script(?:\s[^>]*)?>(.*?)</script>', self.rendered(), flags=re.S)
        with tempfile.TemporaryDirectory() as tmp:
            for idx, body in enumerate(scripts):
                if not body.strip() or body.lstrip().startswith('{'):
                    continue
                path = Path(tmp) / f's{idx}.js'
                path.write_text(body)
                proc = subprocess.run(['node', '--check', str(path)], capture_output=True, text=True)
                self.assertEqual(proc.returncode, 0, proc.stderr)

    @skipUnless(HAS_NODE, 'needs node')
    def test_the_uploader_carries_on_through_whatever_the_connection_does(self):
        html = self.rendered()
        start, end = html.index('  function dkError(xhr) {'), html.index('  // </video-uploader>')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'uploader.js'
            path.write_text(html[start:end] + HARNESS)
            proc = subprocess.run(['node', str(path)], capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        MB = 1024 * 1024

        clean = out['clean']
        self.assertEqual(clean['answer'], {'draft_id': 'the-draft', 'status': 'uploaded'})
        self.assertTrue(clean['same'])
        self.assertEqual(clean['requests'], ['POST /upload @0', f'POST /upload @{8 * MB}', f'POST /upload @{16 * MB}',
                                             'POST /upload/complete'])
        self.assertEqual(clean['waits'], [])
        self.assertEqual(clean['said'], ['sending', 'checking'])

        # Drops, an unreachable spell, delivery-kid restarting, a lost answer: every byte arrives once, in order.
        rough = out['rough']
        self.assertIsNone(rough['error'])
        self.assertTrue(rough['same'], rough['requests'])
        self.assertEqual(rough['completes'], 1)
        self.assertIn(f'POST /upload @{3 * MB}', rough['requests'])        # carried on from the 3 MB that arrived
        self.assertIn('waiting', rough['said'])
        # Backing off while it stays away; from the top again once a piece has got through.
        self.assertEqual(rough['waits'], [1000, 2000, 4000, 8000, 1000])
        # After a failure nothing is sent blind: it asks where the file ends first.
        for i, req in enumerate(rough['requests'][:-1]):
            if '!' in req:
                self.assertTrue(rough['requests'][i + 1].startswith('GET /upload'), rough['requests'])

        # A connection that goes quiet without closing is given up on, and the piece sent again.
        self.assertTrue(out['quiet']['same'])
        self.assertEqual(out['quiet']['requests'][0], 'POST /upload @0 !quiet')

        # A token that runs out mid-upload: one fresh one. Refused again, it stops and says so.
        self.assertTrue(out['expired']['same'])
        self.assertEqual(out['expired']['renews'], 1)
        self.assertEqual(out['locked_out']['error'], {'message': 'delivery-kid said 401: Invalid or expired token', 'fatal': True})
        self.assertEqual(out['locked_out']['renews'], 1)

        # delivery-kid no longer has the draft: said, not retried for ever.
        self.assertEqual(out['gone']['error'], {'message': 'delivery-kid said 404: Content draft not found', 'fatal': True})
        self.assertEqual(out['gone']['waits'], [])

        # All of it arrived and was analysed, but that answer was lost: asked again, not sent again.
        self.assertEqual(out['complete_lost']['answer'], {'draft_id': 'the-draft', 'status': 'uploaded'})
        self.assertEqual(out['complete_lost']['requests'].count('POST /upload @0'), 1)

        # Carrying on an upload a reload cut short: from where delivery-kid has it, not from the top.
        self.assertTrue(out['carried_on']['same'])
        self.assertEqual(out['carried_on']['requests'][0], f'POST /upload @{12 * MB}')
        self.assertTrue(out['stale_start']['same'])
        self.assertEqual(out['stale_start']['requests'][:2], ['POST /upload @0', f'POST /upload @{12 * MB}'])

        # Cancelled while waiting: it stops there.
        self.assertEqual(out['cancelled']['error'], {'message': 'cancelled', 'fatal': True})
        self.assertEqual(out['cancelled']['arrived'], MB)
        self.assertIsNone(out['cancelled']['answer'])
