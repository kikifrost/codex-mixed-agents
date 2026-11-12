# Changelog

## Unreleased

- Include enabled console-managed models and symbolic credential references in
  offline diagnostics; a readable empty installation config is valid for these
  models, without duplicating them in TOML.
- Refresh Kimi usage after bounded recovery instead of reusing a cached unknown
  reading. Keep ordinary caching, account cooldowns and reserve checks.
- Keep console save/create feedback visible while scrolling, preserving drafts
  on errors. Use neutral selection backgrounds in the resource list.

## 0.7.4 — Kimi K3.1 Pro support

- Recognize Coding Plan and public API IDs for K3.1 Pro and Pro Highspeed,
  including provider-qualified identifiers, as distinct Kimi profiles.
- Rank both for complex reasoning and coding, with a higher latency fit for
  Highspeed. Preserve configured context/capabilities, account aliases,
  template permissions and existing capacity/quota rules.
- Enable thinking for managed K3.1 routes; reject imported routes explicitly
  disabling thinking without `always_thinking`. Recommend fresh sessions when
  moving from older models. Document 1M plan limits and Highspeed quota cost.

## 0.7.3 — Kimi unknown-usage recovery guidance

- `model_catalog` and `select_model` expose `kimi_recovery`: one structured
  probe action per affected Kimi account (exact alias, safe `usage_error`,
  fixed `probe_kimi_usage` next action, plain reason) when the usage guard and
  bootstrap probe are enabled and exact per-alias usage is unknown.
- Catalog guidance reads each account individually instead of the aggregate;
  aliases sharing an account deduplicate to one representative (configured
  account order, then routing default). Selection guidance is limited to
  otherwise eligible aliases and also rides the `no_eligible_model` error.
  Known reserve readings and disabled guard/probe yield no recovery action;
  selection also suppresses actions at full shared capacity. Unknown accounts
  stay out of runnable candidates. Managed aliases never cause a probe of a
  template-denied imported source alias.
- Tool descriptions and the Skill document the mandatory recovery preflight:
  run the returned bounded probe once per account, rerun selection, and treat
  cooldown or still-unknown usage as a stop. `credential_expired` is an expired
  local OAuth access token pending CLI refresh, not proof of an unusable
  account.

## 0.7.2 — Visible concurrency controls and zero for unlimited

- Add a header action on every console page for total CLI, Z.ai Provider and Kimi
  account concurrency. Label occupancy and concurrency caps explicitly and explain
  why Z.ai has its own cap while Kimi uses existing account limits.
- Accept 0 as unlimited at each concurrency layer, including a template's Project
  limit. Retain legacy unlimited policy/API values, saved defaults and quota guards.
  Provider selection no longer hides concurrency controls; saves remain independent.

## 0.7.1 — Plugin detail console entry

- Add the native **打开配置控制台** action through the OpenAI structured-settings
  extension. Reading plugin details performs no inference or console startup.
- Start the existing loopback console on a free port when clicked, reuse it for
  repeat clicks, and close it when the MCP process exits. Browser-open failures
  return the local URL for manual opening. Requires a local desktop MCP connection.

## 0.6.0 — Local policy console and Kimi account preferences

- Add a Chinese, loopback-only policy console with independent saves for shared
  capacity, account order/caps, quota guards, probe cooldown and off-peak schedule.
  Credentials stay in the existing CLI installation. No inference on page load.
- Default Kimi probe cooldown to 15 minutes, configurable through MCP or console;
  existing account-scoped durable claims are evaluated against the current interval.
- Prefer kimi-code then kimi-for-coding within eligible Kimi profiles. Optional
  account caps count shared-credential aliases and probes; shared capacity and
  model-family constraints remain mandatory. Explicit aliases remain exact.
- Expose set_kimi_accounts and include account capacity in the model catalog.

## 0.5.1 — Current GLM routing candidates

- Automatically consider only configured GLM 5.3 and 5.3 Flash profiles;
  keep legacy names for explicit dispatch and provider quota/cap accounting.
- Remove legacy-model comparisons from current selection guidance. An old
  off-peak preference cannot restore a hidden legacy candidate.

## 0.5.0 — Kimi reserve and bounded usage bootstrap

- Add exact-account Kimi Code five-hour usage reading and an optional reserve
  guard, default observed remaining threshold 5%, independent of Z.ai's policy.
- Unknown idle admission may use one fixed tool-free inference per account
  per five-hour cooldown, respecting shared capacity, then re-read actual quota.
  Aliases/concurrent callers/restarts share the allowance; unknown after the
  probe blocks real work. Active/queued unknown usage stops without bootstrapping.
- Preserve healthy secondary Kimi account candidates when the usual account is
  blocked, and check authorship constraints before paid bootstrap.
- Add strict policy and bounded probe tools; extend `provider_usage` with optional
  provider/model selection while preserving its no-argument Z.ai behavior.
- Clarify the README prompt: behavior and acceptance first, independent model
  families for tests, optional red-to-green checks and proportionate specs.
  Necessary cleanup is allowed without a mandatory standalone refactor phase.

## 0.4.0 — Z.ai capacity and observed-usage protection

- Add a separate configurable provider cap (default 3) for Z.ai GLM while
  preserving shared CLI capacity and Kimi/GPT routing and family independence.
- Read normalized real five-hour quota from existing credentials through a
  bounded fixed-host GET; add an optional fail-closed reserve guard for new,
  resumed, queued and active turns, default remaining threshold 10%.
- Preserve edits, outputs and sessions on quota interruption; clear pending
  messages and expose explicit reasons. Provider reporting delays and in-flight
  spending prevent a hard reserve guarantee.
- Make SDD primary; optional TDD means observed red-to-green tests and clean
  implementation, with no deliberate poor code or mandatory refactor phase.

## 0.3.1 — Configurable off-peak preference

- Add shared, disabled-by-default `set_cost_policy` with an exact configured
  CLI alias, timezone, same-day peak window and ISO weekdays.
- Prefer the configured alias outside peak only when tied at the highest task
  fit; preserve family independence, context feasibility and shared capacity.
- Preserve normal rotation at peak or with the policy disabled. Explicit model
  dispatch and running sessions remain unchanged. No deferred queue is added.
- Expose the evaluated policy period and whether preference was applied; this
  is a routing assumption, not a billing or account-quota guarantee.

## 0.3.0 — GLM through Kimi CLI

- Recognize configured GLM-4.7, GLM-5, GLM-5-Turbo, GLM-5.2/HighSpeed and
  GLM-5.3/Flash/HighSpeed aliases, preserving provider-qualified names.
- Separate model families (`kimi`, `glm`, `gpt`) from execution backends.
  Kimi and GLM CLI workers share the configured capacity and lifecycle.
- Rotate equal-fit choices across all eligible families. Preserve persistent
  authorship by rejecting cross-family implementation/test session switches.
- Allow TDD when implementation and test authors use different model families.
- Add GLM routing, lifecycle and family-constraint regression coverage.

## Installation follow-up for 0.2.0

- Fix the long-stderr test fixture on Linux by expanding a compact repeat
  specification in the fake CLI, retaining the full 200000-character output.
- Record Linux validation host installation, MCP discovery and 83 passing Linux tests,
  including the missing supported-model configuration boundary.

## 0.2.0 — Shareable beta

- Packaging
  - Bumped all plugin/MCP manifests to `0.2.0`.
  - Added portable `bridge/launch.py` that checks Python >= 3.11 and macOS/Linux
    before importing the server, with clear stderr errors on unsupported hosts.
    The launcher adds its own directory to `sys.path` so it can be imported from
    an unrelated working directory or a relocated package.
  - Native MCP manifests now use `python3` from PATH and call
    `./bridge/launch.py` with relative cwd resolution.
  - Portable root `mcp.json` is the canonical MCP server definition and uses
    only the fields the portable loader accepts (`type`, `command`, `args`,
    `cwd`), launching `./bridge/launch.py` with `cwd: "./"` and `python3` from
    PATH. The native `.codex-plugin/.mcp.json` overlay retains its additional
    timeout keys for the installed Codex loader.
- Documentation
  - Rewrote README with fresh-environment setup, login/account boundaries,
    model aliases, self-check, capacity/timeout/quota distinctions, data and
    permissions, log pruning, upgrades and uninstall steps.
  - Added MIT LICENSE, SECURITY.md and this CHANGELOG.
  - Removed required absolute personal-machine paths from project guidance;
    AGENTS.md now describes optional local/shared guidance only.
- CI
  - Added offline macOS/Linux Python 3.11/3.12 matrix that runs
    `python3 -m unittest discover` and `scripts/check_release.py`.
- Runtime (parent implementation)
  - Added offline `doctor()` and preview-first terminal log pruning.
  - First and resumed Kimi launches use the stored config directory through
    child-local `KIMI_CODE_HOME`.
  - Redact configured credentials and common token/header/assignment formats
    in event logs, results and errors, including JSON keys.
- Models
  - Recognize exact provider-qualified K2.7/K2.8 Preview identifiers while
    preserving distinct Coding HighSpeed profiles and exact account aliases.
- Release
  - Added manifest, credential-pattern and Git-history checks, plus a
    whitelisted reproducible ZIP builder. Scanning remains heuristic.
