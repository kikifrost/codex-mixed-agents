# Pre-release product corrections

## Managed-model diagnostics

`doctor()` must inspect the same imported and bridge-managed model resources
available to the application. A readable installation config without a `models`
table is valid when enabled managed models and their credential sources exist.
Do not require users to duplicate console-managed models in TOML.

Acceptance:

- With an empty, readable `config.toml`, a managed model created through the
  public API, an enabled provider and a populated symbolic environment reference,
  doctor reports that model and does not reject the missing imported model table.
- A missing environment reference is reported as a missing credential source;
  setting it restores offline readiness. No credential values are returned.
- A managed `existing:` reference checks the live imported source offline.
- Disabled resources do not establish readiness. Missing/broken installation
  config, unavailable CLI, and an installation with no usable models still report
  actionable errors. Existing imported-model diagnostics remain compatible.
- Diagnostics do not run inference, request usage, refresh authentication or
  materialize temporary credentials. Verify via public API/process acceptance
  checks with synthetic state and the fake CLI, not new unit tests.

## Console feedback

A save/create result must remain visible when the form is below the viewport.
Keep the existing live status region, but pin it within the viewport while
scrolling. Errors must preserve the form draft. Verify the failing create-model
flow in the browser before and after the change. Retain the existing layout;
use a neutral selected surface instead of decorative left-hand selection rails.

## Usage refresh after recovery

After either explicit or automatic bounded Kimi recovery, re-read usage from
the provider before admitting work. A cached unknown result must not mask a
recovered account. Keep the per-account cooldown identity separate from the
credential-based usage cache identity. Ordinary reads retain the existing cache.

Acceptance: using synthetic credentials, a fake CLI process and a controlled
usage transport, first cache an unavailable reading, then recover to a readable
value. Both public explicit-probe and guarded-spawn flows must observe the new
value immediately. A fresh reserve reading still blocks work, and ordinary
reads remain cached. No real provider requests or credentials are needed.

Publication to a new repository remains a separate final step.

## Validation

- GPT implemented the corrections; an independent Kimi author added nine
  public API/process regression scenarios with synthetic state and fake CLI
  processes. No new unit tests or real credential fixtures were added.
- Before implementation: managed diagnostics had four failures and two passing
  compatibility guards; usage refresh had one failure, one error and one passing
  cache/reserve guard. All nine pass after the fixes.
- Full local offline suite: **469 passed** in 121.459 seconds on Python 3.14.5.
  The real local configuration also passed all 13 offline doctor checks.
- Browser reproduction at the bottom of the create-model form: error feedback
  moved from above the viewport to 12 pixels below its top edge, with the form
  preserved. Screenshots are retained in the ignored local validation directory.
- Release checks passed for current files and reachable Git history. The
  corrections remain unreleased; no new remote CI or provider entitlement claim
  is made by these local checks.
