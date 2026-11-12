# Template implementation stages

Approved scope: existing Kimi Code agent; template management, Project binding,
Provider/model configuration; preserve Kimi and Z.ai policy and exact aliases;
reserve Zcode/Claude Code adapter seams. Select authors by task fit and actual
access; tests for the same unit have a different model-family author.

## Stages and recovery points

1. **Configuration lifecycle** (this stage): SQLite template versions, Project
   registry/bindings, read-only resource projection, resolution preview and MCP
   management API. No dispatch enforcement, Provider writes, UI or installation.
2. **Resource editing and console**: validated Provider/model configuration and
   provider-specific handlers; real template/Project editor replacing the mock.
   Keep credentials in existing authorized storage; preserve existing policies.
3. **Dispatch integration and release**: enforce template allowlists and per-Project
   admission, persist turn snapshots, verify existing lifecycle/guards, small live
   integration, packaging/install/discovery. No implementation of future agents.

Each stage ends with a local commit, targeted and relevant regression results,
an explicit unverified boundary and resumable next steps. Existing JSON policies
remain their sole authority in stage 1; SQLite owns only new template/Project
data, so no existing policy migration or duplicate writer is introduced.

## Stage 1 behavior

- Initialize new tables in the existing `agents.sqlite3` using idempotent,
  serialized SQLite transactions; preserve legacy tables and `config.json`.
- Exactly one default template (`default`), initialized once from configured
  recognized CLI aliases. Native GPT models are not silently imported as Kimi
  models. Missing/empty CLI configuration produces an empty default plus a clear
  unresolved state, not invented usable models. Refresh does not regrant models.
- Public resource projection contains agents, providers and exact model aliases,
  family/capability metadata only; no API keys, credential files, raw TOML or
  historical errors. Discovery runs no inference, quota read or auth refresh.
- An independent custom template copies a saved source revision; subsequent
  changes to source or custom do not propagate between templates.
- Save accepts a strict versioned payload, validates complete references and
  ownership, rejects unknown fields/types/duplicate agent or model entries and
  unsafe limits. Booleans are not integers. Future agent types are known as
  planned; they may be disabled placeholders but cannot resolve as runnable.
- Save requires expected_revision CAS; a conflict or invalid payload writes
  neither the current pointer nor a partial version. Historical versions remain
  immutable. Changing a label does not change resource identity.
- Default cannot be archived. Bound custom templates cannot be archived silently.
  Unknown/archived template IDs fail clearly. No automatic fallback on an explicit
  invalid binding or missing resource.
- Projects use host+canonical-root identity (normalized and symlink resolved),
  not name; same identity registration is idempotent. Names may change without
  changing identity. Binding NULL means follow default; explicit ID means custom.
  Bind uses expected_binding_revision CAS (new Project starts at 0), and publishes
  only saved templates. Project paths remain internal registry metadata; public
  views report ID, name, host, binding/revision and effective template only.
- Resolution uses current saved template revision. Existing missing resource
  references are reported unavailable and excluded from candidate configuration;
  they are never replaced with unauthorized aliases. It is a configuration
  preview, not proof of credential/usage/model availability or actual enforcement.
- Snapshot preview (snapshot_id supplied to resolve_project_config) persists an
  immutable resolution. Same snapshot ID/project returns the pinned snapshot;
  conflicting Project use fails. No credentials or raw policy values enter it.
  Runtime turns are not wired to this snapshot until stage 3.
- Existing spawn/select/followup/setters and native-family/cap/usage semantics
  remain unchanged for stage 1. Never replay a task to another model.

## Fixed payload and resource contract

```json
{
  "schema_version": 1,
  "agent_configs": [
    {"agent_type": "kimi-code", "enabled": true, "providers": [
      {"provider_id": "kimi-code", "models": ["kimi-code/k3-256k"]},
      {"provider_id": "zai-coding-plan", "models": ["zai-coding-plan/glm-5.3"]}
    ]}
  ],
  "limits": {"max_parallel_per_project": 3},
  "selection": {"prefer_provider_discount_on_equal_fit": false}
}
```

max_parallel_per_project: positive integer 1..100. Template preferences cannot
disable or overwrite global guards. Explicitly saving an empty custom template
(`agent_configs=[]`) or enabling an Agent without models is invalid; default bootstrap alone may be
empty when no resource exists. Planned disabled Agent placeholders have no
providers; enabled planned agents reject until an adapter is implemented.
Disabling a known Agent may retain its valid bindings for later re-enabling;
a template with all Agents disabled resolves with no candidates and ready=false.

Resource projection passed to storage:
`agents: {type: {status}}`, `providers: {id: {id, model_aliases}}`,
`models: {exact_alias: {alias, provider_id, agent_type, family, context, capabilities}}`.
Only recognized Kimi CLI models are projected. `provider_id` is the existing
configured prefix before the first slash. This is a stage-1 connection identity,
not a claim that different prefixes have independent real account quota.

## Storage/module interface

`bridge/configuration.py`: `ConfigurationError(code,message)` and
`ConfigurationStore(db_path)`, initialized via `initialize(resources)`.
Uses its own short SQLite transactions against the shared file; no imports of
runtime, no network/CLI and no access to credentials. Fixed method contracts:

- list_templates()
- get_template(template_id, revision=None)
- create_template(name, source_template_id='default')
- save_template(template_id, payload, expected_revision, name=None, resources=None)
- archive_template(template_id, expected_revision)
- register_project(name, root, host_id='local')
- list_projects()
- bind_project(project_id, template_id, expected_binding_revision)
- resolve_project(project_id, resources, snapshot_id=None)

Template view fields: id, name, revision, payload, is_default, archived.
The store list methods return lists; AgentManager/MCP list methods return object
envelopes `templates` and `projects` for valid MCP structuredContent. Historical
template payloads are immutable; the name is a current label. Pinned snapshots
retain the template name at capture. Archiving changes archived state, not the
payload revision.
Project view: id, name, host_id, template_id (NULL=default), binding_revision.
Resolution: project_id, template_id, template_revision, template_name, payload
(saved), candidates (configured allowed model records), unavailable (alias/reason),
ready (configuration references usable; not live entitlement), snapshot_id optional.
No project root is returned. Expected errors: invalid_argument, not_found,
revision_conflict, template_in_use, default_template_protected,
snapshot_conflict. Errors do not embed caller payloads or private paths.

AgentManager exposes these as list_templates/get_template/create_template/
save_template/archive_template/register_project/list_projects/bind_project_template/
resolve_project_config plus configuration_resources. Wrapper maps errors to
BridgeError, injects fresh local projection for init/save/resolve; schemas use
typed objects/positive integer revisions. Existing model_catalog remains stable.
get_template.revision is optional integer/null (minimum 1); save/archive use
expected_revision (minimum 1), binding uses expected_binding_revision (minimum 0).
resolve_project_config is not annotated read-only because snapshot_id can persist
a snapshot. Catalog resource views include known planned agent types, never
fabricated prefixed model aliases or credential-like alias identities.
Configuration methods are registered through MCP/CLI; console access waits for
stage 2 (keep loopback allowlist unchanged). Tests and packaging include module.

## Acceptance

Independent test authors derive checks for: restart/idempotent initialization;
concurrent initialization and CAS winner/loser; copy independence; historical
revision and atomic validation rollback; default/bound archive protection;
project canonical identity and binding CAS; default/custom resolution;
resource ownership, duplicates, unrecognized references, booleans and limits;
removed aliases without substitution; disabled future placeholders and enabled
future rejection; snapshot pinning/conflict across restart; schema/discovery and
MCP round-trip; no inference/credential leakage; existing lifecycle/policy tests.

Stage 1 completion does not claim GUI, Provider editing, dispatch permission
enforcement, runtime snapshot use, per-Project scheduling or installation.

## Stage 1 continuation record (historical)

Stages 2 and 3 are now implemented; see their separate specs and VALIDATION.md.
The following records the original stage-1 recovery boundary.

Implemented: `bridge/configuration.py`, AgentManager wrappers and ten MCP/CLI
configuration methods. New tables are prefixed `configuration_` and share the
existing database. Existing policies, dispatch paths and console allowlist are
unchanged. GPT implementation / Kimi independent tests; tests cover module
contracts and transport behavior. Validation commands/results are in VALIDATION.md.

Next stage starts from this commit, with no pending schema cutover:

1. Add Provider-specific configuration handlers around existing Kimi/Z.ai policy
   functions. Define exact ownership of credential references, safe write/rollback
   of model configuration, and active-worker account-change restrictions before
   changing the CLI TOML. Retain existing resource IDs/alias compatibility when
   introducing persisted Provider connection records.
2. Add controlled resource-editing APIs and the loopback console allowlist/schema.
   Template methods remain separate from public policy edits; saving a template
   must never rewrite global guards, account capacity or credentials.
3. Replace the design preview with the real three-page editor. Read global
   FRONTEND_DESIGN.md, retain Host/Origin/token/CSP protection, draft preservation,
   CAS conflicts and dependence/impact displays. Loading the UI must not infer or
   probe. Project discovery from the desktop host is still pending; registry API
   currently takes an explicit root and returns a path-free public identity.
4. After that stage's commit, wire template configuration into selection/spawn/
   follow-up and claims with Project context, then enforce per-Project admission
   and capture immutable turn snapshots. Check existing model-family, shared cap,
   per-account cap and reserve guards before each allowed dispatch.
   Define the default-template/global-soft-preference mapping explicitly before
   wiring it: stage 1 seeds prefer_provider_discount_on_equal_fit=false, while
   existing dispatch still uses the original global cost policy. Do not silently
   change existing callers' off-peak preference at the cutover.

Use new scope/work_unit names for later modules and record actual authorship.
The user withdrew the earlier GPT-first preference on 2026-10-13; select by task
fit, eligible capacity and observed access. Keep implementation/test families
independent for each work unit.
Do not install a partly enforced configuration release into the running plugin.
