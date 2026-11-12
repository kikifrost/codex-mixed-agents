"""Kimi-authored checks for the GPT-authored MCP transport and CLI."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins/mixed-agents/bridge"))
import server
from runtime import AgentManager

SERVER = Path(server.__file__).resolve()


class ServerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.manager = AgentManager(state_dir=self._tmp.name, kimi_command=["fake-kimi"])
        self.addCleanup(self.manager.close) if hasattr(self.manager, "close") else None

    def test_initialize_negotiation(self):
        supported = {"2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"}
        for version in ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25", "1999-01-01", None):
            params = {} if version is None else {"protocolVersion": version}
            reply = server.handle(self.manager, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": params})
            expected = version if version in supported else "2025-06-18"
            self.assertEqual(reply["result"]["protocolVersion"], expected if version else "2024-11-05")
            self.assertEqual(reply["result"]["serverInfo"]["name"], "mixed-agents")

    def test_tools_list_schemas(self):
        reply = server.handle(self.manager, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tools = {t["name"]: t for t in reply["result"]["tools"]}
        self.assertEqual(sorted(tools), sorted(server.METHODS))
        for tool in tools.values():
            schema = tool["inputSchema"]
            self.assertEqual(schema["type"], "object")
            self.assertFalse(schema["additionalProperties"])
            self.assertTrue(isinstance(schema["required"], list))
            for prop in schema["properties"].values():
                self.assertIn("type", prop)
        spawn = tools["spawn_agent"]["inputSchema"]["properties"]
        self.assertEqual(spawn["role"]["enum"], ["implementation", "tests", "review", "exploration", "research"])
        selected = tools["select_model"]["inputSchema"]["properties"]
        self.assertEqual(selected["complexity"]["enum"], ["simple", "standard", "complex"])

    def test_tools_call_result_and_bad_args(self):
        ok = server.handle(self.manager, {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                          "params": {"name": "list_agents", "arguments": {}}})
        self.assertFalse(ok["result"]["isError"])
        self.assertIn("agents", ok["result"]["structuredContent"])
        text = json.loads(ok["result"]["content"][0]["text"])
        self.assertEqual(text, ok["result"]["structuredContent"])
        bad = server.handle(self.manager, {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                                           "params": {"name": "get_agent", "arguments": {}}})
        self.assertTrue(bad["result"]["isError"])
        self.assertEqual(bad["result"]["structuredContent"]["error"], "invalid_argument")
        unknown = server.handle(self.manager, {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                                               "params": {"name": "nope", "arguments": {}}})
        self.assertTrue(unknown["result"]["isError"])

    def test_unknown_method_invalid_request_and_notifications(self):
        reply = server.handle(self.manager, {"jsonrpc": "2.0", "id": 6, "method": "bogus"})
        self.assertEqual(reply["error"]["code"], -32601)
        reply = server.handle(self.manager, {"jsonrpc": "2.0", "id": 7, "method": "tools/list", "params": []})
        self.assertEqual(reply["error"]["code"], -32602)
        self.assertIsNone(server.handle(self.manager, {"jsonrpc": "2.0", "method": "notifications/initialized"}))
        self.assertIsNone(server.handle(self.manager, {"jsonrpc": "2.0", "method": "tools/list"}))

    def _popen(self):
        env = dict(os.environ, MIXED_AGENTS_STATE_DIR=self._tmp.name, KIMI_CODE_HOME=self._tmp.name)
        return subprocess.Popen([sys.executable, str(SERVER)], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env)

    def test_stdio_transport(self):
        proc = self._popen()
        self.addCleanup(proc.kill)
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}) + "\n")
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        proc.stdin.write("not json\n")
        proc.stdin.flush()
        replies = [json.loads(proc.stdout.readline()) for _ in range(2)]
        self.assertEqual(replies[0]["id"], 1)
        self.assertEqual(replies[1]["error"]["code"], -32700)
        proc.stdin.close()
        proc.wait(timeout=10)
        proc.stdout.close()
        self.assertEqual(proc.returncode, 0)

    def test_cli_with_temp_state(self):
        env = dict(os.environ, MIXED_AGENTS_STATE_DIR=self._tmp.name, KIMI_CODE_HOME=self._tmp.name)
        ok = subprocess.run([sys.executable, str(SERVER), "call", "list_agents", "{}"],
                            capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(ok.returncode, 0)
        self.assertIn("agents", json.loads(ok.stdout))
        bad = subprocess.run([sys.executable, str(SERVER), "call", "get_agent", "{}"],
                             capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(bad.returncode, 1)
        self.assertEqual(json.loads(bad.stdout)["error"], "invalid_argument")


if __name__ == "__main__":
    unittest.main()
