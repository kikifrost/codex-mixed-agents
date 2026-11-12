"""True end-to-end process acceptance for docs/E2E_SPEC.md (MCP process journey).

Drives the real ``server.py`` JSON-RPC stdio transport in a subprocess:
initialize -> tools/list -> tools/call. State is a synthetic temporary
KIMI_CODE_HOME plus MIXED_AGENTS_STATE_DIR, and a PATH shim named ``kimi``
execs the deterministic ``tests/fake_cli.py``. No component of the server,
manager, transport, workers or persistence is mocked or replaced.

The journey registers a managed generic Provider/model with a symbolic fake
``env:`` credential, grants the model in a saved template, registers and
binds a temporary Project, verifies offline doctor readiness, runs a fake
task end to end (result plus captured session), continues it, restarts the
MCP process on the same state and continues again without creating a
replacement task, interrupts a slow fake task and observes a terminal state
with released capacity, and checks that invalid arguments return structured
errors without killing the server.
"""
import json
import os
import queue
import shlex
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BRIDGE = REPO / "plugins" / "mixed-agents" / "bridge"
FAKE = Path(__file__).with_name("fake_cli.py")

PROVIDER_ID = "e2eco"
ALIAS = "e2eco/glm-5.3"
CREDENTIAL_ENV = "MIXED_AGENTS_E2E_KEY"
SCOPE = "e2e-acceptance"


class McpStdioClient:
    """Minimal newline JSON-RPC client over the real server subprocess."""

    def __init__(self, process):
        self.process = process
        self._next_id = 0
        self._pending = {}
        self._lock = threading.Lock()
        self.stderr_tail = ""
        threading.Thread(target=self._read_stderr, daemon=True).start()
        threading.Thread(target=self._read_stdout, daemon=True).start()

    def _read_stderr(self):
        for chunk in iter(self.process.stderr.readline, ""):
            self.stderr_tail = (self.stderr_tail + chunk)[-4000:]

    def _read_stdout(self):
        for line in iter(self.process.stdout.readline, ""):
            try:
                message = json.loads(line)
            except ValueError:
                continue
            ident = message.get("id")
            if ident is None:
                continue
            with self._lock:
                pending = self._pending.pop(ident, None)
            if pending is not None:
                pending.put(message)

    def request(self, method, params=None, timeout=30):
        self._next_id += 1
        ident = self._next_id
        pending = queue.Queue()
        with self._lock:
            self._pending[ident] = pending
        payload = json.dumps({"jsonrpc": "2.0", "id": ident,
                              "method": method, "params": params or {}})
        self.process.stdin.write(payload + "\n")
        self.process.stdin.flush()
        message = pending.get(timeout=timeout)
        if "error" in message:
            raise AssertionError("JSON-RPC error for %s: %s (stderr: %s)" %
                                 (method, message["error"], self.stderr_tail))
        return message["result"]

    def tool(self, name, arguments=None, timeout=30, expect_error=False):
        result = self.request("tools/call", {"name": name,
                                             "arguments": arguments or {}}, timeout)
        value = result["structuredContent"]
        if expect_error:
            assert result["isError"], ("%s should have failed, got: %r" % (name, value))
        else:
            assert not result["isError"], ("%s failed: %r" % (name, value))
        return value

    def wait_agent(self, agent_id, timeout_ms=60000):
        return self.tool("wait_agent", {"agent_ids": [agent_id],
                                        "timeout_ms": timeout_ms}, timeout=timeout_ms / 1000 + 30)

    def close(self):
        if self.process.poll() is not None:
            return
        for stream in (self.process.stdin,):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass
        # communicate() on Python 3.11/3.12 must not flush the closed pipe.
        self.process.stdin = None
        self.process.terminate()
        try:
            self.process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=8)


class EndToEndProcessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="mixed-agents-e2e-")
        cls.addClassCleanup(cls.tmp.cleanup)
        root = Path(cls.tmp.name)
        cls.home = root / "kimi-home"
        cls.home.mkdir()
        (cls.home / "config.toml").write_text(
            "# Synthetic E2E installation; intentionally no models table.\n", encoding="utf-8")
        cls.state = root / "state"
        cls.project_root = root / "project"
        cls.project_root.mkdir()

        shim_dir = root / "shim"
        shim_dir.mkdir()
        shim = shim_dir / "kimi"
        shim.write_text("#!/bin/sh\nexec %s %s \"$@\"\n" %
                        (shlex.quote(sys.executable), shlex.quote(str(FAKE))),
                        encoding="utf-8")
        shim.chmod(shim.stat().st_mode | stat.S_IEXEC)

        cls.env = dict(os.environ)
        cls.env["KIMI_CODE_HOME"] = str(cls.home)
        cls.env["MIXED_AGENTS_STATE_DIR"] = str(cls.state)
        cls.env["PATH"] = str(shim_dir) + os.pathsep + cls.env.get("PATH", "")
        cls.env[CREDENTIAL_ENV] = "e2e-symbolic-credential-value"
        cls.env.pop("MIXED_AGENTS_TEST_PYTHON", None)

        cls.server = None
        cls.rpc = None
        cls.start_server()

    @classmethod
    def start_server(cls):
        cls.server = subprocess.Popen(
            [sys.executable, str(BRIDGE / "server.py")], env=cls.env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1)
        cls.rpc = McpStdioClient(cls.server)
        info = cls.rpc.request("initialize", {"protocolVersion": "2025-06-18",
                                              "capabilities": {}, "clientInfo": {"name": "e2e-tests"}})
        assert info["serverInfo"]["name"] == "mixed-agents", info
        tools = {tool["name"]: tool for tool in cls.rpc.request("tools/list")["tools"]}
        # The journey uses the actual discovered tool surface, not mocks.
        for name in ("create_provider", "create_model", "create_template",
                     "save_template", "register_project", "bind_project_template",
                     "doctor", "spawn_agent", "get_agent", "list_agents",
                     "wait_agent", "followup_task", "interrupt_agent"):
            assert name in tools, "tools/list missing %s" % name
        for name, required in (("create_provider", ["provider_id", "label", "provider_type",
                                                    "endpoint", "protocol", "credential_ref"]),
                               ("create_model", ["alias", "provider_id", "upstream_model",
                                                 "display_name", "context", "capabilities"]),
                               ("spawn_agent", ["scope", "work_unit", "role", "message",
                                                "cwd", "model"])):
            schema_required = tools[name]["inputSchema"]["required"]
            for key in required:
                assert key in schema_required, "%s schema lost required %s" % (name, key)
        return tools

    @classmethod
    def stop_server(cls):
        rpc, cls.rpc = cls.rpc, None
        server, cls.server = cls.server, None
        if rpc is not None:
            rpc.close()
        if server is not None and server.poll() is None:
            server.kill()
            server.wait(timeout=8)
        _, stderr = server.communicate() if server is not None else ("", "")
        return stderr

    @classmethod
    def tearDownClass(cls):
        # Interrupt anything still active so detached workers release their
        # slots and exit before the synthetic state directory is removed.
        if cls.rpc is not None:
            try:
                for agent in cls.rpc.tool("list_agents")["agents"]:
                    if agent["status"] in ("starting", "running", "cancelling", "orphaned"):
                        cls.rpc.tool("interrupt_agent", {"agent_id": agent["agent_id"]},
                                     timeout=30)
            except Exception:
                pass
        cls.stop_server()

    # -- one sequential journey so no failure cascades into an unrelated test --

    @classmethod
    def configure_managed_route(cls):
        rpc = cls.rpc
        provider = rpc.tool("create_provider", {
            "provider_id": PROVIDER_ID,
            "label": "E2E managed connection",
            "provider_type": "generic",
            "endpoint": "https://api.e2e-synth.invalid/v1",
            "protocol": "openai",
            "credential_ref": "env:" + CREDENTIAL_ENV,
        })
        assert provider["id"] == PROVIDER_ID and provider["revision"] == 1, provider
        model = rpc.tool("create_model", {
            "alias": ALIAS,
            "provider_id": PROVIDER_ID,
            "upstream_model": "glm-5.3",
            "display_name": "E2E GLM 5.3",
            "context": 200000,
            "capabilities": ["thinking", "tool_use"],
        })
        assert model["alias"] == ALIAS and model["source"] == "managed", model

        template = rpc.tool("create_template", {"name": "E2E 模板"})
        cls.template_id = template["id"]
        # The synthetic installation has no imported models, so the copied
        # default payload ships empty agent_configs; grant the managed model
        # explicitly through the saved-template payload schema.
        payload = {"schema_version": 1,
                   "agent_configs": [{"agent_type": "kimi-code", "enabled": True,
                                      "providers": [{"provider_id": PROVIDER_ID,
                                                     "models": [ALIAS]}]}],
                   "limits": {"max_parallel_per_project": 3},
                   "selection": {"prefer_provider_discount_on_equal_fit": False}}
        saved = rpc.tool("save_template", {"template_id": cls.template_id,
                                           "payload": payload, "expected_revision": 1})
        assert saved["revision"] == 2, saved

        project = rpc.tool("register_project", {"name": "E2E 项目",
                                                "root": str(cls.project_root),
                                                "host_id": "local"})
        cls.project_id = project["id"]
        bound = rpc.tool("bind_project_template", {"project_id": cls.project_id,
                                                   "template_id": cls.template_id,
                                                   "expected_binding_revision": 0})
        assert bound["binding_revision"] == 1, bound
        resolution = rpc.tool("resolve_project_config", {"project_id": cls.project_id})
        assert resolution["ready"], resolution
        assert any(c["alias"] == ALIAS for c in resolution["candidates"]), resolution

    def test_mcp_process_journey(self):
        rpc = self.rpc
        type(self).configure_managed_route()

        report = rpc.tool("doctor")
        self.assertTrue(report["ready"], json.dumps(report, ensure_ascii=False))
        checks = {c["name"]: c for c in report["checks"]}
        self.assertIn(ALIAS, checks["models"]["supported_aliases"])
        sources = {s["alias"]: s["source"] for s in checks["credentials"]["sources"]}
        self.assertEqual(sources.get(ALIAS), "api_key_env")
        self.assertEqual(report["account_access"], "not_probed")

        spawned = rpc.tool("spawn_agent", {
            "scope": SCOPE, "work_unit": "journey", "role": "implementation",
            "message": "E2E first turn", "cwd": str(self.project_root),
            "model": ALIAS, "project_id": self.project_id, "timeout_seconds": 120,
        })
        self.assertTrue(spawned["agent_id"].startswith("kimi:"), spawned)
        agent_id = spawned["agent_id"]
        waited = rpc.wait_agent(agent_id)
        agent = waited["agents"][0]
        self.assertEqual(agent["status"], "completed", json.dumps(agent, ensure_ascii=False))
        self.assertTrue(agent["resumable"] and agent["session_id"], agent)
        self.assertIn("NEW " + ALIAS, agent["last_result"])
        self.assertIn("E2E first turn", agent["last_result"])
        # The fake CLI echoes KIMI_CODE_HOME: the turn must run against the
        # materialized ephemeral home inside the synthetic state directory.
        self.assertIn(str(self.state), agent["last_result"])

        session_id = agent["session_id"]
        followed = rpc.tool("followup_task", {"agent_id": agent_id,
                                              "message": "E2E second turn"})
        self.assertEqual(followed["agent_id"], agent_id)
        waited = rpc.wait_agent(agent_id)
        agent = waited["agents"][0]
        self.assertEqual(agent["status"], "completed", json.dumps(agent, ensure_ascii=False))
        self.assertEqual(agent["session_id"], session_id, "follow-up must preserve the session")
        self.assertIn("RESUMED " + session_id, agent["last_result"])
        self.assertIn("E2E second turn", agent["last_result"])

        # Restart the MCP process on the same synthetic state.
        type(self).stop_server()
        type(self).start_server()
        rpc = self.rpc
        resumed_view = rpc.tool("get_agent", {"agent_id": agent_id})
        self.assertEqual(resumed_view["status"], "completed", resumed_view)
        self.assertEqual(resumed_view["session_id"], session_id,
                         "restart must preserve the captured session")
        self.assertIn("E2E second turn", resumed_view["last_result"])
        listed = rpc.tool("list_agents", {"scope": SCOPE})
        self.assertEqual([a["agent_id"] for a in listed["agents"]], [agent_id],
                         "restart must not create a replacement task")

        followed = rpc.tool("followup_task", {"agent_id": agent_id,
                                              "message": "E2E third turn after restart"})
        self.assertEqual(followed["agent_id"], agent_id)
        waited = rpc.wait_agent(agent_id)
        agent = waited["agents"][0]
        self.assertEqual(agent["status"], "completed", json.dumps(agent, ensure_ascii=False))
        self.assertIn("E2E third turn after restart", agent["last_result"])
        self.assertEqual(agent["session_id"], session_id,
                         "post-restart third turn must keep the original session")

        # Slow fake task: interrupt it and observe a terminal state plus
        # released shared capacity.
        slow = rpc.tool("spawn_agent", {
            "scope": SCOPE, "work_unit": "cancel-check", "role": "exploration",
            "message": "E2E slow turn\n\n{\"delay\": 60, \"text\": \"slow-result\"}",
            "cwd": str(self.project_root), "model": ALIAS,
            "project_id": self.project_id, "timeout_seconds": 300,
        })
        slow_id = slow["agent_id"]
        self.addCleanup(self._interrupt_quietly, slow_id)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            current = rpc.tool("get_agent", {"agent_id": slow_id})
            if current["status"] == "running":
                break
            time.sleep(0.15)
        self.assertEqual(current["status"], "running", current)
        busy = rpc.tool("list_agents", {"scope": SCOPE})
        self.assertGreaterEqual(busy["kimi_running"], 1, busy)

        interrupted = rpc.tool("interrupt_agent", {"agent_id": slow_id}, timeout=30)
        self.assertEqual(interrupted["status"], "interrupted", interrupted)
        self.assertNotIn(interrupted["status"], ("starting", "running", "cancelling", "orphaned"))
        free = rpc.tool("list_agents", {"scope": SCOPE})
        self.assertEqual(free["kimi_running"], 0, free)
        self.assertEqual(free["kimi_available"], free["kimi_cap"], free)

        # Invalid arguments return structured errors and the server keeps
        # serving subsequent requests.
        missing = rpc.tool("spawn_agent", {"scope": SCOPE, "work_unit": "broken"},
                           expect_error=True)
        self.assertEqual(missing["error"], "invalid_argument", missing)
        unknown = rpc.tool("no_such_tool", {}, expect_error=True)
        self.assertEqual(unknown["error"], "invalid_argument", unknown)
        still_there = rpc.tool("get_agent", {"agent_id": agent_id})
        self.assertEqual(still_there["session_id"], session_id, still_there)
        self.assertIn("E2E third turn after restart", still_there["last_result"])

    def _interrupt_quietly(self, agent_id):
        try:
            if self.rpc is not None:
                self.rpc.tool("interrupt_agent", {"agent_id": agent_id}, timeout=30)
        except Exception:
            pass


if __name__ == "__main__":
    unittest.main()
