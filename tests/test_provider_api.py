"""Independent GLM API-surface tests for the stage-2 resource methods.

Scope templates-stage2-20261013, work_unit resource-backend-final.

Derived from docs/TEMPLATE_STAGE2_SPEC.md: MCP schema/discovery exposure,
strict dispatch argument handling, sanitized errors, authenticated HTTP
round trips and quota-free settings reads. No network, no credentials.
"""
import json
import sys
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "mixed-agents" / "bridge"))
import console  # noqa: E402
import kimi_usage  # noqa: E402
import server  # noqa: E402
import zai  # noqa: E402
from runtime import AgentManager, BridgeError  # noqa: E402

RESOURCE_METHODS = ("create_provider", "save_provider", "create_model", "save_model",
                    "get_provider_settings")

SYNTHETIC_TOML = """# Synthetic API-test installation; no real credentials.
[models."kimi-code/k3-256k"]
model = "k3-256k"
provider = "managed:kimi-code"

[models."zai-synth/glm-5.3"]
model = "glm-5.3"
provider = "zai-managed"

[providers."managed:kimi-code"]
base_url = "https://api.kimi.com/coding/v1"
api_key_env = "MIXED_AGENTS_SYNTH_API_KIMI"

[providers."zai-managed"]
base_url = "https://api.z.ai/api/coding"
api_key_env = "MIXED_AGENTS_SYNTH_API_ZAI"
"""


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = self.root / "config.toml"
        self.config.write_text(SYNTHETIC_TOML, encoding="utf-8")
        self.manager = AgentManager(state_dir=self.root / "state", config_path=self.config)

    def tearDown(self):
        self.tmp.cleanup()

    def invoke(self, method, arguments):
        return server.invoke(self.manager, method, arguments)

    def create_work_provider(self):
        return self.invoke("create_provider",
                           {"provider_id": "api-work-co", "label": "API 连接",
                            "provider_type": "generic", "endpoint": "https://api.work.example/v1",
                            "protocol": "openai", "credential_ref": "env:MIXED_AGENTS_API_WORK"})


class McpSurfaceTests(ApiTestCase):
    def test_tools_list_exposes_resource_methods_with_strict_schemas(self):
        listing = server.handle(self.manager, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        tools = {tool["name"]: tool for tool in listing["result"]["tools"]}
        for name in RESOURCE_METHODS:
            self.assertIn(name, tools)
            schema = tools[name]["inputSchema"]
            self.assertFalse(schema.get("additionalProperties", True))
        settings = tools["get_provider_settings"]
        self.assertTrue(settings["annotations"]["readOnlyHint"])
        for name in ("create_provider", "save_provider", "create_model", "save_model"):
            self.assertFalse(tools[name]["annotations"].get("readOnlyHint"))
        self.assertEqual(set(tools["create_provider"]["inputSchema"]["properties"]),
                         {"provider_id", "label", "provider_type", "endpoint", "protocol",
                          "credential_ref"})
        self.assertEqual(set(tools["create_model"]["inputSchema"]["properties"]),
                         {"alias", "provider_id", "upstream_model", "display_name",
                          "context", "capabilities"})
        self.assertIn("expected_revision", tools["save_provider"]["inputSchema"]["properties"])
        self.assertIn("expected_revision", tools["save_model"]["inputSchema"]["properties"])

    def test_dispatch_rejects_unknown_fields_and_non_object_arguments(self):
        self.assertIsInstance(self.create_work_provider(), dict)
        for arguments in ({"provider_id": "x", "label": "x", "provider_type": "generic",
                           "endpoint": "https://api.work.example/v1", "protocol": "openai",
                           "credential_ref": "env:X", "bogus": 1}, "not-a-dict", 7):
            with self.subTest(arguments=arguments):
                with self.assertRaises(BridgeError) as ctx:
                    server.invoke(self.manager, "create_provider", arguments)
                self.assertEqual(ctx.exception.code, "invalid_argument")

    def test_invoke_errors_are_sanitized_objects(self):
        with self.assertRaises(BridgeError) as ctx:
            self.invoke("create_provider",
                        {"provider_id": "api-key", "label": "x", "provider_type": "generic",
                         "endpoint": "https://api.work.example/v1/SECRETMARKER",
                         "protocol": "openai", "credential_ref": "env:X"})
        payload = json.dumps(ctx.exception.as_dict(), ensure_ascii=False)
        self.assertNotIn("SECRETMARKER", payload)


class ConsoleHttpTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.server = console.make_server(self.manager, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()
        super().tearDown()

    def post(self, body, token=None):
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            connection.putrequest("POST", "/api/call", skip_host=True)
            connection.putheader("Host", f"127.0.0.1:{self.server.server_port}")
            connection.putheader("Origin", self.server.origin)
            if token is not False:
                connection.putheader("X-Console-Token", token or self.server.token)
            connection.putheader("Content-Type", "application/json")
            payload = body if isinstance(body, bytes) else json.dumps(body).encode()
            connection.putheader("Content-Length", str(len(payload)))
            connection.endheaders()
            connection.send(payload)
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def get_state(self):
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            connection.putrequest("GET", "/api/state", skip_host=True)
            connection.putheader("Host", f"127.0.0.1:{self.server.server_port}")
            connection.putheader("X-Console-Token", self.server.token)
            connection.endheaders()
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def test_authenticated_resource_roundtrip_over_http(self):
        status, data = self.post({"method": "create_provider", "arguments": {
            "provider_id": "http-co", "label": "HTTP 连接", "provider_type": "generic",
            "endpoint": "https://api.work.example/v1", "protocol": "openai",
            "credential_ref": "env:MIXED_AGENTS_HTTP_WORK"}})
        self.assertEqual(status, 200)
        value = json.loads(data)
        self.assertEqual(value["revision"], 1)
        self.assertNotIn("config_path", value)
        status, data = self.get_state()
        self.assertEqual(status, 200)
        state = json.loads(data)
        self.assertEqual(state["resources"]["providers"]["http-co"]["credential_ref"],
                         "env:MIXED_AGENTS_HTTP_WORK")
        status, data = self.post({"method": "save_provider", "arguments": {
            "provider_id": "http-co", "expected_revision": 1, "enabled": False}})
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(data)["enabled"])

    def test_resource_posts_require_token_and_strict_envelope(self):
        body = {"method": "get_provider_settings", "arguments": {"provider_id": "kimi-code"}}
        self.assertEqual(self.post(body, token="wrong")[0], 403)
        self.assertEqual(self.post(body, token=False)[0], 403)
        self.assertEqual(self.post({**body, "extra": 1})[0], 400)
        self.assertEqual(self.post(b"{not json")[0], 400)

    def test_settings_scope_and_sanitized_errors_over_http(self):
        status, data = self.post({"method": "get_provider_settings",
                                  "arguments": {"provider_id": "kimi-code"}})
        self.assertEqual(status, 200)
        kimi = json.loads(data)
        self.assertEqual(kimi["handler"], "kimi")
        status, data = self.post({"method": "get_provider_settings",
                                  "arguments": {"provider_id": "zai-synth"}})
        self.assertEqual(json.loads(data)["handler"], "zai")
        self.create_work_provider()
        status, data = self.post({"method": "get_provider_settings",
                                  "arguments": {"provider_id": "api-work-co"}})
        self.assertEqual(json.loads(data)["handler"], "generic")
        status, data = self.post({"method": "create_provider", "arguments": {
            "provider_id": "x/y", "label": "x", "provider_type": "generic",
            "endpoint": "https://api.work.example/v1/SECRETMARKER", "protocol": "openai",
            "credential_ref": "env:X"}})
        self.assertEqual(status, 400)
        self.assertNotIn(b"SECRETMARKER", data)

    def test_resource_calls_over_http_never_read_quota(self):
        def forbidden(*args, **kwargs):
            raise AssertionError("quota read during a resource API call")

        with mock.patch.object(kimi_usage, "read_usage", forbidden), \
                mock.patch.object(zai, "read_usage", forbidden):
            status, _ = self.get_state()
            self.assertEqual(status, 200)
            status, _ = self.post({"method": "get_provider_settings",
                                   "arguments": {"provider_id": "kimi-code"}})
            self.assertEqual(status, 200)
            self.create_work_provider()
    def test_state_endpoint_reports_sanitized_source_conflict(self):
        self.invoke("create_provider",
                    {"provider_id": "clash-co", "label": "托管先行", "provider_type": "generic",
                     "endpoint": "https://api.work.example/v1", "protocol": "openai",
                     "credential_ref": "env:MIXED_AGENTS_CLASH"})
        drifted = self.root / "drifted-clash.toml"
        drifted.write_text(SYNTHETIC_TOML + (
            '\n[models."clash-co/glm-5.3"]\nmodel = "glm-5.3"\nprovider = "clash-entry"\n\n'
            '[providers."clash-entry"]\nbase_url = "https://api.clash.example/v1/CLASHMARKER"\n'
            'api_key_env = "MIXED_AGENTS_SYNTH_CLASH"\n'), encoding="utf-8")
        collided = AgentManager(state_dir=self.root / "state", config_path=drifted)
        server = console.make_server(collided, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            try:
                connection.putrequest("GET", "/api/state", skip_host=True)
                connection.putheader("Host", f"127.0.0.1:{server.server_port}")
                connection.putheader("X-Console-Token", server.token)
                connection.endheaders()
                response = connection.getresponse()
                data = response.read()
                self.assertEqual(response.status, 400)
                value = json.loads(data)
                # The console preserves the sanitized BridgeError code and
                # message instead of a generic invalid-config response.
                self.assertEqual(value["error"], "source_conflict")
                self.assertTrue(value.get("message"))
                self.assertNotIn("config_path", value)
                self.assertNotIn(b"CLASHMARKER", data)
            finally:
                connection.close()
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()


if __name__ == "__main__":
    unittest.main()
