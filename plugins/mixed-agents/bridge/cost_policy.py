"""Off-peak cost preference policy (docs/COST_POLICY_SPEC.md).

Pure standard-library parsing, validation and period evaluation for the
optional exact-alias off-peak preference. The evaluation clock stays
inside the runtime: no MCP request may forge the pricing time, and only
this module's ``evaluate`` helper accepts an aware datetime, exclusively
for deterministic offline tests. Nothing here probes accounts, prices or
model availability; alias availability is the runtime's concern.

A disabled policy (the portable default) never touches zoneinfo data; an
enabled policy requires it and never falls back to a guessed timezone.
"""
from __future__ import annotations

from datetime import datetime, timezone
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_MODEL = "zai-coding-plan/glm-5.3"
DEFAULT_TIMEZONE = "Asia/Shanghai"
DEFAULT_PEAK_START = "14:00"
DEFAULT_PEAK_END = "18:00"
DEFAULT_PEAK_WEEKDAYS = (1, 2, 3, 4, 5)

DEFAULTS = {
    "enabled": False,
    "model": DEFAULT_MODEL,
    "timezone": DEFAULT_TIMEZONE,
    "peak_start": DEFAULT_PEAK_START,
    "peak_end": DEFAULT_PEAK_END,
    "peak_weekdays": list(DEFAULT_PEAK_WEEKDAYS),
}

PERIODS = ("disabled", "peak", "off_peak")
KEYS = ("enabled", "model", "timezone", "peak_start", "peak_end", "peak_weekdays")

_TIME = re.compile(r"\A([0-9]{2}):([0-9]{2})\Z")
_ZONE_ERRORS = (ZoneInfoNotFoundError, ValueError, OSError, TypeError)


class CostPolicyError(ValueError):
    """Invalid policy argument or stored policy object.

    Messages describe the expected shape only; they never echo raw stored
    settings or secrets.
    """


def _minutes(value, name):
    if not isinstance(value, str):
        raise CostPolicyError(f"{name} must be a strict HH:MM time")
    match = _TIME.match(value)
    if not match or int(match[1]) > 23 or int(match[2]) > 59:
        raise CostPolicyError(f"{name} must be a strict HH:MM time")
    return int(match[1]) * 60 + int(match[2])


def _weekdays(value):
    if not isinstance(value, (list, tuple)) or not value:
        raise CostPolicyError("peak_weekdays must be a nonempty list of unique ISO weekdays 1-7")
    for day in value:
        if isinstance(day, bool) or not isinstance(day, int) or not 1 <= day <= 7:
            raise CostPolicyError("peak_weekdays must be a nonempty list of unique ISO weekdays 1-7")
    if len(set(value)) != len(value):
        raise CostPolicyError("peak_weekdays must be a nonempty list of unique ISO weekdays 1-7")
    return list(value)


def validate_timezone(name):
    """Require a resolvable IANA timezone name; never guess the host zone."""
    if not isinstance(name, str) or not name.strip():
        raise CostPolicyError("timezone must be an IANA timezone name")
    try:
        ZoneInfo(name)
    except _ZONE_ERRORS as exc:
        raise CostPolicyError("timezone must be an IANA timezone name available on this system") from exc
    return name


def normalize(raw):
    """Validate one policy object and return it with defaults filled in.

    Accepts partial objects (missing fields default). A disabled policy
    needs no timezone database; an enabled one is fully validated here so
    malformed stored objects fail closed before any selection runs.
    """
    if not isinstance(raw, dict):
        raise CostPolicyError("cost_policy must be an object")
    unknown = set(raw) - set(KEYS)
    if unknown:
        raise CostPolicyError("cost_policy contains unknown fields")
    if any(value is None for value in raw.values()):
        raise CostPolicyError("cost_policy contains null field values")
    merged = {**DEFAULTS, **raw}
    if not isinstance(merged["enabled"], bool):
        raise CostPolicyError("enabled must be a boolean")
    if not isinstance(merged["model"], str) or not merged["model"].strip():
        raise CostPolicyError("model must be a nonempty string")
    if not isinstance(merged["timezone"], str) or not merged["timezone"].strip():
        raise CostPolicyError("timezone must be an IANA timezone name")
    start = _minutes(merged["peak_start"], "peak_start")
    end = _minutes(merged["peak_end"], "peak_end")
    if start >= end:
        raise CostPolicyError("peak_start must be strictly before peak_end in a same-day window")
    merged["peak_weekdays"] = _weekdays(merged["peak_weekdays"])
    if merged["enabled"]:
        validate_timezone(merged["timezone"])
    return {key: merged[key] for key in KEYS}


def update(stored, enabled, model=None, timezone=None, peak_start=None, peak_end=None, peak_weekdays=None):
    """Apply one setter call to a stored (or absent) policy.

    ``enabled`` is required and strictly boolean; omitted/None arguments
    retain the stored value or the default. Every provided argument is
    validated, and the resulting timezone must resolve whenever a
    timezone was provided or the result is enabled.
    """
    if not isinstance(enabled, bool):
        raise CostPolicyError("enabled must be a boolean")
    merged = dict(normalize(stored)) if stored is not None else dict(DEFAULTS)
    if model is not None:
        merged["model"] = model
    if timezone is not None:
        merged["timezone"] = timezone
    if peak_start is not None:
        merged["peak_start"] = peak_start
    if peak_end is not None:
        merged["peak_end"] = peak_end
    if peak_weekdays is not None:
        merged["peak_weekdays"] = peak_weekdays
    merged["enabled"] = enabled
    policy = normalize(merged)
    if timezone is not None or policy["enabled"]:
        validate_timezone(policy["timezone"])
    return policy


def evaluate(policy, moment=None):
    """Return the policy period: "disabled", "peak" or "off_peak".

    ``moment`` must be an aware datetime and exists only for
    deterministic offline tests; ``None`` uses the current UTC clock.
    Evaluation happens in the policy's own timezone (inclusive start,
    exclusive end, ISO weekdays), regardless of the host timezone or DST
    elsewhere. Disabled policies never construct a ZoneInfo.
    """
    policy = normalize(policy)
    if not policy["enabled"]:
        return "disabled"
    if moment is None:
        moment = datetime.now(timezone.utc)
    elif not isinstance(moment, datetime) or moment.tzinfo is None or moment.tzinfo.utcoffset(moment) is None:
        raise CostPolicyError("evaluation timestamp must be an aware datetime")
    try:
        local = moment.astimezone(ZoneInfo(policy["timezone"]))
    except _ZONE_ERRORS as exc:
        raise CostPolicyError("timezone data is required to evaluate an enabled policy") from exc
    minutes = local.hour * 60 + local.minute
    if (local.isoweekday() in policy["peak_weekdays"]
            and _minutes(policy["peak_start"], "peak_start") <= minutes
            < _minutes(policy["peak_end"], "peak_end")):
        return "peak"
    return "off_peak"


def describe(policy, period, model_available):
    """Public policy view for model_catalog, select_model and the setter."""
    policy = normalize(policy)
    if period not in PERIODS:
        raise CostPolicyError("period must be one of disabled, peak, off_peak")
    return {"enabled": policy["enabled"], "model": policy["model"], "timezone": policy["timezone"],
            "peak_start": policy["peak_start"], "peak_end": policy["peak_end"],
            "peak_weekdays": list(policy["peak_weekdays"]), "period": period,
            "model_available": bool(model_available)}
