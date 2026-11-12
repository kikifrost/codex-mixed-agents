"""Model capability profiles and live CLI configuration parsing.

Profiles are documented routing heuristics (capability notes), not benchmark
claims. Live configured metadata from the user's CLI config (context size,
capabilities, display name) always wins over the static hints below.

Ranking uses three fit tiers so equal-fit families can be rotated fairly by
the runtime instead of preferring one provider:

- "routine": k2.8-preview / kimi-for-coding and Sol rank
  equally — everyday implementation/review/tests work.
- "deep": k3.1-pro / k3 / k3-256k, glm-5.3 and Astra rank equally — complex
  reasoning, deep debugging, long-context work.
- "short": k2.7 / kimi-for-coding-highspeed, GLM 5.3 Flash
  variants and Luna rank equally — latency sensitive, short, bounded tasks.

The runtime routing catalog considers only GLM 5.3 and 5.3 Flash. Legacy GLM
profiles below remain for explicit-alias validation and quota/cap accounting.

Unknown native (GPT) names remain available with neutral fit; unknown
configured aliases (DeepSeek, invented model names, ...) are excluded
entirely. No provider configuration or credential detail ever leaves this
module.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

ROLES = ("implementation", "tests", "review", "exploration", "research")
COMPLEXITIES = ("simple", "standard", "complex")
FAMILIES = ("kimi", "glm", "gpt")

# The CLI transport backend (detached kimi/zcode-style CLI workers) versus
# the host-managed native backend. The ID prefix identifies the backend, not
# the model family: both Kimi and GLM ride the CLI backend.
CLI_BACKEND = "kimi_cli"
NATIVE_BACKEND = "codex_native"

MANAGED_PROVIDER = "managed:kimi-code"

KIMI_PROFILES = {
    "k3": {
        "display_name": "Kimi K3",
        "default_context": 1_000_000,
        "default_capabilities": ["thinking", "tool_use"],
        "tier": "deep",
        "role_fit": {"implementation": 3, "tests": 3, "review": 3,
                     "exploration": 2, "research": 3},
        "complexity_fit": {"simple": 1, "standard": 2, "complex": 3},
        "latency_fit": 1,
        "notes": "Complex reasoning, deep debugging, cross-module and "
                 "long-context coding. 1M context only when the plan entitles "
                 "it; prefer k3-256k when large context is not needed.",
    },
    "k3-256k": {
        "display_name": "Kimi K3 (256k)",
        "default_context": 262_144,
        "default_capabilities": ["thinking", "tool_use"],
        "tier": "deep",
        "role_fit": {"implementation": 3, "tests": 3, "review": 3,
                     "exploration": 2, "research": 3},
        "complexity_fit": {"simple": 1, "standard": 2, "complex": 3},
        "latency_fit": 1,
        "notes": "Same K3 within 256k; ordinary quota cost is lower than "
                 "full-context K3.",
    },
    "k3.1-pro": {
        "display_name": "Kimi K3.1 Pro",
        "default_context": 1_048_576,
        "default_capabilities": ["thinking", "always_thinking", "tool_use",
                                 "image_in", "video_in"],
        "tier": "deep",
        "role_fit": {"implementation": 3, "tests": 3, "review": 3,
                     "exploration": 2, "research": 3},
        "complexity_fit": {"simple": 1, "standard": 2, "complex": 3},
        "latency_fit": 1,
        "notes": "Complex reasoning, coding and long-horizon agent work. "
                 "Up to 1M context subject to plan access; thinking required. "
                 "Start a fresh session when moving from older models.",
    },
    "k3.1-pro-highspeed": {
        "display_name": "Kimi K3.1 Pro Highspeed",
        "default_context": 1_048_576,
        "default_capabilities": ["thinking", "always_thinking", "tool_use",
                                 "image_in", "video_in"],
        "tier": "deep",
        "role_fit": {"implementation": 3, "tests": 3, "review": 3,
                     "exploration": 2, "research": 3},
        "complexity_fit": {"simple": 1, "standard": 2, "complex": 3},
        "latency_fit": 3,
        "notes": "Same capability as K3.1 Pro with approximately 2–3x "
                 "output speed and twice its quota usage. Up to 1M context "
                 "subject to plan access; thinking required. Start a fresh "
                 "session when moving from older models.",
    },
    "k2.7": {
        "display_name": "Kimi K2.7",
        "default_context": 262_144,
        "default_capabilities": ["thinking", "tool_use"],
        "tier": "short",
        "role_fit": {"implementation": 2, "tests": 2, "review": 1,
                     "exploration": 3, "research": 1},
        "complexity_fit": {"simple": 3, "standard": 2, "complex": 1},
        "latency_fit": 3,
        "notes": "Regular K2.7 model for everyday coding, completion and "
                 "review work.",
    },
    "k2.8-preview": {
        "display_name": "Kimi K2.8 Preview",
        "default_context": 1_048_576,
        "default_capabilities": ["thinking", "tool_use"],
        "tier": "routine",
        "role_fit": {"implementation": 3, "tests": 3, "review": 3,
                     "exploration": 2, "research": 1},
        "complexity_fit": {"simple": 3, "standard": 3, "complex": 2},
        "latency_fit": 2,
        "notes": "Regular K2.8 Preview model for everyday coding, completion "
                 "and review work.",
    },
    "kimi-for-coding": {
        "display_name": "Kimi for Coding (K2.8 Preview)",
        "default_context": 1_048_576,
        "default_capabilities": ["thinking", "tool_use"],
        "tier": "routine",
        "role_fit": {"implementation": 3, "tests": 3, "review": 3,
                     "exploration": 2, "research": 1},
        "complexity_fit": {"simple": 3, "standard": 3, "complex": 2},
        "latency_fit": 2,
        "notes": "Efficient regular development and code completion; "
                 "everyday implementation/review work. Alias can change "
                 "model version over time.",
    },
    "kimi-for-coding-highspeed": {
        "display_name": "Kimi for Coding HighSpeed (K2.7)",
        "default_context": 262_144,
        "default_capabilities": ["thinking", "tool_use"],
        "tier": "short",
        "role_fit": {"implementation": 2, "tests": 2, "review": 1,
                     "exploration": 3, "research": 1},
        "complexity_fit": {"simple": 3, "standard": 2, "complex": 1},
        "latency_fit": 3,
        "notes": "Latency-sensitive short tasks; faster generation consumes "
                 "more quota, not automatically cheapest.",
    },
}

GLM_PROFILES = {
    "glm-5.3": {
        "display_name": "GLM 5.3",
        "default_context": 1_000_000,  # documented 1M context
        "default_capabilities": ["thinking", "tool_use"],
        "tier": "deep",
        "role_fit": {"implementation": 3, "tests": 3, "review": 3,
                     "exploration": 2, "research": 3},
        "complexity_fit": {"simple": 1, "standard": 2, "complex": 3},
        "latency_fit": 1,
        "notes": "Flagship GLM 5.3; documented 1M context for cross-module "
                 "and long-context work. Capability and access remain "
                 "unverified until observed.",
    },
    "glm-5": {
        "display_name": "GLM 5",
        "default_context": None,  # not documented; configured value wins
        "default_capabilities": ["thinking", "tool_use"],
        "tier": "deep",
        "role_fit": {"implementation": 3, "tests": 3, "review": 3,
                     "exploration": 2, "research": 3},
        "complexity_fit": {"simple": 1, "standard": 2, "complex": 3},
        "latency_fit": 1,
        "notes": "Flagship GLM 5 generation for complex reasoning and deep "
                 "debugging; context size unverified until configured or "
                 "observed.",
    },
    "glm-5.2": {
        "display_name": "GLM 5.2",
        "default_context": None,
        "default_capabilities": ["thinking", "tool_use"],
        "tier": "routine",
        "role_fit": {"implementation": 3, "tests": 3, "review": 3,
                     "exploration": 2, "research": 1},
        "complexity_fit": {"simple": 3, "standard": 3, "complex": 2},
        "latency_fit": 2,
        "notes": "Regular GLM 5.2 development and code completion workhorse.",
    },
    "glm-4.7": {
        "display_name": "GLM 4.7",
        "default_context": 200_000,  # documented 200K context, text modality
        "default_capabilities": ["thinking", "tool_use"],
        "tier": "routine",
        "role_fit": {"implementation": 3, "tests": 3, "review": 3,
                     "exploration": 2, "research": 1},
        "complexity_fit": {"simple": 3, "standard": 3, "complex": 2},
        "latency_fit": 2,
        "notes": "GLM 4.7 series; documented 200K context and text "
                 "input/output. Everyday coding and review work.",
    },
    "glm-5-turbo": {
        "display_name": "GLM 5 Turbo",
        "default_context": None,
        "default_capabilities": ["thinking", "tool_use"],
        "tier": "short",
        "role_fit": {"implementation": 2, "tests": 2, "review": 1,
                     "exploration": 3, "research": 1},
        "complexity_fit": {"simple": 3, "standard": 2, "complex": 1},
        "latency_fit": 3,
        "notes": "Latency-sensitive short and bounded tasks.",
    },
    "glm-5.2-highspeed": {
        "display_name": "GLM 5.2 HighSpeed",
        "default_context": None,
        "default_capabilities": ["thinking", "tool_use"],
        "tier": "short",
        "role_fit": {"implementation": 2, "tests": 2, "review": 1,
                     "exploration": 3, "research": 1},
        "complexity_fit": {"simple": 3, "standard": 2, "complex": 1},
        "latency_fit": 3,
        "notes": "Latency-sensitive short and bounded tasks.",
    },
    "glm-5.3-flash": {
        "display_name": "GLM 5.3 Flash",
        "default_context": None,
        "default_capabilities": ["thinking", "tool_use"],
        "tier": "short",
        "role_fit": {"implementation": 2, "tests": 2, "review": 1,
                     "exploration": 3, "research": 1},
        "complexity_fit": {"simple": 3, "standard": 2, "complex": 1},
        "latency_fit": 3,
        "notes": "Small, fast GLM 5.3 variant for short bounded tasks.",
    },
    "glm-5.3-highspeed": {
        "display_name": "GLM 5.3 HighSpeed",
        "default_context": None,
        "default_capabilities": ["thinking", "tool_use"],
        "tier": "short",
        "role_fit": {"implementation": 2, "tests": 2, "review": 1,
                     "exploration": 3, "research": 1},
        "complexity_fit": {"simple": 3, "standard": 2, "complex": 1},
        "latency_fit": 3,
        "notes": "Latency-sensitive short and bounded tasks.",
    },
}

# Combined CLI (non-native) profile table, keyed by canonical profile name.
# Kimi and GLM keys are disjoint, so a single lookup serves both families.
CLI_PROFILES = {**KIMI_PROFILES, **GLM_PROFILES}

GPT_PROFILES = {
    "sol": {
        "display_name": "Sol",
        "context": None,  # unknown unless the parent supplies one
        "tier": "routine",
        "role_fit": {"implementation": 3, "tests": 3, "review": 3,
                     "exploration": 2, "research": 1},
        "complexity_fit": {"simple": 3, "standard": 3, "complex": 2},
        "latency_fit": 2,
        "notes": "Everyday implementation/review.",
    },
    "astra": {
        "display_name": "Astra",
        "context": None,  # unknown unless the parent supplies one
        "tier": "deep",
        "role_fit": {"implementation": 3, "tests": 3, "review": 3,
                     "exploration": 2, "research": 3},
        "complexity_fit": {"simple": 1, "standard": 2, "complex": 3},
        "latency_fit": 1,
        "notes": "Demanding reasoning.",
    },
    "luna": {
        "display_name": "Luna",
        "context": None,  # unknown unless the parent supplies one
        "tier": "short",
        "role_fit": {"implementation": 2, "tests": 2, "review": 1,
                     "exploration": 3, "research": 1},
        "complexity_fit": {"simple": 3, "standard": 2, "complex": 1},
        "latency_fit": 3,
        "notes": "Short bounded work.",
    },
}

NEUTRAL_GPT_NOTES = ("Unknown native model; neutral fit until the parent "
                     "supplies more detail. Availability is not assumed.")

# Prefer the managed kimi-code transport when the same remote model is
# configured under several providers (transport preference only).
_PROVIDER_PREFERENCE = ("managed:kimi-code",)

# Exact final-component aliases for provider-qualified remote identifiers.
# Unknown suffixes (e.g. "kimi-k3-1") are deliberately absent.
_KIMI_MODEL_ALIASES = {
    # K3.1 Coding Plan and public API IDs
    "k3.1-pro": "k3.1-pro",
    "kimi-k3.1-pro": "k3.1-pro",
    "k3.1-pro-highspeed": "k3.1-pro-highspeed",
    "kimi-k3.1-pro-highspeed": "k3.1-pro-highspeed",
    # K3 family
    "k3": "k3",
    "kimi-k3": "k3",
    "k3-256k": "k3-256k",
    "kimi-k3-256k": "k3-256k",
    # K2.7 regular
    "k2.7": "k2.7",
    "kimi-k2.7": "k2.7",
    # K2.8 Preview
    "k2.8-preview": "k2.8-preview",
    "kimi-k2.8-preview": "k2.8-preview",
    # Coding aliases preserve their existing profile keys
    "kimi-for-coding": "kimi-for-coding",
    "kimi-for-coding-highspeed": "kimi-for-coding-highspeed",
}

# Exact final-component aliases for the supported GLM remote identifiers.
# Deliberately limited to the documented eight: no invented suffixes (e.g.
# "glm-5.3-vision") and no GLM-4.7 sub-variants. A "-flash"/"-highspeed"/
# "-turbo" suffix marks a latency tier, never an image/video capability.
_GLM_MODEL_ALIASES = {
    "glm-4.7": "glm-4.7",
    "glm-5": "glm-5",
    "glm-5-turbo": "glm-5-turbo",
    "glm-5.2": "glm-5.2",
    "glm-5.2-highspeed": "glm-5.2-highspeed",
    "glm-5.3": "glm-5.3",
    "glm-5.3-flash": "glm-5.3-flash",
    "glm-5.3-highspeed": "glm-5.3-highspeed",
}


def default_config_path() -> Path:
    home = os.environ.get("KIMI_CODE_HOME")
    if home:
        return Path(home).expanduser() / "config.toml"
    return Path.home() / ".kimi-code" / "config.toml"


def _cli_profile_for_model(remote_model: str):
    """Return (family, canonical profile key) for a remote model identifier.

    Provider-qualified names such as ``managed:kimi-code/k3`` or
    ``zai/glm-5.3`` are matched by their final slash-delimited component.
    Only exact known aliases are recognized; arbitrary suffixes like
    ``kimi-k3-1`` or ``glm-5.3-vision`` are not mapped. Returns (None, None)
    for unknown, DeepSeek and unrelated provider models.
    """
    name = (remote_model or "").strip().lower()
    name = name.rstrip("/").split("/")[-1]
    key = _KIMI_MODEL_ALIASES.get(name)
    if key is not None:
        return "kimi", key
    key = _GLM_MODEL_ALIASES.get(name)
    if key is not None:
        return "glm", key
    return None, None


def _profile_key_for_model(remote_model: str):
    """Return the canonical Kimi profile key for a remote model identifier.

    Kept for backward compatibility; returns None for non-Kimi models.
    """
    family, key = _cli_profile_for_model(remote_model)
    return key if family == "kimi" else None


def _sanitize_context(value, default: int):
    """Return a positive integer context, ``default`` if absent, or None.

    Malformed values (including booleans) are dropped silently so the raw
    config entry is never leaked through an error message.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return None
    try:
        context = int(value)
        if context <= 0:
            return None
        return context
    except (ValueError, TypeError):
        return None


def _sanitize_capabilities(value, default: list):
    """Return a sorted list of capability strings, ``default`` if absent.

    Dicts, bare strings and non-string elements are treated as malformed and
    dropped rather than serialized, so no raw config value leaks out.
    """
    if value is None:
        return default
    if isinstance(value, (str, dict)):
        return None
    try:
        caps = list(value)
        if not caps:
            return default
        if not all(isinstance(c, str) for c in caps):
            return None
        return sorted(caps)
    except (TypeError, ValueError):
        return None


def load_kimi_models(config_path=None, *, all_aliases=False) -> list:
    """Configured CLI aliases (Kimi and GLM), sanitized.

    Backward-compatible loader entry point, now covering all supported CLI
    models. By default returns one routing candidate per remote CLI model,
    preferring managed kimi-code aliases. With all_aliases=True preserves
    every configured alias so explicit selection never substitutes another
    credential source. Unknown models (DeepSeek, invented names, unrelated
    providers) are ignored. Provider names, base URLs and credentials are
    never included in the result.
    """
    path = Path(config_path).expanduser() if config_path else default_config_path()
    config = {}
    try:
        with open(path, "rb") as fh:
            config = tomllib.load(fh)
    except (FileNotFoundError, tomllib.TOMLDecodeError, OSError):
        return []

    models = config.get("models")
    if not isinstance(models, dict):
        return []

    best: dict = {}
    aliases = []
    for alias, entry in models.items():
        if not isinstance(entry, dict) or not isinstance(alias, str):
            continue
        remote = str(entry.get("model") or "")
        family, key = _cli_profile_for_model(remote)
        if key is None:
            continue  # unknown / non-CLI alias
        provider = str(entry.get("provider") or "")
        managed = provider == MANAGED_PROVIDER
        rank = (_PROVIDER_PREFERENCE.index(provider)
                if provider in _PROVIDER_PREFERENCE else len(_PROVIDER_PREFERENCE))
        profile = CLI_PROFILES[key]
        context = _sanitize_context(entry.get("max_context_size"),
                                    profile["default_context"])
        capabilities = _sanitize_capabilities(entry.get("capabilities"),
                                              profile["default_capabilities"])
        # context None is only an error when a live value was configured;
        # an absent default simply means the capacity is unverified.
        if (context is None and entry.get("max_context_size") is not None
                or capabilities is None):
            continue  # malformed context/capabilities entry; skip silently
        candidate = {
            "alias": alias,
            "model": remote,
            "family": family,
            "backend": CLI_BACKEND,
            "display_name": entry.get("display_name") or profile["display_name"],
            "context": context,
            "capabilities": capabilities,
            "profile": key,
            "managed": managed,
            "notes": profile["notes"],
            "_rank": rank,
        }
        aliases.append(candidate)
        current = best.get(key)
        if current is None or (current["managed"], -current["_rank"]) < (managed, -rank):
            best[key] = candidate

    if all_aliases:
        return [dict({k: v for k, v in entry.items() if k != "_rank"},
                     routing_default=best[entry["profile"]]["alias"] == entry["alias"])
                for entry in aliases]

    result = []
    for key in CLI_PROFILES:  # stable, documented order
        entry = best.get(key)
        if entry is not None:
            entry = dict(entry)
            entry.pop("_rank", None)
            result.append(entry)
    return result


def _gpt_profile_key(name: str):
    """Match a caller-supplied native name to a profile by suffix.

    Native model ids evolve over time (e.g. "gpt-6.1-sol", "gpt-6-astra",
    "gpt-6-luna"); the documented roles attach to the trailing tier token,
    not to a specific generation string. Returns the profile key or None.
    """
    key = name.strip().lower()
    for suffix in GPT_PROFILES:
        if key == suffix or key.endswith("-" + suffix):
            return suffix
    return None


def native_model(name: str, context=None) -> dict:
    """Profile for a native (GPT) model name supplied by the caller.

    Known tier names (Sol/Astra/Luna, matched by suffix) get their documented
    fit; unknown names stay available with neutral fit. Context is unknown
    (None) unless the caller explicitly supplies it — nothing is assumed
    available and no context is invented.
    """
    if not isinstance(name, str) or not name.strip():
        raise ValueError("native model name must be a nonempty string")
    if context is not None:
        context = int(context)
        if context <= 0:
            raise ValueError("context must be a positive integer or None")
    key = _gpt_profile_key(name)
    if key is None:
        return {
            "alias": name,
            "model": name,
            "family": "gpt",
            "backend": NATIVE_BACKEND,
            "display_name": name,
            "context": context,
            "capabilities": [],
            "profile": "unknown",
            "managed": False,
            "notes": NEUTRAL_GPT_NOTES,
            "neutral": True,
            "_fit": {"role_fit": {r: 2 for r in ROLES},
                     "complexity_fit": {c: 2 for c in COMPLEXITIES},
                     "latency_fit": 2},
        }
    profile = GPT_PROFILES[key]
    return {
        "alias": name,
        "model": name,
        "family": "gpt",
        "backend": NATIVE_BACKEND,
        "display_name": profile["display_name"],
        "context": context if context is not None else profile["context"],
        "capabilities": [],
        "profile": key,
        "managed": False,
        "notes": profile["notes"],
        "neutral": False,
        "_fit": {k: profile[k] for k in ("role_fit", "complexity_fit",
                                         "latency_fit")},
    }


def _fit_for(entry: dict):
    if entry["family"] == "gpt":
        return entry.get("_fit")
    return CLI_PROFILES[entry["profile"]]


def _score(entry: dict, role: str, complexity: str,
           context_tokens: int, latency_sensitive: bool):
    fit = _fit_for(entry)
    context = entry["context"]
    # Proven context overflow only excludes a model when its context is
    # actually known; None means "unknown", not "too small".
    if context_tokens and context is not None and context_tokens > context:
        return None
    latency_fit = fit["latency_fit"]
    # The faster Pro variant spends about twice the quota. Its speed should
    # improve selection only when the task actually asks for lower latency.
    if entry["profile"] == "k3.1-pro-highspeed" and not latency_sensitive:
        latency_fit = KIMI_PROFILES["k3.1-pro"]["latency_fit"]
    score = (10 * fit["role_fit"].get(role, 2)
             + 6 * fit["complexity_fit"].get(complexity, 2)
             + (4 if latency_sensitive else 2) * latency_fit)
    return score


def rank_models(models, role, complexity, context_tokens=0,
                latency_sensitive=False, excluded_family=None):
    """Score candidates for a task. Returns a list of dicts:

    {"model": <entry>, "score": int|None, "feasible": bool, "rationale": str}

    sorted best first. Entries whose family is excluded are dropped; entries
    whose configured context cannot hold ``context_tokens`` are kept with
    ``feasible: False`` and ``score: None`` so callers can explain the skip.
    Equal-fit families keep their input order — the caller rotates ties.
    """
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r}; expected one of {ROLES}")
    if complexity not in COMPLEXITIES:
        raise ValueError(
            f"unknown complexity {complexity!r}; expected one of {COMPLEXITIES}")
    if excluded_family is not None and excluded_family not in FAMILIES:
        raise ValueError(f"unknown family {excluded_family!r}")

    ranked = []
    for entry in models:
        if excluded_family is not None and entry["family"] == excluded_family:
            continue
        score = _score(entry, role, complexity, context_tokens,
                       latency_sensitive)
        if score is None:
            ranked.append({"model": entry, "score": None, "feasible": False,
                           "rationale": (
                               f"requires {context_tokens} context tokens, "
                               f"configured {entry['context']}")})
            continue
        fit = _fit_for(entry)
        parts = []
        if entry["family"] == "gpt" and entry.get("neutral"):
            parts.append("neutral fit (unknown native model)")
        else:
            parts.append(f"{entry['profile']} tier fit")
        if latency_sensitive:
            parts.append(f"latency fit {fit['latency_fit']}/3")
        if context_tokens:
            if entry["context"] is None:
                parts.append("context unverified (unknown capacity); fit "
                             "assumes the task fits")
            else:
                parts.append(f"holds {context_tokens} context tokens")
        if (entry["family"] == "kimi" and entry["profile"] == "k3-256k"
                and context_tokens <= 262_144):
            parts.append("preferred over full-context K3 within Kimi "
                         "(lower quota); never outranks an equal-fit GPT tier")
        if (entry["family"] == "kimi" and entry["profile"] == "k3"
                and context_tokens <= 262_144):
            parts.append("within Kimi, k3-256k is preferred when ≤256k "
                         "suffices (lower quota)")
        ranked.append({"model": entry, "score": score, "feasible": True,
                       "rationale": "; ".join(parts)})

    def _k3_penalty(r):
        # Equal-score tie-break: full-context K3 sorts after everything else
        # at the same score, so the 256k preference stays inside the Kimi
        # family and can never suppress equal-fit GPT rotation. Stable sort
        # keeps the caller's input order for all other ties.
        e = r["model"]
        if (e["family"] == "kimi" and e["profile"] == "k3"
                and r["feasible"] and context_tokens <= 262_144):
            return 1
        return 0

    ranked.sort(key=lambda r: (r["score"] is None, -(r["score"] or 0),
                               _k3_penalty(r)))
    return ranked
