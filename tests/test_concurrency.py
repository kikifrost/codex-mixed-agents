"""Offline tests for configurable shared Kimi concurrency (docs/SPEC.md #2).

Real child processes via tests/fake_cli.py; temp state roots; no live APIs.
"""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

BRIDGE = Path(__file__).resolve().parents[1] / "plugins/mixed-agents/bridge"
sys.path.insert(0, str(BRIDGE))
import runtime  # noqa: E402
from runtime import AgentManager, BridgeError  # noqa: E402
import server  # noqa: E402

FAKE = Path(__file__).with_name("fake_cli.py")
TERMINAL = runtime.TERMINAL


class ConcurrencyConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / "state"
        self.config = Path(self.tmp.name) / "config.toml"
        self.config.write_text('[models.fake]\nmodel = "kimi-for-coding"\n'
                               'provider = "managed:kimi-code"\n')
        self.mgr = self.manager()

    def tearDown(self):
        for agent in self.mgr.list_agents()["agents"]:
            if agent["status"] not in TERMINAL:
                try:
                    self.mgr.interrupt_agent(agent["agent_id"])
                except Exception:
                    pass
        self.tmp.cleanup()

    def manager(self):
        return AgentManager(state_dir=str(self.state), config_path=str(self.config),
                            kimi_command=[sys.executable, str(FAKE)])

    @property
    def config_file(self):
        return self.state / "config.json"

    def spawn(self, unit, spec=None, role="exploration", model="fake", mgr=None):
        message = json.dumps(spec) if isinstance(spec, dict) else (spec or "hello")
        return (mgr or self.mgr).spawn_agent("scope", unit, role, message,
                                             str(self.state), model)

    def wait_running(self, n, mgr=None, deadline=30):
        mgr = mgr or self.mgr
        end = time.monotonic() + deadline
        while time.monotonic() < end:
            if mgr.model_catalog([])["kimi_running"] == n:
                return
            time.sleep(0.1)
        self.fail("kimi_running never reached %d" % n)

    def wait_status(self, agent_id, want, deadline=30):
        end = time.monotonic() + deadline
        while time.monotonic() < end:
            agent = self.mgr.get_agent(agent_id)
            if agent["status"] in want:
                return agent
            time.sleep(0.1)
        self.fail("agent %s did not reach %s" % (agent_id, want))

    def row(self, agent_id):
        db = sqlite3.connect(self.state / "agents.sqlite3")
        db.row_factory = sqlite3.Row
        try:
            return db.execute("SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone()
        finally:
            db.close()

    @staticmethod
    def alive(pid):
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    def wait_child_pid(self, agent_id, deadline=15):
        end = time.monotonic() + deadline
        while time.monotonic() < end:
            pid = self.row(agent_id)["child_pid"]
            if pid:
                return pid
            time.sleep(0.1)
        self.fail("child_pid never recorded for %s" % agent_id)

    def test_default_cap_five_and_valid_values_persist(self):
        cat = self.mgr.model_catalog([])
        self.assertEqual((cat["kimi_cap"], cat["kimi_available"], cat["kimi_unlimited"]),
                         (5, 5, False))
        two = self.mgr.set_concurrency(2)
        self.assertEqual((two["kimi_cap"], two["kimi_available"], two["kimi_unlimited"]),
                         (2, 2, False))
        self.assertEqual(json.loads(self.config_file.read_text())["kimi_concurrency"], 2)
        self.assertEqual(self.mgr.set_concurrency(7)["kimi_cap"], 7)
        unl = self.mgr.set_concurrency("unlimited")
        self.assertIsNone(unl["kimi_cap"])
        self.assertTrue(unl["kimi_unlimited"])
        self.assertIsNone(unl["kimi_available"])
        # Persisted: a brand-new manager over the same state root sees it.
        self.assertTrue(self.manager().model_catalog([])["kimi_unlimited"])

    def test_invalid_limits_rejected_without_mutation(self):
        self.mgr.set_concurrency(4)
        before = self.config_file.read_text()
        # 0 is valid (unlimited); bools/floats/negatives/strings are not.
        for bad in (True, False, -3, 2.5, "many", "", None):
            with self.assertRaises(BridgeError) as cm:
                self.mgr.set_concurrency(bad)
            self.assertEqual(cm.exception.code, "invalid_argument", bad)
        self.assertEqual(self.config_file.read_text(), before)
        self.assertEqual(self.mgr.model_catalog([])["kimi_cap"], 4)

    def test_zero_normalizes_to_unlimited_and_roundtrips(self):
        reply = self.mgr.set_concurrency(0)
        self.assertIsNone(reply["kimi_cap"])
        self.assertTrue(reply["kimi_unlimited"])
        self.assertIsNone(reply["kimi_available"])
        # Numeric zero is stored as the existing unlimited sentinel.
        self.assertEqual(json.loads(self.config_file.read_text())["kimi_concurrency"],
                         "unlimited")
        # A brand-new manager over the same state root sees it after restart.
        fresh = self.manager()
        cat = fresh.model_catalog([])
        self.assertTrue(cat["kimi_unlimited"])
        self.assertIsNone(cat["kimi_cap"])
        # Legacy string form keeps identical behavior.
        self.mgr.set_concurrency("unlimited")
        self.assertEqual(json.loads(self.config_file.read_text())["kimi_concurrency"],
                         "unlimited")

    def test_hand_edited_stored_zero_loads_as_unlimited(self):
        # A config.json edited outside the MCP APIs may hold a raw numeric 0;
        # it must load as unlimited, not fail closed as malformed.
        self.config_file.write_text(json.dumps({"kimi_concurrency": 0}))
        cat = self.manager().model_catalog([])
        self.assertTrue(cat["kimi_unlimited"])
        self.assertIsNone(cat["kimi_cap"])
        # Bool/float/negative stored values remain malformed.
        for bad in (True, 2.5, -1):
            with self.subTest(bad=bad):
                self.config_file.write_text(json.dumps({"kimi_concurrency": bad}))
                with self.assertRaises(BridgeError) as cm:
                    self.manager().model_catalog([])
                self.assertEqual(cm.exception.code, "invalid_config")
        self.config_file.write_text(json.dumps({"kimi_concurrency": 5}))

    def test_zero_never_cancels_live_jobs_and_admits_beyond_old_cap(self):
        held = [self.spawn("z-%d" % i, {"delay": 30}) for i in range(3)]
        self.wait_running(3)
        # Zero while jobs run: nothing is cancelled, unlimited afterward.
        status = self.mgr.set_concurrency(0)
        self.assertTrue(status["kimi_unlimited"])
        self.assertEqual(status["kimi_running"], 3)
        for agent in held:
            self.assertIn(self.mgr.get_agent(agent["agent_id"])["status"],
                          {"starting", "running"})
        extra = self.spawn("z-extra", {"delay": 30})
        self.assertEqual(self.mgr.model_catalog([])["kimi_running"], 4)
        self.mgr.interrupt_agent(extra["agent_id"])
        for agent in held:
            self.mgr.interrupt_agent(agent["agent_id"])

    def test_malformed_config_fails_closed(self):
        self.config_file.write_text("{not json")
        with self.assertRaises(BridgeError) as cm:
            self.mgr.model_catalog([])
        self.assertEqual(cm.exception.code, "invalid_config")
        with self.assertRaises(BridgeError) as cm:
            self.spawn("bad", {"delay": 1})
        self.assertEqual(cm.exception.code, "invalid_config")
        with self.assertRaises(BridgeError):  # updates fail closed too
            self.mgr.set_concurrency(2)
        self.assertFalse(list(self.state.glob("kimi-slot-*.lock")))
        # A known key with an invalid value is also malformed.
        self.config_file.write_text(json.dumps({"kimi_concurrency": "bogus"}))
        with self.assertRaises(BridgeError) as cm:
            self.spawn("bad2", {"delay": 1})
        self.assertEqual(cm.exception.code, "invalid_config")
        # Leave a valid config so cleanup can read capacity again.
        self.config_file.write_text(json.dumps({"kimi_concurrency": 5}))

    def test_update_visible_to_existing_fresh_manager_and_other_process(self):
        self.mgr.set_concurrency(2)
        self.assertEqual(self.mgr.model_catalog([])["kimi_cap"], 2)
        self.assertEqual(self.manager().model_catalog([])["kimi_cap"], 2)
        child_code = (
            "import sys,json;sys.path.insert(0,sys.argv[1]);import runtime\n"
            "m=runtime.AgentManager(state_dir=sys.argv[2],config_path=sys.argv[3])\n"
            "print(json.dumps({'before':m.model_catalog([])['kimi_cap']}))\n"
            "m.set_concurrency(4)\n"
            "print(json.dumps({'after':m.model_catalog([])['kimi_cap']}))\n")
        child = subprocess.run([sys.executable, "-c", child_code, str(BRIDGE),
                                str(self.state), str(self.config)],
                               capture_output=True, text=True, timeout=60)
        self.assertEqual(child.returncode, 0, child.stderr)
        lines = [json.loads(x) for x in child.stdout.splitlines() if x.strip()]
        self.assertEqual((lines[0]["before"], lines[1]["after"]), (2, 4))
        # Both the long-lived manager and a fresh one see it: no restart needed.
        self.assertEqual(self.mgr.model_catalog([])["kimi_cap"], 4)
        self.assertEqual(self.manager().model_catalog([])["kimi_cap"], 4)

    def test_set_concurrency_is_atomic_and_preserves_unrelated_keys(self):
        self.config_file.write_text(json.dumps(
            {"kimi_concurrency": 5, "note": "keep", "nested": {"a": [1, 2]}}))
        self.mgr.set_concurrency("unlimited")
        data = json.loads(self.config_file.read_text())
        self.assertEqual(data["kimi_concurrency"], "unlimited")
        self.assertEqual(data["note"], "keep")
        self.assertEqual(data["nested"], {"a": [1, 2]})
        self.mgr.set_concurrency(3)
        data = json.loads(self.config_file.read_text())
        self.assertEqual(data["kimi_concurrency"], 3)
        self.assertEqual(data["note"], "keep")
        self.assertFalse(list(self.state.glob(".config-*.tmp")))  # no temp leftovers

    def test_lowered_limit_counts_all_held_slots_and_never_cancels(self):
        self.mgr.set_concurrency("unlimited")
        held = [self.spawn("low-%d" % i, {"delay": 30}) for i in range(3)]
        self.wait_running(3)
        status = self.mgr.set_concurrency(2)
        self.assertEqual((status["kimi_cap"], status["kimi_running"],
                          status["kimi_available"]), (2, 3, 0))
        for agent in held:  # lowering the cap never cancels running jobs
            self.assertIn(self.mgr.get_agent(agent["agent_id"])["status"],
                          {"starting", "running"})
        with self.assertRaises(BridgeError) as cm:
            self.spawn("low-x", {"delay": 1})
        self.assertEqual(cm.exception.code, "capacity_exceeded")
        self.mgr.set_concurrency(4)  # all three locks still count; one seat free
        self.spawn("low-ok", {"delay": 30})
        self.wait_running(4)

    def test_followup_uses_currently_configured_limit(self):
        self.mgr.set_concurrency(1)
        agent = self.spawn("fu", {"delay": 0.2, "text": "one"})
        agent = self.wait_status(agent["agent_id"], TERMINAL)
        self.spawn("fu-block", {"delay": 30})  # occupies the only slot
        self.wait_running(1)
        with self.assertRaises(BridgeError) as cm:
            self.mgr.followup_task(agent["agent_id"], json.dumps({"text": "two"}))
        self.assertEqual(cm.exception.code, "capacity_exceeded")
        self.mgr.set_concurrency(2)
        self.mgr.followup_task(agent["agent_id"], json.dumps({"delay": 0.2, "text": "two"}))
        agent = self.wait_status(agent["agent_id"], TERMINAL)
        self.assertEqual((agent["turn"], agent["status"]), (2, "completed"))

    def test_unlimited_still_rejects_same_family_tests(self):
        self.mgr.set_concurrency("unlimited")
        self.mgr.record_authorship("scope", "u1", "implementation", "gpt", "parent")
        with self.assertRaises(BridgeError) as cm:
            self.spawn("u1", {"delay": 1}, role="tests", model="gpt-6.1-sol")
        self.assertEqual(cm.exception.code, "family_conflict")
        self.spawn("u2", {"delay": 30}, role="implementation")
        with self.assertRaises(BridgeError) as cm:
            self.spawn("u2", {"delay": 1}, role="tests")
        self.assertEqual(cm.exception.code, "family_conflict")

    def test_finite_overflow_fallback_and_diversity_block(self):
        self.mgr.set_concurrency(1)
        self.spawn("full", {"delay": 30})
        self.wait_running(1)
        with self.assertRaises(BridgeError) as cm:
            self.spawn("other", {"delay": 1})  # family-independent unit
        self.assertEqual(cm.exception.code, "capacity_exceeded")
        self.assertEqual(cm.exception.details["fallback"], "gpt")
        self.mgr.record_authorship("scope", "div", "implementation", "gpt", "parent")
        with self.assertRaises(BridgeError) as cm:
            self.spawn("div", {"delay": 1}, role="tests")  # must be Kimi, none free
        self.assertEqual(cm.exception.code, "capacity_exceeded")
        self.assertTrue(cm.exception.details["diversity_blocked"])
        self.assertIsNone(cm.exception.details["fallback"])

    def test_cap_two_blocks_third_real_child_process(self):
        self.mgr.set_concurrency(2)
        a = self.spawn("c2-a", {"delay": 30})
        b = self.spawn("c2-b", {"delay": 30})
        self.wait_running(2)
        for agent in (a, b):  # both are genuine concurrent child processes
            pid = self.wait_child_pid(agent["agent_id"])
            self.assertTrue(self.alive(pid))
        with self.assertRaises(BridgeError) as cm:
            self.spawn("c2-c", {"delay": 1})
        self.assertEqual(cm.exception.code, "capacity_exceeded")
        self.assertEqual(self.mgr.model_catalog([])["kimi_running"], 2)

    def test_unlimited_admits_at_least_seven_simultaneous_jobs(self):
        self.mgr.set_concurrency("unlimited")
        agents = [self.spawn("unl-%d" % i, {"delay": 30}) for i in range(7)]
        self.assertEqual(len({a["agent_id"] for a in agents}), 7)
        self.wait_running(7)
        cat = self.mgr.model_catalog([])
        self.assertTrue(cat["kimi_unlimited"])
        self.assertIsNone(cat["kimi_cap"])
        self.assertIsNone(cat["kimi_available"])

    def test_finite_cap_seven_runs_seven_real_jobs(self):
        self.mgr.set_concurrency(7)
        agents = [self.spawn("cap7-%d" % i, {"delay": 30}) for i in range(7)]
        self.assertEqual(len({a["agent_id"] for a in agents}), 7)
        self.wait_running(7)
        cat = self.mgr.model_catalog([])
        self.assertEqual((cat["kimi_cap"], cat["kimi_available"]), (7, 0))
        with self.assertRaises(BridgeError) as cm:
            self.spawn("cap7-8th", {"delay": 1})
        self.assertEqual(cm.exception.code, "capacity_exceeded")

    def test_cross_process_admission_race_at_cap_two(self):
        self.mgr.set_concurrency(2)
        child_code = (
            "import sys,json;sys.path.insert(0,sys.argv[1]);import runtime\n"
            "from runtime import BridgeError\n"
            "m=runtime.AgentManager(state_dir=sys.argv[2],config_path=sys.argv[3],"
            "kimi_command=[sys.executable,sys.argv[4]])\n"
            "try:\n"
            " m.spawn_agent('race',sys.argv[5],'exploration','hello',sys.argv[2],'fake')\n"
            " print('ADMITTED')\n"
            "except BridgeError as e:\n"
            " print(json.dumps({'code':e.code}))\n")
        procs = [subprocess.Popen([sys.executable, "-c", child_code, str(BRIDGE),
                                   str(self.state), str(self.config), str(FAKE),
                                   "race-%d" % i],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True)
                 for i in range(3)]
        results = [p.communicate(timeout=90) for p in procs]
        for proc, (_, err) in zip(procs, results):
            self.assertEqual(proc.returncode, 0, err)
        admitted = sum("ADMITTED" in out for out, _ in results)
        self.assertEqual(admitted, 2)  # at most (exactly) two win the race
        self.assertEqual(self.mgr.model_catalog([])["kimi_running"], 2)

    def test_mcp_schema_tools_call_and_recovery_cli(self):
        schema = server.tool_schema("set_concurrency")
        limit = schema["inputSchema"]["properties"]["limit"]
        self.assertEqual(limit["oneOf"], [{"type": "integer", "minimum": 0},
                                          {"type": "string", "enum": ["unlimited"]}])
        self.assertEqual(set(limit.get("type", [])), {"integer", "string"})
        self.assertEqual(schema["inputSchema"]["required"], ["limit"])
        listed = server.handle(self.mgr, {"jsonrpc": "2.0", "id": 1,
                                          "method": "tools/list"})["result"]["tools"]
        self.assertIn("set_concurrency", [t["name"] for t in listed])
        ok = server.handle(self.mgr, {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                      "params": {"name": "set_concurrency",
                                                 "arguments": {"limit": 3}}})
        self.assertFalse(ok["result"]["isError"])
        self.assertEqual(ok["result"]["structuredContent"]["kimi_cap"], 3)
        bad = server.handle(self.mgr, {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                       "params": {"name": "set_concurrency",
                                                  "arguments": {"limit": -1}}})
        self.assertTrue(bad["result"]["isError"])
        self.assertEqual(bad["result"]["structuredContent"]["error"], "invalid_argument")
        # Numeric zero through MCP normalizes to unlimited and persists.
        zero = server.handle(self.mgr, {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                                        "params": {"name": "set_concurrency",
                                                   "arguments": {"limit": 0}}})
        self.assertFalse(zero["result"]["isError"])
        self.assertTrue(zero["result"]["structuredContent"]["kimi_unlimited"])
        self.assertEqual(json.loads(self.config_file.read_text())["kimi_concurrency"],
                         "unlimited")
        env = dict(os.environ, MIXED_AGENTS_STATE_DIR=str(self.state))
        cli = subprocess.run([sys.executable, str(BRIDGE / "server.py"), "call",
                              "set_concurrency", '{"limit": 6}'],
                             capture_output=True, text=True, timeout=60, env=env)
        self.assertEqual(cli.returncode, 0, cli.stderr)
        self.assertEqual(json.loads(cli.stdout.strip())["kimi_cap"], 6)
        bad_cli = subprocess.run([sys.executable, str(BRIDGE / "server.py"), "call",
                                  "set_concurrency", '{"limit": -1}'],
                                 capture_output=True, text=True, timeout=60, env=env)
        self.assertEqual(bad_cli.returncode, 1)
        self.assertEqual(json.loads(bad_cli.stdout.strip())["error"], "invalid_argument")
        # The long-lived manager sees the CLI update without any restart.
        self.assertEqual(self.mgr.model_catalog([])["kimi_cap"], 6)


if __name__ == "__main__":
    unittest.main()
