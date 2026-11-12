"""Z.ai GLM policy normalization and five-hour quota reading (stdlib only).

Seam shared by the runtime: policy defaults/validation, provider-origin alias
recognition, quota payload interpretation, a bounded credential-safe usage
reader with a short-lived sanitized cache, and the reserve/unavailable block
decision. Credentials are read only from the existing Kimi CLI config and are
never persisted or logged; quota responses are reduced to safe metrics and
safe error codes before leaving this module. Raw provider messages, response
bodies and credential values never appear in results or errors.
"""
from __future__ import annotations

import datetime
import fcntl
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import time
import tomllib
import urllib.parse

try:
    from . import catalog
except ImportError:
    import catalog

DEFAULT_LIMIT = 3
DEFAULT_USAGE_GUARD = False
DEFAULT_STOP_REMAINING_PERCENT = 10
POLICY_FIELDS = ("limit", "usage_guard", "stop_remaining_percent")

PROVIDER_HOST = "api.z.ai"
QUOTA_URL = "https://api.z.ai/api/monitor/usage/quota/limit"
QUOTA_HOST = PROVIDER_HOST
QUOTA_PATH = "/api/monitor/usage/quota/limit"

PROVIDER_QUOTA_RESERVE = "provider_quota_reserve"
PROVIDER_USAGE_UNAVAILABLE = "provider_usage_unavailable"

SAFE_ERROR_CODES = frozenset({
    "config_error", "alias_not_zai", "no_zai_account", "credential_missing",
    "redirect_rejected", "http_error", "network_error", "body_too_large",
    "parse_error", "provider_error", "no_data", "busy", "unknown_error",
})

FIVE_HOUR_UNIT = 3
FIVE_HOUR_NUMBER = 5
USAGE_TYPES = ("CREDIT_LIMIT", "TOKENS_LIMIT")

USAGE_TTL_SECONDS = 30.0
REQUEST_TIMEOUT_SECONDS = 10.0
LOCK_WAIT_SECONDS = 15.0
LOCK_POLL_SECONDS = 0.05
MAX_BODY_BYTES = 1 << 20
CACHE_KEEP_SECONDS = 3600.0
MAX_CACHE_ENTRIES = 64

_FINGERPRINT_CONTEXT = "mixed-agents/zai-account/v1"
_LOCK_NAME = "zai-usage.lock"
_CACHE_NAME = "zai-usage-cache.json"


class _Unavailable(Exception):
    """Internal carrier for a safe error code; never holds provider data."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _number(value):
    """Return value as a finite float, or None for non-numeric input.

    Booleans are rejected explicitly: ``True`` is an ``int`` subclass in
    Python but is never a valid quantity here.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    if not math.isfinite(number):
        return None
    return number


def _unknown(code):
    return {
        "available": False,
        "used_percent": None,
        "remaining_percent": None,
        "reset_at": None,
        "error": code,
    }


def _normalize_limit(value):
    if value == "unlimited":
        return "unlimited"
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("zai policy limit must be a nonnegative integer (0 = unlimited) or 'unlimited'")
    if value < 0:
        raise ValueError("zai policy limit must be a nonnegative integer (0 = unlimited) or 'unlimited'")
    return value or "unlimited"


def _normalize_threshold(value):
    threshold = _number(value)
    if threshold is None or not 5 < threshold < 100:
        raise ValueError("stop_remaining_percent must be finite and strictly between 5 and 100")
    return value


def normalize_policy(raw):
    """Validate a partial policy object and fill defaults.

    Defaults: ``limit=3``, ``usage_guard=false``, ``stop_remaining_percent=10``.
    The limit is a nonnegative integer (0 = unlimited) or ``"unlimited"``; the
    threshold is finite and strictly between 5 and 100. Null (callers pass {}
    for a missing policy), unknown fields or invalid values raise ValueError;
    unknown-field errors describe the allowed shape only, never the raw key
    names, which could hold secrets.
    """
    if raw is None:
        raise ValueError("zai policy must be an object; pass {} for a missing policy")
    if not isinstance(raw, dict):
        raise ValueError("zai policy must be an object")
    if any(field not in POLICY_FIELDS for field in raw):
        raise ValueError(
            "zai policy contains unknown fields; allowed fields: %s"
            % ", ".join(POLICY_FIELDS))
    policy = {
        "limit": DEFAULT_LIMIT,
        "usage_guard": DEFAULT_USAGE_GUARD,
        "stop_remaining_percent": DEFAULT_STOP_REMAINING_PERCENT,
    }
    if "limit" in raw:
        policy["limit"] = _normalize_limit(raw["limit"])
    if "usage_guard" in raw:
        if not isinstance(raw["usage_guard"], bool):
            raise ValueError("usage_guard must be a boolean")
        policy["usage_guard"] = raw["usage_guard"]
    if "stop_remaining_percent" in raw:
        policy["stop_remaining_percent"] = _normalize_threshold(raw["stop_remaining_percent"])
    return policy


def _is_zai_origin(base_url):
    """True only for an HTTPS URL whose origin is exactly api.z.ai.

    Origin comparison ignores userinfo: an embedded ``user:pass`` still matches
    because it cannot bypass the provider cap, and quota reads always target
    the fixed host, never the provided URL. Look-alike hosts
    (api.z.ai.evil.invalid), other origins (Ark, Kimi), non-HTTPS schemes and
    non-default ports never match.
    """
    if not isinstance(base_url, str) or not base_url:
        return False
    try:
        parsed = urllib.parse.urlsplit(base_url)
    except ValueError:
        return False
    if parsed.scheme.lower() != "https":
        return False
    if (parsed.hostname or "").lower() != PROVIDER_HOST:
        return False
    try:
        port = parsed.port
    except ValueError:
        return False
    return port is None or port == 443


def _load_config(config_path):
    path = Path(config_path).expanduser() if config_path is not None else catalog.default_config_path()
    try:
        with open(path, "rb") as stream:
            config = tomllib.load(stream)
    except (FileNotFoundError, tomllib.TOMLDecodeError, OSError, ValueError):
        raise _Unavailable("config_error") from None
    if not isinstance(config, dict):
        raise _Unavailable("config_error")
    return path, config


def _zai_accounts(config_path):
    """Return ({alias: provider name}, {provider name: provider entry}).

    Only aliases whose remote model is a recognized GLM profile and whose
    configured provider has HTTPS origin api.z.ai are included; the alias
    spelling itself is irrelevant. Credential values are never returned.
    """
    _, config = _load_config(config_path)
    models = config.get("models")
    providers = config.get("providers")
    if not isinstance(models, dict) or not isinstance(providers, dict):
        return {}, {}
    mapping = {}
    zai_providers = {}
    for alias, entry in models.items():
        if not isinstance(alias, str) or not isinstance(entry, dict):
            continue
        family, _ = catalog._cli_profile_for_model(str(entry.get("model") or ""))
        if family != "glm":
            continue
        provider_name = entry.get("provider")
        if not isinstance(provider_name, str):
            continue
        provider = providers.get(provider_name)
        if not isinstance(provider, dict):
            continue
        if not _is_zai_origin(provider.get("base_url")):
            continue
        mapping[alias] = provider_name
        zai_providers[provider_name] = provider
    return mapping, zai_providers


def aliases(config_path):
    """Set of recognized GLM aliases whose provider origin is api.z.ai.

    An unreadable or invalid config yields an empty set; no credentials,
    provider names or base URLs are exposed.
    """
    try:
        mapping, _ = _zai_accounts(config_path)
    except _Unavailable:
        return set()
    return set(mapping)


def _envelope_ok(payload):
    if "success" in payload and payload["success"] is not True:
        return False
    if "code" in payload:
        code = _number(payload["code"])
        if code is None or not 200 <= code < 300:
            return False
    return True


def _reset_at(value):
    milliseconds = _number(value)
    if milliseconds is None or milliseconds <= 0:
        return None
    try:
        moment = datetime.datetime.fromtimestamp(milliseconds / 1000.0,
                                                 tz=datetime.timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_usage(payload):
    """Interpret a quota payload into safe metrics.

    Returns ``available``, ``used_percent``, ``remaining_percent`` and the
    informational ``reset_at``. Five-hour rows are CREDIT_LIMIT/TOKENS_LIMIT
    entries with unit=3 and number=5; their ``percentage`` is the USED share,
    a precise currentValue/usage ratio is preferred, and the greater valid
    percentage is kept when both are reported. Multiple five-hour rows take
    the lowest remaining. Missing, non-numeric, boolean, non-finite or
    out-of-range data yields unknown metrics with a safe error code — never
    zero — and error envelopes (success=false, non-2xx code) never authorize
    usage.
    """
    if not isinstance(payload, dict):
        return _unknown("parse_error")
    if not _envelope_ok(payload):
        return _unknown("provider_error")
    container = payload["data"] if "data" in payload else payload
    if not isinstance(container, dict):
        return _unknown("parse_error")
    limits = container.get("limits")
    if not isinstance(limits, list):
        return _unknown("parse_error")
    best = None
    saw_five_hour = False
    saw_invalid = False
    for row in limits:
        if not isinstance(row, dict):
            saw_invalid = True
            continue
        row_type = row.get("type")
        if not isinstance(row_type, str):
            row_type = row.get("name")
        if row_type not in USAGE_TYPES:
            continue
        if _number(row.get("unit")) != FIVE_HOUR_UNIT:
            continue
        if _number(row.get("number")) != FIVE_HOUR_NUMBER:
            continue
        saw_five_hour = True
        candidates = []
        percentage = _number(row.get("percentage"))
        if percentage is not None:
            if 0 <= percentage <= 100:
                candidates.append(percentage)
            else:
                saw_invalid = True
        current = _number(row.get("currentValue"))
        total = _number(row.get("usage"))
        if current is not None and total is not None:
            if total > 0 and 0 <= current <= total:
                candidates.append(current / total * 100)
            else:
                saw_invalid = True
        if not candidates:
            continue
        used = max(candidates)
        if best is None or used > best[0]:
            best = (used, _reset_at(row.get("nextResetTime")))
    if best is None:
        if saw_five_hour and saw_invalid:
            return _unknown("parse_error")
        return _unknown("no_data")
    used = best[0]
    return {
        "available": True,
        "used_percent": used,
        "remaining_percent": 100 - used,
        "reset_at": best[1],
        "error": None,
    }


def block_reason(usage, policy):
    """Safe block reason for a usage reading under a policy, or None.

    None when the guard is disabled or the trustworthy remaining reading is
    above the reserve threshold; otherwise ``provider_quota_reserve`` at or
    below the threshold, or ``provider_usage_unavailable`` for anything not
    provably trustworthy (missing, non-numeric, boolean, non-finite or
    out-of-range readings fail closed).
    """
    if not isinstance(policy, dict) or policy.get("usage_guard") is not True:
        return None
    remaining = _number(usage.get("remaining_percent")) if isinstance(usage, dict) else None
    if (not isinstance(usage, dict) or usage.get("available") is not True
            or remaining is None or not 0 <= remaining <= 100):
        return PROVIDER_USAGE_UNAVAILABLE
    threshold = _number(policy.get("stop_remaining_percent"))
    if threshold is None:
        threshold = float(DEFAULT_STOP_REMAINING_PERCENT)
    if remaining <= threshold:
        return PROVIDER_QUOTA_RESERVE
    return None


def _account_credential(provider):
    key = provider.get("api_key")
    if isinstance(key, str) and key.strip():
        return key
    env_name = provider.get("api_key_env")
    if isinstance(env_name, str) and env_name:
        value = os.environ.get(env_name)
        if isinstance(value, str) and value:
            return value
    return None


def _account_fingerprint(config_path, provider_name, provider, credential):
    material = json.dumps(
        [_FINGERPRINT_CONTEXT, str(config_path), provider_name,
         str(provider.get("base_url") or ""), credential or ""],
        sort_keys=True)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _fetch_usage_payload(api_key):
    """GET the fixed HTTPS quota endpoint; bounded, redirects rejected.

    Uses the credential only in the Authorization header. Raises _Unavailable
    with a safe code on any network, redirect, size or parse failure; response
    bodies never travel with the error.
    """
    connection = http.client.HTTPSConnection(QUOTA_HOST,
                                             timeout=REQUEST_TIMEOUT_SECONDS)
    try:
        connection.request("GET", QUOTA_PATH, headers={
            "Authorization": "Bearer %s" % api_key,
            "Accept": "application/json",
        })
        response = connection.getresponse()
        status = response.status
        if 300 <= status < 400:
            raise _Unavailable("redirect_rejected")
        if status != 200:
            raise _Unavailable("http_error")
        body = response.read(MAX_BODY_BYTES + 1)
    except _Unavailable:
        raise
    except (OSError, http.client.HTTPException):
        raise _Unavailable("network_error") from None
    finally:
        connection.close()
    if len(body) > MAX_BODY_BYTES:
        raise _Unavailable("body_too_large")
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise _Unavailable("parse_error") from None


def _read_account(api_key, observed_at):
    try:
        payload = _fetch_usage_payload(api_key)
        metrics = parse_usage(payload)
    except _Unavailable as error:
        return _observed(_unknown(error.code), observed_at)
    except Exception:
        return _observed(_unknown("unknown_error"), observed_at)
    return _observed(metrics, observed_at)


def _observed(metrics, observed_at):
    result = dict(metrics)
    result["observed_at"] = observed_at
    return result


def _valid_reset_at(value):
    """True for None or a string exactly in _reset_at's UTC ISO shape."""
    if value is None:
        return True
    if not isinstance(value, str):
        return False
    try:
        moment = datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return False
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ") == value


def _fresh_entry(entry, now):
    """Return a cache entry only if it is recent and safely shaped.

    Corrupted, injected or future-dated entries are discarded and refetched
    rather than trusted: available entries need finite in-range metrics that
    sum to 100, no error and a None/valid-ISO reset_at; unavailable entries
    need one of this module's safe error codes — arbitrary cached strings or
    objects (which could hold provider data) never pass.
    """
    if not isinstance(entry, dict):
        return None
    observed_at = _number(entry.get("observed_at"))
    if observed_at is None or not 0 <= now - observed_at <= USAGE_TTL_SECONDS:
        return None
    if entry.get("available") is True:
        used = _number(entry.get("used_percent"))
        remaining = _number(entry.get("remaining_percent"))
        if entry.get("error") is not None:
            return None
        if used is None or remaining is None:
            return None
        if not 0 <= used <= 100 or not 0 <= remaining <= 100:
            return None
        if abs(used + remaining - 100) > 1e-6:
            return None
        if not _valid_reset_at(entry.get("reset_at")):
            return None
        return entry
    if entry.get("error") not in SAFE_ERROR_CODES:
        return None
    return entry


def _load_cache(state_dir):
    try:
        raw = (state_dir / _CACHE_NAME).read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    entries = data.get("accounts")
    if not isinstance(entries, dict):
        return {}
    return {str(fp): entry for fp, entry in entries.items() if isinstance(entry, dict)}


def _save_cache(state_dir, entries, now):
    pruned = {
        fp: entry for fp, entry in entries.items()
        if _number(entry.get("observed_at")) is not None
        and now - entry["observed_at"] < CACHE_KEEP_SECONDS
    }
    if len(pruned) > MAX_CACHE_ENTRIES:
        newest = sorted(pruned.items(),
                        key=lambda item: -item[1]["observed_at"])[:MAX_CACHE_ENTRIES]
        pruned = dict(newest)
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = state_dir / (_CACHE_NAME + ".tmp")
        fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump({"accounts": pruned}, stream, sort_keys=True)
        except ValueError:
            return
        os.replace(str(tmp_path), str(state_dir / _CACHE_NAME))
    except OSError:
        return


def _with_single_flight(state_dir, perform):
    """Run perform() holding a cross-process exclusive lock when possible.

    Waits a bounded interval for a concurrent reader; on persistent
    contention fails closed with a safe busy code instead of stampeding the
    quota endpoint. A missing or unwritable state dir degrades to an
    uncached direct read.
    """
    if state_dir is None:
        return perform()
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(state_dir / _LOCK_NAME), os.O_RDWR | os.O_CREAT, 0o600)
    except OSError:
        return perform()
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except OSError:
            if time.monotonic() >= deadline:
                os.close(fd)
                return _observed(_unknown("busy"), time.time())
            time.sleep(LOCK_POLL_SECONDS)
    try:
        return perform()
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def read_usage(state_dir, config_path, alias=None):
    """Read sanitized Z.ai quota metrics, or unavailable safe metrics.

    With ``alias`` the reading covers that alias's actual existing account.
    Without one it takes the most conservative reading across configured
    Z.ai accounts; any unavailable account makes the whole reading unknown.
    Sanitized metrics are cached for at most 30 seconds under a
    credential-fingerprint key (credential or account changes invalidate the
    cache) and reads are single-flight across processes. Expected config,
    credential, network and parse failures return unavailable metrics with a
    safe error code — they never raise and never leak credentials or raw
    provider data.
    """
    state_dir = Path(state_dir) if state_dir is not None else None
    try:
        mapping, providers = _zai_accounts(config_path)
    except _Unavailable as error:
        return _observed(_unknown(error.code), time.time())
    if alias is not None:
        provider_name = mapping.get(alias)
        if provider_name is None:
            return _observed(_unknown("alias_not_zai"), time.time())
        names = [provider_name]
    else:
        names = sorted(set(mapping.values()))
        if not names:
            return _observed(_unknown("no_zai_account"), time.time())
    accounts = []
    for name in names:
        credential = _account_credential(providers[name])
        if credential is None:
            return _observed(_unknown("credential_missing"), time.time())
        accounts.append((name, credential))

    def perform():
        now = time.time()
        cache = _load_cache(state_dir) if state_dir is not None else {}
        results = {}
        stale = []
        for name, credential in accounts:
            fingerprint = _account_fingerprint(config_path, name, providers[name], credential)
            entry = _fresh_entry(cache.get(fingerprint), now)
            if entry is not None:
                results[name] = entry
            else:
                stale.append((name, fingerprint, credential))
        for name, fingerprint, credential in stale:
            entry = _read_account(credential, time.time())
            results[name] = entry
            cache[fingerprint] = entry
        if stale and state_dir is not None:
            _save_cache(state_dir, cache, time.time())
        return _combine([results[name] for name in sorted(results)])

    return _with_single_flight(state_dir, perform)


def _combine(entries):
    if not entries:
        return _observed(_unknown("no_data"), time.time())
    for entry in entries:
        if entry.get("available") is not True:
            code = entry.get("error")
            return _observed(_unknown(code if isinstance(code, str) else "unknown_error"),
                             entry.get("observed_at", time.time()))
    binding = max(entries, key=lambda entry: _number(entry.get("used_percent")) or 0)
    return {
        "available": True,
        "used_percent": binding["used_percent"],
        "remaining_percent": min(_number(entry.get("remaining_percent"))
                                 for entry in entries),
        "reset_at": binding.get("reset_at"),
        "error": None,
        "observed_at": min(_number(entry.get("observed_at")) or time.time()
                           for entry in entries),
    }
