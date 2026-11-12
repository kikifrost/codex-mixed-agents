"""Usage-refresh-after-recovery acceptance tests for OPEN_SOURCE_READINESS_SPEC.md.

Scope: docs/OPEN_SOURCE_READINESS_SPEC.md ("Usage refresh after recovery").

The only stubbed seam is the external provider HTTP transport
``kimi_usage._fetch_usage_payload``: everything else — credential resolution,
the on-disk usage cache, probe allowance, the explicit ``probe_kimi_usage``
public flow, the guarded ``spawn_agent`` admission and the fake CLI probe
subprocess — is the real production code. Synthetic config/state/credentials
only; no network, no real provider requests.
"""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

BRIDGE = Path(__file__).resolve().parents[1] / "plugins" / "mixed-agents" / "bridge"
sys.path.insert(0, str(BRIDGE))
import kimi_usage  # noqa: E402
import runtime  # noqa: E402
from runtime import AgentManager, BridgeError  # noqa: E402

FAKE = Path(__file__).with_name("fake_cli.py")

ENV_A = "MIXED_AGENTS_PROBE_REFRESH_A"
ENV_B = "MIXED_AGENTS_PROBE_REFRESH_B"

SYNTHETIC_TOML = """# Synthetic Coding-origin installation; no real credentials.
[providers.p]
base_url = "https://api.kimi.com/coding/v1"
api_key_env = "MIXED_AGENTS_PROBE_REFRESH_A"

[models.a]
model = "kimi-for-coding"
provider = "p"

[providers.q]
base_url = "https://api.kimi.com/coding/v1"
api_key_env = "MIXED_AGENTS_PROBE_REFRESH_B"

[models.b]
model = "kimi-for-coding"
provider = "q"
"""


def five_hour_payload(remaining):
    return {"limits": [{"window": {"duration": 300, "timeUnit": "MINUTE"},
                        "detail": {"limit": 100, "used": 100 - remaining,
                                   "remaining": remaining}}]}


class ProbeRefreshTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = self.root / "state"
        self.config = self.root / "config.toml"
        self.config.write_text(SYNTHETIC_TOML, encoding="utf-8")
        os.environ[ENV_A] = "SYNTHETIC_KEY_A"
        os.environ[ENV_B] = "SYNTHETIC_KEY_B"
        self.addCleanup(os.environ.pop, ENV_A, None)
        self.addCleanup(os.environ.pop, ENV_B, None)
        self.mgr = AgentManager(state_dir=str(self.state), config_path=str(self.config),
                                kimi_command=[sys.executable, str(FAKE)])
        # Controlled synthetic usage transport: "fail" -> unavailable reading,
        # "reserve" -> fresh reserve reading, otherwise readable at self.remaining.
        self.transport_mode = {"SYNTHETIC_KEY_A": "ok", "SYNTHETIC_KEY_B": "ok"}
        self.remaining = {"SYNTHETIC_KEY_A": 90, "SYNTHETIC_KEY_B": 90}
        self.transport_calls = []
        self.transport = mock.patch.object(kimi_usage, "_fetch_usage_payload",
                                           side_effect=self.fake_transport)
        self.transport.start()
        self.addCleanup(self.transport.stop)
        # Belt and braces: any accidental real socket connect fails the test.
        self.network = mock.patch("socket.socket.connect",
                                  side_effect=AssertionError("offline test"))
        self.network.start()
        self.addCleanup(self.network.stop)
        self.mgr.set_kimi_policy(usage_guard=True, probe_on_unknown=True,
                                 probe_cooldown_minutes=15)

    def tearDown(self):
        for agent in self.mgr.list_agents()["agents"]:
            if agent["status"] not in runtime.TERMINAL:
                try:
                    self.mgr.interrupt_agent(agent["agent_id"])
                except Exception:
                    pass

    def fake_transport(self, credential):
        self.transport_calls.append(credential)
        if self.transport_mode.get(credential) == "fail":
            raise kimi_usage._Unavailable("network_error")
        remaining = 2 if self.transport_mode.get(credential) == "reserve" else self.remaining[credential]
        return five_hour_payload(remaining)

    def read(self, alias="a"):
        return kimi_usage.read_usage(self.state, self.config, alias=alias)

    def wait_terminal(self, agent_id, deadline=30):
        end = time.monotonic() + deadline
        while time.monotonic() < end:
            agent = self.mgr.get_agent(agent_id)
            if agent["status"] in runtime.TERMINAL:
                return agent
            time.sleep(0.05)
        self.fail("agent %s did not terminate" % agent_id)

    def test_explicit_probe_observes_recovered_value_immediately(self):
        self.transport_mode["SYNTHETIC_KEY_A"] = "fail"
        first = self.read("a")
        self.assertNotEqual(first.get("available"), True)
        self.assertGreaterEqual(len(self.transport_calls), 1)

        self.transport_mode["SYNTHETIC_KEY_A"] = "ok"
        self.remaining["SYNTHETIC_KEY_A"] = 88
        result = self.mgr.probe_kimi_usage("a")
        self.assertTrue(result["probed"])
        # After a bounded recovery the provider usage must be re-read, not
        # masked by the cached unknown reading.
        self.assertTrue(result["usage"].get("available"), json.dumps(result, indent=1))
        self.assertEqual(result["usage"]["remaining_percent"], 88)
        # Ordinary reads observe the same fresh value right away.
        self.assertTrue(self.read("a").get("available"))
        self.assertNotIn("SYNTHETIC_KEY_A", json.dumps(result))

    def test_guarded_spawn_auto_recovers_after_probe(self):
        self.transport_mode["SYNTHETIC_KEY_A"] = "fail"
        self.assertNotEqual(self.read("a").get("available"), True)

        self.transport_mode["SYNTHETIC_KEY_A"] = "ok"
        self.remaining["SYNTHETIC_KEY_A"] = 86
        agent = self.mgr.spawn_agent("scope", "probe-refresh-spawn", "exploration",
                                     json.dumps({"text": "recovered"}), str(self.root), "a")
        agent = self.wait_terminal(agent["agent_id"])
        self.assertEqual(agent["status"], "completed", json.dumps(agent, indent=1))

    def test_ordinary_reads_stay_cached_and_fresh_reserve_still_blocks(self):
        one = self.read("a")
        two = self.read("a")
        self.assertTrue(one.get("available") and two.get("available"))
        calls_after_cached_pair = len(self.transport_calls)
        self.assertEqual(calls_after_cached_pair, 1,
                         "ordinary reads must hit the short-lived cache")
        # A changed transport value must not leak through the ordinary cache.
        self.remaining["SYNTHETIC_KEY_A"] = 40
        self.assertEqual(self.read("a")["remaining_percent"], 90)

        # A fresh (uncached) reserve reading still blocks admission.
        self.transport_mode["SYNTHETIC_KEY_B"] = "reserve"
        with self.assertRaises(BridgeError) as ctx:
            self.mgr.spawn_agent("scope", "probe-refresh-reserve", "exploration",
                                 json.dumps({"text": "blocked"}), str(self.root), "b")
        self.assertEqual(ctx.exception.code, kimi_usage.PROVIDER_QUOTA_RESERVE)


if __name__ == "__main__":
    unittest.main()
