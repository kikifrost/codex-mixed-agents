"""GPT-authored checks for the Kimi-authored capability catalog."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins/mixed-agents/bridge"))
import catalog


class CatalogTests(unittest.TestCase):
    def test_sanitized_dedup_and_live_overrides(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text('''[providers.private]
api_key = "SECRET_SENTINEL"
base_url = "https://SECRET_HOST.invalid"
[models.duplicate]
provider = "private"
model = "k3"
[models.preferred]
provider = "managed:kimi-code"
model = "k3"
max_context_size = 123456
display_name = "Live name"
capabilities = ["image_in"]
[models.other]
model = "deepseek-v4"
''')
            entries = catalog.load_kimi_models(path)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["alias"], "preferred")
            self.assertEqual(entries[0]["context"], 123456)
            self.assertEqual(entries[0]["display_name"], "Live name")
            self.assertEqual(entries[0]["capabilities"], ["image_in"])
            serialized = json.dumps(entries)
            for secret in ["SECRET_SENTINEL", "SECRET_HOST", "base_url", "api_key"]:
                self.assertNotIn(secret, serialized)

    def test_actual_native_names_and_unknown_context(self):
        for name, profile in [("gpt-6.1-sol", "sol"), ("gpt-6-astra", "astra"), ("gpt-6-luna", "luna")]:
            entry = catalog.native_model(name)
            self.assertEqual(entry["profile"], profile)
            self.assertIsNone(entry["context"])
        entry = catalog.native_model("gpt-new-unknown")
        self.assertTrue(entry["neutral"])
        ranked = catalog.rank_models([entry], "review", "standard", context_tokens=500000)
        self.assertTrue(ranked[0]["feasible"])
        self.assertIn("unverified", ranked[0]["rationale"])

    def test_peer_fit_and_context_exclusion(self):
        entries = []
        for key, profile in catalog.KIMI_PROFILES.items():
            entries.append(dict(alias=key, model=key, family="kimi", profile=key, context=profile["default_context"]))
        entries += [catalog.native_model(n) for n in ["gpt-6.1-sol", "gpt-6-astra", "gpt-6-luna"]]
        default_deep = catalog.rank_models(entries, "implementation", "complex")
        self.assertEqual(default_deep[0]["model"]["alias"], "k3-256k")
        scores = {r["model"]["alias"]: r["score"] for r in default_deep}
        self.assertEqual(scores["k3-256k"], scores["gpt-6-astra"])
        for complexity, latency, kimi, gpt in [
            ("standard", False, "kimi-for-coding", "gpt-6.1-sol"),
            ("complex", False, "k3-256k", "gpt-6-astra"),
            ("simple", True, "kimi-for-coding-highspeed", "gpt-6-luna")]:
            ranked = catalog.rank_models(entries, "exploration" if latency else "implementation", complexity, 10000, latency)
            scores = {r["model"]["alias"]: r["score"] for r in ranked}
            self.assertEqual(scores[kimi], scores[gpt])
        ranked = catalog.rank_models(entries, "review", "complex", 300000)
        narrow = next(r for r in ranked if r["model"]["alias"] == "k3-256k")
        self.assertFalse(narrow["feasible"])
        self.assertIsNone(narrow["score"])
        excluded = catalog.rank_models(entries, "review", "standard", excluded_family="kimi")
        self.assertTrue(all(r["model"]["family"] == "gpt" for r in excluded))

    def test_missing_or_invalid_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing"
            self.assertEqual(catalog.load_kimi_models(path), [])
            path.write_text("invalid TOML ]")
            self.assertEqual(catalog.load_kimi_models(path), [])


if __name__ == "__main__":
    unittest.main()
