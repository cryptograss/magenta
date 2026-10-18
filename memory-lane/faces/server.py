"""faces: whose faces are in a picture, from reference photos on PickiPedia.

    GET  /health   the model, and how many references and people it knows (no key needed)
    POST /match    the body is a picture; answers its faces, each with whose it is, if known,
                   and the nearest candidates: {model, width, height, faces: [{box, score,
                   match: {person, similarity, file} | null, candidates: [...]}]}
                   ?threshold= overrides how alike counts as the same person
    POST /sync     read Category:Face reference again now (it's also read every SYNC_MINUTES)
    GET  /export   every reference fingerprint, with whose and by which model, as JSON

Everything but /health needs `Authorization: Bearer <FACES_KEY>`. There is no default
key: without FACES_KEY set, it won't start.
"""

import hmac
import json
import logging
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import wiki
from .engine import MODEL, SAME_PERSON, Engine
from .store import Store

log = logging.getLogger('faces')

MAX_BYTES = 30 * 1024 * 1024   # a phone's photo, with room
SYNC_MINUTES = float(os.environ.get('FACES_SYNC_MINUTES', '10'))


class App:
    def __init__(self, key, store, engine, api=wiki.API):
        self.key, self.store, self.engine, self.api = key, store, engine, api
        self.syncing = threading.Lock()
        self.last_sync = None

    def sync(self):
        with self.syncing:
            done = wiki.sync(self.store, self.engine, self.api)
            self.last_sync = {'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), **{k: len(v) for k, v in done.items()}}
            if any(done[k] for k in ('added', 'relabelled', 'removed', 'no_face', 'failed', 'unnamed')):
                log.info('sync: %s', json.dumps(done))
            return done

    def health(self):
        refs = self.store.references()
        return {'model': MODEL, 'references': len(refs), 'people': len({p for _, p, _, _ in refs}),
                'last_sync': self.last_sync}

    def match(self, data, threshold=SAME_PERSON):
        seen = self.engine.look(data)
        if seen is None:
            return None
        width, height, faces = seen
        out = []
        for f in faces:
            match, candidates = self.store.whose(f['fingerprint'], threshold)
            out.append({'box': f['box'], 'score': f['score'], 'match': match, 'candidates': candidates})
        return {'model': MODEL, 'threshold': threshold, 'width': width, 'height': height, 'faces': out}

    def export(self):
        return {'model': MODEL, 'same_person_at': SAME_PERSON, 'references': [
            {'file': f, 'person': p, 'sha1': s, 'fingerprint': [round(float(v), 6) for v in fp]}
            for f, p, s, fp in self.store.references()]}


def handler_for(app):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'faces/1'

        def log_message(self, fmt, *args):
            log.debug(fmt, *args)

        def answer(self, status, body):
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def allowed(self):
            if hmac.compare_digest(self.headers.get('Authorization', '').encode(), f'Bearer {app.key}'.encode()):
                return True
            self.answer(401, {'error': 'needs the key'})
            return False

        def do_GET(self):
            path = urlparse(self.path).path
            if path == '/health':
                return self.answer(200, app.health())
            if path == '/export':
                return self.allowed() and self.answer(200, app.export())
            self.answer(404, {'error': 'no such thing'})

        def do_POST(self):
            url = urlparse(self.path)
            if url.path not in ('/match', '/sync'):
                return self.answer(404, {'error': 'no such thing'})
            if not self.allowed():
                return
            if url.path == '/sync':
                done = app.sync()
                return self.answer(200, {k: v for k, v in done.items()})
            length = int(self.headers.get('Content-Length') or 0)
            if not length:
                return self.answer(400, {'error': 'send the picture as the body'})
            if length > MAX_BYTES:
                return self.answer(413, {'error': f'larger than {MAX_BYTES // (1024 * 1024)} MB'})
            data = self.rfile.read(length)
            try:
                threshold = float(parse_qs(url.query).get('threshold', [SAME_PERSON])[0])
            except ValueError:
                return self.answer(400, {'error': 'threshold is a number'})
            result = app.match(data, threshold)
            if result is None:
                return self.answer(400, {'error': 'not a picture this can read (JPEG, PNG, WebP)'})
            self.answer(200, result)

    return Handler


def keep_syncing(app):
    while True:
        try:
            app.sync()
        except Exception:
            log.exception('sync failed; trying again next round')
        time.sleep(SYNC_MINUTES * 60)


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    key = os.environ.get('FACES_KEY', '')
    if len(key) < 16:
        sys.exit('FACES_KEY must be set (16 characters or more): from the vault, never made up here')
    app = App(key, Store(os.environ.get('FACES_DB', '/data/faces.sqlite3')), Engine())
    threading.Thread(target=keep_syncing, args=(app,), daemon=True).start()
    port = int(os.environ.get('FACES_PORT', '8090'))
    log.info('faces (%s) on :%d', MODEL, port)
    ThreadingHTTPServer(('0.0.0.0', port), handler_for(app)).serve_forever()


if __name__ == '__main__':
    main()
