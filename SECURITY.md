# Security

## Reporting a vulnerability

If you discover a security issue, use
[GitHub private vulnerability reporting](https://github.com/kikifrost/codex-mixed-agents/security/advisories/new)
to contact the maintainer. Do not open a public issue with exploit details.

Public security discussions are welcome after a fix is available, but keep
reports sanitized and do not include credentials, session tokens or private
data.

## Scope and boundaries

This plugin runs as the invoking user and launches the Kimi Code CLI as a child
process. It does not provide its own sandbox. Bridge workers inherit the parent
process permissions and can read or write files the parent can access. Run
scoped tasks and use isolated worktrees where necessary.

The bridge stores state under `~/.local/state/codex-mixed-agents` by default
(or `MIXED_AGENTS_STATE_DIR` for an isolated test root). It does not copy or
persist Kimi credentials; authentication remains in the user's existing Kimi
Code CLI installation.

The optional Z.ai usage reader accesses the existing API key only in memory
for a fixed read-only HTTPS request to `api.z.ai`. Redirects are rejected and
response size/time are bounded. Only normalized quota metrics and a credential
fingerprint may be cached; raw response bodies and credentials are not stored.
An enabled usage guard fails closed when the reading is unavailable. The
undocumented provider endpoint can change; delayed metrics and untracked clients
mean reserve protection cannot guarantee a hard account balance.

Kimi Code usage is read from the fixed HTTPS `api.kimi.com/coding/v1/usages`
endpoint using the selected account's existing API key or file OAuth token.
Only sanitized metrics and hashed identities are cached. Optional bootstrap
performs one tool-free bounded inference in an isolated workspace, within
shared capacity, then requires a trustworthy usage reading before real work.
Its durable account allowance is shared across aliases and restarts. Portable
bootstrap is disabled; enabling it can consume quota even when reading later
fails. CLI authentication refresh remains the CLI's responsibility. Unknown
usage on active turns stops them; no bootstrap retries are run in those turns.

## Data handling

Logs and results redact configured credential values, Bearer tokens, JWTs and
common secret assignment patterns before leaving the bridge. Redaction is
heuristic and cannot guarantee that every secret or code datum is removed.
Review logs before sharing them.
