"""Offline lifecycle tests for the GPT-authored runtime, using real child processes."""
import contextlib
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest

BRIDGE = Path(__file__).resolve().parents[1] / "plugins/mixed-agents/bridge"
sys.path.insert(0, str(BRIDGE))
import runtime  # noqa: E402
from runtime import AgentManager, BridgeError  # noqa: E402

FAKE = Path(__file__).with_name("fake_cli.py")
TERMINAL = runtime.TERMINAL


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / "state"
        self.config = Path(self.tmp.name) / "config.toml"
        self.config.write_text('[models.fake]\nmodel = "kimi-for-coding"\n'
                               'provider = "managed:kimi-code"\n')
        self.mgr = self.manager()

    def tearDown(self):
        for agent in (self.mgr.list_agents()["agents"]):
            if agent["status"] not in TERMINAL:
                try:
                    self.mgr.interrupt_agent(agent["agent_id"])
                except Exception:
                    pass
        self.tmp.cleanup()

    def manager(self):
        return AgentManager(state_dir=str(self.state), config_path=str(self.config),
                            kimi_command=[sys.executable, str(FAKE)])

    def spawn(self, unit, spec=None, role="exploration", mgr=None, **kw):
        message = json.dumps(spec) if isinstance(spec, dict) else (spec or "hello")
        return (mgr or self.mgr).spawn_agent("scope", unit, role, message,
                                             str(self.state), "fake", **kw)

    def wait_status(self, agent_id, want, deadline=30, mgr=None):
        mgr = mgr or self.mgr
        end = time.monotonic() + deadline
        while time.monotonic() < end:
            agent = mgr.get_agent(agent_id)
            if agent["status"] in want:
                return agent
            time.sleep(0.1)
        self.fail("agent %s did not reach %s, last=%s" % (agent_id, want, agent["status"]))

    def row(self, agent_id):
        db = sqlite3.connect(self.state / "agents.sqlite3")
        db.row_factory = sqlite3.Row
        try:
            return db.execute("SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone()
        finally:
            db.close()

    def test_hard_cap_five_sixth_overflow_and_cross_process_slots(self):
        agents = [self.spawn("cap-%d" % i, {"delay": 30}) for i in range(5)]
        self.assertEqual(self.mgr.model_catalog([])["kimi_available"], 0)
        child_code = (
            "import sys, json; sys.path.insert(0, sys.argv[1]);"
            "import runtime; from runtime import BridgeError;"
            "m = runtime.AgentManager(state_dir=sys.argv[2],"
            "kimi_command=[sys.executable, sys.argv[3]], config_path=sys.argv[4])\n"
            "try:\n"
            " m.spawn_agent('cross', 'unit', 'exploration', 'hello', sys.argv[2], 'fake')\n"
            " print('NO_ERROR')\n"
            "except BridgeError as e:\n"
            " print(json.dumps({'code': e.code, 'details': e.details}))\n")
        child = subprocess.run([sys.executable, "-c", child_code,
                                str(BRIDGE), str(self.state), str(FAKE), str(self.config)],
                               capture_output=True, text=True, timeout=60)
        self.assertEqual(child.returncode, 0)
        self.assertIn('"capacity_exceeded"', child.stdout)
        self.assertIn('"fallback": "gpt"', child.stdout.replace("'", '"'))
        try:
            with self.assertRaises(BridgeError) as cm:
                self.spawn("cap-6", {"delay": 1})
            self.assertEqual(cm.exception.code, "capacity_exceeded")
            self.assertEqual(cm.exception.details["fallback"], "gpt")
        finally:
            for agent in agents:
                self.mgr.interrupt_agent(agent["agent_id"])

    def test_family_conflicts_both_directions_and_diversity_outranks_overflow(self):
        self.mgr.record_authorship("scope", "u1", "implementation", "gpt", "parent")
        # GPT implementation -> Kimi tests allowed (cross-family required).
        self.assertEqual(self.mgr.select_model("scope", "u1", "tests",
                                                  ["gpt-6.1-sol"])["selected"]["family"], "kimi")
        # GPT implementation -> GPT tests rejected (same family).
        with self.assertRaises(BridgeError) as cm:
            self.mgr.spawn_agent("scope", "u1", "tests", "work", str(self.state), "gpt-6.1-sol")
        self.assertEqual(cm.exception.code, "family_conflict")
        # Kimi implementation -> GPT tests allowed (cross-family required).
        kimi_impl = self.spawn("u2", {"delay": 30}, role="implementation")
        try:
            self.assertEqual(self.mgr.select_model("scope", "u2", "tests",
                                                      ["gpt-6.1-sol"])["selected"]["family"], "gpt")
            # Kimi implementation -> Kimi tests rejected (same family).
            with self.assertRaises(BridgeError) as cm:
                self.spawn("u2", "work", role="tests")
            self.assertEqual(cm.exception.code, "family_conflict")
            others = [self.spawn("fill-%d" % i, {"delay": 30}) for i in range(4)]
            try:
                # Unit u1 needs Kimi tests (GPT already implemented it); with all
                # five slots held this waits instead of violating diversity.
                with self.assertRaises(BridgeError) as cm:
                    self.mgr.select_model("scope", "u1", "tests", ["gpt-6.1-sol"])
                self.assertEqual(cm.exception.code, "capacity_exceeded")
                self.assertTrue(cm.exception.details["diversity_blocked"])
                self.assertIsNone(cm.exception.details["fallback"])
                with self.assertRaises(BridgeError) as cm:
                    self.spawn("u1", "work", role="tests")
                self.assertEqual(cm.exception.code, "capacity_exceeded")
                self.assertTrue(cm.exception.details["diversity_blocked"])
                self.assertIsNone(cm.exception.details["fallback"])
            finally:
                for agent in others:
                    self.mgr.interrupt_agent(agent["agent_id"])
        finally:
            self.mgr.interrupt_agent(kimi_impl["agent_id"])

    def test_native_reservation_lifecycle_and_release(self):
        spawned = self.mgr.spawn_agent("scope", "native-1", "review", "task",
                                       str(self.state), "gpt-6.1-sol")
        reservation = spawned["reservation_id"]
        self.assertFalse(spawned["dispatched"])
        self.assertEqual(spawned["native_action"]["tool"], "spawn_agent")
        self.assertIn(str(self.state), spawned["native_action"]["arguments"]["message"])
        pending = self.mgr.list_agents()["pending_native_reservations"]
        self.assertEqual([p["reservation_id"] for p in pending], [reservation])
        self.assertTrue(self.mgr.release_native_reservation(reservation)["released"])
        registered = self.mgr.register_native_agent("scope", "native-1", "review",
                                                    "gpt-6.1-sol", "native-agent-1")
        self.assertEqual(registered["status"], "native")
        self.assertFalse(self.mgr.list_agents()["pending_native_reservations"])
        with self.assertRaises(BridgeError):  # confirmed records are never releasable
            self.mgr.release_native_reservation("native-agent-1")
        with self.assertRaises(BridgeError):  # conflicting identity fails
            self.mgr.register_native_agent("scope", "other-unit", "review",
                                           "gpt-6.1-sol", "native-agent-1")
        again = self.mgr.register_native_agent("scope", "native-1", "review",
                                               "gpt-6.1-sol", "native-agent-1")
        self.assertEqual(again["agent_id"], "native-agent-1")

    def test_followup_resumes_same_session_and_idle_message_order(self):
        agent = self.spawn("follow", {"delay": 0.3, "text": "alpha"})
        agent = self.wait_status(agent["agent_id"], TERMINAL)
        session = agent["session_id"]
        self.assertIn("alpha", agent["last_result"])
        self.assertIn("NEW", agent["last_result"])
        self.assertTrue(agent["resumable"])
        delivery = self.mgr.send_message(agent["agent_id"], "beta")
        self.assertEqual(delivery["delivery"], "queued_until_followup")
        self.assertEqual(self.mgr.get_agent(agent["agent_id"])["queued_message_count"], 1)
        running = self.spawn("running-msg", {"delay": 5})
        self.wait_status(running["agent_id"], {"running"})
        try:
            delivery = self.mgr.send_message(running["agent_id"], "mid-turn")
            self.assertEqual(delivery["delivery"], "next_turn_boundary")
        finally:
            self.mgr.interrupt_agent(running["agent_id"])
        self.mgr.followup_task(agent["agent_id"], json.dumps({"text": "gamma"}))
        agent = self.wait_status(agent["agent_id"], TERMINAL)
        self.assertEqual(agent["session_id"], session)  # same Kimi session, -S path
        self.assertEqual(agent["turn"], 2)
        self.assertIn("RESUMED %s" % session, agent["last_result"])
        self.assertLess(agent["last_result"].index("beta"), agent["last_result"].index("gamma"))

    def test_timeout_failure_and_long_stderr(self):
        timed_out = self.spawn("timeout", {"delay": 30}, timeout_seconds=1)
        timed_out = self.wait_status(timed_out["agent_id"], TERMINAL)
        self.assertEqual(timed_out["status"], "failed")
        self.assertIn("timed out", timed_out["error"])
        failed = self.spawn("failure", {"exit": 3, "stderr": "E" * 5000})
        failed = self.wait_status(failed["agent_id"], TERMINAL)
        self.assertEqual(failed["status"], "failed")
        self.assertIn("exited 3", failed["error"])
        self.assertLess(len(failed["error"]), 5000)  # bounded tail
        noisy = self.spawn("stderr", {"delay": 0.1, "stderr": {"char": "S", "repeat": 200000},
                                      "text": "clean"})
        noisy = self.wait_status(noisy["agent_id"], TERMINAL)
        self.assertEqual(noisy["status"], "completed")  # drained without deadlock
        self.assertIn("clean", noisy["last_result"])

    def test_cancellation_kills_descendants_and_reuses_slot(self):
        agent = self.spawn("cancel", {"descendant": 30, "delay": 30})
        self.wait_status(agent["agent_id"], {"running"})
        child_pid = self.row(agent["agent_id"])["child_pid"]
        agent = self.mgr.interrupt_agent(agent["agent_id"])
        self.assertEqual(agent["status"], "interrupted")
        self.assertFalse(self._group_alive(child_pid))
        replacement = self.spawn("replacement", {"text": "reused"})
        replacement = self.wait_status(replacement["agent_id"], TERMINAL)
        self.assertEqual(replacement["status"], "completed")

    def test_dead_worker_recovery_preserves_session_across_restart(self):
        agent = self.spawn("dead", {"delay": 30})
        agent_id = agent["agent_id"]
        self.wait_status(agent_id, {"running"})
        # Wait until the worker has recorded both the child process and session id.
        end = time.monotonic() + 10
        while time.monotonic() < end:
            row = self.row(agent_id)
            if row["child_pid"] and row["session_id"]:
                break
            time.sleep(0.1)
        self.assertTrue(row["child_pid"], "child_pid was not recorded")
        self.assertTrue(row["session_id"], "session_id was not recorded")
        worker_pid, child_pid, session = row["worker_pid"], row["child_pid"], row["session_id"]
        os.kill(worker_pid, signal.SIGKILL)  # worker dies while Kimi still holds the slot
        time.sleep(1.5)
        self.assertEqual(self.mgr.get_agent(agent_id)["status"], "orphaned")
        os.killpg(child_pid, signal.SIGKILL)  # now nothing holds the slot
        restarted = self.manager()  # a fresh manager (MCP restart) reads the same state
        recovered = self.wait_status(agent_id, TERMINAL, mgr=restarted)
        self.assertEqual(recovered["status"], "interrupted")
        self.assertEqual(recovered["session_id"], session)
        self.assertTrue(recovered["resumable"])
        restarted.followup_task(agent_id, json.dumps({"text": "back"}))
        resumed = self.wait_status(agent_id, TERMINAL, mgr=restarted)
        self.assertEqual(resumed["status"], "completed")
        self.assertIn("RESUMED %s" % session, resumed["last_result"])

    @staticmethod
    def _group_alive(pgid):
        out = subprocess.run(["ps", "-axo", "pgid=,stat="], capture_output=True, text=True).stdout
        for line in out.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[0] == str(pgid) and not parts[1].startswith("Z"):
                return True
        return False

    def test_stale_recovery_reader_does_not_clobber_new_turn(self):
        # Completed agent with a captured session.
        agent = self.spawn("stale-recovery", {"delay": 0, "text": "done"})
        agent = self.wait_status(agent["agent_id"], TERMINAL)
        agent_id = agent["agent_id"]
        session = agent["session_id"]
        self.assertTrue(session)

        # Create a genuinely dead pid for the stale worker identity.
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.1)"])
        proc.wait(timeout=5)
        dead_pid = proc.pid

        # Revert the row to an old, stale "running" snapshot.
        old_row = self.row(agent_id)
        stale_updated = time.time() - 5
        with contextlib.closing(sqlite3.connect(self.state / "agents.sqlite3")) as db:
            db.execute("""UPDATE agents SET status='running',worker_pid=?,
                          worker_identity='stale-id',turn=1,updated=?,slot=?
                          WHERE id=?""",
                       (dead_pid, stale_updated, old_row["slot"], agent_id))
            db.commit()

        stale_read = threading.Event()
        release_stale = threading.Event()
        real_identity = runtime._identity
        blocked = {"done": False}
        reader = None

        def fake_identity(pid):
            if pid == dead_pid:
                if not blocked["done"]:
                    blocked["done"] = True
                    stale_read.set()
                    release_stale.wait(timeout=30)
                return None
            return real_identity(pid)

        runtime._identity = fake_identity
        try:
            reader = threading.Thread(target=self.mgr.get_agent, args=(agent_id,))
            reader.start()
            self.assertTrue(stale_read.wait(timeout=10),
                            "stale reader did not inspect dead worker")

            # Main thread recovers and starts a new delayed turn while the
            # stale reader is paused before its CAS update.
            self.mgr.followup_task(agent_id, json.dumps({"delay": 5, "text": "resumed"}))

            release_stale.set()
            reader.join(timeout=10)
            self.assertFalse(reader.is_alive(), "stale reader did not finish")

            current = self.mgr.get_agent(agent_id)
            self.assertIn(current["status"], {"starting", "running"})
            self.assertEqual(current["session_id"], session)
            self.assertTrue(current["resumable"])
            self.assertEqual(self.mgr.list_agents()["kimi_running"], 1)
            self.assertEqual(self.mgr.model_catalog([])["kimi_available"], runtime.CAP - 1)
        finally:
            runtime._identity = real_identity
            release_stale.set()
            if reader is not None and reader.is_alive():
                reader.join(timeout=5)

    def test_equal_fit_rotates_families(self):
        first = self.mgr.select_model("scope", "rotate", "implementation", ["gpt-6.1-sol"])
        second = self.mgr.select_model("scope", "rotate", "implementation", ["gpt-6.1-sol"])
        self.assertNotEqual(first["selected"]["family"], second["selected"]["family"])
        self.assertEqual({first["selected"]["family"], second["selected"]["family"]},
                         {"kimi", "gpt"})

    def test_concurrent_followup_creates_only_one_worker(self):
        agent = self.spawn("concurrent", {"delay": 0, "text": "first"})
        agent_id = agent["agent_id"]
        self.wait_status(agent_id, TERMINAL)
        barrier = threading.Barrier(2)
        errors = []

        def call():
            try:
                barrier.wait(timeout=5)
                self.mgr.followup_task(agent_id, json.dumps({"delay": 2, "text": "second"}))
            except Exception as exc:
                errors.append(exc)

        t1 = threading.Thread(target=call)
        t2 = threading.Thread(target=call)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        self.assertFalse(errors, errors)
        # Only one worker should be running; the other request queued.
        self.assertEqual(self.mgr.list_agents()["kimi_running"], 1)
        final = self.wait_status(agent_id, TERMINAL)
        self.assertEqual(final["turn"], 2)


if __name__ == "__main__":
    unittest.main()
