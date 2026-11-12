"""Shareable beta (0.2.0) regressions for the GPT-authored runtime/MCP.

These tests derive behavioral contracts and negative cases from
SHARE_SPEC.md and docs/SPEC.md. They use only tempfile state and the
fake CLI; no production configuration or credentials are touched.
"""
from __future__ import annotations

import fcntl
import json
import os
import secrets as _secrets
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

BRIDGE = Path(__file__).resolve().parents[1] / "plugins/mixed-agents/bridge"
sys.path.insert(0, str(BRIDGE))
import runtime  # noqa: E402
from runtime import AgentManager, BridgeError  # noqa: E402
import security  # noqa: E402
import server  # noqa: E402

FAKE = Path(__file__).with_name("fake_cli.py")
TERMINAL = runtime.TERMINAL
ACTIVE = runtime.ACTIVE


def _env_text() -> str:
    """Return a value-free assertion message for leak checks."""
    return "configured secret value leaked into observable output"


def _make_secret() -> str:
    """Runtime-only token-shaped string; never committed literally."""
    return _secrets.token_urlsafe(32)


class ShareConfigEnvTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / "state"
        self.state.mkdir(parents=True, exist_ok=True)
        self.real_home = Path(self.tmp.name) / "real_kimi_home"
        self.real_home.mkdir()
        self.config = self.real_home / "config.toml"
        self.config.write_text(
            '[providers.managed]\napi_key_env = "TEST_SHARE_API_KEY"\n'
            '[models.k3]\nprovider = "managed"\nmodel = "k3"\n'
        )
        os.environ["TEST_SHARE_API_KEY"] = _make_secret()
        self.supervisor_home = Path(self.tmp.name) / "supervisor_kimi_home"
        self.supervisor_home.mkdir()
        (self.supervisor_home / "config.toml").write_text("[models]\n")
        self._orig_home = os.environ.get("KIMI_CODE_HOME")

    def tearDown(self):
        if self._orig_home is None:
            os.environ.pop("KIMI_CODE_HOME", None)
        else:
            os.environ["KIMI_CODE_HOME"] = self._orig_home

    def manager(self, config_path=None):
        return AgentManager(
            state_dir=str(self.state),
            config_path=str(config_path or self.config),
            kimi_command=[sys.executable, str(FAKE)],
        )

    def wait_terminal(self, mgr, agent_id, deadline=30):
        end = time.monotonic() + deadline
        while time.monotonic() < end:
            agent = mgr.get_agent(agent_id)
            if agent["status"] in TERMINAL:
                return agent
            time.sleep(0.05)
        self.fail("agent %s did not terminate" % agent_id)

    def interrupt_all(self, mgr):
        for agent in mgr.list_agents()["agents"]:
            if agent["status"] not in TERMINAL:
                try:
                    mgr.interrupt_agent(agent["agent_id"])
                except Exception:
                    pass

    def test_kimi_env_uses_stored_config_parent_and_does_not_mutate_supervisor(self):
        os.environ["KIMI_CODE_HOME"] = str(self.supervisor_home)
        mgr = self.manager()
        env = mgr._kimi_env(mgr.config)
        self.assertEqual(env["KIMI_CODE_HOME"], str(self.real_home.resolve()))
        self.assertEqual(os.environ["KIMI_CODE_HOME"], str(self.supervisor_home))
        self.assertIsNot(env, os.environ)

    def test_first_turn_launches_child_with_stored_config_home(self):
        os.environ["KIMI_CODE_HOME"] = str(self.supervisor_home)
        mgr = self.manager()
        try:
            agent = mgr.spawn_agent("scope", "first-home", "exploration",
                                    "hello", str(self.state), "k3")
            agent = self.wait_terminal(mgr, agent["agent_id"])
            expected = "ENV:%s" % self.real_home.resolve()
            self.assertIn(expected, agent["last_result"])
        finally:
            self.interrupt_all(mgr)

    def test_resumed_turn_uses_same_stored_config_home(self):
        os.environ["KIMI_CODE_HOME"] = str(self.supervisor_home)
        mgr = self.manager()
        try:
            first = mgr.spawn_agent("scope", "resume-home", "exploration",
                                    "hello", str(self.state), "k3")
            first = self.wait_terminal(mgr, first["agent_id"])
            session = first["session_id"]
            self.assertTrue(session)
            os.environ["KIMI_CODE_HOME"] = str(self.supervisor_home / "elsewhere")
            resumed = mgr.followup_task(first["agent_id"], "continue")
            resumed = self.wait_terminal(mgr, resumed["agent_id"])
            self.assertIn("ENV:%s" % self.real_home.resolve(), resumed["last_result"])
            self.assertIn("RESUMED %s" % session, resumed["last_result"])
            self.assertEqual(resumed["turn"], 2)
            self.assertEqual(resumed["session_id"], session)
        finally:
            self.interrupt_all(mgr)

    def test_invalid_basename_rejected_before_claims_slots_or_queue(self):
        bad = self.state / "config.ini"
        bad.write_text("[models]\n")
        mgr = self.manager(config_path=bad)
        with self.assertRaises(BridgeError) as cm:
            mgr.spawn_agent("scope", "bad-base", "exploration", "hello",
                            str(self.state), "k3")
        self.assertEqual(cm.exception.code, "unsupported_config_path")
        db = sqlite3.connect(mgr.db_path)
        try:
            self.assertEqual(db.execute("SELECT count(*) FROM agents").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM claims").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM inbox").fetchone()[0], 0)
        finally:
            db.close()
        self.assertEqual(list(mgr.state.glob("kimi-slot-*.lock")), [])

    def test_config_toml_symlink_supported(self):
        link_dir = Path(self.tmp.name) / "link_home"
        link_dir.mkdir()
        link_config = link_dir / "config.toml"
        link_config.symlink_to(self.config)
        mgr = self.manager(config_path=link_config)
        try:
            agent = mgr.spawn_agent("scope", "symlink", "exploration",
                                    "hello", str(self.state), "k3")
            agent = self.wait_terminal(mgr, agent["agent_id"])
            self.assertIn("ENV:%s" % link_dir.resolve(), agent["last_result"])
            row = mgr._row(agent["agent_id"])
            self.assertEqual(Path(row["config"]).parent.resolve(), link_dir.resolve())
        finally:
            self.interrupt_all(mgr)


class ShareDoctorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / "state"
        self.home = Path(self.tmp.name) / "kimi_home"
        self.home.mkdir()
        self.config = self.home / "config.toml"
        self.config.write_text(
            '[providers.managed]\napi_key_env = "TEST_DOCTOR_API_KEY"\n'
            '[models.k3]\nprovider = "managed"\nmodel = "k3"\n'
        )
        os.environ["TEST_DOCTOR_API_KEY"] = _make_secret()
        self.mgr = AgentManager(
            state_dir=str(self.state),
            config_path=str(self.config),
            kimi_command=[sys.executable, str(FAKE)],
        )

    def tearDown(self):
        os.environ.pop("TEST_DOCTOR_API_KEY", None)

    def _check(self, mgr=None):
        return (mgr or self.mgr).doctor()

    def _no_secret_leak(self, obj, secret: str):
        text = json.dumps(obj)
        self.assertTrue(secret not in text, _env_text())

    def test_doctor_ready_with_fake_cli_and_value_free_output(self):
        report = self._check()
        self.assertTrue(report["ready"])
        self.assertEqual(report["account_access"], "not_probed")
        self.assertEqual(report["native_gpt"], "host_managed_not_probed")
        statuses = {c["name"]: c["status"] for c in report["checks"]}
        for name in ("platform", "python", "kimi_executable", "kimi_version",
                     "kimi_flags", "kimi_config", "models", "credentials",
                     "concurrency"):
            self.assertIn(name, statuses)
        self.assertEqual(statuses["kimi_version"], "ok")
        self.assertEqual(statuses["kimi_flags"], "ok")
        cred = next(c for c in report["checks"] if c["name"] == "credentials")
        self.assertEqual(cred["status"], "ok")
        sources = cred["sources"]
        self.assertTrue(any(s["source"] == "api_key_env" for s in sources))
        self._no_secret_leak(report, os.environ["TEST_DOCTOR_API_KEY"])

    def test_doctor_reports_missing_executable(self):
        mgr = AgentManager(
            state_dir=str(self.state),
            config_path=str(self.config),
            kimi_command=["no_such_kimi_binary_xyz"],
        )
        report = self._check(mgr)
        self.assertFalse(report["ready"])
        statuses = {c["name"]: c["status"] for c in report["checks"]}
        self.assertEqual(statuses["kimi_executable"], "error")

    def test_doctor_reports_bad_version(self):
        script = Path(self.tmp.name) / "bad_version.py"
        script.write_text(
            "import sys\n"
            "if '--version' in sys.argv:\n"
            "    print('0.0.1')\n"
            "elif '--help' in sys.argv:\n"
            "    print('--model --session --agent-file --prompt --output-format')\n"
            "sys.exit(0)\n"
        )
        mgr = AgentManager(
            state_dir=str(self.state),
            config_path=str(self.config),
            kimi_command=[sys.executable, str(script)],
        )
        report = self._check(mgr)
        statuses = {c["name"]: c["status"] for c in report["checks"]}
        self.assertEqual(statuses["kimi_version"], "error")

    def test_doctor_reports_missing_required_flags(self):
        script = Path(self.tmp.name) / "bad_help.py"
        script.write_text(
            "import sys\n"
            "if '--version' in sys.argv:\n"
            "    print('2.1.1')\n"
            "elif '--help' in sys.argv:\n"
            "    print('--model --agent-file --prompt --output-format')\n"
            "sys.exit(0)\n"
        )
        mgr = AgentManager(
            state_dir=str(self.state),
            config_path=str(self.config),
            kimi_command=[sys.executable, str(script)],
        )
        report = self._check(mgr)
        statuses = {c["name"]: c["status"] for c in report["checks"]}
        self.assertEqual(statuses["kimi_flags"], "error")
        missing = next(c for c in report["checks"] if c["name"] == "kimi_flags")["missing"]
        self.assertIn("--session", missing)

    def test_doctor_invalid_toml_without_source_leak(self):
        raw = "this is not ] valid toml {"
        self.config.write_text(raw)
        report = self._check()
        statuses = {c["name"]: c["status"] for c in report["checks"]}
        self.assertEqual(statuses["kimi_config"], "error")
        text = json.dumps(report)
        self.assertTrue(raw not in text, "raw config content leaked in doctor output")

    def test_doctor_unsupported_aliases_disclosed(self):
        self.config.write_text(
            '[providers.managed]\napi_key_env = "TEST_DOCTOR_API_KEY"\n'
            '[models.bad-suffix]\nprovider = "managed"\nmodel = "kimi-k3-1"\n'
        )
        report = self._check()
        models = next(c for c in report["checks"] if c["name"] == "models")
        self.assertEqual(models["status"], "error")
        self.assertIn("bad-suffix", models["unsupported_aliases"])

    def test_doctor_credential_sources_value_free(self):
        self.config.write_text(
            '[providers.literal]\napi_key = "LITERAL_SECRET"\n'
            '[providers.env]\napi_key_env = "TEST_DOCTOR_API_KEY"\n'
            '[models.k3]\nprovider = "env"\nmodel = "k3"\n'
        )
        report = self._check()
        cred = next(c for c in report["checks"] if c["name"] == "credentials")
        self.assertEqual(cred["status"], "ok")
        sources = {s["alias"]: s["source"] for s in cred["sources"]}
        self.assertEqual(sources["k3"], "api_key_env")
        self._no_secret_leak(report, "LITERAL_SECRET")
        self._no_secret_leak(report, os.environ["TEST_DOCTOR_API_KEY"])


class ShareSecurityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.secret = _make_secret()
        self.config_path = Path(self.tmp.name) / "config.toml"
        self.config_path.write_text(
            f'[providers.a]\napi_key = "{self.secret}"\n'
            'access_token = "ACCESS_SECRET"\n'
            'client_secret = "CLIENT_SECRET"\n'
            'password = "PASSWORD_SECRET"\n'
            'api_key_env = "TEST_ROTATION_KEY"\n'
            '[models.k3]\nprovider = "a"\nmodel = "k3"\n'
        )
        os.environ["TEST_ROTATION_KEY"] = _make_secret()

    def tearDown(self):
        os.environ.pop("TEST_ROTATION_KEY", None)

    def _no_secret_leak(self, obj, secret: str):
        text = json.dumps(obj, default=str)
        self.assertTrue(secret not in text, "configured secret value leaked")

    def test_configured_secrets_detect_various_keys_and_env_rotation(self):
        first = security.configured_secrets(self.config_path)
        self.assertTrue(self.secret in first)
        self.assertTrue("ACCESS_SECRET" in first)
        self.assertTrue("CLIENT_SECRET" in first)
        self.assertTrue("PASSWORD_SECRET" in first)
        self.assertTrue(os.environ["TEST_ROTATION_KEY"] in first)
        # Rotation without file change must be reflected.
        rotated = _make_secret()
        os.environ["TEST_ROTATION_KEY"] = rotated
        second = security.configured_secrets(self.config_path)
        self.assertTrue(rotated in second)
        # Longest-first ordering helps avoid partial replacements.
        lengths = [len(v) for v in second]
        self.assertEqual(lengths, sorted(lengths, reverse=True))

    def test_redact_patterns_assignments_bearer_and_query_params(self):
        sk = "sk-" + _secrets.token_urlsafe(32)
        # GitHub PATs use only alphanumerics and underscores after the prefix.
        ghp = "ghp_" + "".join(_secrets.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_")
                                for _ in range(36))
        aws = "AKIA" + "".join(_secrets.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
                                for _ in range(16))
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0." + _secrets.token_urlsafe(43)
        bearer = "Bearer " + _secrets.token_urlsafe(32)
        unconfigured = "unconfigured_literal_" + _secrets.token_hex(16)
        url = "https://example.com/api?api_key=%s&token=%s" % (_secrets.token_hex(16), _secrets.token_hex(16))
        text = " ".join([sk, ghp, aws, jwt, bearer, "api_key = \"%s\"" % unconfigured, url])
        redacted = security.redact(text, security.configured_secrets(self.config_path))
        for value in (sk, ghp, aws, jwt, bearer, unconfigured):
            self.assertTrue(value not in redacted, "token-shaped value leaked in redacted output")
        self.assertIn("[redacted]", redacted)
        self.assertIn("api_key = \"[redacted]\"", redacted)
        self.assertIn('api_key="[redacted]"', redacted)
        self.assertIn('token="[redacted]"', redacted)

    def test_redact_basic_auth_and_unstructured_assignments(self):
        basic1 = "Authorization: Basic " + _secrets.token_urlsafe(24)
        basic2 = "authorization: Basic " + _secrets.token_urlsafe(24)
        unquoted_secret = "secret=" + _secrets.token_urlsafe(16)
        unquoted_token = "token:" + _secrets.token_urlsafe(16)
        unquoted_api_key = "api_key=" + _secrets.token_urlsafe(16)
        text = " | ".join([basic1, basic2, unquoted_secret, unquoted_token, unquoted_api_key])
        redacted = security.redact(text, security.configured_secrets(self.config_path))
        for value in (basic1.split()[-1], basic2.split()[-1],
                      unquoted_secret.split("=", 1)[1],
                      unquoted_token.split(":", 1)[1],
                      unquoted_api_key.split("=", 1)[1]):
            self.assertTrue(value not in redacted, _env_text())
        self.assertIn("[redacted]", redacted)
        self.assertIn('Authorization: "[redacted]"', redacted)

    def test_sanitize_redacts_secret_keys_and_nested_strings(self):
        bearer = "Bearer " + _secrets.token_urlsafe(24)
        payload = {
            "api_key": self.secret,
            "Authorization": bearer,
            "nested": [{"token": self.secret}, "plain"],
            "safe": "visible",
        }
        cleaned = security.sanitize(payload, security.configured_secrets(self.config_path))
        self.assertEqual(cleaned["api_key"], "[redacted]")
        self.assertTrue(self.secret not in cleaned["Authorization"], _env_text())
        self.assertEqual(cleaned["nested"][0]["token"], "[redacted]")
        self.assertEqual(cleaned["safe"], "visible")

    def test_sanitize_redacts_configured_secret_used_as_key(self):
        payload = {self.secret: "value", "plain": "visible"}
        cleaned = security.sanitize(payload, security.configured_secrets(self.config_path))
        self._no_secret_leak(cleaned, self.secret)
        self.assertEqual(cleaned["plain"], "visible")
        self.assertIn("[redacted]", json.dumps(cleaned))

    def test_worker_log_and_result_redact_configured_secrets(self):
        state = Path(self.tmp.name) / "state"
        mgr = AgentManager(
            state_dir=str(state),
            config_path=str(self.config_path),
            kimi_command=[sys.executable, str(FAKE)],
        )
        try:
            prompt = json.dumps({"text": "token %s here" % self.secret})
            agent = mgr.spawn_agent("scope", "secret-log", "exploration",
                                    prompt, str(state), "k3")
            agent = self.wait_terminal(mgr, agent["agent_id"], deadline=30)
            log = state / (agent["agent_id"].replace(":", "-") + "-turn-1.jsonl")
            self.assertTrue(log.exists())
            text = log.read_text(encoding="utf-8")
            self.assertTrue(self.secret not in text, _env_text())
            self.assertTrue(self.secret not in agent["last_result"], _env_text())
        finally:
            self.interrupt_all(mgr)

    def test_worker_stderr_and_error_redact_configured_secrets(self):
        state = Path(self.tmp.name) / "state"
        mgr = AgentManager(
            state_dir=str(state),
            config_path=str(self.config_path),
            kimi_command=[sys.executable, str(FAKE)],
        )
        try:
            prompt = json.dumps({"exit": 3, "stderr": "boom %s" % self.secret})
            agent = mgr.spawn_agent("scope", "secret-err", "exploration",
                                    prompt, str(state), "k3")
            agent = self.wait_terminal(mgr, agent["agent_id"], deadline=30)
            self.assertEqual(agent["status"], "failed")
            self.assertTrue(self.secret not in agent["error"], _env_text())
            self.assertIn("exited 3", agent["error"])
        finally:
            self.interrupt_all(mgr)

    def wait_terminal(self, mgr, agent_id, deadline=30):
        end = time.monotonic() + deadline
        while time.monotonic() < end:
            agent = mgr.get_agent(agent_id)
            if agent["status"] in TERMINAL:
                return agent
            time.sleep(0.05)
        self.fail("agent %s did not terminate" % agent_id)

    def interrupt_all(self, mgr):
        for agent in mgr.list_agents()["agents"]:
            if agent["status"] not in TERMINAL:
                try:
                    mgr.interrupt_agent(agent["agent_id"])
                except Exception:
                    pass


class ShareServerSanitizationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.secret = _make_secret()
        self.home = Path(self.tmp.name) / "home"
        self.home.mkdir()
        self.config = self.home / "config.toml"
        self.config.write_text(
            f'[providers.a]\napi_key = "{self.secret}"\n'
            '[models.k3]\nprovider = "a"\nmodel = "k3"\n'
        )
        self.state = Path(self.tmp.name) / "state"
        self.mgr = AgentManager(
            state_dir=str(self.state),
            config_path=str(self.config),
            kimi_command=[sys.executable, str(FAKE)],
        )
        self._orig_list_agents = self.mgr.list_agents

    def tearDown(self):
        self.mgr.list_agents = self._orig_list_agents
        for agent in self.mgr.list_agents()["agents"]:
            if agent["status"] not in TERMINAL:
                try:
                    self.mgr.interrupt_agent(agent["agent_id"])
                except Exception:
                    pass

    def _wait_terminal(self, agent_id, deadline=30):
        end = time.monotonic() + deadline
        while time.monotonic() < end:
            agent = self.mgr.get_agent(agent_id)
            if agent["status"] in TERMINAL:
                return agent
            time.sleep(0.05)
        self.fail("agent %s did not terminate" % agent_id)

    def _no_secret_leak(self, obj, secret: str):
        text = json.dumps(obj, default=str)
        self.assertTrue(secret not in text, "configured secret value leaked")

    def test_server_tools_call_output_sanitized(self):
        prompt = json.dumps({"text": "server leak %s" % self.secret})
        agent = self.mgr.spawn_agent("scope", "server-out", "exploration",
                                     prompt, str(self.state), "k3")
        agent = self._wait_terminal(agent["agent_id"])
        request = {
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "get_agent",
                       "arguments": {"agent_id": agent["agent_id"]}},
        }
        response = server.handle(self.mgr, request)
        self.assertFalse(response["result"]["isError"])
        text = response["result"]["content"][0]["text"]
        structured = response["result"]["structuredContent"]
        self._no_secret_leak(text, self.secret)
        self._no_secret_leak(structured, self.secret)
        self.assertIn("server-out", text)

    def test_server_error_response_sanitized_and_readable(self):
        bad_id = "unknown-" + self.secret
        request = {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "get_agent",
                       "arguments": {"agent_id": bad_id}},
        }
        response = server.handle(self.mgr, request)
        self.assertTrue(response["result"]["isError"])
        text = response["result"]["content"][0]["text"]
        structured = response["result"]["structuredContent"]
        self._no_secret_leak(text, self.secret)
        self._no_secret_leak(structured, self.secret)
        self.assertIn("Unknown agent ID", text)

    def test_invoke_sanitizes_configured_secret_keys_and_values(self):
        def fake_list():
            return {self.secret: "value", "plain": "visible", "api_key": self.secret}

        self.mgr.list_agents = fake_list
        result = server.invoke(self.mgr, "list_agents", {})
        self._no_secret_leak(result, self.secret)
        self.assertEqual(result["plain"], "visible")
        self.assertIn("[redacted]", json.dumps(result))


class SharePruneTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / "state"
        self.home = Path(self.tmp.name) / "kimi_home"
        self.home.mkdir()
        self.config = self.home / "config.toml"
        self.config.write_text(
            '[providers.managed]\napi_key_env = "TEST_PRUNE_API_KEY"\n'
            '[models.k3]\nprovider = "managed"\nmodel = "k3"\n'
        )
        os.environ["TEST_PRUNE_API_KEY"] = _make_secret()
        self.mgr = AgentManager(
            state_dir=str(self.state),
            config_path=str(self.config),
            kimi_command=[sys.executable, str(FAKE)],
        )

    def tearDown(self):
        os.environ.pop("TEST_PRUNE_API_KEY", None)

    def interrupt_all(self):
        for agent in self.mgr.list_agents()["agents"]:
            if agent["status"] not in TERMINAL:
                try:
                    self.mgr.interrupt_agent(agent["agent_id"])
                except Exception:
                    pass

    def _insert_terminal(self, agent_id: str, status="completed", slot=0, session_id=None):
        now = time.time()
        db = sqlite3.connect(self.mgr.db_path)
        try:
            db.execute(
                """INSERT INTO agents
                   (id,scope,unit,role,family,model,cwd,status,slot,profile,
                    timeout,created,updated,command,config,session_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (agent_id, "scope", "unit", "exploration", "kimi", "fake",
                 str(self.state), status, slot, "worker", 900, now, now,
                 json.dumps([sys.executable, str(FAKE)]), str(self.config),
                 session_id),
            )
            db.commit()
        finally:
            db.close()

    def _old_log(self, agent_id: str, turn: int):
        path = self.state / (agent_id.replace(":", "-") + "-turn-%d.jsonl" % turn)
        path.write_text('{"role":"assistant","content":"old"}\n')
        # well into the past
        os.utime(path, (time.time() - 86400 * 60, time.time() - 86400 * 60))
        return path

    def test_prune_dry_run_lists_old_terminal_logs_without_deleting(self):
        aid = "kimi:" + "a" * 32
        self._insert_terminal(aid)
        log = self._old_log(aid, 1)
        result = self.mgr.prune_logs(older_than_days=30, dry_run=True)
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["deleted"], 0)
        self.assertIn(log.name, result["files"])
        self.assertTrue(log.exists())

    def test_prune_delete_removes_only_listed_files_and_retains_session(self):
        aid = "kimi:" + "b" * 32
        self._insert_terminal(aid, session_id="keep-session")
        log = self._old_log(aid, 1)
        result = self.mgr.prune_logs(older_than_days=30, dry_run=False)
        self.assertFalse(result["dry_run"])
        self.assertEqual(result["deleted"], 1)
        self.assertFalse(log.exists())
        row = self.mgr._row(aid)
        self.assertEqual(row["status"], "completed")
        self.assertIsNotNone(row["session_id"])

    def test_prune_excludes_active_status(self):
        try:
            agent = self.mgr.spawn_agent("scope", "prune-active", "exploration",
                                         json.dumps({"delay": 30}), str(self.state), "k3")
            end = time.monotonic() + 10
            while time.monotonic() < end:
                if self.mgr.get_agent(agent["agent_id"])["status"] in ACTIVE:
                    break
                time.sleep(0.05)
            log = self._old_log(agent["agent_id"], 99)
            result = self.mgr.prune_logs(older_than_days=30, dry_run=True)
            self.assertNotIn(log.name, result["files"])
        finally:
            self.interrupt_all()

    def test_prune_excludes_orphaned_status(self):
        aid = "kimi:" + "c" * 32
        self._insert_terminal(aid, status="orphaned")
        log = self._old_log(aid, 1)
        result = self.mgr.prune_logs(older_than_days=30, dry_run=True)
        self.assertNotIn(log.name, result["files"])

    def test_prune_excludes_held_slot(self):
        aid = "kimi:" + "d" * 32
        self._insert_terminal(aid, status="completed", slot=7)
        log = self._old_log(aid, 1)
        lock_path = self.state / "kimi-slot-7.lock"
        lock_path.touch()
        holder = subprocess.Popen(
            [sys.executable, "-c",
             "import fcntl, os, sys, signal\n"
             "fd = os.open(sys.argv[1], os.O_CREAT|os.O_RDWR, 0o600)\n"
             "fcntl.flock(fd, fcntl.LOCK_EX)\n"
             "sys.stdout.write('locked\\n')\n"
             "sys.stdout.flush()\n"
             "signal.pause()",
             str(lock_path)],
            stdout=subprocess.PIPE, text=True,
        )
        try:
            self.assertTrue("locked" in holder.stdout.readline())
            holder.stdout.close()
            result = self.mgr.prune_logs(older_than_days=30, dry_run=True)
            self.assertNotIn(log.name, result["files"])
        finally:
            holder.terminate()
            try:
                holder.wait(timeout=5)
            except subprocess.TimeoutExpired:
                holder.kill()
                holder.wait()

    def test_prune_excludes_symlinks_and_unrelated_files(self):
        aid = "kimi:" + "e" * 32
        self._insert_terminal(aid)
        real = self._old_log(aid, 1)
        link = self.state / (aid.replace(":", "-") + "-turn-2.jsonl")
        link.symlink_to(real)
        unrelated = self.state / "not-a-kimi-log.jsonl"
        unrelated.write_text("old")
        os.utime(unrelated, (time.time() - 86400 * 60, time.time() - 86400 * 60))
        result = self.mgr.prune_logs(older_than_days=30, dry_run=True)
        self.assertIn(real.name, result["files"])
        self.assertNotIn(link.name, result["files"])
        self.assertNotIn(unrelated.name, result["files"])

    def test_prune_invalid_arguments(self):
        with self.assertRaises(BridgeError):
            self.mgr.prune_logs(older_than_days=-1)
        with self.assertRaises(BridgeError):
            self.mgr.prune_logs(dry_run="yes")


if __name__ == "__main__":
    unittest.main()
