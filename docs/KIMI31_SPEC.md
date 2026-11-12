# Kimi K3.1 support

Source checked 2026-10-29: [Kimi Code model configuration](https://www.kimi.com/code/docs/en/kimi-code/models.html).

## Behavior and acceptance

- Recognize exact Coding Plan IDs `k3.1-pro`, `k3.1-pro-highspeed` and
  public API IDs `kimi-k3.1-pro`, `kimi-k3.1-pro-highspeed`, including
  provider-qualified names. Keep distinct Pro and Highspeed profiles in family
  `kimi`; reject invented suffixes and preserve exact configured account aliases.
- Both profiles support complex coding/reasoning and up to 1,048,576 tokens;
  configured context/capabilities remain authoritative. Highspeed has the same
  task capability with better latency fit and roughly twice Pro's quota use.
  Keep cross-family fit rotation, permissions and quota/capacity rules intact.
- Dispatch managed models with thinking enabled. Imported models use the CLI's
  native `always_thinking` capability or enabled thinking setting; reject a
  disabled setting without that capability before starting inference, with a
  clear configuration error. Do not copy/rewrite installation credentials or
  preferences. Preserve exact upstream IDs on first and resumed turns.
  Recommend a fresh session when moving from older models.
- Existing K3, K2 and GLM routes remain compatible. No automatic account login,
  template permission changes or invented provider entitlement.

## Verification

Independent Kimi-authored process/API acceptance checks must fail on the original
code and pass after the GPT implementation: catalog discovery, managed-model
creation, selection, exact-alias subprocess dispatch, managed thinking on initial
and resumed turns, and rejection of imported disabled thinking. Use fake CLI
subprocesses and temporary state, no new unit tests.
Run existing relevant regression checks and one bounded live connectivity check
for each locally available new model. Record actual access separately from
offline support. Bump the plugin patch version, install locally and preserve a
local commit after validation.
