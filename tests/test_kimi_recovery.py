"""GPT-authored acceptance tests for docs/KIMI_RECOVERY_SPEC.md (offline)."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins/mixed-agents/bridge'))
import kimi_usage
import server
from runtime import AgentManager, BridgeError

PRIMARY = 'kimi-code/standard'
SIBLING = 'shared/standard'
BACKUP = 'kimi-for-coding/standard'
NATIVE = 'gpt-6.1-sol'


class KimiRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = self.root / 'state'
        self.config = self.root / 'config.toml'
        self.config.write_text('''[providers.primary]
base_url = "https://api.kimi.com/coding/v1"
api_key = "SYNTHETIC_A"
[providers.backup]
base_url = "https://api.kimi.com/coding/v1"
api_key = "SYNTHETIC_B"
[models."kimi-code/standard"]
model = "kimi-for-coding"
provider = "primary"
max_context_size = 262144
[models."shared/standard"]
model = "kimi-for-coding"
provider = "primary"
max_context_size = 262144
[models."kimi-for-coding/standard"]
model = "kimi-for-coding"
provider = "backup"
max_context_size = 262144
''')
        self.manager = AgentManager(state_dir=str(self.state), config_path=str(self.config))
        self.remaining = {PRIMARY: None, SIBLING: None, BACKUP: 90}
        self.reads = []
        self.usage_mock = self.start_patch(patch.object(kimi_usage, 'read_usage', side_effect=self.read_usage))
        self.start_patch(patch('socket.socket.connect', side_effect=AssertionError('offline test')))
        self.probe = self.start_patch(patch.object(self.manager, '_run_probe'))
        self.manager.set_kimi_policy(usage_guard=True, probe_on_unknown=True)

    def start_patch(self, patcher):
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def read_usage(self, state, config, alias=None, **kwargs):
        self.reads.append(alias)
        # Aggregate account reading is deliberately unknown: it must not hide
        # the healthy backup or be substituted for an exact account reading.
        remaining = self.remaining.get(alias)
        return {'available': remaining is not None, 'remaining_percent': remaining,
                'used_percent': None if remaining is None else 100 - remaining,
                'reset_at': None, 'observed_at': time.time(),
                'error': 'credential_expired' if remaining is None else None}

    def select(self, **kwargs):
        args = dict(scope='recovery-tests', work_unit='selection', role='exploration',
                    native_models=[NATIVE], cwd=str(self.root))
        args.update(kwargs)
        return self.manager.select_model(**args)

    def recovery(self, result):
        actions = result['kimi_recovery']
        for action in actions:
            self.assertEqual(action['next_action'], {
                'tool': 'probe_kimi_usage', 'arguments': {'model': action['model']}})
            self.assertEqual(action['usage_error'], 'credential_expired')
        return actions

    def assert_no_recovery(self, **kwargs):
        try:
            result = self.select(**kwargs)
        except BridgeError as error:
            self.assertIn(error.code, ('no_eligible_model', 'capacity_exceeded',
                                       'project_capacity_exceeded'))
            result = error.details
        self.assertEqual(result.get('kimi_recovery', []), [])

    def test_expired_token_yields_exact_deduplicated_action_and_healthy_account(self):
        for result in (self.manager.model_catalog([NATIVE]), self.select()):
            actions = self.recovery(result)
            self.assertEqual(len(actions), 1, 'Same-account aliases share recovery')
            self.assertEqual(actions[0]['model'], PRIMARY, 'Prefer configured account order')
            serialized = json.dumps(actions)
            for private in ('SYNTHETIC_A', 'SYNTHETIC_B', str(self.root),
                            kimi_usage.account_fingerprint(self.config, PRIMARY)):
                self.assertNotIn(private, serialized)
        selected = self.select()
        self.assertNotIn(selected['selected']['alias'], (PRIMARY, SIBLING))
        self.assertIn(BACKUP, [entry['alias'] for entry in selected['candidates']])
        self.assertIn(PRIMARY, self.reads)
        self.assertIn(BACKUP, self.reads)
        self.probe.assert_not_called()
        self.assertFalse((self.state / 'kimi-probe-allowance.json').exists())

    def test_separate_unknown_accounts_have_separate_actions(self):
        self.remaining[BACKUP] = None
        actions = self.recovery(self.select())
        self.assertEqual({a['model'] for a in actions}, {PRIMARY, BACKUP})

    def test_no_runnable_models_error_preserves_recovery_instead_of_silent_exclusion(self):
        self.remaining[BACKUP] = None
        with self.assertRaises(BridgeError) as caught:
            self.select(native_models=[])
        self.assertEqual(caught.exception.code, 'no_eligible_model')
        self.assertEqual(len(self.recovery(caught.exception.details)), 2)
        self.probe.assert_not_called()

    def test_selection_reason_signals_recovery_before_fallback(self):
        self.assertIn('preflight', self.select()['reason'].lower())

    def test_mcp_serializes_recovery_on_success_and_error(self):
        arguments = dict(scope='transport', work_unit='selection', role='exploration',
                         native_models=[NATIVE], cwd=str(self.root))
        for no_native in (False, True):
            if no_native:
                arguments['native_models'] = []
                self.remaining[BACKUP] = None
            reply = server.handle(self.manager, {'jsonrpc': '2.0', 'id': 1,
                                  'method': 'tools/call', 'params': {
                                      'name': 'select_model', 'arguments': arguments}})['result']
            self.assertEqual(reply['isError'], no_native)
            data = reply['structuredContent']
            self.assertEqual(json.loads(reply['content'][0]['text']), data)
            self.assertTrue(self.recovery(data))
        self.probe.assert_not_called()

    def test_unrecognized_usage_error_is_reduced_to_safe_code(self):
        original = self.read_usage
        def untrusted(*args, **kwargs):
            reading = original(*args, **kwargs)
            if not reading['available']:
                reading['error'] = 'SYNTHETIC_A raw upstream message ' + str(self.root)
            return reading
        self.usage_mock.side_effect = untrusted
        actions = self.select()['kimi_recovery']
        self.assertEqual(actions[0]['usage_error'], 'unknown_error')
        self.assertNotIn('SYNTHETIC_A', json.dumps(actions))
        self.assertNotIn(str(self.root), json.dumps(actions))

    def test_recovery_action_then_rerank_restores_primary_without_claiming_entitlement(self):
        actions = self.recovery(self.select())
        def refresh(*args):
            self.remaining[PRIMARY] = self.remaining[SIBLING] = 98
        self.probe.side_effect = refresh
        outcome = self.manager.probe_kimi_usage(**actions[0]['next_action']['arguments'])
        self.assertTrue(outcome['probed'])
        self.assertEqual(outcome['usage']['remaining_percent'], 98)
        reranked = self.select()
        self.assertEqual(reranked['kimi_recovery'], [])
        self.assertIn(PRIMARY, [c['alias'] for c in reranked['candidates']])
        self.probe.assert_called_once()

    def test_cooldown_durable_across_aliases_and_catalog_does_not_spend_allowance(self):
        self.recovery(self.select())
        self.manager.model_catalog([NATIVE])
        first = self.manager.probe_kimi_usage(PRIMARY)
        self.assertTrue(first['probed'])
        restarted = AgentManager(state_dir=str(self.state), config_path=str(self.config))
        with patch.object(restarted, '_run_probe') as probe:
            repeated = restarted.probe_kimi_usage(SIBLING)
            self.assertFalse(repeated['probed'])
            self.assertEqual(repeated['reason'], 'probe_cooldown')
            probe.assert_not_called()
        self.assertFalse(first['usage']['available'], 'A successful probe alone is not quota proof')
        self.probe.assert_called_once()

    def test_known_reserve_never_suggests_probe(self):
        self.remaining[PRIMARY] = self.remaining[SIBLING] = 5
        self.assertEqual(self.manager.model_catalog([NATIVE])['kimi_recovery'], [])
        self.assertEqual(self.select()['kimi_recovery'], [])
        self.probe.assert_not_called()

    def test_disabled_bootstrap_has_no_recovery_instruction(self):
        self.manager.set_kimi_policy(probe_on_unknown=False)
        self.assertEqual(self.select()['kimi_recovery'], [])
        self.assertEqual(self.manager.model_catalog([NATIVE])['kimi_recovery'], [])

    def test_disabled_guard_never_reads_usage_or_suggests_recovery(self):
        self.manager.set_kimi_policy(usage_guard=False)
        self.reads.clear()
        self.assertEqual(self.select()['kimi_recovery'], [])
        self.assertEqual(self.manager.model_catalog([NATIVE])['kimi_recovery'], [])
        self.assertEqual(self.reads, [])

    def test_unidentifiable_account_has_no_unexecutable_probe_action(self):
        # The explicit probe rejects accounts it cannot identify; guidance
        # must not tell callers to execute a tool that is already impossible.
        with patch.object(kimi_usage, 'account_fingerprint', return_value=None):
            self.assertEqual(self.manager.model_catalog([NATIVE])['kimi_recovery'], [])
            self.assert_no_recovery()

    def test_family_and_avoid_family_constraints_do_not_offer_probe(self):
        self.assert_no_recovery(avoid_family='kimi')
        self.manager.record_authorship('recovery-tests', 'selection', 'implementation', 'kimi', 'test fixture')
        self.assert_no_recovery(role='tests')

    def test_insufficient_context_does_not_offer_probe(self):
        self.assert_no_recovery(context_tokens=300000)

    def hold_slot(self, alias):
        with self.manager._transaction() as db:
            slot, fd = self.manager._acquire_account(db, self.config, alias)
        self.addCleanup(os.close, fd)
        return slot

    def test_shared_full_does_not_offer_probe_or_consume_allowance(self):
        self.manager.set_concurrency(1)
        self.hold_slot(BACKUP)
        self.assert_no_recovery()
        self.assert_no_recovery(native_models=[])
        self.assertFalse((self.state / 'kimi-probe-allowance.json').exists())

    def test_account_full_does_not_offer_probe_via_same_account_alias(self):
        self.manager.set_kimi_accounts(['kimi-code', 'kimi-for-coding'], {'kimi-code': 1})
        self.hold_slot(PRIMARY)
        self.assert_no_recovery()

    def bind_template(self, aliases, limit=1):
        project = self.manager.register_project('Recovery fixture', str(self.root))
        template = self.manager.create_template('Recovery fixture')
        payload = copy.deepcopy(template['payload'])
        groups = []
        for alias in aliases:
            groups.append({'provider_id': alias.split('/', 1)[0], 'models': [alias]})
        payload['agent_configs'] = [{'agent_type': 'kimi-code', 'enabled': True, 'providers': groups}]
        payload['limits']['max_parallel_per_project'] = limit
        self.manager.save_template(template['id'], payload, expected_revision=template['revision'])
        self.manager.bind_project_template(project['id'], template['id'], expected_binding_revision=0)
        return project['id']

    def test_saved_template_allowlist_filters_recovery_actions(self):
        self.bind_template([BACKUP])
        self.assert_no_recovery()
        self.probe.assert_not_called()

    def test_managed_only_template_never_probes_denied_imported_source_alias(self):
        self.manager.create_provider('managed', 'Managed', 'kimi',
                                     'https://api.kimi.com/coding/v1', 'openai',
                                     'existing:kimi-code')
        self.manager.create_model('managed/standard', 'managed', 'kimi-for-coding',
                                  'Managed', 262144, ['thinking', 'tool_use'])
        self.bind_template(['managed/standard'])
        # This template permits only the managed alias. Its account source
        # alias PRIMARY is not an allowed model for a separate probe launch.
        self.assert_no_recovery()
        self.probe.assert_not_called()

    def test_project_full_does_not_offer_probe(self):
        project_id = self.bind_template([PRIMARY, SIBLING, BACKUP])
        slot = self.hold_slot(BACKUP)
        with self.manager._transaction() as db:
            db.execute("INSERT INTO agents (id,scope,unit,role,family,model,cwd,status,slot,project_id) "
                       "VALUES ('fixture','s','u','exploration','kimi',?,?,'running',?,?)",
                       (BACKUP, str(self.root), slot, project_id))
        self.assert_no_recovery()


if __name__ == '__main__':
    unittest.main()
