# Codex mixed agents

A local plugin makes Kimi Code CLI agents using Kimi or GLM and native GPT
subagents available in one delegation workflow. Select by task fit, maintain independent contexts, and
use different model families for implementation and test authorship.

## Behavior

- Kimi, GLM and GPT are candidate peers; no GPT-first fallback ranking.
- Configurable Kimi CLI concurrency (including GLM workers) across bridge
  processes sharing the default state directory: default **5**, any nonnegative integer (0 = unlimited), or **unlimited**.
  At a finite limit, overflow independent tasks use native GPT. Tests of GPT-authored
  code use Kimi or GLM and wait for CLI capacity, preserving family independence.
- Start, inspect, wait, message, follow up, and interrupt using stable agent IDs.
- Session history and pending messages survive MCP reconnection.
- GPT runs through Codex's native collaboration tools. The bridge returns
  explicit action descriptors for the parent to perform and register.
- Kimi messages during a turn run at the next turn boundary. They do not steer
  an in-flight operation. Stop + follow up for urgent steering.
- Code and test authors for the same work unit use different families. All GPT
  variants count as one family, all Kimi variants as one, and all GLM variants as
  one. The CLI backend does not determine model family. Define intended behavior
  and acceptance criteria before implementation; independent tests verify them.

This is a unified conversational/tool workflow, not a patch to Codex's internal
agent runtime. External Kimi sessions are not promised to appear in the native
Subagents panel. Stopping work does not roll back previous filesystem edits.

## Requirements and installation

macOS/Linux, Python 3.11+, Codex CLI with local plugins, and an authenticated Kimi
Code CLI. No additional Python runtime dependencies. Keep credentials in the
existing Kimi installation.

Clone this repository and install from its root:

```sh
git clone https://github.com/kikifrost/codex-mixed-agents.git
cd codex-mixed-agents
codex plugin marketplace add .
codex plugin add mixed-agents@kai-mixed-agents
codex plugin list --marketplace kai-mixed-agents --json
```

This checkout includes two separate manifest formats:

- **Portable root manifests** (`plugin.json` + `mcp.json`) — canonical package
  layout. The root `plugin.json` carries the `extensions.com.openai.interface`
  only; the root `mcp.json` independently supplies the stdio MCP server with the
  minimal fields the portable loader accepts: `type`, `command`, `args`, `cwd`.
  It launches `./bridge/launch.py` using `python3` from PATH with `cwd: "./"`.
- **Native Codex overlay** (`.codex-plugin/plugin.json` + `.mcp.json`) —
  compatibility layer for the installed Codex CLI version used here. It can keep
  the native timeout keys and `cwd: "."` as that loader permits.

See the [OpenAI plugin docs](https://developers.openai.com/plugins/build/plugins)
for the current plugin schema. On a host where `python3` is not Python 3.11+,
install a supported Python and either make it the default `python3` or edit the
MCP manifests to the full path before installation.

Open a new Codex chat (or restart the app when needed) to load the plugin's Skill
and MCP tools. Example user request:

> Use mixed agents to implement [feature]. First state the intended behavior,
> important edge cases and acceptance criteria. Choose models by task fit;
> assign implementation and independent tests to different model families.
> Coordinate their work around the spec, then report the changes, verification
> results and any acceptance criteria that remain unmet.

This example asks for a concrete outcome. The Skill supplies lifecycle, capacity
and authorship rules; the user does not need to repeat those mechanics in every
request. Test-first work is optional; see Development below.

See [the Skill](plugins/mixed-agents/skills/mixed-agents/SKILL.md) for model
selection and native-action handling. [SPEC.md](docs/SPEC.md) records exact
semantics and invariant precedence. [VALIDATION.md](docs/VALIDATION.md) records
checks actually run and limits still unverified.

### Login and Kimi configuration

The plugin does not manage Kimi credentials. Install and log in with the Kimi
Code CLI first:

```sh
kimi login
```

Current Kimi Code 2.1.1 reads `$KIMI_CODE_HOME/config.toml` and has no
`--config-file` option. The bridge resolves the config directory from
`$KIMI_CODE_HOME`, then `~/.kimi-code`, and sets a child-local `KIMI_CODE_HOME`
to that directory for each Kimi launch. It never copies credentials or mutates
the supervisor environment.

### Supported model aliases

Use the exact configured alias in `spawn_agent` and `followup_task`. The catalog
recognizes provider-qualified identifiers by their final path component:

- Coding remote identifiers: `kimi-for-coding`, `kimi-for-coding-highspeed`
- `k3.1-pro` / `kimi-k3.1-pro`
- `k3.1-pro-highspeed` / `kimi-k3.1-pro-highspeed`
- `k3` / `kimi-k3`
- `k3-256k` / `kimi-k3-256k`
- `k2.7` / `kimi-k2.7` (regular K2.7)
- `k2.8-preview` / `kimi-k2.8-preview` (K2.8 Preview)
- These remote identifiers may have provider prefixes, such as `vendor/kimi-k2.7`.
  Dispatch uses the configured alias, which can have a different name.

Automatic GLM selection considers **`glm-5.3` and `glm-5.3-flash`** only.
They may have provider prefixes; dispatch uses the complete configured alias,
such as `zai-coding-plan/glm-5.3`. Legacy recognized GLM names remain compatible
with explicit dispatch and quota/cap accounting, but are not ranked as separate
automatic candidates. Provider forwarding of a legacy name is not inferred.
All GLM variants are family `glm`, running through backend `kimi_cli`.

K3.1 Pro and Pro Highspeed support up to 1,048,576 context tokens, subject to
the configured limit and plan access. Both require thinking. For an imported
CLI alias, keep `[thinking].enabled = true` or declare `always_thinking` in its
capabilities; disabled thinking without that capability is rejected before
inference. Managed routes enable thinking in their temporary configuration.
Highspeed has the same capability with lower latency and approximately twice
Pro's quota usage. Start a fresh agent/session when moving from older Kimi
models. Model support does not automatically grant aliases to saved templates;
enable the exact configured aliases in the console as needed.
See the [official model configuration](https://www.kimi.com/code/docs/en/kimi-code/models.html).

K2.7 regular and K2.8 Preview are distinct profiles. Unknown identifiers and
other families such as DeepSeek are rejected; `doctor()` lists unsupported
configured aliases. The bridge exposes every configured alias, including aliases that share a remote
model but use different credentials, and never replaces an explicitly requested
alias with a managed equivalent.

## Kimi five-hour usage reserve

Kimi Code has a separate **5% remaining** threshold. Enable it with:

```json
{"tool":"set_kimi_policy","arguments":{"usage_guard":true,"stop_remaining_percent":5,"probe_on_unknown":true,"probe_cooldown_minutes":15}}
```

Read usage for an exact configured account with
`provider_usage(provider="kimi", model="EXACT_ALIAS")`. Aliases on one account
share readings and the bootstrap allowance; separate accounts do not inherit
each other's remaining quota. Kimi hosted elsewhere may not expose this Coding
Plan endpoint and must report unsupported rather than guess its quota.

With the guard enabled, an observed remaining value at or below **5%** blocks
new work and stops affected active workers, retaining edits, output and captured
sessions. Unknown usage is not an empty account: idle admission may first make
one fixed, tool-free, bounded inference when `probe_on_unknown` is enabled, then
read usage again. If it remains unknown, the requested task does not start.
Concurrent callers, aliases and restarts share one durable allowance per account
per configurable cooldown (default **15 minutes**, any positive integer in minutes).
Known exhausted/reserve accounts are never probed, and
active or queued unknown usage stops instead of bootstrapping. Catalog, selection
and doctor never run inference. `probe_kimi_usage(model="EXACT_ALIAS")` exposes
the same bounded operation explicitly; it is not an arbitrary task launcher.

With the guard and `probe_on_unknown` enabled, `model_catalog` and
`select_model` return `kimi_recovery`: one entry per affected account with the
exact alias, a safe `usage_error` code, a fixed
`next_action` (`probe_kimi_usage` with that alias) and a plain reason. Catalog
guidance comes from exact per-alias account reads, not the aggregate reading,
so one unhealthy account cannot hide a healthy one; selection guidance is
limited to aliases that already pass permissions, family, context and capacity
checks. Entries never expose credentials, fingerprints or private paths.
`credential_expired` means an expired local OAuth access token whose usage is
unknown pending CLI refresh — not proof of invalid login, an expired
subscription or exhausted quota. Before excluding a Kimi alias for unknown
usage, first call `select_model` with the actual task and workspace, execute
only its recovery actions (also returned by `no_eligible_model`), then rerun
selection. Catalog actions are global diagnostics and do not establish task
permissions or capacity. Probe at most once per affected account; a
`probe_cooldown` or still-unknown result stops that account without retry
loops. This guidance covers imported CLI aliases that the explicit probe
accepts; it never substitutes an imported source alias for a managed alias
that is the only model allowed by a template.

The portable guard is disabled. A bootstrap uses the same shared CLI slots as
workers and does not bypass capacity. It can consume a small amount of quota,
and provider reporting delays and in-flight requests mean an observed 5% cutoff
cannot guarantee the final remaining balance. See
[KIMI_GUARD_SPEC.md](docs/KIMI_GUARD_SPEC.md) for the accepted behavior.

## Local policy console

The header shows **current occupancy / concurrency limit** and an **编辑并发上限**
button on every page. It opens total CLI concurrency (Kimi + GLM, excluding native
GPT), Z.ai Provider concurrency, and Kimi account caps next to the header. All
concurrency controls, including the template single-Project cap, use **0 = no cap
at that layer**. Other caps and quota guards still apply. Kimi account caps
default to unlimited; Z.ai retains its configured Provider cap (default 3). Existing
`unlimited` policy values and MCP arguments remain compatible.

On desktop clients supporting the OpenAI structured-settings extension, open
**Plugins → Mixed Agents → settings → 打开配置控制台**. The action starts the console
on a free loopback port and opens the default browser. Repeated clicks within the
same MCP connection reuse that service. The console closes when that connection
exits; click again after reconnecting to get a fresh URL. Viewing plugin details
does not launch a console, run inference, refresh authentication or request quota.
This entry requires the local desktop bridge; it does not expose this machine's
configuration to ChatGPT on the web. If your client has no settings action, use
the command below. If the browser cannot open automatically, the action returns
the local URL to open manually.

Launch from the extracted repository or installed plugin directory:

```sh
python3 plugins/mixed-agents/bridge/console.py --open
# From an installed plugin root: python3 bridge/console.py --open
```

The Chinese console opens at `http://127.0.0.1:8765`. Use `--port 0` to choose a
free port. It runs while this command is running; Ctrl-C closes it. On another
machine, forward its loopback port with SSH rather than exposing the console
on the network.

Configure shared CLI concurrency, Kimi account order and individual account caps,
five-hour guards and probe cooldown, Z.ai capacity, and the exact-alias off-peak
schedule. Each form saves independently into the existing private shared state;
the bridge reads it live. The console does not store or edit credentials. Reading
usage is separate from a bounded inference probe, and page load never probes.

Automatic routing prefers `kimi-code` then `kimi-for-coding` **within the same
eligible Kimi profile**. Missing, reserve-blocked or account-full candidates allow
a healthy backup; explicit aliases stay exact. Unknown guarded usage remains
blocked until a bounded probe yields readable quota. Other families remain peers
selected by fit and independence. A failed write task is not automatically replayed.

Use `set_kimi_accounts(order=["kimi-code", "kimi-for-coding"], limits={})` through
MCP or the console to change account policy. Account caps default to unlimited;
limits accept a nonnegative integer (0 = unlimited) or `"unlimited"`. Same-credential aliases share
the strictest configured cap. Lowering a cap keeps running work. The shared CLI
cap still applies: at shared-full capacity, an idle backup cannot add a slot;
eligible GPT handles independent work or CLI work waits. See the
[console acceptance spec](docs/console-spec.md).

控制台用法：运行上面的命令后，在本机页面修改即可。账号顺序默认是
`kimi-code, kimi-for-coding`；探测冷却默认 **15 分钟**。想让主账号达到自己的
并发上限后用备用账号，设置主账号上限小于共享上限；例如共享 5、主账号 3。
这不会绕过共享总上限。修改账号登录信息仍通过 Kimi CLI 完成。

## Z.ai provider capacity and usage reserve

Z.ai GLM has a separate default cap of **3**, shared across aliases whose configured
provider origin is HTTPS `api.z.ai`. Kimi and GLM from other providers keep their
existing rules; the total CLI cap remains **5** by default. Extra independent
work can use eligible Kimi or native GPT, preserving implementation/test families.
Provider admission is checked again at dispatch and idle continuation.

```json
{"tool":"set_zai_policy","arguments":{"limit":3,"usage_guard":true,"stop_remaining_percent":10}}
```

`limit` also accepts any nonnegative integer (0 = unlimited) or `"unlimited"`. Policy updates preserve
the shared cap and off-peak settings. Lowering a cap preserves active jobs.
`provider_usage()` reads normalized real five-hour quota with credentials from
the existing Kimi config. It returns unavailable explicitly when it cannot read
valid data; unknown is never shown as zero usage.

The portable usage guard is disabled. Enable it when reserve protection is wanted
and usage is readable. The default remaining threshold is **10%** (configurable
strictly above 5 and below 100), leaving a margin for a requested reserve above 5%.
With the guard enabled, it checks admission, each queued turn and active workers
with readings at most 30 seconds old. At/below threshold or on unknown usage,
Z.ai dispatch is blocked and active Z.ai workers are interrupted with an explicit
reason. Edits, outputs and captured sessions survive; pending messages are cleared.
Resume requires readable usage above the threshold. Other eligible providers
remain available. Interrupted write tasks are never automatically replayed. Change account
credentials/provider assignments while the affected workers are idle; a running
CLI turn keeps its startup account.

Metrics can lag, in-flight requests may already be billed, and other clients can
spend on the account. The guard cannot guarantee a hard balance above 5%.
The quota endpoint is an undocumented provider endpoint and may change; failures
are explicit. See [the accepted spec](docs/ZAI_GUARD_SPEC.md) for boundaries.

## Self-check

Run `doctor()` as an MCP tool or through the recovery CLI:

```sh
python3 plugins/mixed-agents/bridge/server.py call doctor '{}'
```

`doctor()` performs bounded offline checks of OS/Python, CLI executable/version,
readable TOML, supported/unsupported aliases, credential source presence (not
values), plugin manifest versions and shared capacity/config. It returns
actionable sanitized results; it does not perform inference, claim account
quota, refresh auth, or edit configuration.

Diagnostics include enabled imported and console-managed models. Managed models
need a readable `config.toml`, but do not need a duplicate TOML model entry.
Their `env:` and `existing:` references are checked for source presence offline.

## Templates, Project dispatch and local configuration console

The workspace provides saved templates, immutable payload revisions, Project
bindings and Provider/model configuration through MCP, the recovery CLI and a
three-page local console: templates, Provider/models and Project bindings.
Copy the default template, choose which Kimi Code Provider/model combinations
it permits, then bind a Project to the saved custom template or follow default.
Zcode and Claude Code are visibly planned, with no executable adapter yet.

```sh
python3 plugins/mixed-agents/bridge/console.py --open
python3 plugins/mixed-agents/bridge/server.py call configuration_resources '{}'
python3 plugins/mixed-agents/bridge/server.py call list_templates '{}'
python3 plugins/mixed-agents/bridge/server.py call create_template '{"name":"My project"}'
python3 plugins/mixed-agents/bridge/server.py call register_project '{"name":"My project","root":"/absolute/path/to/project"}'
```

Provider/model forms support create, edit and disable. Existing imported
connection fields remain owned by the CLI installation; bridge-managed
connections accept symbolic `env:VARIABLE_NAME` or `existing:connection_id`
credential references. No credential values are copied and CLI TOML is never
rewritten. Current model creation accepts recognized Kimi/GLM upstream profiles.
New models are not automatically granted to templates; disabled or missing
resources retain their references and appear unavailable in resolution.
Kimi account/guard and Z.ai quota/off-peak controls reuse existing global policies,
with separate explicit Save actions. Loading the console performs no inference,
authentication refresh or remote quota request.

Use returned IDs with `bind_project_template(project_id, template_id,
expected_binding_revision)`. A null template ID follows the default. Publish a
changed payload with `save_template(template_id, payload, expected_revision)`;
conflicting revisions preserve saved data and console drafts. Resource saves also
require their expected revision. `resolve_project_config(project_id, snapshot_id)`
can pin a resolution preview; candidates indicate configured references, not
live entitlement. Actual Kimi Code selection/spawn/follow-up checks saved template
permissions again before admission and before each queued turn.

New data lives alongside existing jobs in `agents.sqlite3`; existing Kimi/Z.ai
policies remain authoritative in `config.json`. Release 0.7.0 applies these saved permissions to Kimi Code dispatch. Spawn uses
an explicit local `project_id` or the longest registered ancestor of its canonical
`cwd`; an explicit Project must contain cwd. Selection accepts `project_id`/`cwd`
too; without either it uses default and spawn checks its own cwd again. Unregistered
folders use default with no Project cap. Registered Projects share their saved
`max_parallel_per_project` across live CLI workers, in addition to global/account/
provider limits. Lowering a limit preserves active turns and checks future ones.

Each actual CLI turn persists an immutable secret-free configuration snapshot,
visible through `get_agent`. Edits affect future turns; queued/follow-up turns
recheck current permission and receive a fresh snapshot. Imported CLI routes keep
the existing installation. Managed routes compose private temporary configuration
and reuse credential/session storage; raw API keys travel only in the child
environment. These temporary files are removed after the turn.

Default templates retain the existing global equal-fit discount semantics. Custom
templates use that preference only when their discount flag is enabled; global
quota guards and caps remain mandatory. Native GPT is the separate host workflow
outside Kimi Code templates. Project-context selection excludes native GPT and
native spawn rejects that context rather than bypassing Project permissions.
Without a Project context, the existing genuine native reservation/registration
workflow remains available.
See the [stage 1 contract](docs/TEMPLATE_IMPLEMENTATION_SPEC.md),
[stage 2 contract and continuation](docs/TEMPLATE_STAGE2_SPEC.md) and
[validation record](docs/VALIDATION.md).

## Concurrency configuration

Set the shared Kimi CLI limit (Kimi and GLM workers together) through MCP
`set_concurrency(limit=8)` or
`set_concurrency(limit="unlimited")`. The recovery CLI provides the same operation:

```sh
python3 plugins/mixed-agents/bridge/server.py call set_concurrency '{"limit":8}'
python3 plugins/mixed-agents/bridge/server.py call set_concurrency '{"limit":"unlimited"}'
```

Configuration persists in `~/.local/state/codex-mixed-agents/config.json`:

```json
{"kimi_concurrency": 5}
```

Replace `5` with any nonnegative integer (0 = unlimited) or the string `"unlimited"`. Changes apply
to new dispatches and idle-session follow-ups without restarting MCP. Lowering
the limit leaves existing jobs running; new admissions wait until running jobs
fall below the new limit. Zero, negative values and malformed config are rejected.
Unlimited removes the bridge's numeric cap; cross-family author constraints
continue to apply. Native GPT capacity is managed by Codex and is unchanged.

## Off-peak preference

The shared policy is **disabled by default**. For a configured Z.ai Coding Plan
alias, enable it through MCP:

```text
set_cost_policy(enabled=true, model="zai-coding-plan/glm-5.3")
```

The recovery CLI uses the same shared configuration:

```sh
python3 plugins/mixed-agents/bridge/server.py call set_cost_policy \
  '{"enabled":true,"model":"zai-coding-plan/glm-5.3"}'
```

Defaults are `Asia/Shanghai`, peak `14:00`–`18:00`, ISO weekdays `[1,2,3,4,5]`.
Start is inclusive and end exclusive; weekends are off-peak. These match the
[Z.ai Coding Plan rule](https://docs.z.ai/devpack/overview) checked 2026-10-11:
off-peak model usage consumes 50% of standard credits. Do not apply that rule to
Ark or another provider merely because its model name is GLM-5.3.

You can pass `timezone`, `peak_start`, `peak_end` and `peak_weekdays` to configure
a user-defined schedule. Windows must remain within one day (start < end).
Omitted fields retain the stored values. `set_cost_policy(enabled=false)` turns
the preference off. Policy changes preserve `kimi_concurrency` and other shared
settings. Enabled policies require host IANA timezone data.

At off-peak, `select_model` prefers only the configured exact alias among
eligible top-score ties, including when another provider is the catalog's
`routing_default`. Better task fit, context feasibility, authorship independence
and capacity still take precedence. At peak or when disabled, ordinary eligible
family rotation resumes. Explicit `spawn_agent(model=...)` is never rewritten.

`model_catalog` and `select_model` expose the policy and evaluated period;
selection reports `cost_preference_applied`. This does not verify account access,
quote prices, lock an entire run to its launch-time rate, stop running workers,
or defer tasks until another time. See [acceptance criteria](docs/COST_POLICY_SPEC.md).

## Recovery and diagnostics

### Selecting and switching CLI models

Use the exact configured alias in `spawn_agent(model="kimi-for-coding/k3", ...)`.
The catalog exposes aliases with distinct credential sources separately, even
when they share a remote model. `kimi-code/k3` uses the managed login; it must
not replace an explicitly requested `kimi-for-coding/k3` API-key alias.

For an idle agent, `followup_task(agent_id, message, model="kimi-for-coding/k3")`
changes the model while retaining its session history. Omit `model` to retain
the current model. GLM aliases work the same way. Wait or interrupt an active turn
before changing its model. Implementation/test agents cannot switch between
model families, preserving their persistent authorship; exploration/review agents
may switch CLI families while keeping the session.
Native GPT follow-up does not support a model override through this bridge.

Catalog observations are historical results for each exact alias, not a live
quota check. A quota failure on one alias says nothing about another alias's
account. `capacity_exceeded` means the local concurrency limit is occupied;
an upstream quota/auth error is a separate provider response.

### Timeouts and errors

`spawn_agent(timeout_seconds=900)` limits each Kimi turn; it is separate from
`wait_agent(timeout_ms=...)`, which waits at most 60 seconds and does not cancel
an agent. A turn timeout stops its process group; inspect edits and the captured
session before resuming.

| Result | Meaning and next action |
| --- | --- |
| `capacity_exceeded` | Shared local slots are occupied. Use eligible native GPT or wait; preserve test-family independence. |
| `provider_capacity_exceeded` | Z.ai slots are occupied; choose eligible Kimi/GPT or wait. |
| `provider_quota_reserve` | Observed Z.ai remaining usage reached the configured margin; inspect partial outputs, use eligible alternatives, or resume after a fresh reading above threshold. |
| `provider_usage_unavailable` | An enabled guard cannot verify usage and pauses that provider. Inspect the safe error state; unknown is not unused quota. For Kimi aliases, follow the `kimi_recovery` probe action when present. |
| Upstream 403 / usage limit | Provider/account response. Verify the exact alias's account in Kimi; another provider may have different access. |
| Auth failure | Check the selected provider's existing Kimi login/configuration. |
| `invalid_argument` model | Read the catalog and use an exact recognized configured alias. No automatic alias substitution. |
| `cannot_resume` | No safe captured session ID. Inspect edits before starting a separate task. |
| Turn timeout | A local deadline was reached; it does not establish account quota exhaustion. |

### Log pruning

Bridge logs for terminal Kimi jobs can be pruned without deleting active jobs,
claims, prompts, Kimi sessions or credentials:

```sh
python3 plugins/mixed-agents/bridge/server.py call prune_logs '{"older_than_days":30}'
```

The default preview (`dry_run=True`) prints names and counts only. Pass
`dry_run=False` to delete. Symlinks, orphaned workers, held slots and unrelated
files are excluded.

Inspect running agents without relying on the MCP connection:

```sh
python3 plugins/mixed-agents/bridge/server.py call list_agents '{}'
```

Use `followup_task` on a stored ID to retain session history. First turns stopped
before Kimi emits a session ID may be unresumable. Never guess the most recent
session in a shared cwd.

## Data destinations, permissions and log retention

The bridge stores state under `~/.local/state/codex-mixed-agents` by default
(or `MIXED_AGENTS_STATE_DIR` for an isolated test root). State files are created
with private permissions (0700 directory, 0600 records/logs). Set
`MIXED_AGENTS_STATE_DIR` only for an isolated test installation; separate state
roots do not share capacity. The cap covers all bridge-launched Kimi CLI workers,
including GLM. Independent Kimi app/CLI sessions outside this plugin.

CLI workers have the caller's CLI permissions; profile instructions are not an
OS sandbox. Assign scoped tasks and use isolated worktrees where necessary.

Logs and results redact configured credential values, Bearer tokens, JWTs and
common secret assignment patterns before leaving the bridge. Redaction is
heuristic and cannot guarantee that every secret or code datum is removed.
Review logs before sharing them. Do not commit local state, logs, credentials or
Kimi session histories.

## Upgrades, uninstall and fresh environments

To upgrade after source changes, re-run:

```sh
codex plugin add mixed-agents@kai-mixed-agents
```

Then open a new Codex chat or restart the app so the updated Skill and MCP
tools are loaded. Existing chats may continue to use the old tool schema.

To uninstall:

```sh
codex plugin remove mixed-agents
```

The bridge does not remove its state directory automatically; delete
`~/.local/state/codex-mixed-agents` manually if you no longer need logs or
history. Uninstalling does not affect the Kimi Code CLI installation or its
credentials.

In a fresh environment, install Kimi Code CLI, authenticate, verify `python3` is
3.11+, install the plugin, open a new chat, and run `doctor()` before the first
delegation. Validation records in [VALIDATION.md](docs/VALIDATION.md) show which
model aliases and platform combinations were actually exercised locally; do not
assume all aliases or all OS versions behave identically.

## Development

Use spec-driven development (SDD): describe the intended behavior and acceptance
criteria before implementation. Keep the spec proportional to the change: a
small fix may need a few sentences; a stateful feature needs its failure and
concurrency boundaries recorded.

Assign implementation and tests to different model families. The test author
derives meaningful checks from the spec and may inspect the code for overlooked
edge cases. Coordinate dependencies and keep concurrent writers in disjoint
files; independent authorship does not require simultaneous implementation.

Where useful, borrow TDD's test-first red-to-green loop: observe a test failing
for the intended missing behavior, implement that behavior, then observe it
passing. Implement-then-test is also allowed. Write clear, maintainable code
throughout; make necessary cleanup when it helps correctness or readability,
without requiring a separate refactor phase.

Run offline tests with `python3 -m unittest discover -s tests -v`. Fake CLI
processes should exercise real locking and lifecycle behavior without API costs.
Build a shareable ZIP after checks pass:

```sh
python3 scripts/check_release.py --output dist/mixed-agents-0.7.4.zip
```

Extract the ZIP and run the installation commands from its root. To share via
a Git marketplace, publish this repository and use its real URL with
`codex plugin marketplace add URL`; no repository URL is assumed here.

Run a bounded live smoke after those pass. Record the chosen model IDs and
validation boundaries, rather than assuming all configured aliases work.

Validate packaging with:

```sh
python3 scripts/check_release.py
```

This scans manifests, history and tracked files for credential-like patterns and
release-blockers. It prints categories and locations only, never matched values.

Authorship in this build: Kimi implemented `catalog.py`; GPT implemented
`runtime.py`/`server.py`. GPT authored catalog tests; Kimi authored runtime/MCP
tests. GLM-5.3 implements the off-peak policy; Kimi independently authors its
tests. Earlier releases also received native GPT review of workflow and
concurrency fixes. See [validation](docs/VALIDATION.md) for this release's checks.
The staged configuration module and runtime/MCP integration are GPT-authored;
their independent lifecycle/API tests are Kimi-authored. Stage 2 Provider storage
includes Kimi contributions completed by GPT; the console is GPT-authored. GLM-5.3
independently authored stage 2 resource/API/HTTP/browser acceptance tests. Stage 3
uses GPT dispatch implementation/Kimi acceptance tests and Kimi materialization
implementation/GPT acceptance tests.

## License and notices

Licensed under the MIT License. See [LICENSE](LICENSE).

See [CHANGELOG.md](CHANGELOG.md) for release notes and
[SECURITY.md](SECURITY.md) for reporting and data-handling guidance.
