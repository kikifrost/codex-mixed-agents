"""Credential-safe Kimi adapter materialization (stage 3).

This module owns the bounded seam between bridge-managed Provider/model
records (see ``providers.py``) and the installed Kimi Code CLI. It never
rewrites the installation TOML, never persists or copies credential values,
and never launches inference, quota reads or auth refresh itself.

Materialization model:

- Imported (installation-owned) models run against the original
  ``config.toml`` and environment. The live installation is re-validated
  against the pinned identity (upstream model, provider assignment,
  endpoint, protocol) before every use; drift fails instead of redirecting.
  Stale public records are compared against the live installation before
  anything is pinned; a disagreement fails instead of silently re-pinning.
  Incomplete imported metadata (a model pointing at a provider that has no
  provider table entry, e.g. the CLI's own ``managed:kimi-code``) passes
  through unchanged: no origin is invented and the CLI itself determines
  credential availability. Vendor-specific provider protocols (types the
  bridge does not publicly model, e.g. the CLI's own ``kimi`` type) are
  retained opaquely: pinned as a hash, never validated against the public
  protocol list, never copied into ephemeral configs.
- Managed models get an ephemeral 0700 home directory per turn containing a
  0600 ``config.toml`` with exactly one provider stanza and one model alias.
  Credentials are symbolic ``env:VAR`` references, or the live credential of
  an existing imported connection resolved internally: ``existing:``
  references name an imported alias prefix; the single underlying physical
  provider is resolved from the live installation (ambiguity fails). The
  managed endpoint must share scheme/host/port with the credential source
  and, when both are publicly known, the effective protocol must agree — a
  credential never travels to a different origin. Unsafe source endpoints
  (embedded userinfo, query or fragment) are rejected clearly. OAuth
  stanzas point at the original credential file through a symlinked
  ``credentials`` directory (no copied tokens); raw API keys travel only in
  the child process environment under a private variable name.
- Shared storage (sessions, credentials, history) is reached through
  symlinks into the original installation home, so captured-session
  continuation uses the original session storage and the source config and
  session storage are never modified.
- Guards and slot accounting always target the actual origin: existing
  references resolve to the original installation alias; env-referenced
  managed routes use the secret-free ephemeral configuration yielded by
  ``prepare`` (runtime-held) as their guard view. Connection IDs are never
  used to infer independent quota or cache.
- The per-turn ephemeral home is removed on success, failure or
  cancellation. The descriptor is JSON-serializable, path-free and
  secret-free so the runtime can persist it verbatim in the turn snapshot.

Public interface (see TEMPLATE_STAGE3_SPEC.md):

- ``MaterializationError(code, message)`` — sanitized static messages only,
  exposes ``code`` and ``message``.
- ``describe(config_path, model_record, provider_record)`` — secret-free
  pinned descriptor; validates live credential reference/origin
  compatibility without reading or inferring credential values.
- ``prepare(config_path, descriptor, state_dir)`` — context manager yielding
  ``{config_path, env, model}``; the ephemeral config doubles as the
  secret-free guard view for env-referenced managed routes.
- ``guard_target(config_path, descriptor)`` — ``{config_path, model}`` on
  the original installation for imported routes and ``existing:``
  references. Env-referenced managed routes raise ``invalid_state``: their
  guard target is the prepared ephemeral configuration.

Documented narrowing of the spec interface: ``guard_target`` takes no
``state_dir`` and never writes durable guard files; env-referenced managed
aliases do not exist in the installation config, so the runtime guards them
through the ``prepare`` context instead.
"""
from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import time
import tomllib
import urllib.parse
from pathlib import Path

try:
    from . import catalog, kimi_usage, providers, zai
except ImportError:  # pragma: no cover - direct module execution
    import catalog, kimi_usage, providers, zai

__all__ = [
    "MaterializationError",
    "ChildEnv",
    "describe",
    "prepare",
    "guard_target",
]

ERR_NOT_FOUND = "not_found"
ERR_DISABLED = "record_disabled"
ERR_UNSUPPORTED = "unsupported_profile"
ERR_UNSAFE = "unsafe_materialization"
ERR_CREDENTIAL = "credential_unavailable"
ERR_DRIFT = "drift_detected"
ERR_INVALID = "invalid_state"

_PRIVATE_ENV_PREFIX = "MIXED_AGENTS_MATERIALIZED_KEY_"

# Installation-home entries linked into the ephemeral home so the child
# reuses original session/credential/history storage. Everything else
# (server token, logs, cache, updates, ...) stays behind.
_SHARED_HOME_ENTRIES = (
    "credentials",
    "oauth",
    "sessions",
    "session_index.jsonl",
    "file-history",
    "user-history",
    "workspace-trust",
    "workspaces.json",
    "device_id",
)


class MaterializationError(Exception):
    """Materialization failure with a stable code and sanitized message."""

    def __init__(self, code, message):
        self.code = code
        self.message = str(message)
        super().__init__(self.message)


class ChildEnv(dict):
    """Child process environment; values are never shown in repr/logs."""

    def __repr__(self):
        return f"<ChildEnv {len(self)} variables; values redacted>"


def _fail(code, message):
    raise MaterializationError(code, message)


def _hash(value):
    """Stable secret-free identity for an installation-owned string."""
    if not isinstance(value, str) or not value:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _classify_origin(base_url):
    if kimi_usage._is_kimi_coding_origin(base_url):
        return "kimi"
    if zai._is_zai_origin(base_url):
        return "zai"
    return None


def _guard_kind(provider_type):
    # Runtime-facing guard kinds: the actual special origin, or none.
    return provider_type if provider_type in ("kimi", "zai") else ""


def _endpoint_origin(url):
    """(scheme, host, port) origin for credential-redirect comparison."""
    if not isinstance(url, str) or not url:
        return None
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except ValueError:
        return None
    if not parsed.scheme or not parsed.hostname:
        return None
    return (parsed.scheme.lower(), parsed.hostname.lower(), port)


def _load_live(config_path):
    """Parse the installation config; fail closed without raw content."""
    path = Path(config_path).expanduser()
    try:
        with open(path, "rb") as stream:
            config = tomllib.load(stream)
    except (FileNotFoundError, tomllib.TOMLDecodeError, OSError, ValueError):
        _fail(ERR_NOT_FOUND, "Kimi installation configuration is unavailable.")
    if not isinstance(config, dict):
        _fail(ERR_NOT_FOUND, "Kimi installation configuration is unavailable.")
    return path, config


def _config_sections(config):
    models = config.get("models")
    providers_cfg = config.get("providers")
    return (models if isinstance(models, dict) else {},
            providers_cfg if isinstance(providers_cfg, dict) else {})


def _physical_provider(config, prefix):
    """Resolve an imported alias prefix to its single physical provider.

    Installation models under the prefix may point at any provider name; all
    of them must agree on exactly one underlying physical provider entry.
    The prefix itself is not assumed to name a provider table entry.
    """
    models_cfg, providers_cfg = _config_sections(config)
    names = set()
    for alias, entry in models_cfg.items():
        if (isinstance(alias, str) and isinstance(entry, dict)
                and alias.split("/", 1)[0] == prefix):
            name = entry.get("provider")
            if isinstance(name, str) and name:
                names.add(name)
    if len(names) != 1:
        _fail(ERR_UNSAFE,
              "The referenced installation connection is ambiguous or "
              "incomplete; refusing to guess a credential source.")
    name = names.pop()
    provider = providers_cfg.get(name)
    if not isinstance(provider, dict):
        _fail(ERR_UNSAFE,
              "The referenced installation connection is incomplete.")
    return name, provider


def _pin_live_source(provider_name, provider, *, require_safe):
    """Secret-free pins for a live physical provider entry.

    The endpoint is pinned only when publicly exposable; unsafe URLs are
    either rejected (credential sources) or left unpinned (imported
    pass-through the CLI itself owns). Known protocols pin publicly; vendor
    protocols pin as an opaque hash so drift is still detected without
    whitelisting or copying them.
    """
    base_url = provider.get("base_url")
    public = None
    if isinstance(base_url, str) and base_url:
        public = providers._exposed_origin_url(base_url)
        if public is None:
            if require_safe:
                _fail(ERR_UNSAFE,
                      "The credential source endpoint is not safely exposable; "
                      "refusing to route credentials to it.")
    protocol = provider.get("type")
    protocol = protocol if isinstance(protocol, str) else None
    known = protocol if protocol in providers.KNOWN_PROTOCOLS else None
    return {
        "provider_identity": _hash(provider_name),
        "endpoint": public,
        "protocol": known,
        "type_identity": (_hash(protocol) if protocol is not None and known is None
                          else None),
        "class": _classify_origin(base_url),
    }


def _check_origin_class(base_url, provider_type):
    """A connection cannot hide a special Kimi/Z.ai origin behind another
    kind, and a special kind cannot silently move to a different origin."""
    origin_class = _classify_origin(base_url)
    if provider_type in ("kimi", "zai") and origin_class != provider_type:
        _fail(ERR_DRIFT,
              "A live connection no longer matches its declared origin; "
              "refusing to redirect.")
    if provider_type == "generic" and origin_class is not None:
        _fail(ERR_DRIFT,
              "A live connection moved to a special provider origin behind "
              "a generic connection; refusing to redirect.")


def _check_source_fingerprint(provider, source, provider_type):
    """Exact pinned endpoint/protocol/origin comparison; same-kind moves to
    a different endpoint are drift, not rotation."""
    if source.get("endpoint") is not None and provider.get("base_url") != source["endpoint"]:
        _fail(ERR_DRIFT, "A live connection endpoint changed; refusing to "
                         "redirect credentials.")
    live_type = provider.get("type")
    live_type = live_type if isinstance(live_type, str) else None
    if source.get("protocol") is not None:
        if live_type != source["protocol"]:
            _fail(ERR_DRIFT, "A live connection protocol changed; refusing "
                             "to redirect credentials.")
    elif (source.get("type_identity") is not None
            and _hash(live_type) != source["type_identity"]):
        _fail(ERR_DRIFT, "A live connection protocol changed; refusing to "
                         "redirect credentials.")
    _check_origin_class(provider.get("base_url"), provider_type)


def _validate_live(config_path, descriptor):
    """Re-check installation-owned identity before guards or execution."""
    _, config = _load_live(config_path)
    source = descriptor.get("source")
    provider_type = descriptor["provider"]["provider_type"]
    if descriptor["kind"] == "imported":
        models_cfg, providers_cfg = _config_sections(config)
        entry = models_cfg.get(descriptor["alias"])
        if not isinstance(entry, dict):
            _fail(ERR_DRIFT, "The model disappeared from the live installation.")
        if str(entry.get("model") or "") != descriptor["upstream_model"]:
            _fail(ERR_DRIFT,
                  "The model upstream changed in the live installation.")
        if descriptor.get("profile") in {"k3.1-pro", "k3.1-pro-highspeed"}:
            thinking = config.get("thinking", {})
            if (isinstance(thinking, dict) and thinking.get("enabled") is False
                    and "always_thinking" not in (entry.get("capabilities") or [])):
                _fail(ERR_INVALID, "K3.1 Pro requires thinking: enable "
                      "[thinking].enabled or declare always_thinking in "
                      "the CLI model capabilities.")
        name = entry.get("provider")
        if not isinstance(name, str) or not name:
            _fail(ERR_DRIFT,
                  "The model provider assignment changed in the live installation.")
        if source is None or _hash(name) != source["provider_identity"]:
            _fail(ERR_DRIFT,
                  "The model provider assignment changed in the live installation.")
        provider = providers_cfg.get(name)
        if not isinstance(provider, dict):
            # Incomplete installation metadata (e.g. the CLI's own managed
            # provider without a provider table entry): pass through
            # unchanged; no origin is invented and the CLI decides
            # credential availability. Known special origins were pinned
            # only when a provider entry actually exists.
            return config
        _check_source_fingerprint(provider, source, provider_type)
    elif source is not None:
        origin = descriptor.get("credential_origin") or {}
        name, provider = _physical_provider(config, origin.get("provider_id"))
        if _hash(name) != source["provider_identity"]:
            _fail(ERR_DRIFT,
                  "The referenced credential source changed in the live installation.")
        _check_source_fingerprint(provider, source, provider_type)
    return config


def describe(config_path, model_record, provider_record):
    """Build a secret-free, pinned materialization descriptor.

    ``model_record``/``provider_record`` are the merged public records from
    ``providers.build_resources``. The descriptor pins model/provider
    identity and revisions, the guard kind, and the credential source
    fingerprint (public endpoint, protocol or opaque vendor-type hash,
    provider assignment hash — never credential material). It validates
    live credential reference/origin compatibility without reading or
    inferring any credential value.
    """
    if not isinstance(model_record, dict):
        _fail(ERR_INVALID, "A model record is required.")
    if not isinstance(provider_record, dict):
        _fail(ERR_INVALID, "A provider record is required.")
    model = model_record
    provider = provider_record
    alias = model.get("alias")
    if not isinstance(alias, str) or not alias:
        _fail(ERR_INVALID, "Model record must carry a public alias.")
    provider_id = model.get("provider_id")
    if not isinstance(provider_id, str) or not provider_id:
        _fail(ERR_INVALID, "Model record must carry a provider identity.")
    # Ownership: the provider record must be the model's own connection.
    if provider.get("id") != provider_id:
        _fail(ERR_INVALID, "Model and connection records do not belong together.")
    if model.get("agent_type") != "kimi-code":
        _fail(ERR_UNSUPPORTED, "Only Kimi Code CLI routes can be materialized.")
    if model.get("enabled") is False or provider.get("enabled") is False:
        _fail(ERR_DISABLED, "The model or its connection is disabled.")
    if provider.get("available") is False:
        _fail(ERR_CREDENTIAL, "The connection has no usable credential reference.")
    upstream = model.get("upstream_model")
    family, profile_key = catalog._cli_profile_for_model(str(upstream or ""))
    if family not in ("kimi", "glm") or model.get("family") != family:
        _fail(ERR_UNSUPPORTED,
              "The upstream model is not a recognized Kimi/GLM profile for "
              "this model record.")
    provider_type = provider.get("provider_type")
    if provider_type not in providers.PROVIDER_TYPES:
        _fail(ERR_UNSAFE, "The connection has an unknown kind.")
    protocol = provider.get("protocol")
    if protocol is not None and protocol not in providers.KNOWN_PROTOCOLS:
        _fail(ERR_UNSAFE, "The connection has an unknown protocol.")
    kind = model.get("source")
    if kind not in ("managed", "imported"):
        _fail(ERR_INVALID, "The model record has an unknown source.")

    endpoint = provider.get("endpoint")
    if kind == "managed":
        if not isinstance(endpoint, str) or not endpoint:
            _fail(ERR_UNSAFE, "A managed connection must declare an endpoint.")
        # A managed route whose model family the connection's guard origin
        # cannot guard (e.g. a GLM model on a Kimi-guarded connection)
        # would bypass the actual-origin guard; fail closed.
        if provider_type in ("kimi", "zai") and family != {"kimi": "kimi", "zai": "glm"}[provider_type]:
            _fail(ERR_UNSAFE,
                  "The model family is not guardable on the connection's origin.")
        origin_class = _classify_origin(endpoint)
        if provider_type == "generic" and origin_class is not None:
            _fail(ERR_UNSAFE,
                  "A generic connection must not use a special provider origin.")
        if provider_type in ("kimi", "zai") and origin_class != provider_type:
            _fail(ERR_UNSAFE,
                  "The managed endpoint does not match the connection kind.")

    # Credential plan. Imported records and managed records under an
    # imported connection resolve credentials from the live installation;
    # bridge-managed connections use their stored symbolic reference.
    credential_ref = provider.get("credential_ref")
    credential_origin = None
    source = None
    _, config = _load_live(config_path)
    if kind == "imported":
        models_cfg, providers_cfg = _config_sections(config)
        entry = models_cfg.get(alias)
        if not isinstance(entry, dict):
            _fail(ERR_NOT_FOUND,
                  "The model is not present in the live installation.")
        if str(entry.get("model") or "") != str(upstream):
            _fail(ERR_DRIFT,
                  "The live installation disagrees with the model record.")
        name = entry.get("provider")
        if not isinstance(name, str) or not name:
            _fail(ERR_UNSAFE,
                  "The live model has no provider assignment to materialize.")
        provider_entry = providers_cfg.get(name)
        if isinstance(provider_entry, dict):
            # A stale public record must not be silently re-pinned: compare
            # everything the record already knows against the live
            # installation first.
            _check_origin_class(provider_entry.get("base_url"), provider_type)
            if (endpoint is not None
                    and provider_entry.get("base_url") != endpoint):
                _fail(ERR_DRIFT,
                      "The connection record is stale; the live endpoint "
                      "differs from the pinned record.")
            live_type = provider_entry.get("type")
            live_type = live_type if isinstance(live_type, str) else None
            if protocol is not None and live_type != protocol:
                _fail(ERR_DRIFT,
                      "The connection record is stale; the live protocol "
                      "differs from the pinned record.")
            source = _pin_live_source(name, provider_entry, require_safe=False)
        else:
            # Incomplete imported metadata: pin the assignment identity
            # only; the route passes through to the original config.
            source = {"provider_identity": _hash(name), "endpoint": None,
                      "protocol": None, "type_identity": None, "class": None}
        credential_origin = {"provider_id": provider_id, "alias": alias}
    elif isinstance(credential_ref, str) and credential_ref.startswith("existing:"):
        target = credential_ref[len("existing:"):]
        if not target or (target == provider_id and provider.get("source") != "imported"):
            _fail(ERR_UNSAFE, "The connection has an invalid credential reference.")
        # Managed models may be added under a live imported connection; the
        # imported projection records that as a self existing: reference.
        name, provider_entry = _physical_provider(config, target)
        source_base = provider_entry.get("base_url")
        if not isinstance(source_base, str) or not source_base:
            _fail(ERR_UNSAFE, "The credential source has no endpoint.")
        # No credential redirect: the managed endpoint must share scheme,
        # host and port with the credential source, and when both protocols
        # are publicly known they must agree. Vendor protocols are carried
        # opaquely and pinned by hash instead of being compared or copied.
        managed_origin = _endpoint_origin(endpoint)
        if managed_origin is None or managed_origin != _endpoint_origin(source_base):
            _fail(ERR_UNSAFE,
                  "The managed endpoint and the credential source must "
                  "share the same origin.")
        source_type = provider_entry.get("type")
        source_type = source_type if isinstance(source_type, str) else None
        if (source_type is not None and source_type in providers.KNOWN_PROTOCOLS
                and protocol is not None and source_type != protocol):
            _fail(ERR_UNSAFE,
                  "The managed protocol disagrees with the credential source.")
        source = _pin_live_source(name, provider_entry, require_safe=True)
        _check_origin_class(source_base, provider_type)
        origin_class = _classify_origin(source_base)
        chosen = _guardable_source_alias(config_path, config, target, origin_class)
        if chosen is None:
            if origin_class in ("kimi", "zai"):
                # Managed references fail closed: the actual-origin guard
                # must be able to see the source account.
                _fail(ERR_CREDENTIAL,
                      "The credential source has no guardable alias for its "
                      "actual origin.")
            _fail(ERR_CREDENTIAL,
                  "The referenced connection has no live model alias.")
        credential_origin = {"provider_id": target, "alias": chosen}
    elif not (isinstance(credential_ref, str)
              and providers._ENV_REF_RE.fullmatch(credential_ref)):
        _fail(ERR_UNSAFE,
              "The connection has no symbolic credential reference.")

    descriptor = {
        "kind": kind,
        "alias": alias,
        "provider_id": provider_id,
        "upstream_model": str(upstream),
        "family": family,
        "profile": profile_key,
        "guard_kind": _guard_kind(provider_type),
        "provider_type": provider_type,
        "endpoint": endpoint if isinstance(endpoint, str) else None,
        "protocol": protocol,
        "credential_ref": (credential_ref if isinstance(credential_ref, str)
                           else None),
        "credential_origin": credential_origin,
        "source": source,
        "model_revision": model.get("revision"),
        "provider_revision": provider.get("revision"),
        "model": {
            "alias": alias,
            "provider_id": provider_id,
            "upstream_model": str(upstream),
            "family": family,
            "context": model.get("context"),
            "capabilities": list(model.get("capabilities") or []),
            "display_name": (model.get("display_name")
                             if isinstance(model.get("display_name"), str) else alias),
            "revision": model.get("revision"),
            "source": kind,
        },
        "provider": {
            "id": provider_id,
            "provider_type": provider_type,
            "endpoint": endpoint if isinstance(endpoint, str) else None,
            "protocol": protocol,
            "credential_ref": (credential_ref if isinstance(credential_ref, str)
                               else None),
            "revision": provider.get("revision"),
            "source": provider.get("source"),
        },
    }
    # Describe validates live credential reference/origin compatibility up
    # front; guard_target/prepare re-run the same check to catch drift.
    _validate_live(config_path, descriptor)
    return descriptor


def models_cfg_aliases(config, prefix):
    models_cfg, _ = _config_sections(config)
    return (alias for alias, entry in models_cfg.items()
            if isinstance(alias, str) and isinstance(entry, dict)
            and alias.split("/", 1)[0] == prefix)


def _guardable_source_alias(config_path, config, prefix, origin_class):
    """Pick the original alias that represents the credential source account
    for guards and slot accounting.

    For a known special origin the alias must come from that origin's
    eligible alias set (the Kimi guard only recognizes Kimi-family aliases;
    a blind lexicographic pick could select a GLM alias under a Kimi source
    and silently bypass the guard). Generic sources may use any alias.
    """
    candidates = sorted(models_cfg_aliases(config, prefix))
    if origin_class == "kimi":
        eligible = kimi_usage.aliases(config_path)
    elif origin_class == "zai":
        eligible = zai.aliases(config_path)
    else:
        return candidates[0] if candidates else None
    guardable = [alias for alias in candidates if alias in eligible]
    return guardable[0] if guardable else None


def _require_descriptor(descriptor):
    if not isinstance(descriptor, dict) or not isinstance(descriptor.get("alias"), str):
        _fail(ERR_INVALID, "A materialization descriptor is required.")
    if descriptor.get("kind") not in ("managed", "imported"):
        _fail(ERR_INVALID, "The descriptor has an unknown route kind.")


def _private_env_var(descriptor):
    digest = hashlib.sha256(
        f"{descriptor['provider_id']}\0{descriptor['alias']}".encode("utf-8")
    ).hexdigest()[:16].upper()
    return f"{_PRIVATE_ENV_PREFIX}{digest}"


def _resolve_credential(descriptor, config):
    """Resolve the ephemeral route credential and its effective type.

    Returns (credential_plan, private_env, route_type). No credential value
    is ever written to disk; raw API keys are returned in private_env for
    the child process environment only. The route type is the credential
    source's own protocol when available (including vendor types the CLI
    natively supports), so the ephemeral provider stanza matches what the
    CLI expects for that endpoint.
    """
    ref = descriptor.get("credential_ref")
    if descriptor["kind"] == "managed" and isinstance(ref, str) and ref.startswith("env:"):
        var = ref[len("env:"):]
        if not os.environ.get(var):
            _fail(ERR_CREDENTIAL, "The credential environment variable is not set.")
        return {"kind": "env", "var": var}, {}, descriptor.get("protocol")

    origin = descriptor.get("credential_origin")
    if not isinstance(origin, dict) or not isinstance(origin.get("provider_id"), str):
        _fail(ERR_UNSAFE, "The route has no resolvable credential reference.")
    _, source_provider = _physical_provider(config, origin["provider_id"])
    route_type = source_provider.get("type")
    if not isinstance(route_type, str) or not route_type:
        route_type = descriptor.get("protocol")
    oauth = source_provider.get("oauth")
    if isinstance(oauth, dict):
        if oauth.get("storage", "file") != "file":
            _fail(ERR_UNSAFE, "Only file-based OAuth storage can be materialized.")
        key = oauth.get("key")
        if (not isinstance(key, str) or not key.strip()
                or "/" in key.removeprefix("oauth/") or "\\" in key or ".." in key):
            _fail(ERR_UNSAFE, "The live OAuth reference is malformed.")
        return {"kind": "oauth", "key": key.strip()}, {}, route_type
    env_name = source_provider.get("api_key_env")
    if isinstance(env_name, str) and env_name:
        if not os.environ.get(env_name):
            _fail(ERR_CREDENTIAL, "The credential environment variable is not set.")
        return {"kind": "env", "var": env_name}, {}, route_type
    api_key = source_provider.get("api_key")
    if isinstance(api_key, str) and api_key.strip():
        var = _private_env_var(descriptor)
        return {"kind": "env", "var": var}, {var: api_key.strip()}, route_type
    _fail(ERR_CREDENTIAL, "The referenced connection has no usable credential.")


def _toml_str(value):
    return ('"' + str(value).replace("\\", "\\\\").replace('"', '\\"')
            .replace("\n", "\\n").replace("\r", "\\r") + '"')


def _write_route_config(path, descriptor, credential_plan, route_type):
    """Write the ephemeral single-route config; credential-free on disk."""
    if not isinstance(route_type, str) or not route_type:
        _fail(ERR_UNSAFE,
              "The route cannot be materialized without a known protocol.")
    provider_id = descriptor["provider_id"]
    alias = descriptor["alias"]
    # Root default_model: the CLI requires a configured default model at
    # load time, even when -m selects the alias explicitly. The ephemeral
    # config is self-contained and defaults to its single route.
    lines = [
        f"default_model = {_toml_str(alias)}",
        "",
        f"[providers.{_toml_str(provider_id)}]",
        f"type = {_toml_str(route_type)}",
        f"base_url = {_toml_str(descriptor['endpoint'])}",
    ]
    if credential_plan["kind"] == "env":
        lines.append(f"api_key_env = {_toml_str(credential_plan['var'])}")
    else:  # oauth: resolved relative to the linked credentials directory
        lines.append("")
        lines.append(f"[providers.{_toml_str(provider_id)}.oauth]")
        lines.append('storage = "file"')
        lines.append(f"key = {_toml_str(credential_plan['key'])}")
    model = descriptor["model"]
    lines.append("")
    lines.append(f"[models.{_toml_str(alias)}]")
    lines.append(f"model = {_toml_str(descriptor['upstream_model'])}")
    lines.append(f"provider = {_toml_str(provider_id)}")
    lines.append(f"display_name = {_toml_str(model['display_name'])}")
    if isinstance(model.get("context"), int) and model["context"] > 0:
        lines.append(f"max_context_size = {model['context']}")
    capabilities = list(model.get("capabilities") or [])
    if descriptor.get("profile") in {"k3.1-pro", "k3.1-pro-highspeed"}:
        capabilities = sorted(set(capabilities) | {"thinking", "always_thinking"})
    caps = ", ".join(_toml_str(c) for c in capabilities)
    lines.append(f"capabilities = [{caps}]")
    if descriptor.get("profile") in {"k3.1-pro", "k3.1-pro-highspeed"}:
        lines.extend(["", "[thinking]", "enabled = true", 'effort = "high"'])
    try:
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.chmod(path, 0o600)
    except OSError:
        _fail(ERR_UNSAFE, "The ephemeral configuration could not be written.")


def _ephemeral_home(config_path, state_dir, descriptor):
    """Create the ephemeral home: 0600 config plus shared-storage symlinks."""
    home = Path(config_path).expanduser().parent
    root = Path(state_dir).expanduser() / "materialized"
    digest = hashlib.sha256(
        f"{descriptor['provider_id']}\0{descriptor['alias']}\0{os.getpid()}\0{time.time_ns()}"
        .encode("utf-8")
    ).hexdigest()[:16]
    directory = root / digest
    try:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
    except OSError:
        _fail(ERR_UNSAFE, "The ephemeral home could not be created.")
    for name in _SHARED_HOME_ENTRIES:
        target = home / name
        if not target.exists() and not target.is_symlink():
            continue
        try:
            os.symlink(target, directory / name)
        except OSError:
            shutil.rmtree(directory, ignore_errors=True)
            _fail(ERR_UNSAFE, "The original storage could not be linked.")
    return directory


def _child_env(config_path, extra=None):
    source = Path(config_path).expanduser()
    path = source.parent.resolve() / source.name
    if path.name != "config.toml":
        _fail(ERR_UNSAFE,
              "Kimi Code requires a file named config.toml; select its existing "
              "directory with KIMI_CODE_HOME.")
    env = ChildEnv(dict(os.environ))
    env["KIMI_CODE_HOME"] = str(path.parent)
    for key, value in (extra or {}).items():
        env[key] = value
    return env


@contextlib.contextmanager
def prepare(config_path, descriptor, state_dir):
    """Yield ``{config_path, env, model}`` for one CLI turn.

    Imported originals yield the original config/env, including unchanged
    or incomplete installation metadata (pass-through). Managed routes
    create an ephemeral home (0600 config, shared-storage symlinks) and
    remove it on success, failure or cancellation. The ephemeral config is
    the runtime-held, secret-free guard view for env-referenced routes.
    """
    _require_descriptor(descriptor)
    config = _validate_live(config_path, descriptor)
    alias = descriptor["alias"]
    if descriptor["kind"] == "imported":
        yield {"config_path": str(Path(config_path).expanduser()),
               "env": _child_env(config_path), "model": alias}
        return
    credential_plan, private_env, route_type = _resolve_credential(descriptor, config)
    directory = _ephemeral_home(config_path, state_dir, descriptor)
    try:
        _write_route_config(directory / "config.toml", descriptor,
                            credential_plan, route_type)
        env = _child_env(directory / "config.toml", extra=private_env)
        yield {"config_path": str(directory / "config.toml"), "env": env, "model": alias}
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def guard_target(config_path, descriptor):
    """Resolve the actual-origin ``{config_path, model}`` for quota guards.

    Imported routes and managed ``existing:`` references guard the original
    installation alias on the original configuration. Env-referenced managed
    routes raise ``invalid_state``: their alias does not exist in the
    installation config, so the runtime guards them through the secret-free
    ephemeral configuration yielded by ``prepare``.
    """
    _require_descriptor(descriptor)
    _validate_live(config_path, descriptor)
    original = str(Path(config_path).expanduser())
    if descriptor["kind"] == "imported":
        return {"config_path": original, "model": descriptor["alias"]}
    origin = descriptor.get("credential_origin")
    if isinstance(origin, dict) and isinstance(origin.get("alias"), str):
        # The referenced imported connection is the actual account: guard it
        # under its original installation alias, never the managed identity.
        return {"config_path": original, "model": origin["alias"]}
    _fail(ERR_INVALID,
          "Env-referenced managed routes guard through the prepared "
          "ephemeral configuration.")
