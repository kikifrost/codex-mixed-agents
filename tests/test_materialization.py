"""GPT-authored independent Stage 3 Kimi adapter acceptance tests.

Derived from TEMPLATE_STAGE3_SPEC.md; synthetic local credentials only.
Scope templates-stage3-20261013, work_unit materialization.
"""
import copy
import importlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins/mixed-agents/bridge'))


class MaterializationTests(unittest.TestCase):
    def setUp(self):
        self.adapter = importlib.import_module('materialization')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / 'installation'
        self.home.mkdir()
        self.config = self.home / 'config.toml'
        self.secret = 'SYNTHETIC' + '_PROCESS_ONLY_CREDENTIAL'
        self.config.write_text(
            '[providers.source]\ntype="openai"\nbase_url="https://api.kimi.com/coding/v1"\n'
            'api_key="' + self.secret + '"\n'
            '[models."source/kimi"]\nmodel="kimi-for-coding"\nprovider="source"\n'
            'max_context_size=262144\n', encoding='utf-8')
        self.state = self.root / 'state'
        self.state.mkdir()
        self.model = {'alias': 'work/kimi', 'provider_id': 'work', 'agent_type': 'kimi-code',
                      'family': 'kimi', 'upstream_model': 'kimi-for-coding',
                      'display_name': 'Work Kimi', 'context': 262144,
                      'capabilities': ['thinking', 'tool_use'], 'enabled': True,
                      'source': 'managed', 'revision': 1}
        self.provider = {'id': 'work', 'label': 'Work', 'provider_type': 'kimi',
                         'endpoint': 'https://api.kimi.com/coding/v1', 'protocol': 'openai',
                         'credential_ref': 'existing:source', 'enabled': True,
                         'source': 'managed', 'revision': 1, 'model_aliases': ['work/kimi']}

    def describe(self, model=None, provider=None):
        return self.adapter.describe(self.config, model or self.model, provider or self.provider)

    def assert_safe_descriptor(self, descriptor):
        encoded = json.dumps(descriptor)
        self.assertNotIn(self.secret, encoded)
        self.assertNotIn(str(self.home), encoded)
        self.assertNotIn('api_key"', encoded)

    def new_files(self):
        return [p for p in self.state.rglob('*') if p.is_file() and not p.is_symlink()]

    def test_descriptor_is_json_safe_without_credential_or_private_path(self):
        self.assert_safe_descriptor(self.describe())

    def test_existing_reference_retains_original_guard_account_alias(self):
        descriptor = self.describe()
        target = self.adapter.guard_target(self.config, descriptor)
        self.assertEqual(Path(target['config_path']), self.config)
        self.assertEqual(target['model'], 'source/kimi')
        self.assertEqual(descriptor['guard_kind'], 'kimi')

    def test_existing_credential_cannot_be_redirected_to_other_origin(self):
        provider = dict(self.provider, endpoint='https://attacker.example/v1', provider_type='generic')
        with self.assertRaises(self.adapter.MaterializationError) as caught:
            self.describe(provider=provider)
        self.assertNotIn(self.secret, str(caught.exception))
        self.assertNotIn('attacker.example', str(caught.exception))

    def test_missing_and_ambiguous_source_fail_before_materialization(self):
        for config in ('[providers.other]\nbase_url="https://api.kimi.com/coding/v1"\n',
                       self.config.read_text() + '\n[providers.other]\nbase_url="https://api.kimi.com/coding/v1"\napi_key_env="OTHER_KEY"\n[models."source/other"]\nmodel="kimi-for-coding"\nprovider="other"\n'):
            with self.subTest(config_shape=len(config)):
                self.config.write_text(config)
                with self.assertRaises(self.adapter.MaterializationError):
                    self.describe()
                self.assertFalse(self.new_files())

    def test_prepare_uses_private_ephemeral_config_and_process_env_only(self):
        original = self.config.read_bytes()
        descriptor = self.describe()
        with self.adapter.prepare(self.config, descriptor, self.state) as launch:
            generated = Path(launch['config_path'])
            self.assertEqual(generated.name, 'config.toml')
            self.assertEqual(launch['model'], self.model['alias'])
            self.assertEqual(Path(launch['env']['KIMI_CODE_HOME']).resolve(), generated.parent.resolve())
            self.assertEqual(stat.S_IMODE(generated.stat().st_mode), 0o600)
            data = tomllib.loads(generated.read_text())
            self.assertEqual(data.get('default_model'), self.model['alias'])
            row = data['models'][self.model['alias']]
            provider = data['providers'][row['provider']]
            self.assertEqual(row['model'], self.model['upstream_model'])
            self.assertEqual(provider['base_url'], self.provider['endpoint'])
            self.assertNotIn('api_key', provider)
            self.assertEqual(launch['env'][provider['api_key_env']], self.secret)
            for file in self.new_files():
                self.assertNotIn(self.secret.encode(), file.read_bytes())
        self.assertFalse(generated.exists())
        self.assertEqual(self.config.read_bytes(), original)

    def test_ephemeral_cleanup_on_child_exception(self):
        with self.assertRaisesRegex(RuntimeError, 'synthetic child error'):
            with self.adapter.prepare(self.config, self.describe(), self.state) as launch:
                generated = Path(launch['config_path'])
                raise RuntimeError('synthetic child error')
        self.assertFalse(generated.exists())
        self.assertFalse(self.new_files())

    def test_env_reference_does_not_copy_credentials_into_descriptor_or_files(self):
        provider = dict(self.provider, credential_ref='env:MIXED_SYNTHETIC_KEY')
        with patch.dict(os.environ, {'MIXED_SYNTHETIC_KEY': self.secret}):
            descriptor = self.describe(provider=provider)
            self.assert_safe_descriptor(descriptor)
            with self.adapter.prepare(self.config, descriptor, self.state) as launch:
                self.assertEqual(launch['env']['MIXED_SYNTHETIC_KEY'], self.secret)
                self.assertNotIn(self.secret, Path(launch['config_path']).read_text())
                self.assertEqual(descriptor['guard_kind'], 'kimi')

    def test_missing_env_fails_safely_and_leaves_no_config(self):
        provider = dict(self.provider, credential_ref='env:NONEXISTENT_STAGE3_KEY')
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(self.adapter.MaterializationError) as caught:
                descriptor = self.describe(provider=provider)
                with self.adapter.prepare(self.config, descriptor, self.state):
                    self.fail('missing credential must not launch')
        self.assertNotIn('NONEXISTENT_STAGE3_KEY', str(caught.exception))
        self.assertFalse(self.new_files())

    def test_source_origin_drift_after_capture_fails_instead_of_redirecting(self):
        descriptor = self.describe()
        self.config.write_text(self.config.read_text().replace('api.kimi.com/coding/v1', 'other.example/v1'))
        with self.assertRaises(self.adapter.MaterializationError):
            with self.adapter.prepare(self.config, descriptor, self.state):
                self.fail('source drift must not launch')
        self.assertFalse(self.new_files())

    def test_same_source_credential_rotation_is_not_serialized(self):
        descriptor = self.describe()
        replacement = 'SYNTHETIC' + '_ROTATED_CREDENTIAL'
        self.config.write_text(self.config.read_text().replace(self.secret, replacement))
        with self.adapter.prepare(self.config, descriptor, self.state) as launch:
            data = tomllib.loads(Path(launch['config_path']).read_text())
            provider = data['providers'][data['models'][self.model['alias']]['provider']]
            self.assertEqual(launch['env'][provider['api_key_env']], replacement)
            self.assertNotIn(replacement, json.dumps(descriptor))
            self.assertNotIn(replacement, Path(launch['config_path']).read_text())

    def test_unrecognized_upstream_and_mismatched_ownership_are_rejected(self):
        for model in (dict(self.model, upstream_model='unknown-upstream'),
                      dict(self.model, provider_id='wrong'), dict(self.model, family='gpt')):
            with self.subTest(model=model['upstream_model']):
                with self.assertRaises(self.adapter.MaterializationError):
                    self.describe(model=model)

    def imported_records(self):
        return (dict(self.model, alias='source/kimi', provider_id='source', source='imported'),
                dict(self.provider, id='source', source='imported', credential_ref='existing:source',
                     model_aliases=['source/kimi']))

    def test_imported_route_uses_original_installation_without_copying_config(self):
        model, provider = self.imported_records()
        descriptor = self.describe(model=model, provider=provider)
        self.assert_safe_descriptor(descriptor)
        with self.adapter.prepare(self.config, descriptor, self.state) as launch:
            self.assertEqual(Path(launch['config_path']), self.config)
            self.assertEqual(launch['model'], model['alias'])
            self.assertFalse(self.new_files())

    def test_imported_upstream_drift_cannot_redirect_a_pinned_turn(self):
        model, provider = self.imported_records()
        descriptor = self.describe(model=model, provider=provider)
        self.config.write_text(self.config.read_text().replace('model="kimi-for-coding"',
                                                               'model="glm-5.3"'))
        with self.assertRaises(self.adapter.MaterializationError):
            with self.adapter.prepare(self.config, descriptor, self.state):
                self.fail('upstream drift must stop a pinned turn')

    def test_existing_oauth_storage_is_reused_without_copying_tokens(self):
        credentials = self.home / 'credentials'
        credentials.mkdir()
        token_file = credentials / 'synthetic-account.json'
        refresh_value = 'SYNTHETIC' + '_REFRESH'
        token_file.write_text(json.dumps({'access_token': self.secret, 'refresh_token': refresh_value}))
        self.config.write_text(self.config.read_text().replace(
            'api_key="' + self.secret + '"',
            'oauth={storage="file", key="oauth/synthetic-account"}'))
        original_token = token_file.read_bytes()
        with self.adapter.prepare(self.config, self.describe(), self.state) as launch:
            home = Path(launch['env']['KIMI_CODE_HOME'])
            for file in self.new_files():
                self.assertNotIn(self.secret.encode(), file.read_bytes())
            # Relative OAuth keys must still resolve to the existing token file.
            self.assertEqual((home / 'credentials' / token_file.name).resolve(), token_file.resolve())
        self.assertEqual(token_file.read_bytes(), original_token)

    def test_existing_prefix_resolves_physical_provider_identity(self):
        self.config.write_text(self.config.read_text().replace('[providers.source]',
                                                               '[providers."managed:actual"]')
                               .replace('provider="source"', 'provider="managed:actual"'))
        descriptor = self.describe()
        target = self.adapter.guard_target(self.config, descriptor)
        self.assertEqual(target['model'], 'source/kimi')
        with self.adapter.prepare(self.config, descriptor, self.state) as launch:
            self.assertEqual(launch['model'], self.model['alias'])

    def test_same_special_origin_endpoint_drift_is_not_silently_accepted(self):
        descriptor = self.describe()
        self.config.write_text(self.config.read_text().replace('api.kimi.com/coding/v1',
                                                               'api.kimi.com/coding'))
        with self.assertRaises(self.adapter.MaterializationError):
            with self.adapter.prepare(self.config, descriptor, self.state):
                self.fail('same-kind endpoint drift must stop a pinned turn')

    def test_unchanged_incomplete_imported_installation_is_passthrough(self):
        self.config.write_text('[models.fake]\nmodel="kimi-for-coding"\nprovider="missing-source"\n')
        model = dict(self.model, alias='fake', provider_id='fake', source='imported')
        provider = dict(self.provider, id='fake', provider_type='generic', endpoint=None,
                        protocol=None, credential_ref=None, source='imported', model_aliases=['fake'])
        with self.adapter.prepare(self.config, self.describe(model, provider), self.state) as launch:
            self.assertEqual(Path(launch['config_path']), self.config)
            self.assertEqual(launch['model'], 'fake')
            self.assertFalse(self.new_files())

    def test_zai_existing_reference_keeps_actual_provider_guard(self):
        self.config.write_text(self.config.read_text().replace('api.kimi.com/coding/v1',
                                                               'api.z.ai/api/coding')
                               .replace('model="kimi-for-coding"', 'model="glm-5.3"'))
        provider = dict(self.provider, provider_type='zai', endpoint='https://api.z.ai/api/coding')
        model = dict(self.model, upstream_model='glm-5.3', family='glm')
        descriptor = self.describe(model, provider)
        self.assertEqual(descriptor['guard_kind'], 'zai')
        target = self.adapter.guard_target(self.config, descriptor)
        import zai
        self.assertIn(target['model'], zai.aliases(target['config_path']))

    def test_parallel_same_route_preparations_do_not_remove_each_other(self):
        descriptor = self.describe()
        with self.adapter.prepare(self.config, descriptor, self.state) as first:
            first_path = Path(first['config_path'])
            with self.adapter.prepare(self.config, copy.deepcopy(descriptor), self.state) as second:
                second_path = Path(second['config_path'])
                self.assertNotEqual(first_path, second_path)
                self.assertTrue(first_path.exists())
                self.assertTrue(second_path.exists())
            self.assertTrue(first_path.exists())
            self.assertFalse(second_path.exists())
        self.assertFalse(first_path.exists())

    def test_generic_existing_credential_cannot_cross_origin(self):
        self.config.write_text(self.config.read_text().replace('api.kimi.com/coding/v1',
                                                               'source.vendor.example/v1'))
        provider = dict(self.provider, provider_type='generic', endpoint='https://other.vendor.example/v1')
        with self.assertRaises(self.adapter.MaterializationError):
            self.describe(provider=provider)

    def test_stale_imported_endpoint_record_does_not_pin_new_live_origin(self):
        model, provider = self.imported_records()
        self.config.write_text(self.config.read_text().replace('api.kimi.com/coding/v1',
                                                               'api.kimi.com/coding'))
        with self.assertRaises(self.adapter.MaterializationError):
            self.describe(model, provider)

    def test_vendor_protocol_imported_route_passes_through_and_detects_drift(self):
        self.config.write_text(self.config.read_text().replace('type="openai"', 'type="kimi"'))
        model, provider = self.imported_records()
        provider['protocol'] = None
        descriptor = self.describe(model, provider)
        with self.adapter.prepare(self.config, descriptor, self.state) as launch:
            self.assertEqual(Path(launch['config_path']), self.config)
        self.config.write_text(self.config.read_text().replace('type="kimi"', 'type="other-vendor"'))
        with self.assertRaises(self.adapter.MaterializationError):
            with self.adapter.prepare(self.config, descriptor, self.state):
                self.fail('vendor protocol drift must stop a pinned turn')

    def test_managed_existing_reference_retains_vendor_transport(self):
        self.config.write_text(self.config.read_text().replace('type="openai"', 'type="kimi"'))
        with self.adapter.prepare(self.config, self.describe(), self.state) as launch:
            data = tomllib.loads(Path(launch['config_path']).read_text())
            row = data['models'][self.model['alias']]
            self.assertEqual(data['providers'][row['provider']]['type'], 'kimi')

    def test_mixed_family_source_chooses_guardable_alias_for_account(self):
        self.config.write_text(self.config.read_text() + '\n[models."source/aaa-glm"]\nmodel="glm-5.3"\nprovider="source"\n')
        target = self.adapter.guard_target(self.config, self.describe())
        import kimi_usage
        self.assertIn(target['model'], kimi_usage.aliases(target['config_path']))
        self.assertEqual(target['model'], 'source/kimi')

    def test_descriptor_does_not_mutate_public_records(self):
        model, provider = copy.deepcopy(self.model), copy.deepcopy(self.provider)
        self.describe()
        self.assertEqual(self.model, model)
        self.assertEqual(self.provider, provider)


if __name__ == '__main__':
    unittest.main()
