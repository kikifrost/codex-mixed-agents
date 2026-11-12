# Local policy console (0.6.0)

## Behavior and acceptance

- Kimi unknown-usage inference probe cooldown defaults to 15 minutes. A positive
  integer `probe_cooldown_minutes` is persisted by `set_kimi_policy`. Claims remain
  account-scoped, atomic and durable; changing the interval evaluates existing
  claim timestamps against the new interval. Models and process restarts cannot
  reset claims. Known reserve readings do not probe; shared capacity still applies.
- Automatic routing prefers `kimi-code` then `kimi-for-coding` within eligible
  Kimi choices of the same profile. Account order is configurable. Explicit model
  aliases remain exact, and task fit, context and cross-family authorship remain
  mandatory. Missing, usage-blocked or account-full aliases allow an eligible
  fallback. Unknown usage with an enabled guard remains blocked until an explicit
  or admission bootstrap succeeds. No failed write task is silently replayed.
- Optional per-account concurrency limits default to unlimited. Limits identify
  the configured alias prefix, not a credential; same-credential aliases must
  share the strictest configured limit using the existing account identity.
  Every CLI invocation remains inside the shared CLI limit. Lowering a limit
  preserves running work; queued/follow-up admission checks again. Shared-full
  capacity uses eligible native GPT or waits, even if a backup account is idle.
- `set_kimi_accounts(order, limits)` validates unique nonempty prefix names and
  positive integer/unlimited limits, preserves unrelated configuration, and is
  exposed through MCP. Catalog reports configured order and account capacity.
- A dependency-free local console binds only 127.0.0.1, serves a Chinese policy
  page and supports shared capacity, Kimi guard/cooldown/account order and caps,
  Z.ai capacity/guard, and exact-alias off-peak policy through existing setters.
  Each form saves independently; unrelated settings survive. The page displays
  current effective policies and capacity, plus explicit usage refresh and Kimi
  probe controls. Loading the page never starts inference.
- Console mutations require a per-process random token and the exact loopback
  Origin and Host; reads also reject unexpected Host. Reject oversized or invalid
  JSON bodies, unsupported operations and non-JSON writes. Do not expose saved
  credentials, raw account configuration, arbitrary commands or file paths.
  UI output uses text nodes rather than interpreting provider content as HTML.
- Document a single launch command and keep it usable from the installed plugin
  or an extracted release. Offline tests exercise cooldown boundaries/restarts,
  account fallback and caps, explicit aliases, guard and family constraints,
  persistence, console validation/security and packaging. Implementation and
  tests use different model families. Record live checks separately.
