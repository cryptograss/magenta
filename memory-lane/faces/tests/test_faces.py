"""faces, against public-domain NASA portraits (pictures/SOURCES.md): two of Suni Williams,
one of Peggy Whitson, and one of Williams beside Butch Wilmore.

Needs the two models (FACES_MODELS_DIR, as the image has them); run with
`python -m unittest faces.tests.test_faces` from the repository's root.
"""

import json
import os
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

from faces import wiki
from faces.engine import SAME_PERSON, Engine, similarity
from faces.server import App, handler_for
from faces.store import Store

HERE = os.path.join(os.path.dirname(__file__), 'pictures')
ENGINE = Engine()


def picture(name):
    with open(os.path.join(HERE, name), 'rb') as f:
        return f.read()


def ref(file, person, sha1='1'):
    return {'file': f'File:{file}', 'person': person, 'sha1': sha1, 'url': file}


class EngineTest(unittest.TestCase):

    def first(self, name):
        return ENGINE.look(picture(name))[2][0]['fingerprint']

    def test_the_same_person_in_two_photos_years_apart_and_two_people(self):
        williams, williams_again, whitson = self.first('williams_a.jpg'), self.first('williams_b.jpg'), self.first('whitson.jpg')
        self.assertGreater(similarity(williams, williams_again), SAME_PERSON)
        self.assertLess(similarity(williams, whitson), SAME_PERSON)

    def test_every_face_in_a_picture_in_its_own_pixels(self):
        width, height, faces = ENGINE.look(picture('crew.jpg'))
        self.assertEqual((width, height, len(faces)), (960, 768, 2))
        left = min(faces, key=lambda f: f['box'][0])
        self.assertGreater(similarity(left['fingerprint'], self.first('williams_a.jpg')), SAME_PERSON)  # Williams is on the left

    def test_not_a_picture(self):
        self.assertIsNone(ENGINE.look(b'not a picture'))


class SyncAndMatchTest(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.dir.name, 'faces.sqlite3'))
        self.app = App('k' * 32, self.store, ENGINE)

    def tearDown(self):
        self.dir.cleanup()

    def sync(self, listed):
        return wiki.sync(self.store, ENGINE, listed=listed, get=picture)

    def test_references_come_from_the_category_and_a_crew_photo_is_told(self):
        done = self.sync([ref('williams_a.jpg', 'Suni Williams'), ref('whitson.jpg', 'Peggy Whitson'),
                          ref('williams_b.jpg', None)])
        self.assertEqual(sorted(done['added']), ['File:whitson.jpg', 'File:williams_a.jpg'])
        self.assertEqual(done['unnamed'], ['File:williams_b.jpg'])  # no person named: left out, and said
        seen = self.app.match(picture('crew.jpg'))
        left, right = sorted(seen['faces'], key=lambda f: f['box'][0])
        self.assertEqual(left['match']['person'], 'Suni Williams')
        self.assertIsNone(right['match'])  # Wilmore has no reference: nobody's name on him
        self.assertTrue(all(c['similarity'] < SAME_PERSON for c in right['candidates']))  # only the nearest, said as such

    def test_unchanged_photos_arent_fetched_again_renamed_ones_relabel_removed_ones_go(self):
        self.sync([ref('williams_a.jpg', 'Suni Williams'), ref('whitson.jpg', 'Peggy Whitson')])
        fetched = []
        def get(url):
            fetched.append(url)
            return picture(url)
        done = wiki.sync(self.store, ENGINE, listed=[ref('williams_a.jpg', 'Sunita Williams')], get=get)
        self.assertEqual(fetched, [])
        self.assertEqual((done['relabelled'], done['removed']), (['File:williams_a.jpg'], ['File:whitson.jpg']))
        self.assertEqual([p for _, p, _, _ in self.store.references()], ['Sunita Williams'])

    def test_a_reference_with_no_face_is_said_and_left_out(self):
        done = wiki.sync(self.store, ENGINE, listed=[ref('blank', 'Nobody')], get=lambda url: _blank_png())
        self.assertEqual((done['no_face'], self.store.references()), (['File:blank'], []))

    def test_export_is_every_fingerprint_with_whose_and_the_model(self):
        self.sync([ref('williams_a.jpg', 'Suni Williams')])
        out = self.app.export()
        self.assertEqual(out['model'], 'opencv-yunet-2023mar+sface-2021dec')
        self.assertEqual((out['references'][0]['person'], len(out['references'][0]['fingerprint'])), ('Suni Williams', 128))


class TemplateTest(unittest.TestCase):

    def test_whose_face_a_page_says(self):
        self.assertEqual(wiki.person_in('{{Face reference|Justin Holmes}}'), 'Justin Holmes')
        self.assertEqual(wiki.person_in('x {{face_reference | person = Skyler Golden | taken=2026}} y'), 'Skyler Golden')
        self.assertIsNone(wiki.person_in('[[Category:Face reference]]'))


class HttpTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.TemporaryDirectory()
        store = Store(os.path.join(cls.dir.name, 'faces.sqlite3'))
        wiki.sync(store, ENGINE, listed=[ref('williams_a.jpg', 'Suni Williams')], get=picture)
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(App('k' * 32, store, ENGINE)))
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f'http://127.0.0.1:{cls.server.server_address[1]}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.dir.cleanup()

    def call(self, path, body=None, key=None):
        req = urllib.request.Request(self.base + path, data=body, method='POST' if body is not None else 'GET')
        if key:
            req.add_header('Authorization', 'Bearer ' + key)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.load(e)

    def test_health_is_open_the_rest_needs_the_key(self):
        status, body = self.call('/health')
        self.assertEqual((status, body['references'], body['people']), (200, 1, 1))
        self.assertEqual(self.call('/match', picture('crew.jpg'))[0], 401)
        self.assertEqual(self.call('/match', picture('crew.jpg'), key='wrong' * 8)[0], 401)
        self.assertEqual(self.call('/export')[0], 401)

    def test_a_picture_answered(self):
        status, body = self.call('/match', picture('crew.jpg'), key='k' * 32)
        self.assertEqual(status, 200)
        self.assertEqual(sorted((f['match'] or {}).get('person', '-') for f in body['faces']), ['-', 'Suni Williams'])
        self.assertEqual(self.call('/match', b'nope', key='k' * 32)[0], 400)


def _blank_png():
    import cv2
    import numpy as np
    return cv2.imencode('.png', np.full((200, 200, 3), 200, np.uint8))[1].tobytes()


if __name__ == '__main__':
    unittest.main()
