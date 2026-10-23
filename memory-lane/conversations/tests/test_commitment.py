"""The record can be committed, checked in parts, and compared across time."""

import contextlib
import hashlib
import io
import json
import os
import random
import tempfile
import uuid
from pathlib import Path
from unittest import mock

from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase

from commitment import compare_memory, export, merkle, prove_memory, verify_memory
from conversations.models import (
    BlockAnchor, ContextHeap, ConversationParticipant, Era, Media, Message, Mood, Note,
    RawImportedContent, Setting, ThinkingEntity, Thought, ToolResult, ToolUse,
)

KEY = bytes(range(32))
OCT, NOV = 1_760_000_000_000, 1_763_000_000_000  # milliseconds: 2025-10-09, 2025-11-13


def run(tool, *args):
    """A stdlib tool's main(): (exit status, what it printed)."""
    printed = io.StringIO()
    with contextlib.redirect_stdout(printed):
        status = tool.main([tool.__name__, *map(str, args)])
    return status, printed.getvalue()


class TreeHashTest(SimpleTestCase):

    def test_rfc_6962_known_answers(self):
        inputs = [b'', b'\x00', b'\x10', b'\x20\x21', b'\x30\x31', b'\x40\x41\x42\x43',
                  bytes(range(0x50, 0x58)), bytes(range(0x60, 0x70))]
        roots = ['6e340b9cffb37a989ca544e6bb780a2c78901d3fb33738768511a30617afa01d',
                 'fac54203e7cc696cf0dfcb42c92a1d9dbaf70ad9e621f4bd8d98662f00e3c125',
                 'aeb6bcfe274b70a14fb067a5e5578264db0fa9b51af5e0ba159158f329e06e77',
                 'd37ee418976dd95753c1c73862b9398fa2a2cf9b4ff0fdfe8b30cd95209614b7',
                 '4e3bbb1f7b478dcfe71fb631631519a3bca12c9aefca1612bfce4c13a86264d4',
                 '76e67dadbcdf1e10e1b74ddc608abd2f98dfb16fbce75277b5232a127f2087ef',
                 'ddb89be403809e325750d3d263cd78929c2942b7942a34b77e122c9594a74c8c',
                 '5dc9da79a70659a9ad559cb701ded9a2ab9d823aad2f4960cfe370eff4604328']
        leaves = [merkle.h('sha256', merkle.LEAF + d) for d in inputs]
        for n, root in enumerate(roots, 1):
            self.assertEqual(merkle.tree_hash('sha256', leaves[:n]).hex(), root, f'{n} leaves')
        self.assertEqual(merkle.tree_hash('sha256', []), hashlib.sha256(b'').digest())

    def test_the_verifier_carries_the_same_tree(self):
        # verify_memory.py has its own copy so it can stand alone; hold the two together.
        rng = random.Random(6962)
        for n in list(range(0, 20)) + [31, 32, 33, 100]:
            hashes = [rng.randbytes(32) for _ in range(n)]
            for alg in merkle.ALGS:
                self.assertEqual(merkle.tree_hash(alg, hashes), verify_memory.tree_hash(alg, hashes))

    def test_every_inclusion_path_leads_to_the_root_and_no_other(self):
        rng = random.Random(9162)
        for n in list(range(1, 41)) + [63, 64, 65, 100]:
            hashes = [rng.randbytes(32) for _ in range(n)]
            for alg in merkle.ALGS:
                root = merkle.tree_hash(alg, hashes)
                self.assertEqual(prove_memory.tree_hash(alg, hashes), root)
                for m in range(n):
                    path = prove_memory.audit_path(alg, hashes, m)
                    self.assertEqual(verify_memory.root_from_path(alg, m, n, hashes[m], path), root, (n, m))
                    self.assertLessEqual(len(path), (n - 1).bit_length())
                    # The same path claimed for another place, or one hash longer, fails. (A wrong size
                    # can still reach the root: SPEC.md says why that proves no less.)
                    if n > 1:
                        self.assertNotEqual(verify_memory.root_from_path(alg, (m + 1) % n, n, hashes[m], path), root)
                    self.assertIsNone(verify_memory.root_from_path(alg, m, n, hashes[m], path + [root]))
                self.assertIsNone(verify_memory.root_from_path(alg, n, n, hashes[0], []))

    def test_a_leaf_cannot_pass_for_a_node(self):
        a, b = b'a' * 32, b'b' * 32
        self.assertNotEqual(merkle.tree_hash('sha256', [a, b]), merkle.h('sha256', merkle.LEAF + a + b))


class CommitmentTest(TestCase):

    def setUp(self):
        self.justin = ThinkingEntity.objects.create(name='justin', participant_type='human')
        self.magent = ThinkingEntity.objects.create(name='magent', participant_type='ai', is_biological_human=False)
        self.kid = ThinkingEntity.objects.create(name='fibonacci', participant_type='human')
        self.tool = ConversationParticipant.objects.create(name='tool-result', participant_type='tool')
        self.heap = ContextHeap.objects.create(era=Era.objects.create(name='Foundation'), type='fresh')
        self.mood = Mood.objects.create(slug='porch', title='The porch')

        def say(cls, sender, content, timestamp, to=(), **extra):
            message = cls.objects.create(id=uuid.uuid4(), sender=sender, content=content, timestamp=timestamp,
                                         context_heap=self.heap, **extra)
            message.recipients.set(to)
            return message

        self.hello = say(Message, self.justin, 'Hey there — do you have a name?', OCT,
                         to=[self.magent], mood=self.mood)
        self.reply = say(Message, self.magent, 'You can call me Claude.', OCT + 1000, to=[self.justin, self.kid],
                         mood=self.mood, model_backend='gpt-4o')
        self.thought = say(Thought, self.magent, 'They want a name.', NOV, signature='sig')
        self.call = say(ToolUse, self.magent, {'command': 'date'}, NOV + 1, tool_name='Bash', tool_id='toolu_1')
        self.result = say(ToolResult, self.tool, {'stdout': 'a\ttab\nand a newline', 'seconds': 1.5}, NOV + 2,
                          tool_use_id='toolu_1')
        self.games = say(Message, self.kid, 'can you make the musik the same ★', None, to=[self.magent])

        Note.objects.create(content_type=ContentType.objects.get_for_model(Message), object_id=self.hello.id,
                            from_entity=self.magent, content='The first message.')
        RawImportedContent.objects.create(content_type=ContentType.objects.get_for_model(Message),
                                          object_id=self.hello.id, raw_data={'type': 'user', 'secret': 'hunter2'})
        self.picture = b'\x89PNG not really'
        Media.objects.create(sha256=hashlib.sha256(self.picture).hexdigest(), mime='image/png',
                             data=self.picture, size=len(self.picture), added_by=self.justin)
        BlockAnchor.objects.create(number=21_081_875, timestamp=1_730_246_400)
        BlockAnchor.objects.create(number=9, timestamp=1)  # sorts after 21081875 as text; the export must cope
        Setting.objects.create(key='effort', value='high', set_by=self.justin)

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def bundle(self, name, **kwargs):
        out = Path(self.tmp.name) / name
        kwargs.setdefault('sealed', True)
        manifest, edition = export.export(out, KEY, kwargs.pop('epoch', 1), 26_000_000, **kwargs)
        return out, manifest, edition

    def lines(self, out, chunk):
        return [line.split(b'\t', 3) for line in (out / 'chunks' / f'{chunk}.tsv').read_bytes().splitlines()]

    def test_a_bundle_checks_against_its_own_manifest(self):
        out, manifest, edition = self.bundle('a')
        status, printed = run(verify_memory, out)
        self.assertEqual(status, 0, printed)
        self.assertNotIn('FAILED', printed)
        self.assertNotIn('NOT SEALED', printed)
        self.assertIn(manifest['root']['sha256'], printed)
        self.assertIn(hashlib.sha256((out / 'manifest.json').read_bytes()).hexdigest(), printed)

        names = [c['name'] for c in manifest['chunks']]
        self.assertEqual(names, sorted(names))
        for expected in ('messages/2025-10', 'messages/2025-11', 'messages/undated', 'heaps', 'eras', 'notes',
                         'participants', 'moods', 'block_anchors', 'settings', 'media', 'content_types'):
            self.assertIn(expected, names)
        self.assertEqual(manifest['totals']['leaves'], sum(c['leaves'] for c in manifest['chunks']))
        self.assertEqual(manifest['views'][f'mood/{self.mood.id}']['leaves'], 2)
        self.assertEqual(manifest['views']['sender/magent']['leaves'], 3)
        self.assertEqual(manifest['views']['sender/fibonacci']['leaves'], 1)
        self.assertEqual(edition['withheld'], {})
        # The bundle carries what a stranger needs to check it.
        for name in ('SPEC.md', 'verify_memory.py', 'prove_memory.py', 'compare_memory.py', 'README.txt'):
            self.assertTrue((out / name).exists(), name)

    def test_records_follow_the_rules_in_the_spec(self):
        out, _, _ = self.bundle('a')
        by_key = {key.decode(): json.loads(record) for kind, key, salt, record in self.lines(out, 'messages/2025-10')}
        reply = by_key[f'message/{self.reply.id}']
        self.assertEqual(list(reply), sorted(reply))
        self.assertNotIn(None, reply.values())            # nulls are left out,
        self.assertNotIn('parent_id', reply)              # so an absent parent is an absent key
        self.assertEqual(reply['kind'], 'message')
        self.assertEqual(reply['recipients'], ['fibonacci', 'justin'])
        self.assertEqual(reply['model_backend'], 'gpt-4o')
        self.assertRegex(reply['created_at'], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z$')

        november = {json.loads(r)['kind']: json.loads(r) for _, _, _, r in self.lines(out, 'messages/2025-11')}
        self.assertEqual(november['thought']['signature'], 'sig')
        self.assertEqual(november['tool_use']['tool_name'], 'Bash')
        # A tab and a newline inside the content stayed inside the JSON: the line still has four fields.
        self.assertEqual(november['tool_result']['content']['stdout'], 'a\ttab\nand a newline')
        self.assertEqual(november['tool_result']['content']['seconds'], 1.5)

        undated = self.lines(out, 'messages/undated')
        self.assertIn('musik the same ★'.encode(), undated[0][3])  # written as UTF-8, not \\u escapes
        # Keys are in byte order, which for numbers is not numeric order.
        anchors = [key for _, key, _, _ in self.lines(out, 'block_anchors')]
        self.assertEqual(anchors, [b'block_anchor/21081875', b'block_anchor/9'])
        self.assertEqual((out / 'media' / f'{hashlib.sha256(self.picture).hexdigest()}.png').read_bytes(), self.picture)

    def test_the_same_record_gives_the_same_bytes(self):
        a, first, _ = self.bundle('a')
        b, second, _ = self.bundle('b')
        self.assertEqual(first['root'], second['root'])
        self.assertEqual(first['chunks'], second['chunks'])
        for path in sorted((a / 'chunks').rglob('*.tsv')):
            self.assertEqual(path.read_bytes(), (b / path.relative_to(a)).read_bytes(), path.name)

    def test_withholding_changes_what_is_published_and_not_what_is_committed(self):
        full, whole, _ = self.bundle('full')
        out, manifest, edition = self.bundle(
            'edition', withhold=['table:raw', 'table:media', 'kind:tool_result', 'sender:fibonacci'])
        self.assertEqual(manifest['root'], whole['root'])
        self.assertEqual(manifest['chunks'], whole['chunks'])
        self.assertEqual(manifest['views'], whole['views'])
        self.assertEqual(edition['withheld'], {'table:raw': 1, 'table:media': Media.objects.count(),
                                               'kind:tool_result': 1, 'sender:fibonacci': 1})

        everything = b''.join(p.read_bytes() for p in out.rglob('*') if p.is_file() and p.suffix in ('.tsv', '.json'))
        for secret in (b'hunter2', b'musik', b'and a newline'):
            self.assertNotIn(secret, everything)
        self.assertFalse((out / 'media').exists())

        status, printed = run(verify_memory, out)
        self.assertEqual(status, 0, printed)
        self.assertIn(f'{3 + Media.objects.count()} withheld', printed)
        self.assertIn('partial sender/fibonacci  0 of 1', printed)
        self.assertIn('ok      sender/justin', printed)

        # A withheld leaf is salted: its hash is not the hash of its record alone.
        kind, key, sha256_hex, _ = self.lines(out, 'messages/undated')[0]
        record = self.lines(full, 'messages/undated')[0][3]
        self.assertEqual(kind, b'W')
        self.assertNotEqual(sha256_hex.decode(), hashlib.sha256(b'\x00' + record).hexdigest())
        self.assertEqual(sha256_hex.decode(),
                         merkle.leaf_hash('sha256', merkle.salt_for(KEY, key.decode()), record).hex())

    def test_a_changed_byte_is_caught_and_only_its_chunk_fails(self):
        out, _, _ = self.bundle('a')
        path = out / 'chunks' / 'messages' / '2025-10.tsv'
        path.write_bytes(path.read_bytes().replace(b'call me Claude', b'call me Magent'))
        status, printed = run(verify_memory, out)
        self.assertEqual(status, 1)
        self.assertIn('FAILED  messages/2025-10', printed)
        # The chunk, and both views the altered message belongs to; nothing else.
        self.assertEqual(printed.count('FAILED'), 3)
        self.assertIn(f'FAILED  mood/{self.mood.id}', printed)
        self.assertIn('FAILED  sender/magent', printed)
        self.assertIn('ok      sender/justin', printed)
        self.assertIn('ok      messages/2025-11', printed)
        self.assertIn('NOT verified', printed)

    def test_a_missing_chunk_leaves_the_rest_checkable(self):
        out, _, _ = self.bundle('a')
        (out / 'chunks' / 'messages' / '2025-11.tsv').unlink()
        status, printed = run(verify_memory, out)
        self.assertEqual(status, 0, printed)
        self.assertIn('missing messages/2025-11  (3 leaves cannot be checked)', printed)
        self.assertIn('ok      messages/2025-10', printed)

    def test_reordered_or_dropped_lines_are_caught(self):
        out, _, _ = self.bundle('a')
        path = out / 'chunks' / 'messages' / '2025-10.tsv'
        first, second = path.read_bytes().splitlines(keepends=True)
        path.write_bytes(second + first)
        self.assertEqual(run(verify_memory, out)[0], 1)
        path.write_bytes(first)
        self.assertEqual(run(verify_memory, out)[0], 1)

    def test_a_relabelled_key_is_caught(self):
        # The key is not in the leaf hash, so the roots alone would not notice; the record's own id does.
        out, _, _ = self.bundle('a')
        path = out / 'chunks' / 'messages' / '2025-10.tsv'
        first, second = path.read_bytes().splitlines(keepends=True)
        kind, key, rest = first.split(b'\t', 2)
        path.write_bytes(b'\t'.join([kind, b'message/00000000-0000-0000-0000-000000000000', rest]) + second)
        status, printed = run(verify_memory, out)
        self.assertEqual(status, 1)
        self.assertIn("FAILED  messages/2025-10: message/00000000-0000-0000-0000-000000000000: the record's id is", printed)

    def prove(self, out, key):
        status, printed = run(prove_memory, out, key)
        self.assertEqual(status, 0, printed)
        return json.loads(printed)

    def check_one(self, proof):
        path = Path(self.tmp.name) / 'elsewhere' / 'proof.json'
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(proof, ensure_ascii=False), encoding='utf-8')
        return run(verify_memory, '--one', path)

    def test_any_leaf_is_proved_and_checked_alone(self):
        out, manifest, _ = self.bundle('a')
        keys = [key.decode() for p in sorted((out / 'chunks').rglob('*.tsv'))
                for kind, key, _, _ in (line.split(b'\t', 3) for line in p.read_bytes().splitlines())]
        self.assertEqual(len(keys), manifest['totals']['leaves'])
        for key in keys:
            proof = self.prove(out, key)
            self.assertEqual(proof['root'], manifest['root'])
            status, printed = self.check_one(proof)
            self.assertEqual(status, 0, printed)
            self.assertIn(manifest['root']['sha3_256'], printed)

        proof = self.prove(out, f'message/{self.hello.id}')
        self.assertEqual(proof['chunk'], 'messages/2025-10')
        self.assertEqual(json.loads(proof['record'])['content'], 'Hey there — do you have a name?')
        status, printed = self.check_one(proof)
        self.assertIn('sent by justin', printed)
        self.assertIn('ok      the key names this record', printed)
        self.assertIn('ok      sha3_256: leaf -> messages/2025-10 -> root', printed)
        self.assertNotIn('NOT SEALED', printed)

    def test_a_proof_with_anything_changed_fails(self):
        out, _, _ = self.bundle('a')
        proof = self.prove(out, f'message/{self.reply.id}')
        self.assertEqual(self.check_one(proof)[0], 0)
        other = self.prove(out, f'message/{self.hello.id}')

        def changed(**fields):
            return {**json.loads(json.dumps(proof)), **fields}

        flipped = proof['chunk_path']['sha256'][0][:-1] + ('0' if proof['chunk_path']['sha256'][0][-1] != '0' else '1')
        for name, bad in [
            ('words', changed(record=proof['record'].replace('call me Claude', 'call me Magent'))),
            ('salt', changed(salt=other['salt'])),
            ('a path hash', changed(chunk_path={**proof['chunk_path'], 'sha256': [flipped]})),
            ('place', changed(leaf_index=1 - proof['leaf_index'])),
            ('chunk', changed(chunk='messages/2025-11')),
            ('chunk place', changed(chunk_index=proof['chunk_index'] + 1)),
            ('root', changed(root=other['root'] | {'sha256': '00' * 32})),
            ('key', changed(key=other['key'])),
            ('another record', changed(record=other['record'], key=other['key'])),
        ]:
            status, printed = self.check_one(bad)
            self.assertEqual(status, 1, f'{name}: {printed}')
            self.assertIn('does NOT show', printed, name)

        del proof['salt']
        status, printed = self.check_one(proof)
        self.assertEqual(status, 1)
        self.assertIn('the proof is malformed', printed)

    def test_a_withheld_leaf_has_no_proof_and_a_rehearsal_says_so(self):
        out, _, _ = self.bundle('edition', withhold=['sender:fibonacci'])
        self.assertEqual(run(prove_memory, out, f'message/{self.games.id}')[0], 1)
        self.assertEqual(run(prove_memory, out, 'message/not-there')[0], 1)

        rehearsal, _, _ = self.bundle('rehearsal', sealed=False)
        status, printed = self.check_one(self.prove(rehearsal, f'message/{self.hello.id}'))
        self.assertEqual(status, 0, printed)
        self.assertIn('NOT SEALED', printed)

    def test_comparing_two_epochs_shows_what_changed_underneath(self):
        before, _, _ = self.bundle('before', epoch=1)
        Message.objects.filter(id=self.reply.id).update(content='You can call me magent.')
        Message.objects.filter(id=self.games.id).delete()
        Message.objects.create(id=uuid.uuid4(), sender=self.justin, content='New.', timestamp=NOV + 5,
                               context_heap=self.heap)
        after, _, _ = self.bundle('after', epoch=2)

        status, printed = run(compare_memory, before, after)
        self.assertEqual(status, 1)
        self.assertIn('added: 1  (1 message)', printed)
        self.assertIn('removed: 1  (1 message)', printed)
        self.assertIn('altered: 1  (1 message)', printed)
        self.assertIn(f'altered message/{self.reply.id}: content', printed)
        self.assertIn(f'removed message/{self.games.id}', printed)

        # Nothing altered or removed: growth alone is not a finding.
        again, _, _ = self.bundle('again', epoch=3)
        status, printed = run(compare_memory, after, again)
        self.assertEqual(status, 0, printed)

        # Withheld on one side: the salted leaf hash still shows the change.
        hidden, _, _ = self.bundle('hidden', epoch=2, withhold=['sender:magent'])
        status, printed = run(compare_memory, before, hidden)
        self.assertEqual(status, 1)
        self.assertIn('altered: 1', printed)

    def test_a_rehearsal_says_so_and_a_sealed_commitment_needs_the_key(self):
        out = Path(self.tmp.name) / 'rehearsal'
        printed = io.StringIO()
        call_command('commit_memory', str(out), '--rehearsal', '--eth-block', '26000000', stdout=printed)
        manifest = json.loads((out / 'manifest.json').read_bytes())
        self.assertFalse(manifest['sealed'])
        self.assertEqual(manifest['epoch'], 0)
        self.assertIn('Verified with its own verify_memory.py', printed.getvalue())
        self.assertIn('NOT SEALED', run(verify_memory, out)[1])

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop('MEMORY_COMMITMENT_KEY', None)
            with self.assertRaises(CommandError):
                call_command('commit_memory', str(Path(self.tmp.name) / 'x'), '--epoch', '1', '--eth-block', '1')
        with mock.patch.dict(os.environ, {'MEMORY_COMMITMENT_KEY': KEY.hex()}):
            sealed = Path(self.tmp.name) / 'sealed'
            call_command('commit_memory', str(sealed), '--epoch', '1', '--eth-block', '26000000', stdout=io.StringIO())
            manifest = json.loads((sealed / 'manifest.json').read_bytes())
            self.assertTrue(manifest['sealed'])
            self.assertEqual(manifest['salt_key_id'], hashlib.sha256(KEY).hexdigest()[:16])
            self.assertNotIn(KEY.hex(), (sealed / 'manifest.json').read_text())
            with self.assertRaises(CommandError):  # never write over a bundle
                call_command('commit_memory', str(sealed), '--epoch', '1', '--eth-block', '1')
            with self.assertRaises(CommandError):  # a sealed commitment covers every table
                call_command('commit_memory', str(Path(self.tmp.name) / 'y'), '--epoch', '1', '--allow-missing',
                             '--eth-block', '1')

    def test_sign_in_state_is_not_part_of_the_record(self):
        committed = {t for table in export.TABLES for t in table.needs}
        for private in ('devices', 'login_codes', 'django_session', 'auth_user', 'sealed_copies'):
            self.assertNotIn(private, committed)
            self.assertIn(private, export.NOT_COMMITTED)

    def test_every_table_is_committed_or_said_not_to_be(self):
        committed = {t for table in export.TABLES for t in table.needs}
        self.assertEqual(committed & set(export.NOT_COMMITTED), set())
        self.assertEqual(export.existing_tables() - committed - set(export.NOT_COMMITTED), set(),
                         'a table that is neither committed nor in export.NOT_COMMITTED')

    def test_every_table_of_the_record_has_keys_that_name_its_records(self):
        from django.utils import timezone
        from conversations.models import (FaceName, MessageChange, MoodAlias, Tiding, TidingComment,
                                          TidingVote, YarnClip)
        picture = Media.objects.get(sha256=hashlib.sha256(self.picture).hexdigest())
        MoodAlias.objects.create(slug='old-porch', mood=self.mood)
        MessageChange.objects.create(message=self.reply, mood=self.mood, kind='edited', by='magent')
        tiding = Tiding.objects.create(message=self.hello, url='https://pickipedia.xyz/', by=self.justin)
        comment = TidingComment.objects.create(tiding=tiding, by=self.magent, text='A good link.')
        TidingVote.objects.create(tiding=tiding, by=self.justin)
        FaceName.objects.create(media=picture, box=[0, 0, 1, 1], person='Justin Holmes', by=self.justin,
                                at=timezone.now())
        YarnClip.objects.create(clip=uuid.uuid4(), media=picture)
        out, manifest, _ = self.bundle('a')
        names = {c['name'] for c in manifest['chunks']}
        for chunk in ('mood_aliases', 'message_changes', 'tidings', 'tiding_comments', 'tiding_votes',
                      'face_names', 'yarn_clips'):
            self.assertIn(chunk, names)
        status, printed = run(verify_memory, out)
        self.assertEqual(status, 0, printed)
        status, printed = self.check_one(self.prove(out, f'tiding_comment/{comment.id}'))
        self.assertEqual(status, 0, printed)

    def test_a_clip_that_is_not_ours_is_committed_and_not_published(self):
        clip = b'\x00\x00\x00\x18ftypmp42 a film'
        sha = hashlib.sha256(clip).hexdigest()
        Media.objects.create(sha256=sha, mime='video/mp4', data=clip, size=len(clip), license='')
        out, manifest, edition = self.bundle('a')
        media = {key.decode(): json.loads(record) for _, key, _, record in self.lines(out, 'media')}
        self.assertEqual(media[f'media/{sha}']['license'], '')
        self.assertFalse((out / 'media' / f'{sha}.mp4').exists())
        self.assertTrue((out / 'media' / f'{hashlib.sha256(self.picture).hexdigest()}.png').exists())
        self.assertEqual(edition['media_files'], Media.objects.exclude(license='').count())
        self.assertEqual(run(verify_memory, out)[0], 0)
