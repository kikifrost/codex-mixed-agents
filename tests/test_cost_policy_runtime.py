"""Kimi runtime/MCP acceptance tests for the off-peak cost preference
(docs/COST_POLICY_SPEC.md): selection behavior, MCP surface and recovery CLI.

Disjoint from tests/test_cost_policy.py (pure clock/config validation). Fixed
aware clocks patch cost_policy.evaluate with a wrapper delegating to real
evaluate(policy, aware_fixed_moment); children are tests/fake_cli.py with
temporary state/config/credentials only.
"""
import contextlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock as mock
from datetime import datetime, timezone

BRIDGE = Path(__file__).resolve().parents[1] / "plugins/mixed-agents/bridge"
sys.path.insert(0, str(BRIDGE))
import runtime  # noqa: E402
from runtime import AgentManager, BridgeError  # noqa: E402
import server  # noqa: E402
import cost_policy  # noqa: E402

FAKE = Path(__file__).with_name("fake_cli.py")
TERMINAL = runtime.TERMINAL
KEY_ENV = "TEST_COST_POLICY_RT_KEY"

# 2026-10-17 is a Saturday: 08:00 UTC == 16:00 Asia/Shanghai -> off_peak;
# 2026-10-12 is a Monday: 07:00 UTC == 15:00 Shanghai -> peak.
OFF_PEAK = datetime(2026, 10, 17, 8, 0, 0, tzinfo=timezone.utc)
PEAK = datetime(2026, 10, 12, 7, 0, 0, tzinfo=timezone.utc)

CONFIG = """\
[providers.zai]
api_key_env = "%(key)s"
[models."ark/glm-5.3"]
model = "glm-5.3"
provider = "zai"
[models."zai-coding-plan/glm-5.3"]
model = "glm-5.3"
provider = "zai"
[models.kimi]
model = "k3-256k"
provider = "managed:kimi-code"
max_context_size = 2000000
"""

LATENCY_CONFIG = CONFIG.replace('model = "k3-256k"',
                                'model = "kimi-for-coding"').replace(
    "\nmax_context_size = 2000000", "")

CONFIG_GPT6_ALIAS = """\
[providers.zai]
api_key_env = "%(key)s"
[models."ark/glm-5.3"]
model = "glm-5.3"
provider = "zai"
[models."gpt-6-astra"]
model = "glm-5.3"
provider = "zai"
[models.kimi]
model = "k3-256k"
provider = "managed:kimi-code"
max_context_size = 2000000
"""

CONFIG_WITHOUT_ZAI_TARGET = """\
[providers.zai]
api_key_env = "%(key)s"
[models."ark/glm-5.3"]
model = "glm-5.3"
provider = "zai"
[models.kimi]
model = "k3-256k"
provider = "managed:kimi-code"
max_context_size = 2000000
"""


class CostPolicyRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / "state"
        self.home = Path(self.tmp.name) / "kimi_home"
        self.home.mkdir()
        self.config = self.home / "config.toml"
        self.config.write_text(CONFIG % {"key": KEY_ENV})
        os.environ[KEY_ENV] = "fake-key"
        self.addCleanup(os.environ.pop, KEY_ENV, None)
        self.mgr = AgentManager(state_dir=str(self.state),
                                config_path=str(self.config),
                                kimi_command=[sys.executable, str(FAKE)])

    def tearDown(self):
        for agent in self.mgr.list_agents()["agents"]:
            if agent["status"] not in TERMINAL:
                with contextlib.suppress(Exception):
                    self.mgr.interrupt_agent(agent["agent_id"])

    def at(self, moment):
        real = cost_policy.evaluate
        return mock.patch.object(cost_policy, "evaluate",
                                 lambda p, now=None: real(p, moment))

    def choose(self, unit, **kw):
        kw.setdefault("role", "research")
        kw.setdefault("native_models", ["gpt-6-astra"])
        return self.mgr.select_model("scope", unit, **kw)

    def test_off_peak_prefers_exact_zai_alias_despite_ark_default(self):
        self.mgr.set_cost_policy(True)
        with self.at(OFF_PEAK):
            out = self.choose("u1", complexity="complex")
        self.assertEqual(out["cost_policy"]["period"], "off_peak")
        self.assertEqual(out["selected"]["alias"], "zai-coding-plan/glm-5.3")
        self.assertTrue(out["cost_preference_applied"])
        self.assertIn("cost preference", out["reason"])
        self.assertNotEqual(out["selected"]["alias"], "ark/glm-5.3")
        self.assertEqual([c["score"] for c in out["candidates"]], [50] * 4)

    def test_disabled_policy_is_exposed_and_neutral(self):
        view = self.mgr.model_catalog(["gpt-6-astra"])
        policy = view["cost_policy"]
        self.assertFalse(policy["enabled"])
        self.assertEqual(policy["period"], "disabled")
        self.assertTrue(policy["model_available"])
        defaults = {m["alias"]: m.get("routing_default") for m in view["models"]}
        self.assertTrue(defaults["ark/glm-5.3"])
        self.assertFalse(defaults["zai-coding-plan/glm-5.3"])
        with self.at(OFF_PEAK):
            out = self.choose("u2", complexity="complex")
        self.assertFalse(out["cost_preference_applied"])
        self.assertNotEqual(out["selected"]["alias"], "zai-coding-plan/glm-5.3")

    def test_peak_preserves_family_rotation(self):
        self.mgr.set_cost_policy(True)
        with self.at(PEAK):
            first = self.choose("u3", complexity="complex")
            second = self.choose("u4", complexity="complex")
        self.assertEqual(first["cost_policy"]["period"], "peak")
        self.assertFalse(first["cost_preference_applied"]
                         or second["cost_preference_applied"])
        self.assertEqual(first["selected"]["family"], "glm")
        self.assertEqual(second["selected"]["family"], "kimi")

    def test_simple_latency_sensitive_not_boosted_above_better_fit(self):
        config = Path(self.tmp.name) / "lat" / "config.toml"
        config.parent.mkdir()
        config.write_text(LATENCY_CONFIG % {"key": KEY_ENV})
        mgr = AgentManager(state_dir=str(self.state), config_path=str(config),
                           kimi_command=[sys.executable, str(FAKE)])
        mgr.set_cost_policy(True)
        with self.at(OFF_PEAK):
            out = mgr.select_model("scope", "u5", "implementation",
                                   ["gpt-6-astra"], complexity="simple",
                                   latency_sensitive=True)
        self.assertFalse(out["cost_preference_applied"])
        self.assertEqual(out["selected"]["family"], "kimi")

    def test_context_overflow_bypasses_preference(self):
        self.mgr.set_cost_policy(True)
        with self.at(OFF_PEAK):
            out = self.choose("u6", complexity="complex",
                              context_tokens=1_000_001)
        self.assertFalse(out["cost_preference_applied"])
        self.assertEqual(out["selected"]["family"], "kimi")
        self.assertNotIn("zai-coding-plan/glm-5.3",
                         [c["alias"] for c in out["candidates"]])

    def test_avoid_family_and_recorded_glm_tests_block_glm(self):
        self.mgr.set_cost_policy(True)
        with self.at(OFF_PEAK):
            out = self.choose("u7", complexity="complex", avoid_family="glm")
            self.assertNotEqual(out["selected"]["family"], "glm")
            self.assertFalse(out["cost_preference_applied"])
            self.mgr.record_authorship("scope", "u8", "implementation", "glm",
                                       "glm-author")
            out = self.choose("u8", role="tests", complexity="complex")
        self.assertNotEqual(out["selected"]["family"], "glm")
        self.assertFalse(out["cost_preference_applied"])

    def test_full_capacity_overflows_to_gpt_and_diversity_blocks(self):
        self.mgr.set_concurrency(1)
        self.mgr.set_cost_policy(True)
        self.mgr.spawn_agent("scope", "u-cap", "exploration",
                             json.dumps({"delay": 60}), str(self.state),
                             "kimi", timeout_seconds=120)
        end = time.monotonic() + 15
        while self.mgr._capacity()["kimi_running"] != 1 and time.monotonic() < end:
            time.sleep(0.1)
        self.assertEqual(self.mgr._capacity()["kimi_running"], 1)
        with self.at(OFF_PEAK):
            out = self.choose("u-cap-select", complexity="complex")
        self.assertEqual(out["selected"]["family"], "gpt")
        self.assertFalse(out["cost_preference_applied"])
        self.mgr.record_authorship("scope", "u-div", "implementation", "gpt",
                                   "gpt-author")
        with self.at(OFF_PEAK):
            with self.assertRaises(BridgeError) as ctx:
                self.choose("u-div", role="tests", complexity="complex")
        self.assertEqual(ctx.exception.code, "capacity_exceeded")
        self.assertIsNone(ctx.exception.details.get("fallback"))
        self.assertTrue(ctx.exception.details.get("diversity_blocked"))

    def test_explicit_spawn_requested_kimi_remains_kimi_while_active(self):
        self.mgr.set_cost_policy(True)
        with self.at(OFF_PEAK):
            agent = self.mgr.spawn_agent("scope", "u9", "exploration",
                                         "hello", str(self.state), "kimi",
                                         timeout_seconds=60)
        self.assertEqual(agent["family"], "kimi")
        self.assertEqual(agent["model"], "kimi")
        end = time.monotonic() + 15
        while self.mgr.get_agent(agent["agent_id"])["status"] not in TERMINAL:
            self.assertLess(time.monotonic(), end)
            time.sleep(0.1)
        self.assertIn("kimi", self.mgr.get_agent(agent["agent_id"])["last_result"])

    def test_mcp_schema_and_forged_clock_rejected_without_mutation(self):
        self.assertTrue({'set_cost_policy', 'set_zai_policy', 'provider_usage',
                         'set_kimi_policy', 'probe_kimi_usage'}.issubset(server.METHODS))
        schema = server.tool_schema("set_cost_policy")
        props = schema["inputSchema"]["properties"]
        self.assertEqual(props["enabled"], {"type": "boolean"})
        self.assertIn("enabled", schema["inputSchema"]["required"])
        self.assertEqual(props["peak_weekdays"]["items"],
                         {"type": "integer", "minimum": 1, "maximum": 7})
        for name in ("model", "timezone", "peak_start", "peak_end"):
            self.assertIn("null", props[name]["type"])
        self.assertFalse({"now", "moment"} & set(props))
        response = server.handle(self.mgr, {"jsonrpc": "2.0", "id": 1,
            "method": "tools/call", "params": {"name": "set_cost_policy",
                "arguments": {"enabled": True, "moment": OFF_PEAK.isoformat()}}})
        self.assertTrue(response["result"]["isError"])
        self.assertEqual(response["result"]["structuredContent"]["error"],
                         "invalid_argument")
        self.assertFalse((self.state / "config.json").exists())

    def test_recovery_cli_set_cost_policy_in_isolated_env(self):
        home = Path(self.tmp.name) / "iso-home"
        home.mkdir()
        (home / "config.toml").write_text(CONFIG % {"key": KEY_ENV})
        state = Path(self.tmp.name) / "iso-state"
        env = {**os.environ, "KIMI_CODE_HOME": str(home),
               "MIXED_AGENTS_STATE_DIR": str(state)}
        cli = [sys.executable, str(BRIDGE / "server.py"), "call",
               "set_cost_policy", json.dumps({"enabled": True})]
        run = subprocess.run(cli, capture_output=True, text=True, env=env,
                             timeout=30)
        self.assertEqual(run.returncode, 0, run.stderr)
        out = json.loads(run.stdout)
        self.assertTrue(out["cost_policy"]["enabled"])
        self.assertEqual(out["cost_policy"]["model"], "zai-coding-plan/glm-5.3")
        self.assertIn(out["cost_policy"]["period"], ("peak", "off_peak"))
        stored = json.loads((state / "config.json").read_text())
        self.assertTrue(stored["cost_policy"]["enabled"])
        run = subprocess.run([sys.executable, str(BRIDGE / "server.py")],
                             input=json.dumps({"jsonrpc": "2.0", "id": 9,
                                               "method": "tools/list"}) + "\n",
                             capture_output=True, text=True, timeout=30, env=env)
        self.assertEqual(run.returncode, 0, run.stderr)
        tools = json.loads(run.stdout)["result"]["tools"]
        self.assertTrue({'set_cost_policy', 'set_zai_policy', 'provider_usage',
                         'set_kimi_policy', 'probe_kimi_usage'}.issubset(
                             {tool['name'] for tool in tools}))
        self.assertIn("set_cost_policy", [t["name"] for t in tools])

    def test_active_policy_selects_configured_gpt6_alias_to_glm(self):
        self.config.write_text(CONFIG_GPT6_ALIAS % {"key": KEY_ENV})
        self.mgr = AgentManager(state_dir=str(self.state),
                                config_path=str(self.config),
                                kimi_command=[sys.executable, str(FAKE)])
        self.mgr.set_cost_policy(True, model="gpt-6-astra")
        with self.at(OFF_PEAK):
            out = self.choose("complexresearch", complexity="complex",
                              native_models=["gpt-6-astra"])
        self.assertEqual(out["cost_policy"]["period"], "off_peak")
        self.assertEqual(out["cost_policy"]["model"], "gpt-6-astra")
        self.assertEqual(out["selected"]["alias"], "gpt-6-astra")
        self.assertEqual(out["selected"]["family"], "glm")
        self.assertTrue(out["cost_preference_applied"])
        view = self.mgr.model_catalog(["gpt-6-astra"])
        defaults = {m["alias"]: m.get("routing_default") for m in view["models"]}
        self.assertTrue(defaults["ark/glm-5.3"])
        self.assertFalse(defaults.get("gpt-6-astra"))

    def test_missing_cost_target_reports_unavailable_and_falls_back(self):
        self.mgr.set_cost_policy(True)
        self.config.write_text(CONFIG_WITHOUT_ZAI_TARGET % {"key": KEY_ENV})
        self.mgr = AgentManager(state_dir=str(self.state),
                                config_path=str(self.config),
                                kimi_command=[sys.executable, str(FAKE)])
        with self.at(OFF_PEAK):
            out = self.choose("u-zai-removed", complexity="complex")
        view = self.mgr.model_catalog(["gpt-6-astra"])
        self.assertFalse(view["cost_policy"]["model_available"])
        self.assertFalse(out["cost_preference_applied"])
        self.assertNotEqual(out["selected"]["alias"], "zai-coding-plan/glm-5.3")
        self.assertIn(out["selected"]["family"], ("glm", "kimi"))


if __name__ == "__main__":
    unittest.main()
