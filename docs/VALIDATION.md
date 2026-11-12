# Validation record

## Kimi K3.1 Pro support 0.7.4 — 2026-10-29

- GPT parent authored the implementation; independent Kimi K3.1 Pro Highspeed
  authored `tests/test_kimi31_runtime.py` (3 process/API integration tests).
  The original source snapshot failed all 3 scenarios (2 failures, 1 error);
  the current implementation passed all 3. These exercise exact Coding Plan,
  public API and provider-qualified IDs, selection, unknown suffix exclusion,
  managed creation and template allowlisting, first/resumed subprocess dispatch
  with thinking enabled, and rejection of imported disabled thinking before CLI
  launch. Temporary state and fake child processes; no new unit tests.
- The existing offline suite passed: **457 tests**, 122.009 seconds. It covers
  lifecycle, concurrency, guards, recovery, model switching and MCP/configuration
  behavior. The 3 new integration tests passed separately in 0.525 seconds.
  Python 3.14 emitted existing SQLite ResourceWarnings without test failures.
- Live tool-free calls through the bridge returned `KIMI31_OK` for both
  `kimi-code/k3.1-pro` and `kimi-code/k3.1-pro-highspeed`, using managed model
  records under the existing `kimi-code` credential reference. Added only these
  two aliases to the local default template; existing provider/account limits
  and quota policies were retained. Highspeed initially waited for shared
  capacity instead of bypassing it. This proves bounded access on this account,
  not a 1M context load, public API account access or throughput benchmarks.
- Installed 0.7.4 from the local marketplace. All **25 plugin files** match
  source; fresh installed stdio initialization reports 0.7.4 and **39 tools**.
  Installed `doctor()` reports ready, and its catalog sees both new profiles.
  An existing chat may retain its previously loaded MCP process; open a fresh
  chat or restart Codex to load the updated server.
- Release checks passed with Git history scanning, followed by a tree scan and
  rebuilt `dist/mixed-agents-0.7.4.zip` including the new acceptance tests.
- Branchline lookup found no existing Mixed Agents task/project, so no progress
  node was changed and no unrelated project was used.

## Kimi recovery preflight 0.7.3 — 2026-10-25

- Spec: `docs/KIMI_RECOVERY_SPEC.md`; scope `kimi-recovery-20261025`, unit
  `recovery`. Kimi production author `kimi:5925bf4d0b474e3ba9936cad78743325`
  completed three turns; GPT parent independently authored
  `tests/test_kimi_recovery.py` and reviewed the implementation. Documentation
  includes contributions from both authors; no GPT-authored production code.
- Reproduced the live failure: the exact `kimi-code/k3-256k` usage reading
  returned `credential_expired`, and selection excluded that account before
  idle-admission bootstrap could run. One existing bounded explicit probe
  restored readable usage at 100% remaining; the same alias then completed the
  real implementation task. No bridge credential-writing logic was added.
- Observed red-to-green regression coverage, including a final managed-template
  case: a permitted managed alias must not suggest a probe using its denied
  imported source alias. All **19 new acceptance tests** pass. Final full offline
  suite: **457 tests passed in 122.412s** on this macOS host. Existing SQLite
  cleanup ResourceWarnings remain; no failures. These checks do not establish
  cross-platform behavior or entitlement to other configured models.
- Installed and enabled **0.7.3** from the local marketplace. All **25 plugin
  files** match source. A fresh installed stdio connection reports 0.7.3 and
  exposes **39 tools**. An isolated synthetic expired-OAuth fixture verifies
  catalog recovery and `no_eligible_model` recovery through actual MCP transport,
  with the exact public probe arguments, no private paths and no probe claim or
  inference. Logs/scripts stay in ignored `.validation/`.
- Catalog remains global diagnostic discovery; only selection's task-scoped
  recovery actions are executed. Selection enforces permissions, family/context
  feasibility and Project/account/shared capacity before suggesting a probe.
  The existing 5% Kimi reserve, 15-minute account cooldown, active/queued fail
  closed behavior and Z.ai 10% policy are preserved. Explicit-probe recovery is
  limited to imported CLI aliases, without a managed-to-source substitution.
- Corrected this host's personal Codex guidance to resolve the installed skill
  from the current catalog instead of a removed 0.6.0 cache path, and added the
  scoped recovery preflight there. Existing chats may retain old MCP schemas;
  open a new chat or reload the connection to consume the installed update.

## Visible concurrency settings 0.7.2 — 2026-10-14

- Spec: `docs/CONCURRENCY_SETTINGS_SPEC.md`; scope `concurrency-ui-20261014`, unit
  `concurrency-settings`. GPT parent production; independent Kimi author
  `kimi:93c1d37494fb4f2aa68caf7f31eb7841` delivered backend/browser tests before
  its turn timed out. No captured session existed. Narrow Kimi recovery author
  `kimi:0e444755978848a5a91baf654f1dc75d` repaired browser constraint-validation
  and async-wait assumptions, preserving test/production family independence.
- Final full offline suite: **438 tests passed in 121.457s**. Covers zero setters,
  legacy `unlimited`, stored numeric-zero compatibility, strict invalid values,
  persistence/restart, unrelated settings/guards, unlimited Project concurrency,
  immutable saved payloads, model allowlists, shared caps and follow-up recovery.
  Existing legacy SQLite/socket cleanup ResourceWarnings remain; no test failed.
- Final Node/Playwright console acceptance: **27 passed, 0 failed, 0 skipped**,
  including header action on all pages, visible shared/Z.ai/Kimi-account controls,
  zero save/display, invalid input with no configuration write, draft preservation,
  independent quota/concurrency saves, and all pages plus expanded panel at 320px
  without horizontal overflow. The narrow test repair passed twice; parent replay
  passed against the final frontend. Browser fixtures use synthetic configuration
  and temporary state, no live account policy or inference.
- Actual in-app browser opened the new console and expanded its controls. Visual
  QA confirms explicit occupancy/cap wording, 0 explanations, correct current
  settings and Kimi account inputs. The prior browser tab with unsaved drafts was
  kept intact. Local screenshots/logs stay under ignored `.validation/`.
- Installed **0.7.2**, enabled; **25 plugin files** match source. Fresh installed
  stdio exposes **39 tools**, reports version 0.7.2 and minimum 0 in shared and
  Project schemas. A zero setter through that transport in an isolated temporary
  installation returns `kimi_unlimited: true`, `kimi_cap: null`, without error.
  Real shared policy values, credentials and quota-guard thresholds were not edited.
- Release checks and deterministic 0.7.2 ZIP pass. Numeric zero removes only its
  own layer; other limits and guards still apply. Kimi remains capped per account
  (default unlimited), Z.ai across its Provider group (existing default 3); no new
  aggregate Kimi cap or policy store. New model entitlement is not tested here.

## Plugin detail console entry 0.7.1 — 2026-10-14

- Spec written first: `docs/PLUGIN_CONSOLE_ENTRY_SPEC.md`. Scope
  `plugin-console-launch-20261014`, unit `plugin-settings`: parent GPT production
  implementation; independent Kimi test author `kimi:294fb677a1f242d2b9bdbf756f0c1062`.
  The cached first dispatch exited before its first turn; workspace recovery used
  the same shared registry/caps and selected eligible Kimi For Coding for tests.
- **425 full offline tests passed in 117.069s**. Existing legacy SQLite cleanup
  ResourceWarnings remain in the full suite. **22 new settings tests pass under
  `-W error`**, including read-only discovery, strict arguments, empty updates,
  free-port HTTP launch, 16 concurrent clicks, reuse, shutdown/reopen, dead-service
  replacement, browser refusal/exception, startup errors and recovery CLI refusal.
  Existing server (6) and console (13) suites also pass. The HTTP security and
  console method allowlist are retained; no new credentials or policy store.
- Refreshed installed **0.7.1**, enabled. Its **25 plugin files** match source.
  Fresh installed stdio negotiates `2025-11-25`, advertises `openai/settings` in
  modern and legacy capability locations, and exposes **39 tools**. Settings read
  returns the native tool-action group and required structured output schema.
- An explicit action on that installed persistent stdio connection started the
  console and successfully requested the OS default browser. Actual loopback GET
  returned HTTP 200 with the existing CSP and Chinese console HTML; real shared
  `config.json` bytes remained unchanged. This check performs no model inference,
  quota request or auth refresh. The console is owned by that MCP connection.
- Official OpenAI structured-settings protocol supports native tool-action
  buttons on plugin details. Computer Use refuses the Codex app, so the actual
  native detail-page render/click is **not verified**. Reload the plugin connection
  or restart the desktop app if its previous discovery is cached. Hosts without
  this extension retain `python3 bridge/console.py --open`; remote/web hosts do
  not acquire access to this machine's loopback console.
- Release history/tree/manifest scan and deterministic 0.7.1 ZIP pass. Private
  validation artifacts and local state remain excluded from the package.

## Project dispatch and configuration release 0.7.0 — 2026-10-14

- Contract defined before code: `docs/TEMPLATE_STAGE3_SPEC.md`. Scope
  `templates-stage3-20261013`: GPT dispatch/integration/console implementation,
  independent Kimi dispatch/API tests; Kimi materialization implementation,
  independent GPT materialization tests. A same-family narrowed repair maintained
  compatibility fixtures; production/test ownership stayed disjoint.
- Final full offline regression: **403 tests passed in 111.776s**, local
  macOS/Python 3.14.5. Includes lifecycle/cancellation/restart/concurrency, template
  permission and cross-process Project admission, immutable actual-turn snapshots,
  active edit/queued checks, default/custom discounts, native boundary, managed
  guard targets, source drift, credential exclusion and adapter cleanup.
  The focused resource/configuration/console/dispatch/materialization suites also
  passed **183 tests in 32.463s with `-W error`**, with no warnings. Legacy full
  suites still emit SQLite cleanup ResourceWarnings; new transactional stores
  explicitly close connections, and the touched legacy runtime fixture now closes.
- Node/Playwright console: **20 passed, 0 failed, 0 skipped**; all three pages at
  320px passed overflow checks. A narrow template screenshot was visually checked.
  Console now labels saved Kimi Code dispatch as enforced; default discount UI
  states that it inherits the existing global policy. No UI network inference on
  load, and existing loopback/Host/Origin/token/CSP/body restrictions remain.
- Real shared-state integration used the exact eligible
  **kimi-code/kimi-for-coding** alias with the existing cap/guard settings:
  saved custom template + temporary Project binding launched one bounded,
  tool-free turn; captured-session follow-up remembered the marker and produced
  turn 2 with a fresh snapshot. Both completed, Project/template identity matched,
  and `config.json` bytes stayed unchanged. Temporary Project removed and template
  archived after completion; durable verification jobs/snapshots remain.
- A second bounded real call used a newly managed Kimi connection/model with an
  `existing:kimi-code` credential reference and an explicitly granting Project
  template. It completed with the expected response, managed resource snapshot
  and Kimi guard. Temporary resources/Project were removed and template archived;
  policies stayed unchanged and ephemeral configuration was cleaned. Its initial
  failure exposed the missing root default_model; the Kimi author fixed it and
  independent GPT coverage plus the live retry passed. No credential values copied.
- Independent adapter tests verified Kimi's real vendor protocol pass-through,
  generic cross-origin credential refusal, exact drift checks, mixed-prefix
  guardable alias selection and Z.ai→GLM family mapping. Normal CLI OAuth refresh
  and readable quota are distinct from a persistent logged-in state; no guard
  was disabled or unknown usage treated as available. Live verification respected
  shared capacity; fake-CLI tests alone used isolated state.
- Reachable-history/tree/manifest scan passed with no findings: 80 files checked;
  deterministic `dist/mixed-agents-0.7.0.zip` contains 78 shareable files. Private
  state, logs, credentials and `.validation/` artifacts are excluded.
- `codex plugin add mixed-agents@kai-mixed-agents --json` refreshed the installed
  0.7.0 package and `plugin list` confirms enabled 0.7.0. All **24 installed plugin
  files** match the tested source byte for byte. A fresh installed stdio MCP
  transport initialized as **0.7.0**, exposed **36 tools** with Project context
  schemas, and completed an isolated fake-CLI Project turn with its snapshot.
  That installed check performs no real inference. Cached transport files were
  refreshed together; workspace recovery CLI used the SAME shared state/caps for
  the final real Kimi author repair when an earlier cached worker exited before
  its first turn. Installed fake lifecycle succeeds after the coherent refresh.
  Installed Z.ai/other model entitlement and remote Linux execution are not
  verified in this release; their guard/routing compatibility is covered offline.
  Zcode/Claude Code are planned adapters. In-flight spending/provider delays remain
  quota-reserve limitations. A chat may retain its earlier MCP schema until the
  desktop host reloads it; fresh transport discovery is checked separately.

## Templates, Provider/model resources and console stage 2 — 2026-10-13

- Spec: `docs/TEMPLATE_STAGE2_SPEC.md`. Kimi contributed partial Provider storage
  and integration code; GPT completed/repaired it and implemented the console.
  GLM-5.3 independently authored delivered resource/API/HTTP/browser acceptance
  tests from the spec. Durable scope `templates-stage2-20261013`, units
  `resource-backend-final` and `console-interface`; actual authorship recorded.
  Earlier Kimi/GPT test drafts remain private recovery references, not acceptance
  suites. CLI recovery followed the user's renewed login; guards/caps stayed on.
- Final local full offline suite: `python3 -m unittest discover -s tests -q`,
  **334 tests passed in 91.299s**, macOS/Python 3.14.5. Existing lifecycle tests
  still emit unclosed SQLite ResourceWarnings; these did not fail the suite.
  The focused resource/API/console/configuration suites also passed **114 tests
  in 12.111s with `-W error`**. Tests cover exact aliases, CAS/rollback/restarts,
  live source drift, missing credentials/resources, managed/imported collisions,
  secret exclusion in JSON/SQLite, permission isolation, local-only loading,
  strict MCP schemas and authenticated HTTP errors/denied dispatch.
- Bundled Node/Playwright executed `tests/test_template_console_browser.js`:
  **20 passed, 0 failed, 0 skipped**. Includes templates/copy/save/CAS/drafts,
  private Project registration/binding/preview, Provider/model create/edit/disable,
  distinct Kimi/Z.ai forms and independent global saves, credential-reference
  storage exclusions, future-Agent disablement and all three pages at 320px.
  Desktop/template and narrow-screen screenshots were visually inspected;
  private screenshots/logs remain ignored under `.validation/`.
- Actual local configuration smoke used a temporary state directory and the
  workspace console/MCP functions: **4 Providers, 18 models, one default template,
  36 workspace MCP tools**. The CLI TOML bytes remained unchanged. No inference,
  authentication refresh, quota lookup or production-policy mutation was requested.
  Configured resources do not establish current model entitlement or quota.
- Reachable-history/tree/manifest release checks and `git diff --check` passed.
  Version remains 0.6.0; this stage is workspace code, not a new installed release.
- Remaining stage 3: actual template permissions in selection/spawn/follow-up,
  safe managed-resource materialization, immutable runtime turn snapshots,
  per-Project capacity, complete version/package/install and installed-MCP/live
  validation. Existing dispatch and Kimi/Z.ai policies retain their behavior.
  Zcode/Claude Code remain planned adapters. Stage 2 intentionally does not rewrite
  CLI installation configuration, migrate credential values or claim separate
  cache/quota pools for connections sharing credentials.

## Template configuration stage 1 — 2026-10-12

- GPT implemented `configuration.py` and the AgentManager/MCP integration;
  Kimi independently authored `test_configuration.py` and
  `test_configuration_api.py`. Native GPT delegation used actual collaboration
  dispatch and bridge registration. Kimi test jobs stayed within shared capacity.
- 56 focused configuration/API checks passed, including serialized initialization,
  CAS save/bind conflicts, ownership and alias validation, copy/version/snapshot
  persistence, no unauthorized substitution, path-free Project views, MCP object
  schemas/discovery and absence of quota/inference calls.
- Final full offline suite: `python3 -m unittest discover -s tests -q` passed 287
  tests in 83.707 seconds on local macOS/Python 3.14.5. ResourceWarnings about
  unclosed sqlite connections were emitted during the full suite and did not
  fail it. The focused 56 configuration/API cases also passed with `-W error`;
  new store transactions and the test count helper close their connections.
- Tree/manifest and reachable-history credential checks (`check_release.py`)
  passed with no findings. This is a bounded scanner, not proof that arbitrary
  private values can never exist.
- Recovery CLI smoke used the actual local Kimi configuration with an isolated
  temporary state directory: 18 recognized configured models, custom template
  creation, Project registration/binding and pinned resolution at revision 1.
  No model inference or production-state mutation was performed. Configured
  candidates do not prove credentials, current quota or actual model access.
- Stage 1 adds configuration lifecycle only. GUI, Provider/model writes,
  template enforcement in dispatch, runtime turn snapshots, per-Project capacity,
  packaging/install and Zcode/Claude Code execution remain unimplemented.


## Local console and Kimi account policy 0.6.0 (2026-10-12)

- Spec: `docs/console-spec.md`. GPT authored production policy/runtime/MCP and
  console; GLM-5.3 independently authored `test_account_policy.py` and
  `test_console.py`, including fixture/default assertion updates. Durable bridge
  scope: `console-20261012`, units `account-policy` and `console`. One GLM turn
  timed out after producing tests; preserved output was inspected and a narrowed
  same-family repair corrected an inconsistent simulated alias reading.
- Local full offline suite: **228 tests, 82.404s, passed**. Linux validation host full suite:
  **228 tests, 88.717s, passed**. Both include existing lifecycle, process capacity,
  recovery and quota guards, plus nine account-policy and eleven console tests.
  Python's existing SQLite ResourceWarnings do not indicate failed checks.
  Three additional GLM-authored boundary checks were then added (probe capacity
  before claim, lowering preserves active work, and follow-up admission). The
  expanded 12-test account-policy suite passed locally and on Linux validation host. No
  production code changed after the complete runs.
- Both Codex-discovered installed MCP transports initialize as **0.6.0**, expose
  **21 tools**, and report shared CLI cap **5**, Z.ai cap **3**, remaining stop
  thresholds **10% / 5%**, cooldown **15 minutes**, and account order
  **kimi-code, kimi-for-coding**. Only GLM-5.3 and GLM-5.3-Flash are automatic GLM
  profiles. Offline doctor reports ready; that is not model entitlement proof.
- All **21 plugin files** match the tested source byte for byte in both installed
  caches. Linux validation host's temporary package path is
  `~/.local/share/codex-mixed-agents/install-tests/0.6.0/package`.
- Installed console loopback HTML/state works on macOS and Linux. Actual browser
  saves persisted the requested cooldown and account order into local shared
  state while preserving shared/provider caps, guard thresholds and cost policy.
  The local page was visually inspected; screenshot is a private validation
  artifact, excluded from the shareable ZIP.
- One explicit local **kimi-code/kimi-for-coding** bootstrap used the fixed
  tool-free bounded profile and subsequently returned a readable five-hour quota.
  No arbitrary coding task or every model's access was verified by that probe.
  Linux validation host exposes an additional configured Kimi account prefix; remote Kimi
  inference/entitlement was not checked. No credentials were transferred.
- Console protects against cross-origin writes and unexpected Host headers,
  strips raw catalog fields and config paths, and stores no credentials. It is
  for trusted local use, not authentication against other local processes.
  Provider reporting delays and in-flight spending remain reserve limitations.

## Earlier releases

- 0.1.0: 20 offline tests passed. Fake child processes checked shared five-slot
  locking, diversity, follow-up/session isolation, cancellation, process-group
  cleanup, server restart and stale-recovery races. Live tool-free K3,
  K3-256K, Coding Preview and Coding HighSpeed checks succeeded on that date.
- 0.1.1: 34 tests passed, adding configurable numeric/unlimited capacity,
  cross-process admission, lowering with occupied higher slots and persistence.
- 0.1.2: 38 tests passed, adding exact provider-qualified aliases and idle
  same-session model switching. One exact custom-provider K3 task and a
  K3-256K follow-up succeeded on that date. Installed MCP exposed 13 tools.

Historical successes and quota failures are per exact alias; neither establishes
another provider's current entitlement or quota.

## Shareable beta 0.2.0 (2026-10-09)

Implementation followed `SHARE_SPEC.md`, then independent model-family tests:
Kimi authored catalog/launcher/packaging; GPT authored catalog/launcher tests.
GPT authored runtime, MCP, diagnostics, redaction and release checking; Kimi
authored the corresponding runtime/release regressions. A native GPT review checks
concrete failure boundaries.

Local platform: macOS, Python 3.11.15/3.12.13/3.14.5, Codex CLI 0.161.0,
Kimi Code CLI 2.1.1. The CLI uses
`KIMI_CODE_HOME/config.toml`, with no `--config-file` flag. Offline doctor checks
configuration/credential-source presence and required flags; it never probes
account identity, OAuth validity, quota or inference.

Live provider checks in this work used shared production capacity (limit 5).
The API-key HighSpeed alias returned an upstream 403 five-hour usage limit.
After inspecting partial edits, a separately configured managed OAuth HighSpeed
alias succeeded and completed the catalog/packaging tasks. These are observed
results for those aliases only. No credentials were copied or printed.

Executed final checks:

- **83 tests passed** on each of Python 3.11.15 (23.682s), 3.12.13 (23.732s)
  and 3.14.5 (24.123s), locally on macOS. New cases cover config propagation,
  malformed model metadata, offline doctor, credential redaction, safe pruning,
  Git-history scanning and portable/native manifest separation.
- The installed/enabled 0.2.0 plugin is recognized by `codex mcp list --json`.
  Starting the exact discovered command/cwd/env returned version 0.2.0,
  **15 tools**, and offline doctor ready. No startup errors were observed.
- One installed-bridge live managed HighSpeed follow-up retained the same
  captured session ID and correctly recalled the earlier response `READY`.
  Account access for other aliases/providers was not probed in this final check.
- A relocated ZIP with temporary fake Kimi configuration/state passed stdio
  discovery, offline doctor and **83 tests** on Python 3.11 (23.602s).
  Package contents exclude authentication files,
  database state, caches and logs. Real fresh-account setup remains untested.
- The release checker scanned the current tree and all reachable local Git
  history with no credential-pattern findings. No suspected values are printed.
- Native review's credential assignment/Basic-header and JSON-key findings were
  fixed and independently rechecked; Kimi authored regression coverage.
- Skill metadata validation passed with the official validator; PyYAML was
  used only in a cached offline validation environment, not shipped or needed
  by the plugin runtime.
- A local commit and whitelisted marketplace ZIP preserve this beta. No remote
  publication or remote CI run was performed.

Actual installed-host discovery caught a portable MCP format error that direct
stdio tests did not: native timeout fields are not accepted by the portable
Agent Plugins schema, and its package cwd is `./`. The packaging and release
checks now keep the portable/native formats separate. The pinned Codex 0.161.0
loader was read to establish the accepted fields; no fetched code was executed.

## Linux validation host installation check (2026-10-09)

Installed the 0.2.0 ZIP through the documented local marketplace commands on
Linux validation host: Ubuntu 24.04.5 LTS, x86_64, Python 3.12.3, Codex CLI 0.162.1.
The initial package was from commit `b88fb19`; the test fixture correction below
was then applied and included in the refreshed shareable ZIP.

- Archive SHA256 verification passed. Codex reported the plugin installed and
  enabled, and `codex mcp list --json` discovered `mixed_agents` without manual
  MCP configuration. Starting that exact command/cwd/env passed initialization
  and discovery of **15 tools**, with empty stderr and exit code 0. The installed
  Skill includes the cross-family TDD rule.
- The initial offline run had 82 passing tests and one failure: the long-stderr
  test put 200000 characters into a single prompt argument, exceeding Linux's
  per-argument limit before the fake CLI could start (`E2BIG`). Kimi corrected
  only the fixture and test: a compact repeat specification still emits all
  200000 stderr characters in the child, while keeping argv small. String-based
  stderr, nonzero-exit/error-tail and timeout checks remain intact. No runtime
  code changed; this does not add support for oversized real CLI prompt arguments.
- After correction, **83 tests passed** on Linux validation host/Python 3.12.3 (26.069s).
  Kimi also ran all 83 locally on macOS (25.677s). Linux coverage includes real
  fake-CLI child processes, locking, capacity, cancellation, follow-up, recovery,
  diagnostics, redaction and MCP behavior; these are offline checks.
- Kimi CLI was upgraded from 2.0.2 to **2.1.1**. Its built-in downloader stalled,
  including a bounded retry through the existing local proxy. The official
  Linux archive was transferred and its executable verified against the official
  manifest checksum before installation. The old executable was preserved as
  `~/.kimi-code/bin/kimi.bak-install-test-2.0.2`; the existing config was verified
  unchanged. No credentials were transferred from another host.
- Final offline `doctor()` passed the platform, Python, CLI version/flags,
  readable config, manifest and shared-capacity checks (limit 5, no running jobs).
  It correctly reported **not ready** because this host configures only GLM
  aliases, which this version does not recognize. Actual Kimi inference,
  authentication, quota and live same-session follow-up remain unverified here.
- Release checks passed locally, including reachable Git history, and on the
  extracted Linux package. The extracted package has no Git history; its check
  reports `history: not_checked`. Remote CI was not run.

At completion of this 0.2.0 check, the plugin was installed at
`~/.codex/plugins/cache/kai-mixed-agents/mixed-agents/0.2.0`.
The local marketplace source and retained test evidence are under
`~/.local/share/codex-mixed-agents/install-tests/b88fb19`.

## GLM support and installed 0.3.0

Implemented `GLM_SPEC.md` with different model families authoring code and tests:
Kimi implemented the catalog; native GPT authored its acceptance tests. GPT
implemented runtime/MCP; Kimi authored 15 GLM runtime/MCP regressions. An extra
alias-routing regression was verified to fail against simulated prefix-first
routing and pass with configured-alias-first dispatch.

- **105 tests passed** locally on macOS/Python 3.14.5 (34.405s) and on
  Linux validation host/Linux/Python 3.12.3 (34.769s). New cases cover the eight exact GLM
  identifiers, arbitrary/provider-qualified aliases, secret exclusion, metadata,
  all three families in rotation, mixed CLI capacity, role independence in both
  orders, family-safe follow-up, queued messages, cancellation, recovery,
  pruning, GLM-only doctor readiness and MCP family schemas.
- The final 0.3.0 plugin was installed and enabled locally and on Linux validation host
  through the local marketplace. Both hosts' exact Codex-discovered MCP
  command/cwd/env passed initialization with version **0.3.0**, **15 tools**,
  empty stderr and offline doctor ready. Linux validation host uses Codex CLI 0.162.1
  and Kimi CLI 2.1.1; local Codex CLI is 0.161.0.
- Linux validation host's existing configuration exposed seven GLM aliases. Task-fit
  selection for a small, latency-sensitive exploration chose the exact alias
  **`zai-coding-plan/glm-5-turbo`**. The installed MCP bridge started one
  tool-free smoke worker, which returned `READY HBGLM-73c9`. Follow-up through
  MCP preserved the same agent ID and captured session ID; turn 2 correctly
  recalled `HBGLM-73c9` without the token appearing in its new prompt.
- Kimi and GLM share the existing state root and configured CLI limit **5**.
  This live check did not change capacity or copy provider credentials. Config
  digests compared before/after the operational checks remained unchanged.
  Same-session GLM variant switching and cross-family rejection were validated
  offline; the live follow-up retained the same model.
- Other configured GLM aliases and all models on new accounts remain unprobed
  for live authentication, quota and entitlement. Doctor remains an offline
  configuration check. Local Kimi test authorship briefly hit an OAuth TLS
  connection failure; a same-session retry completed without changing accounts.
- The official Skill validator passed. Release checks passed for the current
  tree and reachable local Git history; the extracted remote package has no
  Git history to inspect. The whitelisted ZIP excludes credentials, logs,
  state and caches. No external publication or remote CI was performed.

Installed Linux validation host plugin:
`~/.codex/plugins/cache/kai-mixed-agents/mixed-agents/0.3.0`.
Retained marketplace source and sanitized evidence:
`~/.local/share/codex-mixed-agents/install-tests/0.3.0`.
The local installed Skill/global guidance now use 0.3.0 and the three families.
A new chat may be needed to reload an already-open conversation's tool schemas.

## Off-peak preference 0.3.1 (2026-10-11)

Implemented `COST_POLICY_SPEC.md`: GLM-5.3 authored the policy helper and
runtime/MCP integration; Kimi independently authored 23 new tests in disjoint
clock/config and selection/MCP files. The parent authored docs, release metadata
and installed-transport verification.

- **128 tests passed** locally on macOS/Python 3.14.5 (33.075s) and on
  Linux validation host/Linux/Python 3.12.3 (37.024s). The 23 additions cover peak start/end,
  weekends, timezone date rollover, custom windows/weekdays, disabled operation
  without zoneinfo data, strict validation, private shared-setting persistence,
  malformed stored nulls, exact-provider preference despite catalog deduplication,
  native-name/backend collisions, missing targets, fit/context/family/capacity
  precedence, explicit dispatch, MCP schema/clock rejection and recovery CLI.
- Both hosts' installed Codex-discovered command/cwd/env initialized as version
  **0.3.1**, exposed **16 tools**, and passed offline doctor with no stderr.
  `set_cost_policy(enabled=true, model="zai-coding-plan/glm-5.3")` enabled the
  user's default schedule on both hosts. Shared capacity stayed **5**.
- Genuine installed selection on both hosts reported `period: off_peak`, selected
  the exact Z.ai alias among top-fit ties, and returned
  `cost_preference_applied: true`. This did not change the catalog's Ark default
  or apply the rule to other GLM aliases. Peak/disabled behavior was tested with
  aware fixed clocks offline; no provider billing boundary was simulated live.
- One Linux validation host installed-MCP tool-free **GLM-5.3** worker completed with
  `GLM53_COST_READY`. Local GLM-5.3 also completed the implementation/repair work.
  Access on other aliases/accounts was not probed. Discount claims follow the
  documented Coding Plan schedule; actual charged credits were not measured.
- Initial long external implementation/test turns hit their configured timeout;
  partial files were inspected and work was split into narrow repairs and
  disjoint tests. No uncaptured session was guessed or resumed. Parent review
  found stored-null and backend-admission cases; GLM fixed them and Kimi added
  regressions. Kimi's narrow HighSpeed test completion succeeded on that exact
  alias, independent of older historical quota errors.
- Upgrading removed the previous installed cache path while this conversation's
  MCP server still cached it. A later old-server worker stopped before its first
  CLI turn. The current installed recovery CLI completed the remaining work,
  using the same registry and shared cap. Open a new chat/reload tools after
  upgrading before starting workers; this is not an upstream quota error.
- Skill metadata validation and release/tree checks passed. The shareable ZIP
  retains the **disabled** portable default and excludes local config, credentials,
  state, logs and caches. Local reachable Git history was scanned; the extracted
  remote package has no history. No publication, remote CI or automation was run.

Linux validation host installed root:
`~/.codex/plugins/cache/kai-mixed-agents/mixed-agents/0.3.1`.
Marketplace/evidence root:
`~/.local/share/codex-mixed-agents/install-tests/0.3.1`.
The local installed Skill/global guidance now point to 0.3.1. No deferred queue,
running-worker changes or actual monetary/account-quota estimates were added.

## Z.ai capacity and usage protection 0.4.0 (2026-10-11)

Implemented `ZAI_GUARD_SPEC.md` with SDD as the primary workflow. Optional TDD
uses observed red-to-green tests and clean implementation, with no mandatory
refactor phase. GLM-5.3 authored the quota helper; Kimi authored runtime/MCP
integration. GPT independently authored the 48 new tests across those components.
The parent authored documentation, version metadata and operational checks.

- **176 tests passed** locally on macOS/Python 3.14.5 (61.624s) and on
  Linux validation host/Linux/Python 3.12.3 (74.902s). New cases cover strict policy validation,
  origin/account identity, safe five-hour parsing and HTTP/cache behavior,
  cross-process admission and schema upgrades, legacy/orphan capacity,
  follow-up and queued turns, quota interruptions and live policy changes,
  selection fallback and family independence. These tests use fake processes
  and temporary state; no real credentials or paid concurrency stress tests.
- Both installed Codex-discovered MCP transports initialized as **0.4.0**,
  exposed **18 tools** including `provider_usage` and `set_zai_policy`, and
  passed offline doctor. All 17 plugin source/Skill/manifest files matched each
  installed cache. Shared CLI capacity remained **5** and cost policy was
  preserved; the separate Z.ai provider cap is **3**.
- Real read-only five-hour usage succeeded on both hosts. Enabled the guard
  with a **10% remaining** threshold in each host's private settings. Usage
  unavailable while enabled blocks Z.ai; delayed reporting, in-flight spending
  and independent clients prevent a hard guarantee of >5% remaining. No
  forged/simulated quota was injected into a live account to trigger a stop.
- Each installed host completed one tool-free `zai-coding-plan/glm-5.3` worker
  returning `GLM53_GUARD_READY`. A real reading above 10% preceded each launch;
  the final Linux validation host reading was 12.5% remaining. Actual threshold/unknown
  interruption was validated offline, rather than deliberately draining quota.
  Other aliases and fresh accounts were not live-probed.
- With explicit user approval, lowered only that exact alias's
  `max_output_size` from 131072 to **16384** on both hosts. All other parsed
  configuration values were preserved; credentials were neither copied nor
  logged. Readback confirmed the new value on both machines.
- Skill validation and release checks passed. The shareable ZIP keeps the
  portable usage guard **disabled** by default and contains no personal policy,
  credentials, state, caches or validation logs. Local reachable Git history
  was scanned. No external publication or remote CI was performed.

Linux validation host installed root:
`~/.codex/plugins/cache/kai-mixed-agents/mixed-agents/0.4.0`.
Marketplace and sanitized installation evidence:
`~/.local/share/codex-mixed-agents/install-tests/0.4.0`.
Local installed Skill and shared guidance point to 0.4.0. Start a fresh chat to
reload cached Skill/MCP schemas before using the new tools in an older session.

## Kimi reserve and bounded bootstrap 0.5.0 (2026-10-11)

Implemented `KIMI_GUARD_SPEC.md`: Kimi authored the usage helper, runtime/MCP and
probe profile. GPT independently authored 32 new tests and updated existing MCP
compatibility assertions. The parent authored docs, release metadata and
operational installed-transport checks. Initial long K3/Highspeed turns timed
out after writing partial implementation; preserved files were inspected and
repaired by the same model family in narrower tasks. No missing session ID was
guessed. Two GLM test attempts produced no files; the quota guard stopped the
second after a real reserve reading. Their read-only logs were audited before
releasing unfulfilled assignment claims; original job records were retained.

- **208 tests passed** in the independent GPT author's final local suite
  (macOS/Python 3.14.5, 76.609s) and in the extracted Linux validation host package
  (Linux/Python 3.12.3, 92.332s). Local Python 3.14 emitted non-failing SQLite
  ResourceWarnings. After the local full run, synthetic refresh-token fixtures
  were renamed to the release scanner's `test_` convention; the four affected
  OAuth cases passed again (0.400s). The remote full run included that rename.
- New tests cover actual five-hour payloads and strict numeric/window parsing,
  fixed-host HTTP and secret-safe results, per-account cache/identity isolation,
  OAuth expiry/refresh/path handling, one-shot probes across aliases, threads,
  processes and restarts, small-output/one-step/one-attempt bounds, full/lowered
  capacity and slot release, healthy secondary-account routing, pre-probe family
  checks, queued/active unknown interruption, MCP and existing Z.ai compatibility.
  A 65-account synthetic regression verifies live allowances cannot be evicted
  early. Offline processes/config/credentials are all fake; no paid stress batch.
- Both Codex-discovered installed transports initialized as **0.5.0**, exposed
  **20 tools**, and passed offline doctor. All 19 plugin code/Skill/profile/
  manifest files matched each installed cache. Both hosts persist Kimi guard
  enabled with remaining threshold **5%**, `probe_on_unknown=true`; shared cap
  **5**, Z.ai cap **3**, Z.ai threshold **10%** and cost policy were preserved.
- Local `kimi-for-coding/kimi-for-coding` completed one installed tool-free
  worker returning `KIMI_GUARD_READY`, preceded by readable quota above 5%.
  Its verification reading was 43% remaining. Other live Kimi variants were not
  re-probed for this release. Linux validation host has no configured Kimi Code account;
  it verified installation, policies and all offline tests without borrowing or
  copying local credentials or claiming a live Kimi call on that host.
- Genuine installed `probe_kimi_usage(model="kimi-code/kimi-for-coding")`
  started with `credential_expired`, ran one bounded CLI bootstrap, let the CLI
  perform its normal OAuth refresh, then read **50% remaining**. A repeat tool
  call returned `probed=false`, `reason=known_reading`, without another bootstrap.
  This proves the real expiry-to-readable path; it does not prove every unknown
  error can be repaired by inference. Unknown after a probe remains blocked.
- Usage thresholds and interruption at <=5% were verified offline; no live
  account was deliberately drained. Reporting delays and in-flight requests
  prevent a hard final reserve guarantee. Accounts/providers without the Kimi
  Coding endpoint and fresh-account access remain unverified/unsupported.
- Skill validation and release/tree/reachable-history checks passed. The ZIP
  retains disabled portable Kimi/Z.ai guards and disabled automatic bootstrap,
  excludes private policies/credentials/state/caches/logs, and includes the spec
  and reproducible offline tests. No publication or remote CI was performed.

Linux validation host installed root:
`~/.codex/plugins/cache/kai-mixed-agents/mixed-agents/0.5.0`.
Retained package and sanitized evidence:
`~/.local/share/codex-mixed-agents/install-tests/0.5.0`.
Shared local guidance points to 0.5.0. Fresh chats may be needed to reload an
older conversation's cached Skill/MCP schemas.

## Current GLM routing candidates 0.5.1 (2026-10-12)

The user confirmed the two current GLM targets. The parent GPT added a small
public catalog filter for `glm-5.3` / `glm-5.3-flash`; raw compatibility loading,
explicit dispatch and provider cap/quota accounting retain legacy names. Current
README/Skill selection guidance no longer compares obsolete GLM tiers.

- Local **208 tests passed** (Python 3.14.5, 78.757s). The initial run found one
  obsolete automatic-selection fixture: the three-family tie used GLM-5.2.
  A genuine GLM-5.3 agent independently selected modern equal-fit fixture inputs
  (K3, GLM-5.3, GPT Astra at complex difficulty). The parent integrated those
  values; all existing Kimi-authored rotation assertions were preserved. The
  corrected targeted case also passed on Linux validation host (Python 3.12.3, 0.157s).
  The full remote suite was not repeated for this platform-neutral patch;
  the preceding 0.5.0 remote full run is recorded above.
- Both installed Codex-discovered MCP transports initialized as **0.5.1**,
  exposed **20 tools**, reported exactly the two GLM profiles, and passed offline
  doctor. Shared cap **5**, Z.ai cap **3**, Z.ai threshold **10%** and Kimi
  threshold **5%** stayed unchanged. Provider forwarding destinations of legacy
  names were not guessed or live-probed.
- The API-key Kimi test account returned a genuine **weekly** usage-limit error
  before any writer action. Its unfulfilled assignment claim was retired after
  auditing the empty tool log, preserving the original job record. The other
  Kimi account was usage-unknown and already in probe cooldown; no guard was
  bypassed. GLM Flash attempts timed out without writing tests. A bounded,
  tool-free GLM-5.3 response supplied the successful fixture decision; those
  observations do not establish access or latency for every alias/provider.
- Skill validation and release/tree/reachable-history checks passed. The ZIP
  excludes local settings, credentials, caches and logs. No publication or
  remote CI was performed. Both installations and shared local guidance now
  point to 0.5.1; a fresh chat can reload older cached tool schemas.

Linux validation host package/evidence root:
`~/.local/share/codex-mixed-agents/install-tests/0.5.1`.

## Integration boundaries

- External Kimi/GLM CLI sessions do not appear in Codex's native Subagents panel.
  The parent conversation, stable IDs and lifecycle tools combine the workflow.
- Native GPT actions require actual host collaboration tools. Registry metadata
  cannot independently query native progress or prove execution.
- Running CLI agents receive queued messages at its next turn boundary. Stopping a
  turn does not undo edits. Without a captured session ID, safe resume may be
  impossible; the bridge never guesses another session.
- The cap covers bridge launches sharing one state root, including resumes.
  Independent Kimi app/CLI sessions and separate state roots are outside it.
- Worker prompts/profiles are not an OS sandbox. Redaction and release scanning
  are bounded heuristics, not a guarantee that arbitrary private data is absent.
- Tests use fake CLI processes and temporary credentials/state. No paid batch
  above five agents was run. Numeric/unlimited modes are covered offline.
- GitHub-hosted Linux/Python 3.11/3.12 CI runs the offline suite and release
  checks. macOS compatibility is a separate manual workflow. Earlier Linux
  installation checks are recorded above; current pre-release product fixes
  and their local validation are recorded in
  [OPEN_SOURCE_READINESS_SPEC.md](OPEN_SOURCE_READINESS_SPEC.md).
- Fresh chats may be needed to load changed Skill/MCP schemas after installation.
  Model-fit scores are routing assumptions, not benchmarks or proof of access.
