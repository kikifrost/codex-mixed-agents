# Operations and troubleshooting

[English README](../README.md) | [简体中文 README](../README.zh-CN.md)

Inspect running agents, recover sessions and manage local data. Run shell commands from the repository root.

- [Runtime behavior](#runtime-behavior)
- [Self-check](#self-check)
- [Recovery and diagnostics](#recovery-and-diagnostics)
- [Data destinations, permissions and log retention](#data-destinations-permissions-and-log-retention)

## Runtime behavior

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
including GLM. Independent Kimi app/CLI sessions outside this plugin are not
counted toward its cap.

CLI workers have the caller's CLI permissions; profile instructions are not an
OS sandbox. Assign scoped tasks and use isolated worktrees where necessary.

Logs and results redact configured credential values, Bearer tokens, JWTs and
common secret assignment patterns before leaving the bridge. Redaction is
heuristic and cannot guarantee that every secret or code datum is removed.
Review logs before sharing them. Do not commit local state, logs, credentials or
Kimi session histories.
