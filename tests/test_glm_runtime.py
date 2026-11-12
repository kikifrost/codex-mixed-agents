"""GLM runtime/MCP acceptance tests derived from docs/GLM_SPEC.md.

Kimi-authored tests for the GPT-authored runtime/server. Real child
processes via tests/fake_cli.py, temporary config/state only; every
credential below is a fake env-var reference, never a real secret.
"""
import json
import os
from pathlib import Path
import secrets as _secrets
import signal
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

KEY_ENV = "TEST_GLM_RUNTIME_KEY"

BASE_CONFIG = """\
[providers.zai]
api_key_env = "%(key_env)s"

[models.kimi]
model = "kimi-for-coding"
provider = "managed:kimi-code"

[models.glm]
model = "glm-5.2"
provider = "zai"

[models.glm-fast]
model = "glm-5.3-flash"
provider = "zai"

[models.glm-deep]
model = "glm-5.3"
provider = "zai"

[models.deepseek]
model = "deepseek-v4"
provider = "zai"

[models.vision]
model = "glm-5.3-vision"
provider = "zai"
"""

ALL_GLM_CONFIG = """\
[providers.zai]
api_key_env = "%(key_env)s"

[models.g47]
model = "glm-4.7"
provider = "zai"

[models.g5]
model = "glm-5"
provider = "zai"

[models.g5t]
model = "glm-5-turbo"
provider = "zai"

[models.g52]
model = "glm-5.2"
provider = "zai"

[models.g52h]
model = "glm-5.2-highspeed"
provider = "zai"

[models.g53]
model = "glm-5.3"
provider = "zai"

[models.g53f]
model = "glm-5.3-flash"
provider = "zai"

[models.g53h]
model = "glm-5.3-highspeed"
provider = "zai"

[models.zai53]
model = "zai/glm-5.3"
provider = "zai"
"""

ROTATION_CONFIG = """\
[providers.zai]
api_key_env = "%(key_env)s"

[models.kimi]
model = "k3"
provider = "managed:kimi-code"

[models.glm]
model = "glm-5.3"
provider = "zai"
"""


class GlmRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / "state"
        self.home = Path(self.tmp.name) / "kimi_home"
        self.home.mkdir()
        self.config = self.home / "config.toml"
        self.config.write_text(BASE_CONFIG % {"key_env": KEY_ENV})
        os.environ[KEY_ENV] = "fake-" + _secrets.token_urlsafe(16)
        self.addCleanup(os.environ.pop, KEY_ENV, None)
        self.mgr = self.manager()

    def manager(self, config=None):
        return AgentManager(state_dir=str(self.state),
                            config_path=str(config or self.config),
                            kimi_command=[sys.executable, str(FAKE)])

    def tearDown(self):
        self.interrupt_all(self.mgr)

    def interrupt_all(self, mgr):
        for agent in mgr.list_agents()["agents"]:
            if agent["status"] not in TERMINAL:
                try:
                    mgr.interrupt_agent(agent["agent_id"])
                except Exception:
                    pass

    def spawn(self, unit, spec=None, role="exploration", model="glm",
              mgr=None, **kw):
        message = json.dumps(spec) if isinstance(spec, dict) else (spec or "hello")
        return (mgr or self.mgr).spawn_agent("scope", unit, role, message,
                                             str(self.state), model, **kw)

    def wait_status(self, agent_id, want=TERMINAL, deadline=30, mgr=None):
        mgr = mgr or self.mgr
        end = time.monotonic() + deadline
        while time.monotonic() < end:
            agent = mgr.get_agent(agent_id)
            if agent["status"] in want:
                return agent
            time.sleep(0.1)
        self.fail("agent %s did not reach %s, last=%s" %
                  (agent_id, want, agent["status"]))

    def row(self, agent_id):
        db = sqlite3.connect(self.state / "agents.sqlite3")
        db.row_factory = sqlite3.Row
        try:
            return db.execute("SELECT * FROM agents WHERE id=?",
                              (agent_id,)).fetchone()
        finally:
            db.close()

    def test_exact_glm_aliases_spawn_as_glm_family_on_cli_backend(self):
        config = Path(self.tmp.name) / "all_glm" / "config.toml"
        config.parent.mkdir()
        config.write_text(ALL_GLM_CONFIG % {"key_env": KEY_ENV})
        mgr = self.manager(config)
        try:
            for alias in ("g47", "g5", "g5t", "g52", "g52h", "g53", "g53f",
                          "g53h", "zai53"):
                agent = self.spawn("alias-" + alias, {"text": "ping"},
                                   model=alias, mgr=mgr)
                agent = self.wait_status(agent["agent_id"], mgr=mgr)
                self.assertEqual(agent["status"], "completed", alias)
                self.assertEqual(agent["family"], "glm", alias)
                self.assertEqual(agent["backend"], "kimi_cli", alias)
                self.assertTrue(agent["agent_id"].startswith("kimi:"), alias)
                self.assertEqual(agent["model"], alias)  # exact alias kept
                self.assertTrue(agent["session_id"], alias)
                self.assertIn("ping", agent["last_result"])
        finally:
            self.interrupt_all(mgr)

    def test_near_miss_and_unsupported_aliases_rejected(self):
        for alias in ("vision", "deepseek"):
            with self.assertRaises(BridgeError) as cm:
                self.spawn("bad-" + alias, model=alias)
            # Stage 3 allowlist: unknown/near-miss aliases are not allowed by
            # the saved template; no other alias is substituted.
            self.assertEqual(cm.exception.code, "model_not_allowed")
        self.assertEqual(self.mgr.list_agents()["agents"], [])

    def test_gpt_prefixed_configured_alias_dispatches_as_cli_glm(self):
        # An arbitrary configured alias beginning "gpt-" whose remote model is
        # GLM must ride the CLI worker path, not the native GPT prefix match.
        config = Path(self.tmp.name) / "gpt_alias" / "config.toml"
        config.parent.mkdir()
        config.write_text(ROTATION_CONFIG % {"key_env": KEY_ENV} +
                          '\n[models.gpt-glm]\nmodel = "glm-5.2"\n'
                          'provider = "zai"\n')
        mgr = self.manager(config)
        try:
            agent = self.spawn("gpt-alias", {"delay": 30}, model="gpt-glm",
                               mgr=mgr)
            self.assertEqual(agent["family"], "glm")
            self.assertEqual(agent["backend"], "kimi_cli")
            self.assertEqual(agent["model"], "gpt-glm")  # exact alias kept
            self.assertTrue(agent["agent_id"].startswith("kimi:"))
            self.assertNotIn("native_action", agent)
            self.assertNotIn("dispatched", agent)
            self.wait_status(agent["agent_id"], {"running"}, mgr=mgr)
            capacity = mgr.model_catalog([])
            self.assertEqual(capacity["kimi_running"], 1)
            self.assertEqual(capacity["kimi_available"], runtime.CAP - 1)
            self.assertEqual(mgr.interrupt_agent(agent["agent_id"])["status"],
                             "interrupted")
            # The native prefix path still serves unconfigured gpt- names.
            native = mgr.spawn_agent("scope", "native-unit", "exploration",
                                     "hi", str(self.state), "gpt-6.1-sol")
            self.assertFalse(native["dispatched"])
            self.assertEqual(native["family"], "gpt")
            self.assertEqual(native["backend"], "codex_native")
            self.assertEqual(native["native_action"]["tool"], "spawn_agent")
        finally:
            self.interrupt_all(mgr)

    def test_shared_cap_counts_kimi_and_glm_with_family_aware_overflow(self):
        self.mgr.set_concurrency(2)
        kimi = self.spawn("cap-kimi", {"delay": 30}, model="kimi")
        glm = self.spawn("cap-glm", {"delay": 30})
        self.wait_status(kimi["agent_id"], {"running"})
        self.wait_status(glm["agent_id"], {"running"})
        try:
            self.assertEqual(self.mgr.model_catalog([])["kimi_running"], 2)
            # Independent task: finite overflow may suggest native GPT.
            with self.assertRaises(BridgeError) as cm:
                self.spawn("cap-free", {"delay": 1})
            self.assertEqual(cm.exception.code, "capacity_exceeded")
            self.assertEqual(cm.exception.details["fallback"], "gpt")
            self.assertNotIn("diversity_blocked", cm.exception.details)

            # GPT-implemented unit still needs non-GPT tests: with the cap
            # full this waits, never overflowing to GPT and breaking pairing.
            self.mgr.record_authorship("scope", "gpt-unit", "implementation",
                                       "gpt", "parent")
            with self.assertRaises(BridgeError) as cm:
                self.spawn("gpt-unit", {"delay": 1}, role="tests")
            self.assertEqual(cm.exception.code, "capacity_exceeded")
            self.assertTrue(cm.exception.details["diversity_blocked"])
            self.assertIsNone(cm.exception.details["fallback"])
            with self.assertRaises(BridgeError) as cm:
                self.mgr.select_model("scope", "gpt-unit", "tests",
                                      ["gpt-6.1-sol"])
            self.assertEqual(cm.exception.code, "capacity_exceeded")
            self.assertTrue(cm.exception.details["diversity_blocked"])

            # GLM-implemented unit: same-family GLM tests are a conflict even
            # before capacity is considered.
            self.mgr.record_authorship("scope", "glm-unit", "implementation",
                                       "glm", "worker")
            with self.assertRaises(BridgeError) as cm:
                self.spawn("glm-unit", {"delay": 1}, role="tests")
            self.assertEqual(cm.exception.code, "family_conflict")
            # Non-GLM tests for the GLM unit are family-legal, so full
            # capacity may still suggest native GPT overflow.
            with self.assertRaises(BridgeError) as cm:
                self.spawn("glm-unit", {"delay": 1}, role="tests",
                           model="kimi")
            self.assertEqual(cm.exception.code, "capacity_exceeded")
            self.assertEqual(cm.exception.details["fallback"], "gpt")
        finally:
            self.mgr.interrupt_agent(kimi["agent_id"])
            self.mgr.interrupt_agent(glm["agent_id"])
        freed = self.spawn("cap-freed", {"text": "again"})
        self.assertEqual(self.wait_status(freed["agent_id"])["status"],
                         "completed")

    def test_three_way_tie_rotates_through_every_family(self):
        config = Path(self.tmp.name) / "rotate" / "config.toml"
        config.parent.mkdir()
        config.write_text(ROTATION_CONFIG % {"key_env": KEY_ENV})
        mgr = self.manager(config)
        picks = [mgr.select_model("scope", "rotate", "implementation",
                                  ["gpt-6-astra"], complexity="complex")["selected"]["family"]
                 for _ in range(6)]
        for window in (picks[0:3], picks[3:6], picks[1:4], picks[2:5]):
            self.assertEqual(sorted(window), ["glm", "gpt", "kimi"], picks)
        for earlier, later in zip(picks, picks[1:]):
            self.assertNotEqual(earlier, later, picks)

        selected = mgr.select_model("scope", "rotate", "implementation",
                                    ["gpt-6-astra"], complexity="complex", avoid_family="glm")
        self.assertNotEqual(selected["selected"]["family"], "glm")
        with self.assertRaises(BridgeError) as cm:
            mgr.select_model("scope", "rotate", "implementation",
                             ["gpt-6-astra"], complexity="complex", avoid_family="qwen")
        self.assertEqual(cm.exception.code, "invalid_argument")
        self.assertIn("glm", cm.exception.details["allowed_families"])

    def test_glm_authorship_and_paired_role_conflicts_both_directions(self):
        recorded = self.mgr.record_authorship("scope", "u1", "implementation",
                                              "glm", "glm-worker")
        self.assertEqual(recorded["family"], "glm")
        # Same family for both paired roles is rejected either way.
        with self.assertRaises(BridgeError) as cm:
            self.mgr.record_authorship("scope", "u1", "tests", "glm", "other")
        self.assertEqual(cm.exception.code, "family_conflict")
        with self.assertRaises(BridgeError) as cm:
            self.spawn("u1", "work", role="tests")
        self.assertEqual(cm.exception.code, "family_conflict")
        # A different family is accepted; Kimi counts as distinct from GLM.
        selected = self.mgr.select_model("scope", "u1", "tests",
                                         ["gpt-6.1-sol"])
        self.assertNotEqual(selected["selected"]["family"], "glm")

        # Test-first is equally binding.
        self.mgr.record_authorship("scope", "u2", "tests", "glm", "glm-tester")
        with self.assertRaises(BridgeError) as cm:
            self.spawn("u2", "work", role="implementation")
        self.assertEqual(cm.exception.code, "family_conflict")
        impl = self.spawn("u2", {"text": "impl"}, role="implementation",
                          model="kimi")
        self.assertEqual(self.wait_status(impl["agent_id"])["family"], "kimi")

        with self.assertRaises(BridgeError) as cm:
            self.mgr.record_authorship("scope", "u3", "implementation",
                                       "qwen", "who")
        self.assertEqual(cm.exception.code, "invalid_argument")

    def test_same_session_glm_variant_switch_and_message_lifecycle(self):
        agent = self.spawn("switch", {"text": "alpha"}, role="implementation")
        agent = self.wait_status(agent["agent_id"])
        session = agent["session_id"]
        self.assertIn("alpha", agent["last_result"])

        delivery = self.mgr.send_message(agent["agent_id"], "queued-beta")
        self.assertEqual(delivery["delivery"], "queued_until_followup")
        self.assertEqual(
            self.mgr.get_agent(agent["agent_id"])["queued_message_count"], 1)

        # Idle follow-up may switch GLM variants, keeping the session.
        again = self.mgr.followup_task(agent["agent_id"],
                                       json.dumps({"text": "gamma"}),
                                       model="glm-fast")
        self.assertEqual(again["status"], "starting")
        agent = self.wait_status(agent["agent_id"])
        self.assertEqual(agent["session_id"], session)
        self.assertEqual(agent["turn"], 2)
        self.assertEqual(agent["model"], "glm-fast")
        self.assertEqual(agent["family"], "glm")
        self.assertLess(agent["last_result"].index("queued-beta"),
                        agent["last_result"].index("gamma"))
        with sqlite3.connect(self.state / "agents.sqlite3") as db:
            family = db.execute(
                "SELECT family FROM claims WHERE owner=?",
                (agent["agent_id"],)).fetchone()[0]
        self.assertEqual(family, "glm")

        waited = self.mgr.wait_agent([agent["agent_id"]], timeout_ms=1000)
        self.assertFalse(waited["timed_out"])
        self.assertEqual(waited["agents"][0]["family"], "glm")

    def test_rejected_cross_family_followup_mutates_nothing(self):
        agent = self.spawn("paired", {"text": "one"}, role="implementation")
        agent = self.wait_status(agent["agent_id"])
        claims_before = self.row(agent["agent_id"])["family"]
        cap_before = self.mgr._capacity()
        with self.assertRaises(BridgeError) as cm:
            self.mgr.followup_task(agent["agent_id"], "switch please",
                                   model="kimi")
        self.assertEqual(cm.exception.code, "family_conflict")
        after = self.mgr.get_agent(agent["agent_id"])
        self.assertEqual(after["status"], "completed")
        self.assertEqual(after["model"], "glm")
        self.assertEqual(after["family"], "glm")
        self.assertEqual(after["queued_message_count"], 0)
        self.assertEqual(after["session_id"], agent["session_id"])
        self.assertEqual(self.mgr._capacity(), cap_before)
        with sqlite3.connect(self.state / "agents.sqlite3") as db:
            claim = db.execute(
                "SELECT family FROM claims WHERE owner=?",
                (agent["agent_id"],)).fetchone()
        self.assertEqual(claim[0], claims_before)
        self.assertEqual(self.mgr._slots_used(), 0)

    def test_exploration_role_may_switch_cli_families(self):
        agent = self.spawn("free-role", {"text": "look"}, role="exploration")
        agent = self.wait_status(agent["agent_id"])
        session = agent["session_id"]
        switched = self.mgr.followup_task(agent["agent_id"],
                                          json.dumps({"text": "more"}),
                                          model="kimi")
        self.assertEqual(switched["status"], "starting")
        agent = self.wait_status(agent["agent_id"])
        self.assertEqual(agent["family"], "kimi")
        self.assertEqual(agent["model"], "kimi")
        self.assertEqual(agent["session_id"], session)
        self.assertIn("RESUMED %s" % session, agent["last_result"])
        review = self.spawn("free-review", {"text": "check"}, role="review",
                            model="glm-fast")
        self.assertEqual(self.wait_status(review["agent_id"])["family"], "glm")

    def _allow_in_default(self, aliases):
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
        self.mgr.save_template("default", payload,
                               expected_revision=default["revision"])

    def test_config_alias_family_change_is_revalidated_both_directions(self):
        # Alias starts as GLM; becomes Kimi. The existing GLM implementation
        # claim must not be silently reinterpreted as Kimi.
        swap = self.home / "config.toml"
        agent = self.spawn("swap-unit", {"text": "v1"}, role="implementation",
                           model="glm")
        agent = self.wait_status(agent["agent_id"])
        swap.write_text(BASE_CONFIG % {"key_env": KEY_ENV} +
                        '\n[models.swap]\nmodel = "k3"\n'
                        'provider = "managed:kimi-code"\n')
        # Grant the new alias in the saved default template after the config
        # rewrite so the follow-up reaches the family-conflict check.
        self._allow_in_default(["swap"])
        with self.assertRaises(BridgeError) as cm:
            self.mgr.followup_task(agent["agent_id"], "continue",
                                   model="swap")
        self.assertEqual(cm.exception.code, "family_conflict")
        after = self.mgr.get_agent(agent["agent_id"])
        self.assertEqual(after["family"], "glm")
        self.assertEqual(after["model"], "glm")

        # Reverse: alias starts as Kimi, becomes GLM.
        swap.write_text(BASE_CONFIG % {"key_env": KEY_ENV} +
                        '\n[models.swap2]\nmodel = "k3"\n'
                        'provider = "managed:kimi-code"\n')
        self._allow_in_default(["swap2"])
        other = self.spawn("swap2-unit", {"text": "v1"},
                           role="implementation", model="swap2")
        other = self.wait_status(other["agent_id"])
        self.assertEqual(other["family"], "kimi")
        swap.write_text(BASE_CONFIG % {"key_env": KEY_ENV} +
                        '\n[models.swap2]\nmodel = "glm-5.2"\n'
                        'provider = "zai"\n')
        with self.assertRaises(BridgeError) as cm:
            self.mgr.followup_task(other["agent_id"], "continue",
                                   model="swap2")
        self.assertEqual(cm.exception.code, "family_conflict")
        self.assertEqual(self.mgr.get_agent(other["agent_id"])["family"],
                         "kimi")

    def test_cancel_kills_descendants_and_frees_shared_slot(self):
        agent = self.spawn("cancel", {"descendant": 30, "delay": 30})
        self.wait_status(agent["agent_id"], {"running"})
        child_pid = self.row(agent["agent_id"])["child_pid"]
        agent = self.mgr.interrupt_agent(agent["agent_id"])
        self.assertEqual(agent["status"], "interrupted")
        self.assertFalse(self._group_alive(child_pid))
        replacement = self.spawn("replacement", {"text": "reused"})
        self.assertEqual(self.wait_status(replacement["agent_id"])["status"],
                         "completed")

    def test_dead_glm_worker_recovers_with_preserved_session(self):
        agent = self.spawn("dead", {"delay": 30})
        agent_id = agent["agent_id"]
        self.wait_status(agent_id, {"running"})
        end = time.monotonic() + 10
        while time.monotonic() < end:
            row = self.row(agent_id)
            if row["child_pid"] and row["session_id"]:
                break
            time.sleep(0.1)
        self.assertTrue(row["child_pid"] and row["session_id"])
        worker_pid, child_pid, session = (row["worker_pid"], row["child_pid"],
                                          row["session_id"])
        os.kill(worker_pid, signal.SIGKILL)
        time.sleep(1.5)
        self.assertEqual(self.mgr.get_agent(agent_id)["status"], "orphaned")
        os.killpg(child_pid, signal.SIGKILL)
        restarted = self.manager()
        try:
            recovered = self.wait_status(agent_id, mgr=restarted)
            self.assertEqual(recovered["status"], "interrupted")
            self.assertEqual(recovered["family"], "glm")
            self.assertEqual(recovered["session_id"], session)
            restarted.followup_task(agent_id, json.dumps({"text": "back"}))
            resumed = self.wait_status(agent_id, mgr=restarted)
            self.assertEqual(resumed["status"], "completed")
            self.assertIn("RESUMED %s" % session, resumed["last_result"])
        finally:
            self.interrupt_all(restarted)

    def test_prune_covers_terminal_glm_but_not_native_gpt(self):
        now = time.time()

        def insert(agent_id, family):
            with sqlite3.connect(self.state / "agents.sqlite3") as db:
                db.execute(
                    """INSERT INTO agents
                       (id,scope,unit,role,family,model,cwd,status,slot,
                        profile,timeout,created,updated,command,config)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (agent_id, "scope", "unit", "exploration", family, "glm",
                     str(self.state), "completed", None, "worker", 900,
                     now, now, json.dumps([sys.executable, str(FAKE)]),
                     str(self.config)))

        glm_id, gpt_id = "kimi:" + "a" * 32, "native-agent-9"
        insert(glm_id, "glm")
        insert(gpt_id, "gpt")
        for agent_id in (glm_id, gpt_id):
            log = self.state / (agent_id.replace(":", "-") + "-turn-1.jsonl")
            log.write_text('{"role":"assistant","content":"old"}\n')
            os.utime(log, (now - 86400 * 60, now - 86400 * 60))
        result = self.mgr.prune_logs(older_than_days=30, dry_run=True)
        self.assertIn(glm_id.replace(":", "-") + "-turn-1.jsonl",
                      result["files"])
        self.assertNotIn(gpt_id + "-turn-1.jsonl", result["files"])

    @staticmethod
    def _group_alive(pgid):
        out = subprocess.run(["ps", "-axo", "pgid=,stat="],
                             capture_output=True, text=True).stdout
        for line in out.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[0] == str(pgid) \
                    and not parts[1].startswith("Z"):
                return True
        return False


class GlmDoctorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / "state"
        self.home = Path(self.tmp.name) / "glm_home"
        self.home.mkdir()
        self.config = self.home / "config.toml"
        self.config.write_text(
            '[providers.zai]\napi_key_env = "%s"\n'
            '[models.glm]\nprovider = "zai"\nmodel = "glm-5.2"\n'
            '[models.deepseek]\nprovider = "zai"\nmodel = "deepseek-v4"\n'
            % KEY_ENV)
        os.environ[KEY_ENV] = "fake-" + _secrets.token_urlsafe(16)
        self.addCleanup(os.environ.pop, KEY_ENV, None)
        self.mgr = AgentManager(state_dir=str(self.state),
                                config_path=str(self.config),
                                kimi_command=[sys.executable, str(FAKE)])

    def test_glm_only_configuration_is_ready_and_value_free(self):
        report = self.mgr.doctor()
        self.assertTrue(report["ready"], report)
        self.assertEqual(report["account_access"], "not_probed")
        statuses = {c["name"]: c["status"] for c in report["checks"]}
        self.assertEqual(statuses["models"], "ok")
        self.assertEqual(statuses["credentials"], "ok")
        models = next(c for c in report["checks"] if c["name"] == "models")
        self.assertEqual(models["supported_aliases"], ["glm"])
        self.assertEqual(models["unsupported_aliases"], ["deepseek"])
        self.assertNotIn(os.environ[KEY_ENV], json.dumps(report))


class GlmMcpSchemaTests(unittest.TestCase):
    def test_family_and_avoid_family_enums_cover_glm(self):
        auth = server.tool_schema("record_authorship")
        families = auth["inputSchema"]["properties"]["family"]["enum"]
        self.assertEqual(families, ["kimi", "glm", "gpt"])
        select = server.tool_schema("select_model")
        avoid = select["inputSchema"]["properties"]["avoid_family"]["enum"]
        self.assertIn("glm", avoid)
        self.assertIn(None, avoid)
        self.assertIn("GLM", server.DESCRIPTIONS["spawn_agent"])
        self.assertIn("GLM", server.DESCRIPTIONS["model_catalog"])
        self.assertIn("GLM", server.DESCRIPTIONS["set_concurrency"])
        self.assertIn("families", server.DESCRIPTIONS["followup_task"])


if __name__ == "__main__":
    unittest.main()
