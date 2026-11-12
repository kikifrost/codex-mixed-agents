#!/usr/bin/env python3
"""Regression tests for Kimi alias routing and model-switch followups."""
import inspect
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "plugins" / "mixed-agents"))
from bridge import runtime  # noqa: E402

MANAGED = "k3-managed"
CUSTOM = "k3-custom"


class ModelSwitchTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_dir = Path(self._tmp.name) / "state"
        self.state_dir.mkdir()
        self.config_path = Path(self._tmp.name) / "config.toml"
        self.config_path.write_text(
            "[models.k3-managed]\n"
            'model = "k3"\n'
            'provider = "managed:kimi-code"\n'
            'api_key = "__MANAGED_KEY__"\n'
            'base_url = "https://managed.example"\n\n'
            "[models.k3-custom]\n"
            'model = "k3"\n'
            'provider = "api"\n'
            'api_key = "__CUSTOM_KEY__"\n'
            'base_url = "https://api.example"\n',
            encoding="utf-8",
        )
        self.fake_cli = str(PROJECT_ROOT / "tests" / "fake_cli.py")
        self.manager = runtime.AgentManager(
            state_dir=str(self.state_dir),
            config_path=str(self.config_path),
            kimi_command=[sys.executable, self.fake_cli],
        )
        self.manager.set_concurrency(2)

    def tearDown(self):
        for agent in self.manager.list_agents().get("agents", []):
            if agent["family"] == "kimi" and agent["status"] in runtime.ACTIVE:
                self.manager.interrupt_agent(agent["agent_id"])

    def _wait(self, agent_id, timeout_ms=5000):
        return self.manager.wait_agent([agent_id], timeout_ms=timeout_ms)

    def _wait_running(self, agent_id, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            agent = self.manager.get_agent(agent_id)
            if agent["status"] == "running" and agent["queued_message_count"] == 0:
                return agent
            time.sleep(0.01)
        agent = self.manager.get_agent(agent_id)
        self.assertEqual(agent["status"], "running")
        self.assertEqual(agent["queued_message_count"], 0)
        return agent

    def test_catalog_routing_and_observation_isolation(self):
        with self.manager._db() as db:
            db.execute(
                "INSERT INTO observations(model,failures) VALUES (?,1) "
                "ON CONFLICT(model) DO UPDATE SET failures=failures+1",
                (MANAGED,),
            )
        catalog = self.manager.model_catalog(native_models=["gpt-6-sol"])
        aliases = {m["alias"]: m for m in catalog["models"] if m["family"] == "kimi"}
        self.assertEqual(set(aliases), {MANAGED, CUSTOM})
        defaults = [m for m in aliases.values() if m.get("routing_default")]
        self.assertEqual(defaults, [aliases[MANAGED]])
        self.assertEqual(aliases[CUSTOM]["observation"]["failures"], 0)
        self.assertEqual(aliases[MANAGED]["observation"]["failures"], 1)
        dumped = json.dumps(catalog)
        self.assertNotIn("__MANAGED_KEY__", dumped)
        self.assertNotIn("__CUSTOM_KEY__", dumped)
        self.assertIsNone(
            inspect.signature(self.manager.followup_task).parameters["model"].default
        )

    def test_followup_switches_alias_and_reuses_session(self):
        agent_id = self.manager.spawn_agent(
            "scope", "unit1", "implementation", "hello",
            str(self.state_dir), CUSTOM, timeout_seconds=30,
        )["agent_id"]
        self._wait(agent_id)
        first = self.manager.get_agent(agent_id)
        self.assertEqual(first["model"], CUSTOM)
        self.assertTrue(first["session_id"])

        self.manager.followup_task(agent_id, "resume please", model=MANAGED)
        self._wait(agent_id)
        second = self.manager.get_agent(agent_id)
        self.assertIn("RESUMED", second["last_result"])
        self.assertIn(MANAGED, second["last_result"])
        self.assertIn(first["session_id"], second["last_result"])
        with self.manager._db() as db:
            claim = db.execute(
                "SELECT model FROM claims WHERE owner=?", (agent_id,)
            ).fetchone()
        self.assertEqual(claim["model"], MANAGED)

        self.manager.followup_task(agent_id, "again")
        self._wait(agent_id)
        third = self.manager.get_agent(agent_id)
        self.assertIn(MANAGED, third["last_result"])

    def test_idle_invalid_model_is_rejected_cleanly(self):
        agent_id = self.manager.spawn_agent(
            "scope", "unit2", "implementation", "hi",
            str(self.state_dir), CUSTOM, timeout_seconds=30,
        )["agent_id"]
        self._wait(agent_id)
        before = self.manager.get_agent(agent_id)
        cap_before = self.manager._capacity()
        with self.assertRaises(runtime.BridgeError) as ctx:
            self.manager.followup_task(agent_id, "nope", model="not-an-alias")
        # Stage 3 validates the saved-template allowlist first: a model the
        # template does not allow is model_not_allowed, never substituted.
        self.assertEqual(ctx.exception.code, "model_not_allowed")
        after = self.manager.get_agent(agent_id)
        self.assertEqual(after["model"], before["model"])
        self.assertEqual(after["queued_message_count"], 0)
        self.assertEqual(self.manager._capacity(), cap_before)

    def test_active_model_change_busy_and_native_override_rejected(self):
        agent_id = self.manager.spawn_agent(
            "scope", "unit3", "implementation", '{"delay": 3}\n\nhold',
            str(self.state_dir), CUSTOM, timeout_seconds=30,
        )["agent_id"]
        self._wait_running(agent_id)
        cap_before = self.manager._capacity()
        with self.assertRaises(runtime.BridgeError) as ctx:
            self.manager.followup_task(agent_id, "switch", model=MANAGED)
        self.assertEqual(ctx.exception.code, "busy")
        agent = self.manager.get_agent(agent_id)
        self.assertEqual(agent["model"], CUSTOM)
        self.assertEqual(agent["queued_message_count"], 0)
        self.assertEqual(self.manager._capacity(), cap_before)

        native_id = "gpt:test-123"
        self.manager.register_native_agent(
            "scope", "unit3-gpt", "tests", "gpt-6-sol", native_id
        )
        with self.assertRaises(runtime.BridgeError) as ctx:
            self.manager.followup_task(native_id, "switch", model="gpt-6-astra")
        self.assertEqual(ctx.exception.code, "unsupported_model_change")


if __name__ == "__main__":
    unittest.main()
