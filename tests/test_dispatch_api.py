"""Independent acceptance tests for the Stage 3 dispatch MCP/API surface
(docs/TEMPLATE_STAGE3_SPEC.md): optional project_id/cwd parameters on
select_model and project_id on spawn_agent, sanitization of public results
and structured error codes. Complements tests/test_dispatch_configuration.py
(behavior) with schema and transport-level contract checks.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

BRIDGE = Path(__file__).resolve().parents[1] / "plugins/mixed-agents/bridge"
sys.path.insert(0, str(BRIDGE))

# See tests/test_dispatch_configuration.py: the real bridge.materialization is
# authored by the Kimi production author; until it lands, use the same
# test-local stand-in so dispatch behavior remains exercisable.
import contextlib  # noqa: E402
import importlib.util  # noqa: E402
import types  # noqa: E402

if importlib.util.find_spec("materialization") is None:
    _stub = types.ModuleType("materialization")

    class _StubMaterializationError(Exception):
        def __init__(self, code, message):
            self.code = code
            self.message = message
            super().__init__(message)

    def _describe(config_path, model_record, provider_record):
        return {"model": model_record["alias"], "provider_id": provider_record["id"],
                "source": "test-stub"}

    def _guard_target(config_path, descriptor):
        return {"config_path": config_path, "model": descriptor["model"]}

    @contextlib.contextmanager
    def _prepare(config_path, descriptor, state_dir):
        env = dict(os.environ)
        env["KIMI_CODE_HOME"] = str(Path(config_path).expanduser().resolve().parent)
        yield {"config_path": config_path, "env": env, "model": descriptor["model"]}

    _stub.MaterializationError = _StubMaterializationError
    _stub.describe = _describe
    _stub.guard_target = _guard_target
    _stub.prepare = _prepare
    sys.modules["materialization"] = _stub

import server  # noqa: E402
from runtime import AgentManager, BridgeError  # noqa: E402

ALIAS_A = "kimi/k3-256k"
SECRET = "test-fake-SECRET_SENTINEL"

CONFIG_TOML = """
[providers.kimi]
api_key = "%(secret)s"
base_url = "https://api.kimi.com/coding/v1"

[models."%(a)s"]
model = "k3-256k"
provider = "kimi"
max_context_size = 2000000
""" % {"secret": SECRET, "a": ALIAS_A}


class DispatchSchemaTests(unittest.TestCase):
    def test_select_model_has_optional_project_id_and_cwd(self):
        schema = server.tool_schema("select_model")
        properties = schema["inputSchema"]["properties"]
        for key in ("project_id", "cwd"):
            self.assertIn(key, properties)
            self.assertEqual(sorted(properties[key]["type"]), ["null", "string"])
            self.assertNotIn(key, schema["inputSchema"]["required"])

    def test_spawn_agent_has_optional_project_id(self):
        schema = server.tool_schema("spawn_agent")
        properties = schema["inputSchema"]["properties"]
        self.assertIn("project_id", properties)
        self.assertEqual(sorted(properties["project_id"]["type"]), ["null", "string"])
        self.assertNotIn("project_id", schema["inputSchema"]["required"])
        self.assertFalse(schema.get("annotations", {}).get("readOnlyHint"))


class DispatchApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / "kimi-home"
        self.home.mkdir()
        self.config = self.home / "config.toml"
        self.config.write_text(CONFIG_TOML)
        self.work = self.root / "work"
        self.inner = self.work / "inner"
        self.inner.mkdir(parents=True)
        self.manager = AgentManager(
            state_dir=str(self.root / "state"), config_path=str(self.config),
            kimi_command=[sys.executable, "-c", "print('noop')"])

    def bind_allowing(self, project_id, models=(ALIAS_A,), enabled=True):
        template = self.manager.create_template("Custom")
        payload = template["payload"]
        payload["agent_configs"] = [{
            "agent_type": "kimi-code", "enabled": enabled,
            "providers": [{"provider_id": "kimi", "models": list(models)}]
            if models else [],
        }]
        self.manager.save_template(template["id"], payload, expected_revision=1)
        self.manager.bind_project_template(project_id, template["id"],
                                           expected_binding_revision=0)
        return template

    def test_register_and_list_projects_expose_no_root_path(self):
        project = self.manager.register_project("Inner", str(self.inner))
        for value in project.values():
            self.assertNotIn(str(self.inner), str(value))
        listing = self.manager.list_projects()
        blob = json.dumps(listing)
        self.assertNotIn(str(self.inner), blob)
        self.assertNotIn(str(self.work), blob)

    def test_invoke_spawn_with_project_id_returns_public_view(self):
        project = self.manager.register_project("Inner", str(self.inner))
        self.bind_allowing(project["id"])
        result = server.invoke(self.manager, "spawn_agent", {
            "scope": "scope", "work_unit": "u1", "role": "exploration",
            "message": "hello", "cwd": str(self.inner), "model": ALIAS_A,
            "project_id": project["id"],
        })
        self.assertEqual(result["project_id"], project["id"])
        # The immutable turn snapshot is persisted by the worker as the turn
        # starts; poll briefly instead of asserting launch-time state.
        snapshot = None
        for _ in range(100):
            snapshot = self.manager.get_agent(result["agent_id"])["configuration_snapshot"]
            if snapshot is not None:
                break
            time.sleep(0.05)
        self.assertIsNotNone(snapshot)
        blob = json.dumps(self.manager.get_agent(result["agent_id"]))
        self.assertNotIn(str(self.inner), blob)
        self.assertNotIn(SECRET, blob)

    def test_invoke_select_model_accepts_project_context(self):
        project = self.manager.register_project("Inner", str(self.inner))
        self.bind_allowing(project["id"])
        result = server.invoke(self.manager, "select_model", {
            "scope": "scope", "work_unit": "u1", "role": "research",
            "native_models": ["gpt-6.1-sol"], "project_id": project["id"],
        })
        self.assertEqual(result["project_id"], project["id"])
        self.assertEqual(result["selected"]["alias"], ALIAS_A)
        self.assertTrue(result["selection_is_advisory"])
        self.assertEqual({c["family"] for c in result["candidates"]}, {"kimi"})

    def test_handle_native_project_unsupported_structured_error(self):
        project = self.manager.register_project("Inner", str(self.inner))
        self.bind_allowing(project["id"])
        response = server.handle(self.manager, {
            "jsonrpc": "2.0", "id": 5, "method": "tools/call",
            "params": {"name": "spawn_agent", "arguments": {
                "scope": "scope", "work_unit": "u1", "role": "exploration",
                "message": "hello", "cwd": str(self.inner),
                "model": "gpt-6.1-sol", "project_id": project["id"]}}})
        result = response["result"]
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["error"],
                         "native_project_unsupported")
        self.assertNotIn(str(self.inner), json.dumps(result["structuredContent"]))
        self.assertNotIn(SECRET, json.dumps(result["structuredContent"]))

    def test_handle_model_not_allowed_structured_error(self):
        project = self.manager.register_project("Inner", str(self.inner))
        self.bind_allowing(project["id"], models=(), enabled=False)
        payload = self.manager.get_template("default")["payload"]
        payload["agent_configs"] = [{
            "agent_type": "kimi-code", "enabled": False, "providers": [],
        }]
        self.manager.save_template("default", payload, expected_revision=1)
        response = server.handle(self.manager, {
            "jsonrpc": "2.0", "id": 6, "method": "tools/call",
            "params": {"name": "spawn_agent", "arguments": {
                "scope": "scope", "work_unit": "u1", "role": "exploration",
                "message": "hello", "cwd": str(self.inner), "model": ALIAS_A,
                "project_id": project["id"]}}})
        result = response["result"]
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["error"], "model_not_allowed")


if __name__ == "__main__":
    unittest.main()
