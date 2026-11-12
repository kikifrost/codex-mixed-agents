"""GLM independent tests for Kimi account policy: cooldown, order, caps. Offline."""
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins/mixed-agents/bridge'))
import runtime
from runtime import AgentManager, BridgeError

FAKE = Path(__file__).with_name('fake_cli.py')
CONFIG = ('[providers.p]\nbase_url="https://api.kimi.com/coding/v1"\napi_key="SYNTHETIC_A"\n'
          '[models."kimi-code/x"]\nmodel="kimi-for-coding"\nprovider="p"\n'
          '[models."shared/w"]\nmodel="kimi-for-coding"\nprovider="p"\n'
          '[providers.q]\nbase_url="https://api.kimi.com/coding/v1"\napi_key="SYNTHETIC_B"\n'
          '[models."aaa-backup/y"]\nmodel="kimi-for-coding"\nprovider="q"\n'
          '[providers.r]\nbase_url="https://api.kimi.com/coding/v1"\napi_key="SYNTHETIC_C"\n'
          '[models."kimi-for-coding/z"]\nmodel="kimi-for-coding"\nprovider="r"\n')


class AccountPolicyTests(unittest.TestCase):
    def setUp(self):
        self.kimi = importlib.import_module('kimi_usage')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = self.root / 'state'
        self.config = self.root / 'config.toml'
        self.config.write_text(CONFIG)
        self.manager = AgentManager(state_dir=str(self.state), config_path=str(self.config),
                                    kimi_command=[sys.executable, str(FAKE)])
        self.by_alias = {}

        def read(state, config, alias=None, **kwargs):
            remaining = self.by_alias.get(alias, 94)
            return {'available': remaining is not None, 'remaining_percent': remaining,
                    'used_percent': None if remaining is None else 100 - remaining,
                    'observed_at': time.time(), 'reset_at': None,
                    'error': None if remaining is not None else 'no_data'}

        self.patches = [patch.object(self.kimi, 'read_usage', side_effect=read),
                        patch('socket.socket.connect', side_effect=AssertionError('offline test'))]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        # Fake starting rows: _launch keeps each slot FD held in this process.
        self.held = []
        self.launch = patch.object(self.manager, '_launch',
                                   side_effect=lambda agent_id, fd: self.held.append(fd))
        self.launch.start()
        self.addCleanup(self.launch.stop)
        self.addCleanup(lambda: [os.close(fd) for fd in self.held])
        self.manager.set_kimi_policy(usage_guard=True, probe_on_unknown=False)

    def spawn(self, model, unit):
        return self.manager.spawn_agent('accounts', unit, 'exploration', '{"text":"ok"}', str(self.root), model)

    def test_default_cooldown_900s_durable_claim_and_boundary(self):
        self.assertEqual(self.kimi.DEFAULT_PROBE_COOLDOWN_MINUTES * 60, 900)
        self.assertEqual(self.manager.model_catalog([])['kimi_policy']['probe_cooldown_minutes'], 15)
        fingerprint = self.kimi.account_fingerprint(self.config, 'kimi-code/x')
        self.assertEqual(fingerprint, self.kimi.account_fingerprint(self.config, 'shared/w'))
        self.by_alias = {'kimi-code/x': None, 'shared/w': None}
        base = 1800000000.0
        with patch('time.time', return_value=base), patch.object(self.manager, '_run_probe') as probe:
            self.assertTrue(self.manager.probe_kimi_usage('kimi-code/x')['probed'])
            probe.assert_called_once()
        for alias in ('kimi-code/x', 'shared/w'):  # restart and same-credential alias share the claim
            fresh = AgentManager(state_dir=str(self.state), config_path=str(self.config),
                                 kimi_command=[sys.executable, str(FAKE)])
            with patch('time.time', return_value=base + 899), patch.object(fresh, '_run_probe') as probe:
                reply = fresh.probe_kimi_usage(alias)
                self.assertFalse(reply['probed'])
                self.assertEqual(reply['reason'], 'probe_cooldown')
                probe.assert_not_called()
        with patch('time.time', return_value=base + 900):
            self.assertTrue(self.kimi.claim_probe(self.state, fingerprint)['claimed'])

    def test_changed_interval_reevaluates_persisted_claims(self):
        fingerprint = self.kimi.account_fingerprint(self.config, 'kimi-code/x')
        base = 1800000000.0
        with patch('time.time', return_value=base):
            self.assertTrue(self.kimi.claim_probe(self.state, fingerprint)['claimed'])
        self.manager.set_kimi_policy(probe_cooldown_minutes=1)
        self.assertEqual(self.manager.model_catalog([])['kimi_policy']['probe_cooldown_minutes'], 1)
        with patch('time.time', return_value=base + 59):
            self.assertFalse(self.kimi.claim_probe(self.state, fingerprint, 1)['claimed'])
        with patch('time.time', return_value=base + 60):
            self.assertTrue(self.kimi.claim_probe(self.state, fingerprint, 1)['claimed'])
        self.manager.set_kimi_policy(probe_cooldown_minutes=15)
        with patch('time.time', return_value=base + 60 + 899):
            self.assertFalse(self.kimi.claim_probe(self.state, fingerprint)['claimed'])
        with patch('time.time', return_value=base + 60 + 900):
            self.assertTrue(self.kimi.claim_probe(self.state, fingerprint)['claimed'])

    def test_invalid_probe_cooldown_rejected_without_writes(self):
        path = self.state / 'config.json'
        before = path.read_bytes()
        for value in (True, 0, -3, 2.5, float('nan')):
            with self.assertRaises(BridgeError):
                self.manager.set_kimi_policy(probe_cooldown_minutes=value)
            self.assertEqual(path.read_bytes(), before)
        self.manager.set_kimi_policy(probe_cooldown_minutes=20)
        self.assertEqual(json.loads(path.read_text())['kimi_policy']['probe_cooldown_minutes'], 20)

    def test_default_order_catalog_rows_and_configurable_order(self):
        view = self.manager.model_catalog([])['kimi_accounts']
        self.assertEqual(view['order'], ['kimi-code', 'kimi-for-coding'])
        self.assertEqual(view['limits'], {})
        rows = {row['prefix']: row for row in view['accounts']}
        self.assertEqual(set(rows['kimi-code']),
                         {'prefix', 'cap', 'running', 'available', 'unlimited', 'configured'})
        self.assertEqual(rows['kimi-code'], {'prefix': 'kimi-code', 'cap': None, 'running': 0,
                                             'available': None, 'unlimited': True, 'configured': True})
        self.assertTrue(all(rows[p]['configured'] for p in ('kimi-for-coding', 'aaa-backup', 'shared')))
        # kimi-code wins even though aaa-backup sorts first alphabetically.
        chosen = self.manager.select_model('order', 'default', 'exploration', [])
        self.assertEqual(chosen['selected']['alias'], 'kimi-code/x')
        self.manager.set_kimi_accounts(['aaa-backup', 'kimi-code', 'shared', 'kimi-for-coding'], {})
        chosen = self.manager.select_model('order', 'custom', 'exploration', [])
        self.assertEqual(chosen['selected']['alias'], 'aaa-backup/y')

    def test_quota_blocked_or_cap_full_primary_falls_back(self):
        self.by_alias = {'kimi-code/x': 5, 'shared/w': 5, 'aaa-backup/y': 94, 'kimi-for-coding/z': 94}
        chosen = self.manager.select_model('fallback', 'quota', 'exploration', [])
        self.assertEqual(chosen['selected']['alias'], 'kimi-for-coding/z')
        self.manager.set_kimi_accounts(['kimi-code', 'kimi-for-coding'], {'kimi-code': 1})
        self.by_alias = {}
        self.spawn('kimi-code/x', 'hold-primary')
        chosen = self.manager.select_model('fallback', 'cap', 'exploration', [])
        self.assertEqual(chosen['selected']['alias'], 'kimi-for-coding/z')
        with self.assertRaises(BridgeError) as caught:
            self.spawn('shared/w', 'same-account')
        self.assertEqual(caught.exception.code, 'account_capacity_exceeded')
        idle = self.spawn('aaa-backup/y', 'other-account')
        self.assertEqual(self.manager.get_agent(idle['agent_id'])['model'], 'aaa-backup/y')

    def test_shared_capacity_full_uses_gpt_and_blocks_cli(self):
        self.manager.set_concurrency(1)
        self.spawn('kimi-code/x', 'occupant')
        chosen = self.manager.select_model('shared', 'full', 'exploration', ['gpt-6.1-sol'])
        self.assertEqual(chosen['selected']['alias'], 'gpt-6.1-sol')
        self.assertTrue(all(candidate['family'] == 'gpt' for candidate in chosen['candidates']))
        with self.assertRaises(BridgeError) as caught:  # idle backup account, shared cap still full
            self.spawn('aaa-backup/y', 'idle-backup')
        self.assertEqual(caught.exception.code, 'capacity_exceeded')
        self.assertEqual(caught.exception.details.get('fallback'), 'gpt')

    def test_explicit_alias_never_rewritten(self):
        agent = self.spawn('aaa-backup/y', 'explicit')
        self.assertEqual(self.manager.get_agent(agent['agent_id'])['model'], 'aaa-backup/y')
        with self.assertRaises(BridgeError) as caught:
            self.spawn('kimi-code/unknown', 'typo')
        # Stage 3 allowlist: an exact alias the saved template does not allow
        # is model_not_allowed; nothing is substituted for it.
        self.assertEqual(caught.exception.code, 'model_not_allowed')

    def test_set_kimi_accounts_validation_and_preservation(self):
        path = self.state / 'config.json'
        self.manager.set_concurrency(4)
        self.manager.set_zai_policy(limit=2)
        data = json.loads(path.read_text())
        data['unrelated'] = {'keep': True}
        path.write_text(json.dumps(data))
        before = path.read_bytes()
        invalid = [(None, {}), ([], None), ('kimi-code', {}), (['kimi-code', 'kimi-code'], {}),
                   ([''], {}), ([' spaced'], {}), (['a/b'], {}), ([], 'unlimited'),
                   ([], {'x': True}), ([], {'x': 1.5}), ([], {'x': -2})]
        for order, limits in invalid:
            with self.assertRaises(BridgeError):
                self.manager.set_kimi_accounts(order, limits)
            self.assertEqual(path.read_bytes(), before)
        reply = self.manager.set_kimi_accounts(['aaa-backup', 'ghost', 'kimi-code'],
                                               {'kimi-code': 2, 'aaa-backup': 'unlimited'})
        self.assertEqual(reply['kimi_accounts']['order'], ['aaa-backup', 'ghost', 'kimi-code'])
        data = json.loads(path.read_text())
        self.assertEqual(data['kimi_concurrency'], 4)
        self.assertEqual(data['zai_policy']['limit'], 2)
        self.assertEqual(data['unrelated'], {'keep': True})
        fresh = AgentManager(state_dir=str(self.state), config_path=str(self.config))
        accounts = fresh.model_catalog([])['kimi_accounts']
        self.assertEqual(accounts['limits'], {'kimi-code': 2, 'aaa-backup': 'unlimited'})
        rows = {row['prefix']: row for row in accounts['accounts']}
        self.assertFalse(rows['ghost']['configured'])
        self.assertEqual((rows['kimi-code']['cap'], rows['kimi-code']['available']), (2, 2))
        self.assertTrue(rows['aaa-backup']['unlimited'])

    def test_zero_account_cap_normalizes_to_unlimited_and_persists(self):
        path = self.state / 'config.json'
        before_keys = set(json.loads(path.read_text()))
        reply = self.manager.set_kimi_accounts(['kimi-code', 'kimi-for-coding'],
                                               {'kimi-code': 0, 'kimi-for-coding': 3})
        # Numeric zero stores as the existing unlimited sentinel.
        self.assertEqual(reply['kimi_accounts']['limits'],
                         {'kimi-code': 'unlimited', 'kimi-for-coding': 3})
        data = json.loads(path.read_text())
        self.assertEqual(data['kimi_accounts']['limits'],
                         {'kimi-code': 'unlimited', 'kimi-for-coding': 3})
        self.assertTrue(set(data).issuperset(before_keys))
        # Restart: a fresh manager resolves the same unlimited account state.
        fresh = AgentManager(state_dir=str(self.state), config_path=str(self.config))
        rows = {row['prefix']: row for row in fresh.model_catalog([])['kimi_accounts']['accounts']}
        self.assertTrue(rows['kimi-code']['unlimited'])
        self.assertIsNone(rows['kimi-code']['cap'])
        self.assertFalse(rows['kimi-for-coding']['unlimited'])
        # A zero-capped account admits multiple concurrent jobs on that account.
        self.manager.set_kimi_accounts(['kimi-code', 'kimi-for-coding'],
                                       {'kimi-code': 0, 'kimi-for-coding': 3})
        self.spawn('kimi-code/x', 'zero-a')
        second = self.spawn('kimi-code/x', 'zero-b')
        self.assertEqual(self.manager.get_agent(second['agent_id'])['model'], 'kimi-code/x')
        # Zero never bypasses the shared CLI cap: fill it, then admission fails
        # with the shared capacity error even for an unlimited account.
        self.manager.set_concurrency(2)
        with self.assertRaises(BridgeError) as caught:
            self.spawn('kimi-code/x', 'zero-c')
        self.assertEqual(caught.exception.code, 'capacity_exceeded')

    def test_same_credential_aliases_share_strictest_cap(self):
        self.manager.set_kimi_accounts(['kimi-code', 'kimi-for-coding'], {'shared': 1})
        rows = {row['prefix']: row for row in self.manager.model_catalog([])['kimi_accounts']['accounts']}
        self.assertEqual(rows['kimi-code']['cap'], 1)
        self.assertEqual(rows['shared'], {'prefix': 'shared', 'cap': 1, 'running': 0,
                                          'available': 1, 'unlimited': False, 'configured': True})
        self.spawn('kimi-code/x', 'primary-running')
        with self.assertRaises(BridgeError) as caught:
            self.spawn('shared/w', 'alias-of-same-credential')
        self.assertEqual(caught.exception.code, 'account_capacity_exceeded')

    def test_probe_blocked_by_capacity_skips_cooldown(self):
        self.manager.set_kimi_accounts(order=['kimi-code', 'kimi-for-coding'], limits={'kimi-code': 1})
        self.spawn('kimi-code/x', 'u1')
        self.by_alias['kimi-code/x'] = None
        with patch.object(self.kimi, 'claim_probe') as claim, patch.object(self.manager, '_run_probe') as run:
            with self.assertRaises(BridgeError) as cm:
                self.manager.probe_kimi_usage(model='kimi-code/x')
            self.assertEqual(cm.exception.code, 'account_capacity_exceeded')
            claim.assert_not_called()
            run.assert_not_called()

    def test_lowered_cap_grandfathers_running_blocks_new(self):
        a1 = self.spawn('kimi-code/x', 'u1')['agent_id']
        a2 = self.spawn('kimi-code/x', 'u2')['agent_id']
        self.manager.set_kimi_accounts(order=['kimi-code', 'kimi-for-coding'], limits={'kimi-code': 1})
        with self.manager._transaction() as db:
            statuses = [db.execute('SELECT status FROM agents WHERE id=?', (a,)).fetchone()[0] for a in (a1, a2)]
        self.assertEqual(statuses, ['starting', 'starting'])
        rows = self.manager.model_catalog(native_models=[])['kimi_accounts']['accounts']
        row = next(r for r in rows if r['prefix'] == 'kimi-code')
        self.assertEqual((row['running'], row['available']), (2, 0))
        with self.assertRaises(BridgeError) as cm:
            self.spawn('kimi-code/x', 'u3')
        self.assertEqual(cm.exception.code, 'account_capacity_exceeded')

    def test_followup_counts_toward_capacity(self):
        old = self.spawn('kimi-code/x', 'u1')['agent_id']
        with self.manager._transaction() as db:
            db.execute('UPDATE agents SET status=?, session_id=? WHERE id=?', ('completed', 'test-session', old))
        os.close(self.held.pop())
        self.spawn('kimi-code/x', 'u2')
        self.manager.set_kimi_accounts(order=['kimi-code', 'kimi-for-coding'], limits={'kimi-code': 1})
        with self.assertRaises(BridgeError) as cm:
            self.manager.followup_task(old, 'next')
        self.assertEqual(cm.exception.code, 'account_capacity_exceeded')
        self.assertEqual(len(self.held), 1)


if __name__ == '__main__':
    unittest.main()
