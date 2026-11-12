# Z.ai capacity and usage protection

Status: accepted, 2026-10-11. Supersedes the development-process wording in earlier
specs: SDD is primary. Optional TDD means an observed failing behavior test (red),
then a clean implementation passing it (green), authored by different families.
There is no required refactor phase and no deliberate poor initial code.

## Provider capacity

- Keep the existing shared Kimi CLI cap (currently 5). Add a separate shared
  Z.ai GLM cap, portable default 3, configurable positive integer or unlimited
  through `set_zai_policy(limit=...)`. Persist in the same private config.json;
  preserve concurrency, cost policy and unrelated settings on updates.
- Identify recognized GLM aliases by their configured provider's HTTPS origin
  api.z.ai, not their alias spelling. All matching aliases/accounts share this
  provider cap. GLM from Ark or other origins and Kimi are unaffected.
- Enforce atomic cross-process admission on spawn, idle continuation (including
  switching into Z.ai), and keep a capacity reservation through queued turns.
  Count starting/running/cancelling workers and live orphan children. Lowering
  capacity never cancels running jobs; a failed launch releases admission.
- Catalog exposes provider cap, running and available slots. Automatic routing
  removes blocked Z.ai candidates and considers eligible Kimi/GPT/other providers
  with existing fit and family rules. Explicit blocked dispatch returns a
  distinct provider_capacity_exceeded error; it never silently replays writes.

## Quota

- Add read-only `provider_usage()` for Z.ai, reading credentials only from the
  existing Kimi config. GET only the fixed HTTPS api.z.ai quota endpoint; reject
  redirects, use a bounded timeout/body, return normalized metrics and safe error
  codes only. Never persist/log credentials or raw quota response/error bodies.
- Parse CREDIT_LIMIT and legacy TOKENS_LIMIT five-hour rows (unit=3, number=5).
  Percentage is USED, remaining is 100-used. Prefer precise currentValue/usage
  ratio when present, conservatively use the greater valid percentage if both
  reported. Invalid/missing/nonfinite/out-of-range data is unknown, never zero.
  Distinguish five-hour from weekly rows; reset timestamp is informational.
- `set_zai_policy` also controls `usage_guard` (portable default false) and
  `stop_remaining_percent` (default 10, strictly >5 and <100). Enable the guard
  on kikifrost's installed copies when live reading works. No caller-forged quota.
- With guard enabled: check fresh/recent (at most 30 seconds) usage before
  admitting Z.ai, before every queued turn, and at most every 30 seconds during
  running turns. Share bounded cached metrics across workers, bound requests,
  and invalidate credential/account changes without storing credentials.
- At remaining <= threshold, block Z.ai selection/dispatch and stop active Z.ai
  processes at their next monitor check, clearing pending messages while
  preserving edits, outputs and captured session IDs. Expose a quota-related
  interruption reason. Other families/providers remain available. Do not count
  an intentional quota stop as an access failure. Resume requires fresh quota
  above the threshold; a past reset timestamp alone cannot authorize spending.
- When an enabled guard cannot read trustworthy usage, fail closed for Z.ai
  admissions and active turns with a distinct unavailable reason. Users may
  explicitly disable the guard, but reserve protection is then unavailable.
  Disabled guard does not cause network reads during catalog/selection/workers.
- This protects observable spending with a 10% margin; delayed provider metrics,
  requests already billed/in flight, and untracked clients on the same account
  prevent guaranteeing a hard >5% remaining balance. Never claim otherwise.

## Acceptance

Independent-family offline tests cover boundary/config validation, provider
identification, simultaneous cross-process admission, continuation/queued turns,
capacity release/cancellation/recovery, selection fallback/diversity, quota
parsing, request safety/errors, threshold and active interruption. No network or
real secrets in tests. Existing lifecycle/concurrency suites remain green.
Verify actual usage without inference, then a minimal live GLM-5.3 smoke only
when above the configured reserve threshold. Install locally and Linux validation host,
verify genuine MCP tool discovery, preserve a local commit and shareable ZIP.

The quota reader uses an undocumented internal endpoint. The response shape was
verified against the configured account locally and on Linux validation host. Endpoint
background: [OpenUsage Z.ai implementation notes](https://github.com/robinebers/openusage/blob/main/docs/providers/zai.md).

## Implementation seam

`bridge/zai.py` owns these standard-library helpers, independent of runtime:

- `normalize_policy(raw)` validates a partial object and fills defaults:
  `limit=3`, `usage_guard=false`, `stop_remaining_percent=10`. Limit is a positive
  integer or `"unlimited"`; stored null/unknown/invalid fields raise ValueError.
- `aliases(config_path)` returns the set of recognized GLM aliases whose
  configured provider has HTTPS origin api.z.ai. Do not expose credentials.
- `parse_usage(payload)` returns safe `available`, `used_percent`,
  `remaining_percent`, `reset_at` metrics; unknown values are None with safe
  error code. Optional success/code envelopes must not indicate an error.
- `read_usage(state_dir, config_path, alias=None)` returns those metrics plus
  `observed_at`; alias selects its actual existing account. No alias means the
  most conservative reading across configured Z.ai accounts (any unavailable
  account makes it unknown). Cache sanitized metrics for at most 30 seconds,
  with account-fingerprint invalidation and cross-process single-flight reads.
  All expected config/network/parse failures return unavailable safe metrics.
- `block_reason(usage, policy)` returns None if guard disabled or the trustworthy
  remaining reading is above threshold; otherwise `provider_usage_unavailable`
  or `provider_quota_reserve`. Never expose raw provider messages.

Runtime owns provider reservations, admission, routing, worker stopping and
public methods. Public `catalog.zai_policy` includes `cap`, `running`,
`available`, `unlimited`, `usage_guard`, `stop_remaining_percent`. With the guard
enabled, it may additionally expose normalized usage and blocked reason. The
offline doctor may inspect settings/capacity but must never call quota network.

### Component authorship

The quota helper (`zai.py`, work unit `zai-guard`) is implemented by GLM-5.3;
its HTTP, parsing and identity tests are independently authored by GPT. The
runtime/MCP integration (work unit `zai-runtime`) is implemented by Kimi and
independently tested by GPT. Earlier GLM attempts to integrate the runtime
produced no production changes. Parent GPT edits are documentation and release
metadata only. File boundaries follow the helper/runtime seam above.
