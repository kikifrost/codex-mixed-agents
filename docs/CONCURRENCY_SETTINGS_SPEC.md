# Visible concurrency settings and zero semantics — 2026-10-14

## Behavior

The console header labels occupancy and concurrency limits explicitly and offers
**编辑并发上限** on every page. The button expands an immediately adjacent panel
containing: total concurrency for this bridge's CLI workers (Kimi and GLM, excluding
native GPT), Z.ai Provider concurrency (all api.z.ai GLM routes), and existing Kimi
account concurrency controls. These controls stay visible regardless of the
selected Provider; no second policy store or duplicate inputs are introduced.
Kimi is not missing a cap: each account already has a configurable cap, default
unlimited. Z.ai retains its previously configured Provider cap, default 3. Explain
these scopes rather than claiming a model-family-wide Z.ai cap or inventing a new
aggregate Kimi cap. Existing quota protections/off-peak preferences retain their
Provider-specific placement and values.

All concurrency inputs use **0 = no limit at this layer**: total bridge concurrency,
Z.ai Provider, Kimi account, and template single-Project concurrency. Positive
integers retain their meaning; negative/fraction/bool/blank input is invalid. Legacy
`unlimited` remains accepted by shared/provider/account MCP APIs and stored policies.
Those APIs also accept numeric zero and normalize to the existing `unlimited`
storage sentinel. Project template concurrency stores 0, with CAS save and normal
template validation; range remains 0–100. Zero never disables quota guards, grants
new models, cancels live tasks or bypasses other layers' caps. Finite lower limits
still keep active workers and block future admissions until space is available.
Saving each concurrency group is independent, drafts survive page/Provider changes,
and no configuration writes happen just by opening the panel. Editing a policy
shows an unsaved state; actions show processing before awaiting their result,
so previous success messages cannot masquerade as the current action's result.

## Acceptance

- Every page has the header edit action; one click exposes shared, Z.ai and Kimi
  account cap controls above normal page content. Header distinguishes occupancy
  from configured maximum, and includes that Z.ai tasks count toward the total.
- Numeric inputs display legacy unlimited as 0, explain 0, and reject invalid
  input without losing draft state. Existing defaults/saved policy are unchanged.
- MCP zero setters return unlimited state and persist safely across restart,
  preserving unrelated configuration and existing account identity/cap semantics.
- Project 0 saves/resolves/snapshots without partial writes; fake-CLI admissions
  bypass only the Project cap. Shared/provider/account and quota constraints still
  apply. Finite Project concurrency keeps previous behavior.
- Independent Kimi tests cover offline validation/storage/admission/schema and
  browser actions/save payloads/draft persistence/320px overflow. Update only old
  zero-invalid assertions made obsolete by the new requirement. Run meaningful
  offline regressions; no real model inference or policy changes for acceptance.
- Install 0.7.2, render the updated local console, verify installed discovery,
  release checks, commit and push. Keep credentials in the existing installation.

## Authorship

Scope `concurrency-ui-20261014`, unit `concurrency-settings`: GPT parent production
implementation, independent eligible Kimi/GLM test author. Production and tests
have disjoint writers. User's explicit 0 semantics override earlier positive-only
concurrency instructions; preserve former unlimited compatibility.
