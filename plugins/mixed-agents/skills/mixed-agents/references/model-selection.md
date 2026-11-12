# Task-fit guidance

Kimi model source checked 2026-10-29; Codex source checked 2026-10-03:
- [Kimi model configuration](https://www.kimi.com/code/docs/en/kimi-code/models.html)
- [Codex subagents and model selection](https://learn.chatgpt.com/docs/agent-configuration/subagents)

GLM sources checked 2026-10-09:
- [Z Code model configuration](https://zcode.z.ai/en/docs/configuration)

These are routing heuristics based on documented characteristics, not local
benchmarks or a general claim that one vendor is better. Read the live catalog
and actual native tool choices. Context sizes are configured limits, not proof
of account entitlement. Capability and task-fit reasons outrank provider names.

| Candidate | Useful task shape | Tradeoff |
| --- | --- | --- |
| Kimi K3.1 Pro | Complex coding, deep reasoning, long-horizon agent work | Up to 1M subject to plan; thinking required; start a fresh session from older models |
| Kimi K3.1 Pro Highspeed | Same demanding work when output latency matters | Same capability, about 2–3× output speed and twice Pro's quota use; thinking required |
| Kimi K3-256K | Deep debugging, complex changes, algorithms within 256K | Full K3 reasoning with smaller context/quota footprint |
| Kimi K3 | Cross-module or very large-context work | 1M requires entitlement; avoid paying that quota without a need |
| Kimi K2.8 Preview | Regular development, code completion, efficient workhorse | Distinct regular K2.8 Preview profile |
| Kimi `kimi-for-coding` | Regular development via coding alias | Alias currently maps to K2.8 Preview and can change |
| Kimi K2.7 | General K2.7 coding work | Distinct regular K2.7 profile |
| Kimi `kimi-for-coding-highspeed` | Small bounded edits, quick exploration or review | Fast generation uses more quota; don't assume cheapest |
| GLM 5.3 | Complex reasoning, deep debugging, implementation and review | Use configured context and observed access; consider latency and quota |
| GLM 5.3 Flash | Small bounded edits, quick exploration or review | A latency tier; do not infer image/video capability from the suffix |
| GPT Sol | Implementation, review and multi-step work | Use a currently exposed Sol choice; no provider-wide preference |
| GPT Astra | Ambiguous, especially demanding reasoning | Higher latency/token cost can be justified by task difficulty |
| GPT Luna | Short, clear, bounded exploration or changes | Keep scope appropriately narrow |

For routine work, consider K2.8 Preview, `kimi-for-coding`, GLM 5.3 and
GPT Sol together. For deep reasoning, consider K3.1 Pro (or Pro Highspeed when
latency matters), K3, GLM 5.3 and an
appropriate GPT reasoning model together. For quick work, compare K2.7,
`kimi-for-coding-highspeed`, GLM 5.3 Flash and GPT
Luna. Kimi and GLM share the same CLI backend and concurrency cap, but remain
separate families: a Kimi implementation still requires GPT (or GLM) tests,
and GLM variants alone do not satisfy family independence. Context needs,
multimodal requirements, observed reliability, role independence and
available slots determine dispatch.

Implementation and tests for a component use different families among Kimi,
GLM and GPT. K3 versus K2.8, GLM 5.3 versus GLM 5.3 Flash, or Sol versus Astra alone
does not satisfy family independence.
Do not send the implementation author's reasoning as the test author's answer
key: provide the spec, relevant code and expected behavior instead.

Z.ai GLM provider capacity and an enabled usage reserve guard are hard eligibility
constraints alongside shared CLI capacity. Read current catalog capacity/policy;
when Z.ai is blocked, consider Kimi, native GPT and other configured providers by
fit and family independence. Historical outcomes cannot replace live quota data.

With the Kimi usage guard and bootstrap probe enabled, an otherwise eligible
Kimi alias whose exact account usage is unknown stays out of runnable candidates,
but catalog and selection return `kimi_recovery` actions — one bounded
`probe_kimi_usage` call per affected account. Catalog actions are global
diagnostics: first select with the actual task and workspace, then execute
only that selection's actions (including those in `no_eligible_model`) once
and rerun selection; a cooldown or still-unknown result stops that account without retry
loops. `credential_expired` is an expired local OAuth access token pending CLI
refresh, not proof of invalid login or an unusable account. Historical access
failures remain advisory per alias and never substitute for the live per-account
reading.

Current GLM candidates (user-confirmed 2026-10-12) are 5.3 and 5.3 Flash only.
Legacy names are retained for explicit dispatch compatibility, not automatic
model comparison; their provider forwarding target is not guessed.
