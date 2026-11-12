"""Independent GPT acceptance tests for GLM implementation of ZAI_GUARD_SPEC."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

BRIDGE = Path(__file__).resolve().parents[1] / 'plugins/mixed-agents/bridge'
sys.path.insert(0, str(BRIDGE))
from runtime import AgentManager, BridgeError
import server

class ZaiPolicyTests(unittest.TestCase):
    def setUp(self):
        self.network = patch('socket.socket.connect', side_effect=AssertionError('Offline test forbids network'))
        self.network.start()
        self.addCleanup(self.network.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = self.root / 'kimi.toml'
        self.config.write_text('[providers.account]\nbase_url="https://api.z.ai/api/coding/paas/v4"\napi_key="SECRET_SENTINEL"\n[models.renamed]\nmodel="glm-5.3"\nprovider="account"\n')
        self.manager = AgentManager(state_dir=str(self.root / 'state'), config_path=str(self.config))
    def tearDown(self):
        self.tmp.cleanup()
    def test_portable_defaults_are_independent(self):
        catalog = self.manager.model_catalog([])
        self.assertEqual(catalog['kimi_cap'], 5)
        self.assertIn('zai_policy', catalog)
        self.assertEqual(catalog['zai_policy']['cap'], 3)
        self.assertFalse(catalog['zai_policy']['usage_guard'])
        self.assertEqual(catalog['zai_policy']['stop_remaining_percent'], 10)
    def test_policy_updates_preserve_shared_and_unrelated_config(self):
        self.manager.set_concurrency(5)
        path = self.root / 'state/config.json'
        data = json.loads(path.read_text())
        data['unrelated'] = {'nested': [1, 2]}
        path.write_text(json.dumps(data))
        self.manager.set_zai_policy(limit=2, usage_guard=True, stop_remaining_percent=12)
        data = json.loads(path.read_text())
        self.assertEqual(data['kimi_concurrency'], 5)
        self.assertEqual(data['unrelated'], {'nested': [1, 2]})
        policy = self.manager.model_catalog([])['zai_policy']
        self.assertEqual(policy['cap'], 2)
        self.assertTrue(policy['usage_guard'])
        self.assertEqual(policy['stop_remaining_percent'], 12)
    def test_invalid_policy_is_rejected_atomically(self):
        self.manager.set_zai_policy(limit=3)
        path = self.root / 'state/config.json'
        before = path.read_bytes()
        for arguments in ({'limit': -1}, {'limit': True}, {'limit': 1.5}, {'limit': 'bad'}, {'usage_guard': 'true'}, {'stop_remaining_percent': 5}, {'stop_remaining_percent': 100}, {'stop_remaining_percent': float('nan')}, {'stop_remaining_percent': float('inf')}):
            with self.subTest(arguments=arguments):
                with self.assertRaises(BridgeError) as caught:
                    self.manager.set_zai_policy(**arguments)
                self.assertEqual(caught.exception.code, 'invalid_argument')
                self.assertEqual(path.read_bytes(), before)
    def test_mcp_policy_and_readonly_usage_discovery(self):
        reply = server.handle(self.manager, {'jsonrpc':'2.0','id':1,'method':'tools/list'})
        tools = {t['name']:t for t in reply['result']['tools']}
        self.assertIn('set_zai_policy', tools)
        self.assertIn('provider_usage', tools)
        self.assertTrue(tools['provider_usage']['annotations']['readOnlyHint'])
        self.assertTrue(tools['provider_usage']['annotations']['openWorldHint'])
        self.assertTrue(tools['model_catalog']['annotations']['openWorldHint'])
        self.assertTrue(tools['select_model']['annotations']['openWorldHint'])
        schema=tools['set_zai_policy']['inputSchema']
        self.assertEqual(schema['required'], [])
        props=schema['properties']
        self.assertIn('boolean', props['usage_guard']['type'])
        self.assertIn('number', props['stop_remaining_percent']['type'])
        self.assertEqual(props['stop_remaining_percent']['exclusiveMinimum'],5)
        self.assertEqual(props['stop_remaining_percent']['exclusiveMaximum'],100)
        self.assertIn('integer', props['limit']['type'])
        self.assertIn('string', props['limit']['type'])
        usage_schema = tools['provider_usage']['inputSchema']
        self.assertEqual(usage_schema['required'], [])
        self.assertEqual(usage_schema['properties']['provider']['default'], 'zai')
        self.assertIn('model', usage_schema['properties'])
        with patch('zai.read_usage', return_value={'available': False, 'error': 'no_data'}) as read:
            usage_reply = server.handle(self.manager, {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
                                                     'params': {'name': 'provider_usage', 'arguments': {}}})
            self.assertFalse(usage_reply['result']['isError'])
            read.assert_called_once()
        reply = server.handle(self.manager, {'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'set_zai_policy','arguments':{'limit':2}}})
        self.assertFalse(reply['result']['isError'])
        self.assertEqual(self.manager.model_catalog([])['zai_policy']['cap'],2)
    def test_fractional_stop_threshold_is_allowed(self):
        self.manager.set_zai_policy(stop_remaining_percent=10.5)
        self.assertEqual(self.manager.model_catalog([])['zai_policy']['stop_remaining_percent'],10.5)
    def test_offline_doctor_does_not_probe_enabled_guard(self):
        self.manager.set_zai_policy(usage_guard=True)
        with patch('socket.socket.connect',side_effect=AssertionError('doctor must stay offline')) as network:
            self.manager.doctor()
            network.assert_not_called()
    def test_unlimited_persists_to_new_manager(self):
        self.manager.set_zai_policy(limit='unlimited')
        fresh = AgentManager(state_dir=str(self.root / 'state'), config_path=str(self.config))
        self.assertIsNone(fresh.model_catalog([])['zai_policy']['cap'])
    def test_zero_limit_normalizes_to_unlimited_and_preserves_rest_of_policy(self):
        self.manager.set_zai_policy(limit=2, usage_guard=True, stop_remaining_percent=12)
        path = self.root / 'state/config.json'
        before = set(json.loads(path.read_text()))
        reply = self.manager.set_zai_policy(limit=0)
        policy = reply['zai_policy']
        # Numeric zero stores as the existing unlimited sentinel; quota guard
        # settings are untouched (zero never disables quota guards).
        self.assertIsNone(policy['cap'])
        self.assertTrue(policy['usage_guard'])
        self.assertEqual(policy['stop_remaining_percent'], 12)
        data = json.loads(path.read_text())
        self.assertEqual(data['zai_policy']['limit'], 'unlimited')
        self.assertTrue(data['zai_policy']['usage_guard'])
        self.assertEqual(data['zai_policy']['stop_remaining_percent'], 12)
        self.assertTrue(set(data).issuperset(before))
        # Persisted safely across restart with unrelated keys preserved.
        data['unrelated'] = {'keep': True}
        path.write_text(json.dumps(data))
        fresh = AgentManager(state_dir=str(self.root / 'state'), config_path=str(self.config))
        fresh_policy = fresh.model_catalog([])['zai_policy']
        self.assertIsNone(fresh_policy['cap'])
        self.assertEqual(fresh_policy['stop_remaining_percent'], 12)
        self.assertEqual(json.loads(path.read_text())['unrelated'], {'keep': True})
    def test_zero_limit_via_direct_module_normalization(self):
        import zai
        self.assertEqual(zai.normalize_policy({'limit': 0})['limit'], 'unlimited')
        self.assertEqual(zai.normalize_policy({'limit': 5})['limit'], 5)
        for bad in (-1, True, 1.5, 'bad', None):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    zai.normalize_policy({'limit': bad})

if __name__ == '__main__':
    unittest.main()
