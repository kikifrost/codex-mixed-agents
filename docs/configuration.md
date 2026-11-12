# Configuration

[English README](../README.md) | [简体中文 README](../README.zh-CN.md)

Model aliases, saved templates and resource policies. Run shell commands from the repository root. Usage guards and the off-peak preference are disabled by default; enable them explicitly when needed.

- [Supported model aliases](#supported-model-aliases)
- [Local policy console](#local-policy-console)
- [Templates and Project dispatch](#templates-project-dispatch-and-local-configuration-console)
- [Concurrency configuration](#concurrency-configuration)
- [Kimi five-hour usage reserve](#kimi-five-hour-usage-reserve)
- [Z.ai capacity and usage reserve](#zai-provider-capacity-and-usage-reserve)
- [Off-peak preference](#off-peak-preference)

## Supported model aliases

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
[console acceptance spec](console-spec.md).

To let a backup account take work after the primary reaches its own cap, set
the primary cap below the shared limit: for example, shared 5 and primary 3.
This does not bypass the shared cap. Change account login information through
the Kimi CLI.

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
See the [stage 1 contract](TEMPLATE_IMPLEMENTATION_SPEC.md),
[stage 2 contract and continuation](TEMPLATE_STAGE2_SPEC.md) and
[validation record](VALIDATION.md).

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
fall below the new limit. Negative values and malformed config are rejected.
Unlimited removes the bridge's numeric cap; cross-family author constraints
continue to apply. Native GPT capacity is managed by Codex and is unchanged.

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
[KIMI_GUARD_SPEC.md](KIMI_GUARD_SPEC.md) for the accepted behavior.

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
are explicit. See [the accepted spec](ZAI_GUARD_SPEC.md) for boundaries.

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
or defer tasks until another time. See [acceptance criteria](COST_POLICY_SPEC.md).
