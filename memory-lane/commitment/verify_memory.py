#!/usr/bin/env python3
"""Check a memory commitment bundle against its manifest, or one leaf against its root.

    python3 verify_memory.py BUNDLE_DIR
    python3 verify_memory.py --one PROOF.json

Needs only Python's standard library, and nothing else from wherever this
file was found. SPEC.md beside it says what is being checked and why.

A bundle is a directory holding manifest.json and chunks/<name>.tsv. Any
chunk may be missing; the others are still checked. Exit status is 0 only
if nothing that is present disagrees with the manifest.

Compare the three values printed under "to compare with the chain" against
the commitment recorded on Ethereum. If they match, every leaf this script
accepted is exactly as it was when the commitment was made.
"""
import hashlib
import json
import sys
from pathlib import Path

ALGS = ('sha256', 'sha3_256')
LEAF, NODE = b'\x00', b'\x01'
SPEC_ID = 'cryptograss-memory-commitment/1'

# The column of each table's record that its key names: a key is <table>/<that value>.
KEY_COLUMNS = {
    'message': 'id', 'raw': 'id', 'heap': 'id', 'era': 'id', 'compacting_action': 'id', 'summary': 'id',
    'note': 'id', 'mood': 'id', 'mood_alias': 'id', 'mood_session': 'session_id', 'message_change': 'message_id',
    'tiding': 'id', 'tiding_comment': 'id', 'tiding_vote': 'id', 'face_name': 'id', 'yarn_clip': 'clip',
    'participant': 'name', 'topic': 'id', 'message_topic': 'id', 'conversation_file': 'id',
    'block_anchor': 'number', 'setting': 'id', 'content_type': 'id', 'media': 'sha256',
}


def h(alg, data):
    return hashlib.new(alg, data).digest()


def tree_hash(alg, hashes):
    def mth(lo, hi):
        n = hi - lo
        if n == 1:
            return hashes[lo]
        k = 1 << ((n - 1).bit_length() - 1)
        return h(alg, NODE + mth(lo, lo + k) + mth(lo + k, hi))

    return mth(0, len(hashes)) if hashes else h(alg, b'')


def root_from_path(alg, index, size, leaf, path):
    """The root that an inclusion path leads to (RFC 9162, section 2.1.3.2), or None.

    None means the path does not fit a tree of `size` leaves at `index`.
    """
    if not 0 <= index < size:
        return None
    fn, sn, r = index, size - 1, leaf
    for p in path:
        if sn == 0:
            return None
        if fn & 1 or fn == sn:
            r = h(alg, NODE + p + r)
            while not fn & 1 and fn != 0:
                fn, sn = fn >> 1, sn >> 1
        else:
            r = h(alg, NODE + r + p)
        fn, sn = fn >> 1, sn >> 1
    return r if sn == 0 else None


def key_problem(key, record):
    """Why a record cannot be the one its key names, or None if it can."""
    table, _, value = key.partition('/')
    column = KEY_COLUMNS.get(table)
    if column is None:
        return f'{key}: no table called {table!r} in version 1'
    named = json.loads(record).get(column)
    if isinstance(named, bool) or not isinstance(named, (str, int)) or str(named) != value:
        return f'{key}: the record\'s {column} is {named!r}'
    return None


def read_chunk(path):
    """Yield (key, {alg: leaf hash}, record text or None) for each line."""
    with open(path, 'rb') as f:
        for number, line in enumerate(f, 1):
            if not line.endswith(b'\n'):
                raise ValueError(f'line {number} does not end in a newline')
            parts = line[:-1].split(b'\t', 3)
            if len(parts) != 4:
                raise ValueError(f'line {number} has {len(parts)} fields, not 4')
            kind, key, a, b = parts
            if kind == b'D':
                salt = bytes.fromhex(a.decode('ascii'))
                if len(salt) != 32:
                    raise ValueError(f'line {number}: salt is not 32 bytes')
                yield key, {alg: h(alg, LEAF + salt + b) for alg in ALGS}, b
            elif kind == b'W':
                hashes = {'sha256': bytes.fromhex(a.decode('ascii')), 'sha3_256': bytes.fromhex(b.decode('ascii'))}
                if any(len(v) != 32 for v in hashes.values()):
                    raise ValueError(f'line {number}: a withheld hash is not 32 bytes')
                yield key, hashes, None
            else:
                raise ValueError(f'line {number} starts with {kind!r}, not D or W')


def check_chunk(path, entry, views):
    """Returns (problem or None, disclosed, withheld)."""
    leaves = {alg: [] for alg in ALGS}
    disclosed = withheld = 0
    previous = None
    for key, hashes, record in read_chunk(path):
        if previous is not None and key <= previous:
            return f'keys out of order at {key.decode("utf-8", "replace")}', disclosed, withheld
        previous = key
        for alg in ALGS:
            leaves[alg].append(hashes[alg])
        if record is None:
            withheld += 1
            continue
        disclosed += 1
        problem = key_problem(key.decode('utf-8'), record)
        if problem:
            return problem, disclosed, withheld
        if key.startswith(b'message/'):
            row = json.loads(record)
            for name in (f"mood/{row['mood_id']}" if row.get('mood_id') else None,
                         f"sender/{row['sender_id']}" if row.get('sender_id') else None):
                if name in views:
                    views[name].append((key, hashes))
    if len(leaves['sha256']) != entry['leaves']:
        return f"{len(leaves['sha256'])} leaves, manifest says {entry['leaves']}", disclosed, withheld
    for alg in ALGS:
        if tree_hash(alg, leaves[alg]).hex() != entry['roots'][alg]:
            return f'{alg} root does not match the manifest', disclosed, withheld
    return None, disclosed, withheld


def check_one(path):
    """Check one leaf's proof, as written by prove_memory.py, against the root it names."""
    proof = json.loads(Path(path).read_text(encoding='utf-8'))
    if proof.get('spec') != SPEC_ID:
        print(f"This script checks version 1 proofs; this one says {proof.get('spec')!r}.")
        return 2
    key, record, name = proof['key'], proof['record'].encode('utf-8'), proof['chunk']
    print(f"One leaf of {proof['subject']!r}, epoch {proof['epoch']}: {key}")
    if not proof.get('sealed'):
        print('NOT SEALED: from a rehearsal made with a throwaway key. It proves nothing.')

    failed = False
    try:
        problem = key_problem(key, record)
    except ValueError as e:
        problem = f'the record is not JSON: {e}'
    if problem:
        failed = True
        print(f'FAILED  {problem}')
    else:
        if key.startswith('message/'):
            row = json.loads(record)
            print(f"  sent by {row.get('sender_id')}, {row.get('created_at')}")
        print('ok      the key names this record')

    salt = bytes.fromhex(proof['salt'])
    if len(salt) != 32:
        failed = True
        print('FAILED  the salt is not 32 bytes')
    steps = f"{len(proof['chunk_path']['sha256'])} + {len(proof['root_path']['sha256'])} steps"
    for alg in ALGS:
        reached = None
        if len(salt) == 32:
            leaf = h(alg, LEAF + salt + record)
            chunk_root = root_from_path(alg, proof['leaf_index'], proof['chunk_size'], leaf,
                                        [bytes.fromhex(p) for p in proof['chunk_path'][alg]])
            if chunk_root is not None:
                entry = h(alg, LEAF + name.encode('utf-8') + b'\x00' + chunk_root)
                reached = root_from_path(alg, proof['chunk_index'], proof['chunk_count'], entry,
                                         [bytes.fromhex(p) for p in proof['root_path'][alg]])
        if reached is None:
            failed = True
            print(f'FAILED  {alg}: the path does not fit its tree')
        elif reached.hex() != proof['root'][alg]:
            failed = True
            print(f'FAILED  {alg}: the path leads to {reached.hex()}, not the root the proof names')
        else:
            print(f'ok      {alg}: leaf -> {name} -> root ({steps})')

    print()
    if failed:
        print('This proof does NOT show the leaf is in the commitment.')
        return 1
    print('To compare with the chain:')
    print(f"  root, SHA-256:      {proof['root']['sha256']}")
    print(f"  root, SHA3-256:     {proof['root']['sha3_256']}")
    return 0


def main(argv):
    if len(argv) == 3 and argv[1] == '--one':
        try:
            return check_one(argv[2])
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            print(f'FAILED  the proof is malformed: {e!r}')
            return 1
    if len(argv) != 2:
        print(__doc__)
        return 2
    bundle = Path(argv[1])
    manifest_bytes = (bundle / 'manifest.json').read_bytes()
    manifest = json.loads(manifest_bytes)
    if manifest.get('spec') != SPEC_ID:
        print(f"This script checks version 1 bundles; this one says {manifest.get('spec')!r}.")
        return 2

    failed = False
    print(f"Commitment of {manifest['subject']!r}, epoch {manifest['epoch']}, "
          f"taken {manifest['snapshot']['taken_at']} at Ethereum block {manifest['snapshot']['eth_block']}.")
    if not manifest.get('sealed'):
        print('NOT SEALED: a rehearsal made with a throwaway key. It commits to nothing.')

    # The root, from the manifest's own chunk entries.
    for alg in ALGS:
        entries = [h(alg, LEAF + c['name'].encode('utf-8') + b'\x00' + bytes.fromhex(c['roots'][alg]))
                   for c in sorted(manifest['chunks'], key=lambda c: c['name'].encode('utf-8'))]
        if tree_hash(alg, entries).hex() != manifest['root'][alg]:
            print(f'FAILED  the manifest\'s {alg} root is not the root of its chunks')
            failed = True

    views = {name: [] for name in manifest.get('views', {})}
    totals = {'ok': 0, 'missing': 0, 'bad': 0, 'disclosed': 0, 'withheld': 0, 'unverified': 0}
    for entry in manifest['chunks']:
        path = bundle / 'chunks' / (entry['name'] + '.tsv')
        if not path.exists():
            totals['missing'] += 1
            print(f"missing {entry['name']}  ({entry['leaves']} leaves cannot be checked)")
            continue
        try:
            problem, disclosed, withheld = check_chunk(path, entry, views)
        except (ValueError, KeyError, UnicodeDecodeError) as e:
            problem, disclosed, withheld = str(e), 0, 0
        if problem:
            totals['bad'] += 1
            totals['unverified'] += entry['leaves']
            failed = True
            print(f"FAILED  {entry['name']}: {problem}")
        else:
            totals['ok'] += 1
            totals['disclosed'] += disclosed
            totals['withheld'] += withheld
            print(f"ok      {entry['name']}  {disclosed} disclosed, {withheld} withheld")

    for name, expected in sorted(manifest.get('views', {}).items()):
        members = sorted(views[name], key=lambda m: m[0])
        if len(members) != expected['leaves']:
            print(f"partial {name}  {len(members)} of {expected['leaves']} leaves disclosed here; root not checkable")
            continue
        if all(tree_hash(alg, [m[1][alg] for m in members]).hex() == expected['roots'][alg] for alg in ALGS):
            print(f"ok      {name}  {len(members)} leaves")
        else:
            failed = True
            print(f"FAILED  {name}: root does not match the manifest")

    print()
    print(f"{totals['ok']} chunks verified, {totals['missing']} missing, {totals['bad']} failed; "
          f"{totals['disclosed']} leaves disclosed and verified, {totals['withheld']} withheld"
          + (f"; {totals['unverified']} leaves in failed chunks are NOT verified." if totals['bad'] else '.'))
    print('To compare with the chain:')
    print(f"  root, SHA-256:      {manifest['root']['sha256']}")
    print(f"  root, SHA3-256:     {manifest['root']['sha3_256']}")
    print(f"  manifest, SHA-256:  {hashlib.sha256(manifest_bytes).hexdigest()}")
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
