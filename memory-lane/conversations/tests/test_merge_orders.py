"""Merge orders by mail (Plan, section 8): kept here unread, fetched by githouse, answered from its records."""

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path

from unittest import skipUnless

from django.core.cache import cache
from django.test import TestCase, override_settings

from conversations.models import MergeOrder, Setting
from conversations.services import merge_orders
from conversations.tests.test_mood_auth import HAS_SSH_KEYGEN, make_key, sign

JUNK = '-----BEGIN SSH SIGNATURE-----\nU1NIU0lH\n-----END SSH SIGNATURE-----\n'   # armored, signed by no one


def order(**change):
    fields = {'repo': 'magenta', 'branch': 're/foo', 'commit': 'a' * 40, 'into': 'main', 'by': 'skyler',
              'nonce': os.urandom(16).hex(), 'expires': str(int(time.time()) + 3600), **change}
    return merge_orders.HEADER + '\n' + ''.join(f'{k}: {fields[k]}\n' for k in merge_orders.FIELDS)


@skipUnless(HAS_SSH_KEYGEN, 'needs ssh-keygen')
class MergeOrdersTest(TestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.dir = tempfile.mkdtemp()
        cls.keys = {name: make_key(cls.dir, name) for name in ('justin', 'skyler')}
        # As on maybelle: hunter's inventory, in the sign-in namespace. The mailbox screens by these keys.
        cls.signers = os.path.join(cls.dir, 'allowed_signers')
        with open(cls.signers, 'w') as f:
            for name, key in cls.keys.items():
                f.write(f'{name} namespaces="magenta-moods" {open(key + ".pub").read().strip()}\n')

    def setUp(self):
        cache.clear()
        self.githouse = Path(tempfile.mkdtemp())
        (self.githouse / '.merges' / 'mailed').mkdir(parents=True)
        for patch in (override_settings(GITHOUSE_DIR=str(self.githouse)),
                      override_settings(MOOD_ALLOWED_SIGNERS=self.signers)):
            patch.enable()
            self.addCleanup(patch.disable)
        self.signed = {}

    def sign(self, text, key=None):
        signature = sign(self.keys[key or merge_orders.parse(text)['by']], text, namespace=merge_orders.NAMESPACE)
        self.signed[text] = signature
        return signature

    def post(self, text, signature=None, **more):
        signature = signature if signature is not None else self.sign(text)
        return self.client.post('/api/merge-orders/', json.dumps({'order': text, 'signature': signature, **more}),
                                content_type='application/json')

    def stored(self, text, signature=JUNK):
        """An order in the mailbox, put there directly: what githouse is handed doesn't depend on how it came."""
        fields = merge_orders.parse(text)
        self.signed[text] = signature
        return MergeOrder.objects.create(order=text, signature=signature, nonce=fields['nonce'], repo=fields['repo'],
                                         branch=fields['branch'], by=fields['by'], expires=fields['expires'])

    def pending(self):
        return [o['order'] for o in self.client.get('/api/merge-orders/pending/').json()['orders']]

    def status(self, posted):
        return self.client.get(f"/api/merge-orders/{posted.json()['id']}/").json()

    def githouse_kept(self, text, signature=None, **result):
        nonce = merge_orders.parse(text)['nonce']
        (self.githouse / '.merges' / f'{nonce}.json').write_text(
            json.dumps({**result, 'order': text, 'signature': signature or self.signed[text]}))

    def test_an_order_waits_for_githouse_until_it_has_been_carried_out(self):
        text = order()
        posted = self.post(text, mood='ramp')
        self.assertEqual(posted.status_code, 201)
        self.assertEqual(posted.json()['status'], 'waiting')
        self.assertEqual(self.pending(), [text])
        self.githouse_kept(text, outcome='merged', merge='b' * 40)
        self.assertEqual(self.pending(), [])
        said = self.status(posted)
        self.assertEqual((said['status'], said['merge'], said['repo'], said['by']), ('merged', 'b' * 40, 'magenta', 'skyler'))

    def test_githouse_s_refusal_is_said(self):
        text = order()
        posted = self.post(text)
        self.githouse_kept(text, outcome='refused', why='skyler: banned')
        self.assertEqual((self.status(posted)['status'], self.status(posted)['why']), ('refused', 'skyler: banned'))

    def test_an_order_refused_before_it_counted_as_signed(self):
        text = order()
        posted = self.post(text)
        seen = hashlib.sha256(f'{text}\0{self.signed[text]}'.encode()).hexdigest()
        (self.githouse / '.merges' / 'mailed' / f'{seen}.json').write_text(
            json.dumps({'outcome': 'refused', 'why': 'not signed by skyler, or skyler may not merge'}))
        self.assertEqual(self.pending(), [])
        self.assertIn('may not merge', self.status(posted)['why'])

    def test_strangers_and_forgeries_never_get_in(self):
        text = order()
        for why, signature in (('signed by no one', JUNK),
                               ("justin's key on an order by skyler", self.sign(text, key='justin')),
                               ('a sign-in signature', sign(self.keys['skyler'], text))):
            with self.subTest(why):
                said = self.post(text, signature=signature)
                self.assertEqual(said.status_code, 400)
                self.assertIn("not signed by skyler's key", said.json()['error'])
        self.assertEqual(self.post(order(by='nobody'), signature=JUNK).status_code, 400)  # no key: no ssh-keygen
        self.assertEqual(MergeOrder.objects.count(), 0)
        self.assertEqual(self.post(text).status_code, 201)  # the real one, nonce and all, after them

    def test_one_signer_s_flood_doesn_t_use_up_another_s_turn(self):
        for _ in range(20):
            self.assertEqual(self.post(order()).status_code, 201)
        flooded = self.post(order())
        self.assertEqual(flooded.status_code, 429)
        self.assertIn('skyler has sent 20', flooded.json()['error'])
        self.assertEqual(self.post(order(by='justin')).status_code, 201)

    def test_two_orders_with_one_nonce_only_one_is_carried_out(self):
        text = order()
        first, second = self.stored(text, JUNK), self.stored(text, self.sign(text))
        self.assertEqual(len(self.pending()), 2)
        self.githouse_kept(text, outcome='merged', merge='c' * 40)
        self.assertEqual(merge_orders.status(second)['status'], 'merged')
        self.assertIn('same nonce', merge_orders.status(first)['why'])

    def test_an_expired_order_is_not_handed_on(self):
        posted = self.post(order(expires=str(int(time.time()) + 2)))
        MergeOrder.objects.update(expires=int(time.time()) - 1)
        self.assertEqual(self.pending(), [])
        self.assertEqual(self.status(posted)['status'], 'expired')

    def test_what_isn_t_an_order_is_refused(self):
        for text, why in ((order(expires=str(int(time.time()) - 5)), 'expires'),
                          (order(expires=str(int(time.time()) + 3 * 3600)), 'expires'),
                          (order(commit='HEAD'), 'well formed'),
                          ('merge everything please', 'not a merge order'),
                          (order().replace('by: skyler\n', ''), 'missing: by')):
            with self.subTest(why):
                said = self.post(text, signature=self.sign(text, key='skyler'))  # signed, and still not an order
                self.assertEqual(said.status_code, 400)
                self.assertIn(why, said.json()['error'])
        self.assertEqual(self.post(order(), signature='nonsense').status_code, 400)
        self.assertEqual(self.post(order(), signature=JUNK).status_code, 400)
        self.assertEqual(self.client.post('/api/merge-orders/', 'x', content_type='application/json').status_code, 400)
        self.assertEqual(MergeOrder.objects.count(), 0)

    def test_none_taken_during_az5(self):
        Setting.objects.create(key='scram', value=True, note='AZ5 by justin')
        self.assertEqual(self.post(order()).status_code, 423)

    def test_whose_key(self):
        signers = self.githouse / 'allowed_signers'
        signers.write_text('skyler namespaces="magenta-moods" ssh-rsa AAAAB3Nza skyler@mbp\n')
        with override_settings(MOOD_ALLOWED_SIGNERS=str(signers)):
            found = self.client.get('/api/merge-orders/whose/', {'key': 'ssh-rsa AAAAB3Nza skylergolden@laptop'})
            self.assertEqual(found.json(), {'name': 'skyler'})  # the comment isn't part of the key
            self.assertEqual(self.client.get('/api/merge-orders/whose/', {'key': 'ssh-rsa BBBB'}).status_code, 404)

    def test_githouse_is_handed_twenty_at_a_time_oldest_first(self):
        texts = [order() for _ in range(25)]
        for text in texts:
            self.stored(text)
        self.assertEqual(self.pending(), texts[:20])

    def test_orders_githouse_has_answered_cant_hide_a_waiting_one(self):
        for _ in range(201):  # well-formed, unsigned, each refused once by githouse, unexpired for an hour yet
            text = order()
            self.stored(text)
            seen = hashlib.sha256(f'{text}\0{JUNK}'.encode()).hexdigest()
            (self.githouse / '.merges' / 'mailed' / f'{seen}.json').write_text(json.dumps({'outcome': 'refused'}))
        real = order(by='justin')
        self.stored(real)
        self.assertEqual(self.pending(), [real])
