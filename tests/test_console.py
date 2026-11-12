"""Offline stdlib HTTP tests for the loopback-only policy console."""
import json
import sys
import tempfile
import threading
import unittest
from collections import Counter
from http.client import HTTPConnection
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "mixed-agents" / "bridge"))
import console  # noqa: E402

LEGACY_METHODS = frozenset({"set_concurrency", "set_kimi_policy", "set_kimi_accounts",
                            "set_zai_policy", "set_cost_policy", "provider_usage",
                            "probe_kimi_usage"})
STAGE1_METHODS = frozenset({"configuration_resources", "list_templates", "get_template",
                            "create_template", "save_template", "archive_template",
                            "register_project", "list_projects", "bind_project_template",
                            "resolve_project_config"})
RESOURCE_METHODS = frozenset({"create_provider", "save_provider", "create_model",
                              "save_model", "get_provider_settings"})
CONSOLE_METHODS = LEGACY_METHODS | STAGE1_METHODS | RESOURCE_METHODS

# Obviously synthetic values assembled at runtime so the tree scan never sees
# a key-shaped literal; never real credentials, only leak sentinels.
FAKE_KIMI_KEY = "s" + "k-" + "synthkimi" + "0" * 18
FAKE_ZAI_KEY = "s" + "k-" + "synthzai" + "0" * 19
SYNTHETIC_TOML = f"""# Synthetic console-test installation; no real credentials.
[models."kimi-code/k3-256k"]
model = "k3-256k"
provider = "managed:kimi-code"

[models."zai-synth/glm-5.3"]
model = "glm-5.3"
provider = "zai-managed"

[providers."managed:kimi-code"]
base_url = "https://api.kimi.com/coding/v1"
api_key = "{FAKE_KIMI_KEY}"

[providers."zai-managed"]
base_url = "https://api.z.ai/api/coding"
api_key = "{FAKE_ZAI_KEY}"
"""

# Local accessors a pure configuration snapshot may touch; nothing else.
SNAPSHOT_LOCAL_CALLS = Counter({"_capacity": 1, "_kimi_policy_view": 1, "_zai_policy_view": 1,
                                "_policy": 1, "_policy_view": 1, "_kimi_accounts_view": 1,
                                "configuration_resources": 1, "list_templates": 1,
                                "list_projects": 1})
REMOTE_CALLS = ("model_catalog", "provider_usage", "select_model", "spawn_agent")


class FakeManager:
    """Local offline accessor; no real config, quota or inference."""

    def __init__(self, config):
        self.config = config
        self.calls = []
        self.probe_calls = []

    def _record(self, name):
        self.calls.append(name)
        return name

    def _capacity(self):
        self._record("_capacity")
        return {"kimi_cap": 5, "kimi_running": 2, "kimi_available": 3, "kimi_unlimited": False}

    def _policy(self):
        self._record("_policy")
        return {"enabled": False, "model": None, "timezone": "Asia/Shanghai",
                "peak_start": "14:00", "peak_end": "18:00", "peak_weekdays": [1, 2, 3, 4, 5]}

    def _kimi_policy_view(self, include_usage=False):
        self._record("_kimi_policy_view")
        return {"usage_guard": True, "stop_remaining_percent": 5,
                "probe_on_unknown": False, "probe_cooldown_minutes": 15}

    def _zai_policy_view(self, include_usage=False):
        self._record("_zai_policy_view")
        return {"cap": 3, "running": 0, "unlimited": False, "available": 3,
                "usage_guard": True, "stop_remaining_percent": 10}

    def _policy_view(self, policy, cli_aliases):
        self._record("_policy_view")
        return {"enabled": False, "model": None, "timezone": "Asia/Shanghai",
                "peak_start": "14:00", "peak_end": "18:00", "peak_weekdays": [1, 2, 3, 4, 5],
                "period": "disabled", "model_available": False}

    def _kimi_accounts_view(self):
        self._record("_kimi_accounts_view")
        return {"order": ["kimi-code"], "limits": {"kimi-code": 3},
                "accounts": [{"prefix": "kimi-code", "configured": True, "running": 2}]}

    def configuration_resources(self):
        self._record("configuration_resources")
        # Stage 2 public shape: credential_ref is symbolic public metadata
        # (env:VARIABLE), never a credential value, file or private path.
        return {
            "agents": {
                "kimi-code": {"status": "supported"},
                "zcode": {"status": "planned"},
                "claude-code": {"status": "planned"},
            },
            "providers": {
                "kimi-code": {
                    "id": "kimi-code", "label": "kimi-code", "provider_type": "kimi",
                    "endpoint": "https://api.kimi.com/coding/v1", "protocol": "openai",
                    "credential_ref": "env:KIMI_API_KEY", "enabled": True, "revision": 1,
                    "source": "imported", "model_aliases": ["kimi-code/k3-256k"],
                },
                "zai-synth": {
                    "id": "zai-synth", "label": "zai-synth", "provider_type": "zai",
                    "endpoint": "https://api.z.ai/api/coding", "protocol": "openai",
                    "credential_ref": "env:ZAI_API_KEY", "enabled": True, "revision": 1,
                    "source": "imported", "model_aliases": ["zai-synth/glm-5.3"],
                },
            },
            "models": {
                "kimi-code/k3-256k": {
                    "alias": "kimi-code/k3-256k", "provider_id": "kimi-code",
                    "agent_type": "kimi-code", "family": "kimi", "context": 262144,
                    "capabilities": ["thinking", "tool_use"], "upstream_model": "k3-256k",
                    "display_name": "Kimi K3 (256k)", "enabled": True, "revision": 1,
                    "source": "imported",
                },
                "zai-synth/glm-5.3": {
                    "alias": "zai-synth/glm-5.3", "provider_id": "zai-synth",
                    "agent_type": "kimi-code", "family": "glm", "context": 1000000,
                    "capabilities": ["thinking", "tool_use"], "upstream_model": "glm-5.3",
                    "display_name": "GLM 5.3", "enabled": True, "revision": 1,
                    "source": "imported",
                },
            },
        }

    def list_templates(self):
        self._record("list_templates")
        return {"templates": [{"id": "default", "name": "Default", "revision": 1,
                               "is_default": True, "archived": False,
                               "payload": {"schema_version": 1, "agent_configs": [],
                                           "limits": {"max_parallel_per_project": 3},
                                           "selection": {"prefer_provider_discount_on_equal_fit": False}}}]}

    def list_projects(self):
        self._record("list_projects")
        return {"projects": [{"id": "p1", "name": "Demo", "host_id": "local",
                              "template_id": None, "binding_revision": 0}]}

    def probe_kimi_usage(self, *args, **kwargs):
        self.probe_calls.append((args, kwargs))
        return {"usage": "unknown"}

    def __getattr__(self, name):
        # Remote-facing bridge operations must never be reached by a
        # configuration snapshot; record instead of performing them.
        if name in REMOTE_CALLS:
            def forbidden(*args, **kwargs):
                self.calls.append(name)
                return {}
            return forbidden
        raise AttributeError(name)


class ConsoleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config_path = Path(self.tmp.name) / "config.toml"
        self.config_path.write_text(SYNTHETIC_TOML, encoding="utf-8")
        self.manager = FakeManager(self.config_path)
        self.server = console.make_server(self.manager, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()
        self.tmp.cleanup()

    def exchange(self, method, path, headers, body=b"", declared=None, server=None):
        server = server or self.server
        conn = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        try:
            conn.putrequest(method, path, skip_host=True)
            for key, value in headers:
                conn.putheader(key, value)
            conn.putheader("Content-Length", str(len(body) if declared is None else declared))
            conn.endheaders()
            if body: conn.send(body)
            response = conn.getresponse()
            return response.status, {k.lower(): v for k, v in response.getheaders()}, response.read()
        finally:
            conn.close()

    def send(self, method, path, *, hosts=None, token=None, origin=None,
             ctype="application/json", body=None, declared=None, server=None):
        server = server or self.server
        if hosts is None: hosts = [f"127.0.0.1:{server.server_port}"]
        headers = [("Host", host) for host in hosts]
        if token is None: token = server.token if method == "POST" else False
        if token is not False:
            headers.append(("X-Console-Token", token))
        if origin is None: origin = server.origin if method == "POST" else False
        if origin is not False:
            headers.append(("Origin", origin))
        if ctype:
            headers.append(("Content-Type", ctype))
        payload = b"" if body is None else body if isinstance(body, bytes) else json.dumps(body).encode()
        return self.exchange(method, path, headers, payload, declared, server)

    def call(self, method, arguments, server=None):
        status, _, data = self.send("POST", "/api/call", server=server,
                                    body={"method": method, "arguments": arguments})
        return status, json.loads(data) if data else {}

    def test_loopback_html_page(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        status, headers, data = self.send("GET", "/", token=False)
        self.assertEqual(status, 200)
        self.assertEqual(headers["content-type"], "text/html; charset=utf-8")
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertIn("default-src 'none'", headers["content-security-policy"])
        self.assertIn(f"nonce-{self.server.token}", headers["content-security-policy"])
        self.assertIn(self.server.token.encode(), data)

    def test_reads_reject_bad_host_and_api_requires_token(self):
        host = f"127.0.0.1:{self.server.server_port}"
        for hosts in ([], ["evil.example"], ["127.0.0.1"], [f"localhost:{host.split(':')[1]}"], [host, host]):
            with self.subTest(hosts=hosts):
                self.assertEqual(self.send("GET", "/", hosts=hosts, token=False)[0], 403)
        self.assertEqual(self.send("GET", "/api/state", token=False)[0], 403)
        self.assertEqual(self.send("GET", "/api/state", token="wrong")[0], 403)
        status, _, data = self.send("GET", "/api/state", token=self.server.token)
        self.assertEqual((status, b'"models"' in data), (200, True))

    def test_state_snapshot_is_local_sanitized_and_symbolic(self):
        self.send("GET", "/", token=False)
        status, headers, data = self.send("GET", "/api/state", token=self.server.token)
        self.assertEqual(status, 200)
        self.assertEqual(headers["content-type"], "application/json; charset=utf-8")
        value = json.loads(data)
        # Aliases come from the synthetic config TOML, not a hardcoded fake list.
        self.assertEqual(value["models"], [{"alias": "kimi-code/k3-256k", "family": "kimi",
                                            "profile": "k3-256k", "routing_default": True},
                                           {"alias": "zai-synth/glm-5.3", "family": "glm",
                                            "profile": "glm-5.3", "routing_default": True}])
        self.assertEqual({row["alias"] for row in value["usage_models"]},
                         {"kimi-code/k3-256k", "zai-synth/glm-5.3"})
        self.assertEqual(value["kimi_policy"]["probe_cooldown_minutes"], 15)
        self.assertEqual(value["kimi_accounts"]["order"], ["kimi-code"])
        self.assertIs(value["runtime_configuration_enforced"], True)
        # credential_ref stays public symbolic metadata (env:VARIABLE_NAME).
        providers = value["resources"]["providers"]
        self.assertEqual(providers["kimi-code"]["credential_ref"], "env:KIMI_API_KEY")
        self.assertEqual(providers["zai-synth"]["credential_ref"], "env:ZAI_API_KEY")
        for provider in providers.values():
            for key in ("id", "label", "provider_type", "endpoint", "protocol",
                        "credential_ref", "enabled", "revision", "source", "model_aliases"):
                self.assertIn(key, provider)
        for model in value["resources"]["models"].values():
            for key in ("alias", "provider_id", "agent_type", "family", "context",
                        "capabilities", "upstream_model", "display_name", "enabled",
                        "revision", "source"):
                self.assertIn(key, model)
        self.assertIn(b"env:KIMI_API_KEY", data)
        self.assertEqual([t["id"] for t in value["templates"]], ["default"])
        self.assertEqual([p["id"] for p in value["projects"]], ["p1"])
        for project in value["projects"]:
            self.assertNotIn("root", project)
        for banned in ("observations", "config_path", "secrets", "prompts", "workers",
                       "credentials", "api_key"):
            self.assertNotIn(banned, value)
        # No credential value or private path is ever serialized.
        self.assertNotIn(FAKE_KIMI_KEY.encode(), data)
        self.assertNotIn(FAKE_ZAI_KEY.encode(), data)
        self.assertNotIn(self.tmp.name.encode(), data)
        # Pure local snapshot: exactly the local accessors, no catalog/usage/probe.
        self.assertEqual(Counter(self.manager.calls), SNAPSHOT_LOCAL_CALLS)
        self.assertEqual(self.manager.probe_calls, [])

    def test_post_rejects_bad_host_origin_or_token(self):
        body = {"method": "provider_usage", "arguments": {}}
        for kwargs in ({"origin": False}, {"origin": "null"}, {"origin": "http://127.0.0.1:1"},
                       {"token": False}, {"token": "wrong"}, {"hosts": ["evil.example"]}):
            with self.subTest(**kwargs), mock.patch.object(console.server, "invoke", return_value={}):
                self.assertEqual(self.send("POST", "/api/call", body=body, **kwargs)[0], 403)

    def test_set_kimi_policy_forwards_cooldown_and_strips_config_path(self):
        arguments = {"usage_guard": True, "stop_remaining_percent": 5,
                     "probe_on_unknown": False, "probe_cooldown_minutes": 15}
        with mock.patch.object(console.server, "invoke",
                               return_value={"ok": True, "config_path": str(self.manager.config)}) as invoke:
            status, _, data = self.send("POST", "/api/call",
                                        body={"method": "set_kimi_policy", "arguments": arguments})
        self.assertEqual(status, 200)
        invoke.assert_called_once_with(self.manager, "set_kimi_policy", arguments)
        self.assertEqual(json.loads(data), {"ok": True})

    def test_allowed_methods_are_exactly_the_console_surface(self):
        self.assertEqual(len(CONSOLE_METHODS), 22)
        self.assertEqual(console.ALLOWED, CONSOLE_METHODS)
        self.assertEqual(len(console.ALLOWED), 22)
        self.assertTrue(LEGACY_METHODS <= console.ALLOWED)
        self.assertTrue(STAGE1_METHODS <= console.ALLOWED)
        self.assertTrue(RESOURCE_METHODS <= console.ALLOWED)

    def test_all_console_methods_reach_invoke(self):
        with mock.patch.object(console.server, "invoke", return_value={"ok": True}) as invoke:
            for method in sorted(console.ALLOWED):
                status, _, data = self.send("POST", "/api/call",
                                            body={"method": method, "arguments": {}})
                self.assertEqual(status, 200, method)
                self.assertEqual(json.loads(data), {"ok": True}, method)
        self.assertEqual([call.args[1] for call in invoke.call_args_list], sorted(console.ALLOWED))
        self.assertEqual(invoke.call_count, 22)

    def test_dispatch_denies_bridge_tools_outside_the_allowlist(self):
        with mock.patch.object(console.server, "invoke", return_value={"ok": True}) as invoke:
            for method in ("spawn_agent", "select_model", "shutdown", "prune_logs"):
                with self.subTest(method=method):
                    status, _, data = self.send("POST", "/api/call",
                                                body={"method": method, "arguments": {}})
                    self.assertEqual(status, 400)
                    self.assertEqual(json.loads(data)["error"], "invalid_argument")
        invoke.assert_not_called()

    def test_invalid_json_bodies_rejected(self):
        objs = [{"method": "provider_usage"}, {"method": "provider_usage", "arguments": {}, "extra": 1},
                {"method": "shutdown", "arguments": {}}, {"method": 7, "arguments": {}}]
        cases = [b"{not json", b"[1, 2]", b'"text"', b"null"] + [json.dumps(o).encode() for o in objs]
        with mock.patch.object(console.server, "invoke", return_value={}) as invoke:
            for body in cases:
                with self.subTest(body=body):
                    status, _, data = self.send("POST", "/api/call", body=body)
                    self.assertEqual(status, 400)
                    self.assertEqual(json.loads(data)["error"], "invalid_argument")
        invoke.assert_not_called()

    def test_non_json_and_oversized_bodies_rejected(self):
        with mock.patch.object(console.server, "invoke", return_value={}) as invoke:
            status, _, data = self.send("POST", "/api/call", ctype="text/plain", body=b"hello")
            self.assertEqual((status, json.loads(data)["error"]), (415, "json_required"))
            self.assertEqual(self.send("POST", "/api/call", ctype=None, body=b"{}")[0], 415)
            status, _, data = self.send("POST", "/api/call", body=b"", declared=console.MAX_BODY + 1)
            self.assertEqual((status, json.loads(data)["error"]), (413, "body_too_large"))
        invoke.assert_not_called()

    def test_console_html_is_packaged_next_to_module(self):
        page = Path(console.__file__).with_name("console.html")
        self.assertTrue(page.is_file())
        self.assertIn("__CONSOLE_TOKEN__", page.read_text(encoding="utf-8"))

    def test_http_config_lifecycle_roundtrip_is_local_and_persistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "synthetic-config.toml"
            config.write_text(SYNTHETIC_TOML, encoding="utf-8")
            state = root / "state"
            remote_reads = []

            def forbid_remote(*args, **kwargs):
                remote_reads.append(args)
                raise AssertionError("remote usage read during configuration load")

            def make_server():
                manager = console.AgentManager(state_dir=state, config_path=config)
                server = console.make_server(manager, port=0)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                return server, thread

            patches = [mock.patch.object(console.kimi_usage, "read_usage", forbid_remote),
                       mock.patch.object(console.zai, "read_usage", forbid_remote)]
            with patches[0], patches[1]:
                server, thread = make_server()
                try:
                    # Loading the console and the initial snapshot stays local.
                    self.assertEqual(self.send("GET", "/", token=False, server=server)[0], 200)
                    status, _, data = self.send("GET", "/api/state", token=server.token, server=server)
                    self.assertEqual(status, 200)
                    self.assertEqual(remote_reads, [])

                    posts = [("set_concurrency", {"limit": 7}),
                             ("set_kimi_policy", {"usage_guard": True, "stop_remaining_percent": 5,
                                                  "probe_on_unknown": False, "probe_cooldown_minutes": 20}),
                             ("set_kimi_accounts", {"order": ["kimi-code"], "limits": {"kimi-code": 2}}),
                             ("set_zai_policy", {"limit": 4, "usage_guard": False,
                                                 "stop_remaining_percent": 10}),
                             ("set_cost_policy", {"enabled": True, "model": "kimi-code/k3-256k",
                                                  "timezone": "Asia/Shanghai", "peak_start": "09:00",
                                                  "peak_end": "11:00", "peak_weekdays": [1, 2]})]
                    for method, arguments in posts:
                        status, value = self.call(method, arguments, server=server)
                        self.assertEqual(status, 200, f"{method}: {value}")
                    # An enabled cost policy must name one exact configured alias.
                    status, value = self.call("set_cost_policy",
                                              {"enabled": True, "model": "missing/alias"}, server=server)
                    self.assertEqual((status, value["error"]), (400, "invalid_argument"))

                    # Template lifecycle with CAS revisions.
                    status, value = self.call("list_templates", {}, server=server)
                    self.assertEqual(status, 200)
                    default = next(t for t in value["templates"] if t["is_default"])
                    self.assertEqual(default["revision"], 1)
                    status, custom = self.call("create_template",
                                               {"name": "定制 A", "source_template_id": "default"},
                                               server=server)
                    self.assertEqual(status, 200)
                    self.assertEqual(custom["revision"], 1)
                    self.assertFalse(custom["is_default"])
                    saved_payload = {
                        "schema_version": 1,
                        "agent_configs": [{"agent_type": "kimi-code", "enabled": True,
                                           "providers": [{"provider_id": "kimi-code",
                                                          "models": ["kimi-code/k3-256k"]}]}],
                        "limits": {"max_parallel_per_project": 5},
                        "selection": {"prefer_provider_discount_on_equal_fit": False}}
                    status, saved = self.call("save_template",
                                              {"template_id": custom["id"], "payload": saved_payload,
                                               "expected_revision": 1, "name": "定制 A 改"}, server=server)
                    self.assertEqual((status, saved["revision"], saved["name"]), (200, 2, "定制 A 改"))
                    status, conflict = self.call("save_template",
                                                 {"template_id": custom["id"], "payload": saved_payload,
                                                  "expected_revision": 1}, server=server)
                    self.assertEqual((status, conflict["error"]), (400, "revision_conflict"))
                    status, protected = self.call("archive_template",
                                                  {"template_id": "default", "expected_revision": 1},
                                                  server=server)
                    self.assertEqual((status, protected["error"]), (400, "default_template_protected"))

                    # Project lifecycle: registration keeps the root private.
                    status, project = self.call("register_project",
                                                {"name": "Demo", "root": str(root), "host_id": "local"},
                                                server=server)
                    self.assertEqual(status, 200)
                    self.assertNotIn("root", project)
                    status, listing = self.call("list_projects", {}, server=server)
                    self.assertEqual([p["id"] for p in listing["projects"]], [project["id"]])
                    for record in listing["projects"]:
                        self.assertNotIn("root", record)
                    status, bound = self.call("bind_project_template",
                                              {"project_id": project["id"], "template_id": custom["id"],
                                               "expected_binding_revision": 0}, server=server)
                    self.assertEqual((status, bound["template_id"], bound["binding_revision"]),
                                     (200, custom["id"], 1))
                    status, bind_conflict = self.call("bind_project_template",
                                                      {"project_id": project["id"],
                                                       "template_id": None,
                                                       "expected_binding_revision": 0}, server=server)
                    self.assertEqual((status, bind_conflict["error"]), (400, "revision_conflict"))
                    status, in_use = self.call("archive_template",
                                               {"template_id": custom["id"], "expected_revision": 2},
                                               server=server)
                    self.assertEqual((status, in_use["error"]), (400, "template_in_use"))
                    status, resolution = self.call("resolve_project_config",
                                                   {"project_id": project["id"]}, server=server)
                    self.assertEqual(status, 200)
                    self.assertEqual((resolution["template_id"], resolution["template_revision"]),
                                     (custom["id"], 2))
                    self.assertEqual([c["alias"] for c in resolution["candidates"]],
                                     ["kimi-code/k3-256k"])
                    self.assertEqual(resolution["unavailable"], [])
                    self.assertTrue(resolution["ready"])
                    status, scratch = self.call("create_template",
                                                {"name": "临时", "source_template_id": "default"},
                                                server=server)
                    status, archived = self.call("archive_template",
                                                 {"template_id": scratch["id"],
                                                  "expected_revision": 1}, server=server)
                    self.assertEqual((status, archived["archived"]), (200, True))
                finally:
                    server.shutdown()
                    thread.join(timeout=5)
                    server.server_close()

                # A fresh process reloads persisted configuration, still locally.
                server, thread = make_server()
                try:
                    status, _, data = self.send("GET", "/api/state", token=server.token, server=server)
                    self.assertEqual(status, 200)
                    value = json.loads(data)
                    self.assertEqual(value["kimi_cap"], 7)
                    self.assertEqual(value["kimi_policy"]["probe_cooldown_minutes"], 20)
                    self.assertEqual(value["kimi_accounts"]["limits"]["kimi-code"], 2)
                    self.assertEqual(value["zai_policy"]["cap"], 4)
                    self.assertFalse(value["zai_policy"]["usage_guard"])
                    self.assertEqual(value["cost_policy"]["model"], "kimi-code/k3-256k")
                    self.assertTrue(value["cost_policy"]["enabled"])
                    templates = {t["name"]: t for t in value["templates"]}
                    self.assertEqual(templates["定制 A 改"]["revision"], 2)
                    self.assertEqual(value["projects"][0]["template_id"], custom["id"])
                    self.assertEqual(remote_reads, [])
                    self.assertNotIn(FAKE_KIMI_KEY.encode(), data)
                    self.assertNotIn(FAKE_ZAI_KEY.encode(), data)
                    self.assertNotIn(str(root).encode(), data)
                finally:
                    server.shutdown()
                    thread.join(timeout=5)
                    server.server_close()

    def test_release_packaging_rules_cover_console_html(self):
        scripts = str(Path(__file__).resolve().parents[1] / "scripts")
        sys.path.insert(0, scripts)
        try:
            import check_release
        finally:
            sys.path.remove(scripts)
        self.assertTrue(check_release.package_file(Path("plugins/mixed-agents/bridge/console.html")))
        self.assertFalse(check_release.package_file(Path("plugins/mixed-agents/pyproject.toml")))


if __name__ == "__main__":
    unittest.main()
