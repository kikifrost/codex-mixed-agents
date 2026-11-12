# Kimi five-hour reserve and bounded bootstrap

Accepted 2026-10-11. Applies to configured Kimi-family models with HTTPS
`api.kimi.com` Coding origins `/coding` or `/coding/v1`. Other Kimi providers
must report unsupported rather than inherit another account's quota.

## Behavior and acceptance criteria

- Add a separate Kimi usage policy, portable guard and automatic bootstrap
  disabled, default threshold
  **5% remaining**. kikifrost's installations enable it. Keep Z.ai's cap 3 and its
  10% guard unchanged, shared CLI cap 5 and existing cost policy unchanged.
  Validate and persist policy updates without dropping unrelated settings.
- Read actual five-hour limits from the official Kimi Code `/coding/v1/usages`
  endpoint, using the exact selected alias's existing API key or file OAuth
  access token. Read-only reader never refreshes or copies credentials. Expired,
  unsupported credential storage and malformed data are explicitly unknown.
  Never print/store secrets or raw response/error bodies. Fixed HTTPS origin,
  no redirects, bounded response/time, safe error codes and private short-lived
  sanitized cache. Changing accounts invalidates cache and separates budgets;
  same-account aliases share observations and bootstrap allowance.
- Parse only a provable five-hour window (300 minutes/5 hours/18000 seconds),
  including string numerals, `detail.limit`, `used` or `remaining`. Validate
  finite nonnegative coherent numbers; use conservative valid readings and
  never substitute weekly/total/token/context usage for remaining quota.
- Enabled guard checks admissions, idle follow-ups, queued turns and active
  workers using readings at most 30 seconds old. At remaining **<=5%**, block
  new work and stop affected active workers, clear pending messages, preserve
  edits/results/captured session IDs, and expose a quota reason. Other accounts
  and eligible GLM/GPT remain available. Routing and quota checks never silently
  replay partially completed writes.
- Unknown usage permits **one bounded bootstrap inference** before a new idle
  admission, when `probe_on_unknown` is enabled. This is a fixed, tool-free
  one-step response with no inference retries, isolated workspace, short timeout
  and small output bound. It goes through the shared bridge slot accounting,
  including lowering/full/unlimited behavior. It cannot perform the user's
  write task or spawn nested agents. A successful probe is not proof of quota:
  invalidate stale unknown cache, re-read usage, require a readable value >5%
  before the requested real task begins.
- Claim the allowance durably and atomically per account before launching the
  probe; aliases/concurrent callers/server restarts cannot multiply probes.
  Unknown after the probe blocks further inference for that account. Permit a
  new attempt only after the configurable cooldown (default 15 minutes) or changed account identity;
  an observed quota exhaustion/reserve is never eligible for a bootstrap.
  Catalog/selection/doctor never perform inference. Active/queued unknown usage
  stops instead of bootstrapping. CLI's normal OAuth refresh may run during the
  bounded bootstrap; the bridge does not rewrite authentication files itself.
- Expose Kimi policy and per-alias read-only usage through MCP without breaking
  existing no-argument Z.ai `provider_usage()` callers. Provide a strict policy
  setter and an explicit bounded probe tool; caller prompts/timeouts cannot
  turn a probe into arbitrary work. No automatic re-probing loops.
- Tests are authored independently by GPT; Kimi authors helper/runtime/MCP
  production code. Test meaningful boundary, account identity, request safety,
  concurrent probe deduplication, cross-process slots, admission/continuation,
  unknown/reserve interruption and selection eligibility offline. Then do a
  small real exact-alias check above reserve. Do not drain a live account to
  prove stopping. Provider delays/in-flight spending prevent a hard balance
  guarantee at an observed 5% threshold.

## Development prompt wording

The user-facing example should describe the requested feature and acceptance,
then task-fit delegation and independent tests. Development guidance explains
that SDD establishes behavior/acceptance before implementation; scale specs to
the change rather than require ritual documents. Independent test authors derive
cases from the spec and may inspect code for edge cases. Where useful, optional
test-first work records a meaningful failure then a passing clean implementation.
No forced standalone refactor phase; necessary cleanup to meet clarity or
correctness remains allowed. Do not require both authors to write concurrently.

Primary reference: [Kimi Code CLI usage command](https://github.com/MoonshotAI/kimi-cli/blob/main/src/kimi_cli/ui/shell/usage.py).
