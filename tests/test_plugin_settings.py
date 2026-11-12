"""Kimi-authored acceptance checks for the GPT-authored plugin settings console entry.

Derived from docs/PLUGIN_CONSOLE_ENTRY_SPEC.md: strict schemas, read-only settings,
loopback-only console launch with existing Host/Origin/token/CSP protections,
per-manager server reuse, shutdown/reopen, browser failure and startup failure.
Temporary state and a fake webbrowser only; no real policy, credentials or network.
"""
import http.client
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "mixed-agents" / "bridge"))
import console  # noqa: E402
import plugin_settings  # noqa: E402
import server  # noqa: E402
from runtime import AgentManager, BridgeError  # noqa: E402

SERVER = Path(server.__file__).resolve()
PLUGIN_JSON = Path(__file__).resolve().parents[1] / "plugins" / "mixed-agents" / "plugin.json"
SETTINGS_METHODS = ("read_plugin_settings", "update_plugin_settings", "open_console")


class RaisingManager:
    """Any attribute access fails: proves the settings path never touches the manager."""

    def __getattr__(self, name):
        raise AssertionError(f"manager must not be accessed, got {name}")


def _patched_browser(result=True):
    return mock.patch.object(plugin_settings.webbrowser, "open", return_value=result)


def _get(origin, path="/", token=None, host=None, origin_header=None, method="GET", timeout=5):
    connection = http.client.HTTPConnection("127.0.0.1", origin.rsplit(":", 1)[1], timeout=timeout)
    headers = {}
    if host is not None:
        headers["Host"] = host
    if origin_header is not None:
        headers["Origin"] = origin_header
    if token:
        headers["X-Console-Token"] = token
    connection.request(method, path, headers=headers)
    response = connection.getresponse()
    body = response.read()
    connection.close()
    return response, body


class ToolSchemaTests(unittest.TestCase):
    def test_read_tool_schema_strict_readonly_with_output_schema(self):
        schema = plugin_settings.tool_schema("read_plugin_settings")
        self.assertEqual(schema["title"], "读取插件设置")
        self.assertEqual(schema["annotations"], {"readOnlyHint": True, "destructiveHint": False,
                                                 "openWorldHint": False})
        self.assertEqual(schema["inputSchema"], {"type": "object", "properties": {},
                                                 "required": [], "additionalProperties": False})
        self.assertEqual(schema["outputSchema"], plugin_settings.READ_OUTPUT_SCHEMA)
        self.assertEqual(schema["outputSchema"]["required"], ["schema", "values"])

    def test_update_tool_schema_requires_empty_set(self):
        schema = plugin_settings.tool_schema("update_plugin_settings")
        self.assertEqual(schema["title"], "更新插件设置")
        self.assertFalse(schema["annotations"]["readOnlyHint"])
        self.assertEqual(schema["inputSchema"]["properties"],
                         {"set": {"type": "object", "properties": {}, "additionalProperties": False}})
        self.assertEqual(schema["inputSchema"]["required"], ["set"])
        self.assertFalse(schema["inputSchema"]["additionalProperties"])
        self.assertEqual(schema["outputSchema"]["required"], ["values"])

    def test_open_console_tool_schema_strict_and_chinese_title(self):
        schema = plugin_settings.tool_schema("open_console")
        self.assertEqual(schema["title"], "打开配置控制台")
        self.assertIn("no inference", schema["description"])
        self.assertEqual(schema["inputSchema"], {"type": "object", "properties": {},
                                                 "required": [], "additionalProperties": False})
        self.assertNotIn("outputSchema", schema)

    def test_read_settings_returns_empty_group_without_side_effects(self):
        with mock.patch.object(plugin_settings.webbrowser, "open") as browser, \
                mock.patch.object(console, "make_server") as make:
            value = plugin_settings.read_settings(RaisingManager())
        browser.assert_not_called()
        make.assert_not_called()
        self.assertEqual(value["schema"], {"type": "object", "properties": {}})
        self.assertEqual(value["values"], {})
        groups = [item for item in value["layout"] if item["kind"] == "group"]
        actions = [item for group in groups for item in group["items"] if item["kind"] == "tool"]
        self.assertEqual([a["tool"] for a in actions], ["open_console"])

    def test_update_settings_accepts_only_empty_set(self):
        self.assertEqual(plugin_settings.update_settings(RaisingManager(), {}), {"values": {}})
        for bad in ({"anything": 1}, None, [], "x"):
            with self.assertRaises(BridgeError) as caught:
                plugin_settings.update_settings(RaisingManager(), bad)
            self.assertEqual(caught.exception.code, "invalid_argument")

    def test_server_rejects_unknown_tool_and_strict_arguments(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = AgentManager(state_dir=tmp, kimi_command=["fake-kimi"],
                                   config_path=Path(tmp) / "config.toml")
            with self.assertRaises(BridgeError):
                server.invoke(manager, "nope", {})
            ghost = RaisingManager()
            with self.assertRaises(BridgeError) as caught:
                server.invoke(ghost, "read_plugin_settings", {"extra": 1})
            self.assertEqual(caught.exception.code, "invalid_argument")
            with self.assertRaises(BridgeError):
                server.invoke(ghost, "open_console", {"anything": 1})
            with self.assertRaises(BridgeError):
                server.invoke(ghost, "update_plugin_settings", {})
            with self.assertRaises(BridgeError):
                server.invoke(ghost, "update_plugin_settings", {"set": {"x": 1}})
            frozen = {str(path): path.read_bytes()
                      for path in sorted(Path(tmp).rglob("*")) if path.is_file()}
        self.assertTrue(frozen)

    def test_tools_call_structured_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = AgentManager(state_dir=tmp, kimi_command=["fake-kimi"],
                                   config_path=Path(tmp) / "config.toml")
            reply = server.handle(manager, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                            "params": {"name": "read_plugin_settings", "arguments": {}}})
            bad = server.handle(manager, {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                          "params": {"name": "update_plugin_settings",
                                                     "arguments": {"set": {"x": 1}}}})
        self.assertFalse(reply["result"]["isError"])
        self.assertEqual(reply["result"]["structuredContent"],
                         plugin_settings.read_settings(manager))
        self.assertTrue(bad["result"]["isError"])
        self.assertEqual(bad["result"]["structuredContent"]["error"], "invalid_argument")


class InitializationTests(unittest.TestCase):
    def test_initialize_advertises_settings_extension_on_supported_versions(self):
        capability = {"readTool": "read_plugin_settings", "updateTool": "update_plugin_settings"}
        for version in ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"):
            reply = server.handle(None, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                         "params": {"protocolVersion": version}})
            result = reply["result"]
            self.assertEqual(result["protocolVersion"], version)
            self.assertEqual(result["capabilities"]["extensions"], {"openai/settings": capability})
            self.assertEqual(result["capabilities"]["experimental"], {"openai/settings": capability})

    def test_initialize_older_fallback_unchanged(self):
        reply = server.handle(None, {"jsonrpc": "2.0", "id": 2, "method": "initialize",
                                     "params": {"protocolVersion": "1999-01-01"}})
        self.assertEqual(reply["result"]["protocolVersion"], "2025-06-18")
        self.assertIn("openai/settings", reply["result"]["capabilities"]["extensions"])
        reply = server.handle(None, {"jsonrpc": "2.0", "id": 3, "method": "initialize"})
        self.assertEqual(reply["result"]["protocolVersion"], "2024-11-05")


class OpenConsoleTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._env = mock.patch.dict(os.environ, KIMI_CODE_HOME=self._tmp.name)
        self._env.start()
        self.addCleanup(self._env.stop)
        self.manager = AgentManager(state_dir=self._tmp.name, kimi_command=["fake-kimi"],
                                    config_path=Path(self._tmp.name) / "config.toml")
        self.addCleanup(self._stop)

    def _stop(self):
        plugin_settings.stop_console(self.manager)

    def _server(self):
        return self.manager._plugin_console[0]

    def test_open_starts_loopback_server_and_requests_browser(self):
        with _patched_browser(True) as browser:
            value = plugin_settings.open_console(self.manager)
        browser.assert_called_once_with(value["url"], new=2)
        self.assertTrue(value["browser_opened"])
        self.assertIn("已请求", value["message"])
        httpd = self._server()
        self.assertTrue(value["url"].startswith("http://127.0.0.1:"))
        self.assertEqual(value["url"], httpd.origin)
        self.assertEqual(httpd.server_address[0], "127.0.0.1")
        self.assertGreater(httpd.server_port, 0)

    def test_url_allocated_at_click_time_not_written_to_manifest(self):
        with _patched_browser(True):
            value = plugin_settings.open_console(self.manager)
        self.assertNotIn(value["url"], PLUGIN_JSON.read_text(encoding="utf-8"))
        self.assertNotIn(str(self._server().server_port), PLUGIN_JSON.read_text(encoding="utf-8"))

    def test_existing_host_origin_token_csp_protections_and_allowlist(self):
        with _patched_browser(True):
            value = plugin_settings.open_console(self.manager)
        url, token = value["url"], self._server().token
        response, _ = _get(url, "/")
        self.assertEqual(response.status, 200)
        self.assertIn("nonce-", response.getheader("Content-Security-Policy"))
        self.assertEqual(response.getheader("Cache-Control"), "no-store")
        response, body = _get(url, "/", host="example.com")
        self.assertEqual(response.status, 403)
        self.assertEqual(json.loads(body)["error"], "invalid_host")
        response, body = _get(url, "/api/state")
        self.assertEqual(response.status, 403)
        self.assertEqual(json.loads(body)["error"], "invalid_token")
        response, _ = _get(url, "/api/state", token=token)
        self.assertEqual(response.status, 200)
        response, body = _get(url, "/api/call",
                              origin_header="http://evil.example", method="POST")
        self.assertEqual(response.status, 403)
        self.assertEqual(json.loads(body)["error"], "invalid_origin")
        for name in SETTINGS_METHODS:
            self.assertNotIn(name, console.ALLOWED)

    def test_policy_and_state_bytes_unchanged_by_open(self):
        def snapshot_bytes():
            return {str(path): path.read_bytes()
                    for path in sorted(Path(self._tmp.name).rglob("*")) if path.is_file()}
        before = snapshot_bytes()
        with _patched_browser(False):
            value = plugin_settings.open_console(self.manager)
        response, _ = _get(value["url"], "/")
        self.assertEqual(response.status, 200)
        self.assertEqual(before, snapshot_bytes())

    def test_concurrent_and_repeated_clicks_reuse_one_server(self):
        real_make_server = console.make_server
        with mock.patch.object(console, "make_server",
                               side_effect=lambda **kw: real_make_server(**kw)) as make, \
                _patched_browser(True):
            with ThreadPoolExecutor(max_workers=8) as pool:
                values = list(pool.map(lambda _: plugin_settings.open_console(self.manager),
                                       range(16)))
            again = plugin_settings.open_console(self.manager)
        self.assertEqual(make.call_count, 1)
        urls = {value["url"] for value in values + [again]}
        self.assertEqual(len(urls), 1)

    def test_dead_server_is_replaced_not_reused(self):
        closed = []
        fake_httpd = mock.Mock(server_close=lambda: closed.append(True))
        dead_thread = threading.Thread()
        self.manager._plugin_console = (fake_httpd, dead_thread)
        with _patched_browser(True):
            value = plugin_settings.open_console(self.manager)
        self.assertEqual(closed, [True])
        self.assertIsNot(self.manager._plugin_console[0], fake_httpd)
        self.assertTrue(value["url"].startswith("http://127.0.0.1:"))

    def test_shutdown_releases_port_and_reopen_gets_fresh_server(self):
        with _patched_browser(True):
            first = plugin_settings.open_console(self.manager)
        port = self._server().server_port
        plugin_settings.stop_console(self.manager)
        self.assertIsNone(getattr(self.manager, "_plugin_console", None))
        with self.assertRaises(OSError):
            _get(first["url"], "/", timeout=1)
        with _patched_browser(True):
            second = plugin_settings.open_console(self.manager)
        self.assertNotEqual(second["url"], first["url"])
        response, _ = _get(second["url"], "/")
        self.assertEqual(response.status, 200)

    def test_serve_shutdown_stops_console(self):
        with _patched_browser(True):
            value = plugin_settings.open_console(self.manager)
        with mock.patch("sys.stdin", io.StringIO("")), \
                mock.patch("sys.stdout", io.StringIO()):
            server.serve(self.manager)
        self.assertIsNone(getattr(self.manager, "_plugin_console", None))
        with self.assertRaises(OSError):
            _get(value["url"], "/", timeout=1)

    def test_browser_refusal_returns_url_and_manual_open_message(self):
        with _patched_browser(False):
            value = plugin_settings.open_console(self.manager)
        self.assertFalse(value["browser_opened"])
        self.assertIn("手动打开", value["message"])
        self.assertTrue(value["url"].startswith("http://127.0.0.1:"))
        response, _ = _get(value["url"], "/")
        self.assertEqual(response.status, 200)

    def test_browser_exception_returns_url_and_manual_open_message(self):
        with mock.patch.object(plugin_settings.webbrowser, "open",
                               side_effect=RuntimeError("no browser")):
            value = plugin_settings.open_console(self.manager)
        self.assertFalse(value["browser_opened"])
        self.assertIn("手动打开", value["message"])
        response, _ = _get(value["url"], "/")
        self.assertEqual(response.status, 200)

    def test_startup_failure_is_safe_and_leaves_no_server(self):
        with mock.patch.object(console, "make_server", side_effect=OSError("no socket")), \
                mock.patch.object(plugin_settings.webbrowser, "open") as browser:
            with self.assertRaises(BridgeError) as caught:
                plugin_settings.open_console(self.manager)
        self.assertEqual(caught.exception.code, "console_start_failed")
        self.assertIsNone(getattr(self.manager, "_plugin_console", None))
        browser.assert_not_called()

    def test_update_via_tools_call_does_not_mutate_state(self):
        def snapshot_bytes():
            return {str(path): path.read_bytes()
                    for path in sorted(Path(self._tmp.name).rglob("*")) if path.is_file()}
        before = snapshot_bytes()
        reply = server.handle(self.manager, {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                                             "params": {"name": "update_plugin_settings",
                                                        "arguments": {"set": {}}}})
        self.assertFalse(reply["result"]["isError"])
        self.assertEqual(reply["result"]["structuredContent"], {"values": {}})
        self.assertEqual(before, snapshot_bytes())


class CliTests(unittest.TestCase):
    def test_cli_open_console_rejected_with_recovery_hint(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, MIXED_AGENTS_STATE_DIR=tmp, KIMI_CODE_HOME=tmp)
            result = subprocess.run([sys.executable, str(SERVER), "call", "open_console", "{}"],
                                    capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(result.returncode, 1)
        value = json.loads(result.stdout)
        self.assertEqual(value["error"], "console_requires_mcp")
        self.assertIn("console.py --open", value["message"])
        self.assertNotIn("url", value)


if __name__ == "__main__":
    unittest.main()
