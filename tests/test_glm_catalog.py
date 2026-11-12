"""GPT-authored acceptance tests for the Kimi-authored GLM catalog."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins/mixed-agents/bridge'))
import catalog


SUPPORTED = ('glm-4.7', 'glm-5', 'glm-5-turbo', 'glm-5.2',
             'glm-5.2-highspeed', 'glm-5.3', 'glm-5.3-flash',
             'glm-5.3-highspeed')


class GlmCatalogTests(unittest.TestCase):
    def load(self, body, all_aliases=True):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.toml'
            path.write_text(body, encoding='utf-8')
            return catalog.load_kimi_models(path, all_aliases=all_aliases)

    def models(self, names):
        return self.load('\n'.join(
            '[models."dispatch-%d"]\nmodel = %s' % (i, json.dumps(name))
            for i, name in enumerate(names)))

    def test_exact_eight_remote_ids_and_arbitrary_dispatch_aliases(self):
        self.assertEqual(catalog.FAMILIES, ('kimi', 'glm', 'gpt'))
        self.assertEqual(set(catalog.CLI_PROFILES) - set(catalog.KIMI_PROFILES), set(SUPPORTED))
        for prefix in ('', 'zai/', 'managed:other/nested/'):
            with self.subTest(prefix=prefix):
                entries = self.models([prefix + name for name in SUPPORTED])
                self.assertEqual([e['alias'] for e in entries], ['dispatch-%d' % i for i in range(8)])
                self.assertEqual([e['model'] for e in entries], [prefix + name for name in SUPPORTED])
                self.assertEqual([e['profile'] for e in entries], list(SUPPORTED))
                self.assertTrue(all(e['family'] == 'glm' and e['backend'] == 'kimi_cli' for e in entries))
        peers = self.models(['k3']) + [catalog.native_model('gpt-6.1-sol')]
        self.assertEqual([(e['family'], e['backend']) for e in peers],
                         [('kimi', 'kimi_cli'), ('gpt', 'codex_native')])

    def test_alias_never_identifies_family_and_unknown_suffixes_rejected(self):
        body = '[models."glm-5.3"]\nmodel = "deepseek-v4"\n'
        body += '[models."k3"]\nmodel = "vendor/glm-5.3"\n'
        entries = self.load(body)
        self.assertEqual([(e['alias'], e['family']) for e in entries], [('k3', 'glm')])
        self.assertEqual(self.models(['glm-5.3-vision', 'vendor/glm-5.3-flash-extra',
                                     'glm-4.7-flash', 'glm-5.4', 'deepseek-v4',
                                     'vendor/deepseek-v3', 'unrelated-model']), [])

    def test_live_metadata_and_unknown_context(self):
        entries = self.load('''[models.custom]
model = "glm-5.3"
max_context_size = 12345
capabilities = ["tool_use", "image_in"]
display_name = "Configured display"
[models.unknown]
model = "glm-5.2"
''')
        custom, unknown = entries
        self.assertEqual(custom['context'], 12345)
        self.assertEqual(custom['capabilities'], ['image_in', 'tool_use'])
        self.assertEqual(custom['display_name'], 'Configured display')
        self.assertIsNone(unknown['context'])
        ranked = catalog.rank_models(entries, 'review', 'standard', context_tokens=12346)
        by_alias = {r['model']['alias']: r for r in ranked}
        self.assertFalse(by_alias['custom']['feasible'])
        self.assertIsNone(by_alias['custom']['score'])
        self.assertTrue(by_alias['unknown']['feasible'])
        self.assertIn('unverified', by_alias['unknown']['rationale'])

    def test_exact_accounts_secret_exclusion_and_distinct_model_dedup(self):
        body = '''[providers.secret-a]
api_key = "SECRET_A"
base_url = "https://SECRET_HOST_A.invalid"
[providers.secret-b]
api_key = "SECRET_B"
base_url = "https://SECRET_HOST_B.invalid"
[models."account-a/arbitrary"]
model = "vendor/glm-5.3"
provider = "secret-a"
max_context_size = 111
[models."account-b/arbitrary"]
model = "glm-5.3"
provider = "secret-b"
max_context_size = 222
[models."account-c/fast"]
model = "glm-5.3-highspeed"
provider = "secret-a"
'''
        entries = self.load(body)
        self.assertEqual([e['alias'] for e in entries],
                         ['account-a/arbitrary', 'account-b/arbitrary', 'account-c/fast'])
        self.assertEqual([e['context'] for e in entries[:2]], [111, 222])
        self.assertEqual(sum(e['routing_default'] for e in entries), 2)
        routing = self.load(body, False)
        self.assertEqual({e['profile'] for e in routing}, {'glm-5.3', 'glm-5.3-highspeed'})
        self.assertEqual(len(routing), 2)
        self.assertTrue(all(e['alias'] in {a['alias'] for a in entries} for e in routing))
        serialized = json.dumps(entries + routing)
        for secret in ('SECRET_A', 'SECRET_B', 'SECRET_HOST', 'secret-a', 'secret-b', 'api_key', 'base_url'):
            self.assertNotIn(secret, serialized)

    def test_malformed_glm_metadata_is_skipped_without_leaking(self):
        for metadata in ('max_context_size = true', 'max_context_size = 0',
                         'max_context_size = "SECRET_VALUE"',
                         'capabilities = "SECRET_VALUE"',
                         'capabilities = ["tool_use", 123]'):
            with self.subTest(metadata=metadata):
                self.assertEqual(self.load('[models.bad]\nmodel = "glm-5.3"\n' + metadata), [])

    def test_no_suffix_infers_image_or_video_capability(self):
        for entry in self.models(SUPPORTED):
            with self.subTest(model=entry['model']):
                self.assertFalse(any('image' in c or 'video' in c for c in entry['capabilities']))

    def test_family_exclusion_and_task_fit_equal_peers(self):
        cases = [('glm-5.2', 'kimi-for-coding', 'gpt-6.1-sol', 'implementation', 'standard', False),
                 ('glm-5.3', 'k3-256k', 'gpt-6-astra', 'review', 'complex', False),
                 ('glm-5.3-flash', 'kimi-for-coding-highspeed', 'gpt-6-luna', 'exploration', 'simple', True)]
        for glm, kimi, gpt, role, complexity, latency in cases:
            entries = self.models([glm, kimi]) + [catalog.native_model(gpt)]
            with self.subTest(glm=glm):
                ranked = catalog.rank_models(entries, role, complexity, 10000, latency)
                self.assertEqual(len({r['score'] for r in ranked}), 1)
                self.assertEqual({r['model']['family'] for r in ranked}, set(catalog.FAMILIES))
                for family in catalog.FAMILIES:
                    remaining = catalog.rank_models(entries, role, complexity, excluded_family=family)
                    self.assertEqual({r['model']['family'] for r in remaining}, set(catalog.FAMILIES) - {family})
        entries = self.models(['glm-5.3', 'glm-5.3-flash', 'glm-5.2'])
        self.assertEqual(catalog.rank_models(entries, 'review', 'complex')[0]['model']['profile'], 'glm-5.3')
        self.assertEqual(catalog.rank_models(entries, 'exploration', 'simple', latency_sensitive=True)[0]['model']['profile'], 'glm-5.3-flash')
        self.assertEqual(catalog.rank_models(entries, 'implementation', 'standard')[0]['model']['profile'], 'glm-5.2')


if __name__ == '__main__':
    unittest.main()
