"""GPT-authored regressions for the completed Kimi-authored model catalog."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins/mixed-agents/bridge'))
import catalog


class CatalogReleaseTests(unittest.TestCase):
    def load(self, body, all_aliases=True):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'config.toml'
            config.write_text(body)
            return catalog.load_kimi_models(config, all_aliases=all_aliases)

    def test_provider_qualified_models_and_distinct_highspeed(self):
        names = ['vendor/kimi-k2.7', 'vendor/kimi-k2.8-preview',
                 'kimi-for-coding-highspeed', 'kimi-for-coding',
                 'vendor/kimi-k3', 'vendor/kimi-k3-256k']
        body = '\n'.join('[models.alias%d]\nmodel = %s\n' % (i, json.dumps(n))
                         for i, n in enumerate(names))
        entries = self.load(body)
        self.assertEqual([e['profile'] for e in entries],
                         ['k2.7', 'k2.8-preview', 'kimi-for-coding-highspeed',
                          'kimi-for-coding', 'k3', 'k3-256k'])
        self.assertEqual([e['model'] for e in entries], names)
        self.assertNotEqual(entries[0]['display_name'], entries[2]['display_name'])
        self.assertNotIn('faster generation', entries[0]['notes'].lower())
        self.assertNotEqual(entries[0]['notes'], entries[2]['notes'])
        self.assertTrue(all(e['family'] == 'kimi' for e in entries))

    def test_unknown_suffixes_and_non_kimi_are_not_reinterpreted(self):
        body = '\n'.join('[models.alias%d]\nmodel = %s\n' % (i, json.dumps(n))
                         for i, n in enumerate(['vendor/kimi-k3-1', 'k3-1',
                                                 'kimi-k2.9', 'glm-5.4',
                                                 'kimi-for-coding-ultra']))
        self.assertEqual(self.load(body), [])

    def test_metadata_and_exact_account_aliases_survive_routing_dedup(self):
        body = '''[providers.private]
api_key = "SECRET_SENTINEL"
base_url = "https://PRIVATE_HOST.invalid"
[models."account-a/k2.7"]
model = "vendor/kimi-k2.7"
provider = "private"
max_context_size = 123456
capabilities = ["tool_use", "image_in"]
[models."account-b/k2.7"]
model = "kimi-k2.7"
provider = "managed:kimi-code"
'''
        entries = self.load(body)
        self.assertEqual(len(entries), 2)
        first, second = entries
        self.assertEqual(first['alias'], 'account-a/k2.7')
        self.assertEqual(first['context'], 123456)
        self.assertEqual(first['capabilities'], ['image_in', 'tool_use'])
        self.assertFalse(first['routing_default'])
        self.assertTrue(second['routing_default'])
        self.assertEqual(self.load(body, False)[0]['alias'], 'account-b/k2.7')
        serialized = json.dumps(entries)
        for private in ['SECRET_SENTINEL', 'PRIVATE_HOST', 'api_key', 'base_url']:
            self.assertNotIn(private, serialized)

    def test_malformed_context_and_capabilities_are_skipped(self):
        for metadata in ['max_context_size = true', 'max_context_size = false',
                         'max_context_size = 0', 'max_context_size = "PRIVATE_VALUE"',
                         'capabilities = "PRIVATE_VALUE"',
                         'capabilities = {secret = "PRIVATE_VALUE"}',
                         'capabilities = ["tool_use", 123]',
                         'capabilities = ["tool_use", {secret = "PRIVATE_VALUE"}]']:
            with self.subTest(metadata=metadata):
                self.assertEqual(self.load('[models.bad]\nmodel = "k2.7"\n' + metadata), [])


if __name__ == '__main__':
    unittest.main()
