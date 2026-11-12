# Stage 3 — dispatch integration and release

Status: implemented and independently validated, 2026-10-14.
Stages 1–2 are committed; this contract was defined on 2026-10-13. Existing config.json policies remain authoritative.

## Project and permission context

Kimi Code dispatch always uses a saved template: default when no registered
Project matches, otherwise that Project's saved binding. Add optional project_id
and cwd to select_model; optional project_id to spawn_agent. Spawn cwd must exist.
Explicit Project IDs must exist on host_id=local and contain the canonical cwd;
no mismatched root, unknown ID, archived binding or silent fallback. With cwd and
no explicit ID, the longest registered local ancestor wins (symlinks canonical).
Selection without cwd/project_id uses default; its result is advisory, spawn
checks the actual cwd again. Unregistered cwd uses default with no Project cap.
No automatic Project creation or root disclosure in responses.

Exact enabled, available Kimi Code aliases are the sole CLI allowlist. Selection
filters BEFORE task fit, guards, account order, diversity and global capacity;
managed aliases use recognized catalog profiles with stored context/capabilities.
Explicit denied spawn/follow-up fails model_not_allowed before inference/quota
or acquiring a slot. Missing/disabled records never substitute another alias.
Both default and custom templates gate Kimi Code. A partial missing template can
still run its explicitly valid candidates; an empty allowlist cannot run CLI.

Native GPT is the existing host-orchestrator workflow, outside Kimi Code templates.
A Project-context call must not use native GPT as a silent fallback: exclude it
from Project selection and reject native spawn with native_project_unsupported.
Without Project context, the existing native reservation/registration path remains
available and clearly outside template enforcement. Future native-Agent policy
requires an explicit supported adapter/schema; do not fabricate one now.

The default template preserves legacy global off-peak preference semantics.
Custom templates enable the global eligible equal-fit discount only when their
prefer_provider_discount_on_equal_fit flag is true; it cannot override global
policy enablement, eligibility, family independence or explicit model requests.

## Admission, turns and immutable records

Registered Projects enforce max_parallel_per_project in the SAME SQLite admission
transaction as family/provider/account checks and shared-slot acquisition. Count
live held CLI slots for starting/running/cancelling/orphaned Project workers;
stale rows with free locks do not consume capacity. Different Projects are
independent; shared/provider/account caps still apply. Lowering limits preserves
active turns, blocks new admission, and rechecks queued turns at their boundary.
Use project_capacity_exceeded with path-free metadata. Synchronous detached-worker launch failure rolls back
new job/claim/queue/snapshot state. An asynchronous child launch/prepare failure
retains the already-returned durable job ID with sanitized failed status, removes
its non-started turn snapshot and queue, and releases capacity; no phantom Project
occupancy or silently disappearing returned IDs.

Persist a secret-free immutable snapshot for each actual CLI turn (agent ID,
turn number, Project/binding/template IDs/revisions, saved payload, selected
model/provider records and resource revisions). Public get_agent includes current
project_id and configuration snapshot; never a registry root, env value or raw CLI TOML. Worker-authored coding output
may intentionally reference workspace files; internal diagnostic config/log/state
paths are redacted from error fields.
Legacy rows migrate idempotently and get a fresh context when resumed. Restart
keeps snapshots. Active turns retain their snapshot; template/resource/binding
edits apply only to future turns. Idle follow-up uses the original cwd/Project,
validates current permission and family before usage/capacity and gets a fresh
snapshot. Active enqueue validates permission; the worker checks it again before
executing the queued turn. No continuation if the source alias disappeared.

## Credential-safe Kimi adapter materialization

New module bridge/materialization.py owns the bounded CLI adapter seam. Never
rewrite installation TOML or persist/copy credential values. Compose an ephemeral
0600 config.toml for a selected managed model and connection. Its credentials are
symbolic env refs, or live existing imported-provider refs resolved internally.
Raw API key values, if needed by the child, travel only in its process environment;
OAuth uses the existing installation credential storage, without copied tokens.
Reuse original session storage for captured-session continuation. Temporary
configuration is scoped to one turn and removed on success, failure/cancellation.
Only recognized Kimi/GLM profiles/protocols are supported; fail clearly if the
installed CLI cannot materialize a credential safely.

Pin public connection/model identity and revisions in the turn snapshot; compare
live installation-owned origin/protocol/upstream before execution, fail on drift
rather than redirect. Credential rotation at the SAME source is permitted without
serializing credentials. Existing credential references must retain actual origin
and guard/account identity; they cannot hide Kimi/Z.ai behind a generic connection.
Every managed alias obeys the existing actual-origin provider guard and shared
account cap. Never infer independent quota/cache from connection IDs. No guards,
quota requests or auth refresh on console/configuration reads.

Materializer interface (Kimi author owns this module only):
- MaterializationError(code, message), sanitized messages only.
- describe(config_path, model_record, provider_record): secret-free descriptor
  with source alias/provider identity, guard kind and public pinned records;
  validates live credential reference/origin compatibility, no inference.
- prepare(config_path, descriptor, state_dir): context manager yielding
  {config_path, env, model}; returns original config/env for imported original
  models when safe (unchanged incomplete imported metadata remains pass-through;
  the CLI decides actual credential availability, with no invented origin); otherwise creates ephemeral config and shared storage links.
- guard_target(config_path, descriptor): {config_path, model}; original alias
  for existing references; managed env refs must support actual-origin guards
  without copied credentials. Stable source account identity for slot accounting.
  Runtime may call prepare for managed env references before quota/admission to
  obtain a secret-free guard view whose env name is already in os.environ; keep
  that context alive through the check, never persist its path in jobs/snapshots.
  Compare account fingerprints against original installation prefixes too, so
  managed env routes cannot evade strict imported account caps.
Describe/prepare must not expose values in repr/errors/logs. APIs may be narrowed
with a documented parent/author agreement before integration, not guessed.

## Acceptance and release

Dispatch implementation: GPT; independent tests: Kimi. Materializer implementation:
Kimi; independent tests: GPT. Scope templates-stage3-20261013; work units dispatch
and materialization. Parallel writers have disjoint files. Parent records actual
production integration authorship. Tests derive from this spec, use temporary
state/synthetic configuration/fake CLI and cover denied inference, longest-root
matching, symlinks, explicit wrong host/root, default/custom discounts, native
boundary, atomic same/different Project concurrency, cap lowering, stale/orphan
locks, launch rollback, active edits/queued rejection/follow-up new snapshots,
restart/legacy migration, managed profiles, same-source guard/account sharing,
credential-free files/DB/responses/errors and cleanup/session continuation.

Run full offline lifecycle/guard/configuration regression, focused -W error,
console browser checks, release/history scan. Perform a bounded live smoke on an
eligible exact Kimi alias, including saved Project binding and follow-up. Package,
bump version, install via supported Codex plugin CLI, verify installed files and
fresh MCP discovery. Preserve user state/policies and verify console runtime label.
Zcode/Claude Code remain planned. Commit and push the completed stage.

## Completion record

GPT implemented dispatch/configuration/server integration and console/release
updates. Kimi independently authored dispatch/API acceptance and maintained old
fixtures for the new permission-error semantics. Kimi implemented materialization;
GPT independently authored its acceptance suite. Authors stayed in disjoint files.
A narrowed same-family test repair preserved the original author's completed
suites; a final Kimi recovery-CLI task fixed the Provider-kind/model-family map
without changing implementation/test-family independence. Actual bridge author
records use the scope and work units above.

Every actual turn pins its selected resource records/revisions and a path-free
adapter descriptor. Existing references select a guardable source alias rather
than the first alias by name. Managed env credentials matching an installed OAuth
account share its original guard/probe target and stable slot identity. Kimi and
Z.ai guard kinds map to model families kimi and glm respectively; unsupported
managed guard/model combinations fail closed. Imported incomplete originals
retain the installation's existing behavior.

Generated single-route TOML includes its own default_model. Existing references
must stay on the credential source's origin; transport type follows the actual
source (including Kimi's vendor protocol), with opaque noncredential type pins
for drift detection. Symbolic env routes use scoped private configuration for
guard reads; neither temporary paths nor credentials enter snapshots.

Validation includes 403 offline tests, 183 focused tests under -W error, 20
browser checks, and live imported Project spawn/follow-up plus managed connection
execution under the existing shared policies. See VALIDATION.md for exact evidence,
installation/discovery status and unverified boundaries. Zcode/Claude Code remain
planned; this release supports Kimi Code execution only. Native GPT remains the
separate host workflow outside Project-scoped Kimi Code templates.
