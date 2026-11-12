"""Public Provider/model resource backend (stage 2).

SQLite owns bridge-managed Provider/model records and imported-resource
metadata overlays. The CLI TOML remains the credential/installation source:
this module never rewrites it, copies secrets, refreshes auth or launches
inference on configuration reads. Imported records are a live projection —
aliases that disappear from the installation are not resurrected from the
database. New connections use symbolic ``env:VARIABLE`` or
``existing:imported_id`` credential references only.
"""
from contextlib import contextmanager
import json
from pathlib import Path
import re
import sqlite3
import tomllib
import urllib.parse

try:
    from . import catalog, configuration, kimi_usage, security, zai
except ImportError:
    import catalog, configuration, kimi_usage, security, zai

ConfigurationError = configuration.ConfigurationError

PROVIDER_TYPES = ("kimi", "zai", "generic")
KNOWN_PROTOCOLS = ("openai", "anthropic")
CAPABILITY_WHITELIST = ("thinking", "always_thinking", "tool_use", "image_in",
                        "video_in", "dynamically_loaded_tools")
MAX_CONTEXT = 2_000_000
MAX_ID_LENGTH = 64
MAX_LABEL_LENGTH = 200
MAX_ALIAS_LENGTH = 256
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_ENV_REF_RE = re.compile(r"^env:[A-Za-z_][A-Za-z0-9_]*$")

POLICY_SCOPE = {
    "kimi": "Shared global Kimi policy and account order/caps; it applies to "
            "every Kimi CLI account and is not a per-connection policy store.",
    "zai": "Shared Z.ai GLM provider cap/guard across its eligible accounts; "
           "the off-peak preference uses the existing global cost policy.",
    "generic": "No provider-specific quota or off-peak policy; shared CLI "
               "concurrency and model-family independence rules still apply.",
}


def _classify_origin(base_url):
    """Provider kind from the actual configured endpoint origin."""
    if kimi_usage._is_kimi_coding_origin(base_url):
        return "kimi"
    if zai._is_zai_origin(base_url):
        return "zai"
    return None


def _exposed_origin_url(base_url):
    """Return base_url only when safe to expose: HTTPS, a valid port, a host,
    and no embedded userinfo, query or fragment."""
    if not isinstance(base_url, str) or not base_url:
        return None
    try:
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.port is not None and parsed.port < 1:
            return None
    except ValueError:
        return None
    if (parsed.scheme.lower() != "https" or not parsed.hostname
            or any(c.isspace() for c in base_url)):
        return None
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        return None
    return base_url


def imported_projection(config_path, secrets=()):
    """Live imported Provider/model projection from the CLI installation.

    Provider kind is established from the actual configured endpoint origin
    using the existing Kimi/Z.ai origin validators, never from alias prefixes
    or model family. Ambiguous or incomplete imported details classify as
    generic with no invented endpoint or credential reference. Credential
    values and raw config are never returned.
    """
    path = Path(config_path).expanduser()
    try:
        with open(path, "rb") as stream:
            config = tomllib.load(stream)
    except (FileNotFoundError, tomllib.TOMLDecodeError, OSError, ValueError):
        config = {}
    if not isinstance(config, dict):
        config = {}
    models_cfg = config.get("models")
    providers_cfg = config.get("providers")
    models_cfg = models_cfg if isinstance(models_cfg, dict) else {}
    providers_cfg = providers_cfg if isinstance(providers_cfg, dict) else {}

    providers, models, origins = {}, {}, {}
    for model in catalog.load_kimi_models(path, all_aliases=True):
        alias = model["alias"]
        # Redacting an identity would produce a different, unusable alias.
        # Omit credential-like identities instead of copying them into state.
        if (security.redact(alias, secrets) != alias
                or security.redact(model["model"], secrets) != model["model"]):
            continue
        provider_id = alias.split("/", 1)[0]
        if (not provider_id or security.SECRET_KEY.fullmatch(provider_id)
                or security.SECRET_KEY.fullmatch(alias)):
            continue
        entry = models_cfg.get(alias)
        entry = entry if isinstance(entry, dict) else {}
        info = origins.setdefault(provider_id, {"classes": set(), "providers": set(),
                                                "ambiguous": False, "base_url": None,
                                                "protocol": None, "credential_ref": None})
        provider_name = entry.get("provider")
        provider = providers_cfg.get(provider_name) if isinstance(provider_name, str) else None
        if not isinstance(provider, dict):
            # Incomplete imported details must not invent an endpoint.
            info["ambiguous"] = True
        else:
            info["providers"].add(provider_name)
            info["classes"].add(_classify_origin(provider.get("base_url")) or "generic")
            if info["base_url"] is None:
                info["base_url"] = provider.get("base_url")
                ptype = provider.get("type")
                if isinstance(ptype, str) and ptype in KNOWN_PROTOCOLS:
                    info["protocol"] = ptype
                env_name = provider.get("api_key_env")
                if isinstance(env_name, str) and _ENV_REF_RE.fullmatch("env:" + env_name):
                    info["credential_ref"] = "env:" + env_name
                elif provider.get("api_key") or provider.get("oauth"):
                    info["credential_ref"] = "existing:" + provider_id
        record = providers.setdefault(provider_id, {"id": provider_id, "model_aliases": [],
                                                    "endpoint": None, "protocol": None,
                                                    "credential_ref": None})
        record["model_aliases"].append(alias)
        models[alias] = {
            "alias": alias,
            "provider_id": provider_id,
            "upstream_model": model["model"],
            "display_name": model["display_name"] if isinstance(model["display_name"], str) else alias,
            "agent_type": "kimi-code",
            "family": model["family"],
            "context": model["context"],
            "capabilities": list(model["capabilities"]),
        }
    for provider_id, info in origins.items():
        # Mixed or missing origins under one prefix are generic: no guesses.
        if info["ambiguous"] or len(info["providers"]) != 1 or len(info["classes"]) != 1:
            providers[provider_id]["provider_type"] = "generic"
            continue
        providers[provider_id]["provider_type"] = info["classes"].pop()
        exposed = _exposed_origin_url(info["base_url"])
        if exposed is not None and security.redact(exposed, secrets) == exposed:
            providers[provider_id]["endpoint"] = exposed
        providers[provider_id]["protocol"] = info["protocol"]
        reference = info["credential_ref"]
        if reference is not None and security.redact(reference, secrets) == reference:
            providers[provider_id]["credential_ref"] = reference
    return security.sanitize({"providers": providers, "models": models}, secrets)


def _check_no_secret(value, secrets):
    if security.redact(value, secrets) != value:
        raise ConfigurationError("invalid_argument",
                                 "Argument must not contain credential material.")


def _provider_id(value, imported_ids, secrets):
    if (not isinstance(value, str) or not value or len(value) > MAX_ID_LENGTH
            or "/" in value or not _ID_RE.fullmatch(value)
            or security.SECRET_KEY.fullmatch(value)):
        raise ConfigurationError("invalid_argument",
                                 "Provider ID must be a safe public name without slashes.")
    _check_no_secret(value, secrets)
    if value in imported_ids:
        raise ConfigurationError("invalid_argument",
                                 "Provider ID collides with an imported installation connection.")
    return value


def _label(value, secrets):
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_LABEL_LENGTH:
        raise ConfigurationError("invalid_argument", "Label must be a nonempty string.")
    value = value.strip()
    _check_no_secret(value, secrets)
    return value


def _provider_type(value):
    if not isinstance(value, str) or value not in PROVIDER_TYPES:
        raise ConfigurationError("invalid_argument", "Provider type must be a known kind.")
    return value


def _endpoint(value, provider_type, secrets):
    if not isinstance(value, str) or not value:
        raise ConfigurationError("invalid_argument", "Endpoint must be a nonempty HTTPS URL.")
    try:
        parsed = urllib.parse.urlsplit(value)
        parsed.port
    except ValueError:
        raise ConfigurationError("invalid_argument", "Endpoint must be a valid HTTPS URL.") from None
    if (parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment
            or any(c.isspace() for c in value) or parsed.port == 0):
        raise ConfigurationError("invalid_argument",
                                 "Endpoint must be HTTPS with a host and no embedded "
                                 "credential, query or fragment.")
    _check_no_secret(value, secrets)
    origin = _classify_origin(value)
    if provider_type == "generic" and origin is not None:
        raise ConfigurationError("invalid_argument",
                                 "A generic connection cannot declare a special Kimi/Z.ai "
                                 "endpoint origin.")
    if provider_type in ("kimi", "zai") and origin != provider_type:
        raise ConfigurationError("invalid_argument",
                                 "Special provider kinds must match the actual endpoint origin.")
    return value


def _protocol(value):
    if not isinstance(value, str) or value not in KNOWN_PROTOCOLS:
        raise ConfigurationError("invalid_argument", "Protocol must be a known protocol.")
    return value


def _credential_ref(value, imported_ids, managed_ids):
    if not isinstance(value, str) or not value:
        raise ConfigurationError("invalid_argument",
                                 "Credential reference must use the env: or existing: form.")
    if _ENV_REF_RE.fullmatch(value):
        return value
    if value.startswith("existing:"):
        target = value[len("existing:"):]
        # References resolve to a currently imported, unambiguous connection
        # only: never to another managed connection, never in chains.
        if target in managed_ids or target not in imported_ids:
            raise ConfigurationError("invalid_argument",
                                     "existing: references must resolve to a currently "
                                     "imported connection.")
        return value
    raise ConfigurationError("invalid_argument",
                             "Credential reference must use the env: or existing: form.")


def _alias(value, provider_id, secrets):
    if (not isinstance(value, str) or not value or len(value) > MAX_ALIAS_LENGTH
            or "/" not in value or value.split("/", 1)[0] != provider_id
            or not value.split("/", 1)[1] or security.SECRET_KEY.fullmatch(value)):
        raise ConfigurationError("invalid_argument",
                                 "Alias must be the provider ID plus a nonempty suffix.")
    _check_no_secret(value, secrets)
    if any(c.isspace() for c in value):
        raise ConfigurationError("invalid_argument", "Alias must not contain whitespace.")
    return value


def _context(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_CONTEXT:
        raise ConfigurationError("invalid_argument",
                                 "Context must be a positive integer within the supported bound.")
    return value


def _capabilities(value):
    if (not isinstance(value, list)
            or not all(isinstance(c, str) for c in value)
            or any(c not in CAPABILITY_WHITELIST for c in value)
            or len(set(value)) != len(value)):
        raise ConfigurationError("invalid_argument",
                                 "Capabilities must be unique strings from the known set.")
    return list(value)


def _upstream_family(value):
    if not isinstance(value, str):
        raise ConfigurationError("invalid_argument",
                                 "Upstream model must be a recognized Kimi or GLM profile.")
    family, key = catalog._cli_profile_for_model(value)
    if family not in ("kimi", "glm") or key is None:
        raise ConfigurationError("invalid_argument",
                                 "Upstream model must be a recognized Kimi or GLM profile.")
    return family


class ProviderStore:
    """SQLite-backed managed Provider/model records plus imported metadata."""

    def __init__(self, db_path):
        self.db_path = str(db_path)

    @contextmanager
    def _transaction(self):
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _ensure_tables(db):
        db.execute("""CREATE TABLE IF NOT EXISTS provider_records (
            provider_id TEXT PRIMARY KEY, label TEXT NOT NULL, provider_type TEXT NOT NULL,
            endpoint TEXT, protocol TEXT, credential_ref TEXT,
            enabled INTEGER NOT NULL, revision INTEGER NOT NULL, source TEXT NOT NULL)""")
        db.execute("""CREATE TABLE IF NOT EXISTS model_records (
            alias TEXT PRIMARY KEY, provider_id TEXT NOT NULL, upstream_model TEXT NOT NULL,
            display_name TEXT NOT NULL, family TEXT NOT NULL, context INTEGER,
            capabilities TEXT NOT NULL, enabled INTEGER NOT NULL, revision INTEGER NOT NULL,
            source TEXT NOT NULL)""")

    @staticmethod
    def _provider_record(row, model_aliases=()):
        record = {"id": row["provider_id"], "label": row["label"],
                  "provider_type": row["provider_type"], "enabled": bool(row["enabled"]),
                  "revision": row["revision"], "source": row["source"],
                  "model_aliases": sorted(model_aliases)}
        record.update(endpoint=row["endpoint"], protocol=row["protocol"],
                      credential_ref=row["credential_ref"])
        return record

    @staticmethod
    def _model_record(row):
        return {"alias": row["alias"], "provider_id": row["provider_id"],
                "upstream_model": row["upstream_model"], "display_name": row["display_name"],
                "agent_type": "kimi-code", "family": row["family"], "context": row["context"],
                "capabilities": json.loads(row["capabilities"]), "enabled": bool(row["enabled"]),
                "revision": row["revision"], "source": row["source"]}

    def _provider_row(self, db, provider_id):
        row = db.execute("SELECT * FROM provider_records WHERE provider_id=?",
                         (provider_id,)).fetchone()
        if row is None:
            raise ConfigurationError("not_found", "Provider not found.")
        return row

    def _model_row(self, db, alias):
        row = db.execute("SELECT * FROM model_records WHERE alias=?", (alias,)).fetchone()
        if row is None:
            raise ConfigurationError("not_found", "Model not found.")
        return row

    def sync_imported(self, projection):
        """Upsert live imported metadata; managed rows are never touched.

        Disappearing CLI aliases are not resurrected: rows for absent aliases
        simply do not take part in the merged view.
        """
        with self._transaction() as db:
            self._ensure_tables(db)
            for provider_id, record in projection["providers"].items():
                row = db.execute("SELECT * FROM provider_records WHERE provider_id=?",
                                 (provider_id,)).fetchone()
                if row is None:
                    db.execute("INSERT INTO provider_records VALUES (?,?,?,?,?,?,?,?,?)",
                               (provider_id, provider_id, record.get("provider_type", "generic"),
                                record.get("endpoint"), record.get("protocol"),
                                record.get("credential_ref"), 1, 1, "imported"))
                elif row["source"] == "imported":
                    # Kind follows the live installation (drift is observable);
                    # user metadata (label/enabled/revision) is preserved.
                    changed = any(row[k] != record.get(k) for k in
                                  ("provider_type", "endpoint", "protocol", "credential_ref"))
                    db.execute("UPDATE provider_records SET provider_type=?, endpoint=?, "
                               "protocol=?, credential_ref=?, revision=revision+? WHERE provider_id=?",
                               (record.get("provider_type", "generic"), record.get("endpoint"),
                                record.get("protocol"), record.get("credential_ref"), int(changed), provider_id))
            for alias, record in projection["models"].items():
                row = db.execute("SELECT * FROM model_records WHERE alias=?", (alias,)).fetchone()
                if row is None:
                    db.execute("INSERT INTO model_records VALUES (?,?,?,?,?,?,?,?,?,?)",
                               (alias, record["provider_id"], record["upstream_model"],
                                record["display_name"], record["family"], record["context"],
                                json.dumps(record["capabilities"]), 1, 1, "imported"))
                elif row["source"] == "imported":
                    changed = row["upstream_model"] != record["upstream_model"] or row["family"] != record["family"]
                    db.execute("UPDATE model_records SET upstream_model=?, family=?, revision=revision+? WHERE alias=?",
                               (record["upstream_model"], record["family"], int(changed), alias))

    def create_provider(self, provider_id, label, provider_type, endpoint, protocol,
                        credential_ref, *, imported_ids, secrets):
        provider_id = _provider_id(provider_id, imported_ids, secrets)
        label = _label(label, secrets)
        provider_type = _provider_type(provider_type)
        endpoint = _endpoint(endpoint, provider_type, secrets)
        protocol = _protocol(protocol)
        if isinstance(credential_ref, str):
            _check_no_secret(credential_ref, secrets)
        with self._transaction() as db:
            self._ensure_tables(db)
            managed_ids = {r[0] for r in db.execute(
                "SELECT provider_id FROM provider_records WHERE source='managed'")}
            credential_ref = _credential_ref(credential_ref, imported_ids, managed_ids)
            try:
                db.execute("INSERT INTO provider_records VALUES (?,?,?,?,?,?,?,?,?)",
                           (provider_id, label, provider_type, endpoint, protocol,
                            credential_ref, 1, 1, "managed"))
            except sqlite3.IntegrityError:
                raise ConfigurationError("invalid_argument",
                                         "A connection with this ID already exists.") from None
            return self._provider_record(self._provider_row(db, provider_id))

    def save_provider(self, provider_id, expected_revision, label=None, enabled=None,
                      endpoint=None, protocol=None, credential_ref=None, *,
                      imported_ids, secrets):
        if type(expected_revision) is not int or expected_revision < 1:
            raise ConfigurationError("invalid_argument",
                                     "Expected revision must be a positive integer.")
        if enabled is not None and not isinstance(enabled, bool):
            raise ConfigurationError("invalid_argument", "Enabled must be a boolean.")
        if not isinstance(provider_id, str) or not provider_id:
            raise ConfigurationError("not_found", "Provider not found.")
        with self._transaction() as db:
            self._ensure_tables(db)
            row = self._provider_row(db, provider_id)
            if row["revision"] != expected_revision:
                raise ConfigurationError("revision_conflict", "Connection revision changed.")
            # Validate the complete new record before any write so invalid
            # requests never partially mutate state.
            new_label = row["label"] if label is None else _label(label, secrets)
            new_enabled = row["enabled"] if enabled is None else int(enabled)
            if row["source"] == "imported":
                if endpoint is not None or protocol is not None or credential_ref is not None:
                    raise ConfigurationError("invalid_argument",
                                             "Imported connection fields remain installation-owned; "
                                             "only label and enabled may be edited.")
                new_endpoint, new_protocol, new_ref = (row["endpoint"], row["protocol"],
                                                       row["credential_ref"])
            else:
                managed_ids = {r[0] for r in db.execute(
                    "SELECT provider_id FROM provider_records WHERE source='managed'")}
                new_endpoint = (row["endpoint"] if endpoint is None
                                else _endpoint(endpoint, row["provider_type"], secrets))
                new_protocol = row["protocol"] if protocol is None else _protocol(protocol)
                new_ref = (row["credential_ref"] if credential_ref is None
                           else _credential_ref(credential_ref, imported_ids, managed_ids))
                _check_no_secret(new_ref, secrets)
            db.execute("UPDATE provider_records SET label=?, enabled=?, endpoint=?, protocol=?, "
                       "credential_ref=?, revision=? WHERE provider_id=?",
                       (new_label, new_enabled, new_endpoint, new_protocol, new_ref,
                        row["revision"] + 1, provider_id))
            return self._provider_record(self._provider_row(db, provider_id))

    def create_model(self, alias, provider_id, upstream_model, display_name, context,
                     capabilities, *, imported_aliases, secrets):
        if not isinstance(provider_id, str) or not provider_id:
            raise ConfigurationError("not_found", "Provider not found.")
        alias = _alias(alias, provider_id, secrets)
        if isinstance(upstream_model, str):
            _check_no_secret(upstream_model, secrets)
            if any(c.isspace() for c in upstream_model):
                raise ConfigurationError("invalid_argument", "Upstream model must not contain whitespace.")
        family = _upstream_family(upstream_model)
        display_name = _label(display_name, secrets)
        context = _context(context)
        capabilities = _capabilities(capabilities)
        with self._transaction() as db:
            self._ensure_tables(db)
            if db.execute("SELECT 1 FROM provider_records WHERE provider_id=?",
                          (provider_id,)).fetchone() is None:
                raise ConfigurationError("not_found", "Provider not found.")
            if alias in imported_aliases:
                raise ConfigurationError("source_conflict",
                                         "Alias belongs to a live imported installation model.")
            try:
                db.execute("INSERT INTO model_records VALUES (?,?,?,?,?,?,?,?,?,?)",
                           (alias, provider_id, upstream_model, display_name, family, context,
                            json.dumps(capabilities), 1, 1, "managed"))
            except sqlite3.IntegrityError:
                raise ConfigurationError("invalid_argument",
                                         "A model with this alias already exists.") from None
            return self._model_record(self._model_row(db, alias))

    def save_model(self, alias, expected_revision, display_name=None, enabled=None,
                   context=None, capabilities=None, *, secrets):
        if type(expected_revision) is not int or expected_revision < 1:
            raise ConfigurationError("invalid_argument",
                                     "Expected revision must be a positive integer.")
        if enabled is not None and not isinstance(enabled, bool):
            raise ConfigurationError("invalid_argument", "Enabled must be a boolean.")
        if not isinstance(alias, str) or not alias:
            raise ConfigurationError("not_found", "Model not found.")
        if display_name is not None:
            display_name = _label(display_name, secrets)
        if context is not None:
            context = _context(context)
        if capabilities is not None:
            capabilities = json.dumps(_capabilities(capabilities))
        with self._transaction() as db:
            self._ensure_tables(db)
            row = self._model_row(db, alias)
            if row["revision"] != expected_revision:
                raise ConfigurationError("revision_conflict", "Model revision changed.")
            db.execute("UPDATE model_records SET display_name=?, enabled=?, context=?, "
                       "capabilities=?, revision=? WHERE alias=?",
                       (row["display_name"] if display_name is None else display_name,
                        row["enabled"] if enabled is None else int(enabled),
                        row["context"] if context is None else context,
                        row["capabilities"] if capabilities is None else capabilities,
                        row["revision"] + 1, alias))
            return self._model_record(self._model_row(db, alias))


def build_resources(config_path, db_path, secrets=()):
    """Merged public resource view: live imported projection overlaid with
    persisted SQLite metadata and managed records."""
    projection = imported_projection(config_path, secrets)
    store = ProviderStore(db_path)
    store.sync_imported(projection)
    with store._transaction() as db:
        store._ensure_tables(db)
        provider_rows = {r["provider_id"]: r for r in db.execute("SELECT * FROM provider_records")}
        model_rows = {r["alias"]: r for r in db.execute("SELECT * FROM model_records")}
    imported_providers = set(projection["providers"])
    imported_models = set(projection["models"])
    managed_providers = {pid for pid, r in provider_rows.items() if r["source"] == "managed"}
    managed_models = {a for a, r in model_rows.items() if r["source"] == "managed"}
    collisions = ((imported_providers & managed_providers)
                  | (imported_models & managed_models))
    if collisions:
        raise ConfigurationError("source_conflict",
                                 "Imported installation resources collide with managed "
                                 "connections; resolve the collision explicitly.")

    providers, models = {}, {}
    for provider_id, record in projection["providers"].items():
        merged = dict(record)
        row = provider_rows.get(provider_id)
        if row is not None and row["source"] == "imported":
            merged.update({"label": row["label"], "enabled": bool(row["enabled"]),
                           "revision": row["revision"], "source": "imported"})
        providers[provider_id] = merged
    for alias, record in projection["models"].items():
        merged = dict(record)
        row = model_rows.get(alias)
        if row is not None and row["source"] == "imported":
            merged.update({"display_name": row["display_name"], "family": row["family"],
                           "context": row["context"],
                           "capabilities": json.loads(row["capabilities"]),
                           "enabled": bool(row["enabled"]), "revision": row["revision"],
                           "source": "imported"})
        models[alias] = merged

    aliases_by_provider = {}
    for alias, row in model_rows.items():
        if row["source"] == "managed":
            aliases_by_provider.setdefault(row["provider_id"], []).append(alias)
    for provider_id, row in provider_rows.items():
        if row["source"] == "managed":
            providers[provider_id] = ProviderStore._provider_record(
                row, aliases_by_provider.get(provider_id, []))
            reference = row["credential_ref"]
            if reference.startswith("existing:") and not projection["providers"].get(reference[9:], {}).get("credential_ref"):
                providers[provider_id].update(available=False, unavailable_reason="credential_reference_unavailable")
    for alias, row in model_rows.items():
        if row["source"] == "managed" and row["provider_id"] in providers:
            models[alias] = ProviderStore._model_record(row)
    # Managed models added under an imported provider stay visible under it.
    for provider_id, aliases in aliases_by_provider.items():
        if provider_id in providers:
            known = providers[provider_id].get("model_aliases", [])
            providers[provider_id]["model_aliases"] = sorted(set(known) | set(aliases))
    return {"providers": providers, "models": models}
