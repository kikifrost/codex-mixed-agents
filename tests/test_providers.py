"""Independent GLM resource-store tests, derived from docs/TEMPLATE_STAGE2_SPEC.md.

Scope templates-stage2-20261013, work_unit resource-backend-final.

Offline stdlib checks against AgentManager on a synthetic installation TOML
and temporary state: import projection/classification, symbolic credential
references, strict validation, CAS semantics, installation-drift revision
increments, persistence, import drift, disabled/orphaned references and
quota-free reads. No network, no credentials.
"""
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "mixed-agents" / "bridge"))
import kimi_usage  # noqa: E402
import zai  # noqa: E402
from runtime import AgentManager, BridgeError  # noqa: E402

# Runtime-assembled synthetic sentinels; the tree scan never sees key literals.
FAKE_SECRET = "s" + "k-" + "synthprov" + "0" * 18
RAW_TOKEN = "s" + "k-" + "aliasraw" + "0" * 14
TOKEN_ALIAS = f"tokalias/{RAW_TOKEN}"
LEAK_MARKERS = ("token=leaky123", "SECRETMARKER")

SYNTHETIC_TOML = f"""# Synthetic resource-test installation; no real credentials.
[models."kimi-code/k3-256k"]
model = "k3-256k"
provider = "managed:kimi-code"

[models."zai-synth/glm-5.3"]
model = "glm-5.3"
provider = "zai-managed"

[models."neutral-co/k2.7"]
model = "k2.7"
provider = "neutral-co"

[models."kimi-lookalike/glm-5.3"]
model = "glm-5.3"
provider = "second-zai"

[models."bare/glm-5.2"]
model = "glm-5.2"
provider = "incomplete-co"

[models."literal-co/kimi-for-coding"]
model = "kimi-for-coding"
provider = "literal-co"

[models."query-co/glm-5.2"]
model = "glm-5.2"
provider = "query-co"

[models."userinfo-co/glm-5.2"]
model = "glm-5.2"
provider = "userinfo-co"

[models."dup-co/glm-5.3"]
model = "glm-5.3"
provider = "dup-first"

[models."dup-co/k2.7"]
model = "k2.7"
provider = "dup-second"

[models."{TOKEN_ALIAS}"]
model = "k3-256k"
provider = "neutral-co"

[providers."managed:kimi-code"]
base_url = "https://api.kimi.com/coding/v1"
api_key_env = "MIXED_AGENTS_SYNTH_PROV_KIMI"

[providers."zai-managed"]
base_url = "https://api.z.ai/api/coding"
api_key_env = "MIXED_AGENTS_SYNTH_PROV_ZAI"

[providers."neutral-co"]
base_url = "https://api.neutral.example/v1"
api_key_env = "MIXED_AGENTS_SYNTH_PROV_NEUTRAL"

[providers."second-zai"]
base_url = "https://api.z.ai/api/coding"
api_key_env = "MIXED_AGENTS_SYNTH_PROV_ZAI2"

[providers."incomplete-co"]
api_key_env = "MIXED_AGENTS_SYNTH_PROV_BARE"

[providers."literal-co"]
base_url = "https://api.literal.example/v1"
api_key = "{FAKE_SECRET}"

[providers."query-co"]
base_url = "https://api.query.example/v1?token=leaky123"
api_key_env = "MIXED_AGENTS_SYNTH_PROV_QUERY"

[providers."userinfo-co"]
base_url = "https://inurl-cred-marker@api.userinfo.example/v1"
api_key_env = "MIXED_AGENTS_SYNTH_PROV_USERINFO"

[providers."dup-first"]
base_url = "https://api.dup.example/v1"
api_key_env = "MIXED_AGENTS_SYNTH_PROV_DUP1"

[providers."dup-second"]
base_url = "https://api.kimi.com/coding/v1"
api_key_env = "MIXED_AGENTS_SYNTH_PROV_DUP2"
"""

SYMBOLIC = ("env:", "existing:")


class ResourceTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = self.root / "config.toml"
        self.config.write_text(SYNTHETIC_TOML, encoding="utf-8")
        self.state = self.root / "state"
        self.manager = self.open_manager()

    def tearDown(self):
        self.tmp.cleanup()

    def open_manager(self, config=None):
        return AgentManager(state_dir=self.state, config_path=config or self.config)

    def config_without(self, *markers):
        """SYNTHETIC_TOML with whole blank-line-separated blocks removed."""
        blocks = SYNTHETIC_TOML.split("\n\n")
        return "\n\n".join(block for block in blocks
                           if not any(marker in block for marker in markers)) + "\n"

    def config_with(self, *replacements):
        text = SYNTHETIC_TOML
        for old, new in replacements:
            self.assertIn(old, text)
            text = text.replace(old, new)
        return text

    def bridge_error(self, callable_, *args, code="invalid_argument", **kwargs):
        with self.assertRaises(BridgeError) as ctx:
            callable_(*args, **kwargs)
        if code is not None:
            self.assertEqual(ctx.exception.code, code)
        return ctx.exception

    def resources(self, manager=None):
        return (manager or self.manager).configuration_resources()

    def provider(self, provider_id, manager=None):
        return self.resources(manager)["providers"][provider_id]

    def revision(self, provider_id, manager=None):
        return self.provider(provider_id, manager)["revision"]

    def create_work_provider(self, provider_id="work-co", credential_ref="env:MIXED_AGENTS_WORK_KEY",
                             endpoint="https://api.work.example/v1", provider_type="generic",
                             protocol="openai", label="工作连接"):
        return self.manager.create_provider(provider_id=provider_id, label=label,
                                            provider_type=provider_type, endpoint=endpoint,
                                            protocol=protocol, credential_ref=credential_ref)

    def template_payload(self, aliases):
        groups = {}
        for alias in aliases:
            groups.setdefault(alias.split("/")[0], []).append(alias)
        return {"schema_version": 1,
                "agent_configs": [{"agent_type": "kimi-code", "enabled": True,
                                   "providers": [{"provider_id": pid, "models": models}
                                                 for pid, models in groups.items()]}],
                "limits": {"max_parallel_per_project": 3},
                "selection": {"prefer_provider_discount_on_equal_fit": False}}

    def saved_template(self, aliases, name="资源测试"):
        template = self.manager.create_template(name=name, source_template_id="default")
        return self.manager.save_template(template_id=template["id"], payload=self.template_payload(aliases),
                                          expected_revision=1)

    def registered_project(self, name="demo"):
        return self.manager.register_project(name=name, root=str(self.root), host_id="local")


class ImportProjectionTests(ResourceTestCase):
    def test_connection_type_comes_from_origin_not_alias_or_family(self):
        providers = self.resources()["providers"]
        self.assertEqual(providers["kimi-code"]["provider_type"], "kimi")
        self.assertEqual(providers["zai-synth"]["provider_type"], "zai")
        self.assertEqual(providers["kimi-lookalike"]["provider_type"], "zai")
        self.assertEqual(providers["neutral-co"]["provider_type"], "generic")
        # A Kimi-family model behind a neutral origin stays a generic connection.
        self.assertEqual(self.resources()["models"]["neutral-co/k2.7"]["family"], "kimi")
        self.assertEqual(providers["neutral-co"]["provider_type"], "generic")

    def test_imported_public_metadata_shape(self):
        for provider_id in ("kimi-code", "zai-synth", "neutral-co"):
            record = self.provider(provider_id)
            for key in ("id", "label", "provider_type", "endpoint", "protocol",
                        "credential_ref", "enabled", "revision", "source", "model_aliases"):
                self.assertIn(key, record)
            self.assertEqual(record["source"], "imported")
            self.assertTrue(record["enabled"])
        for alias in ("kimi-code/k3-256k", "zai-synth/glm-5.3"):
            model = self.resources()["models"][alias]
            for key in ("alias", "provider_id", "agent_type", "family", "context",
                        "capabilities", "upstream_model", "display_name", "enabled",
                        "revision", "source"):
                self.assertIn(key, model)

    def test_credential_references_stay_symbolic_and_secret_values_never_leak(self):
        text = json.dumps(self.resources(), ensure_ascii=False)
        self.assertNotIn(FAKE_SECRET, text)
        for marker in LEAK_MARKERS:
            self.assertNotIn(marker, text)
        self.assertEqual(self.provider("kimi-code")["credential_ref"],
                         "env:MIXED_AGENTS_SYNTH_PROV_KIMI")
        literal = self.provider("literal-co")["credential_ref"]
        self.assertTrue(literal is None or str(literal).startswith(SYMBOLIC),
                        f"literal api_key must not become a public credential_ref: {literal!r}")
        query = self.provider("query-co")
        self.assertNotIn("token=leaky123", json.dumps(query))

    def test_incomplete_import_invents_no_endpoint_or_credential(self):
        bare = self.provider("bare")
        self.assertEqual(bare["provider_type"], "generic")
        self.assertFalse(bare.get("endpoint"), "incomplete import must not invent an endpoint")
        # A genuinely configured symbolic env reference is real metadata, not an
        # invention; anything else (values, paths) must not appear.
        reference = bare.get("credential_ref")
        self.assertTrue(reference is None or str(reference).startswith(SYMBOLIC),
                        f"unexpected credential material: {reference!r}")

    def test_generic_import_preserves_endpoint_protocol_and_nondefault_port(self):
        with tempfile.TemporaryDirectory() as fresh:
            root = Path(fresh)
            config = root / "config.toml"
            config.write_text(
                '[models."port-co/glm-5.3"]\nmodel = "glm-5.3"\nprovider = "port-entry"\n\n'
                '[providers.port-entry]\nbase_url = "https://api.port.example:8443/v1"\n'
                'type = "anthropic"\napi_key_env = "MIXED_AGENTS_SYNTH_PROV_PORT"\n',
                encoding="utf-8")
            manager = AgentManager(state_dir=root / "state", config_path=config)
            record = manager.configuration_resources()["providers"]["port-co"]
            self.assertEqual(record["provider_type"], "generic")
            self.assertEqual(record["endpoint"], "https://api.port.example:8443/v1")
            self.assertEqual(record["protocol"], "anthropic")
            self.assertEqual(record["credential_ref"], "env:MIXED_AGENTS_SYNTH_PROV_PORT")

    def test_unsafe_imported_endpoints_never_exposed_or_stored(self):
        resources = self.resources()
        for provider_id in ("query-co", "userinfo-co"):
            record = resources["providers"][provider_id]
            self.assertFalse(record.get("endpoint"),
                             f"{provider_id} must not expose its unsafe endpoint")
        text = json.dumps(resources)
        for marker in ("token=leaky123", "inurl-cred-marker"):
            self.assertNotIn(marker, text)
        database = sqlite3.connect(self.state / "agents.sqlite3")
        try:
            for table in ("provider_records", "model_records"):
                blob = json.dumps([tuple(map(str, row)) for row in
                                   database.execute(f"SELECT * FROM {table}")])
                self.assertNotIn("token=leaky123", blob, table)
                self.assertNotIn("inurl-cred-marker", blob, table)
        finally:
            database.close()


class ProviderValidationTests(ResourceTestCase):
    def test_create_provider_managed_defaults_and_duplicate_failure(self):
        created = self.create_work_provider()
        self.assertEqual((created["revision"], created["source"], created["enabled"]), (1, "managed", True))
        self.assertEqual(self.provider("work-co")["label"], "工作连接")
        self.bridge_error(self.create_work_provider)  # duplicate of a managed id
        self.bridge_error(self.create_work_provider, provider_id="kimi-code")  # collides with imported id
        self.assertEqual(self.revision("work-co"), 1)

    def test_provider_id_rules(self):
        for bad in ("", "a/b", "api_key", FAKE_SECRET, 7, " lead", "trail "):
            with self.subTest(bad=bad):
                self.bridge_error(self.manager.create_provider, provider_id=bad, label="x",
                                  provider_type="generic", endpoint="https://api.work.example/v1",
                                  protocol="openai", credential_ref="env:WORK_KEY")

    def test_managed_endpoint_rules(self):
        for bad in ("http://api.work.example/v1", "https://user:pw@api.work.example/v1",
                    "https://api.work.example/v1?x=1", "https://api.work.example/v1#frag",
                    "not a url", "https://", "//api.work.example/v1", ""):
            with self.subTest(bad=bad):
                self.bridge_error(self.create_work_provider, endpoint=bad)

    def test_special_kinds_must_match_endpoint_origin(self):
        self.bridge_error(self.create_work_provider, provider_type="kimi",
                          endpoint="https://api.z.ai/api/coding")
        self.bridge_error(self.create_work_provider, provider_type="zai",
                          endpoint="https://api.kimi.com/coding/v1")
        self.bridge_error(self.create_work_provider, provider_type="generic",
                          endpoint="https://api.kimi.com/coding/v1")
        self.bridge_error(self.create_work_provider, provider_type="generic",
                          endpoint="https://api.z.ai/api/coding")
        kimi = self.create_work_provider(provider_id="managed-kimi", provider_type="kimi",
                                         endpoint="https://api.kimi.com/coding/v1")
        self.assertEqual(kimi["provider_type"], "kimi")
        zai_provider = self.create_work_provider(provider_id="managed-zai", provider_type="zai",
                                                 endpoint="https://api.z.ai/api/coding")
        self.assertEqual(zai_provider["provider_type"], "zai")

    def test_protocol_whitelist(self):
        self.bridge_error(self.create_work_provider, protocol="grpc")
        created = self.create_work_provider(protocol="anthropic")
        self.assertEqual(created["protocol"], "anthropic")

    def test_credential_reference_rules(self):
        plaintext_ref = "s" + "k-" + "plaintext-is-not-a-reference"  # assembled; no tree literal
        self.bridge_error(self.create_work_provider, credential_ref=plaintext_ref)
        self.bridge_error(self.create_work_provider, credential_ref="existing:unknown-connection")
        self.create_work_provider(provider_id="ref-ok", credential_ref="existing:kimi-code")
        self.create_work_provider(provider_id="managed-a", credential_ref="env:MIXED_AGENTS_A")
        # References to another managed connection are refused (no shared managed chains).
        self.bridge_error(self.create_work_provider, provider_id="managed-b",
                          credential_ref="existing:managed-a")

    def test_ambiguous_same_prefix_connections_reject_existing_reference(self):
        # Two distinct actual CLI connections behind one alias prefix do not
        # identify a single credential source.
        record = self.provider("dup-co")
        self.assertEqual(record["provider_type"], "generic")
        self.assertFalse(record.get("endpoint"))
        self.assertFalse(record.get("credential_ref"))
        self.bridge_error(self.create_work_provider, provider_id="ref-dup",
                          credential_ref="existing:dup-co")

    def test_source_collision_fails_without_substitution(self):
        before = json.dumps(self.provider("kimi-code"), sort_keys=True)
        self.bridge_error(self.create_work_provider, provider_id="kimi-code",
                          endpoint="https://evil.example/v1",
                          credential_ref="env:ATTACKER_REF")
        self.assertEqual(json.dumps(self.provider("kimi-code"), sort_keys=True), before)
        model_before = json.dumps(self.resources()["models"]["kimi-code/k3-256k"], sort_keys=True)
        with self.assertRaises(BridgeError):
            self.manager.create_model(alias="kimi-code/k3-256k", provider_id="kimi-code",
                                      upstream_model="glm-5.3", display_name="替换",
                                      context=1000, capabilities=[])
        self.assertEqual(json.dumps(self.resources()["models"]["kimi-code/k3-256k"], sort_keys=True),
                         model_before)

    def test_errors_never_echo_caller_content(self):
        error = self.bridge_error(self.create_work_provider,
                                  endpoint=f"https://api.work.example/v1/{'SECRETMARKER'}",
                                  credential_ref=f"env:X_{FAKE_SECRET}")
        text = json.dumps(error.as_dict(), ensure_ascii=False)
        self.assertNotIn("SECRETMARKER", text)
        self.assertNotIn(FAKE_SECRET, text)


class ProviderCasTests(ResourceTestCase):
    def test_managed_save_is_cas_and_rejects_non_bool_enabled(self):
        self.create_work_provider()
        saved = self.manager.save_provider(provider_id="work-co", expected_revision=1,
                                           label="改名", enabled=False)
        self.assertEqual((saved["revision"], saved["label"], saved["enabled"]), (2, "改名", False))
        conflict = self.bridge_error(self.manager.save_provider, provider_id="work-co",
                                     expected_revision=1, label="过期", code="revision_conflict")
        self.assertEqual(conflict.code, "revision_conflict")
        self.assertEqual(self.provider("work-co")["label"], "改名")
        self.bridge_error(self.manager.save_provider, provider_id="work-co",
                          expected_revision=2, enabled=1)  # bool is not an int
        self.assertEqual(self.revision("work-co"), 2)

    def test_imported_installation_fields_reject_edits(self):
        revision = self.revision("kimi-code")
        self.bridge_error(self.manager.save_provider, provider_id="kimi-code",
                          expected_revision=revision, endpoint="https://evil.example/v1")
        self.bridge_error(self.manager.save_provider, provider_id="kimi-code",
                          expected_revision=revision, protocol="anthropic")
        self.bridge_error(self.manager.save_provider, provider_id="kimi-code",
                          expected_revision=revision, credential_ref="env:ATTACK")
        self.assertEqual(self.revision("kimi-code"), revision)
        saved = self.manager.save_provider(provider_id="kimi-code", expected_revision=revision,
                                           label="导入改名", enabled=False)
        self.assertEqual((saved["label"], saved["enabled"]), ("导入改名", False))

    def test_concurrent_cas_has_exactly_one_winner(self):
        self.create_work_provider()
        barrier = threading.Barrier(2)
        outcomes = []

        def writer(label):
            barrier.wait()
            try:
                outcomes.append(("ok", self.manager.save_provider(
                    provider_id="work-co", expected_revision=1, label=label)))
            except BridgeError as exc:
                outcomes.append((exc.code, label))

        threads = [threading.Thread(target=writer, args=(f"写入-{index}",)) for index in range(2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(timeout=10)
        winners = [outcome for outcome in outcomes if outcome[0] == "ok"]
        losers = [outcome for outcome in outcomes if outcome[0] == "revision_conflict"]
        self.assertEqual((len(winners), len(losers)), (1, 1))
        self.assertEqual(self.revision("work-co"), 2)
        self.assertEqual(self.provider("work-co")["label"], winners[0][1]["label"])


class ModelTests(ResourceTestCase):
    def test_create_model_contract(self):
        self.create_work_provider()
        created = self.manager.create_model(alias="work-co/k3-copy", provider_id="work-co",
                                            upstream_model="k3-256k", display_name="K3 副本",
                                            context=262144, capabilities=["thinking", "tool_use"])
        self.assertEqual((created["revision"], created["source"]), (1, "managed"))
        self.assertEqual(self.resources()["models"]["work-co/k3-copy"]["display_name"], "K3 副本")
        for upstream in ("deepseek-chat", "kimi-k3-1", ""):
            with self.subTest(upstream=upstream):
                self.bridge_error(self.manager.create_model, alias="work-co/bad",
                                  provider_id="work-co", upstream_model=upstream,
                                  display_name="x", context=1000, capabilities=[])
        for alias in ("k3copy", "kimi-code/foreign", "work-co/", "", 7):
            with self.subTest(alias=alias):
                self.bridge_error(self.manager.create_model, alias=alias, provider_id="work-co",
                                  upstream_model="k3-256k", display_name="x",
                                  context=1000, capabilities=[])
        # Creating over an existing imported alias is a source collision; the
        # spec pins the failure, not the exact error code spelling.
        with self.assertRaises(BridgeError):
            self.manager.create_model(alias="kimi-code/k3-256k", provider_id="kimi-code",
                                      upstream_model="k3-256k", display_name="x",
                                      context=1000, capabilities=[])
        self.assertNotIn("work-co/bad", self.resources()["models"])
        self.bridge_error(self.manager.create_model, alias="missing-co/x", provider_id="missing-co",
                          upstream_model="k3-256k", display_name="x",
                          context=1000, capabilities=[], code="not_found")  # unknown provider

    def test_create_model_strict_fields(self):
        self.create_work_provider()
        base = dict(alias="work-co/strict", provider_id="work-co", upstream_model="glm-5.3",
                    display_name="x")
        for context in (0, -5, True, 2_000_001, 2.5, "1000"):
            with self.subTest(context=context):
                self.bridge_error(self.manager.create_model, context=context,
                                  capabilities=[], **base)
        for capabilities in (["nope"], ["tool_use", "tool_use"], [7], "tool_use", 7):
            with self.subTest(capabilities=capabilities):
                self.bridge_error(self.manager.create_model, context=1000,
                                  capabilities=capabilities, **base)
        created = self.manager.create_model(context=2000000, capabilities=["always_thinking"], **base)
        self.assertEqual(created["context"], 2000000)

    def test_save_model_cas_and_editable_fields(self):
        self.create_work_provider()
        self.manager.create_model(alias="work-co/k3-copy", provider_id="work-co",
                                  upstream_model="k3-256k", display_name="K3",
                                  context=262144, capabilities=["tool_use"])
        saved = self.manager.save_model(alias="work-co/k3-copy", expected_revision=1,
                                        display_name="K3 改", enabled=False, context=131072,
                                        capabilities=["thinking"])
        self.assertEqual((saved["revision"], saved["display_name"], saved["enabled"],
                          saved["context"], saved["capabilities"]),
                         (2, "K3 改", False, 131072, ["thinking"]))
        self.bridge_error(self.manager.save_model, alias="work-co/k3-copy", expected_revision=1,
                          display_name="过期", code="revision_conflict")
        self.bridge_error(self.manager.save_model, alias="work-co/k3-copy", expected_revision=2,
                          enabled=1)  # bool is not an int
        self.bridge_error(self.manager.save_model, alias="missing/x", expected_revision=1,
                          display_name="x", code="not_found")

    def test_new_models_are_not_granted_to_saved_templates(self):
        before = self.manager.list_templates()["templates"]
        self.create_work_provider()
        self.manager.create_model(alias="work-co/k3-copy", provider_id="work-co",
                                  upstream_model="k3-256k", display_name="K3",
                                  context=262144, capabilities=["tool_use"])
        after = self.manager.list_templates()["templates"]
        self.assertEqual(json.dumps(before), json.dumps(after))
        self.assertNotIn("work-co/k3-copy", json.dumps(after))


class DriftAndDisabledTests(ResourceTestCase):
    def test_disabling_retains_references_and_reports_unavailable(self):
        template = self.saved_template(["kimi-code/k3-256k"])
        project = self.registered_project()
        self.manager.bind_project_template(project_id=project["id"], template_id=template["id"],
                                           expected_binding_revision=0)
        revision = self.resources()["models"]["kimi-code/k3-256k"]["revision"]
        self.manager.save_model(alias="kimi-code/k3-256k", expected_revision=revision, enabled=False)
        model = self.resources()["models"]["kimi-code/k3-256k"]
        self.assertFalse(model["enabled"])
        stored = self.manager.get_template(template["id"])
        self.assertIn("kimi-code/k3-256k", json.dumps(stored["payload"]))
        resolution = self.manager.resolve_project_config(project_id=project["id"])
        unavailable = [item["alias"] for item in resolution["unavailable"]]
        self.assertIn("kimi-code/k3-256k", unavailable)
        self.assertFalse(resolution["ready"])
        self.assertNotIn("kimi-code/k3-256k", [item["alias"] for item in resolution["candidates"]])
        # Repeated reads never re-enable or regrant the disabled resource.
        self.assertFalse(self.resources()["models"]["kimi-code/k3-256k"]["enabled"])

    def test_import_drift_makes_alias_unavailable_without_resurrection(self):
        template = self.saved_template(["zai-synth/glm-5.3"])
        project = self.registered_project()
        self.manager.bind_project_template(project_id=project["id"], template_id=template["id"],
                                           expected_binding_revision=0)
        drifted = self.root / "drifted.toml"
        drifted.write_text(self.config_without("zai-synth", "zai-managed"), encoding="utf-8")
        manager = self.open_manager(drifted)
        self.assertNotIn("zai-synth/glm-5.3", manager.configuration_resources()["models"])
        stored = manager.get_template(template["id"])
        self.assertIn("zai-synth/glm-5.3", json.dumps(stored["payload"]))
        resolution = manager.resolve_project_config(project_id=project["id"])
        self.assertIn("zai-synth/glm-5.3",
                      [item["alias"] for item in resolution["unavailable"]])
        self.assertFalse(resolution["ready"])
        # Restoring the installation makes the live alias observable again.
        self.assertIn("zai-synth/glm-5.3", self.resources()["models"])

    def test_installation_drift_increments_revision_and_preserves_edits(self):
        provider_revision = self.revision("neutral-co")
        self.manager.save_provider(provider_id="neutral-co", expected_revision=provider_revision,
                                   label="用户标签")
        model_revision = self.resources()["models"]["kimi-code/k3-256k"]["revision"]
        self.manager.save_model(alias="kimi-code/k3-256k", expected_revision=model_revision,
                                display_name="用户模型名")
        drifted = self.root / "drifted-revision.toml"
        drifted.write_text(self.config_with(
            ("https://api.neutral.example/v1", "https://api.z.ai/api/coding"),
            ("MIXED_AGENTS_SYNTH_PROV_NEUTRAL", "MIXED_AGENTS_SYNTH_PROV_NEUT2"),
            ('model = "k3-256k"', 'model = "k2.7"')), encoding="utf-8")
        manager = self.open_manager(drifted)
        provider = self.provider("neutral-co", manager)
        self.assertEqual(provider["provider_type"], "zai")  # origin drift is observable
        self.assertEqual(provider["credential_ref"], "env:MIXED_AGENTS_SYNTH_PROV_NEUT2")
        self.assertEqual(provider["revision"], provider_revision + 2)  # drift increments
        self.assertEqual(provider["label"], "用户标签")  # user edits preserved
        model = manager.configuration_resources()["models"]["kimi-code/k3-256k"]
        self.assertEqual(model["upstream_model"], "k2.7")  # upstream drift applied
        self.assertEqual(model["revision"], model_revision + 2)
        self.assertEqual(model["display_name"], "用户模型名")  # model metadata preserved
        # Stale editor saves fail CAS after the drift increment.
        self.bridge_error(manager.save_provider, provider_id="neutral-co",
                          expected_revision=provider_revision + 1, label="过期",
                          code="revision_conflict")
        self.bridge_error(manager.save_model, alias="kimi-code/k3-256k",
                          expected_revision=model_revision + 1, display_name="过期",
                          code="revision_conflict")

    def test_stale_existing_reference_reports_unavailable(self):
        self.create_work_provider(provider_id="ref-holder", label="引用连接",
                                  credential_ref="existing:zai-synth")
        self.assertEqual(self.provider("ref-holder")["credential_ref"], "existing:zai-synth")
        drifted = self.root / "drifted-ref.toml"
        drifted.write_text(self.config_without("zai-synth", "zai-managed"), encoding="utf-8")
        manager = self.open_manager(drifted)
        record = self.provider("ref-holder", manager)
        # Saved configuration is retained for correction, but reported unavailable.
        self.assertEqual(record["credential_ref"], "existing:zai-synth")
        self.assertEqual(record["label"], "引用连接")
        self.assertEqual(record["endpoint"], "https://api.work.example/v1")
        self.assertFalse(record.get("available", True))
        self.assertTrue(record.get("unavailable_reason"))

    def test_managed_model_orphan_under_missing_imported_provider(self):
        created = self.manager.create_model(alias="kimi-code/managed-extra", provider_id="kimi-code",
                                            upstream_model="k3-256k", display_name="托管模型",
                                            context=262144, capabilities=["tool_use"])
        self.assertEqual(created["source"], "managed")
        drifted = self.root / "drifted-orphan.toml"
        drifted.write_text(self.config_without("kimi-code"), encoding="utf-8")
        manager = self.open_manager(drifted)
        models = manager.configuration_resources()["models"]
        self.assertNotIn("kimi-code/managed-extra", models)
        self.assertNotIn("kimi-code/managed-extra", json.dumps(models))
        # Restoring the imported provider makes the retained managed row usable again.
        restored = self.open_manager()
        self.assertEqual(restored.configuration_resources()["models"]["kimi-code/managed-extra"]["display_name"],
                         "托管模型")

    def test_managed_prefix_collision_with_import_fails_safe(self):
        self.create_work_provider(provider_id="clash-co", label="托管先行")
        drifted = self.root / "drifted-clash.toml"
        drifted.write_text(SYNTHETIC_TOML + (
            '\n[models."clash-co/glm-5.3"]\nmodel = "glm-5.3"\nprovider = "clash-entry"\n\n'
            '[providers."clash-entry"]\nbase_url = "https://api.clash.example/v1/CLASHMARKER"\n'
            'api_key_env = "MIXED_AGENTS_SYNTH_CLASH"\n'), encoding="utf-8")
        manager = self.open_manager(drifted)
        error = self.bridge_error(manager.configuration_resources, code="source_conflict")
        payload = json.dumps(error.as_dict(), ensure_ascii=False)
        self.assertNotIn("config_path", payload)
        self.assertNotIn("CLASHMARKER", payload)

    def test_managed_resources_survive_restart_with_revisions(self):
        self.create_work_provider()
        self.manager.create_model(alias="work-co/k3-copy", provider_id="work-co",
                                  upstream_model="k3-256k", display_name="K3",
                                  context=262144, capabilities=["tool_use"])
        self.manager.save_provider(provider_id="work-co", expected_revision=1, label="重启后")
        reopened = self.open_manager()
        provider = self.provider("work-co", reopened)
        model = reopened.configuration_resources()["models"]["work-co/k3-copy"]
        self.assertEqual((provider["source"], provider["revision"], provider["label"]),
                         ("managed", 2, "重启后"))
        self.assertEqual((model["source"], model["revision"]), ("managed", 1))


class BootstrapAndSecretTests(ResourceTestCase):
    def test_crud_works_before_any_configuration_read(self):
        with tempfile.TemporaryDirectory() as fresh:
            root = Path(fresh)
            config = root / "config.toml"
            config.write_text(SYNTHETIC_TOML, encoding="utf-8")
            manager = AgentManager(state_dir=root / "state", config_path=config)
            manager.create_provider(provider_id="first-co", label="先创建",
                                    provider_type="generic",
                                    endpoint="https://api.first.example/v1",
                                    protocol="openai",
                                    credential_ref="env:MIXED_AGENTS_FIRST")
            manager.create_model(alias="first-co/glm-5.3", provider_id="first-co",
                                 upstream_model="glm-5.3", display_name="先模型",
                                 context=1000000, capabilities=[])
            manager.save_provider(provider_id="first-co", expected_revision=1, enabled=False)
            resources = manager.configuration_resources()  # first explicit read
            self.assertEqual(resources["providers"]["first-co"]["revision"], 2)
            self.assertFalse(resources["providers"]["first-co"]["enabled"])
            self.assertEqual(resources["models"]["first-co/glm-5.3"]["display_name"], "先模型")

    def test_managed_model_under_imported_provider_never_bootstrap_grants(self):
        # Ordering A: the default template is already initialized.
        before = self.manager.list_templates()["templates"]
        self.manager.create_model(alias="kimi-code/boot-extra", provider_id="kimi-code",
                                  upstream_model="k3-256k", display_name="Bootstrap",
                                  context=262144, capabilities=[])
        after = self.manager.list_templates()["templates"]
        self.assertEqual(json.dumps(before), json.dumps(after))
        # Ordering B: the managed model exists before the first template read.
        with tempfile.TemporaryDirectory() as fresh:
            root = Path(fresh)
            config = root / "config.toml"
            config.write_text(SYNTHETIC_TOML, encoding="utf-8")
            manager = AgentManager(state_dir=root / "state", config_path=config)
            manager.create_model(alias="kimi-code/boot-fresh", provider_id="kimi-code",
                                 upstream_model="k3-256k", display_name="Bootstrap2",
                                 context=262144, capabilities=[])
            default = next(t for t in manager.list_templates()["templates"] if t["is_default"])
            payload = json.dumps(default["payload"])
            self.assertNotIn("kimi-code/boot-fresh", payload)
            self.assertIn("kimi-code/k3-256k", payload)  # imported seeding still applies

    def test_raw_token_and_secret_identities_are_excluded_everywhere(self):
        resources = self.resources()
        self.assertNotIn(TOKEN_ALIAS, resources["models"])
        text = json.dumps(resources)
        for banned in (RAW_TOKEN, FAKE_SECRET):
            self.assertNotIn(banned, text)
        # Configured secrets and raw-token identities never reach the resource DB.
        database = sqlite3.connect(self.state / "agents.sqlite3")
        try:
            for table in ("provider_records", "model_records"):
                blob = json.dumps([tuple(map(str, row)) for row in
                                   database.execute(f"SELECT * FROM {table}")])
                self.assertNotIn(FAKE_SECRET, blob, table)
                self.assertNotIn(RAW_TOKEN, blob, table)
        finally:
            database.close()


class PolicyScopeTests(ResourceTestCase):
    def test_kimi_settings_report_shared_scope_without_usage(self):
        settings = self.manager.get_provider_settings(provider_id="kimi-code")
        self.assertEqual(settings["provider_id"], "kimi-code")
        self.assertEqual(settings["handler"], "kimi")
        self.assertIn("policy_scope", settings)
        self.assertIn("kimi_policy", settings)
        self.assertIn("kimi_accounts", settings)
        self.assertNotIn("zai_policy", settings)
        self.assertNotIn("cost_policy", settings)

    def test_zai_settings_report_zai_and_cost_scope(self):
        settings = self.manager.get_provider_settings(provider_id="zai-synth")
        self.assertEqual(settings["handler"], "zai")
        self.assertIn("zai_policy", settings)
        self.assertIn("cost_policy", settings)
        self.assertNotIn("kimi_policy", settings)
        self.assertNotIn("kimi_accounts", settings)

    def test_generic_settings_have_no_special_policy(self):
        self.create_work_provider()
        settings = self.manager.get_provider_settings(provider_id="work-co")
        self.assertEqual(settings["handler"], "generic")
        for absent in ("kimi_policy", "kimi_accounts", "zai_policy", "cost_policy"):
            self.assertNotIn(absent, settings)

    def test_unknown_provider_settings_fail_closed(self):
        self.bridge_error(self.manager.get_provider_settings, provider_id="missing-co",
                          code="not_found")

    def test_resource_reads_never_touch_quota_or_usage(self):
        def forbidden(*args, **kwargs):
            raise AssertionError("quota read during a configuration-only operation")

        with mock.patch.object(kimi_usage, "read_usage", forbidden), \
                mock.patch.object(zai, "read_usage", forbidden):
            self.resources()
            self.manager.get_provider_settings(provider_id="kimi-code")
            self.create_work_provider()
            settings = self.manager.get_provider_settings(provider_id="work-co")
        self.assertNotIn("usage", settings)


if __name__ == "__main__":
    unittest.main()
