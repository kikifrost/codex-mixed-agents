# Shareable beta acceptance criteria (0.2.0)

This records the 0.2.0 acceptance criteria. The 0.3.0 GLM extension is defined in
[GLM_SPEC.md](GLM_SPEC.md) and supersedes the Kimi-only model restriction below.

Prepare a local/Git marketplace beta; no remote publication in this work.
Use Python 3.11+ standard library on macOS/Linux; Windows fails clearly before
importing the Unix runtime. Do not install dependencies or edit Kimi credentials.

## Configuration and runtime (GPT implementation, Kimi tests)

- Normalize and persist absolute `config_path`. Current Kimi Code 2.1.1 reads
  `$KIMI_CODE_HOME/config.toml` and has no `--config-file` option. Every child
  launch (first and resumed) must set its own `KIMI_CODE_HOME` to the parent of
  the stored config, without mutating the supervisor environment or copying
  credentials. Reject other basenames before slot/claim/queue mutation.
- Add `doctor()` to MCP/recovery CLI: bounded offline checks of OS/Python,
  CLI executable/version, readable TOML, supported/unsupported aliases,
  credential source presence (not values), plugin manifest versions and shared
  capacity/config. No inference, account quota claim, auth refresh or config edit.
  Missing/invalid dependencies/config return actionable sanitized checks.
- Add explicit `prune_logs(older_than_days=30, dry_run=True)` for bridge logs
  belonging to terminal Kimi jobs only. Transactionally exclude active/orphaned
  workers/held slots, symlinks and unrelated files. Do not delete jobs, claims,
  prompts, Kimi sessions or credentials. Default previews names/counts only.
- Redact credential assignments (JSON/TOML/header/query including non-sk values),
  Bearer tokens, JWTs and known configured credential values before logs,
  progress/results/errors and catalog observations leave the bridge. Preserve
  readable error causes. Never print or copy raw credentials. Explain that
  heuristic redaction cannot guarantee removal of every secret or code datum.

## Model catalog (Kimi implementation, GPT tests)

- Recognize provider-qualified remote identifiers by their final path component:
  current coding aliases, `k3.1-pro`/`kimi-k3.1-pro`,
  `k3.1-pro-highspeed`/`kimi-k3.1-pro-highspeed`,
  `k3`/`kimi-k3`, `k3-256k`/`kimi-k3-256k`, `k2.7`/
  `kimi-k2.7`, `k2.8-preview`/`kimi-k2.8-preview`, and explicit highspeed names.
  K2.7 regular and K2.8 Preview are distinct documented profiles; preserve exact
  dispatch aliases and live metadata, and retain Kimi/GPT family independence.
  Unknown/non-Kimi identifiers stay unsupported; disclose them in doctor.
- Profiles describe routing assumptions, not proven access or benchmark claims.
  Do not reinterpret arbitrary suffixes such as `kimi-k3-1` as known models.

## Packaging and documentation (Kimi implementation, GPT validation)

- Root portable MCP uses the Agent Plugins schema with only allowed stdio
  fields (type, command, args, env, cwd), and cwd `./`. Native `.mcp.json` may
  retain native timeouts and cwd `.`. Both use relative `./bridge/launch.py`;
  portable components are discovered at fixed root paths. No host-specific macro.
- Use PATH-based `python3` in MCP manifests and a small portable launcher with
  clear Python/OS dependency errors. All manifests/MCP handshake agree on 0.2.0.
- Remove required personal-machine paths from distributed guidance. Do not
  automatically replace another user's global AGENTS.md/preferences.
- Document initial install/login, model aliases/provider accounts, self-check,
  shared numeric/unlimited capacity, timeouts/errors, permissions/data destinations,
  logs/pruning, upgrades/uninstall, version compatibility and fresh-environment
  validation limits. Native GPT requires host collaboration tools and Kimi does
  not appear in the native Subagents UI.
- Add MIT LICENSE, CHANGELOG and security/reporting guidance with no invented
  repository URL. Add automatic macOS/Linux Python 3.11/3.12 offline CI with no
  API keys/inference. Validate JSON/manifests and package contents; exclude local
  state, credentials, caches and validation logs from release artifacts.

## Verification

SDD is primary. Implementation and tests use different model families. Optional
TDD uses red-to-green with clean code and no mandatory refactor phase. Run lifecycle/concurrency suite,
new negative/regression checks, installed MCP discovery and a relocated clean
package with isolated fake Kimi config/state. Scan tracked tree and Git history
without printing suspected secret values. Run one bounded exact-alias live smoke
and same-session follow-up. Do not claim remote CI or another OS ran locally.
Preserve a local commit and record verification gaps.
