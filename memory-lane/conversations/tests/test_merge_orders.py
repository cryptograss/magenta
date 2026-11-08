"""Merge orders by mail (Plan, section 8): kept here unread, fetched by githouse, answered from its records."""

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path

from django.core.cache import cache
from django.test import TestCase, override_settings

from conversations.models import MergeOrder, Setting
from conversations.services import merge_orders

SIGNATURE = '-----BEGIN SSH SIGNATURE-----\nU1NIU0lH\n-----END SSH SIGNATURE-----\n'


def order(**change):
    fields = {'repo': 'magenta', 'branch': 're/foo', 'commit': 'a' * 40, 'into': 'main', 'by': 'skyler',
              'nonce': os.urandom(16).hex(), 'expires': str(int(time.time()) + 3600), **change}
    return merge_orders.HEADER + '\n' + ''.join(f'{k}: {fields[k]}\n' for k in merge_orders.FIELDS)


class MergeOrdersTest(TestCase):

    def setUp(self):
        cache.clear()
        self.githouse = Path(tempfile.mkdtemp())
        (self.githouse / '.merges' / 'mailed').mkdir(parents=True)
        patch = override_settings(GITHOUSE_DIR=str(self.githouse))
        patch.enable()
        self.addCleanup(patch.disable)

    def post(self, text, signature=SIGNATURE, **more):
        return self.client.post('/api/merge-orders/', json.dumps({'order': text, 'signature': signature, **more}),
                                content_type='application/json')

    def pending(self):
        return [o['order'] for o in self.client.get('/api/merge-orders/pending/').json()['orders']]

    def status(self, posted):
        return self.client.get(f"/api/merge-orders/{posted.json()['id']}/").json()

    def githouse_kept(self, text, signature=SIGNATURE, **result):
        nonce = merge_orders.parse(text)['nonce']
        (self.githouse / '.merges' / f'{nonce}.json').write_text(
            json.dumps({**result, 'order': text, 'signature': signature}))

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
        seen = hashlib.sha256(f'{text}\0{SIGNATURE}'.encode()).hexdigest()
        (self.githouse / '.merges' / 'mailed' / f'{seen}.json').write_text(
            json.dumps({'outcome': 'refused', 'why': 'not signed by skyler, or skyler may not merge'}))
        self.assertEqual(self.pending(), [])
        self.assertIn('may not merge', self.status(posted)['why'])

    def test_a_forgery_can_t_take_a_real_order_s_place(self):
        text = order()
        forged = self.post(text, signature=SIGNATURE.replace('U1NIU0lH', 'Zm9yZ2Vk'))
        real = self.post(text)
        self.assertEqual(real.status_code, 201)  # the nonce isn't the forgery's to hold
        self.assertEqual(len(self.pending()), 2)
        self.githouse_kept(text, outcome='merged', merge='c' * 40)
        self.assertEqual(self.status(real)['status'], 'merged')
        self.assertEqual(self.status(forged)['status'], 'refused')
        self.assertIn('same nonce', self.status(forged)['why'])

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
                said = self.post(text)
                self.assertEqual(said.status_code, 400)
                self.assertIn(why, said.json()['error'])
        self.assertEqual(self.post(order(), signature='nonsense').status_code, 400)
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
            merge_orders.receive(text, SIGNATURE)
        self.assertEqual(self.pending(), texts[:20])
