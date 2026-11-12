"""Managed-model diagnostics acceptance tests for OPEN_SOURCE_READINESS_SPEC.md.

Scope: docs/OPEN_SOURCE_READINESS_SPEC.md (managed-model diagnostics).

These tests exercise the real AgentManager plus the MCP ``server.invoke``
public entry point against temporary synthetic state and the fake CLI
subprocess (tests/fake_cli.py). No mocks, no network, no real credentials.
Credential *values* are runtime-generated and never written to state or
tracked files; doctor must report sources only.
"""
import json
import os
import secrets
import sys
import tempfile
import unittest
from pathlib import Path

BRIDGE = Path(__file__).resolve().parents[1] / "plugins" / "mixed-agents" / "bridge"
sys.path.insert(0, str(BRIDGE))
import server  # noqa: E402
from runtime import AgentManager  # noqa: E402

FAKE = Path(__file__).with_name("fake_cli.py")

MANAGED_ENV = "MIXED_AGENTS_DOCTOR_MANAGED"
IMPORTED_ENV = "MIXED_AGENTS_DOCTOR_IMPORTED"

EMPTY_TOML = "# Synthetic installation; intentionally no models table.\n"

IMPORTED_TOML = """# Synthetic imported installation; no real credentials.
[providers."doctorimport"]
api_key_env = "MIXED_AGENTS_DOCTOR_IMPORTED"

[models."doctorimport/k3"]
provider = "doctorimport"
model = "k3"
"""


class DoctorManagedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "kimi_home"
        self.home.mkdir()
        self.config = self.home / "config.toml"
        self.config.write_text(EMPTY_TOML, encoding="utf-8")
        self.mgr = AgentManager(
            state_dir=str(Path(self.tmp.name) / "state"),
            config_path=str(self.config),
            kimi_command=[sys.executable, str(FAKE)],
        )

    def tearDown(self):
        for name in (MANAGED_ENV, IMPORTED_ENV):
            os.environ.pop(name, None)

    def invoke(self, method, arguments):
        return server.invoke(self.mgr, method, arguments)

    def doctor(self):
        return self.invoke("doctor", {})

    @staticmethod
    def statuses(report):
        return {c["name"]: c["status"] for c in report["checks"]}

    @staticmethod
    def credential_sources(report):
        cred = next(c for c in report["checks"] if c["name"] == "credentials")
        return cred, {s["alias"]: s["source"] for s in cred.get("sources", [])}

    def create_managed(self, provider_id="doctorco", alias="doctorco/k3-256k",
                       credential_ref="env:" + MANAGED_ENV):
        self.invoke("create_provider", {
            "provider_id": provider_id,
            "label": "Doctor managed connection",
            "provider_type": "generic",
            "endpoint": "https://api.doctor.example/v1",
            "protocol": "openai",
            "credential_ref": credential_ref,
        })
        self.invoke("create_model", {
            "alias": alias,
            "provider_id": provider_id,
            "upstream_model": "k3-256k",
            "display_name": "Doctor managed K3",
            "context": 262144,
            "capabilities": ["thinking", "tool_use"],
        })
        return alias

    def test_managed_only_empty_toml_reports_model_and_stays_ready(self):
        os.environ[MANAGED_ENV] = secrets.token_urlsafe(16)
        alias = self.create_managed()
        report = self.doctor()
        self.assertTrue(report["ready"], json.dumps(report, indent=1))
        statuses = self.statuses(report)
        # A readable config without a models table is valid once managed
        # resources exist; doctor must not demand an imported models table.
        self.assertEqual(statuses["kimi_config"], "ok")
        models = next(c for c in report["checks"] if c["name"] == "models")
        self.assertIn(alias, models["supported_aliases"])
        cred, sources = self.credential_sources(report)
        self.assertNotEqual(sources.get(alias), "missing")
        # Offline guarantees: no inference, no usage request, no auth refresh.
        self.assertEqual(report["account_access"], "not_probed")
        self.assertEqual(report["native_gpt"], "host_managed_not_probed")

    def test_missing_env_reference_reported_then_set_env_restores_readiness(self):
        os.environ.pop(MANAGED_ENV, None)
        alias = self.create_managed()
        report = self.doctor()
        self.assertFalse(report["ready"])
        cred, sources = self.credential_sources(report)
        self.assertEqual(cred["status"], "error")
        self.assertEqual(sources.get(alias), "missing")

        value = secrets.token_urlsafe(24)
        os.environ[MANAGED_ENV] = value
        report = self.doctor()
        self.assertTrue(report["ready"], json.dumps(report, indent=1))
        _, sources = self.credential_sources(report)
        self.assertEqual(sources.get(alias), "api_key_env")
        # Credential sources are reported symbolically; values never appear.
        self.assertNotIn(value, json.dumps(report))

    def test_existing_reference_checks_live_imported_source_offline(self):
        self.config.write_text(IMPORTED_TOML, encoding="utf-8")
        os.environ.pop(IMPORTED_ENV, None)
        alias = self.create_managed(provider_id="doctorref",
                                    alias="doctorref/k3-256k",
                                    credential_ref="existing:doctorimport")
        report = self.doctor()
        self.assertFalse(report["ready"])
        _, sources = self.credential_sources(report)
        self.assertEqual(sources.get(alias), "missing")

        os.environ[IMPORTED_ENV] = secrets.token_urlsafe(24)
        report = self.doctor()
        self.assertTrue(report["ready"], json.dumps(report, indent=1))
        _, sources = self.credential_sources(report)
        # The managed reference resolves to the imported source offline.
        self.assertEqual(sources.get(alias), "api_key_env")
        self.assertEqual(report["account_access"], "not_probed")

    def test_disabled_managed_model_does_not_establish_readiness(self):
        os.environ[MANAGED_ENV] = secrets.token_urlsafe(16)
        alias = self.create_managed()
        self.assertTrue(self.doctor()["ready"])

        self.invoke("save_model", {"alias": alias, "expected_revision": 1,
                                   "enabled": False})
        report = self.doctor()
        self.assertFalse(report["ready"])
        self.assertEqual(self.statuses(report)["models"], "error")

        self.invoke("save_model", {"alias": alias, "expected_revision": 2,
                                   "enabled": True})
        self.assertTrue(self.doctor()["ready"])

    def test_broken_installation_config_still_actionable_with_managed_models(self):
        os.environ[MANAGED_ENV] = secrets.token_urlsafe(16)
        self.create_managed()
        raw = "this is not ] valid toml {"
        self.config.write_text(raw, encoding="utf-8")
        report = self.doctor()
        self.assertFalse(report["ready"])
        self.assertEqual(self.statuses(report)["kimi_config"], "error")
        self.assertNotIn(raw, json.dumps(report),
                         "raw config content leaked in doctor output")

    def test_empty_installation_without_managed_models_still_errors(self):
        report = self.doctor()
        self.assertFalse(report["ready"])
        statuses = self.statuses(report)
        self.assertIn("error", (statuses["kimi_config"], statuses["models"]))


if __name__ == "__main__":
    unittest.main()
