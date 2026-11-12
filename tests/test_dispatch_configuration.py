"""Independent acceptance tests for Stage 3 Kimi Code dispatch
(docs/TEMPLATE_STAGE3_SPEC.md): Project/permission context, allowlist gating,
native GPT boundary, discount mapping, per-Project concurrency, immutable
turn snapshots and leak-free public views.

Real child processes via tests/fake_cli.py; temporary state/config; no live
APIs. Derived from the behavior contract only; dispatch implementation is a
different model family than these tests.
"""
import contextlib
import copy
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import types
import unittest
import unittest.mock as mock
from datetime import datetime, timezone

BRIDGE = Path(__file__).resolve().parents[1] / "plugins/mixed-agents/bridge"
sys.path.insert(0, str(BRIDGE))

# bridge.materialization is authored by the Kimi production author. While it
# is absent, provide a faithful in-test stand-in for the documented interface
# (secret-free descriptor, original guard target, one-turn prepare context
# manager) so dispatch behavior can be exercised. When the real module lands
# it takes precedence and this stub is ignored.
if importlib.util.find_spec("materialization") is None:
    _stub = types.ModuleType("materialization")

    class _StubMaterializationError(Exception):
        def __init__(self, code, message):
            self.code = code
            self.message = message
            super().__init__(message)

    def _describe(config_path, model_record, provider_record):
        return {"model": model_record["alias"], "provider_id": provider_record["id"],
                "source": "test-stub"}

    def _guard_target(config_path, descriptor):
        return {"config_path": config_path, "model": descriptor["model"]}

    @contextlib.contextmanager
    def _prepare(config_path, descriptor, state_dir):
        env = dict(os.environ)
        env["KIMI_CODE_HOME"] = str(Path(config_path).expanduser().resolve().parent)
        yield {"config_path": config_path, "env": env, "model": descriptor["model"]}

    _stub.MaterializationError = _StubMaterializationError
    _stub.describe = _describe
    _stub.guard_target = _guard_target
    _stub.prepare = _prepare
    sys.modules["materialization"] = _stub

import runtime  # noqa: E402
from runtime import AgentManager, BridgeError  # noqa: E402
import cost_policy  # noqa: E402
import materialization  # noqa: E402

FAKE = Path(__file__).with_name("fake_cli.py")
TERMINAL = runtime.TERMINAL

ALIAS_A = "kimi/k3-256k"      # kimi family, deep profile
ALIAS_B = "kimi/glm-5.3"      # glm family, identical fit table to ALIAS_A
PROVIDER = "kimi"
SECRET = "test-fake-SECRET_SENTINEL"

# 2026-10-17 is a Saturday: 08:00 UTC == 16:00 Asia/Shanghai -> off_peak.
OFF_PEAK = datetime(2026, 10, 17, 8, 0, 0, tzinfo=timezone.utc)

CONFIG_TOML = """
[providers.kimi]
api_key = "%(secret)s"
base_url = "https://api.kimi.com/coding/v1"

[models."%(a)s"]
model = "k3-256k"
provider = "%(provider)s"
max_context_size = 2000000

[models."%(b)s"]
model = "glm-5.3"
provider = "%(provider)s"
max_context_size = 2000000
""" % {"secret": SECRET, "a": ALIAS_A, "b": ALIAS_B, "provider": PROVIDER}


def payload_from(template, models=(ALIAS_A, ALIAS_B), limit=3, discount=False,
                 enabled=True):
    payload = copy.deepcopy(template["payload"])
    payload["agent_configs"] = [{
        "agent_type": "kimi-code",
        "enabled": enabled,
        "providers": [{"provider_id": PROVIDER, "models": list(models)}] if models else [],
    }]
    payload["limits"]["max_parallel_per_project"] = limit
    payload["selection"]["prefer_provider_discount_on_equal_fit"] = discount
    return payload


class DispatchCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / "kimi-home"
        self.home.mkdir()
        self.config = self.home / "config.toml"
        self.config.write_text(CONFIG_TOML)
        self.state = self.root / "state"
        self.work = self.root / "work"
        self.outer = self.work / "outer"
        self.inner = self.outer / "inner"
        self.other = self.work / "other"
        self.plain = self.work / "plain"
        for path in (self.outer, self.inner, self.other, self.plain):
            path.mkdir(parents=True)
        self.mgr = self.make_manager()

    def tearDown(self):
        for agent in self.mgr.list_agents()["agents"]:
            if agent["status"] not in TERMINAL:
                with contextlib.suppress(Exception):
                    self.mgr.interrupt_agent(agent["agent_id"])

    def make_manager(self):
        return AgentManager(state_dir=str(self.state), config_path=str(self.config),
                            kimi_command=[sys.executable, str(FAKE)])

    # -- helpers ---------------------------------------------------------
    def wait_status(self, agent_id, want, mgr=None, deadline=30):
        mgr = mgr or self.mgr
        end = time.monotonic() + deadline
        while time.monotonic() < end:
            agent = mgr.get_agent(agent_id)
            if agent["status"] in want:
                return agent
            time.sleep(0.1)
        self.fail("agent %s did not reach %s (status %s)" %
                  (agent_id, want, agent["status"]))

    def wait_running(self, n, mgr=None, deadline=30):
        mgr = mgr or self.mgr
        end = time.monotonic() + deadline
        while time.monotonic() < end:
            if mgr.model_catalog([])["kimi_running"] == n:
                return
            time.sleep(0.1)
        self.fail("kimi_running never reached %d" % n)

    def spawn(self, unit, cwd, model, spec=None, project_id=None, role="exploration",
              mgr=None):
        message = json.dumps(spec) if isinstance(spec, dict) else (spec or "hello")
        return (mgr or self.mgr).spawn_agent("scope", unit, role, message,
                                             str(cwd), model, project_id=project_id)

    def register(self, name, root, **kw):
        return self.mgr.register_project(name, str(root), **kw)

    def bind_template(self, project_id, name="Custom", **payload_kw):
        template = self.mgr.create_template(name)
        payload = payload_from(template, **payload_kw)
        saved = self.mgr.save_template(template["id"], payload, expected_revision=1)
        self.mgr.bind_project_template(project_id, template["id"],
                                       expected_binding_revision=0)
        return saved

    def save_payload(self, template_id, **payload_kw):
        current = self.mgr.get_template(template_id)
        payload = payload_from(current, **payload_kw)
        return self.mgr.save_template(template_id, payload,
                                      expected_revision=current["revision"])

    def db_count(self, table):
        db = sqlite3.connect(self.state / "agents.sqlite3")
        try:
            return db.execute("SELECT count(*) FROM %s" % table).fetchone()[0]
        finally:
            db.close()


class DispatchProjectContextTests(DispatchCase):
    def test_explicit_project_and_cwd_admitted_with_binding(self):
        project = self.register("Inner", self.inner)
        template = self.bind_template(project["id"], models=(ALIAS_A,))
        agent = self.spawn("u1", self.inner, ALIAS_A,
                           spec={"delay": 0.2, "text": "first"},
                           project_id=project["id"])
        self.assertEqual(agent["project_id"], project["id"])
        done = self.wait_status(agent["agent_id"], TERMINAL)
        self.assertEqual((done["status"], done["turn"]), ("completed", 1))
        snapshot = done["configuration_snapshot"]
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot["template_id"], template["id"])
        self.assertEqual(snapshot["project_id"], project["id"])

    def test_explicit_project_rejects_mismatched_cwd_without_fallback(self):
        project = self.register("Inner", self.inner)
        self.bind_template(project["id"], models=(ALIAS_A,))
        with self.assertRaises(BridgeError):
            self.spawn("u1", self.other, ALIAS_A, project_id=project["id"])
        self.assertEqual(self.mgr.list_agents()["agents"], [])
        # The same Project still admits a spawn from its real root.
        agent = self.spawn("u2", self.inner, ALIAS_A,
                           spec={"delay": 0.2}, project_id=project["id"])
        self.assertEqual(agent["project_id"], project["id"])

    def test_unknown_project_id_fails_not_found(self):
        with self.assertRaises(BridgeError) as ctx:
            self.spawn("u1", self.inner, ALIAS_A, project_id="no-such-project")
        self.assertEqual(ctx.exception.code, "not_found")
        self.assertEqual(self.mgr.list_agents()["agents"], [])

    def test_nonlocal_host_project_rejected(self):
        remote = self.register("Remote", self.other, host_id="remote")
        self.assertEqual(remote["host_id"], "remote")
        with self.assertRaises(BridgeError):
            self.spawn("u1", self.other, ALIAS_A, project_id=remote["id"])
        self.assertEqual(self.mgr.list_agents()["agents"], [])

    def test_longest_registered_ancestor_wins_no_default_fallback(self):
        outer = self.register("Outer", self.outer)
        inner = self.register("Inner", self.inner)
        self.bind_template(outer["id"], name="OuterT", models=(ALIAS_B,))
        self.bind_template(inner["id"], name="InnerT", models=(ALIAS_A,))
        # Inner allows only ALIAS_A; ALIAS_B is denied with no fallback even
        # though the Outer binding (and default) would allow it.
        ok = self.spawn("u1", self.inner, ALIAS_A, spec={"delay": 0.2})
        self.assertEqual(ok["project_id"], inner["id"])
        with self.assertRaises(BridgeError) as ctx:
            self.spawn("u2", self.inner, ALIAS_B)
        self.assertEqual(ctx.exception.code, "model_not_allowed")
        # Outer allows only ALIAS_B; ALIAS_A denied there too.
        with self.assertRaises(BridgeError) as ctx:
            self.spawn("u3", self.outer, ALIAS_A)
        self.assertEqual(ctx.exception.code, "model_not_allowed")
        ok_outer = self.spawn("u4", self.outer, ALIAS_B, spec={"delay": 0.2})
        self.assertEqual(ok_outer["project_id"], outer["id"])

    def test_symlink_cwd_matches_canonical_project_root(self):
        project = self.register("Inner", self.inner)
        self.bind_template(project["id"], models=(ALIAS_A,), limit=1)
        link = self.root / "linkdir"
        link.symlink_to(self.inner)
        first = self.spawn("u1", link, ALIAS_A, spec={"delay": 30})
        self.assertEqual(first["project_id"], project["id"])
        self.wait_running(1)
        # A second concurrent turn in the same Project is capped even though
        # the symlinked cwd resolves to the canonical registered root.
        with self.assertRaises(BridgeError) as ctx:
            self.spawn("u2", link, ALIAS_A, spec={"delay": 30})
        self.assertEqual(ctx.exception.code, "project_capacity_exceeded")
        self.assertNotIn(str(self.inner), json.dumps(ctx.exception.details))

    def test_unregistered_cwd_uses_default_without_project(self):
        agent = self.spawn("u1", self.plain, ALIAS_A, spec={"delay": 0.2})
        self.assertIsNone(agent["project_id"])
        done = self.wait_status(agent["agent_id"], TERMINAL)
        self.assertIsNone(done["configuration_snapshot"]["project_id"])

    def test_spawn_never_creates_projects_implicitly(self):
        before = {p["id"] for p in self.mgr.list_projects()["projects"]}
        agent = self.spawn("u1", self.plain, ALIAS_A, spec={"delay": 0.2})
        self.wait_status(agent["agent_id"], TERMINAL)
        after = {p["id"] for p in self.mgr.list_projects()["projects"]}
        self.assertEqual(before, after)


class DispatchAllowlistTests(DispatchCase):
    def test_default_template_gates_cli_aliases(self):
        default = self.mgr.get_template("default")
        self.assertEqual(default["revision"], 1)
        self.save_payload("default", models=(ALIAS_A,))
        ok = self.spawn("u1", self.plain, ALIAS_A, spec={"delay": 0.2})
        self.wait_status(ok["agent_id"], TERMINAL)
        with self.assertRaises(BridgeError) as ctx:
            self.spawn("u2", self.plain, ALIAS_B)
        self.assertEqual(ctx.exception.code, "model_not_allowed")

    def test_denied_alias_fails_before_capacity_not_with_it(self):
        # Fill the only shared CLI slot, then attempt a denied alias: the
        # allowlist verdict must win over capacity admission.
        self.mgr.set_concurrency(1)
        self.spawn("u0", self.plain, ALIAS_A, spec={"delay": 30})
        self.wait_running(1)
        self.save_payload("default", models=(ALIAS_A,))
        with self.assertRaises(BridgeError) as ctx:
            self.spawn("u1", self.plain, ALIAS_B)
        self.assertEqual(ctx.exception.code, "model_not_allowed")
        self.assertNotEqual(ctx.exception.code, "capacity_exceeded")

    def test_partial_template_runs_remaining_valid_candidates(self):
        project = self.register("Inner", self.inner)
        self.bind_template(project["id"], models=(ALIAS_A, ALIAS_B))
        self.mgr.save_model(ALIAS_B, expected_revision=1, enabled=False)
        resolution = self.mgr.resolve_project_config(project["id"])
        self.assertEqual([u["alias"] for u in resolution["unavailable"]], [ALIAS_B])
        self.assertEqual([c["alias"] for c in resolution["candidates"]], [ALIAS_A])
        # The valid candidate still runs under the Project binding.
        ok = self.spawn("u1", self.inner, ALIAS_A, spec={"delay": 0.2},
                        project_id=project["id"])
        self.wait_status(ok["agent_id"], TERMINAL)
        # The disabled record is never substituted or admitted explicitly.
        with self.assertRaises(BridgeError) as ctx:
            self.spawn("u2", self.inner, ALIAS_B, project_id=project["id"])
        self.assertEqual(ctx.exception.code, "model_not_allowed")

    def test_empty_allowlist_cannot_run_cli(self):
        project = self.register("Inner", self.inner)
        self.bind_template(project["id"], models=(), enabled=False)
        with self.assertRaises(BridgeError) as ctx:
            self.spawn("u1", self.inner, ALIAS_A, project_id=project["id"])
        self.assertEqual(ctx.exception.code, "model_not_allowed")
        with self.assertRaises(BridgeError) as ctx:
            self.mgr.select_model("scope", "u2", "exploration", ["gpt-6.1-sol"],
                                  project_id=project["id"])
        self.assertEqual(ctx.exception.code, "no_eligible_model")

    def test_selection_filters_allowlist_before_native_choices(self):
        project = self.register("Inner", self.inner)
        self.bind_template(project["id"], models=(ALIAS_A,))
        out = self.mgr.select_model("scope", "u1", "research",
                                    ["gpt-6.1-sol"], project_id=project["id"],
                                    complexity="complex")
        self.assertEqual(out["project_id"], project["id"])
        self.assertTrue(out["selection_is_advisory"])
        self.assertEqual({c["family"] for c in out["candidates"]}, {"kimi"})
        self.assertEqual(out["selected"]["alias"], ALIAS_A)


class DispatchNativeBoundaryTests(DispatchCase):
    def test_native_spawn_with_project_context_rejected(self):
        project = self.register("Inner", self.inner)
        self.bind_template(project["id"], models=(ALIAS_A,))
        with self.assertRaises(BridgeError) as ctx:
            self.spawn("u1", self.inner, "gpt-6.1-sol", project_id=project["id"])
        self.assertEqual(ctx.exception.code, "native_project_unsupported")
        # No reservation, claim or agent row is created as a side effect.
        self.assertEqual(self.mgr.list_agents()["pending_native_reservations"], [])
        self.assertEqual(self.mgr.list_agents()["agents"], [])

    def test_project_selection_excludes_native_models(self):
        project = self.register("Inner", self.inner)
        self.bind_template(project["id"], models=(ALIAS_A, ALIAS_B))
        out = self.mgr.select_model("scope", "u1", "research",
                                    ["gpt-6.1-sol"], project_id=project["id"],
                                    complexity="complex")
        families = {c["family"] for c in out["candidates"]}
        self.assertNotIn("gpt", families)
        self.assertNotEqual(out["selected"]["family"], "gpt")

    def test_native_path_still_available_without_project(self):
        out = self.mgr.select_model("scope", "u1", "research",
                                    ["gpt-6.1-sol"], complexity="complex")
        self.assertIn("gpt", {c["family"] for c in out["candidates"]})
        result = self.spawn("u2", self.plain, "gpt-6.1-sol")
        self.assertIn("reservation_id", result)
        self.assertFalse(result["dispatched"])


class DispatchDiscountTests(DispatchCase):
    def at(self, moment):
        real = cost_policy.evaluate
        return mock.patch.object(cost_policy, "evaluate",
                                 lambda p, now=None: real(p, moment))

    def choose(self, unit, **kw):
        kw.setdefault("role", "research")
        kw.setdefault("native_models", ["gpt-6.1-sol"])
        kw.setdefault("complexity", "complex")
        return self.mgr.select_model("scope", unit, **kw)

    def test_default_template_preserves_legacy_off_peak_preference(self):
        self.mgr.set_cost_policy(True, model=ALIAS_A)
        with self.at(OFF_PEAK):
            out = self.choose("u1")
        self.assertTrue(out["cost_preference_applied"])
        self.assertEqual(out["selected"]["alias"], ALIAS_A)

    def test_custom_template_flag_false_disables_discount(self):
        project = self.register("Inner", self.inner)
        self.bind_template(project["id"], models=(ALIAS_A, ALIAS_B), discount=False)
        self.mgr.set_cost_policy(True, model=ALIAS_A)
        with self.at(OFF_PEAK):
            out = self.choose("u2", project_id=project["id"])
        self.assertEqual(out["cost_policy"]["period"], "off_peak")
        self.assertFalse(out["cost_preference_applied"])

    def test_custom_template_flag_true_applies_discount_on_tie(self):
        project = self.register("Inner", self.inner)
        self.bind_template(project["id"], models=(ALIAS_A, ALIAS_B), discount=True)
        self.mgr.set_cost_policy(True, model=ALIAS_A)
        with self.at(OFF_PEAK):
            out = self.choose("u3", project_id=project["id"])
        self.assertTrue(out["cost_preference_applied"])
        self.assertEqual(out["selected"]["alias"], ALIAS_A)

    def test_flag_true_cannot_override_global_disable(self):
        project = self.register("Inner", self.inner)
        self.bind_template(project["id"], models=(ALIAS_A, ALIAS_B), discount=True)
        self.mgr.set_cost_policy(False, model=ALIAS_A)
        with self.at(OFF_PEAK):
            out = self.choose("u4", project_id=project["id"])
        self.assertFalse(out["cost_preference_applied"])


class DispatchProjectConcurrencyTests(DispatchCase):
    def test_per_project_cap_blocks_second_and_metadata_is_path_free(self):
        self.mgr.set_concurrency("unlimited")
        project = self.register("Inner", self.inner)
        self.bind_template(project["id"], models=(ALIAS_A,), limit=1)
        first = self.spawn("u1", self.inner, ALIAS_A, spec={"delay": 30},
                           project_id=project["id"])
        self.wait_running(1)
        with self.assertRaises(BridgeError) as ctx:
            self.spawn("u2", self.inner, ALIAS_A, spec={"delay": 30},
                       project_id=project["id"])
        self.assertEqual(ctx.exception.code, "project_capacity_exceeded")
        details = json.dumps(ctx.exception.details)
        self.assertNotIn(str(self.inner), details)
        self.assertNotIn(str(self.work), details)
        self.assertEqual(ctx.exception.details["limit"], 1)

    def test_different_projects_are_independent(self):
        self.mgr.set_concurrency("unlimited")
        one = self.register("One", self.inner)
        two = self.register("Two", self.other)
        self.bind_template(one["id"], name="T1", models=(ALIAS_A,), limit=1)
        self.bind_template(two["id"], name="T2", models=(ALIAS_A,), limit=1)
        a = self.spawn("u1", self.inner, ALIAS_A, spec={"delay": 30},
                       project_id=one["id"])
        b = self.spawn("u2", self.other, ALIAS_A, spec={"delay": 30},
                       project_id=two["id"])
        self.wait_running(2)
        self.wait_status(a["agent_id"], {"running"})
        self.wait_status(b["agent_id"], {"running"})

    def test_shared_cli_cap_still_applies_inside_project(self):
        self.mgr.set_concurrency(1)
        project = self.register("Inner", self.inner)
        self.bind_template(project["id"], models=(ALIAS_A,), limit=3)
        self.spawn("u1", self.inner, ALIAS_A, spec={"delay": 30},
                   project_id=project["id"])
        self.wait_running(1)
        with self.assertRaises(BridgeError) as ctx:
            self.spawn("u2", self.inner, ALIAS_A, spec={"delay": 30},
                       project_id=project["id"])
        self.assertEqual(ctx.exception.code, "capacity_exceeded")

    def test_zero_project_limit_bypasses_only_the_project_cap(self):
        self.mgr.set_concurrency("unlimited")
        project = self.register("Inner", self.inner)
        template = self.bind_template(project["id"], models=(ALIAS_A,), limit=0)
        self.assertEqual(
            template["payload"]["limits"]["max_parallel_per_project"], 0)
        # Zero admits multiple concurrent turns in the same Project.
        agents = [self.spawn("z%d" % i, self.inner, ALIAS_A,
                             spec={"delay": 30}, project_id=project["id"])
                  for i in range(3)]
        self.wait_running(3)
        for agent in agents:
            self.assertEqual(self.mgr.get_agent(agent["agent_id"])["project_id"],
                             project["id"])
        # Zero never grants models: the allowlist still rejects ALIAS_B.
        with self.assertRaises(BridgeError) as ctx:
            self.spawn("zb", self.inner, ALIAS_B, project_id=project["id"])
        self.assertEqual(ctx.exception.code, "model_not_allowed")
        # The shared CLI cap still applies inside a zero-capped Project.
        self.mgr.set_concurrency(3)
        with self.assertRaises(BridgeError) as ctx:
            self.spawn("zc", self.inner, ALIAS_A, spec={"delay": 30},
                       project_id=project["id"])
        self.assertEqual(ctx.exception.code, "capacity_exceeded")
        # Live jobs are never cancelled by any limit change, including zero.
        for agent in agents:
            self.assertIn(self.mgr.get_agent(agent["agent_id"])["status"],
                          {"starting", "running"})

    def test_zero_project_limit_rejects_noninteger_and_followup_survives_restart(self):
        self.mgr.set_concurrency("unlimited")
        project = self.register("Inner", self.inner)
        template = self.bind_template(project["id"], models=(ALIAS_A,), limit=0)
        # Strict zero: bools/floats/negatives/out-of-range stay invalid.
        for bad in (True, 2.5, -1, 101):
            with self.subTest(bad=bad):
                with self.assertRaises(BridgeError):
                    self.save_payload(template["id"], models=(ALIAS_A,), limit=bad)
        agent = self.spawn("u1", self.inner, ALIAS_A,
                           spec={"delay": 0.2, "text": "first"},
                           project_id=project["id"])
        self.wait_status(agent["agent_id"], TERMINAL)
        # Restart recovery: the saved zero limit still resolves and admits a
        # follow-up turn; the Project cap no longer blocks anything.
        restarted = self.make_manager()
        resolution = restarted.resolve_project_config(project["id"])
        self.assertTrue(resolution["ready"])
        followed = restarted.followup_task(
            agent["agent_id"], json.dumps({"delay": 0.2, "text": "second"}))
        self.assertEqual(followed["project_id"], project["id"])
        second = self.wait_status(agent["agent_id"], TERMINAL, mgr=restarted)
        self.assertEqual(second["turn"], 2)
        self.assertIn("second", second["last_result"])
        self.mgr = restarted  # cleanup through the restarted manager

    def test_queued_turn_rechecked_at_boundary_after_lowering(self):
        self.mgr.set_concurrency("unlimited")
        project = self.register("Inner", self.inner)
        template = self.bind_template(project["id"], models=(ALIAS_A,), limit=2)
        blocker = self.spawn("u1", self.inner, ALIAS_A, spec={"delay": 30},
                             project_id=project["id"])
        active = self.spawn("u2", self.inner, ALIAS_A,
                            spec={"delay": 4, "text": "first"},
                            project_id=project["id"])
        self.wait_status(blocker["agent_id"], {"running"})
        # Wait until turn 1 actually started so the queued message is picked
        # up at the next boundary, not batched into the current turn.
        self.wait_status(active["agent_id"], {"running"})
        delivery = self.mgr.send_message(active["agent_id"],
                                         json.dumps({"text": "second"}))
        self.assertEqual(delivery["delivery"], "next_turn_boundary")
        # Lower the Project limit while both turns are live: active turns are
        # preserved, the queued turn is re-checked and stopped at its boundary.
        self.save_payload(template["id"], models=(ALIAS_A,), limit=1)
        done = self.wait_status(active["agent_id"], TERMINAL)
        self.assertEqual(done["turn"], 1)
        self.assertEqual(done["status"], "interrupted")
        self.assertEqual(done["queued_message_count"], 0)
        self.assertNotIn("second", done["last_result"])
        self.assertIn("project_capacity_exceeded", done["error"])
        # New admission is blocked while the blocker holds the one seat.
        with self.assertRaises(BridgeError) as ctx:
            self.spawn("u3", self.inner, ALIAS_A, spec={"delay": 30},
                       project_id=project["id"])
        self.assertEqual(ctx.exception.code, "project_capacity_exceeded")
        self.assertEqual(self.mgr.get_agent(blocker["agent_id"])["status"], "running")

    def test_interrupted_row_releases_project_occupancy(self):
        self.mgr.set_concurrency("unlimited")
        project = self.register("Inner", self.inner)
        self.bind_template(project["id"], models=(ALIAS_A,), limit=1)
        first = self.spawn("u1", self.inner, ALIAS_A, spec={"delay": 30},
                           project_id=project["id"])
        self.wait_running(1)
        stopped = self.mgr.interrupt_agent(first["agent_id"])
        self.assertEqual(stopped["status"], "interrupted")
        # A stale row with a free lock must not consume Project capacity.
        second = self.spawn("u2", self.inner, ALIAS_A, spec={"delay": 30},
                            project_id=project["id"])
        self.assertEqual(second["project_id"], project["id"])
        self.wait_running(1)

    def test_launch_failure_rolls_back_without_phantom_occupancy(self):
        self.mgr.set_concurrency("unlimited")
        project = self.register("Inner", self.inner)
        self.bind_template(project["id"], models=(ALIAS_A,), limit=1)
        # A failing launch still releases the slot descriptor it received, as
        # the real _launch does in its finally block.
        def failing_launch(agent_id, fd):
            os.close(fd)
            raise RuntimeError("boom")

        with mock.patch.object(self.mgr, "_launch", side_effect=failing_launch):
            with self.assertRaises(RuntimeError):
                self.spawn("u1", self.inner, ALIAS_A, spec={"delay": 30},
                           project_id=project["id"])
        # Job, claims, queue and snapshot state are all released atomically.
        self.assertEqual(self.mgr.list_agents()["agents"], [])
        self.assertEqual(self.db_count("turn_snapshots"), 0)
        # No phantom Project occupancy: a fresh spawn is admitted at once.
        ok = self.spawn("u2", self.inner, ALIAS_A, spec={"delay": 30},
                        project_id=project["id"])
        self.assertEqual(ok["project_id"], project["id"])
        self.wait_running(1)

    def test_cross_process_project_admission_is_atomic(self):
        self.mgr.set_concurrency("unlimited")
        project = self.register("Inner", self.inner)
        self.bind_template(project["id"], models=(ALIAS_A,), limit=1)
        child_code = (
            "import sys,json;sys.path.insert(0,sys.argv[1]);import runtime\n"
            "from runtime import BridgeError\n"
            "m=runtime.AgentManager(state_dir=sys.argv[2],config_path=sys.argv[3],"
            "kimi_command=[sys.executable,sys.argv[4]])\n"
            "try:\n"
            " m.spawn_agent('race',sys.argv[5],'exploration','{\"delay\": 30}',"
            "sys.argv[6],sys.argv[7],project_id=sys.argv[8])\n"
            " print('ADMITTED')\n"
            "except BridgeError as e:\n"
            " print(json.dumps({'code':e.code}))\n")
        procs = [subprocess.Popen(
            [sys.executable, "-c", child_code, str(BRIDGE), str(self.state),
             str(self.config), str(FAKE), "race-%d" % i, str(self.inner),
             ALIAS_A, project["id"]],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for i in range(3)]
        results = [p.communicate(timeout=90) for p in procs]
        for proc, (_, err) in zip(procs, results):
            self.assertEqual(proc.returncode, 0, err)
        admitted = sum("ADMITTED" in out for out, _ in results)
        self.assertEqual(admitted, 1)
        self.wait_running(1)


class DispatchSnapshotLifecycleTests(DispatchCase):
    def test_active_turn_keeps_snapshot_and_followup_gets_fresh_one(self):
        project = self.register("Inner", self.inner)
        template = self.bind_template(project["id"], models=(ALIAS_A,), limit=1)
        pinned_revision = template["revision"]
        agent = self.spawn("u1", self.inner, ALIAS_A,
                           spec={"delay": 2, "text": "first"},
                           project_id=project["id"])
        running = self.wait_status(agent["agent_id"], {"running"})
        self.assertEqual(running["configuration_snapshot"]["template_revision"],
                         pinned_revision)
        # Template edits apply only to future turns; the live turn is pinned.
        edited = self.save_payload(template["id"], models=(ALIAS_A,), limit=2)
        done = self.wait_status(agent["agent_id"], TERMINAL)
        self.assertEqual(done["configuration_snapshot"]["template_revision"],
                         pinned_revision)
        self.assertIn("first", done["last_result"])
        # Idle follow-up re-resolves the current binding and snapshots it.
        followed = self.mgr.followup_task(agent["agent_id"],
                                          json.dumps({"delay": 1, "text": "second"}))
        self.assertEqual(followed["status"], "starting")
        second = self.wait_status(agent["agent_id"], TERMINAL)
        self.assertEqual(second["turn"], 2)
        self.assertEqual(second["configuration_snapshot"]["template_revision"],
                         edited["revision"])
        self.assertIn("RESUMED", second["last_result"])
        self.assertIn("second", second["last_result"])

    def test_followup_revalidates_permission_and_stops_when_alias_removed(self):
        project = self.register("Inner", self.inner)
        template = self.bind_template(project["id"], models=(ALIAS_A,), limit=1)
        agent = self.spawn("u1", self.inner, ALIAS_A, spec={"delay": 0.2},
                           project_id=project["id"])
        self.wait_status(agent["agent_id"], TERMINAL)
        # Remove the source alias from the saved binding: no continuation.
        self.save_payload(template["id"], models=(), enabled=False)
        with self.assertRaises(BridgeError) as ctx:
            self.mgr.followup_task(agent["agent_id"], "again")
        self.assertEqual(ctx.exception.code, "model_not_allowed")
        row = self.mgr.get_agent(agent["agent_id"])
        self.assertEqual(row["turn"], 1)
        self.assertEqual(row["status"], "completed")

    def test_worker_rechecks_permission_before_queued_turn(self):
        project = self.register("Inner", self.inner)
        template = self.bind_template(project["id"], models=(ALIAS_A,), limit=1)
        agent = self.spawn("u1", self.inner, ALIAS_A,
                           spec={"delay": 3, "text": "first"},
                           project_id=project["id"])
        self.wait_status(agent["agent_id"], {"running"})
        delivery = self.mgr.send_message(agent["agent_id"], json.dumps({"text": "second"}))
        self.assertEqual(delivery["delivery"], "next_turn_boundary")
        # Revoke permission while the first turn is live.
        self.save_payload(template["id"], models=(), enabled=False)
        done = self.wait_status(agent["agent_id"], TERMINAL)
        self.assertEqual(done["turn"], 1)
        self.assertNotIn("second", done["last_result"])

    def test_restart_keeps_snapshots_and_allows_followup(self):
        project = self.register("Inner", self.inner)
        self.bind_template(project["id"], models=(ALIAS_A,), limit=1)
        agent = self.spawn("u1", self.inner, ALIAS_A, spec={"delay": 0.2},
                           project_id=project["id"])
        self.wait_status(agent["agent_id"], TERMINAL)
        restarted = self.make_manager()
        view = restarted.get_agent(agent["agent_id"])
        self.assertEqual(view["project_id"], project["id"])
        self.assertIsNotNone(view["configuration_snapshot"])
        followed = restarted.followup_task(agent["agent_id"],
                                           json.dumps({"delay": 0.2, "text": "again"}))
        self.assertEqual(followed["project_id"], project["id"])
        second = self.wait_status(agent["agent_id"], TERMINAL, mgr=restarted)
        self.assertEqual(second["turn"], 2)
        self.mgr = restarted  # cleanup through the restarted manager

    def test_legacy_row_without_snapshot_migrates_on_resume(self):
        project = self.register("Inner", self.inner)
        self.bind_template(project["id"], models=(ALIAS_A,), limit=1)
        agent = self.spawn("u1", self.inner, ALIAS_A, spec={"delay": 0.2},
                           project_id=project["id"])
        self.wait_status(agent["agent_id"], TERMINAL)
        # Simulate a pre-Stage-3 legacy row: durable state without a snapshot.
        db = sqlite3.connect(self.state / "agents.sqlite3")
        try:
            db.execute("UPDATE agents SET configuration_snapshot=NULL WHERE id=?",
                       (agent["agent_id"],))
            db.commit()
        finally:
            db.close()
        view = self.mgr.get_agent(agent["agent_id"])
        self.assertIsNone(view["configuration_snapshot"])
        # Idempotent reads, then a fresh context is snapshotted on resume.
        self.assertIsNone(self.mgr.get_agent(agent["agent_id"])["configuration_snapshot"])
        followed = self.mgr.followup_task(agent["agent_id"],
                                          json.dumps({"delay": 0.2, "text": "resumed"}))
        self.assertEqual(followed["project_id"], project["id"])
        second = self.wait_status(agent["agent_id"], TERMINAL)
        self.assertEqual(second["turn"], 2)
        snapshot = second["configuration_snapshot"]
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot["project_id"], project["id"])
        self.assertIn("resumed", second["last_result"])


class DispatchLeakTests(DispatchCase):
    def collect_views(self, project_id):
        agent = self.spawn("u1", self.inner, ALIAS_A, spec={"delay": 0.2},
                           project_id=project_id)
        self.wait_status(agent["agent_id"], TERMINAL)
        views = [
            self.mgr.get_agent(agent["agent_id"]),
            self.mgr.list_agents(),
            self.mgr.select_model("scope", "u2", "research", ["gpt-6.1-sol"],
                                  project_id=project_id, complexity="complex"),
            self.mgr.resolve_project_config(project_id),
            self.mgr.list_projects(),
        ]
        return agent, views

    def test_no_root_path_secret_or_raw_toml_in_public_views(self):
        project = self.register("Inner", self.inner)
        self.bind_template(project["id"], models=(ALIAS_A,))
        agent, views = self.collect_views(project["id"])
        blob = json.dumps(views)
        self.assertNotIn(str(self.inner), blob)
        self.assertNotIn(str(self.work), blob)
        self.assertNotIn(SECRET, blob)
        self.assertNotIn("api_key", blob)
        self.assertNotIn("[providers", blob)
        # The public view pins Project/template identity without the root.
        view = views[0]
        self.assertEqual(view["project_id"], project["id"])
        self.assertNotIn("root", json.dumps(views[4]))

    def test_project_capacity_error_contains_no_paths_or_secrets(self):
        self.mgr.set_concurrency("unlimited")
        project = self.register("Inner", self.inner)
        self.bind_template(project["id"], models=(ALIAS_A,), limit=1)
        first = self.spawn("u1", self.inner, ALIAS_A, spec={"delay": 30},
                           project_id=project["id"])
        self.wait_running(1)
        with self.assertRaises(BridgeError) as ctx:
            self.spawn("u2", self.inner, ALIAS_A, spec={"delay": 30},
                       project_id=project["id"])
        self.assertEqual(ctx.exception.code, "project_capacity_exceeded")
        blob = json.dumps(ctx.exception.as_dict())
        self.assertNotIn(str(self.inner), blob)
        self.assertNotIn(SECRET, blob)


class DispatchManagedRuntimeTests(DispatchCase):
    def create_managed(self, provider_id, alias, upstream, credential_ref,
                       provider_type="kimi", endpoint=None, protocol="openai",
                       context=200000, capabilities=None):
        endpoint = endpoint or (
            "https://api.kimi.com/coding/v1" if provider_type == "kimi"
            else "https://api.z.ai/api/coding")
        self.mgr.create_provider(provider_id, provider_id, provider_type,
                                 endpoint, protocol, credential_ref)
        return self.mgr.create_model(
            alias, provider_id, upstream, upstream, context,
            capabilities or ["thinking", "tool_use"])

    def allow_default(self, aliases):
        default = self.mgr.get_template("default")
        payload = default["payload"]
        by_provider = {}
        for alias in aliases:
            by_provider.setdefault(alias.split("/", 1)[0], []).append(alias)
        payload["agent_configs"] = [{
            "agent_type": "kimi-code", "enabled": True,
            "providers": [{"provider_id": pid, "models": models}
                          for pid, models in by_provider.items()],
        }]
        return self.mgr.save_template("default", payload,
                                      expected_revision=default["revision"])

    def test_managed_env_alias_executes_and_pins_secret_free_snapshot(self):
        env_var = "TEST_MANAGED_KIMI_KEY"
        os.environ[env_var] = "fake-managed-key-no-secret"
        self.addCleanup(os.environ.pop, env_var, None)
        self.create_managed("managed-kimi", "managed-kimi/k3", "k3",
                            "env:" + env_var)
        self.allow_default(["managed-kimi/k3"])
        agent = self.spawn("managed", self.plain, "managed-kimi/k3",
                           spec={"delay": 0.2, "text": "managed-run"})
        done = self.wait_status(agent["agent_id"], TERMINAL)
        self.assertEqual(done["status"], "completed")
        snapshot = done["configuration_snapshot"]
        self.assertIsNotNone(snapshot)
        descriptor = snapshot["descriptor"]
        self.assertEqual(descriptor["kind"], "managed")
        self.assertEqual(descriptor["alias"], "managed-kimi/k3")
        self.assertEqual(descriptor["credential_ref"], "env:" + env_var)
        self.assertIsNone(descriptor.get("credential_origin"))
        self.assertIsNone(descriptor.get("source"))
        blob = json.dumps(snapshot)
        self.assertNotIn(os.environ[env_var], blob)
        self.assertNotIn(str(self.home), blob)
        self.assertNotIn(str(self.state), blob)

    def test_managed_existing_ref_uses_original_guard_target_and_shared_cap(self):
        # The installation prefix has both Kimi and GLM aliases. The guard
        # target must be an eligible Kimi-account alias (recognized by the
        # Kimi guard/account module), not the lexicographically-first GLM
        # alias, otherwise the shared account cap is bypassed.
        self.create_managed("managed-copy", "managed-copy/k3-256k", "k3-256k",
                            "existing:kimi")
        self.allow_default(["kimi/k3-256k", "managed-copy/k3-256k"])
        resources = self.mgr.configuration_resources()
        desc = materialization.describe(
            str(self.config), resources["models"]["managed-copy/k3-256k"],
            resources["providers"]["managed-copy"])
        self.assertEqual(desc["credential_ref"], "existing:kimi")
        self.assertEqual(desc["credential_origin"]["provider_id"], "kimi")
        target = materialization.guard_target(str(self.config), desc)
        # Only Kimi-family aliases are valid Kimi guard/account targets.
        self.assertNotEqual(target["model"], "kimi/glm-5.3")
        self.assertEqual(target["model"], "kimi/k3-256k")
        self.assertEqual(Path(target["config_path"]).resolve(),
                         self.config.resolve())
        # Shared cap: original and managed alias count as one account.
        self.mgr.set_kimi_accounts(["kimi"], {"kimi": 1})
        first = self.spawn("original", self.plain, "kimi/k3-256k",
                           spec={"delay": 30})
        self.wait_running(1)
        try:
            with self.assertRaises(BridgeError) as ctx:
                self.spawn("copy", self.plain, "managed-copy/k3-256k",
                           spec={"delay": 30})
            self.assertEqual(ctx.exception.code, "account_capacity_exceeded")
        finally:
            self.mgr.interrupt_agent(first["agent_id"])

    def test_env_sourced_oauth_token_maps_to_original_account_cap_and_probe(self):
        # p1 takes its credential from an env var whose value is the same
        # current OAuth access token stored in p2's credential file. The
        # runtime helper must map the env-sourced alias to the original OAuth
        # account so cap and probe cooldown are shared.
        oauth_home = self.home / "oauth-home"
        oauth_home.mkdir(parents=True, exist_ok=True)
        creds = oauth_home / "credentials"
        creds.mkdir(parents=True, exist_ok=True)
        token_path = creds / "shared.json"
        shared_token = "fake-shared-oauth-token"
        future = int(time.time()) + 86400
        token_path.write_text(json.dumps({
            "access_token": shared_token,
            "expires_at": future,
            "refresh_token": "fake-refresh",
        }))
        env_var = "TEST_SAME_OAUTH_TOKEN"
        os.environ[env_var] = shared_token
        self.addCleanup(os.environ.pop, env_var, None)
        config = oauth_home / "config.toml"
        config.write_text('''
[providers.p1]
base_url = "https://api.kimi.com/coding/v1"
api_key_env = "TEST_SAME_OAUTH_TOKEN"

[providers.p2]
base_url = "https://api.kimi.com/coding/v1"
[providers.p2.oauth]
storage = "file"
key = "oauth/shared"

[models."p1/x"]
model = "k3"
provider = "p1"

[models."p2/y"]
model = "k3"
provider = "p2"
''')
        mgr = AgentManager(state_dir=str(self.state), config_path=str(config),
                           kimi_command=[sys.executable, str(FAKE)])
        import kimi_usage

        def read(state, cfg, alias=None, **kwargs):
            return {"available": True, "remaining_percent": 94,
                    "used_percent": 6, "reset_at": None,
                    "observed_at": time.time(), "error": None}

        with mock.patch.object(kimi_usage, "read_usage", side_effect=read):
            # Cap the original OAuth-file account; the env-sourced alias maps
            # to the same account, so it consumes that one seat.
            mgr.set_kimi_accounts(["p2"], {"p2": 1})
            a = mgr.spawn_agent("scope", "u1", "exploration",
                                json.dumps({"delay": 30}), str(self.plain),
                                "p1/x")
            self.assertTrue(a["agent_id"].startswith("kimi:"))
            self.wait_running(1, mgr=mgr)
            try:
                with self.assertRaises(BridgeError) as ctx:
                    mgr.spawn_agent("scope", "u2", "exploration",
                                    json.dumps({"delay": 30}), str(self.plain),
                                    "p2/y")
                self.assertEqual(ctx.exception.code,
                                 "account_capacity_exceeded")
                # Same mapped account identity is also the probe target.
                probe = mgr.probe_kimi_usage("p1/x")
                self.assertFalse(probe["probed"])
                self.assertEqual(probe["reason"], "known_reading")
            finally:
                mgr.interrupt_agent(a["agent_id"])


if __name__ == "__main__":
    unittest.main()
