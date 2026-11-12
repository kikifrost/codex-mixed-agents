"""Newline JSON-RPC MCP transport and JSON-only recovery CLI."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from functools import partial
import inspect
import json
import sys
import threading

try:
    from .runtime import AgentManager, BridgeError, catalog, providers, security
    from .version import VERSION
    from . import plugin_settings
except ImportError:
    from runtime import AgentManager, BridgeError, catalog, providers, security
    from version import VERSION
    import plugin_settings

METHODS = (
    "model_catalog", "select_model", "spawn_agent", "register_native_agent",
    "record_authorship", "release_native_reservation", "get_agent", "list_agents",
    "wait_agent", "send_message", "followup_task", "interrupt_agent", "set_concurrency", "set_cost_policy",
    "set_zai_policy", "set_kimi_policy", "set_kimi_accounts", "provider_usage", "probe_kimi_usage", "doctor", "prune_logs",
    "configuration_resources", "list_templates", "get_template", "create_template",
    "save_template", "archive_template", "register_project", "list_projects",
    "bind_project_template", "resolve_project_config",
    "create_provider", "save_provider", "create_model", "save_model", "get_provider_settings",
    "read_plugin_settings", "update_plugin_settings", "open_console",
)
DESCRIPTIONS = {
    "configuration_resources": "Read local configured Kimi Code Provider/model resources and planned Agent types. No inference, quota or auth reads; no credentials.",
    "list_templates": "List saved template configurations, versions and archive state.",
    "get_template": "Read a saved template at the current or specified payload revision.",
    "create_template": "Create an independent custom template from a saved source template.",
    "save_template": "Atomically publish a validated template payload using expected_revision concurrency control. Applies to future CLI turns without changing global policy.",
    "archive_template": "Archive an unused custom template. Default and bound templates are protected.",
    "register_project": "Register a Project by host and canonical root; return its public identity and binding, without paths.",
    "list_projects": "List registered Project identities and saved template bindings without paths.",
    "bind_project_template": "Publish a Project binding with expected_binding_revision. A null template_id follows default; only saved templates are accepted.",
    "resolve_project_config": "Preview the saved Project configuration and configured allowed models. An optional snapshot_id pins the resolution; preview does not prove live entitlement.",
    "create_provider": "Create a managed Provider connection (SQLite only): immutable identity/type, revision 1, symbolic env:VARIABLE or existing:imported_id credential reference. Managed HTTPS endpoints only; special Kimi/Z.ai kinds must match the actual endpoint origin and a generic kind is rejected there. Never writes the CLI TOML or copies credentials.",
    "save_provider": "CAS-update a Provider connection (expected_revision). Managed records allow label/enabled/endpoint/protocol/credential_ref edits; imported records allow only label and enabled because endpoint/protocol/credential reference remain installation-owned. No partial mutation on invalid input.",
    "create_model": "Create a managed model under an existing Provider: exact provider-qualified alias, recognized Kimi/GLM upstream profile for the current adapter, strict positive context within 2,000,000, unique whitelisted capabilities. Nothing is granted to saved templates automatically.",
    "save_model": "CAS-update a managed or imported model (expected_revision): display_name/enabled/context/capabilities only; alias, provider and upstream identity are immutable.",
    "get_provider_settings": "Read a Provider's explicit handler kind (kimi/zai/generic) and policy scope from the existing normalized shared policy views with no usage request. Kimi includes kimi_policy/kimi_accounts; Z.ai includes zai_policy/cost_policy; generic includes neither. This does not create a per-connection policy store.",
    "set_kimi_accounts": "Persist Kimi account prefix order and per-account nonnegative integer (0 = unlimited) or unlimited concurrency caps. Identical credentials share the strictest cap; shared CLI capacity always applies.",
    "doctor": "Run bounded offline dependency/config/model checks without inference, auth changes or quota claims.",
    "prune_logs": "Preview (default) or remove old bridge logs for terminal idle CLI jobs; retains jobs, claims and CLI sessions.",
    "model_catalog": "Read configured Kimi/GLM profiles and supplied available native GPT choices; no secrets. With the Kimi usage guard and bootstrap probe enabled, kimi_recovery lists one bounded probe action per Kimi account whose exact per-alias usage reading is unknown; historical observations stay advisory only.",
    "select_model": "Select saved-template allowed aliases by task fit, capacity and family independence; advisory. Optional cwd/project_id resolves local Project context; native GPT is outside templates and excluded for Projects. An enabled off-peak cost preference can prefer one exact tied CLI alias without overriding fit, family or capacity. Otherwise-eligible configured Kimi aliases with unknown usage stay out of ranked candidates and are returned as kimi_recovery probe actions (one per account); a no_eligible_model error carries the same guidance.",
    "spawn_agent": "Start a detached Kimi CLI worker using Kimi or GLM, or return a native GPT spawn action. Enforce saved CLI template permissions and atomic Project/shared concurrency. Native GPT is unsupported in Project context.",
    "set_concurrency": "Persist shared Kimi CLI concurrency (Kimi and GLM): nonnegative integer (0 = unlimited) or unlimited. Existing jobs keep running.",
    "set_cost_policy": "Persist the optional off-peak cost preference for one exact configured CLI alias (disabled by default): IANA timezone, strict same-day HH:MM window, ISO weekdays. No account probing, no forged clock, capacity unchanged.",
    "set_zai_policy": "Persist the Z.ai GLM provider policy without touching shared concurrency, cost policy or other settings: provider cap (nonnegative integer, 0 or 'unlimited' = no cap, portable default 3), optional five-hour usage guard and stop_remaining_percent reserve (finite, strictly >5 and <100). Lowering the cap never cancels running jobs; an enabled usage guard may interrupt active Z.ai turns at the next monitor check.",
    "set_kimi_policy": "Persist the Kimi Code five-hour usage policy without touching shared concurrency, cost policy, Z.ai policy or other settings: optional five-hour usage guard, stop_remaining_percent reserve (finite, at least 5 and below 100) optional bounded bootstrap probe on unknown usage, and a positive integer probe_cooldown_minutes (default 15). Provider delays/in-flight spending prevent a hard reserve guarantee.",
    "probe_kimi_usage": "Run one explicit bounded bootstrap probe for an exact configured Kimi alias when usage is unknown. Prompt, profile and bounds are fixed; cooldown follows the persisted Kimi policy; the caller only selects the account. The result reports probed/reason/usage: a known reading (including a reserve reading) never probes, and probe_cooldown or still-unknown usage means stop for that account until the cooldown elapses. usage error credential_expired means an expired local OAuth access token — usage is unknown pending CLI refresh, not proof of invalid login, an expired subscription or exhausted quota.",
    "provider_usage": "Read-only sanitized Z.ai or Kimi five-hour quota metrics (used/remaining percent, reset time, safe error codes only) for the configured account(s); never exposes credentials or raw provider bodies. Safe code credential_expired marks an expired local OAuth access token — usage is unknown pending CLI refresh, not proof of an unusable account.",
    "register_native_agent": "Register a genuine native GPT agent ID after executing the returned action.",
    "record_authorship": "Record actual parent/other authorship for implementation/test family independence.",
    "release_native_reservation": "Release a pending reservation only when native spawning failed.",
    "get_agent": "Inspect compact durable progress/results; native IDs return a native inspection action.",
    "list_agents": "List persisted agents and pending native reservations, plus live shared CLI capacity.",
    "wait_agent": "Wait at most 60 seconds for a CLI result; native targets return native wait actions.",
    "send_message": "Queue a CLI message at the next turn boundary; idle agents do not start.",
    "followup_task": "Continue a CLI session, optionally switching its exact model alias while idle. Implementation/test authorship cannot switch families; native IDs return native actions.",
    "interrupt_agent": "Cancel queued messages and stop an active CLI process group; does not undo edits.",
}
ARRAYS = {"native_models", "agent_ids", "order", "capabilities"}
INT_ARRAYS = {"peak_weekdays"}
INTS = {"context_tokens", "timeout_seconds", "timeout_ms", "older_than_days", "probe_cooldown_minutes",
        "revision", "expected_revision", "expected_binding_revision", "context"}
NUMBERS = {"stop_remaining_percent"}
BOOLEANS = {"latency_sensitive", "dry_run", "enabled", "usage_guard", "probe_on_unknown"}

TEMPLATE_PAYLOAD_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["schema_version", "agent_configs", "limits", "selection"],
    "properties": {
        "schema_version": {"type": "integer", "const": 1},
        "agent_configs": {"type": "array", "minItems": 1, "items": {
            "type": "object", "additionalProperties": False,
            "required": ["agent_type", "enabled", "providers"],
            "properties": {
                "agent_type": {"type": "string", "enum": ["kimi-code", "zcode", "claude-code"]},
                "enabled": {"type": "boolean"},
                "providers": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["provider_id", "models"], "properties": {
                        "provider_id": {"type": "string", "minLength": 1},
                        "models": {"type": "array", "minItems": 1, "uniqueItems": True,
                                   "items": {"type": "string", "minLength": 1}},
                    },
                }},
            },
        }},
        "limits": {"type": "object", "additionalProperties": False,
                   "required": ["max_parallel_per_project"], "properties": {
                       "max_parallel_per_project": {"type": "integer", "minimum": 0, "maximum": 100,
                                                    "description": "0 removes only the Project concurrency cap; other caps still apply."}}},
        "selection": {"type": "object", "additionalProperties": False,
                      "required": ["prefer_provider_discount_on_equal_fit"], "properties": {
                          "prefer_provider_discount_on_equal_fit": {"type": "boolean"}}},
    },
}

READ_ONLY = {"model_catalog", "list_agents", "get_agent", "wait_agent", "doctor", "provider_usage",
             "configuration_resources", "list_templates", "get_template", "list_projects",
             "get_provider_settings"}


def tool_schema(name):
    if name in plugin_settings.TOOLS:
        return plugin_settings.tool_schema(name)
    properties, required = {}, []
    for parameter in inspect.signature(getattr(AgentManager, name)).parameters.values():
        key = parameter.name
        if key == "self":
            continue
        item = ({"type": "array", "items": {"type": "string"}} if key in ARRAYS else
                {"type": "array", "items": {"type": "integer", "minimum": 1, "maximum": 7}} if key in INT_ARRAYS else
                {"type": "integer" if key in INTS else "number" if key in NUMBERS else "boolean" if key in BOOLEANS else "string"})
        if parameter.default is None and key not in ARRAYS:
            item["type"] = [item["type"], "null"]
        elif parameter.default is not inspect.Parameter.empty and parameter.default is not None:
            item["default"] = parameter.default
        if parameter.default is inspect.Parameter.empty:
            required.append(key)
        if key == "role":
            item["enum"] = ["implementation", "tests", "review", "exploration", "research"]
        if key == "complexity":
            item["enum"] = ["simple", "standard", "complex"]
        if key in {"family", "avoid_family"}:
            item["enum"] = list(catalog.FAMILIES)
            if key == "avoid_family":
                item["enum"].append(None)
        if key == "stop_remaining_percent":
            if name == "set_kimi_policy":
                item["minimum"] = 5
            else:
                item["exclusiveMinimum"] = 5
            item["exclusiveMaximum"] = 100
        if key == "probe_cooldown_minutes":
            item["minimum"] = 1
        if key == "limits":
            item = {"type": "object", "additionalProperties": {"oneOf": [{"type": "integer", "minimum": 0}, {"type": "string", "enum": ["unlimited"]}]}}
        if key == "limit":
            item = {"type": ["integer", "string"], "oneOf": [
                {"type": "integer", "minimum": 0}, {"type": "string", "enum": ["unlimited"]}],
                    "description": "0 or unlimited removes this concurrency cap; other caps and quota guards remain."}
        if key == "payload":
            item = TEMPLATE_PAYLOAD_SCHEMA
        if key == "context":
            item.update(minimum=1, maximum=providers.MAX_CONTEXT)
        if key == "capabilities":
            item = {"type": ["array", "null"] if parameter.default is None else "array",
                    "items": {"type": "string", "enum": list(providers.CAPABILITY_WHITELIST)},
                    "uniqueItems": True}
        if key in {"provider_type", "protocol"}:
            item["enum"] = list(providers.PROVIDER_TYPES if key == "provider_type" else providers.KNOWN_PROTOCOLS)
            if parameter.default is None:
                item["enum"].append(None)
        if key in {"revision", "expected_revision", "expected_binding_revision"}:
            item["minimum"] = 0 if key == "expected_binding_revision" else 1
        if name == "bind_project_template" and key == "template_id":
            item["type"] = ["string", "null"]
        properties[key] = item
    return {"name": name, "description": DESCRIPTIONS[name], "annotations": {
        "readOnlyHint": name in READ_ONLY,
        "destructiveHint": name in {"interrupt_agent", "prune_logs", "archive_template"}, "openWorldHint": name in {"model_catalog", "select_model", "spawn_agent", "followup_task", "provider_usage", "probe_kimi_usage"}}, "inputSchema": {
        "type": "object", "properties": properties, "required": required, "additionalProperties": False}}


def invoke(manager, name, arguments):
    if name not in METHODS:
        raise BridgeError("invalid_argument", "Unknown tool", tool=name)
    if not isinstance(arguments, dict):
        raise BridgeError("invalid_argument", "arguments must be an object")
    method = partial(plugin_settings.TOOLS[name], manager) if name in plugin_settings.TOOLS else getattr(manager, name)
    try:
        inspect.signature(method).bind(**arguments)
    except TypeError as exc:
        raise BridgeError("invalid_argument", str(exc)) from exc
    return security.sanitize(method(**arguments), security.configured_secrets(manager.config))


def handle(manager, request):
    if not isinstance(request, dict) or request.get("jsonrpc") != "2.0" or not isinstance(request.get("method"), str):
        return {"jsonrpc": "2.0", "id": request.get("id") if isinstance(request, dict) else None,
                "error": {"code": -32600, "message": "Invalid Request"}}
    if "id" not in request:
        return None
    identifier = request["id"]
    response = {"jsonrpc": "2.0", "id": identifier}
    method, params = request["method"], request.get("params", {})
    if not isinstance(params, dict):
        response["error"] = {"code": -32602, "message": "params must be an object"}
        return response
    if method == "initialize":
        version = params.get("protocolVersion", "2024-11-05")
        if version not in {"2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"}:
            version = "2025-06-18"
        extensions = {"openai/settings": plugin_settings.CAPABILITY}
        response["result"] = {"protocolVersion": version, "capabilities": {
                                  "tools": {}, "extensions": extensions, "experimental": extensions},
                              "serverInfo": {"name": "mixed-agents", "version": VERSION}}
    elif method == "ping":
        response["result"] = {}
    elif method == "tools/list":
        response["result"] = {"tools": [tool_schema(n) for n in METHODS]}
    elif method == "tools/call":
        try:
            value = invoke(manager, params.get("name"), params.get("arguments", {}))
            failed = False
        except (BridgeError, ValueError) as exc:
            value = security.sanitize(exc.as_dict() if isinstance(exc, BridgeError) else {"error": "invalid_argument", "message": str(exc)}, security.configured_secrets(manager.config))
            failed = True
        except Exception:
            value = {"error": "internal_error", "message": "Bridge operation failed; inspect local state"}
            failed = True
        response["result"] = {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}],
                              "structuredContent": value, "isError": failed}
    else:
        response["error"] = {"code": -32601, "message": "Method not found"}
    return response


def _serve(manager):
    output_lock = threading.Lock()

    def write(value):
        if value is not None:
            with output_lock:
                sys.stdout.write(json.dumps(value, ensure_ascii=False) + "\n")
                sys.stdout.flush()

    def dispatch(request):
        write(handle(manager, request))

    with ThreadPoolExecutor(max_workers=16) as pool:
        for line in sys.stdin:
            try:
                request = json.loads(line)
            except ValueError:
                write({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
                continue
            pool.submit(dispatch, request)


def serve(manager):
    try:
        _serve(manager)
    finally:
        plugin_settings.stop_console(manager)


def main():
    manager = AgentManager()
    if len(sys.argv) > 1:
        if len(sys.argv) != 4 or sys.argv[1] != "call":
            print("Usage: server.py [call TOOL JSON_ARGUMENTS]", file=sys.stderr)
            return 2
        try:
            if sys.argv[2] == "open_console":
                raise BridgeError("console_requires_mcp", "请运行 python3 bridge/console.py --open；插件详情按钮通过持久 MCP 服务启动控制台。")
            result = invoke(manager, sys.argv[2], json.loads(sys.argv[3]))
        except (BridgeError, ValueError) as exc:
            print(json.dumps(security.sanitize(exc.as_dict() if isinstance(exc, BridgeError) else {"error": "invalid_argument", "message": str(exc)}, security.configured_secrets(manager.config))))
            return 1
        print(json.dumps(result, ensure_ascii=False))
        return 0
    serve(manager)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
