"""The reference fingerprints: one per reference photo, with whose face it is.

The photos themselves stay on PickiPedia (Category:Face reference): they are the
record anyone can take elsewhere. What's kept here is only what can be made again
from them -- each photo's fingerprint, by MODEL -- in a SQLite file.
"""

import sqlite3
import threading

import numpy as np

from .engine import MODEL, SAME_PERSON, similarity


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.Lock()
        with self.lock:
            self.db.execute("""CREATE TABLE IF NOT EXISTS reference (
                file TEXT NOT NULL, model TEXT NOT NULL, person TEXT NOT NULL, sha1 TEXT NOT NULL,
                fingerprint BLOB NOT NULL, PRIMARY KEY (file, model))""")
            self.db.commit()
        self._cache = None

    def known(self):
        """{file: sha1} for this model's references: what needn't be fetched again."""
        with self.lock:
            return dict(self.db.execute('SELECT file, sha1 FROM reference WHERE model = ?', (MODEL,)))

    def put(self, file, person, sha1, fingerprint):
        with self.lock:
            self.db.execute('INSERT OR REPLACE INTO reference (file, model, person, sha1, fingerprint) VALUES (?, ?, ?, ?, ?)',
                            (file, MODEL, person, sha1, np.asarray(fingerprint, np.float32).tobytes()))
            self.db.commit()
            self._cache = None

    def relabel(self, file, person):
        with self.lock:
            self.db.execute('UPDATE reference SET person = ? WHERE file = ? AND model = ?', (person, file, MODEL))
            self.db.commit()
            self._cache = None

    def keep_only(self, files):
        """Forget references no longer on the wiki (taken out of the category, or deleted)."""
        files = set(files)
        with self.lock:
            gone = [f for (f,) in self.db.execute('SELECT file FROM reference WHERE model = ?', (MODEL,)) if f not in files]
            self.db.executemany('DELETE FROM reference WHERE file = ? AND model = ?', [(f, MODEL) for f in gone])
            # Fingerprints by another model can't be compared with this one's: made again from the photos.
            self.db.execute('DELETE FROM reference WHERE model != ?', (MODEL,))
            self.db.commit()
            self._cache = None
        return gone

    def references(self):
        """[(file, person, sha1, fingerprint)] for this model."""
        with self.lock:
            if self._cache is None:
                self._cache = [(f, p, s, np.frombuffer(b, np.float32))
                               for f, p, s, b in self.db.execute(
                                   'SELECT file, person, sha1, fingerprint FROM reference WHERE model = ? ORDER BY person, file',
                                   (MODEL,))]
            return self._cache

    def whose(self, fingerprint, threshold=SAME_PERSON, candidates=3):
        """The best match per person, best first: [{'person', 'similarity', 'file'}], up to `candidates`;
        and the match itself -- the best, if it's at or above `threshold` -- or None."""
        best = {}
        for file, person, _, ref in self.references():
            s = similarity(fingerprint, ref)
            if person not in best or s > best[person]['similarity']:
                best[person] = {'person': person, 'similarity': round(s, 3), 'file': file}
        ranked = sorted(best.values(), key=lambda m: -m['similarity'])[:candidates]
        match = ranked[0] if ranked and ranked[0]['similarity'] >= threshold else None
        return match, ranked
