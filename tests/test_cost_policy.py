"""Offline tests for the off-peak cost preference helper and shared config.

Kimi-family tests for the GLM-authored implementation of
docs/COST_POLICY_SPEC.md: the pure cost_policy helper (deterministic aware
clocks injected only through ``evaluate``'s ``moment`` argument) and the
persistence of cost_policy in the shared config.json state. No live CLI,
credentials or network are involved; kimi_command points at the inert
tests/fake_cli.py and is never executed here.
"""
import json
import sys
import tempfile
import unittest
import unittest.mock as mock
from datetime import datetime, timezone
from pathlib import Path

BRIDGE = Path(__file__).resolve().parents[1] / "plugins/mixed-agents/bridge"
sys.path.insert(0, str(BRIDGE))
import cost_policy  # noqa: E402
from runtime import AgentManager, BridgeError  # noqa: E402

UTC = timezone.utc
FAKE = Path(__file__).with_name("fake_cli.py")


def at(day, hour, minute=0, second=0):
    """Fixed aware UTC clock; 2026-10-11 is Sunday, 10-12 a Monday,
    10-17 a Saturday and 10-18 a Sunday."""
    return datetime(2026, 10, day, hour, minute, second, tzinfo=UTC)


ENABLED = {"enabled": True}  # normalize fills in the spec defaults


class EvaluateWindowTests(unittest.TestCase):
    def test_default_shanghai_window_boundaries(self):
        # Monday 14:00-18:00 Asia/Shanghai == 06:00-10:00Z; start inclusive,
        # end exclusive.
        for moment, expected in [
            (at(12, 5, 59, 59), "off_peak"),
            (at(12, 6, 0, 0), "peak"),
            (at(12, 9, 59, 59), "peak"),
            (at(12, 10, 0, 0), "off_peak"),
        ]:
            with self.subTest(moment=moment):
                self.assertEqual(cost_policy.evaluate(ENABLED, moment), expected)

    def test_weekends_are_off_peak_even_inside_window(self):
        self.assertEqual(cost_policy.evaluate(ENABLED, at(17, 6, 30)), "off_peak")  # Sat 14:30 SH
        self.assertEqual(cost_policy.evaluate(ENABLED, at(18, 9, 30)), "off_peak")  # Sun 17:30 SH

    def test_local_weekday_across_utc_rollover(self):
        # 2026-10-11 22:30Z is Sunday UTC but already Monday 06:30 in
        # Shanghai; only the policy timezone's weekday and window count.
        sunday_only = {"enabled": True, "timezone": "Asia/Shanghai", "peak_weekdays": [7],
                       "peak_start": "22:00", "peak_end": "23:00"}
        self.assertEqual(cost_policy.evaluate(sunday_only, at(11, 22, 30)), "off_peak")

    def test_custom_utc_window_and_weekday(self):
        saturday = {"enabled": True, "timezone": "UTC", "peak_weekdays": [6],
                    "peak_start": "06:00", "peak_end": "10:00"}
        for moment, expected in [
            (at(17, 5, 59, 59), "off_peak"),
            (at(17, 6, 0, 0), "peak"),
            (at(17, 9, 59, 59), "peak"),
            (at(17, 10, 0, 0), "off_peak"),
            (at(19, 7, 0, 0), "off_peak"),  # Monday is not a configured weekday
        ]:
            with self.subTest(moment=moment):
                self.assertEqual(cost_policy.evaluate(saturday, moment), expected)

    def test_disabled_default_never_constructs_zoneinfo(self):
        with mock.patch.object(cost_policy, "ZoneInfo", side_effect=AssertionError("zoneinfo touched")):
            self.assertEqual(cost_policy.evaluate({}, at(12, 7)), "disabled")
            self.assertEqual(cost_policy.evaluate({"enabled": False, "timezone": "Mars/Olympus"}, at(12, 7)),
                             "disabled")
        with self.assertRaises(cost_policy.CostPolicyError):
            cost_policy.evaluate(ENABLED, datetime(2026, 10, 12, 6, 0, 0))  # naive clock


class NormalizeAndUpdateTests(unittest.TestCase):
    BAD_OBJECTS = [
        None, ["enabled"], {"enabled": "yes"}, {"enabled": 1},
        {"model": ""}, {"model": 5},
        {"peak_start": "14:0"}, {"peak_start": "24:00"}, {"peak_start": 1400},
        {"peak_end": "6:00 pm"},
        {"peak_start": "18:00", "peak_end": "18:00"},
        {"peak_start": "18:00", "peak_end": "14:00"},
        {"peak_weekdays": []}, {"peak_weekdays": [True]}, {"peak_weekdays": [1, 1]},
        {"peak_weekdays": [0]}, {"peak_weekdays": [8]}, {"peak_weekdays": "1"},
        {"timezone": ""}, {"timezone": 5}, {"surplus": True},
    ]

    def test_normalize_rejects_bad_objects(self):
        for raw in self.BAD_OBJECTS:
            with self.subTest(raw=raw):
                with self.assertRaises(cost_policy.CostPolicyError):
                    cost_policy.normalize(raw)

    def test_update_validation_and_none_retention(self):
        with self.assertRaises(cost_policy.CostPolicyError):
            cost_policy.update(None, "yes")  # enabled is strictly bool
        with self.assertRaises(cost_policy.CostPolicyError):
            cost_policy.update(None, False, timezone="Mars/Olympus")  # provided TZ must resolve
        stored = cost_policy.update(None, True)
        self.assertTrue(stored["enabled"])
        kept = cost_policy.update(stored, False, peak_start="09:30")
        self.assertFalse(kept["enabled"])
        self.assertEqual(kept["peak_start"], "09:30")
        self.assertEqual(kept["model"], cost_policy.DEFAULT_MODEL)
        self.assertEqual(kept["timezone"], cost_policy.DEFAULT_TIMEZONE)
        self.assertEqual(kept["peak_weekdays"], [1, 2, 3, 4, 5])


CONFIG_TOML = """\
[models."zai-coding-plan/glm-5.3"]
model = "glm-5.3"
provider = "fake"
"""


class SharedConfigTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.state = root / "state"
        self.state.mkdir()
        self.config_path = root / "config.toml"
        self.config_path.write_text(CONFIG_TOML, encoding="utf-8")
        self.config_json = self.state / "config.json"
        self.config_json.write_text(json.dumps(
            {"kimi_concurrency": 4, "extra_top": {"keep": True}, "cost_policy": {"enabled": False}}),
            encoding="utf-8")

    def manager(self):
        return AgentManager(self.state, config_path=self.config_path,
                            kimi_command=[sys.executable, str(FAKE)])

    def test_enable_exact_alias_and_fresh_manager_roundtrip(self):
        view = self.manager().set_cost_policy(True)["cost_policy"]
        self.assertTrue(view["enabled"])
        self.assertEqual(view["model"], "zai-coding-plan/glm-5.3")
        self.assertEqual(view["timezone"], "Asia/Shanghai")
        self.assertEqual(view["peak_start"], "14:00")
        self.assertEqual(view["peak_end"], "18:00")
        self.assertEqual(view["peak_weekdays"], [1, 2, 3, 4, 5])
        self.assertIn(view["period"], cost_policy.PERIODS)
        self.assertTrue(view["model_available"])
        # A fresh manager over the same shared state reads the same policy.
        self.assertEqual(self.manager()._policy(), cost_policy.normalize(ENABLED))
        with self.assertRaises(BridgeError) as ctx:
            self.manager().set_cost_policy(True, model="zai-coding-plan/glm-9.9")
        self.assertEqual(ctx.exception.code, "invalid_argument")

    def test_none_retains_and_interleaving_preserves(self):
        manager = self.manager()
        manager.set_cost_policy(True, peak_start="09:00")
        kept = manager.set_cost_policy(False)["cost_policy"]
        self.assertFalse(kept["enabled"])
        self.assertEqual(kept["peak_start"], "09:00")  # omitted args retain stored values
        self.assertEqual(kept["model"], "zai-coding-plan/glm-5.3")
        manager.set_concurrency(3)
        manager.set_cost_policy(True)
        settings = json.loads(self.config_json.read_text(encoding="utf-8"))
        self.assertEqual(settings["kimi_concurrency"], 3)
        self.assertTrue(settings["cost_policy"]["enabled"])
        self.assertEqual(settings["extra_top"], {"keep": True})  # unknown keys survive both setters

    def test_invalid_inputs_do_not_mutate_shared_config(self):
        manager = self.manager()  # construction may create state files; snapshot after
        snapshot = self.config_json.read_text(encoding="utf-8")
        before = sorted(p.name for p in self.state.iterdir())
        bad = [
            lambda: manager.set_cost_policy(enabled=1),
            lambda: manager.set_cost_policy(True, timezone="Mars/Olympus"),
            lambda: manager.set_cost_policy(True, peak_start="18:00", peak_end="18:00"),
            lambda: manager.set_cost_policy(True, peak_start="18:00", peak_end="14:00"),
            lambda: manager.set_cost_policy(True, peak_weekdays=[True]),
            lambda: manager.set_cost_policy(True, peak_weekdays=[1, 1]),
            lambda: manager.set_cost_policy(True, peak_weekdays=[]),
            lambda: manager.set_cost_policy(True, model="zai-coding-plan/glm-9.9"),
        ]
        for call in bad:
            with self.subTest(call=call):
                with self.assertRaises(BridgeError) as ctx:
                    call()
                self.assertEqual(ctx.exception.code, "invalid_argument")
        self.assertEqual(self.config_json.read_text(encoding="utf-8"), snapshot)
        self.assertEqual(sorted(p.name for p in self.state.iterdir()), before)

    def test_invalid_stored_policy_fails_closed(self):
        self.config_json.write_text(json.dumps(
            {"kimi_concurrency": 4, "cost_policy": {"enabled": True, "model": None}}), encoding="utf-8")
        for operation in (lambda m: m.set_concurrency(2), lambda m: m.set_cost_policy(False)):
            with self.subTest(operation=operation):
                with self.assertRaises(BridgeError) as ctx:
                    operation(self.manager())
                self.assertEqual(ctx.exception.code, "invalid_config")
        self.config_json.write_text(json.dumps({"cost_policy": None}), encoding="utf-8")
        with self.assertRaises(BridgeError) as ctx:
            self.manager().set_concurrency(2)
        self.assertEqual(ctx.exception.code, "invalid_config")


if __name__ == "__main__":
    unittest.main()
