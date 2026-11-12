# Stage 2 — public resources and local console

Status: stage 2 implemented and independently validated, 2026-10-13. Builds on stage 1.
The user withdrew the earlier GPT-first delegation preference. Select authors by
fit, observed access and capacity; implementation and tests remain cross-family.

## Ownership and cutover

SQLite owns bridge-managed Provider/model configuration and imported-resource
metadata. Existing CLI TOML remains the credential/installation source; this
stage never rewrites it, copies secrets, refreshes auth or launches inference on
configuration reads. Import is a live local projection: disappearing CLI aliases
become unavailable, not resurrected from the database. Preserve exact legacy
prefix IDs and model aliases. Existing global policies remain in config.json.

New connections use symbolic `env:VARIABLE_NAME` or `existing:connection_id`
credential references, never credential values/files. Existing references must
resolve to a currently imported connection; do not allow cycles or references to
another managed connection. Shared credential references are not independent
quota/cache pools. Stage 3 will materialize these resources for actual turns and
enforce template permissions. Stage 2 must label this boundary in the UI/API.
This avoids partial CLI-config migrations and affecting current running turns.

## Resource API contract

`configuration_resources` retains agents/providers/models maps. Agents remain
kimi-code supported, zcode/claude-code planned. Providers carry id, label,
provider_type (`kimi`, `zai`, `generic`), endpoint, protocol, credential_ref,
enabled, revision, source (`imported` or `managed`), model_aliases. Model records
retain alias/provider_id/agent_type/family/context/capabilities and add
upstream_model, display_name, enabled, revision, source. Imported connection
type is established from actual configured origin, not alias/family guesses;
ambiguous/incomplete imported details must not invent an endpoint or credential.
Credential values, raw config, OAuth metadata and private paths are never public.
Endpoints with userinfo/query/fragment or secret values must not be exposed.

New methods (object responses, not bare lists):

- create_provider(provider_id, label, provider_type, endpoint, protocol,
  credential_ref): a new immutable ID, revision 1; duplicate fails.
- save_provider(provider_id, expected_revision, label=None, enabled=None,
  endpoint=None, protocol=None, credential_ref=None): CAS, revision increments.
  Imported endpoint/protocol/credential reference remain installation-owned;
  only label and enabled may be edited. Managed identity/type are immutable.
- create_model(alias, provider_id, upstream_model, display_name, context,
  capabilities): exact provider-qualified alias; recognized Kimi/GLM upstream
  profile only for the current adapter, revision 1; no automatic granting.
- save_model(alias, expected_revision, display_name=None, enabled=None,
  context=None, capabilities=None): CAS; identity/provider/upstream immutable.
- get_provider_settings(provider_id): explicit handler kind and policy scope,
  current Kimi/Z.ai settings from existing normalized policy functions, no usage
  request. Generic handler has no invented quota/off-peak policy.

Settings response fields: provider_id, handler (`kimi`/`zai`/`generic`),
policy_scope (explicit description of the existing shared policy). Kimi includes
kimi_policy/kimi_accounts, Z.ai includes zai_policy/cost_policy; generic includes
neither. This response does not create a per-connection policy store.

Known protocols: `openai`, `anthropic`. Managed HTTPS endpoints only, with host
and no embedded credential/query/fragment; special Kimi/Z.ai kinds must match
existing origin validators. IDs use safe nonempty public names, no slashes for
Provider IDs, reserved secret-field names or configured credential substrings.
Alias has provider_id + slash + nonempty suffix. Strict argument types/unknown
fields; context positive int, bounded to 2,000,000; capabilities unique strings
from `thinking`, `always_thinking`, `tool_use`, `image_in`, `video_in`,
`dynamically_loaded_tools`. Bool is not an int. Errors never echo caller content.
All writes use short SQLite transactions; concurrent CAS has one winner, invalid
requests/duplicates/conflicts do not partially mutate state.
Managed kind must agree with actual endpoint origin, including rejecting a
generic declaration at a special origin. A label cannot opt out of future guards.
Live changes to installation-owned origin/protocol/credential reference or model
upstream/family increment that resource's revision, preserving edited labels,
enablement and model metadata. Stale editor saves then fail CAS. Raw credential
rotation is not tracked in public revisions and does not expose secret hashes.
Managed existing: references that lose their imported source report unavailable;
their saved configuration is retained for correction. Managed models under a
missing imported Provider do not appear as usable orphan resources.

Provider-specific policy forms call the existing setters separately, each with
its own Save action and effect label. Do not pretend multiple setter calls are
one atomic publication. Z.ai off-peak form permits only exact currently imported
Z.ai aliases; do not change existing global cost-policy semantics for CLI callers.
UI discloses any existing legacy target outside that Provider. Kimi global guard
and account order/caps retain existing semantics; Z.ai cap/guard are shared across
its eligible accounts. Generic connections have no special-policy form.

Disabling a public resource retains template references and reports unavailable
on resolution; do not substitute other models. Validation may retain known
disabled resources, resolution excludes them. Reads do not re-enable or regrant
resources. Saved snapshots remain immutable. New models in an existing Provider
are not automatically granted to any saved template. Imported-resource metadata
survives restart while disappearance of its underlying CLI alias is observable.

## Console contract

Three navigable pages: 配置模板 / Provider 与模型 / Project 绑定. First page is
template editor with Agent enablement and per-Provider model checkboxes. Future
Agents visibly planned and cannot enable. Copy saved source, rename/save CAS,
archive unused custom templates; show version/default identity and affected
Project count. Draft maps persist across internal navigation and refresh;
explicit discard is required to replace an unsaved draft with current saved data.
CAS conflicts retain edits and offer reload/discard; never overwrite silently.
Before unload warn when dirty. Initial/refresh reads are local-only.

Provider page supports create/edit/disable connection and add/edit/disable models,
separately from template permissions. Shows referencing templates and Projects;
type-specific Kimi/Z.ai/global forms reuse existing policy methods. Credential
input is a reference, clearly labeled, with no secret-value entry. Existing
installation fields are read-only. Resource drafts survive page switching.
Separate global-policy save feedback from resource/template save feedback.
Shared concurrency remains editable on this page.

Project page registers an explicit name/root/host, lists public identities only,
binds saved custom template or follows default with binding CAS. Shows effective
template/revision and preview readiness/unavailable references. Edited selection
is not published until Apply. No fabricated desktop discovery or live entitlement.

Retain loopback binding, exact Host/Origin/token checks, strict method allowlist,
body bounds, nonces/CSP, sanitized errors. Add only configuration methods to
console allowlist; no spawn/shell/worker output. Render user strings with DOM
textContent/value, no unsanitized HTML. Support keyboard and 320px width; follow
FRONTEND_DESIGN.md. Explain once that templates/resources become runtime policy
in stage 3; existing global controls already affect subsequent admission.

## Acceptance and authorship

Independent tests cover persistence/import drift/exact aliases, missing/disabled
references, new resource isolation from permissions, strict validation/secret
exclusion, CAS/atomic rollback, special-origin classification and policy scope,
no inference/auth/usage on console loading, MCP schema/discovery, authenticated
HTTP round trips and denied dispatch. Browser checks cover template copy/save,
draft preservation/conflict recovery, Provider/model CRUD, type-specific forms,
Project binding/apply and narrow-screen overflow. Existing lifecycle and policy
tests must pass. Use synthetic configurations/temporary state for mutations;
read-only local smoke may inspect actual public aliases. Do not install this
partly enforced release. Finish this stage with validation, commit and push.

Scope `templates-stage2-20261013`: implementation and acceptance authors remain
different model families for each work unit.

### Completed recovery and authorship

After the user renewed the local Kimi login, independent GLM CLI work resumed
within the existing usage guards and shared capacity. No credentials were copied
and no guard was disabled. A Kimi implementation turn produced partial Provider
storage/integration code; GPT completed and repaired that implementation. GPT
implemented the console. GLM-5.3 independently authored the delivered resource,
API, HTTP and browser acceptance suites from this spec. Earlier Kimi/GPT test
drafts were preserved as private recovery references and are not delivered tests.

Bridge scope: `templates-stage2-20261013`. Final resource unit:
`resource-backend-final` (Kimi partial + GPT implementation; GLM tests). Console
unit: `console-interface` (GPT implementation; GLM tests). Actual authorship is
recorded; family independence is preserved. Validation evidence and remaining
boundaries are in `docs/VALIDATION.md`.

### Stage 3 continuation

1. Materialize managed resources for actual Kimi CLI turns using symbolic
   credential sources, with no copied secrets. Preserve physical account/pool
   identity and existing Kimi/Z.ai guards across aliases and connections.
2. Apply effective saved Project templates to selection, spawn and follow-up;
   enforce permissions and per-Project capacity. Pin immutable turn snapshots
   so edits cannot silently change an active turn's configuration.
3. Define legacy/default/global-policy mapping and native GPT permissions
   explicitly. Keep Zcode/Claude Code adapters planned until implemented and
   independently verified.
4. Version/package/install the complete cutover, verify installed MCP discovery,
   lifecycle/concurrency recovery and a bounded live integration check.

Stage 2 does not replace the installed plugin or modify current dispatch behavior.
A managed/imported identity collision fails with `source_conflict`; resolve the
conflicting CLI installation identity before discovery can proceed. No automatic
renaming or credential substitution is performed.
