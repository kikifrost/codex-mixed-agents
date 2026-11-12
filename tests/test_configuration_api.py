"""Kimi-family API tests for AgentManager configuration wrappers and MCP schema."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins/mixed-agents/bridge"))
import server
from runtime import AgentManager, BridgeError


CONFIG_TOML = """
[providers.kimi]
api_key = "test-fake-SECRET_SENTINEL"
base_url = "https://api.kimi.com/coding/v1"

[models."kimi-code/k3-256k"]
model = "k3-256k"
provider = "managed:kimi-code"

[models."kimi-code/kimi-for-coding"]
model = "kimi-for-coding"
provider = "managed:kimi-code"

[models."zai-coding-plan/glm-5.3"]
model = "glm-5.3"
provider = "zai-coding-plan"
"""


BARE_ALIAS_TOML = """
[providers.kimi]
api_key = "test-fake-SECRET_SENTINEL"
base_url = "https://api.kimi.com/coding/v1"

[models.k3]
model = "k3-256k"
provider = "managed:kimi-code"

[models.coding]
model = "kimi-for-coding"
provider = "managed:kimi-code"
"""


class AgentManagerConfigurationResourcesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config_path = Path(self.tmp.name) / "config.toml"
        self.config_path.write_text(CONFIG_TOML)
        self.state_dir = Path(self.tmp.name) / "state"
        self.manager = AgentManager(
            state_dir=str(self.state_dir),
            config_path=str(self.config_path),
            kimi_command=[sys.executable, "-c", "print('noop')"],
        )

    def test_configuration_resources_reads_config_toml(self):
        resources = self.manager.configuration_resources()
        self.assertIn("agents", resources)
        self.assertIn("providers", resources)
        self.assertIn("models", resources)
        self.assertEqual(resources["agents"], {
            "kimi-code": {"status": "supported"},
            "zcode": {"status": "planned"},
            "claude-code": {"status": "planned"},
        })
        # Only recognized Kimi CLI aliases are projected.
        for alias, record in resources["models"].items():
            self.assertEqual(record["alias"], alias)
            self.assertIn(record["provider_id"], resources["providers"])
            self.assertEqual(record["agent_type"], "kimi-code")
            self.assertIn("family", record)
            self.assertNotIn("api_key", json.dumps(record))
            self.assertNotIn("test-fake-SECRET_SENTINEL", json.dumps(record))
        aliases = set(resources["models"])
        self.assertTrue({"kimi-code/k3-256k", "kimi-code/kimi-for-coding", "zai-coding-plan/glm-5.3"}.issubset(aliases))

    def test_configuration_resources_preserves_bare_aliases(self):
        bare_config = Path(self.tmp.name) / "bare.toml"
        bare_config.write_text(BARE_ALIAS_TOML)
        manager = AgentManager(
            state_dir=Path(self.tmp.name) / "bare-state",
            config_path=str(bare_config),
            kimi_command=[sys.executable, "-c", "print('noop')"],
        )
        resources = manager.configuration_resources()
        aliases = set(resources["models"])
        self.assertIn("k3", aliases)
        self.assertIn("coding", aliases)
        self.assertNotIn("kimi-code/k3-256k", aliases)

    def test_configuration_resources_omits_credential_like_alias(self):
        secret_config = Path(self.tmp.name) / "secret.toml"
        secret_config.write_text(
            '[providers.kimi]\napi_key = "test-fake-SECRET_SENTINEL"\nbase_url = "https://api.kimi.com/coding/v1"\n'
            '[models."test-fake-SECRET_SENTINEL"]\nmodel = "k3-256k"\nprovider = "managed:kimi-code"\n'
            '[models."kimi-code/k3-256k"]\nmodel = "k3-256k"\nprovider = "managed:kimi-code"\n'
        )
        manager = AgentManager(
            state_dir=Path(self.tmp.name) / "secret-state",
            config_path=str(secret_config),
            kimi_command=[sys.executable, "-c", "print('noop')"],
        )
        resources = manager.configuration_resources()
        self.assertIn("kimi-code/k3-256k", resources["models"])
        self.assertNotIn("test-fake-SECRET_SENTINEL", resources["models"])
        # No synthetic redacted alias is granted instead.
        for alias in resources["models"]:
            self.assertNotIn("SECRET_SENTINEL", alias)

    def test_configuration_resources_omits_reserved_secret_field_identities(self):
        reserved_config = Path(self.tmp.name) / "reserved.toml"
        reserved_config.write_text(
            '[providers.kimi]\napi_key = "test-fake-SECRET_SENTINEL"\nbase_url = "https://api.kimi.com/coding/v1"\n'
            '[models."api_key"]\nmodel = "k3-256k"\nprovider = "managed:kimi-code"\n'
            '[models."secret"]\nmodel = "k3-256k"\nprovider = "managed:kimi-code"\n'
            '[models."kimi-code/k3-256k"]\nmodel = "k3-256k"\nprovider = "managed:kimi-code"\n'
        )
        manager = AgentManager(
            state_dir=Path(self.tmp.name) / "reserved-state",
            config_path=str(reserved_config),
            kimi_command=[sys.executable, "-c", "print('noop')"],
        )
        resources = manager.configuration_resources()
        self.assertIn("kimi-code/k3-256k", resources["models"])
        self.assertNotIn("api_key", resources["models"])
        self.assertNotIn("secret", resources["models"])

    def test_configuration_resources_no_inference_or_quota_calls(self):
        with mock.patch.object(self.manager, "probe_kimi_usage") as probe, \
             mock.patch.object(self.manager, "provider_usage") as usage:
            resources = self.manager.configuration_resources()
            self.assertIsNotNone(resources)
            probe.assert_not_called()
            usage.assert_not_called()

    def test_configuration_resources_does_not_touch_config_json_or_policies(self):
        # Seed a config.json and existing policy state; resources read must not mutate it.
        self.state_dir.mkdir(parents=True, exist_ok=True)
        config_json = self.state_dir / "config.json"
        original = {"kimi_concurrency": 7, "zai_policy": {"limit": 2}, "kimi_policy": {"usage_guard": True}}
        config_json.write_text(json.dumps(original))
        before = config_json.read_text()
        resources = self.manager.configuration_resources()
        self.assertIsNotNone(resources)
        self.assertEqual(config_json.read_text(), before)


class AgentManagerWrapperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config_path = Path(self.tmp.name) / "config.toml"
        self.config_path.write_text(CONFIG_TOML)
        self.state_dir = Path(self.tmp.name) / "state"
        self.manager = AgentManager(
            state_dir=str(self.state_dir),
            config_path=str(self.config_path),
            kimi_command=[sys.executable, "-c", "print('noop')"],
        )

    def test_wrapper_list_templates_returns_object(self):
        result = self.manager.list_templates()
        self.assertIsInstance(result, dict)
        self.assertIn("templates", result)
        ids = {t["id"] for t in result["templates"]}
        self.assertIn("default", ids)

    def test_wrapper_create_save_archive_round_trip(self):
        custom = self.manager.create_template("Custom")
        self.assertIn("id", custom)
        self.assertEqual(custom["name"], "Custom")

        payload = custom["payload"].copy()
        payload["limits"]["max_parallel_per_project"] = 5
        saved = self.manager.save_template(custom["id"], payload, expected_revision=1, name="Renamed")
        self.assertEqual(saved["revision"], 2)
        self.assertEqual(saved["name"], "Renamed")

        archived = self.manager.archive_template(custom["id"], expected_revision=2)
        self.assertTrue(archived["archived"])

    def test_wrapper_save_maps_configuration_error_to_bridge_error(self):
        custom = self.manager.create_template("Custom")
        with self.assertRaises(BridgeError) as ctx:
            self.manager.save_template(custom["id"], custom["payload"], expected_revision=99)
        self.assertEqual(ctx.exception.code, "revision_conflict")

    def test_wrapper_zero_limit_roundtrips_and_resolves_after_restart(self):
        custom = self.manager.create_template("ZeroProject")
        payload = json.loads(json.dumps(custom["payload"]))
        payload["limits"]["max_parallel_per_project"] = 0
        saved = self.manager.save_template(custom["id"], payload,
                                           expected_revision=1)
        self.assertEqual(saved["payload"]["limits"]["max_parallel_per_project"], 0)
        # Strict zero vs bool/negative/strings through the manager wrapper.
        for bad in (True, -1, "0", 2.5):
            with self.subTest(bad=bad):
                broken = json.loads(json.dumps(custom["payload"]))
                broken["limits"]["max_parallel_per_project"] = bad
                with self.assertRaises(BridgeError):
                    self.manager.save_template(custom["id"], broken,
                                               expected_revision=2)
        # Restart: a fresh manager over the same state sees the zero limit.
        fresh = AgentManager(state_dir=str(self.state_dir),
                             config_path=str(self.config_path),
                             kimi_command=[sys.executable, "-c", "print('noop')"])
        template = fresh.get_template(custom["id"])
        self.assertEqual(template["payload"]["limits"]["max_parallel_per_project"], 0)
        project = fresh.register_project("zero-demo", self.tmp.name)
        fresh.bind_project_template(project["id"], custom["id"],
                                    expected_binding_revision=0)
        resolution = fresh.resolve_project_config(project["id"])
        self.assertTrue(resolution["ready"])
        self.assertEqual(resolution["template_id"], custom["id"])

    def test_wrapper_project_binding_and_resolution(self):
        project = self.manager.register_project("demo", self.tmp.name)
        self.assertIn("id", project)
        self.assertEqual(project["name"], "demo")

        projects = self.manager.list_projects()
        self.assertIsInstance(projects, dict)
        self.assertEqual(len(projects["projects"]), 1)

        bound = self.manager.bind_project_template(project["id"], None, expected_binding_revision=0)
        self.assertIsNone(bound["template_id"])

        resolution = self.manager.resolve_project_config(project["id"])
        self.assertEqual(resolution["project_id"], project["id"])
        self.assertEqual(resolution["template_id"], "default")
        self.assertIn("candidates", resolution)
        self.assertIn("unavailable", resolution)

    def test_wrapper_resolve_snapshot_pinned(self):
        project = self.manager.register_project("demo", self.tmp.name)
        first = self.manager.resolve_project_config(project["id"], snapshot_id="s1")
        self.assertEqual(first["snapshot_id"], "s1")
        second = self.manager.resolve_project_config(project["id"], snapshot_id="s1")
        self.assertEqual(second["template_revision"], first["template_revision"])


class ConfigurationMcpSchemaTests(unittest.TestCase):
    def test_configuration_methods_present(self):
        expected = {
            "list_templates", "get_template", "create_template", "save_template",
            "archive_template", "register_project", "list_projects",
            "bind_project_template", "resolve_project_config", "configuration_resources",
        }
        self.assertTrue(expected.issubset(set(server.METHODS)),
                        f"Missing configuration methods: {expected - set(server.METHODS)}")

    def test_get_template_schema_revision_integer_null_minimum1(self):
        schema = server.tool_schema("get_template")
        rev = schema["inputSchema"]["properties"]["revision"]
        self.assertEqual(sorted(rev["type"]), ["integer", "null"])
        self.assertEqual(rev.get("minimum"), 1)

    def test_save_archive_expected_revision_integer_minimum1(self):
        for name in ("save_template", "archive_template"):
            with self.subTest(method=name):
                schema = server.tool_schema(name)
                rev = schema["inputSchema"]["properties"]["expected_revision"]
                self.assertEqual(rev["type"], "integer")
                self.assertEqual(rev.get("minimum"), 1)

    def test_bind_project_template_expected_binding_revision_integer_minimum0(self):
        schema = server.tool_schema("bind_project_template")
        rev = schema["inputSchema"]["properties"]["expected_binding_revision"]
        self.assertEqual(rev["type"], "integer")
        self.assertEqual(rev.get("minimum"), 0)

    def test_template_payload_max_parallel_allows_zero(self):
        schema = server.tool_schema("save_template")
        limits = schema["inputSchema"]["properties"]["payload"]["properties"]["limits"]
        limit = limits["properties"]["max_parallel_per_project"]
        self.assertEqual(limit["type"], "integer")
        self.assertEqual(limit.get("minimum"), 0)
        self.assertEqual(limit.get("maximum"), 100)
        self.assertIn("max_parallel_per_project", limits["required"])

    def test_concurrency_limit_schemas_accept_zero(self):
        for name in ("set_concurrency", "set_zai_policy"):
            with self.subTest(method=name):
                schema = server.tool_schema(name)
                limit = schema["inputSchema"]["properties"]["limit"]
                self.assertIn({"type": "integer", "minimum": 0}, limit["oneOf"])
        accounts = server.tool_schema("set_kimi_accounts")
        additional = accounts["inputSchema"]["properties"]["limits"]["additionalProperties"]
        self.assertIn({"type": "integer", "minimum": 0}, additional["oneOf"])

    def test_schema_payload_is_object(self):
        schema = server.tool_schema("save_template")
        payload = schema["inputSchema"]["properties"]["payload"]
        self.assertEqual(payload["type"], "object")

    def test_schema_readonly_and_destructive_hints(self):
        readonly = {"configuration_resources", "list_templates", "get_template", "list_projects"}
        destructive = {"archive_template"}
        # resolve_project_config is NOT readOnly because snapshot_id persists an immutable snapshot.
        resolve_schema = server.tool_schema("resolve_project_config")
        self.assertFalse(resolve_schema.get("annotations", {}).get("readOnlyHint"))
        for name in readonly:
            with self.subTest(method=name):
                schema = server.tool_schema(name)
                self.assertTrue(schema.get("annotations", {}).get("readOnlyHint"), name)
        for name in destructive:
            with self.subTest(method=name):
                schema = server.tool_schema(name)
                self.assertTrue(schema.get("annotations", {}).get("destructiveHint"), name)


class ConfigurationInvokeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config_path = Path(self.tmp.name) / "config.toml"
        self.config_path.write_text(CONFIG_TOML)
        self.manager = AgentManager(
            state_dir=str(Path(self.tmp.name) / "state"),
            config_path=str(self.config_path),
            kimi_command=[sys.executable, "-c", "print('noop')"],
        )

    def test_invoke_list_templates_succeeds(self):
        result = server.invoke(self.manager, "list_templates", {})
        self.assertIsInstance(result, dict)
        self.assertIn("templates", result)

    def test_invoke_save_failure_returns_bridge_error(self):
        with self.assertRaises(BridgeError) as ctx:
            server.invoke(self.manager, "save_template", {
                "template_id": "default",
                "payload": {"invalid": True},
                "expected_revision": 1,
            })
        self.assertEqual(ctx.exception.code, "invalid_argument")

    def test_invoke_result_sanitizes_no_secrets(self):
        result = server.invoke(self.manager, "configuration_resources", {})
        text = json.dumps(result)
        self.assertNotIn("test-fake-SECRET_SENTINEL", text)
        self.assertNotIn("api_key", text)

    def test_invoke_unknown_configuration_tool_fails(self):
        with self.assertRaises(BridgeError) as ctx:
            server.invoke(self.manager, "not_a_tool", {})
        self.assertEqual(ctx.exception.code, "invalid_argument")


class ConfigurationMcpHandleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config_path = Path(self.tmp.name) / "config.toml"
        self.config_path.write_text(CONFIG_TOML)
        self.manager = AgentManager(
            state_dir=str(Path(self.tmp.name) / "state"),
            config_path=str(self.config_path),
            kimi_command=[sys.executable, "-c", "print('noop')"],
        )

    def test_handle_tools_call_returns_structured_content_object(self):
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "configuration_resources", "arguments": {}},
        }
        response = server.handle(self.manager, request)
        self.assertEqual(response["id"], 1)
        self.assertNotIn("error", response)
        result = response["result"]
        self.assertIn("structuredContent", result)
        self.assertIn("content", result)
        self.assertIsInstance(result["structuredContent"], dict)
        self.assertEqual(result["structuredContent"]["agents"]["kimi-code"], {"status": "supported"})

    def test_handle_tools_list_is_isolated_from_manager_state(self):
        # Stdio discovery must not read configuration or initialize state.
        mock_manager = mock.Mock(spec=AgentManager)
        request = {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/list",
            "params": {},
        }
        response = server.handle(mock_manager, request)
        self.assertEqual(response["id"], 7)
        self.assertIn("tools", response["result"])
        mock_manager.assert_not_called()
        mock_manager.configuration_resources.assert_not_called()


if __name__ == "__main__":
    unittest.main()
