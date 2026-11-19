"""Voice memo transcripts show their first ~200 characters in view and fold only
longer transcripts under 'Full transcript · N words' (Justin, #interfaces, 19 Nov 2026)."""

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import skipUnless

from django.core.cache import cache
from django.test import TestCase

from conversations.models import Mood, ThinkingEntity
from conversations.services import mood_auth

HAS_NODE = shutil.which('node') is not None


class MemoTranscriptPreviewTest(TestCase):

    def setUp(self):
        cache.clear()
        self.justin, _ = ThinkingEntity.objects.get_or_create(name='justin', defaults={'is_biological_human': True})
        Mood.objects.create(slug='interfaces', title='interfaces')
        _, token = mood_auth.enrol_device(self.justin, 'phone')
        self.client.cookies[mood_auth.COOKIE] = token

    @skipUnless(HAS_NODE, 'needs node')
    def test_short_memos_stay_open_and_long_memos_preview_first_200_chars(self):
        resp = self.client.get('/moods/interfaces/')
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn('.turn .body.memoed:has(> details.transcript[open]) > .memo-preview { display: none; }', html)

        scripts = re.findall(r'<script(?:\s[^>]*)?>(.*?)</script>', html, flags=re.S)
        with tempfile.TemporaryDirectory() as tmp:
            for idx, body in enumerate(scripts):
                if not body.strip():
                    continue
                path = Path(tmp) / f's{idx}.js'
                path.write_text(body)
                proc = subprocess.run(['node', '--check', str(path)], capture_output=True, text=True)
                self.assertEqual(proc.returncode, 0, proc.stderr)

        m = re.search(r'(const MEMO_PREVIEW_CHARS = 200;.*?function foldMemo\(el\)\s*\{.*?\n  \})', html, flags=re.S)
        self.assertIsNotNone(m)

        # Exercise foldMemo against a lightweight DOM in Node across all 5 cases.
        driver = r"""
class N {
  constructor(type, tag = '', val = '') {
    this.nodeType = type;
    this.tagName = tag;
    this.nodeValue = val;
    this.childNodes = [];
    this.parentNode = null;
    this._classes = new Set();
    this.classList = {
      add: c => this._classes.add(c),
      contains: c => this._classes.has(c),
    };
  }
  get className() { return [...this._classes].join(' '); }
  set className(v) { this._classes = new Set(String(v).split(/\s+/).filter(Boolean)); }
  get lastChild() { return this.childNodes[this.childNodes.length - 1] || null; }
  get parentElement() { return this.parentNode; }
  get nextElementSibling() {
    if (!this.parentNode) return null;
    const kids = this.parentNode.childNodes;
    for (let i = kids.indexOf(this) + 1; i < kids.length; i++) {
      if (kids[i].nodeType === 1) return kids[i];
    }
    return null;
  }
  get textContent() {
    return this.nodeType === 3 ? (this.nodeValue || '') : this.childNodes.map(c => c.textContent).join('');
  }
  appendChild(c) {
    if (c.parentNode) {
      const idx = c.parentNode.childNodes.indexOf(c);
      if (idx >= 0) c.parentNode.childNodes.splice(idx, 1);
    }
    c.parentNode = this;
    this.childNodes.push(c);
    return c;
  }
  cloneNode(deep) {
    const copy = new N(this.nodeType, this.tagName, this.nodeValue);
    copy._classes = new Set(this._classes);
    if (deep) this.childNodes.forEach(c => copy.appendChild(c.cloneNode(true)));
    return copy;
  }
  set innerHTML(s) {
    this.childNodes = [];
    const t = new N(1, 'SUMMARY');
    t.appendChild(new N(3, '', s.replace(/<[^>]+>/g, '')));
    this.appendChild(t);
  }
  querySelector(sel) {
    if (sel === ':scope > .body') return this.childNodes.find(c => c.classList.contains('body')) || null;
    if (sel === ':scope > p > audio') {
      for (const p of this.childNodes) {
        if (p.tagName === 'P') {
          const a = p.childNodes.find(x => x.tagName === 'AUDIO');
          if (a) return a;
        }
      }
      return null;
    }
    if (sel === ':scope > details.transcript') {
      return this.childNodes.find(c => c.tagName === 'DETAILS' && c.classList.contains('transcript')) || null;
    }
    if (sel === 'img, video, audio, .embed, .yarnbox') {
      const check = node => {
        for (const c of node.childNodes) {
          if (c.nodeType === 1 && (['IMG', 'VIDEO', 'AUDIO'].includes(c.tagName)
              || c.classList.contains('embed') || c.classList.contains('yarnbox') || check(c))) return c;
        }
        return null;
      };
      return check(this);
    }
    return null;
  }
}
const Node = { ELEMENT_NODE: 1, TEXT_NODE: 3 };
const document = {
  createElement: tag => new N(1, tag.toUpperCase()),
  createTextNode: val => new N(3, '', val),
};
function el(tag, cls, ...kids) {
  const n = new N(1, tag.toUpperCase());
  if (cls) n.className = cls;
  for (const k of kids) n.appendChild(typeof k === 'string' ? new N(3, '', k) : k);
  return n;
}
function makeTurn(...afterAudio) {
  return el('article', 'turn', el('div', 'body', el('p', '', el('audio', '')), ...afterAudio));
}
""" + m.group(1) + r"""

// 1. Short memo (<= 220 chars): stays unfolded.
const shortTurn = makeTurn(el('p', '', el('span', 'mention', '@magent'), ' quick check on short memos.'));
foldMemo(shortTurn);
const shortBody = shortTurn.childNodes[0];

// 2. Long memo (> 220 chars) with @mention + wikilink + 2 paragraphs + trailing photo:
const longTurn = makeTurn(
  el('p', '',
    el('span', 'mention', '@magent'),
    ' For voice memos, instead of having the transcript completely collapse, let us still show the first two hundred characters or so right underneath the player so anybody skimming ',
    el('a', 'wikilink', 'Magenta:Voice'),
    ' or #interfaces immediately sees what the memo is about.'
  ),
  el('p', '', 'Second paragraph with extra details from the tour van about how the chartifact timeline behaves.'),
  el('p', '', el('a', 'img', el('img', '')))
);
foldMemo(longTurn);
const longBody = longTurn.childNodes[0];
const preview = longBody.childNodes.find(c => c.classList.contains('memo-preview'));
const details = longBody.childNodes.find(c => c.tagName === 'DETAILS');
const tailTag = longBody.lastChild.childNodes[0]?.className || '';

// 3. Multi-paragraph boundary at ~197 chars: does not emit a 2-letter stub paragraph.
const boundaryTurn = makeTurn(
  el('p', '', 'A'.repeat(197)),
  el('p', '', 'Second paragraph that starts right after 197 characters and keeps going past 230 characters easily.')
);
foldMemo(boundaryTurn);
const boundaryPreview = boundaryTurn.childNodes[0].childNodes.find(c => c.classList.contains('memo-preview'));

// 4. Inline link ending right at the cut: ellipsis sits outside <a>, not inside link text.
const linkEndTurn = makeTurn(
  el('p', '', 'x'.repeat(180) + ' ', el('a', 'wikilink', 'PickiPedia'), ' and more words after the link so total exceeds 220 chars easily indeed.')
);
foldMemo(linkEndTurn);
const linkPreviewP = linkEndTurn.childNodes[0].childNodes.find(c => c.classList.contains('memo-preview')).childNodes[0];
const linkNodeInPreview = linkPreviewP.childNodes.find(c => c.tagName === 'A');

console.log(JSON.stringify({
  shortMemoed: shortBody.classList.contains('memoed'),
  shortHasDetails: shortBody.childNodes.some(c => c.tagName === 'DETAILS'),
  longPreviewText: preview.textContent,
  longPreviewLen: preview.textContent.length,
  longHasMention: preview.childNodes[0].childNodes.some(c => c.classList.contains('mention')),
  longSummary: details.childNodes[0].textContent,
  longTailStayedOutsideDetails: tailTag === 'img',
  boundaryParaCount: boundaryPreview.childNodes.length,
  boundaryTextEndsClean: boundaryPreview.textContent.endsWith('AAA…'),
  linkTextClean: linkNodeInPreview.textContent,
  linkPreviewEndsEllipsis: linkPreviewP.textContent.endsWith('PickiPedia…'),
}));
"""
        proc = subprocess.run(['node', '-e', driver], capture_output=True, text=True, check=True)
        out = json.loads(proc.stdout.strip())
        self.assertTrue(out['shortMemoed'])
        self.assertFalse(out['shortHasDetails'])
        self.assertTrue(out['longPreviewText'].endswith('…'))
        self.assertGreaterEqual(out['longPreviewLen'], 160)
        self.assertLessEqual(out['longPreviewLen'], 205)
        self.assertTrue(out['longHasMention'])
        self.assertEqual(out['longSummary'], 'Full transcript · 54 words')
        self.assertTrue(out['longTailStayedOutsideDetails'])
        self.assertEqual(out['boundaryParaCount'], 1)
        self.assertTrue(out['boundaryTextEndsClean'])
        self.assertEqual(out['linkTextClean'], 'PickiPedia')
        self.assertTrue(out['linkPreviewEndsEllipsis'])
