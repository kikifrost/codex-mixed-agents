"""Kimi Code five-hour usage policy, reading and probe allowance (stdlib only).

Seam shared by the runtime: Kimi guard policy defaults/validation, Coding-origin
alias recognition, `/coding/v1/usages` payload interpretation, a bounded
credential-safe usage reader with a short-lived sanitized cache, the
reserve/unavailable block decision, and the durable single-flight bootstrap
probe allowance. Credentials are read only from the existing Kimi Code
installation (config `api_key`/`api_key_env`, or the configured file OAuth
credential — never refreshed, rewritten or copied by the bridge). Quota
responses are reduced to safe metrics and safe error codes before leaving this
module. Raw provider messages, response bodies and credential values never
appear in results or errors.

Applies only to Kimi-family aliases whose configured provider has an HTTPS
`api.kimi.com` Coding origin (`/coding` or `/coding/v1`). Kimi-family aliases
on any other provider report `unsupported_provider` and never inherit another
account's quota.
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

DEFAULT_USAGE_GUARD = False
DEFAULT_STOP_REMAINING_PERCENT = 5
DEFAULT_PROBE_ON_UNKNOWN = False
DEFAULT_PROBE_COOLDOWN_MINUTES = 15
POLICY_FIELDS = ("usage_guard", "stop_remaining_percent", "probe_on_unknown", "probe_cooldown_minutes")

PROVIDER_HOST = "api.kimi.com"
USAGE_PATH = "/coding/v1/usages"
CODING_PATHS = ("/coding", "/coding/v1")
OAUTH_KEY_PREFIX = "oauth/"
# Match the CLI's expiry skew so a token the CLI would refresh is unknown here.
TOKEN_EXPIRY_SKEW_SECONDS = 30.0

PROVIDER_QUOTA_RESERVE = "provider_quota_reserve"
PROVIDER_USAGE_UNAVAILABLE = "provider_usage_unavailable"

SAFE_ERROR_CODES = frozenset({
    "config_error", "alias_not_kimi", "unsupported_provider", "no_kimi_account",
    "credential_missing", "credential_expired", "credential_malformed",
    "unsupported_credential_storage", "redirect_rejected", "http_error",
    "network_error", "body_too_large", "parse_error", "no_data", "busy",
    "unknown_error",
})

USAGE_TTL_SECONDS = 30.0
REQUEST_TIMEOUT_SECONDS = 10.0
LOCK_WAIT_SECONDS = 15.0
LOCK_POLL_SECONDS = 0.05
MAX_BODY_BYTES = 1 << 20
CACHE_KEEP_SECONDS = 3600.0
MAX_CACHE_ENTRIES = 64

PROBE_COOLDOWN_SECONDS = DEFAULT_PROBE_COOLDOWN_MINUTES * 60.0
PROBE_MAX_ENTRIES = 64

_FINGERPRINT_CONTEXT = "mixed-agents/kimi-account/v1"
_LOCK_NAME = "kimi-usage.lock"
_CACHE_NAME = "kimi-usage-cache.json"
_PROBE_LOCK_NAME = "kimi-probe.lock"
_PROBE_NAME = "kimi-probe-allowance.json"


class _Unavailable(Exception):
    """Internal carrier for a safe error code; never holds provider data."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _number(value):
    """Return value as a finite float, or None for non-numeric input.

    Accepts decimal string numerals (the usages endpoint may serialize window
    durations and amounts as strings); booleans are never valid quantities.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        try:
            value = float(value)
        except ValueError:
            return None
    if not isinstance(value, (int, float)):
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


def _normalize_threshold(value):
    if isinstance(value, bool) or isinstance(value, str):
        raise ValueError("stop_remaining_percent must be a finite number at least 5 and below 100")
    threshold = _number(value)
    if threshold is None or not 5 <= threshold < 100:
        raise ValueError("stop_remaining_percent must be finite and at least 5 and below 100")
    return value


def normalize_policy(raw):
    """Validate a partial Kimi policy object and fill defaults.

    Defaults: ``usage_guard=false``, ``stop_remaining_percent=5``,
    ``probe_on_unknown=false``. The threshold is finite, at least 5 (the
    documented five-hour reserve floor) and below 100. Null (callers pass {}
    for a missing policy), unknown fields or invalid values raise ValueError;
    unknown-field errors describe the allowed shape only, never the raw key
    names, which could hold secrets.
    """
    if raw is None:
        raise ValueError("kimi policy must be an object; pass {} for a missing policy")
    if not isinstance(raw, dict):
        raise ValueError("kimi policy must be an object")
    if any(field not in POLICY_FIELDS for field in raw):
        raise ValueError(
            "kimi policy contains unknown fields; allowed fields: %s"
            % ", ".join(POLICY_FIELDS))
    policy = {
        "usage_guard": DEFAULT_USAGE_GUARD,
        "stop_remaining_percent": DEFAULT_STOP_REMAINING_PERCENT,
        "probe_on_unknown": DEFAULT_PROBE_ON_UNKNOWN,
        "probe_cooldown_minutes": DEFAULT_PROBE_COOLDOWN_MINUTES,
    }
    if "usage_guard" in raw:
        if not isinstance(raw["usage_guard"], bool):
            raise ValueError("usage_guard must be a boolean")
        policy["usage_guard"] = raw["usage_guard"]
    if "stop_remaining_percent" in raw:
        policy["stop_remaining_percent"] = _normalize_threshold(raw["stop_remaining_percent"])
    if "probe_on_unknown" in raw:
        if not isinstance(raw["probe_on_unknown"], bool):
            raise ValueError("probe_on_unknown must be a boolean")
        policy["probe_on_unknown"] = raw["probe_on_unknown"]
    if "probe_cooldown_minutes" in raw:
        value = raw["probe_cooldown_minutes"]
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError("probe_cooldown_minutes must be a positive integer")
        policy["probe_cooldown_minutes"] = value
    return policy


def _is_kimi_coding_origin(base_url):
    """True only for an HTTPS URL on api.kimi.com with a Coding path.

    The origin must be exactly api.kimi.com (default port) and the path
    exactly ``/coding`` or ``/coding/v1``. Look-alike hosts, other paths,
    non-HTTPS schemes and non-default ports never match; usage reads always
    target the fixed endpoint, never the provided URL.
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
    if port is not None and port != 443:
        return False
    path = (parsed.path or "").rstrip("/") or "/"
    return path in CODING_PATHS


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


def _kimi_entries(config):
    """Return {alias: (provider name, provider entry)} for Kimi-family aliases.

    Includes aliases whose provider is not a Coding origin so callers can
    report them as unsupported instead of silently inheriting quota.
    Credential values are never returned.
    """
    models = config.get("models")
    providers = config.get("providers")
    if not isinstance(models, dict) or not isinstance(providers, dict):
        return {}
    entries = {}
    for alias, entry in models.items():
        if not isinstance(alias, str) or not isinstance(entry, dict):
            continue
        family, _ = catalog._cli_profile_for_model(str(entry.get("model") or ""))
        if family != "kimi":
            continue
        provider_name = entry.get("provider")
        if not isinstance(provider_name, str):
            continue
        provider = providers.get(provider_name)
        if not isinstance(provider, dict):
            continue
        entries[alias] = (provider_name, provider)
    return entries


def _kimi_accounts(config_path):
    """Return ({alias: provider name}, {provider name: provider entry}).

    Only Kimi-family aliases whose configured provider has an HTTPS
    api.kimi.com Coding origin are included. Credential values are never
    returned.
    """
    _, config = _load_config(config_path)
    mapping = {}
    kimi_providers = {}
    for alias, (provider_name, provider) in _kimi_entries(config).items():
        if not _is_kimi_coding_origin(provider.get("base_url")):
            continue
        mapping[alias] = provider_name
        kimi_providers[provider_name] = provider
    return mapping, kimi_providers


def aliases(config_path):
    """Set of recognized Kimi aliases whose provider is a Coding origin.

    An unreadable or invalid config yields an empty set; no credentials,
    provider names or base URLs are exposed.
    """
    try:
        mapping, _ = _kimi_accounts(config_path)
    except _Unavailable:
        return set()
    return set(mapping)


def _is_five_hour(duration, time_unit):
    """True only for a provable 300-minute/5-hour/18000-second window."""
    value = _number(duration)
    unit = str(time_unit or "").strip().upper()
    if value is None or not unit:
        return False
    if unit in {"MINUTE", "TIME_UNIT_MINUTE"}:
        return value == 300
    if unit in {"HOUR", "TIME_UNIT_HOUR"}:
        return value == 5
    if unit in {"SECOND", "TIME_UNIT_SECOND"}:
        return value == 18000
    return False


def _reset_at(value):
    """Normalize a reset hint to UTC ISO form, or None."""
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        candidate = text
        if candidate.endswith("Z"):
            candidate = candidate[:-1] + "+00:00"
        try:
            moment = datetime.datetime.fromisoformat(candidate)
        except ValueError:
            return None
        if moment.tzinfo is None:
            return None
        moment = moment.astimezone(datetime.timezone.utc)
        return moment.strftime("%Y-%m-%dT%H:%M:%SZ")
    number = _number(value)
    if number is None or number <= 0:
        return None
    if number > 1e12:  # epoch milliseconds
        number = number / 1000.0
    try:
        moment = datetime.datetime.fromtimestamp(number, tz=datetime.timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _reset_hint(item, detail):
    for source in (detail, item):
        if not isinstance(source, dict):
            continue
        for key in ("reset_at", "resetAt", "reset_time", "resetTime"):
            if key in source:
                hint = _reset_at(source.get(key))
                if hint is not None:
                    return hint
    return None


def _quantity(source, key):
    """Return (number, invalid) for an explicitly present key.

    A missing key yields ``(None, False)`` so the caller can infer the amount
    from the complementary field. Explicit ``None``, booleans, ``NaN``,
    ``Infinity`` and other non-finite values are reported as invalid so the
    row fails closed rather than being treated as absent.
    """
    if not isinstance(source, dict) or key not in source:
        return None, False
    value = source[key]
    if value is None or isinstance(value, bool):
        return None, True
    number = _number(value)
    if number is None:
        return None, True
    return number, False


def parse_usage(payload):
    """Interpret a ``/coding/v1/usages`` payload into safe metrics.

    Returns ``available``, ``used_percent``, ``remaining_percent`` and the
    informational ``reset_at``. Only provable five-hour rows count: a window
    of 300 minutes, 5 hours or 18000 seconds (string numerals accepted), with
    amounts from ``detail.limit`` plus ``used`` or ``remaining``. Numbers must
    be finite, nonnegative and coherent (used + remaining == limit when both
    are present); the conservative reading (lowest remaining) wins across
    rows and across used/remaining within a row. The weekly/top-level
    ``usage`` summary and any weekly/total/token/context figure are never
    substituted for five-hour remaining quota. Missing, malformed or
    out-of-range data yields unknown metrics with a safe error code — never
    zero.
    """
    if not isinstance(payload, dict):
        return _unknown("parse_error")
    limits = payload.get("limits")
    if not isinstance(limits, list):
        return _unknown("no_data")
    best = None
    saw_five_hour = False
    saw_invalid = False
    for item in limits:
        if not isinstance(item, dict):
            saw_invalid = True
            continue
        detail = item.get("detail")
        if not isinstance(detail, dict):
            detail = item
        window = item.get("window")
        if not isinstance(window, dict):
            window = {}
        duration = window.get("duration", item.get("duration", detail.get("duration")))
        time_unit = window.get("timeUnit", item.get("timeUnit", detail.get("timeUnit")))
        if not _is_five_hour(duration, time_unit):
            continue
        saw_five_hour = True
        limit, limit_invalid = _quantity(detail, "limit")
        used, used_invalid = _quantity(detail, "used")
        remaining, remaining_invalid = _quantity(detail, "remaining")
        row_invalid = limit_invalid or used_invalid or remaining_invalid
        if limit is None or limit <= 0:
            if row_invalid:
                saw_invalid = True
            continue
        if used is not None and remaining is not None \
                and abs(used + remaining - limit) > max(1.0, limit * 1e-3):
            saw_invalid = True
            continue
        candidates = []
        if used is not None:
            if 0 <= used <= limit:
                candidates.append(remaining if remaining is not None else limit - used)
            else:
                row_invalid = True
        if remaining is not None:
            if 0 <= remaining <= limit:
                candidates.append(remaining)
            else:
                row_invalid = True
        if row_invalid:
            saw_invalid = True
        if not candidates:
            continue
        if row_invalid:
            continue
        row_remaining = min(candidates)
        row_used = limit - row_remaining
        if best is None or row_remaining < best[2]:
            best = (row_used, limit, row_remaining, _reset_hint(item, item.get("detail")))
    if best is None:
        if saw_five_hour and saw_invalid:
            return _unknown("parse_error")
        return _unknown("no_data")
    used_percent = best[0] / best[1] * 100
    return {
        "available": True,
        "used_percent": used_percent,
        "remaining_percent": best[2] / best[1] * 100,
        "reset_at": best[3],
        "error": None,
    }


def block_reason(usage, policy):
    """Safe block reason for a usage reading under a Kimi policy, or None.

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


def _oauth_file_path(config_path, oauth):
    """Configured file-OAuth credential location.

    Rejects path traversal, non-file storage and malformed keys before any
    file access. The resolved path is inside the CLI's credentials directory
    next to its config home; this reader only ever reads that existing file.
    """
    if not isinstance(oauth, dict):
        raise _Unavailable("credential_malformed")
    if oauth.get("storage", "file") != "file":
        raise _Unavailable("unsupported_credential_storage")
    key = oauth.get("key")
    if not isinstance(key, str) or not key.strip():
        raise _Unavailable("credential_malformed")
    name = key.removeprefix(OAUTH_KEY_PREFIX)
    if not name or name.startswith((".", "/", "\\")) or "/" in name or "\\" in name or ".." in name:
        raise _Unavailable("credential_malformed")
    return Path(config_path).expanduser().parent / "credentials" / (name + ".json")


def _account_probe_identity(config_path, provider):
    """Stable per-account identity for probe allowance and fingerprints.

    OAuth file identity is the credential file path, so access/refresh token
    rotation keeps the same account. API-key identity is the key value itself.
    A missing or unreadable OAuth file falls back to a configured API key, but
    an expired OAuth file is still identifiable. Returns None when the account
    cannot be identified.
    """
    oauth = provider.get("oauth")
    if isinstance(oauth, dict):
        try:
            path = _oauth_file_path(config_path, oauth)
        except _Unavailable:
            return None
        if path.is_file():
            return "oauth:file:%s" % path.resolve()
    key = provider.get("api_key")
    if isinstance(key, str) and key.strip():
        return key.strip()
    env_name = provider.get("api_key_env")
    if isinstance(env_name, str) and env_name:
        value = os.environ.get(env_name)
        if isinstance(value, str) and value:
            return value
    return None


def _account_credential(config_path, provider):
    """Return (credential, cache_identity, probe_identity) for the account.

    The credential is the current access token or API key value used in the
    Authorization header. The cache identity includes the current credential
    value so token rotation invalidates stale usage cache. The probe identity
    is the stable account ref returned by ``_account_probe_identity``. Expired
    or missing credentials raise ``_Unavailable`` with a safe code; the bridge
    never refreshes or rewrites authentication files.
    """
    probe_identity = _account_probe_identity(config_path, provider)
    oauth = provider.get("oauth")
    if isinstance(oauth, dict):
        path = _oauth_file_path(config_path, oauth)
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raw = None
        except OSError:
            raise _Unavailable("credential_missing") from None
        if raw is not None:
            try:
                token = json.loads(raw)
            except ValueError:
                raise _Unavailable("credential_malformed") from None
            if not isinstance(token, dict):
                raise _Unavailable("credential_malformed")
            access = token.get("access_token")
            if not isinstance(access, str) or not access.strip():
                raise _Unavailable("credential_malformed")
            expires_at = _number(token.get("expires_at"))
            if expires_at is None or expires_at <= time.time() + TOKEN_EXPIRY_SKEW_SECONDS:
                raise _Unavailable("credential_expired")
            return access.strip(), "oauth:%s" % access.strip(), probe_identity
    key = provider.get("api_key")
    if isinstance(key, str) and key.strip():
        value = key.strip()
        return value, "api:%s" % value, probe_identity
    env_name = provider.get("api_key_env")
    if isinstance(env_name, str) and env_name:
        value = os.environ.get(env_name)
        if isinstance(value, str) and value:
            return value, "api:%s" % value, probe_identity
    if isinstance(oauth, dict):
        raise _Unavailable("credential_missing")
    return None, None, None


def _account_fingerprint(identity):
    material = json.dumps([_FINGERPRINT_CONTEXT, identity or ""], sort_keys=True)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def account_fingerprint(config_path, alias):
    """Stable fingerprint for the alias's account, or None if unidentifiable.

    Aliases on the same provider entry (and providers presenting the same
    credential material) share the fingerprint; a credential or account
    change yields a new one, invalidating cache and probe allowance. Never
    raises and never exposes credential material.
    """
    try:
        _, config = _load_config(config_path)
        entries = _kimi_entries(config)
        if alias not in entries:
            return None
        provider_name, provider = entries[alias]
        if not _is_kimi_coding_origin(provider.get("base_url")):
            return None
        identity = _account_probe_identity(config_path, provider)
        if identity is None:
            return None
        return _account_fingerprint(identity)
    except _Unavailable:
        return None


def _fetch_usage_payload(credential):
    """GET the fixed HTTPS usages endpoint; bounded, redirects rejected.

    Uses the credential only in the Authorization header. Raises _Unavailable
    with a safe code on any network, redirect, size or parse failure; response
    bodies never travel with the error.
    """
    connection = http.client.HTTPSConnection(PROVIDER_HOST,
                                             timeout=REQUEST_TIMEOUT_SECONDS)
    try:
        connection.request("GET", USAGE_PATH, headers={
            "Authorization": "Bearer %s" % credential,
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


def _observed(metrics, observed_at):
    result = dict(metrics)
    result["observed_at"] = observed_at
    return result


def _read_account(credential, observed_at):
    try:
        payload = _fetch_usage_payload(credential)
        metrics = parse_usage(payload)
    except _Unavailable as error:
        return _observed(_unknown(error.code), observed_at)
    except Exception:
        return _observed(_unknown("unknown_error"), observed_at)
    return _observed(metrics, observed_at)


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


def _save_json(state_dir, name, payload):
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = state_dir / (name + ".tmp")
        fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, sort_keys=True)
        except ValueError:
            return False
        os.replace(str(tmp_path), str(state_dir / name))
        return True
    except OSError:
        return False


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
    _save_json(state_dir, _CACHE_NAME, {"accounts": pruned})


def _with_single_flight(state_dir, lock_name, perform, busy, require_lock=False):
    """Run perform() holding a cross-process exclusive lock when possible.

    Waits a bounded interval for a concurrent holder; on persistent
    contention returns busy() instead of stampeding. A missing or unwritable
    state dir degrades to an unlocked direct call.
    """
    if state_dir is None:
        return perform()
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(state_dir / lock_name), os.O_RDWR | os.O_CREAT, 0o600)
    except OSError:
        return busy() if require_lock else perform()
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except OSError:
            if time.monotonic() >= deadline:
                os.close(fd)
                return busy()
            time.sleep(LOCK_POLL_SECONDS)
    try:
        return perform()
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def read_usage(state_dir, config_path, alias=None, *, force_refresh=False):
    """Read sanitized Kimi Code five-hour usage metrics, or unavailable ones.

    With ``alias`` the reading covers that alias's actual existing account;
    a Kimi-family alias on a non-Coding provider is ``unsupported_provider``
    and a non-Kimi alias is ``alias_not_kimi``. Without one it takes the most
    conservative reading across configured Coding-origin accounts; any
    unavailable account makes the whole reading unknown. Sanitized metrics
    are cached for at most 30 seconds under a credential-fingerprint key
    (credential or account changes invalidate the cache) and reads are
    single-flight across processes. ``force_refresh`` bypasses cached readings
    after a bounded recovery probe, while retaining the same account locking.
    Expected config, credential, network and
    parse failures return unavailable metrics with a safe error code — they
    never raise and never leak credentials or raw provider data.
    """
    state_dir = Path(state_dir) if state_dir is not None else None
    try:
        path, config = _load_config(config_path)
    except _Unavailable as error:
        return _observed(_unknown(error.code), time.time())
    entries = _kimi_entries(config)
    providers = {}
    accounts = []
    try:
        if alias is not None:
            if alias not in entries:
                raise _Unavailable("alias_not_kimi")
            provider_name, provider = entries[alias]
            if not _is_kimi_coding_origin(provider.get("base_url")):
                raise _Unavailable("unsupported_provider")
            names = [provider_name]
            providers[provider_name] = provider
        else:
            for name, (provider_name, provider) in entries.items():
                if _is_kimi_coding_origin(provider.get("base_url")):
                    providers[provider_name] = provider
            names = sorted(providers)
            if not names:
                raise _Unavailable("no_kimi_account")
        for name in names:
            credential, cache_identity, probe_identity = _account_credential(path, providers[name])
            if credential is None:
                raise _Unavailable("credential_missing")
            accounts.append((name, credential, cache_identity, probe_identity))
    except _Unavailable as error:
        return _observed(_unknown(error.code), time.time())

    def perform():
        now = time.time()
        cache = _load_cache(state_dir) if state_dir is not None else {}
        results = {}
        stale = []
        for name, credential, cache_identity, probe_identity in accounts:
            fingerprint = _account_fingerprint(cache_identity)
            entry = None if force_refresh else _fresh_entry(cache.get(fingerprint), now)
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

    return _with_single_flight(state_dir, _LOCK_NAME, perform,
                               lambda: _observed(_unknown("busy"), time.time()))


def _combine(entries):
    if not entries:
        return _observed(_unknown("no_data"), time.time())
    for entry in entries:
        if entry.get("available") is not True:
            code = entry.get("error")
            return _observed(_unknown(code if isinstance(code, str) else "unknown_error"),
                             entry.get("observed_at", time.time()))
    remaining_percent = min(_number(entry.get("remaining_percent")) for entry in entries)
    used_percent = 100 - remaining_percent
    binding = max(entries, key=lambda entry: _number(entry.get("observed_at")) or 0)
    return {
        "available": True,
        "used_percent": used_percent,
        "remaining_percent": remaining_percent,
        "reset_at": binding.get("reset_at"),
        "error": None,
        "observed_at": min(_number(entry.get("observed_at")) or time.time()
                           for entry in entries),
    }


def _load_probe_allowances(state_dir):
    try:
        raw = (state_dir / _PROBE_NAME).read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    entries = data.get("accounts")
    if not isinstance(entries, dict):
        return {}
    clean = {}
    for fp, entry in entries.items():
        if not isinstance(entry, dict):
            continue
        claimed_at = _number(entry.get("claimed_at"))
        if claimed_at is None or claimed_at <= 0:
            continue
        clean[str(fp)] = {"claimed_at": claimed_at}
    return clean


def _save_probe_allowances(state_dir, entries, now):
    # Keep timestamps: a later policy increase must apply to previous claims.
    return _save_json(state_dir, _PROBE_NAME, {"accounts": entries})


def claim_probe(state_dir, fingerprint, probe_cooldown_minutes=DEFAULT_PROBE_COOLDOWN_MINUTES):
    """Atomically claim the account's single bootstrap probe allowance.

    Durable and single-flight across processes and server restarts: the
    first claimant per account fingerprint wins; later claims within the
    configured cooldown are refused. Returns ``{"claimed": bool,
    "cooldown_until": float|None, "busy": bool}``; a missing state dir or an
    invalid fingerprint refuses the claim rather than allowing a probe.
    """
    cooldown_seconds = normalize_policy({"probe_cooldown_minutes": probe_cooldown_minutes})["probe_cooldown_minutes"] * 60
    if not isinstance(fingerprint, str) or not fingerprint:
        return {"claimed": False, "cooldown_until": None, "busy": False}
    state_dir = Path(state_dir) if state_dir is not None else None
    if state_dir is None:
        return {"claimed": False, "cooldown_until": None, "busy": False}

    def perform():
        now = time.time()
        entries = _load_probe_allowances(state_dir)
        entry = entries.get(fingerprint)
        if entry is not None and now - entry["claimed_at"] < cooldown_seconds:
            return {"claimed": False,
                    "cooldown_until": entry["claimed_at"] + cooldown_seconds,
                    "busy": False}
        entries[fingerprint] = {"claimed_at": now}
        if not _save_probe_allowances(state_dir, entries, now):
            # A claim that cannot be persisted would let a restart multiply
            # probes; refuse it instead.
            return {"claimed": False, "cooldown_until": None, "busy": False}
        return {"claimed": True, "cooldown_until": now + cooldown_seconds,
                "busy": False}

    return _with_single_flight(state_dir, _PROBE_LOCK_NAME, perform,
                               lambda: {"claimed": False, "cooldown_until": None,
                                        "busy": True}, require_lock=True)
