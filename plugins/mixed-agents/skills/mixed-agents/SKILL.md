---
name: mixed-agents
description: Delegate work across Kimi CLI agents using Kimi or GLM and native GPT subagents, choose models by task fit, and manage their lifecycle together. Use when the user or applicable instructions request subagents, parallel agent work, Kimi or GLM delegation, or mixed model implementation and testing. Does not authorize delegation for unrelated tasks.
---

# Mixed agents

Use Kimi, GLM and GPT as peers. Choose by task requirements and available capacity,
not by familiarity with GPT or a fixed provider priority. This skill supplies
workflow instructions; the `mixed_agents` MCP server manages Kimi processes.
GPT agents continue to use native collaboration tools.

## Select and dispatch

1. Give this parent task a unique `scope` (reuse it throughout the task). Assign
   each independently implemented/tested component a stable `work_unit` within
   that scope. Roles are `implementation`, `tests`, `review`, `exploration`,
   `research`. Define file ownership and acceptance criteria before dispatch.
2. Read the actual native spawn tool's model choices. Pass those names as
   `native_models` to `model_catalog` / `select_model`. Never infer availability
   from an old list. Read [model selection](references/model-selection.md) for
   task-fit criteria. Pass complexity, estimated context and latency needs.
   For GLM, automatically consider only configured 5.3 and 5.3 Flash. Other
   recognized names are explicit-dispatch compatibility aliases, not additional
   models to rank; do not guess their provider forwarding target.
3. Consider the returned candidates from all available families and give a short reason
   for selection. Explicit user model requests win, subject to hard constraints.
   Equal-fit choices rotate families; do not always pick the first GPT model.
   Configured Kimi aliases are not proof of account entitlement; disclose errors.
   Preserve explicit aliases, including their prefix: `kimi-for-coding/k3` and
   `kimi-code/k3` may use different credentials. The catalog exposes both;
   `routing_default` only controls automatic selection. Historical errors are
   per alias, not live quota readings. Never apply another alias's quota failure
   to the requested one or confuse local concurrency with remote account quota.
4. **Mandatory Kimi recovery preflight.** Before excluding a configured Kimi
   alias for unknown usage — or accepting a GLM/GPT fallback for that reason —
   run `select_model` with the real `cwd`, scope, work_unit, role, complexity,
   context and latency needs first, and execute only that selection's
   `kimi_recovery` (or the same list carried by a `no_eligible_model` error).
   `model_catalog`'s `kimi_recovery` is global discovery across all configured
   accounts; it is not scoped by Project permissions, family, context or
   capacity, so never probe catalog entries the selection did not return. For
   each returned entry (at most one per affected account), execute its
   `next_action` exactly once — `probe_kimi_usage` with the exact alias — then
   rerun `select_model` once. Usage
   error `credential_expired` means an expired local OAuth access token pending
   CLI refresh, not invalid login, an expired subscription or an unusable
   account. A `probe_cooldown` result, or usage still unknown after the probe,
   stops that account until the cooldown elapses: continue with other eligible
   families and never loop probes or probe several aliases of one account.
   Recovery guidance appears only while the usage guard and `probe_on_unknown`
   are enabled; a known reserve/exhausted reading has no recovery action.
   Unknown-usage aliases stay out of runnable candidates until usage is
   verified, and family rotation among healthy equal-fit candidates resumes
   after a successful probe.
5. Call MCP `spawn_agent` with the selected model, scope/work_unit/role, absolute
   cwd and a self-contained task (requirements, allowed files, output and checks).
   Pass the target absolute `cwd` to `select_model` as well as `spawn_agent`;
   this resolves the longest registered local Project and its saved template.
   An explicit `project_id` must match that cwd. Kimi Code permissions and
   Project/shared/provider/account caps are enforced again at dispatch and each
   turn boundary. Never grant a new alias or change a binding to bypass these
   constraints. Native GPT is outside Kimi Code templates and cannot be a silent
   fallback within a Project context; do not evade that boundary with a raw spawn.
   Kimi returns a stable `kimi:...` ID. A GPT selection returns a `native_action`:
   perform that native spawn, then `register_native_agent` with the actual ID.
   Check native capacity before dispatch. If native spawning fails without an
   agent ID, call `release_native_reservation` with its reservation_id. Reconcile
   visible pending reservations after reconnect; never remove confirmed authors.
   Never present a native action descriptor as a completed/spawned agent.
   Native model overrides may require a fresh bounded context rather than a
   full-history fork; follow the live spawn tool schema.

## Optional off-peak preference

Read `model_catalog.cost_policy` before automatic selection. The policy is
portable and disabled by default; use `set_cost_policy` only when the user asks
for a change. It shares `config.json` with concurrency and preserves the cap.

The default schedule is `Asia/Shanghai`, Monday–Friday 14:00–18:00 peak, with
all other hours off-peak. This rule was verified for Z.ai Coding Plan, not every
GLM provider. Bind it to the user's exact configured alias. At off-peak, the
selector prefers that alias only among eligible top-fit ties. During peak,
disabled, missing target or incompatible family/capacity, ordinary routing
continues. Check `cost_preference_applied` and the evaluated period in the result.

Explicit model requests still win within hard constraints. Do not switch or
cancel ongoing workers at a window boundary. Pricing periods are routing hints,
not live account/quota readings or a guaranteed rate for a whole session. This
feature does not schedule tasks or authorize future automation.

## Mandatory constraints

- All Kimi versions are family `kimi`, all GLM versions are family `glm`, and
  all GPT versions are family `gpt`. Kimi/GLM use backend `kimi_cli`; GPT uses
  backend `codex_native`. Stable `kimi:` IDs identify the CLI backend, not family.
  **For the same work_unit, implementation and test authors MUST be different
  families.** Record actual authorship as work occurs, including code the parent
  already wrote. For work already written by a parent, call
  `record_authorship` with its actual family and attribution. Do not manufacture
  a child agent or treat a dispatch reservation as evidence of authorship.
  SDD is primary: state behavior and acceptance criteria first, proportional to
  the change. Independent test authors derive checks from the spec and inspect
  code for overlooked edge cases. Coordinate dependencies; family independence
  does not require simultaneous writes. Optionally borrow TDD's test-first
  red-to-green loop: observe a meaningful failure for missing behavior, then a
  passing implementation. Keep code clear throughout and make necessary cleanup
  without a forced separate refactor phase. Test-after is also allowed.
  Keep the same work_unit for test-first work so
  the test-family assignment constrains the later implementation family.
- Kimi CLI concurrency is shared across this installation, including Kimi and
  GLM workers and resumed turns.
  Default **5**; the user can configure any nonnegative integer (0 = unlimited) or **unlimited**.
  Read the effective `kimi_cap` / `kimi_unlimited` in the catalog. Use
  `set_concurrency(limit=NUMBER)` or `set_concurrency(limit="unlimited")` when the
  user requests a change. Configuration persists in the state root's `config.json`
  (`kimi_concurrency` key) and is read live by every bridge process. Lowering the
  cap preserves existing tasks and blocks new admission until capacity is free.
  Do not change the configured limit merely to fit a batch. Every Kimi invocation
  goes through the bridge so slot accounting holds.
  Do not launch extra CLI processes or nested Kimi agents to evade the limit.
- If a finite configured Kimi limit is full, dispatch additional eligible tasks
  to native GPT. A `capacity_exceeded` response is NOT a started Kimi task.
  Cross-family test independence outranks overflow: tests of GPT-authored code
  use Kimi or GLM and wait for a CLI slot if needed. Use available GPT capacity
  for other work meanwhile.
- Don't silently replay failed write tasks with another model. Inspect partial
  changes and decide whether to resume the same agent or delegate a narrowed
  repair. Availability failures are evidence, not reasons to permanently rank
  GPT first. Keep credentials out of prompts, logs and generated artifacts.
- Parallel writers use disjoint ownership or isolated worktrees. Kimi's worker
  profile constrains delegation, not OS filesystem access. A prompt naming
  allowed files is not a sandbox; preserve the user's/repository's permissions.

## Kimi Code five-hour reserve

Read `model_catalog.kimi_policy` alongside shared capacity. Recognized Kimi Code
accounts use an observed **5% remaining** cutoff, independent of Z.ai's 10%
policy. Use `set_kimi_policy(usage_guard=true, stop_remaining_percent=5,
probe_on_unknown=true)` when the user requests this protection. Portable guard
and automatic bootstrap defaults are disabled. Read the exact account with
`provider_usage(provider="kimi", model="EXACT_ALIAS")`; unsupported providers,
unknown usage and historical access failures never mean an unused account.

When usage is unknown, `probe_kimi_usage(model="EXACT_ALIAS")` permits one fixed,
tool-free bounded inference through shared capacity, then reads usage again.
Enabled automatic bootstrap performs the same operation before idle admission.
Catalog and selection surface this as `kimi_recovery`: one entry per affected
account carrying the exact alias, its safe `usage_error`, a fixed `next_action`
(`probe_kimi_usage` with that alias) and a plain reason — never credentials,
fingerprints, private paths or raw provider errors. Selection guidance is
limited to otherwise eligible aliases (permissions, family, context and
capacity all pass); the catalog list is unscoped global discovery, so act only
on entries a real selection returned. Aliases on one account share a durable
allowance; concurrent calls and restarts
cannot reset it. Unknown after the probe blocks work for that account. A new
attempt is permitted only after the configured cooldown (default 15 minutes;
`set_kimi_policy(probe_cooldown_minutes=NUMBER)`) or a changed account
identity. Known reserve/exhausted accounts never bootstrap. Catalog, selection
and doctor perform no inference. Active and queued unknown usage stops rather
than probing; inspect preserved outputs before continuing.

At remaining <=5%, the guard blocks new work and interrupts affected workers,
preserving edits, outputs and captured sessions. Consider healthy accounts and
eligible GLM/GPT peers by fit and family independence. Normal CLI OAuth refresh
may run during a bootstrap; the bridge only reads the existing credentials.
Reporting delays and in-flight requests prevent a hard final-balance guarantee.

## Self-service account and policy console

The console header provides **编辑并发上限** on every page. It exposes shared CLI
(Kimi + GLM), Z.ai Provider and Kimi account limits without choosing a Provider.
Numeric 0 removes only that layer's cap; legacy `unlimited` is still accepted by
MCP/policy storage. Templates also support 0 for single-Project concurrency.
Other caps, model permissions and quota guards remain in effect. Do not change
any configured value merely to make room for delegation.

On a desktop host supporting OpenAI structured settings, the plugin details page
offers **打开配置控制台**. The action uses `open_console` to start a free-port
loopback service and request opening the default browser; repeat calls reuse the
service in that MCP process. If automatic opening fails, show the returned `url`.
Reading plugin settings does not start a service or perform inference. The service
ends with its MCP connection. The entry needs the local bridge, not a web host.

Run `python3 bridge/console.py --open` from the installed plugin root to configure
shared/provider concurrency, account order and caps, guard thresholds, probe
cooldown and off-peak preferences. It binds only to loopback and loads no inference.
`set_kimi_accounts(order=["kimi-code", "kimi-for-coding"], limits={})` updates the
same durable policy through MCP. Automatic Kimi choices prefer that account order
within each eligible profile; missing, quota-blocked or account-full candidates
allow backup accounts. Exact requested aliases remain exact; family rotation and
task fit remain mandatory. Account caps default to unlimited and same-credential
aliases share the strictest cap. Shared-full capacity still requires GPT or waiting.

## Z.ai provider capacity and reserve

Read `model_catalog.zai_policy` in addition to the shared CLI cap. GLM aliases
whose configured provider has HTTPS origin `api.z.ai` share a separate default
cap of **3**. This does not apply to GLM from other providers or to Kimi. Use
`set_zai_policy(limit=NUMBER)` or `limit="unlimited"` only for user-requested
changes. Lowering preserves active workers. When Z.ai is full, select eligible
Kimi or GPT according to fit and family independence; never bypass a cap through
another alias or direct CLI launch.

`provider_usage()` performs a bounded read-only request with existing local
credentials and returns normalized five-hour usage or an explicit unavailable
state. It does not run inference. The portable `usage_guard` default is false;
enable with `set_zai_policy(usage_guard=true)` when the user requests reserve
protection and usage is readable. Default `stop_remaining_percent=10`, configurable
strictly above 5 and below 100. An enabled guard checks admissions, queued turns
and running workers using readings no older than 30 seconds. At or below the
threshold, or when usage is unknown, it blocks Z.ai and interrupts active Z.ai
workers with a reason, preserving edits, outputs and captured sessions while
clearing queued messages. Resume requires a trustworthy reading above threshold. Change account credentials
or provider assignments only after affected workers are idle: each CLI turn uses
its startup account, while the reader resolves the current saved configuration.
Do not silently replay interrupted write tasks with another model.

Use other eligible providers/families for additional work. Never treat a historical
access error, a reset timestamp or token logs as a current quota reading. Provider
reporting delays, requests already in flight and other clients can spend beyond
the observed threshold; describe the margin honestly, without guaranteeing a hard
balance. Off-peak preference does not override capacity or this usage guard.

## One lifecycle vocabulary

| Operation | Kimi / GLM through Kimi CLI | Native GPT |
| --- | --- | --- |
| Start | MCP `spawn_agent` | Native spawn from the returned action, then register |
| Inspect | MCP `get_agent` / `list_agents` | Native status; registry holds metadata only |
| Wait | MCP `wait_agent` | Execute native wait action; combine summaries |
| Send message | MCP `send_message` | Native send message |
| Continue | MCP `followup_task` | Native follow-up task |
| Stop | MCP `interrupt_agent` | Native interrupt |
| Set capacity | MCP `set_concurrency`: number or unlimited | Native capacity remains platform-managed |

For a registered GPT ID, MCP lifecycle calls return `native_action` instructions
for the parent to execute. Report real native state from the native tools.
For mixed waits, wait boundedly for each backend and collect actual results.
Keep tool wait calls at most 60 seconds; provide meaningful progress updates.

Kimi messages sent during a turn are delivered at its **next turn boundary**.
They cannot steer an in-flight model/tool operation. For an urgent change, stop
the current Kimi turn, inspect side effects, then continue the same session if
`resumable=true`. A first turn stopped before Kimi emits its session ID may be
unresumable; do not use `--continue` or guess another session from the cwd.
Stopping does not undo previous edits. Native UI integration of external Kimi
threads is not promised; the unified experience is the parent conversation,
tool vocabulary, stable IDs, status, follow-ups and results.

To change an idle Kimi agent's model, use `followup_task(agent_id, message,
model="EXACT_CONFIGURED_ALIAS")`. It keeps that agent's captured session history.
Omitting `model` keeps the existing alias. While active, wait or stop and inspect
side effects before changing the model; a model change is not queued. Native GPT
follow-up has no model override; a new native agent is required for that change.
GLM variants can switch within the same family. Implementation/test agents
cannot switch model families: persistent authorship must not be rewritten.
Exploration/review agents may switch between configured Kimi and GLM aliases
while keeping their CLI session.

## Availability and recovery

The MCP server's registry survives a server/client disconnect, and Kimi workers
run independently. Reuse stored IDs with `list_agents(scope=...)` after reconnect.
Do not resubmit a task merely because the MCP server restarted.

If the MCP tools are not loaded yet, locate `bridge/server.py` at the plugin root
(two directories above this SKILL.md's directory) and invoke its local CLI:

```text
python3 /absolute/plugin/root/bridge/server.py call TOOL JSON_ARGUMENTS
```

This uses the same registry and cap. Use properly quoted JSON or a subprocess
argument list; no shell interpolation of task text. Do not reinstall repeatedly
inside an active chat. A new chat/restart may be required to load installed MCP
tools. Run offline `doctor()` before a large delegation batch, then a small exact-alias
connectivity check when authorized. Offline readiness does not verify account
identity, token validity, model access or remaining quota.

The runtime requires macOS/Linux and Python 3.11+. Kimi Code 2.1.1 reads
`KIMI_CODE_HOME/config.toml`; each bridge child receives the stored config
directory for first and resumed turns. Keep credentials in that installation.
Use `prune_logs(older_than_days=30)` to preview terminal bridge logs; only pass
`dry_run=false` when deletion is requested. It retains jobs, prompts, claims and
Kimi sessions. Redaction is heuristic: inspect exported logs before sharing.
