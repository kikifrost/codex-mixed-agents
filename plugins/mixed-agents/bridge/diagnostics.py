"""Bounded offline checks; readiness never means account entitlement."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tomllib

try:
    from . import materialization, providers, security
    from .version import VERSION
except ImportError:
    import materialization, providers, security
    from version import VERSION


def _credential_source(provider):
    """Describe presence only, without returning any credential material."""
    provider = provider if isinstance(provider, dict) else {}
    provider_env = provider.get("env", {})
    provider_env = provider_env if isinstance(provider_env, dict) else {}
    env_name = provider.get("api_key_env")
    return ("oauth" if provider.get("oauth") else "api_key" if provider.get("api_key") else
            "api_key_env" if isinstance(env_name, str) and os.environ.get(env_name) else
            "provider_env" if any(provider_env.get(k) for k in
                                  ("KIMI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY")) else "missing")


def _model_credential_source(config, model, provider):
    if model.get("source") == "imported":
        entry = config.get("models", {}).get(model["alias"], {})
        raw_providers = config.get("providers", {})
        return _credential_source(raw_providers.get(entry.get("provider"))
                                  if isinstance(raw_providers, dict) else None)
    reference = provider.get("credential_ref") or ""
    if reference.startswith("env:"):
        return "api_key_env" if os.environ.get(reference[4:]) else "missing"
    if reference.startswith("existing:"):
        try:
            _, source = materialization._physical_provider(config, reference[9:])
            return _credential_source(source)
        except materialization.MaterializationError:
            pass
    return "missing"


def doctor(manager):
    checks = []

    def add(name, status, message, **details):
        checks.append(dict(name=name, status=status, message=message, **details))

    add("platform", "ok" if sys.platform in {"darwin", "linux"} else "error", "Requires macOS or Linux", platform=sys.platform)
    add("python", "ok" if sys.version_info >= (3, 11) else "error", "Requires Python 3.11+", version=sys.version.split()[0])
    executable = shutil.which(manager.command[0])
    add("kimi_executable", "ok" if executable else "error", "Install Kimi Code and expose kimi on PATH" if not executable else "Kimi executable found")
    try:
        env = manager._kimi_env(manager.config)
    except Exception:
        env = None
        add("config_location", "error", "Kimi Code requires a file named config.toml in KIMI_CODE_HOME")
    if executable and env is not None:
        try:
            output = subprocess.run(manager.command + ["--version"], capture_output=True, text=True, timeout=5, env=env)
            match = re.search(r"\b(\d+)\.(\d+)\.(\d+)\b", output.stdout)
            version = tuple(map(int, match.groups())) if match else None
            add("kimi_version", "ok" if output.returncode == 0 and version and version >= (2, 1, 1) else "error",
                "Tested with Kimi Code 2.1.1; requires 2.1.1+", version=".".join(map(str, version)) if version else None)
            help_result = subprocess.run(manager.command + ["--help"], capture_output=True, text=True, timeout=5, env=env)
            required = ("--model", "--session", "--agent-file", "--prompt", "--output-format")
            missing = [flag for flag in required if flag not in help_result.stdout]
            add("kimi_flags", "error" if help_result.returncode or missing else "ok", "Required bridge flags", missing=missing)
        except (OSError, subprocess.TimeoutExpired):
            add("kimi_version", "error", "Kimi version/help check failed or exceeded five seconds; check installation")
    config = None
    try:
        with manager.config.open("rb") as stream:
            config = tomllib.load(stream)
        add("kimi_config", "ok", "TOML configuration is readable")
    except FileNotFoundError:
        add("kimi_config", "error", "Run kimi login or configure providers in KIMI_CODE_HOME/config.toml")
    except (OSError, ValueError):
        add("kimi_config", "error", "Configuration is unreadable or invalid TOML; no source content included")
    if config is not None:
        try:
            resources = providers.build_resources(manager.config, manager.db_path,
                                                 security.configured_secrets(manager.config))
            connections = resources["providers"]
            models = [m for m in resources["models"].values()
                      if m.get("enabled", True)
                      and connections[m["provider_id"]].get("enabled", True)]
            aliases = [m["alias"] for m in models]
            configured = config.get("models", {})
            unsupported = sorted(set(configured) - set(resources["models"])) if isinstance(configured, dict) else []
            add("models", "ok" if aliases else "error", "Recognition only; no live access or quota check", supported_aliases=aliases, unsupported_aliases=unsupported)
            sources = [dict(alias=m["alias"], source=_model_credential_source(
                config, m, connections[m["provider_id"]])) for m in models]
            add("credentials", "error" if any(s["source"] == "missing" for s in sources) else "ok",
                "Credential source presence only; identity, OAuth token validity and account access are not verified", sources=sources)
        except (ValueError, TypeError, providers.ConfigurationError):
            add("models", "error", "Invalid or conflicting model resources; check imported and managed configuration")
    for manifest in ("plugin.json", ".codex-plugin/plugin.json"):
        try:
            path = Path(__file__).resolve().parents[1] / manifest
            data = json.loads(path.read_text())
            add("manifest:" + manifest, "ok" if data.get("version") == VERSION else "error", "Manifest must match runtime version", version=data.get("version"))
        except (OSError, ValueError, AttributeError):
            add("manifest:" + manifest, "error", "Manifest missing or invalid")
    try:
        capacity = manager._capacity()
        add("concurrency", "ok", "Shared local capacity; independent of provider quota", **capacity)
    except Exception:
        add("concurrency", "error", "Shared concurrency configuration is invalid")
    try:
        policy = manager._zai_policy()
        checks.append(dict(name="zai_policy", status="ok",
                           message="Z.ai GLM provider policy (offline; no quota read here)",
                           cap=None if policy["limit"] == "unlimited" else policy["limit"],
                           unlimited=policy["limit"] == "unlimited",
                           usage_guard=policy["usage_guard"],
                           stop_remaining_percent=policy["stop_remaining_percent"]))
    except Exception:
        add("zai_policy", "error", "Z.ai provider policy configuration is invalid")
    try:
        with manager._db() as db:
            zai_running = manager._zai_running(db)
        add("zai_capacity", "ok", "Live Z.ai workers sharing the provider cap; no network quota check",
            running=zai_running)
    except Exception:
        add("zai_capacity", "error", "Could not inspect Z.ai provider capacity")
    return {"ready": not any(c["status"] == "error" for c in checks), "version": VERSION,
            "checks": checks, "account_access": "not_probed", "native_gpt": "host_managed_not_probed"}
