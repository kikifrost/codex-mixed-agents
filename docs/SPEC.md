# Mixed agents behavioral specification

## Scope and user requirements

A local Codex plugin combines Kimi CLI agents using Kimi or GLM with native GPT subagents. The
parent coordinates both using the same lifecycle vocabulary. A plugin cannot
replace native collaboration tools or promise native UI representation of Kimi.
GPT dispatch remains native: the bridge returns a `native_action` for the parent
to execute, then the parent registers the real native agent ID. Never fabricate
native agent completion. Do not implement GPT by launching another Codex CLI.

1. Consider Kimi, GLM and GPT together based on task fit, not provider priority.
2. Kimi CLI concurrency, including GLM workers, is configurable across bridge instances, projects and sessions
   sharing one state root. Default 5; accept any positive integer or `unlimited`.
   Read `<state_dir>/config.json` with key `kimi_concurrency` on admission; expose
   `set_concurrency(limit)` for atomic shared updates. Reject invalid values or
   malformed configuration. Missing config defaults to 5. Unlimited is represented
   by `kimi_cap=null`, `kimi_available=null`, `kimi_unlimited=true` in responses.
   Finite overflow recommends eligible native GPT. Count all held slot files,
   including indices above a newly lowered limit. Lowering never cancels existing
   jobs: admit none while running >= limit. Follow-up uses the same current limit.
   Different manager processes must see updates without MCP restart; serialize
   configuration updates and admission using the shared SQLite transaction.
3. For each scope/work_unit, implementation and test authors must be different
   families. `kimi` includes all Kimi variants, `glm` includes all GLM variants,
   and `gpt` includes all GPT variants. Model family is independent of backend:
   Kimi/GLM use `kimi_cli`, GPT uses `codex_native`.
   Different GPT variants do not satisfy diversity. Role-family assignments
   persist across completed turns and are checked atomically before dispatch.
   If GPT implemented a unit and all configured CLI slots are occupied, tests for
   that unit wait; capacity overflow must never override family independence.
4. SDD is primary: behavior and acceptance criteria precede code. Implementation
   and test authors use different families. Optional TDD is red-to-green with a
   clean implementation and no mandatory refactor phase. Test-after development
   is also allowed. Code changes and test changes have separate owners.
   Tests derive from requirements and include negative cases.

## Files and API

Implement standard-library-only Python 3.11+ in
`plugins/mixed-agents/bridge/catalog.py`, `runtime.py`, `server.py`.
Each work unit's implementation and test authors use different model families;
SDD is primary; optional TDD uses red-to-green without a mandatory refactor phase.
Assign disjoint files when delegating. No runtime dependencies, secrets or edits
to the user's Kimi configuration. The only direct provider network request is
the bounded read-only quota GET defined in [ZAI_GUARD_SPEC.md](ZAI_GUARD_SPEC.md).

`runtime.AgentManager(state_dir=None, kimi_command=None, config_path=None)`:
default state is `~/.local/state/codex-mixed-agents`, optional environment
`MIXED_AGENTS_STATE_DIR`. `kimi_command` is a list of executable/argument strings
(supports fake CLI tests); default resolve `kimi`, then `~/.kimi-code/bin/kimi`.
`config_path` defaults to `$KIMI_CODE_HOME/config.toml` or `~/.kimi-code/config.toml`.
Resolve the config directory to an absolute path while preserving the lexical
`config.toml` basename (including symlinks). Validate it before Kimi admission.
Every child receives a child-local `KIMI_CODE_HOME` pointing at that directory;
CLI 2.1.1 has no `--config-file`. Do not mutate the parent environment.
Persist jobs in SQLite with transactions and unique scope/work_unit/role-family
claims. Files at state_dir are private (0700 dir, 0600 records/logs). Read-only
queries must not mutate model configuration or claim routing roles.

Expose methods accepting keyword args and returning JSON-serializable dictionaries:

- `model_catalog(native_models: list[str] = [])`: configured supported Kimi/GLM aliases,
  model display names, context/capabilities, observed successful/failed calls,
  profiles from catalog.py, cap and live slots. GPT names are supplied by parent
  from currently exposed native model choices, not assumed available. Strip all
  credential/provider connection details. Ignore unsupported models (DeepSeek etc).
- `doctor()`: bounded offline OS/Python/CLI version and flag checks, readable
  config, recognized aliases, credential source presence, manifest versions and
  shared capacity. No inference, token refresh or account/quota verification.
- `prune_logs(older_than_days=30, dry_run=True)`: preview by default; delete only
  old terminal Kimi bridge logs under the state root. Exclude live/orphan workers,
  held slots, symlinks and unrelated files; retain metadata, prompts and sessions.
- `set_concurrency(limit: int | str)`: positive integer or `unlimited`; return
  effective cap, running count, available count, unlimited flag and config path.
  Atomic file replacement under the same admission transaction. Persist across
  manager/server restart; preserve any unrelated config keys. No task cancellation.
- `select_model(scope, work_unit, role, native_models, complexity='standard',
  context_tokens=0, latency_sensitive=False, avoid_family=None)`: return candidate
  models and selected model/family, short rationale, and family exclusion.
  Roles: implementation, tests, review, exploration, research. Complexities:
  simple, standard, complex. Apply capabilities/context/family constraints first.
  Resolve equal-fit family ties cyclically across all eligible families, never GPT-first.
  Selection is advisory; dispatch must check constraints/capacity again.
- `spawn_agent(scope, work_unit, role, message, cwd, model,
  timeout_seconds=900, profile='worker')`: validate cwd exists, nonempty strings,
  role and configured model; reject unsupported aliases (no silent fallback).
  GPT model dispatch returns `native_action` (spawn_agent with model/task), not a
  fake job ID, and enforces the family claim before the parent dispatches.
  CLI dispatch acquires a shared hard slot, atomically checks the actual model-family claim, creates
  stable `kimi:<uuid>` agent_id and starts a detached worker; returns promptly.
  `profile` is worker or smoke. profile files are resolved relative to plugin.
- `register_native_agent(scope, work_unit, role, model, agent_id)`: after an actual
  native spawn, record its real id with family gpt and enforce diversity.
  Idempotent exact registration; conflicting identity fails. Represent native
  state as externally managed, never claim to poll native progress itself.
- `record_authorship(scope, work_unit, role, family, author)`: explicitly record
  work already written by the parent or another author; no fake agent dispatch.
  Role is implementation/tests, family is kimi/glm/gpt, author is a real attribution.
- `release_native_reservation(reservation_id)`: GPT spawn returns a distinct
  pending reservation_id. If the native spawn fails before producing an agent,
  release that reservation. Never release confirmed authorship/registered agents.
  Pending reservations are visible and separate from confirmed author records.
- `get_agent(agent_id)`: stable identity, scope, role, model, status, turn number,
  backend, CLI session_id, resumable flag, last assistant result, short progress/error
  tail, queued_message_count. Native results include native_action instructions.
- `list_agents(scope=None)`: same compact records, optional scope filter.
- `wait_agent(agent_ids, timeout_ms=10000)`: bounded 0..60000, return when first
  Kimi target is terminal or timeout; include native wait actions when needed.
  No endless polling or shell busy loops. Keep snapshots bounded.
- `send_message(agent_id, message)`: if Kimi running, queue durably for the next
  turn boundary; if idle queue without starting (native tool semantics).
  Explicitly return delivery='next_turn_boundary' or 'queued_until_followup'.
  Do not claim live interruption/steering that print mode cannot provide.
- `followup_task(agent_id, message, model=None)`: running Kimi queues the follow-up; idle Kimi
  acquires one slot and resumes the SAME Kimi session, consumes old queued
  messages in order followed by the new prompt. No two workers may run the same
  agent/session. Capacity overflow remains actionable, not silently queued.
  GLM follows the same CLI lifecycle. Revalidate configured model/family before
  admission; paired implementation/tests cannot change their persistent family.
- `interrupt_agent(agent_id)`: stop that Kimi agent's active turn and process
  group (TERM then bounded KILL), cancel pending queued messages; idempotent.
  Cancellation status is distinct from failure. Does not undo filesystem edits.
  Worker retains slot until process cleanup finishes. Native ID returns a native
  interrupt action. Never send a signal to an unverified stale/reused PID.

Use a small `BridgeError(code, message)` for machine-readable errors, e.g.
`capacity_exceeded` (include fallback=gpt except blocked diversity),
`family_conflict`, `not_found`, `busy`, `cannot_resume`, `invalid_argument`.
Never silently repeat a failed write task on another model.

## Process lifecycle and concurrency

Use dynamically allocated `fcntl.flock` slot files so the configured cap works
across server processes, including unlimited mode without a hidden numeric cap.
The parent transfers the held lock FD to the detached worker and closes its own
copy. The worker also passes that FD to Kimi so if the supervisor is killed the
still-running Kimi cannot free the slot early. Clean all descendants in the
Kimi process group before releasing a slot, including after normal CLI exit.
Use separate process groups and never shell=True. Do not recycle the slot just
because a database timestamp is old. Tests can share one state_dir across many
processes. Independent state roots are independent caps; production uses one
fixed root. Direct Kimi launches outside the plugin are out of scope.

SQLite records, queued prompts and logs outlive MCP server exit. On a later
manager read, detect a dead worker with no held slot as interrupted, retaining
session_id and outputs. Avoid PID-reuse hazards by verifying process identity
before signalling (e.g. process start identity and/or worker liveness handshake).
No background worker should require an MCP server to stay alive.

Launch first turn with:
`kimi -m ALIAS --agent-file PROFILE -p MESSAGE --output-format stream-json`.
Subsequent turn:
`kimi -m ALIAS -S SESSION_ID -p MESSAGE --output-format stream-json`.
Do not pass --agent-file with -S (Kimi rejects this).

Observed Kimi CLI 2.1.1 JSONL:
`{"role":"meta","type":"system.version","version":"2.1.1"}`
`{"role":"assistant","content":"MIXED_AGENT_READY"}`
`{"role":"meta","type":"session.resume_hint","session_id":"session_UUID",...}`
Capture session.resume_hint. On a first-turn interruption before that hint,
return resumable=false/cannot_resume if no session can be safely identified.
Never resume the latest session in a cwd: parallel tasks would mix histories.
Assistant content can be text or content blocks. Drain both stdout/stderr without
deadlock, write bounded logs, update progress, capture failure/timeout clearly.

Kimi worker profile disables Agent and AgentSwarm and prohibits CLI/MCP delegation.
Smoke profile has tools: []. User code-write scope is communicated in the prompt,
not represented as an OS sandbox. No claim of isolation from a prompt alone.

Credential redaction covers known configured values, common assignments, Bearer
tokens and JWTs before event logs/results/progress/errors leave the bridge.
It is heuristic and does not certify removal of arbitrary private code or data.

## Model policy (capability profiles, not benchmark claims)

Official Kimi model reference checked 2026-10-29:
https://www.kimi.com/code/docs/en/kimi-code/models.html

- k2.7 / kimi-k2.7: regular K2.7, kept separate from Coding HighSpeed.
- k2.8-preview / kimi-k2.8-preview: explicit Preview identifiers.
- k3.1-pro / kimi-k3.1-pro and k3.1-pro-highspeed /
  kimi-k3.1-pro-highspeed: distinct deep-reasoning profiles, up to 1,048,576
  context tokens; thinking required, Highspeed improves latency with about
  twice Pro's quota usage. See [KIMI31_SPEC.md](KIMI31_SPEC.md).
- k3: complex reasoning, deep debugging, cross-module/long-context coding; 1M
  only when plan entitles it. Prefer 256k version when large context not needed.
- k3-256k: same K3 within 256k, ordinary quota lower than full-context K3.
- kimi-for-coding: K2.8 Preview; efficient regular development/code completion,
  supports up to 1M; alias can change model version over time.
- kimi-for-coding-highspeed: K2.7 HighSpeed; latency-sensitive short tasks;
  faster generation consumes more quota, not automatically cheapest.
- GLM support and exact identifiers are specified in [GLM_SPEC.md](GLM_SPEC.md).
  GLM is its own family; all CLI workers share capacity regardless of model.

Read live configured metadata over stale profile context/capabilities. Expose
every configured supported CLI alias, including aliases sharing a remote model but using
different credentials. Explicit dispatch must use the exact requested alias.
Automatic routing may deduplicate remote models (managed aliases are routing
defaults); this must never hide or replace an explicitly selected alias.
Observations are historical per-alias call outcomes, not a current quota check;
never apply one provider's quota failure to another alias. Local concurrency
capacity and remote account quota are separate.

`followup_task(agent_id, message, model=None)` keeps the existing model when
omitted. An idle CLI session can resume with another exact configured CLI
alias, preserving its captured session ID. Reject a different model while active,
before queuing messages or mutating state; let the caller stop/wait first.
Reject native GPT model changes because the native follow-up tool does not
expose a model override. Validate before admission and update claim metadata
with the resumed model. Paired implementation/test agents cannot change model
families; exploration/review may switch CLI families and preserve their session.
GPT profiles should match the parent's supplied native choices: Sol for everyday
implementation/review, Astra for demanding reasoning, Luna for short bounded work.
Do not rank a provider generally superior; fit scores are documented routing
heuristics and overrideable by explicit model. Unknown GPT names remain available
but have neutral fit. Recent failed live calls are disclosed; never claim access
confirmed just because configured. Role pair constraint outranks capacity.

## MCP transport

The portable `mcp.json` uses only schema-defined stdio fields, with cwd `./`.
Native timeouts belong in the compatibility `.mcp.json`, not the portable file.
Installed-host discovery must be checked separately from direct stdio startup.

`python3 bridge/server.py` serves newline-delimited JSON-RPC over stdin/stdout.
Implement initialize (echo supported 2024-11-05/2025-03-26/2025-06-18), initialized
notification, ping, tools/list and tools/call; return proper JSON-RPC errors for
bad input/methods, MCP isError content for BridgeError, no responses to notifications.
Tools have the method names above and valid inputSchema (no silent extra args).
Each call result has `content` text JSON + structuredContent. Native actions are
explicit handoff descriptors. Concurrent tool requests can be processed while a
bounded wait runs; all stdout writes are locked. Keep server logs on stderr.

Provide a CLI mode: `python3 bridge/server.py call TOOL JSON_ARGUMENTS` for manual
operation and test/recovery when MCP isn't loaded. JSON args only, no shell eval.

## Validation coverage

Offline fake Kimi executable exercises real child processes, deterministic JSONL
and sentinel session ids. Cover default-five and configured caps across processes; numeric/unlimited
configuration persistence and lowering; overflow; slot
reuse after completion/cancel; cancelled group descendants; long stderr; timeout;
queued next-turn message; follow-up preserves identity; no duplicate workers;
registry survives MCP shutdown; dead-worker recovery; invalid aliases; family
pair conflicts in both directions/races; capacity cannot violate diversity;
safe catalog output; model routing treats families fairly; MCP discovery/errors.

Small live checks: configured Kimi variants connectivity; one task and follow-up
retain context; start/cancel a tool-free task. A genuine GPT native agent does a
bounded independent workflow evaluation. Report installed plugin and available
tools separately from native UI behavior, which is outside this integration.

## Optional off-peak preference

Release 0.3.1 adds the configurable exact-alias selection preference defined in
[COST_POLICY_SPEC.md](COST_POLICY_SPEC.md). It is disabled by default and never
changes fit scores, explicit dispatch, capacity or persistent family authorship.

## Z.ai provider controls (0.4.0)

[ZAI_GUARD_SPEC.md](ZAI_GUARD_SPEC.md) adds a separate provider concurrency cap
and optional observed-usage guard without changing the shared CLI cap or the
off-peak selection policy. It also supersedes earlier TDD process wording.
