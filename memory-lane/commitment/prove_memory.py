#!/usr/bin/env python3
"""Write the proof that one leaf of a memory commitment is in it.

    python3 prove_memory.py BUNDLE_DIR KEY > proof.json

KEY is a leaf's key, for example message/0199c3a1-5c2e-7d41-9f0a-3b8e2c6d1a77.
The proof is a small file: the leaf's record and salt, and the hashes on
the way from it to its chunk's root and from there to the commitment's
root. With it, the leaf can be checked alone, without the bundle:

    python3 verify_memory.py --one proof.json

Needs only Python's standard library. Anyone holding a copy of the bundle
can make a proof; nothing secret is needed. SPEC.md says what a proof is.
"""
import hashlib
import json
import sys
from pathlib import Path

SPEC_ID = 'cryptograss-memory-commitment/1'
ALGS = ('sha256', 'sha3_256')
LEAF, NODE = b'\x00', b'\x01'


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


def audit_path(alg, hashes, m):
    """The inclusion path of leaf m, from the leaf upward (RFC 6962, section 2.1.1)."""
    def path(m, lo, hi):
        n = hi - lo
        if n == 1:
            return []
        k = 1 << ((n - 1).bit_length() - 1)
        if m < k:
            return path(m, lo, lo + k) + [tree_hash(alg, hashes[lo + k:hi])]
        return path(m - k, lo + k, hi) + [tree_hash(alg, hashes[lo:lo + k])]

    return path(m, 0, len(hashes))


class ProofError(Exception):
    pass


def find(bundle, manifest, key):
    """(chunk entry, leaf hashes of the chunk, index of the key, salt, record)."""
    wanted = key.encode('utf-8')
    for entry in manifest['chunks']:
        path = bundle / 'chunks' / (entry['name'] + '.tsv')
        if not path.exists():
            continue
        leaves, found = {alg: [] for alg in ALGS}, None
        with open(path, 'rb') as f:
            for line in f:
                kind, line_key, a, b = line.rstrip(b'\n').split(b'\t', 3)
                if kind == b'D':
                    salt = bytes.fromhex(a.decode('ascii'))
                    hashes = {alg: h(alg, LEAF + salt + b) for alg in ALGS}
                else:
                    hashes = {'sha256': bytes.fromhex(a.decode('ascii')), 'sha3_256': bytes.fromhex(b.decode('ascii'))}
                if line_key == wanted:
                    if kind != b'D':
                        raise ProofError(f'{key} is withheld in this copy: a proof needs its record')
                    found = (len(leaves['sha256']), salt, b)
                for alg in ALGS:
                    leaves[alg].append(hashes[alg])
        if found:
            for alg in ALGS:
                if tree_hash(alg, leaves[alg]).hex() != entry['roots'][alg]:
                    raise ProofError(f"this copy of {entry['name']} does not match its manifest")
            return (entry, leaves) + found
    raise ProofError(f'{key} is not in any chunk of this copy')


def prove(bundle, key):
    bundle = Path(bundle)
    manifest = json.loads((bundle / 'manifest.json').read_bytes())
    if manifest.get('spec') != SPEC_ID:
        raise ProofError(f"this makes version 1 proofs; the bundle says {manifest.get('spec')!r}")
    entry, leaves, index, salt, record = find(bundle, manifest, key)
    chunks = sorted(manifest['chunks'], key=lambda c: c['name'].encode('utf-8'))
    position = [c['name'] for c in chunks].index(entry['name'])
    entries = {alg: [h(alg, LEAF + c['name'].encode('utf-8') + b'\x00' + bytes.fromhex(c['roots'][alg]))
                     for c in chunks] for alg in ALGS}
    for alg in ALGS:
        if tree_hash(alg, entries[alg]).hex() != manifest['root'][alg]:
            raise ProofError(f"the manifest's {alg} root is not the root of its chunks")
    return {
        'spec': SPEC_ID,
        'subject': manifest['subject'],
        'epoch': manifest['epoch'],
        'sealed': manifest['sealed'],
        'key': key,
        'salt': salt.hex(),
        'record': record.decode('utf-8'),
        'chunk': entry['name'],
        'leaf_index': index,
        'chunk_size': len(leaves['sha256']),
        'chunk_path': {alg: [p.hex() for p in audit_path(alg, leaves[alg], index)] for alg in ALGS},
        'chunk_index': position,
        'chunk_count': len(chunks),
        'root_path': {alg: [p.hex() for p in audit_path(alg, entries[alg], position)] for alg in ALGS},
        'root': manifest['root'],
    }


def main(argv):
    if len(argv) != 3:
        print(__doc__)
        return 2
    try:
        proof = prove(argv[1], argv[2])
    except ProofError as e:
        print(f'No proof: {e}.', file=sys.stderr)
        return 1
    print(json.dumps(proof, indent=1, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
