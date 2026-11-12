"""Durable Kimi CLI workers and native Codex handoffs. Standard library only."""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import uuid

try:
    from . import catalog, configuration, cost_policy, kimi_usage, materialization, providers, security, zai
except ImportError:
    import catalog, configuration, cost_policy, kimi_usage, materialization, providers, security, zai

CAP = 5
ZAI_POLL_SECONDS = 5.0
PROBE_TIMEOUT_SECONDS = 30.0
PROBE_MAX_OUTPUT_TOKENS = 128
PROBE_PROMPT = "Reply with exactly: ok"
GUARD_MODULES = {"zai": zai, "kimi": kimi_usage}
GUARD_LABELS = {"zai": "Z.ai", "kimi": "Kimi"}
ROLES = {"implementation", "tests", "review", "exploration", "research"}
ACTIVE = {"starting", "running", "cancelling"}
TERMINAL = {"completed", "interrupted", "failed", "orphaned"}
ROOT = Path(__file__).resolve().parents[1]


class BridgeError(Exception):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details

    def as_dict(self):
        return {"error": self.code, "message": self.message, **self.details}


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise BridgeError("invalid_argument", f"{name} must be a nonempty string")
    return value


def _integer(value, name, low, high):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise BridgeError("invalid_argument", f"{name} must be between {low} and {high}")
    return value


def _redact(text, config_path=None):
    return security.redact(text, security.configured_secrets(config_path or catalog.default_config_path()))


def _identity(pid):
    if not pid:
        return None
    # lstart plus process state avoids signalling a reused PID or a zombie.
    p = subprocess.run(["ps", "-p", str(pid), "-o", "lstart=,stat="],
                       capture_output=True, text=True)
    value = p.stdout.strip()
    if p.returncode or not value or "Z" in value.split()[-1]:
        return None
    return " ".join(value.split()[:-1])


class AgentManager:
    def __init__(self, state_dir=None, kimi_command=None, config_path=None):
        self.state = Path(state_dir or os.environ.get("MIXED_AGENTS_STATE_DIR") or
                          Path.home() / ".local/state/codex-mixed-agents").expanduser().resolve()
        self.state.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.state.chmod(0o700)
        configured_path = Path(config_path or catalog.default_config_path()).expanduser()
        # Canonical spelling: detached workers re-read the stored row config from
        # their own cwd, so relative paths must be resolved here; a symlinked
        # config.toml keeps its own directory as KIMI_CODE_HOME.
        self.config = configured_path.parent.resolve() / configured_path.name
        self.command = kimi_command or [shutil.which("kimi") or str(Path.home() / ".kimi-code/bin/kimi")]
        if not isinstance(self.command, list) or not self.command or not all(isinstance(x, str) for x in self.command):
            raise BridgeError("invalid_argument", "kimi_command must be a nonempty argument list")
        self.db_path = self.state / "agents.sqlite3"
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS agents (
                    id TEXT PRIMARY KEY, scope TEXT, unit TEXT, role TEXT, family TEXT,
                    model TEXT, cwd TEXT, status TEXT, turn INTEGER DEFAULT 0,
                    session_id TEXT, result TEXT DEFAULT '', progress TEXT DEFAULT '',
                    error TEXT DEFAULT '', worker_pid INTEGER, worker_identity TEXT,
                    child_pid INTEGER, child_identity TEXT, slot INTEGER,
                    profile TEXT, timeout INTEGER, cancel INTEGER DEFAULT 0,
                    created REAL, updated REAL, command TEXT, config TEXT,
                    provider TEXT
                );
                CREATE TABLE IF NOT EXISTS claims (
                    owner TEXT PRIMARY KEY, scope TEXT, unit TEXT, role TEXT,
                    family TEXT, kind TEXT, model TEXT, author TEXT
                );
                CREATE TABLE IF NOT EXISTS inbox (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, agent_id TEXT, message TEXT
                );
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE IF NOT EXISTS slot_accounts (slot INTEGER PRIMARY KEY, fingerprint TEXT, prefix TEXT);
                CREATE TABLE IF NOT EXISTS observations (
                    model TEXT PRIMARY KEY, successes INTEGER DEFAULT 0,
                    failures INTEGER DEFAULT 0, last_error TEXT DEFAULT ''
                );
            """)
            # The legacy migration runs under BEGIN IMMEDIATE so concurrent
            # initializers serialize on the write lock instead of racing
            # duplicate ALTER TABLE statements.
            db.execute("BEGIN IMMEDIATE")
            columns = {row[1] for row in db.execute("PRAGMA table_info(agents)")}
            if "provider" not in columns:
                try:
                    # Legacy rows keep NULL and are re-classified from their
                    # stored row config at accounting time.
                    db.execute("ALTER TABLE agents ADD COLUMN provider TEXT")
                except sqlite3.OperationalError:
                    columns = {row[1] for row in db.execute("PRAGMA table_info(agents)")}
                    if "provider" not in columns:
                        raise
            for name in ('project_id', 'configuration_snapshot'):
                if name not in columns:
                    db.execute(f"ALTER TABLE agents ADD COLUMN {name} TEXT")
            db.execute("CREATE TABLE IF NOT EXISTS turn_snapshots (agent_id TEXT, turn INTEGER, resolution TEXT NOT NULL, PRIMARY KEY(agent_id, turn))")
        self.db_path.chmod(0o600)

    @contextlib.contextmanager
    def _db(self):
        db = sqlite3.connect(self.db_path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=15000")
        try:
            with db:
                yield db
        finally:
            db.close()

    @contextlib.contextmanager
    def _transaction(self):
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            yield db

    def _slot(self, index):
        fd = os.open(self.state / f"kimi-slot-{index}.lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return None
        return fd

    def _slots_used(self):
        used = 0
        for path in self.state.glob("kimi-slot-*.lock"):
            match = re.fullmatch(r"kimi-slot-(\d+)\.lock", path.name)
            if not match:
                continue
            index = int(match[1])
            fd = self._slot(index)
            if fd is None:
                used += 1
            else:
                os.close(fd)
        return used

    @staticmethod
    def _parse_limit(value):
        if isinstance(value, str) and value.strip().lower() == "unlimited":
            return None
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise BridgeError("invalid_argument", "Concurrency must be a nonnegative integer (0 = unlimited) or 'unlimited'")
        return value or None

    def _settings(self):
        path = self.state / "config.json"
        try:
            settings = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(settings, dict):
                raise ValueError("Expected an object")
            self._parse_limit(settings.get("kimi_concurrency", CAP))
            if "cost_policy" in settings:
                cost_policy.normalize(settings["cost_policy"])
            if "zai_policy" in settings:
                zai.normalize_policy(settings["zai_policy"])
            if "kimi_policy" in settings:
                kimi_usage.normalize_policy(settings["kimi_policy"])
            if "kimi_accounts" in settings:
                self._normalize_accounts(**settings["kimi_accounts"])
            return settings
        except FileNotFoundError:
            return {}
        except (OSError, ValueError, TypeError, BridgeError) as exc:
            raise BridgeError("invalid_config", "Invalid shared concurrency config", config_path=str(path)) from exc

    def _cap(self):
        return self._parse_limit(self._settings().get("kimi_concurrency", CAP))

    def _capacity(self):
        cap, used = self._cap(), self._slots_used()
        return {"kimi_cap": cap, "kimi_running": used, "kimi_unlimited": cap is None,
                "kimi_available": None if cap is None else max(0, cap - used)}

    def _write_settings(self, settings):
        path = self.state / "config.json"
        temporary = self.state / (".config-" + uuid.uuid4().hex + ".tmp")
        try:
            fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(settings, stream, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return path

    def set_concurrency(self, limit):
        cap = self._parse_limit(limit)
        with self._transaction():
            settings = self._settings()
            settings["kimi_concurrency"] = "unlimited" if cap is None else cap
            path = self._write_settings(settings)
            result = self._capacity()
        return {**result, "config_path": str(path)}

    def _zai_policy(self):
        return zai.normalize_policy(self._settings().get("zai_policy", {}))

    def _zai_running(self, db):
        """Live Z.ai workers: active/orphan rows whose slot lock is still held.

        The persisted provider group survives later config/account edits; NULL
        legacy rows are re-classified from their own stored config. A row whose
        slot lock is free has no live worker or orphan child and is not counted.
        """
        rows = db.execute("SELECT model,provider,config,slot FROM agents "
                          "WHERE slot IS NOT NULL AND status IN ('starting','running','cancelling','orphaned')").fetchall()
        alias_cache = {}
        running = 0
        for row in rows:
            group = row["provider"]
            if group is None:
                config = row["config"] or str(self.config)
                if config not in alias_cache:
                    alias_cache[config] = zai.aliases(config)
                group = "zai" if row["model"] in alias_cache[config] else ""
            if group != "zai":
                continue
            fd = self._slot(row["slot"])
            if fd is None:
                running += 1
            else:
                os.close(fd)
        return running

    def _zai_policy_view(self, include_usage):
        policy = self._zai_policy()
        with self._db() as db:
            running = self._zai_running(db)
        cap = None if policy["limit"] == "unlimited" else policy["limit"]
        view = {"cap": cap, "running": running, "unlimited": cap is None,
                "available": None if cap is None else max(0, cap - running),
                "usage_guard": policy["usage_guard"],
                "stop_remaining_percent": policy["stop_remaining_percent"]}
        if include_usage and policy["usage_guard"]:
            usage = zai.read_usage(self.state, self.config)
            view["usage"] = usage
            view["blocked"] = zai.block_reason(usage, policy)
        return view

    def set_zai_policy(self, limit=None, usage_guard=None, stop_remaining_percent=None):
        with self._transaction():
            settings = self._settings()
            merged = dict(settings.get("zai_policy") or {})
            if limit is not None:
                merged["limit"] = limit
            if usage_guard is not None:
                merged["usage_guard"] = usage_guard
            if stop_remaining_percent is not None:
                merged["stop_remaining_percent"] = stop_remaining_percent
            try:
                policy = zai.normalize_policy(merged)
            except ValueError as exc:
                raise BridgeError("invalid_argument", str(exc)) from exc
            settings["zai_policy"] = policy
            path = self._write_settings(settings)
        return {"zai_policy": self._zai_policy_view(include_usage=False), "config_path": str(path)}

    @classmethod
    def _normalize_accounts(cls, order=None, limits=None):
        order = ["kimi-code", "kimi-for-coding"] if order is None else order
        limits = {} if limits is None else limits
        def valid(prefix):
            return isinstance(prefix, str) and bool(prefix) and prefix == prefix.strip() and "/" not in prefix and not any(c.isspace() for c in prefix)
        if not isinstance(order, list) or not all(valid(p) for p in order) or len(set(order)) != len(order):
            raise BridgeError("invalid_argument", "order must be a list of unique nonempty account prefixes")
        if not isinstance(limits, dict) or not all(valid(p) for p in limits):
            raise BridgeError("invalid_argument", "limits must map nonempty account prefixes to concurrency limits")
        return {"order": list(order), "limits": {p: "unlimited" if cls._parse_limit(v) is None else v for p, v in limits.items()}}

    def set_kimi_accounts(self, order, limits):
        if order is None or limits is None:
            raise BridgeError("invalid_argument", "order and limits are required")
        policy = self._normalize_accounts(order, limits)
        with self._transaction():
            settings = self._settings()
            settings["kimi_accounts"] = policy
            path = self._write_settings(settings)
        return {"kimi_accounts": self._kimi_accounts_view(), "config_path": str(path)}

    def _account_target(self, config_path, alias):
        """Resolve a copied OAuth token to the original account privately.

        Comparing credentials is confined to memory. Returning the original
        config/alias makes capacity, quota cache and bounded-probe cooldown
        use the same stable OAuth-file identity, while the child keeps its
        separately materialized route and environment.
        """
        target = {'config_path': config_path, 'model': alias}
        try:
            mapping, accounts = kimi_usage._kimi_accounts(config_path)
            selected = accounts.get(mapping.get(alias))
            if selected is None or isinstance(selected.get('oauth'), dict):
                return target
            credential, _, _ = kimi_usage._account_credential(config_path, selected)
            if not credential:
                return target
            source_mapping, sources = kimi_usage._kimi_accounts(self.config)
            for name, source in sources.items():
                if not isinstance(source.get('oauth'), dict):
                    continue
                try:
                    current, _, _ = kimi_usage._account_credential(self.config, source)
                except kimi_usage._Unavailable:
                    continue
                if current and credential == current:
                    source_alias = next(key for key, value in source_mapping.items() if value == name)
                    return {'config_path': str(self.config), 'model': source_alias}
        except kimi_usage._Unavailable:
            pass
        return target

    def _account_fingerprint(self, config_path, alias):
        target = self._account_target(config_path, alias)
        return kimi_usage.account_fingerprint(target['config_path'], target['model'])

    def _account_state(self, db, config_path, alias, exclude_slot=None):
        policy = self._normalize_accounts(**self._settings().get("kimi_accounts", {}))
        prefix = alias.split("/", 1)[0]
        fingerprint = self._account_fingerprint(config_path, alias)
        prefixes = {prefix}
        if fingerprint:
            for source in dict.fromkeys((str(config_path), str(self.config))):
                for name in kimi_usage.aliases(source):
                    if self._account_fingerprint(source, name) == fingerprint:
                        prefixes.add(name.split("/", 1)[0])
        rows = {r["slot"]: dict(r) for r in db.execute("SELECT * FROM slot_accounts")}
        # Legacy live workers have no slot metadata yet.
        for row in db.execute("SELECT model,config,slot FROM agents WHERE slot IS NOT NULL AND status IN ('starting','running','cancelling','orphaned')"):
            if row["slot"] not in rows:
                rows[row["slot"]] = {"slot": row["slot"], "prefix": row["model"].split("/", 1)[0],
                                     "fingerprint": self._account_fingerprint(row["config"] or self.config, row["model"])}
        running = 0
        for row in rows.values():
            fd = self._slot(row["slot"])
            if fd is not None:
                os.close(fd)
                continue
            if (fingerprint and row["fingerprint"] == fingerprint) or row["prefix"] == prefix:
                prefixes.add(row["prefix"])
                if row["slot"] != exclude_slot:
                    running += 1
        caps = [self._parse_limit(policy["limits"].get(p, "unlimited")) for p in prefixes]
        cap = min((v for v in caps if v is not None), default=None)
        return {"prefix": prefix, "cap": cap, "running": running,
                "available": None if cap is None else max(0, cap - running), "unlimited": cap is None}

    def _kimi_accounts_view(self):
        policy = self._normalize_accounts(**self._settings().get("kimi_accounts", {}))
        aliases = sorted(kimi_usage.aliases(self.config))
        by_prefix = {}
        for alias in aliases:
            by_prefix.setdefault(alias.split("/", 1)[0], alias)
        prefixes = list(dict.fromkeys(policy["order"] + list(policy["limits"]) + list(by_prefix)))
        with self._db() as db:
            accounts = [dict(self._account_state(db, self.config, by_prefix.get(p, p + "/")), configured=p in by_prefix) for p in prefixes]
        return {**policy, "accounts": accounts}

    def _account_admit(self, db, config_path, alias, exclude_slot=None):
        if alias not in kimi_usage.aliases(config_path):
            return
        view = self._account_state(db, config_path, alias, exclude_slot)
        if view["cap"] is not None and view["running"] >= view["cap"]:
            raise BridgeError("account_capacity_exceeded", "Kimi account concurrency limit reached; select an eligible model or wait", **view)

    def _remember_slot(self, db, slot, config_path, alias):
        fingerprint = self._account_fingerprint(config_path, alias)
        db.execute("INSERT OR REPLACE INTO slot_accounts VALUES (?,?,?)", (slot, fingerprint, alias.split("/", 1)[0]))

    def _acquire_account(self, db, config_path, alias):
        self._account_admit(db, config_path, alias)
        slot, fd = self._acquire()
        try:
            self._remember_slot(db, slot, config_path, alias)
        except BaseException:
            os.close(fd)
            raise
        return slot, fd

    @contextlib.contextmanager
    def _probe_reservation(self, config_path, alias, fingerprint):
        fd = None
        try:
            # Serialize against policy setters as well as other admissions.
            with self._transaction() as db:
                _, fd = self._acquire_account(db, config_path, alias)
                claim = kimi_usage.claim_probe(self.state, fingerprint,
                                              self._kimi_policy()["probe_cooldown_minutes"])
            yield fd, claim
        finally:
            if fd is not None:
                os.close(fd)

    def _kimi_policy(self):
        return kimi_usage.normalize_policy(self._settings().get("kimi_policy", {}))

    def _kimi_policy_view(self, include_usage):
        policy = self._kimi_policy()
        view = {"usage_guard": policy["usage_guard"],
                "stop_remaining_percent": policy["stop_remaining_percent"],
                "probe_on_unknown": policy["probe_on_unknown"],
                "probe_cooldown_minutes": policy["probe_cooldown_minutes"]}
        if include_usage and policy["usage_guard"]:
            usage = kimi_usage.read_usage(self.state, self.config)
            view["usage"] = usage
            view["blocked"] = kimi_usage.block_reason(usage, policy)
        return view

    def set_kimi_policy(self, usage_guard=None, stop_remaining_percent=None, probe_on_unknown=None, probe_cooldown_minutes=None):
        with self._transaction():
            settings = self._settings()
            merged = dict(settings.get("kimi_policy") or {})
            if usage_guard is not None:
                merged["usage_guard"] = usage_guard
            if stop_remaining_percent is not None:
                merged["stop_remaining_percent"] = stop_remaining_percent
            if probe_on_unknown is not None:
                merged["probe_on_unknown"] = probe_on_unknown
            if probe_cooldown_minutes is not None:
                merged["probe_cooldown_minutes"] = probe_cooldown_minutes
            try:
                policy = kimi_usage.normalize_policy(merged)
            except ValueError as exc:
                raise BridgeError("invalid_argument", str(exc)) from exc
            settings["kimi_policy"] = policy
            path = self._write_settings(settings)
        return {"kimi_policy": self._kimi_policy_view(include_usage=False), "config_path": str(path)}

    def provider_usage(self, provider="zai", model=None):
        """Read-only sanitized five-hour quota metrics (safe codes only).

        ``provider`` selects the account family: ``"zai"`` (default, keeping
        no-argument callers compatible) or ``"kimi"``. ``model`` narrows the
        reading to one exact configured alias of that provider.
        """
        if provider == "zai":
            return zai.read_usage(self.state, self.config, alias=model)
        if provider == "kimi":
            return kimi_usage.read_usage(self.state, self.config, alias=model)
        raise BridgeError("invalid_argument", "provider must be 'zai' or 'kimi'", provider=provider)

    def _kimi_quota_check(self, config_path, alias):
        """Guard admission for a Coding-origin Kimi alias.

        A reserve reading blocks immediately. Unknown usage may earn one
        bounded bootstrap probe per account per configured cooldown when the
        policy opts in; afterwards the reading must be trustworthily above
        the reserve before the real task starts.
        """
        policy = self._kimi_policy()
        if not policy["usage_guard"]:
            return
        reason = kimi_usage.block_reason(kimi_usage.read_usage(self.state, config_path, alias=alias), policy)
        if reason == kimi_usage.PROVIDER_QUOTA_RESERVE:
            raise BridgeError(reason, "Kimi five-hour quota reserve reached; the usage guard blocks new Kimi work")
        if reason is None:
            return
        if not policy["probe_on_unknown"]:
            raise BridgeError(reason, "Kimi usage is not trustworthily readable; the enabled guard fails closed")
        usage = self._kimi_bootstrap_probe(config_path, alias, policy)
        reason = kimi_usage.block_reason(usage, policy)
        if reason == kimi_usage.PROVIDER_QUOTA_RESERVE:
            raise BridgeError(reason, "Kimi five-hour quota reserve reached; the usage guard blocks new Kimi work")
        if reason is not None:
            raise BridgeError(reason, "Kimi usage is still not trustworthily readable after the bounded bootstrap probe; "
                              "the enabled guard fails closed")

    def _kimi_bootstrap_probe(self, config_path, alias, policy):
        """Run the one-per-cooldown bootstrap probe, then re-read usage.

        Re-reads first: a fresh reading above the reserve needs no probe and
        an observed reserve/exhaustion is never eligible for one. The
        allowance is claimed durably before launch; the probe itself goes
        through the shared slot accounting.
        """
        fingerprint = kimi_usage.account_fingerprint(config_path, alias)
        if fingerprint is None:
            raise BridgeError(kimi_usage.PROVIDER_USAGE_UNAVAILABLE,
                              "Kimi account credential is unavailable; cannot run a bootstrap probe")
        usage = kimi_usage.read_usage(self.state, config_path, alias=alias)
        reason = kimi_usage.block_reason(usage, policy)
        if reason is None or reason == kimi_usage.PROVIDER_QUOTA_RESERVE:
            return usage
        with self._probe_reservation(config_path, alias, fingerprint) as (fd, claim):
            if not claim["claimed"]:
                detail = {"cooldown_until": claim["cooldown_until"]} if claim.get("cooldown_until") else {}
                raise BridgeError(kimi_usage.PROVIDER_USAGE_UNAVAILABLE,
                                  "Kimi usage is unknown and this account's bootstrap probe allowance is exhausted "
                                  "until the configured cooldown elapses", **detail)
            self._run_probe(config_path, alias, fd)
        # A successful probe is not proof of quota. Fetch fresh usage rather
        # than confusing the stable cooldown identity with the cache identity.
        return kimi_usage.read_usage(self.state, config_path, alias=alias, force_refresh=True)

    def _run_probe(self, config_path, alias, slot_fd):
        """Launch the fixed bounded bootstrap probe; output is discarded.

        Fixed tool-free profile, fixed prompt, one step, one attempt, a small
        output budget and a short timeout via documented CLI environment
        overrides (Kimi Code 2.1.1 has no --config flag); the child runs in
        an isolated workspace under the state root. The caller's held slot FD
        is inherited so the parent's death cannot release the concurrency slot
        while the child still spends it. The CLI's own OAuth refresh may run;
        the bridge never rewrites authentication files.
        """
        workspace = self.state / "probe-workspace"
        workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
        workspace.chmod(0o700)
        env = self._kimi_env(config_path)
        env["KIMI_LOOP_MAX_STEPS_PER_TURN"] = "1"
        env["KIMI_LOOP_MAX_ATTEMPTS_PER_STEP"] = "1"
        env["KIMI_MODEL_MAX_OUTPUT_SIZE"] = str(PROBE_MAX_OUTPUT_TOKENS)
        cmd = list(self.command) + ["-m", alias, "--agent-file", str(ROOT / "profiles" / "usage-probe.md"),
                                    "-p", PROBE_PROMPT, "--output-format", "stream-json"]
        child = subprocess.Popen(cmd, cwd=str(workspace), env=env, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True, pass_fds=(slot_fd,))
        try:
            child.wait(timeout=PROBE_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            _stop_group(child.pid)
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()

    def probe_kimi_usage(self, model):
        """Explicit bounded bootstrap probe for one exact Kimi alias.

        Caller input selects only the account: prompt, profile and
        bounds are fixed; cooldown follows the persisted policy. A known reading (above or at/below the reserve)
        is returned without probing — an observed reserve/exhaustion is never
        eligible for a bootstrap. Unknown usage claims the durable
        per-account allowance, runs one tool-free probe through the shared
        slot cap, then re-reads; the result still reports whatever the usage
        endpoint says afterwards.
        """
        _text(model, "model")
        if model not in kimi_usage.aliases(self.config):
            raise BridgeError("invalid_argument",
                              "probe_kimi_usage requires an exact configured Kimi alias whose provider is an HTTPS "
                              "api.kimi.com Coding origin", model=model)
        fingerprint = kimi_usage.account_fingerprint(self.config, model)
        if fingerprint is None:
            raise BridgeError(kimi_usage.PROVIDER_USAGE_UNAVAILABLE,
                              "Kimi account credential is unavailable; cannot run a bootstrap probe")
        usage = kimi_usage.read_usage(self.state, self.config, alias=model)
        if usage.get("available") is True:
            return {"model": model, "probed": False, "reason": "known_reading", "usage": usage}
        with self._probe_reservation(self.config, model, fingerprint) as (fd, claim):
            if not claim["claimed"]:
                return {"model": model, "probed": False,
                        "reason": "probe_cooldown" if claim.get("cooldown_until") else "probe_unavailable",
                        "cooldown_until": claim.get("cooldown_until"), "usage": usage}
            self._run_probe(self.config, model, fd)
        return {"model": model, "probed": True,
                "usage": kimi_usage.read_usage(self.state, self.config, alias=model, force_refresh=True)}

    def _zai_quota_check(self, config_path, alias=None):
        policy = self._zai_policy()
        if not policy["usage_guard"]:
            return
        reason = zai.block_reason(zai.read_usage(self.state, config_path, alias=alias), policy)
        if reason == zai.PROVIDER_QUOTA_RESERVE:
            raise BridgeError(reason, "Z.ai five-hour quota reserve reached; the usage guard blocks new Z.ai work")
        if reason is not None:
            raise BridgeError(reason, "Z.ai usage is not trustworthily readable; the enabled guard fails closed")

    @staticmethod
    def _provider_group(config_path, alias):
        """Persisted provider group for guard/cap accounting: zai, kimi or ""."""
        if alias in zai.aliases(config_path):
            return "zai"
        if alias in kimi_usage.aliases(config_path):
            return "kimi"
        return ""

    def _zai_admit(self, db, scope, unit, role):
        """Atomic in-transaction admission against the shared Z.ai provider cap."""
        limit = self._zai_policy()["limit"]
        if limit == "unlimited" or self._zai_running(db) < limit:
            return
        # No automatic GPT preference and no diversity verdict here: spawn has
        # no native model choices, so the error only points at select_model,
        # which enforces task fit, family independence and remaining capacity.
        raise BridgeError("provider_capacity_exceeded",
                          f"All {limit} configured Z.ai GLM provider slots are occupied; "
                          "select an eligible Kimi or native GPT model by task fit and family, or wait",
                          fallback="select_model", zai_cap=limit)

    def _policy(self):
        settings = self._settings()
        if "cost_policy" not in settings:
            return dict(cost_policy.DEFAULTS)
        return cost_policy.normalize(settings["cost_policy"])

    def _policy_view(self, policy, cli_aliases):
        try:
            period = cost_policy.evaluate(policy)
        except cost_policy.CostPolicyError as exc:
            raise BridgeError("invalid_config", "Invalid cost policy", config_path=str(self.state / "config.json")) from exc
        return cost_policy.describe(policy, period, policy["model"] in cli_aliases)

    def set_cost_policy(self, enabled, model=None, timezone=None, peak_start=None, peak_end=None, peak_weekdays=None):
        if not isinstance(enabled, bool):
            raise BridgeError("invalid_argument", "enabled must be a boolean")
        cli_aliases = {m["alias"] for m in self._cli_models(self.configuration_resources())}
        try:
            with self._transaction():
                settings = self._settings()
                policy = cost_policy.update(settings.get("cost_policy"), enabled, model=model, timezone=timezone,
                                            peak_start=peak_start, peak_end=peak_end, peak_weekdays=peak_weekdays)
                if policy["enabled"]:
                    # An active preference must name one exact configured CLI
                    # alias; a disabled policy may retain an absent one.
                    available = cli_aliases
                    if policy["model"] not in available:
                        raise BridgeError("invalid_argument", "Enabled cost policy requires an exact configured CLI alias",
                                          model=policy["model"])
                settings["cost_policy"] = policy
                path = self._write_settings(settings)
        except cost_policy.CostPolicyError as exc:
            raise BridgeError("invalid_argument", str(exc)) from exc
        return {"cost_policy": self._policy_view(policy, cli_aliases), "config_path": str(path)}

    def _acquire(self):
        cap = self._cap()
        if cap is not None and self._slots_used() >= cap:
            raise BridgeError("capacity_exceeded", f"All {cap} configured Kimi CLI slots are occupied", fallback="gpt", kimi_cap=cap)
        index = 0
        while True:
            fd = self._slot(index)
            if fd is not None:
                return index, fd
            index += 1

    def _acquire_for(self, db, scope, unit, role, project_id=None):
        try:
            return self._acquire()
        except BridgeError as exc:
            if project_id is not None:
                exc.details['fallback'] = None
                exc.message = 'Wait for CLI capacity; native GPT is outside Project templates'
                raise
            try:
                self._check_family(db, scope, unit, role, "gpt")
            except BridgeError:
                exc.details.update(fallback=None, diversity_blocked=True)
                exc.message = "Wait for CLI capacity to preserve family independence"
            raise exc

    def _context(self, scope, work_unit, role):
        _text(scope, "scope"); _text(work_unit, "work_unit")
        if not isinstance(role, str) or role not in ROLES:
            raise BridgeError("invalid_argument", "Unknown role", allowed_roles=sorted(ROLES))

    def _check_family(self, db, scope, unit, role, family):
        if role not in {"implementation", "tests"}:
            return
        claims = db.execute("SELECT role,family FROM claims WHERE scope=? AND unit=?", (scope, unit)).fetchall()
        for claim in claims:
            if claim["role"] not in {"implementation", "tests"}:
                continue
            if (claim["role"] == role and claim["family"] != family) or (claim["role"] != role and claim["family"] == family):
                raise BridgeError("family_conflict", "Implementation and tests must have different model families",
                                  role=role, requested_family=family, existing_role=claim["role"], existing_family=claim["family"])

    def _claim(self, db, owner, scope, unit, role, family, kind, model="", author=""):
        self._check_family(db, scope, unit, role, family)
        db.execute("INSERT OR IGNORE INTO claims VALUES (?,?,?,?,?,?,?,?)", (owner, scope, unit, role, family, kind, model, author))

    def record_authorship(self, scope, work_unit, role, family, author):
        self._context(scope, work_unit, role)
        if role not in {"implementation", "tests"} or family not in catalog.FAMILIES:
            raise BridgeError("invalid_argument", "Authorship requires implementation/tests and a supported model family",
                              allowed_families=list(catalog.FAMILIES))
        _text(author, "author")
        owner = "author:" + hashlib.sha256(json.dumps([scope, work_unit, role, family, author]).encode()).hexdigest()
        with self._transaction() as db:
            self._claim(db, owner, scope, work_unit, role, family, "authorship", author=author)
        return {"recorded": True, "family": family, "author": author, "work_unit": work_unit}

    def release_native_reservation(self, reservation_id):
        _text(reservation_id, "reservation_id")
        with self._transaction() as db:
            row = db.execute("SELECT kind FROM claims WHERE owner=?", (reservation_id,)).fetchone()
            if row and row["kind"] != "reservation":
                raise BridgeError("invalid_argument", "Confirmed authorship cannot be released")
            db.execute("DELETE FROM claims WHERE owner=? AND kind='reservation'", (reservation_id,))
        return {"released": True, "reservation_id": reservation_id}

    def configuration_resources(self):
        """Local configured resources only; no quota, inference or auth calls."""
        return self._configuration_resources(self.config)

    def _configuration_resources(self, config_path):
        secrets = security.configured_secrets(config_path)
        try:
            resources = providers.build_resources(config_path, self.db_path, secrets)
        except configuration.ConfigurationError as exc:
            raise BridgeError(exc.code, exc.message) from exc
        return security.sanitize({
            "agents": {
                "kimi-code": {"status": "supported"},
                "zcode": {"status": "planned"},
                "claude-code": {"status": "planned"},
            },
            **resources,
        }, secrets)

    def _configuration_call(self, method, *args, **kwargs):
        resources = self.configuration_resources()
        store = configuration.ConfigurationStore(self.db_path)
        try:
            store.initialize(resources)
            if method in {"save_template", "resolve_project"}:
                kwargs["resources"] = resources
            return getattr(store, method)(*args, **kwargs)
        except configuration.ConfigurationError as exc:
            raise BridgeError(exc.code, exc.message) from exc

    def list_templates(self):
        return {"templates": self._configuration_call("list_templates")}

    def get_template(self, template_id, revision=None):
        return self._configuration_call("get_template", template_id, revision)

    def create_template(self, name, source_template_id="default"):
        return self._configuration_call("create_template", name, source_template_id)

    def save_template(self, template_id, payload, expected_revision, name=None):
        return self._configuration_call("save_template", template_id, payload,
                                        expected_revision, name=name)

    def archive_template(self, template_id, expected_revision):
        return self._configuration_call("archive_template", template_id, expected_revision)

    def register_project(self, name, root, host_id="local"):
        return self._configuration_call("register_project", name, root, host_id)

    def list_projects(self):
        return {"projects": self._configuration_call("list_projects")}

    def bind_project_template(self, project_id, template_id, expected_binding_revision):
        return self._configuration_call("bind_project", project_id, template_id,
                                        expected_binding_revision)

    def resolve_project_config(self, project_id, snapshot_id=None):
        """Preview saved configuration without inference or capacity reservation."""
        return self._configuration_call("resolve_project", project_id,
                                        snapshot_id=snapshot_id)

    def _provider_call(self, method, *args, **kwargs):
        try:
            return getattr(providers.ProviderStore(self.db_path), method)(*args, **kwargs)
        except configuration.ConfigurationError as exc:
            raise BridgeError(exc.code, exc.message) from exc

    def _provider_context(self):
        """Live imported ids for reference validation; no managed state."""
        secrets = security.configured_secrets(self.config)
        resources = self.configuration_resources()
        return (secrets,
                {pid for pid, p in resources["providers"].items()
                 if p["source"] == "imported" and p.get("credential_ref")},
                {alias for alias, m in resources["models"].items() if m["source"] == "imported"})

    def _require_configuration_resource(self, kind, identity):
        if not isinstance(identity, str) or not identity:
            raise BridgeError("invalid_argument", "Resource identity must be a nonempty string")
        if identity not in self.configuration_resources()[kind]:
            raise BridgeError("not_found", "Resource not found")

    def create_provider(self, provider_id, label, provider_type, endpoint, protocol, credential_ref):
        """Create a managed connection with a symbolic credential reference.

        Identity/type are immutable afterwards; the record persists in SQLite
        and never touches the CLI installation TOML or credential files.
        """
        secrets, imported_ids, _ = self._provider_context()
        return self._provider_call("create_provider", provider_id, label, provider_type,
                                   endpoint, protocol, credential_ref,
                                   imported_ids=imported_ids, secrets=secrets)

    def save_provider(self, provider_id, expected_revision, label=None, enabled=None,
                      endpoint=None, protocol=None, credential_ref=None):
        """CAS update of a connection. Imported installation-owned fields
        (endpoint/protocol/credential reference) reject edits."""
        self._require_configuration_resource("providers", provider_id)
        secrets, imported_ids, _ = self._provider_context()
        return self._provider_call("save_provider", provider_id, expected_revision,
                                   label=label, enabled=enabled, endpoint=endpoint,
                                   protocol=protocol, credential_ref=credential_ref,
                                   imported_ids=imported_ids, secrets=secrets)

    def create_model(self, alias, provider_id, upstream_model, display_name, context, capabilities):
        """Create a managed model under an existing connection.

        The upstream must be a recognized Kimi/GLM profile for the current
        adapter; nothing is granted to saved templates automatically.
        """
        self._require_configuration_resource("providers", provider_id)
        secrets, _, imported_aliases = self._provider_context()
        return self._provider_call("create_model", alias, provider_id, upstream_model,
                                   display_name, context, capabilities,
                                   imported_aliases=imported_aliases, secrets=secrets)

    def save_model(self, alias, expected_revision, display_name=None, enabled=None,
                   context=None, capabilities=None):
        """CAS update of a model; identity/provider/upstream are immutable."""
        self._require_configuration_resource("models", alias)
        secrets, _, _ = self._provider_context()
        return self._provider_call("save_model", alias, expected_revision,
                                   display_name=display_name, enabled=enabled,
                                   context=context, capabilities=capabilities,
                                   secrets=secrets)

    def get_provider_settings(self, provider_id):
        """Explicit handler kind and the existing shared policy scope.

        Reads the normalized policy views with include_usage=False: no quota,
        auth or inference runs. Generic connections carry no invented policy.
        """
        if not isinstance(provider_id, str) or not provider_id:
            raise BridgeError("invalid_argument", "provider_id must be a nonempty string")
        resources = self.configuration_resources()
        record = resources["providers"].get(provider_id)
        if record is None:
            raise BridgeError("not_found", "Provider not found")
        handler = record.get("provider_type")
        if handler not in ("kimi", "zai"):
            handler = "generic"
        settings = {"provider_id": provider_id, "handler": handler,
                    "policy_scope": providers.POLICY_SCOPE[handler]}
        if handler == "kimi":
            settings["kimi_policy"] = self._kimi_policy_view(include_usage=False)
            settings["kimi_accounts"] = self._kimi_accounts_view()
        elif handler == "zai":
            settings["zai_policy"] = self._zai_policy_view(include_usage=False)
            settings["cost_policy"] = self._policy_view(self._policy(), set(zai.aliases(self.config)))
        return security.sanitize(settings, security.configured_secrets(self.config))

    def _dispatch_resources(self, config_path=None):
        resources = self._configuration_resources(config_path or self.config)
        store = configuration.ConfigurationStore(self.db_path)
        store.initialize(resources)
        return store, resources

    def _dispatch_context(self, store, resources, db, cwd=None, project_id=None):
        try:
            # Resource settings share the admission transaction: a concurrent
            # disable/revision update cannot be hidden by a prior quota read.
            for row in db.execute('SELECT * FROM provider_records'):
                record = resources['providers'].get(row['provider_id'])
                if record is not None:
                    record.update(providers.ProviderStore._provider_record(row, record.get('model_aliases', [])))
            for row in db.execute('SELECT * FROM model_records'):
                record = resources['models'].get(row['alias'])
                if record is not None:
                    record.update(providers.ProviderStore._model_record(row))
            return store.resolve_dispatch(db, resources, cwd, project_id)
        except configuration.ConfigurationError as exc:
            raise BridgeError(exc.code, exc.message) from exc

    def _cli_models(self, resources):
        defaults = {entry['alias']: entry.get('routing_default', False)
                    for entry in catalog.load_kimi_models(self.config, all_aliases=True)}
        models = []
        for alias, record in resources['models'].items():
            family, key = catalog._cli_profile_for_model(record.get('upstream_model'))
            if key is None:
                continue
            models.append(dict(alias=alias, model=record['upstream_model'], family=family,
                               profile=key, backend=catalog.CLI_BACKEND,
                               context=record.get('context'), capabilities=record.get('capabilities', []),
                               display_name=record.get('display_name', alias),
                               managed=record.get('source') == 'managed', notes=catalog.CLI_PROFILES[key]['notes'],
                               routing_default=defaults.get(alias, False)))
        return models

    def _allowed(self, context, resources, model, config_path, guard_stack):
        if model not in {m['alias'] for m in context['candidates']}:
            raise BridgeError('model_not_allowed', 'The saved template does not allow this exact available model.')
        record = resources['models'][model]
        provider = resources['providers'][record['provider_id']]
        try:
            descriptor = materialization.describe(config_path, record, provider)
            if record.get('source') == 'managed' and (provider.get('credential_ref') or '').startswith('env:'):
                prepared = guard_stack.enter_context(materialization.prepare(config_path, descriptor, self.state))
                target = {'config_path': prepared['config_path'], 'model': prepared['model']}
            else:
                target = materialization.guard_target(config_path, descriptor)
        except materialization.MaterializationError as exc:
            raise BridgeError(exc.code, str(exc)) from exc
        target = self._account_target(target['config_path'], target['model'])
        expected_group = descriptor.get('guard_kind')
        if record.get('source') == 'managed' and expected_group in GUARD_MODULES:
            actual_group = self._provider_group(target['config_path'], target['model'])
            if actual_group != expected_group:
                raise BridgeError('unsupported_guard_route',
                                  'The managed route cannot enforce its provider usage guard and account limits.')
        return descriptor, target

    def _project_admit(self, db, context, exclude_slot=None):
        if context['project_id'] is None:
            return
        limit = context['payload']['limits']['max_parallel_per_project']
        if limit == 0:
            return
        running = 0
        for row in db.execute("SELECT slot FROM agents WHERE project_id=? AND slot IS NOT NULL AND status IN ('starting','running','cancelling','orphaned')", (context['project_id'],)):
            if row['slot'] == exclude_slot:
                continue
            fd = self._slot(row['slot'])
            if fd is None:
                running += 1
            else:
                os.close(fd)
        if running >= limit:
            raise BridgeError('project_capacity_exceeded', 'Project concurrency limit reached.', project_id=context['project_id'], limit=limit, running=running)

    def _snapshot(self, db, row, context, resources, descriptor):
        turn = row['turn'] + 1
        value = dict(context, agent_id=row['id'], turn=turn, descriptor={key: value for key, value in descriptor.items() if not key.startswith('_')},
                     selected_model=resources['models'][row['model']],
                     selected_provider=resources['providers'][resources['models'][row['model']]['provider_id']])
        serialized = json.dumps(value)
        db.execute('INSERT INTO turn_snapshots VALUES (?,?,?)', (row['id'], turn, serialized))
        db.execute('UPDATE agents SET project_id=?,configuration_snapshot=? WHERE id=?', (context['project_id'], serialized, row['id']))
        return value

    def _error_text(self, value, config_path=None, command=None, secrets=()):
        """Sanitize runtime diagnostics, leaving worker outputs untouched."""
        config_path = Path(config_path or self.config)
        text = security.redact(value, tuple(secrets) + security.configured_secrets(config_path))
        text = re.sub(r'(?im)See log:[^\r\n]*', '', text)
        if isinstance(command, str):
            try:
                command = json.loads(command)
            except (TypeError, ValueError):
                command = None
        command = command if isinstance(command, list) else self.command
        paths = {str(self.state), str(config_path), str(config_path.parent), str(ROOT), sys.executable}
        paths.update(item for item in command if isinstance(item, str) and os.path.isabs(item))
        for path in sorted(paths, key=len, reverse=True):
            if path and path != os.path.sep:
                text = re.sub(re.escape(path) + r"(?:[/\\][^\s\"'<>]*)?", '[runtime path]', text)
        return text.strip()

    def _kimi_recovery_entries(self, unknown, account_order):
        """Structured recovery guidance: one entry per affected Kimi account.

        ``unknown`` items carry an exact alias that ``probe_kimi_usage``
        accepts, that alias's safe per-account usage error code and its
        routing-default flag. Aliases sharing an account fingerprint collapse
        to one representative, preferring configured account order, then the
        profile's routing default, then the alias name. Entries never expose
        credentials, fingerprints or private paths, and guidance never claims
        a probe allowance: ``probe_kimi_usage`` remains the authority for the
        durable per-account cooldown and atomic capacity admission.
        """
        by_account = {}
        for item in unknown:
            fingerprint = self._account_fingerprint(self.config, item["model"])
            if fingerprint is None:
                # probe_kimi_usage refuses accounts it cannot identify; never
                # emit guidance for an action that is already impossible.
                continue
            prefix = item["model"].split("/", 1)[0]
            rank = (account_order.index(prefix) if prefix in account_order else len(account_order),
                    not item.get("routing_default", False), item["model"])
            current = by_account.get(fingerprint)
            if current is None or rank < current[0]:
                by_account[fingerprint] = (rank, item)
        entries = []
        for _, item in sorted(by_account.values(), key=lambda pair: pair[0]):
            code = item["usage_error"]
            if code not in kimi_usage.SAFE_ERROR_CODES:
                code = "unknown_error"
            if code == "credential_expired":
                reason = ("Expired local OAuth access token; usage is unknown pending CLI refresh, "
                          "not proof of invalid login, an expired subscription or an unusable account. ")
            else:
                reason = "Five-hour usage for this account is not trustworthily readable. "
            reason += ("Run the returned bounded probe once, then rerun catalog/selection; treat "
                       "probe_cooldown or still-unknown usage as a stop for this account.")
            entries.append({
                "model": item["model"],
                "usage_error": code,
                "next_action": {"tool": "probe_kimi_usage", "arguments": {"model": item["model"]}},
                "reason": reason,
            })
        return entries

    def model_catalog(self, native_models=None):
        if native_models is None:
            native_models = []
        if not isinstance(native_models, list) or not all(isinstance(x, str) and x.startswith("gpt-") for x in native_models):
            raise BridgeError("invalid_argument", "native_models must list available GPT model names")
        # Legacy GLM names remain valid for explicit dispatch and quota/cap
        # accounting, but are no longer independent automatic candidates.
        models_cli = [m for m in self._cli_models(self.configuration_resources())
                      if m["family"] != "glm" or m["profile"] in {"glm-5.3", "glm-5.3-flash"}]
        models = models_cli + [catalog.native_model(m) for m in dict.fromkeys(native_models)]
        with self._db() as db:
            observed = {r["model"]: dict(r) for r in db.execute("SELECT * FROM observations")}
        for observation in observed.values():
            observation['last_error'] = self._error_text(observation['last_error'])
        for model in models:
            model["observation"] = security.sanitize(observed.get(model["alias"], {"successes": 0, "failures": 0, "access": "configured_only"}), security.configured_secrets(self.config))
        # Exact per-alias account reads, never the aggregate: an unhealthy
        # account must not hide a healthy one, and no inference runs here.
        kimi_policy = self._kimi_policy()
        kimi_recovery = []
        if kimi_policy["usage_guard"] and kimi_policy["probe_on_unknown"]:
            account_order = self._normalize_accounts(**self._settings().get("kimi_accounts", {}))["order"]
            probe_aliases = kimi_usage.aliases(self.config)
            unknown = []
            for model in models_cli:
                alias = model["alias"]
                if model["family"] != "kimi" or alias not in probe_aliases:
                    continue
                usage = kimi_usage.read_usage(self.state, self.config, alias=alias)
                if kimi_usage.block_reason(usage, kimi_policy) == kimi_usage.PROVIDER_USAGE_UNAVAILABLE:
                    unknown.append({"model": alias, "usage_error": usage.get("error"),
                                    "routing_default": model.get("routing_default", False)})
            kimi_recovery = self._kimi_recovery_entries(unknown, account_order)
        return {"models": models, "observation_semantics": "Historical calls for each exact alias; not a current account quota check.",
                "kimi_recovery": kimi_recovery,
                "cost_policy": self._policy_view(self._policy(), {m["alias"] for m in models_cli}),
                "zai_policy": self._zai_policy_view(include_usage=True),
                "kimi_policy": self._kimi_policy_view(include_usage=True),
                "kimi_accounts": self._kimi_accounts_view(),
                **self._capacity()}

    def select_model(self, scope, work_unit, role, native_models, complexity="standard", context_tokens=0, latency_sensitive=False, avoid_family=None, project_id=None, cwd=None):
        with contextlib.ExitStack() as guard_stack:
            return self._select_model(guard_stack, scope, work_unit, role, native_models, complexity, context_tokens, latency_sensitive, avoid_family, project_id, cwd)

    def _select_model(self, guard_stack, scope, work_unit, role, native_models, complexity="standard", context_tokens=0,
                     latency_sensitive=False, avoid_family=None, project_id=None, cwd=None):
        self._context(scope, work_unit, role)
        if complexity not in {"simple", "standard", "complex"} or not isinstance(latency_sensitive, bool):
            raise BridgeError("invalid_argument", "Invalid complexity/latency preference")
        _integer(context_tokens, "context_tokens", 0, 2_000_000)
        if avoid_family is not None and avoid_family not in catalog.FAMILIES:
            raise BridgeError("invalid_argument", "avoid_family must be a supported model family",
                              allowed_families=list(catalog.FAMILIES))
        if not isinstance(native_models, list) or not all(isinstance(x, str) and x.startswith('gpt-') for x in native_models):
            raise BridgeError('invalid_argument', 'native_models must list available GPT model names')
        store, resources = self._dispatch_resources()
        with self._db() as db:
            context = self._dispatch_context(store, resources, db, cwd, project_id)
        allowed = {m['alias'] for m in context['candidates']}
        models = [m for m in self._cli_models(resources) if m['alias'] in allowed
                  and (m['family'] != 'glm' or m['profile'] in {'glm-5.3', 'glm-5.3-flash'})]
        if context['project_id'] is None:
            models += [catalog.native_model(m) for m in dict.fromkeys(native_models)]
        policy = self._policy_view(self._policy(), allowed)
        prefer = (policy['model'] if policy['enabled'] and policy['period'] == 'off_peak' and policy['model_available']
                  and (context['template_id'] == 'default' or context['payload']['selection']['prefer_provider_discount_on_equal_fit']) else None)
        zai_aliases, kimi_aliases, zai_blocked, kimi_blocked = set(), set(), set(), set()
        kimi_unknown = {}
        targets = {}
        zai_policy, kimi_policy = self._zai_policy(), self._kimi_policy()
        usable = []
        for model in models:
            alias = model['alias']
            if model['family'] == 'gpt':
                usable.append(model)
                continue
            try:
                _, target = self._allowed(context, resources, alias, self.config, guard_stack)
            except BridgeError:
                continue
            targets[alias] = target
            group = self._provider_group(target['config_path'], target['model'])
            module = GUARD_MODULES.get(group)
            guard_policy = zai_policy if group == 'zai' else kimi_policy
            if group == 'zai':
                zai_aliases.add(alias)
            elif group == 'kimi':
                kimi_aliases.add(alias)
            if module and guard_policy['usage_guard']:
                usage = module.read_usage(self.state, target['config_path'], alias=target['model'])
                reason = module.block_reason(usage, guard_policy)
                if reason:
                    (zai_blocked if group == 'zai' else kimi_blocked).add(alias)
                    # Unknown-usage Kimi aliases on this installation's own
                    # config may be recoverable through one bounded probe; a
                    # known reserve/exhausted reading never earns guidance.
                    # Recovery is limited to imported configured aliases
                    # themselves: the permitted alias must BE the exact alias
                    # probe_kimi_usage accepts, so a template permitting only a
                    # managed alias never probes its denied imported source.
                    if (group == 'kimi' and reason == kimi_usage.PROVIDER_USAGE_UNAVAILABLE
                            and guard_policy['probe_on_unknown']
                            and alias == target['model']
                            and str(target['config_path']) == str(self.config)
                            and alias in kimi_usage.aliases(self.config)):
                        kimi_unknown[alias] = {"probe_alias": target['model'],
                                               "usage_error": usage.get("error")}
            usable.append(model)
        models = usable
        # Hold role checks and tie rotation in the same transaction. Selection
        # does not reserve capacity or authorship; dispatch checks again.
        with self._transaction() as db:
            self._project_admit(db, context)
            eligible = []
            for model in models:
                try:
                    self._check_family(db, scope, work_unit, role, model["family"])
                except BridgeError:
                    continue
                if model["family"] != avoid_family:
                    eligible.append(model)
            if zai_aliases:
                # Blocked Z.ai candidates (usage guard per alias, or the whole
                # provider at capacity) are removed; eligible Kimi/GPT/other
                # providers keep fit+diversity.
                removed = set(zai_blocked)
                limit = zai_policy["limit"]
                if limit != "unlimited" and self._zai_running(db) >= limit:
                    removed = set(zai_aliases)
                if removed:
                    eligible = [m for m in eligible if m["alias"] not in removed]
            kimi_account_full = set()
            for alias in kimi_aliases:
                view = self._account_state(db, targets[alias]["config_path"], targets[alias]["model"])
                if view["available"] == 0:
                    kimi_account_full.add(alias)
                    kimi_blocked.add(alias)
            account_order = self._normalize_accounts(**self._settings().get("kimi_accounts", {}))["order"]
            # Recovery guidance covers only otherwise eligible unknown-usage
            # Kimi aliases: saved permissions, family/avoid-family and account
            # capacity already passed; context feasibility is checked below.
            recovery_pool = [m for m in eligible
                             if m["alias"] in kimi_unknown and m["alias"] not in kimi_account_full]
            kimi_recovery = []
            if recovery_pool:
                feasible = {r["model"]["alias"]
                            for r in catalog.rank_models(recovery_pool, role, complexity, context_tokens, latency_sensitive)
                            if r["feasible"]}
                kimi_recovery = self._kimi_recovery_entries(
                    [{"model": kimi_unknown[m["alias"]]["probe_alias"],
                      "usage_error": kimi_unknown[m["alias"]]["usage_error"],
                      "routing_default": m.get("routing_default", False)}
                     for m in recovery_pool if m["alias"] in feasible], account_order)
            if kimi_blocked:
                eligible = [m for m in eligible if m["alias"] not in kimi_blocked]
            # Reorder only aliases of the same Kimi profile; preserve family rotation.
            for profile in {m["profile"] for m in eligible if m["family"] == "kimi"}:
                positions = [i for i, m in enumerate(eligible) if m["family"] == "kimi" and m["profile"] == profile]
                choices = sorted((eligible[i] for i in positions), key=lambda m: account_order.index(m["alias"].split("/", 1)[0]) if m["alias"].split("/", 1)[0] in account_order else len(account_order))
                for i, choice in zip(positions, choices):
                    eligible[i] = choice
            ranked = [dict(m["model"], score=m["score"], rationale=m["rationale"])
                      for m in catalog.rank_models(eligible, role, complexity, context_tokens, latency_sensitive)
                      if m["feasible"]]
            cap = self._cap()
            shared_full = cap is not None and self._slots_used() >= cap
            if shared_full:
                # Full shared capacity is the binding constraint; a probe goes
                # through the same slots, so recovery guidance would mislead.
                kimi_recovery = []
            if not ranked:
                raise BridgeError("no_eligible_model", "No model meets task and family constraints",
                                  kimi_recovery=kimi_recovery)
            if shared_full:
                gpt = [m for m in ranked if m["family"] == "gpt"]
                if not gpt:
                    raise BridgeError("capacity_exceeded", "Wait for CLI capacity to preserve family independence", fallback=None, diversity_blocked=True)
                ranked = gpt
            top_score = ranked[0]["score"]
            equal = [m for m in ranked if m["score"] == top_score]
            selected = None
            if prefer is not None:
                # Cost preference applies only after context/family/capacity
                # filters and only when the exact alias ties at the top score.
                selected = next((m for m in equal if m["alias"] == prefer
                                 and m.get("backend") == catalog.CLI_BACKEND), None)
            if selected is None:
                last = db.execute("SELECT value FROM meta WHERE key='last_family'").fetchone()
                previous = last[0] if last else "gpt"
                families = list(dict.fromkeys(m["family"] for m in equal))
                next_family = families[(families.index(previous) + 1) % len(families)] if previous in families else families[0]
                selected = next(m for m in equal if m["family"] == next_family)
            db.execute("INSERT OR REPLACE INTO meta VALUES ('last_family',?)", (selected["family"],))
        applied = (prefer is not None and selected["alias"] == prefer
                   and selected.get("backend") == catalog.CLI_BACKEND)
        reason = selected.get("rationale", "Task fit and model-family independence")
        if applied:
            reason += f"; off-peak cost preference applied to exact alias {prefer}"
        if kimi_recovery:
            reason += ("; Kimi recovery preflight pending: execute each kimi_recovery "
                       "probe action once per account, then rerun selection before "
                       "accepting a non-Kimi fallback for unknown usage")
        return {"selected": selected, "candidates": ranked, "selection_is_advisory": True,
                "reason": reason, "cost_policy": policy, "cost_preference_applied": applied,
                "kimi_recovery": kimi_recovery,
                "project_id": context["project_id"], "template_id": context["template_id"], "native_template_boundary": "outside_kimi_code_templates"}

    def spawn_agent(self, scope, work_unit, role, message, cwd, model, timeout_seconds=900, profile="worker", project_id=None):
        with contextlib.ExitStack() as guard_stack:
            return self._spawn_agent(guard_stack, scope, work_unit, role, message, cwd, model, timeout_seconds, profile, project_id)

    def _spawn_agent(self, guard_stack, scope, work_unit, role, message, cwd, model, timeout_seconds=900, profile="worker", project_id=None):
        self._context(scope, work_unit, role)
        _text(message, "message"); _text(model, "model"); _text(cwd, "cwd")
        path = Path(cwd).expanduser().resolve()
        if not path.is_dir():
            raise BridgeError("invalid_argument", "cwd must be an existing directory")
        _integer(timeout_seconds, "timeout_seconds", 1, 3600)
        if profile not in {"worker", "smoke"}:
            raise BridgeError("invalid_argument", "profile must be worker or smoke")
        store, resources = self._dispatch_resources()
        with self._db() as db:
            context = self._dispatch_context(store, resources, db, str(path), project_id)
        available = {m['alias']: m for m in self._cli_models(resources)}
        if model.startswith("gpt-") and model not in available:
            if context['project_id'] is not None:
                raise BridgeError('native_project_unsupported', 'Native GPT dispatch is outside Project template enforcement.')
            reservation = "reservation:" + uuid.uuid4().hex
            with self._transaction() as db:
                self._claim(db, reservation, scope, work_unit, role, "gpt", "reservation", model)
            name = re.sub(r"[^a-z0-9_]", "_", work_unit.lower())[:40] or "worker"
            return {"reservation_id": reservation, "family": "gpt", "backend": "codex_native", "dispatched": False,
                    "template_enforcement": "outside_kimi_code_templates",
                    "native_action": {"tool": "spawn_agent", "arguments": {"task_name": name,
                                      "model": model, "fork_turns": "none", "message":
                                      f"Assigned workspace: {path}. Read its AGENTS.md and applicable parent guidance. "
                                      "Use this absolute directory as workdir for every command and absolute paths for edits; "
                                      "do not operate from the parent's checkout. If unavailable, stop and report.\n\n" + message}}}
        self._kimi_env(self.config)
        descriptor, target = self._allowed(context, resources, model, self.config, guard_stack)
        family = available[model]["family"]
        # Family check precedes provider quota/capacity checks so a blocked
        # account can never hide an implementation/tests family conflict.
        with self._db() as db:
            self._check_family(db, scope, work_unit, role, family)
        group = self._provider_group(target['config_path'], target['model'])
        if group == "zai":
            self._zai_quota_check(target['config_path'], target['model'])
        elif group == "kimi":
            self._kimi_quota_check(target['config_path'], target['model'])
        agent_id = "kimi:" + uuid.uuid4().hex
        # Recheck saved permission and Project capacity under the slot transaction.
        with self._transaction() as db:
            context = self._dispatch_context(store, resources, db, str(path), project_id)
            descriptor, target = self._allowed(context, resources, model, self.config, guard_stack)
            self._project_admit(db, context)
            if group == "zai":
                self._zai_admit(db, scope, work_unit, role)
            self._account_admit(db, target["config_path"], target["model"])
            slot, fd = self._acquire_for(db, scope, work_unit, role, context["project_id"])
            try:
                self._remember_slot(db, slot, target["config_path"], target["model"])
                now = time.time()
                self._claim(db, agent_id, scope, work_unit, role, family, "agent", model)
                db.execute("""INSERT INTO agents
                    (id,scope,unit,role,family,model,cwd,status,slot,profile,timeout,created,updated,command,config,provider,project_id)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                           (agent_id, scope, work_unit, role, family, model, str(path), "starting", slot, profile,
                            timeout_seconds, now, now, json.dumps(self.command), str(self.config), group, context["project_id"]))
                db.execute("INSERT INTO inbox(agent_id,message) VALUES (?,?)", (agent_id, message))
            except BaseException:
                os.close(fd)
                raise
        try:
            self._launch(agent_id, fd)
        except BaseException:
            with self._transaction() as db:
                db.execute("DELETE FROM turn_snapshots WHERE agent_id=?", (agent_id,))
                db.execute("DELETE FROM agents WHERE id=?", (agent_id,))
                db.execute("DELETE FROM claims WHERE owner=?", (agent_id,))
                db.execute("DELETE FROM inbox WHERE agent_id=?", (agent_id,))
            raise
        return self.get_agent(agent_id)

    def _launch(self, agent_id, fd):
        try:
            starting = self._row(agent_id)
            worker = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--worker", str(self.state), agent_id, str(fd)],
                                      pass_fds=(fd,), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL, start_new_session=True)
            threading.Thread(target=worker.wait, daemon=True).start()
            with self._db() as db:
                db.execute("""UPDATE agents SET worker_pid=?,worker_identity=? WHERE id=?
                    AND status='starting' AND worker_pid IS NULL AND turn=? AND updated=?""",
                           (worker.pid, _identity(worker.pid), agent_id, starting["turn"], starting["updated"]))
        finally:
            # Closing, not LOCK_UN: the worker owns the SAME open description.
            os.close(fd)

    def register_native_agent(self, scope, work_unit, role, model, agent_id):
        self._context(scope, work_unit, role)
        _text(agent_id, "agent_id")
        if not isinstance(model, str) or not model.startswith("gpt-") or agent_id.startswith("kimi:"):
            raise BridgeError("invalid_argument", "Register a genuine native GPT ID and model")
        with self._transaction() as db:
            existing = db.execute("SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone()
            if existing and (existing["scope"], existing["unit"], existing["role"], existing["model"]) != (scope, work_unit, role, model):
                raise BridgeError("invalid_argument", "Native ID already has a different assignment")
            self._claim(db, agent_id, scope, work_unit, role, "gpt", "agent", model)
            db.execute("DELETE FROM claims WHERE scope=? AND unit=? AND role=? AND model=? AND kind='reservation'",
                       (scope, work_unit, role, model))
            now = time.time()
            db.execute("""INSERT OR IGNORE INTO agents
                (id,scope,unit,role,family,model,cwd,status,created,updated) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                       (agent_id, scope, work_unit, role, "gpt", model, "", "native", now, now))
        return self.get_agent(agent_id)

    def _row(self, agent_id):
        _text(agent_id, "agent_id")
        with self._db() as db:
            row = db.execute("SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone()
        if row is None:
            raise BridgeError("not_found", "Unknown agent ID", agent_id=agent_id)
        return dict(row)

    def get_agent(self, agent_id):
        row = self._row(agent_id)
        if row["family"] != "gpt" and row["status"] in ACTIVE | {"orphaned"} and time.time() - row["updated"] > 1:
            worker = _identity(row["worker_pid"])
            if not worker or worker != row["worker_identity"]:
                with self._transaction() as db:
                    fd = self._slot(row["slot"])
                    status = "orphaned" if fd is None else "interrupted"
                    try:
                        db.execute("""UPDATE agents SET status=?,error=? WHERE id=?
                            AND status IN ('starting','running','cancelling','orphaned')
                            AND worker_pid IS ? AND worker_identity IS ? AND turn=? AND updated=? AND slot IS ?""",
                                   (status, "Worker stopped; preserved session and outputs", agent_id,
                                    row["worker_pid"], row["worker_identity"], row["turn"], row["updated"], row["slot"]))
                    finally:
                        if fd is not None:
                            os.close(fd)
                row = self._row(agent_id)
        with self._db() as db:
            queued = db.execute("SELECT count(*) FROM inbox WHERE agent_id=?", (agent_id,)).fetchone()[0]
        result = {"agent_id": row["id"], "scope": row["scope"], "work_unit": row["unit"], "role": row["role"],
                  "family": row["family"], "backend": "codex_native" if row["family"] == "gpt" else "kimi_cli",
                  "model": row["model"], "status": row["status"], "turn": row["turn"],
                  "session_id": row["session_id"], "resumable": bool(row["session_id"]),
                  "last_result": _redact(row["result"], row["config"] or self.config)[-8000:], "progress": _redact(row["progress"], row["config"] or self.config)[-1200:],
                  "error": self._error_text(row["error"], row["config"], row["command"])[-1600:], "queued_message_count": queued,
                  "project_id": row["project_id"], "configuration_snapshot": json.loads(row["configuration_snapshot"]) if row["configuration_snapshot"] else None}
        if row["family"] == "gpt":
            result["native_action"] = {"tool": "list_agents", "arguments": {}, "target": agent_id}
        return result

    def list_agents(self, scope=None):
        if scope is not None:
            _text(scope, "scope")
        with self._db() as db:
            ids = [r[0] for r in db.execute("SELECT id FROM agents" + (" WHERE scope=?" if scope else "") + " ORDER BY created DESC LIMIT 100", (scope,) if scope else ())]
            pending = [dict(r) for r in db.execute("SELECT owner AS reservation_id,scope,unit AS work_unit,role,family,model FROM claims WHERE kind='reservation'" + (" AND scope=?" if scope else ""), (scope,) if scope else ())]
        return {"agents": [self.get_agent(i) for i in ids], "pending_native_reservations": pending,
                **self._capacity()}

    def wait_agent(self, agent_ids, timeout_ms=10000):
        if not isinstance(agent_ids, list) or not agent_ids or not all(isinstance(i, str) for i in agent_ids):
            raise BridgeError("invalid_argument", "agent_ids must be a nonempty list")
        _integer(timeout_ms, "timeout_ms", 0, 60000)
        end = time.monotonic() + timeout_ms / 1000
        while True:
            snapshots = [self.get_agent(i) for i in agent_ids]
            cli = [s for s in snapshots if s["family"] != "gpt"]
            if not cli or any(s["status"] in TERMINAL for s in cli) or time.monotonic() >= end:
                break
            time.sleep(min(0.1, max(0, end - time.monotonic())))
        native = [s["agent_id"] for s in snapshots if s["family"] == "gpt"]
        result = {"agents": snapshots, "timed_out": bool(cli) and not any(s["status"] in TERMINAL for s in cli)}
        if native:
            result["native_action"] = {"tool": "wait_agent", "arguments": {"timeout_ms": timeout_ms}, "targets": native}
        return result

    def send_message(self, agent_id, message):
        with contextlib.ExitStack() as guard_stack:
            return self._send_message(guard_stack, agent_id, message)

    def _send_message(self, guard_stack, agent_id, message):
        _text(message, "message")
        self.get_agent(agent_id)
        row = self._row(agent_id)
        if row["family"] == "gpt":
            return {"native_action": {"tool": "send_message", "arguments": {"target": agent_id, "message": message}}}
        store, resources = self._dispatch_resources(row["config"])
        with self._transaction() as db:
            row = dict(db.execute("SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone())
            context = self._dispatch_context(store, resources, db, row['cwd'], row['project_id'])
            self._allowed(context, resources, row['model'], row['config'], guard_stack)
            if row["cancel"] or row["status"] in {"cancelling", "orphaned"}:
                raise BridgeError("busy", "Wait until cancellation/cleanup finishes")
            db.execute("INSERT INTO inbox(agent_id,message) VALUES (?,?)", (agent_id, message))
        return {"agent_id": agent_id, "delivery": "next_turn_boundary" if row["status"] in ACTIVE else "queued_until_followup"}

    def followup_task(self, agent_id, message, model=None):
        with contextlib.ExitStack() as guard_stack:
            return self._followup_task(guard_stack, agent_id, message, model)

    def _followup_task(self, guard_stack, agent_id, message, model=None):
        _text(message, "message")
        if model is not None:
            _text(model, "model")
        self.get_agent(agent_id)
        row = self._row(agent_id)
        if row["family"] == "gpt":
            if model is not None and model != row["model"]:
                raise BridgeError("unsupported_model_change", "Native follow-up does not expose a model override; start a new native agent")
            return {"native_action": {"tool": "followup_task", "arguments": {"target": agent_id, "message": message}}}
        store, resources = self._dispatch_resources(row["config"])
        selected = model if model is not None else row['model']
        with self._db() as db:
            context = self._dispatch_context(store, resources, db, row['cwd'], row['project_id'])
            descriptor, target = self._allowed(context, resources, selected, row['config'], guard_stack)
            # A deliberate idle switch may leave a denied model, but it must
            # not silently revive a session whose source alias disappeared.
            if row['model'] not in resources['models']:
                raise BridgeError('model_not_allowed', 'The session source alias is no longer available.')
            family = resources['models'][selected]['family']
            self._check_family(db, row['scope'], row['unit'], row['role'], family)
        group = self._provider_group(target['config_path'], target['model'])
        fd = None
        if row['status'] not in ACTIVE | {'cancelling', 'orphaned'}:
            if group == 'zai':
                self._zai_quota_check(target['config_path'], target['model'])
            elif group == 'kimi':
                self._kimi_quota_check(target['config_path'], target['model'])
        with self._transaction() as db:
            row = dict(db.execute("SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone())
            context = self._dispatch_context(store, resources, db, row['cwd'], row['project_id'])
            self._allowed(context, resources, selected, row['config'], guard_stack)
            if row["status"] in {"cancelling", "orphaned"}:
                raise BridgeError("busy", "Wait for process cleanup before following up")
            if row["status"] in ACTIVE:
                if model is not None and model != row["model"]:
                    raise BridgeError("busy", "Wait or interrupt this Kimi turn before changing its model")
                db.execute("INSERT INTO inbox(agent_id,message) VALUES (?,?)", (agent_id, message))
                return {"agent_id": agent_id, "delivery": "next_turn_boundary"}
            if not row["session_id"]:
                raise BridgeError("cannot_resume", "No captured Kimi session ID; do not guess a session")
            self._kimi_env(row["config"])
            context = self._dispatch_context(store, resources, db, row['cwd'], row['project_id'])
            descriptor, target = self._allowed(context, resources, selected, row['config'], guard_stack)
            self._check_family(db, row['scope'], row['unit'], row['role'], family)
            self._project_admit(db, context)
            if group == "zai":
                self._zai_admit(db, row["scope"], row["unit"], row["role"])
            self._account_admit(db, target["config_path"], target["model"])
            slot, fd = self._acquire_for(db, row["scope"], row["unit"], row["role"], context["project_id"])
            try:
                self._remember_slot(db, slot, target["config_path"], target["model"])
                db.execute("UPDATE agents SET status='starting',model=?,family=?,provider=?,slot=?,project_id=?,cancel=0,worker_pid=NULL,worker_identity=NULL,updated=? WHERE id=?",
                           (selected, family, group, slot, context['project_id'], time.time(), agent_id))
                db.execute("UPDATE claims SET model=?,family=? WHERE owner=?", (selected, family, agent_id))
                queued_seq = db.execute("INSERT INTO inbox(agent_id,message) VALUES (?,?)", (agent_id, message)).lastrowid
            except BaseException:
                os.close(fd)
                raise
        try:
            self._launch(agent_id, fd)
        except BaseException:
            with self._transaction() as db:
                db.execute('DELETE FROM inbox WHERE seq=?', (queued_seq,))
                db.execute('UPDATE claims SET model=?,family=? WHERE owner=?', (row['model'], row['family'], agent_id))
                fields = ('status', 'model', 'family', 'provider', 'slot', 'project_id', 'cancel', 'worker_pid', 'worker_identity', 'updated')
                db.execute('UPDATE agents SET ' + ','.join(key + '=?' for key in fields) + ' WHERE id=?', tuple(row[key] for key in fields) + (agent_id,))
            raise
        return self.get_agent(agent_id)

    @staticmethod
    def _kimi_env(config_path):
        source = Path(config_path).expanduser()
        path = source.parent.resolve() / source.name
        if path.name != "config.toml":
            raise BridgeError("unsupported_config_path", "Kimi Code requires a file named config.toml; select its existing directory with KIMI_CODE_HOME")
        env = dict(os.environ)
        env["KIMI_CODE_HOME"] = str(path.parent)
        return env

    def doctor(self):
        try:
            from .diagnostics import doctor
        except ImportError:
            from diagnostics import doctor
        return doctor(self)

    def prune_logs(self, older_than_days=30, dry_run=True):
        _integer(older_than_days, "older_than_days", 0, 36500)
        if not isinstance(dry_run, bool):
            raise BridgeError("invalid_argument", "dry_run must be boolean")
        cutoff = time.time() - older_than_days * 86400
        files = []
        skipped = []
        with self._transaction() as db:
            for row in db.execute("SELECT * FROM agents WHERE family!='gpt' AND status IN ('completed','failed','interrupted')"):
                if _identity(row["worker_pid"]) == row["worker_identity"] and row["worker_identity"]:
                    continue
                if _identity(row["child_pid"]) == row["child_identity"] and row["child_identity"]:
                    continue
                fd = self._slot(row["slot"]) if row["slot"] is not None else None
                if row["slot"] is not None and fd is None:
                    continue
                try:
                    for path in self.state.glob(row["id"].replace(":", "-") + "-turn-*.jsonl"):
                        if not re.fullmatch(r"kimi-[0-9a-f]{32}-turn-[0-9]+\.jsonl", path.name):
                            continue
                        try:
                            if path.is_symlink() or not path.is_file() or path.stat().st_mtime > cutoff:
                                continue
                            if not dry_run:
                                path.unlink()
                            files.append(path.name)
                        except OSError:
                            skipped.append(path.name)
                finally:
                    if fd is not None:
                        os.close(fd)
        return {"dry_run": dry_run, "older_than_days": older_than_days,
                "files": sorted(files), "count": len(files), "deleted": 0 if dry_run else len(files), "skipped": sorted(skipped)}

    def interrupt_agent(self, agent_id):
        row = self._row(agent_id)
        if row["family"] == "gpt":
            return {"native_action": {"tool": "interrupt_agent", "arguments": {"target": agent_id}}}
        with self._transaction() as db:
            row = dict(db.execute("SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone())
            db.execute("DELETE FROM inbox WHERE agent_id=?", (agent_id,))
            if row["status"] in ACTIVE:
                db.execute("UPDATE agents SET cancel=1,status='cancelling' WHERE id=?", (agent_id,))
        if row["status"] == "orphaned":
            identity = _identity(row["child_pid"])
            if identity and (identity != row["child_identity"] or not row["child_identity"]):
                raise BridgeError("orphan_unverified", "Cannot safely signal a stale process identity")
            if identity:
                _stop_group(row["child_pid"])
            fd = self._slot(row["slot"])
            if fd is not None:
                os.close(fd)
                with self._db() as db:
                    db.execute("UPDATE agents SET status='interrupted',cancel=0,updated=? WHERE id=?", (time.time(), agent_id))
        end = time.monotonic() + 6
        while time.monotonic() < end:
            current = self.get_agent(agent_id)
            if current["status"] not in ACTIVE:
                return current
            time.sleep(0.1)
        return self.get_agent(agent_id)


def _stop_group(pid):
    if not pid:
        return
    def exists():
        snapshot = subprocess.run(["ps", "-axo", "pid=,pgid=,stat="], capture_output=True, text=True)
        if snapshot.returncode:
            raise RuntimeError("Cannot verify process group for cleanup")
        return any(len(parts) == 3 and parts[1] == str(pid) and "Z" not in parts[2]
                   for parts in (line.split() for line in snapshot.stdout.splitlines()))
    if not exists():
        return
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except PermissionError:
        if not exists():
            return  # macOS can return EPERM for a just-exited process group.
        raise
    # Keep the slot during the grace period and kill any descendants left over.
    time.sleep(0.15)
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError:
        if exists():
            raise


def _content(event):
    value = event.get("content", "")
    if isinstance(value, list):
        return "\n".join(x.get("text", "") for x in value if isinstance(x, dict))
    return value if isinstance(value, str) else ""


def run_worker(state_dir, agent_id, slot_fd):
    os.umask(0o077)
    manager = AgentManager(state_dir)
    child = None
    turn_secrets = ()
    pending_turn = None
    turn_started = False
    prepared = contextlib.ExitStack()
    interrupted = False
    manager.config = Path(manager._row(agent_id)["config"])

    def signal_stop(signum, frame):
        nonlocal interrupted
        interrupted = True

    signal.signal(signal.SIGTERM, signal_stop)
    signal.signal(signal.SIGINT, signal_stop)
    try:
        with manager._db() as db:
            db.execute("UPDATE agents SET worker_pid=?,worker_identity=? WHERE id=?", (os.getpid(), _identity(os.getpid()), agent_id))
        while True:
            pending_turn = None
            turn_started = False
            snapshot = manager._row(agent_id)
            with manager._transaction() as db:
                if interrupted or snapshot['cancel']:
                    db.execute('DELETE FROM inbox WHERE agent_id=?', (agent_id,))
                    db.execute("UPDATE agents SET status='interrupted',cancel=0,updated=? WHERE id=?", (time.time(), agent_id))
                    break
                if not db.execute('SELECT 1 FROM inbox WHERE agent_id=?', (agent_id,)).fetchone():
                    db.execute("UPDATE agents SET status='completed',updated=? WHERE id=?", (time.time(), agent_id))
                    break
            store, resources = manager._dispatch_resources()
            with manager._db() as db:
                context = manager._dispatch_context(store, resources, db, snapshot['cwd'], snapshot['project_id'])
                descriptor, target = manager._allowed(context, resources, snapshot['model'], snapshot['config'], prepared)
                manager._check_family(db, snapshot['scope'], snapshot['unit'], snapshot['role'], resources['models'][snapshot['model']]['family'])
            provider = manager._provider_group(target['config_path'], target['model'])
            guard_module = GUARD_MODULES.get(provider)
            guard_label = GUARD_LABELS.get(provider, "Provider")
            quota_policy = None
            quota_reason = None
            if guard_module is not None:
                quota_policy = manager._zai_policy() if provider == "zai" else manager._kimi_policy()
                if quota_policy["usage_guard"]:
                    # Fresh/cached (<=30s) read before every queued turn,
                    # sourced from the row's own config and exact
                    # alias/account, not this manager's defaults. Unknown
                    # usage stops here; active/queued work never bootstraps.
                    quota_reason = guard_module.block_reason(
                        guard_module.read_usage(manager.state, target["config_path"], alias=target["model"]),
                        quota_policy)
            with manager._transaction() as db:
                row = dict(db.execute("SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone())
                messages = db.execute("SELECT message FROM inbox WHERE agent_id=? ORDER BY seq", (agent_id,)).fetchall()
                if interrupted or row["cancel"]:
                    db.execute("DELETE FROM inbox WHERE agent_id=?", (agent_id,))
                    db.execute("UPDATE agents SET status='interrupted',cancel=0,updated=? WHERE id=?", (time.time(), agent_id))
                    break
                if not messages:
                    db.execute("UPDATE agents SET status='completed',updated=? WHERE id=?", (time.time(), agent_id))
                    break
                context = manager._dispatch_context(store, resources, db, row['cwd'], row['project_id'])
                descriptor, target = manager._allowed(context, resources, row['model'], row['config'], prepared)
                if row["turn"]:
                    try:
                        manager._account_admit(db, target["config_path"], target["model"], exclude_slot=row["slot"])
                        manager._project_admit(db, context, exclude_slot=row["slot"])
                    except BridgeError as exc:
                        quota_reason = exc.code
                    provider_limit = manager._zai_policy()['limit']
                    if provider == 'zai' and provider_limit != 'unlimited' and manager._zai_running(db) > provider_limit:
                        quota_reason = 'provider_capacity_exceeded'
                    cap = manager._cap()
                    if cap is not None and manager._slots_used() > cap:
                        quota_reason = "capacity_exceeded"
                if quota_reason is not None:
                    # Quota stop: interrupted, queue cleared, session and results
                    # preserved, and no access-failure observation recorded.
                    db.execute("DELETE FROM inbox WHERE agent_id=?", (agent_id,))
                    db.execute("UPDATE agents SET status='interrupted',error=?,cancel=0,updated=? WHERE id=?",
                               (f"{guard_label} usage guard stopped a queued turn: {quota_reason}", time.time(), agent_id))
                    break
                if row["turn"] and not row["session_id"]:
                    db.execute("UPDATE agents SET status='failed',error='No Kimi session ID for queued continuation',updated=? WHERE id=?", (time.time(), agent_id))
                    break
                manager._project_admit(db, context, exclude_slot=row['slot'])
                pending_turn = row
                manager._snapshot(db, row, context, resources, descriptor)
                manager._remember_slot(db, row['slot'], target['config_path'], target['model'])
                db.execute('UPDATE agents SET provider=? WHERE id=?', (provider, agent_id))
                db.execute("DELETE FROM inbox WHERE agent_id=?", (agent_id,))
                db.execute("UPDATE agents SET status='running',turn=turn+1,error='',updated=? WHERE id=?", (time.time(), agent_id))
            message = "\n\n".join(m[0] for m in messages)
            materialized = prepared.enter_context(materialization.prepare(row['config'], descriptor, manager.state))
            turn_secrets = security.configured_secrets(row['config']) + security.configured_secrets(materialized['config_path'])
            cmd = json.loads(row["command"]) + ["-m", materialized['model']]
            if row["session_id"]:
                cmd += ["-S", row["session_id"]]
            else:
                cmd += ["--agent-file", str(ROOT / "profiles" / f"{row['profile']}.md")]
            cmd += ["-p", message, "--output-format", "stream-json"]
            child = subprocess.Popen(cmd, cwd=row["cwd"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, pass_fds=(slot_fd,), start_new_session=True,
                                     env=materialized["env"])
            turn_started = True
            with manager._db() as db:
                db.execute("UPDATE agents SET child_pid=?,child_identity=?,progress='Kimi turn started' WHERE id=?", (child.pid, _identity(child.pid), agent_id))
            deadline = time.monotonic() + row["timeout"]
            quota_error = None
            last_quota_check = time.monotonic()
            stderr_tail = ""
            stdout_pending = b""
            result = ""
            timed_out = False
            sel = selectors.DefaultSelector()
            for stream, kind in [(child.stdout, "out"), (child.stderr, "err")]:
                os.set_blocking(stream.fileno(), False)
                sel.register(stream, selectors.EVENT_READ, kind)
            log_path = manager.state / (agent_id.replace(":", "-") + f"-turn-{row['turn'] + 1}.jsonl")
            with open(log_path, "w", encoding="utf-8") as log:
                written = 0
                while sel.get_map():
                    with manager._db() as db:
                        cancel = db.execute("SELECT cancel FROM agents WHERE id=?", (agent_id,)).fetchone()[0]
                    if cancel or interrupted or time.monotonic() > deadline:
                        timed_out = not cancel and not interrupted
                        interrupted = bool(cancel) or interrupted
                        _stop_group(child.pid)
                        break
                    if guard_module is not None and time.monotonic() - last_quota_check >= ZAI_POLL_SECONDS:
                        last_quota_check = time.monotonic()
                        # The provider identity is fixed for this turn, but the
                        # guard policy is live: enabling it mid-turn starts
                        # protecting (reading the row's exact config/alias),
                        # disabling it stops further network reads.
                        policy = manager._zai_policy() if provider == "zai" else manager._kimi_policy()
                        if policy["usage_guard"]:
                            quota_error = guard_module.block_reason(
                                guard_module.read_usage(manager.state, target["config_path"], alias=target["model"]), policy)
                        else:
                            quota_error = None
                    if quota_error is not None:
                        # Quota stop: interrupt the turn keeping session,
                        # results and the slot's provider reservation; the
                        # epilogue clears queued messages and records no
                        # access failure.
                        _stop_group(child.pid)
                        interrupted = True
                        break
                    for key, _ in sel.select(timeout=0.1):
                        data = os.read(key.fileobj.fileno(), 65536)
                        if not data:
                            sel.unregister(key.fileobj)
                            key.fileobj.close()
                            continue
                        if key.data == "err":
                            stderr_tail = (stderr_tail + data.decode("utf-8", "replace"))[-1600:]
                            continue
                        stdout_pending += data
                        while b"\n" in stdout_pending:
                            line, stdout_pending = stdout_pending.split(b"\n", 1)
                            try:
                                event = json.loads(line)
                            except (ValueError, UnicodeDecodeError):
                                continue
                            if not isinstance(event, dict):
                                continue
                            if written < 4_000_000:
                                serialized = json.dumps(security.sanitize(event, turn_secrets), ensure_ascii=False) + "\n"
                                log.write(serialized); log.flush()
                                written += len(serialized)
                            with manager._db() as db:
                                if event.get("type") == "session.resume_hint" and isinstance(event.get("session_id"), str):
                                    db.execute("UPDATE agents SET session_id=? WHERE id=?", (event["session_id"], agent_id))
                                if event.get("role") == "assistant" and _content(event):
                                    result = _content(event)[-8000:]
                                    db.execute("UPDATE agents SET result=?,progress=?,updated=? WHERE id=?", (security.redact(result, turn_secrets), security.redact(result[-1200:], turn_secrets), time.time(), agent_id))
                                elif event.get("role") == "tool":
                                    db.execute("UPDATE agents SET progress=?,updated=? WHERE id=?", (security.redact(_content(event), turn_secrets)[-1200:], time.time(), agent_id))
                sel.close()
            # Always clean the process group before releasing any concurrency slot.
            _stop_group(child.pid)
            try:
                code = child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill(); code = child.wait()
            for pipe in [child.stdout, child.stderr]:
                if pipe and not pipe.closed:
                    pipe.close()
            child = None
            prepared.close()
            if interrupted or timed_out or code != 0 or not result:
                status = "interrupted" if interrupted else "failed"
                if quota_error is not None:
                    error = f"{guard_label} usage guard stopped the active turn: {quota_error}"
                else:
                    error = "" if interrupted else ("Turn timed out" if timed_out else f"Kimi exited {code}: {stderr_tail[-1400:]}" if code else "Kimi returned no assistant result")
                error = manager._error_text(error, row['config'], row['command'], turn_secrets)
                with manager._db() as db:
                    db.execute("UPDATE agents SET status=?,error=?,cancel=0,updated=? WHERE id=?", (status, error, time.time(), agent_id))
                    if interrupted:
                        db.execute("DELETE FROM inbox WHERE agent_id=?", (agent_id,))
                    if not interrupted:
                        db.execute("INSERT INTO observations(model,failures,last_error) VALUES (?,1,?) ON CONFLICT(model) DO UPDATE SET failures=failures+1,last_error=excluded.last_error", (row["model"], error))
                break
            with manager._db() as db:
                db.execute("INSERT INTO observations(model,successes) VALUES (?,1) ON CONFLICT(model) DO UPDATE SET successes=successes+1", (row["model"],))
            # A queued message is consumed in the next loop with the same session.
    except BaseException as exc:
        status = "failed"
        if child:
            try:
                _stop_group(child.pid)
                child.wait(timeout=5)
            except Exception:
                status = "orphaned"  # Child retains its slot FD if cleanup failed.
        if pending_turn is not None and not turn_started:
            with manager._transaction() as db:
                db.execute('DELETE FROM turn_snapshots WHERE agent_id=? AND turn=?', (agent_id, pending_turn['turn'] + 1))
                db.execute('DELETE FROM inbox WHERE agent_id=?', (agent_id,))
                db.execute('UPDATE agents SET turn=?,configuration_snapshot=?,project_id=? WHERE id=?',
                           (pending_turn['turn'], pending_turn['configuration_snapshot'], pending_turn['project_id'], agent_id))
        with manager._db() as db:
            db.execute('DELETE FROM inbox WHERE agent_id=?', (agent_id,))
            if isinstance(exc, OSError):
                error = 'Worker could not access a required executable or runtime resource.'
            else:
                error = (exc.code + ': ' + str(exc)) if isinstance(exc, (BridgeError, materialization.MaterializationError)) else str(exc)
            failed_row = manager._row(agent_id)
            error = manager._error_text(error, failed_row['config'], failed_row['command'], turn_secrets)
            db.execute("UPDATE agents SET status=?,error=?,updated=? WHERE id=?", (status, error[-1600:], time.time(), agent_id))
    finally:
        prepared.close()
        os.close(slot_fd)


if __name__ == "__main__":
    if len(sys.argv) == 5 and sys.argv[1] == "--worker":
        run_worker(sys.argv[2], sys.argv[3], int(sys.argv[4]))
