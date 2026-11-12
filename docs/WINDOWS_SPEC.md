# Native Windows support

## Behavior

Run the existing Mixed Agents workflow on native Windows 10/11 x64 with Python
3.11+ and Kimi Code 2.1.1+, without WSL, Git Bash, a Unix `ps` executable or new
third-party Python runtime dependencies. Keep the existing macOS/Linux workflows,
MCP schemas, durable agent IDs, SQLite state, account policies and model-family
independence intact. Native GPT dispatch remains a genuine host action.

Support Windows absolute paths containing spaces and non-ASCII characters.
Resolve the installed native `kimi.exe` and use the existing account/config;
never copy credentials between machines, log their values or disable quota
guards to make a verification pass. Document a Windows launcher configuration
using the actual Python executable instead of assuming `python3` is available.

## Acceptance criteria

1. Launcher, server, catalog, quota readers and offline doctor import/start on
   Windows. Relocated packages can initialize MCP and discover all public tools.
   Reports identify platform/version honestly and do not equate offline readiness
   with valid authentication or model access.
2. CLI workers start, capture Unicode stdout/stderr without blocking on large or
   partial output, retain session IDs and support queued messages and follow-up.
3. Shared CLI, provider and account limits remain cross-process and race safe.
   Lowering limits preserves existing work. A slot remains occupied throughout
   manager-to-worker and worker-to-CLI handoff, including when a worker dies but
   its CLI child remains alive. Never unlock/reacquire through an admission gap.
   Quota-reader single-flight locks remain cross-process as well.
4. Cancellation, timeout and enabled quota protection stop the CLI and its child
   process tree; do not kill unrelated or PID-reused processes. Reconnection
   does not duplicate work. Dead-worker recovery preserves outputs and captured
   sessions and does not free a slot while descendants still hold it.
5. Keep POSIX lock and process-group semantics working on macOS/Linux. The full
   applicable offline suites run on Windows, macOS and Linux in CI. Tests that
   specifically prove POSIX behavior may remain platform specific; Windows must
   have equivalent native lifecycle/concurrency coverage rather than broad skips.
6. Package manifests, runtime version, release validation and installation docs
   agree. Release checks work on all supported platforms. Do not change UI design
   or add unrelated features.
7. On SSH host `office-win`, install the reviewed package into a dedicated user
   directory and register it with the existing Codex installation while preserving
   existing configuration and the native Branchline connector. Verify actual
   installed MCP discovery, offline checks and deterministic process lifecycle
   tests, then one bounded live Kimi task plus same-session follow-up when account
   usage permits. Report any unavailable provider/model separately. Keep the host
   Windows native and leave WSL uninstalled.

## Verification and authorship

Production code is authored by a GLM-family worker. Independent tests and CI are
authored by GPT-family agents from this spec and observable behavior. Give writers
disjoint files. Retain regression coverage and report actual platform evidence;
mocked Windows branches on macOS are not native Windows acceptance.

Desktop Computer Use capabilities are separate from the bridge runtime. This
change does not grant Kimi/GLM workers Codex desktop tools or claim DOCX visual
acceptance. Windows desktop/tool availability may be inspected independently.
