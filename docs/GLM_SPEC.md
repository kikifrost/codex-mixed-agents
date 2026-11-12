# GLM support acceptance criteria (0.3.0)

## Current automatic candidates (0.5.1, 2026-10-12)

The user confirms GLM now resolves to 5.3 or 5.3 Flash. The routing catalog and
automatic selection expose only configured `glm-5.3` and `glm-5.3-flash` profiles,
including provider-qualified names, while preserving their exact account aliases.
Legacy recognized names remain valid for explicit dispatch and provider quota/cap
accounting; do not guess which of the two targets a provider forwards them to.
An old off-peak preference cannot reintroduce a hidden legacy candidate. Preserve
Kimi/GPT candidates, family independence, capacity, and real-usage guards.
Independent-family tests verify the public catalog/selection and compatibility.
The older acceptance criteria below describe compatibility, not current routing.

Extend the existing CLI bridge to configured GLM models, then finish the
Linux validation host installation check with its existing account. Do not copy credentials,
edit provider configuration, add dependencies or implement a separate API client.

## Catalog (Kimi implementation, GPT tests)

- Recognize exact remote final components `glm-4.7`, `glm-5`, `glm-5-turbo`,
  `glm-5.2`, `glm-5.2-highspeed`, `glm-5.3`, `glm-5.3-flash`,
  `glm-5.3-highspeed`, including provider-qualified remote IDs. Do not infer a
  model family from an arbitrary dispatch alias or accept invented suffixes.
- Preserve exact configured dispatch aliases, live context/capabilities and
  per-alias historical access observations. All GLM variants are family `glm`.
  Keep `load_kimi_models` as the compatible loader entry point, now covering
  supported CLI models. Export `CLI_PROFILES` combining Kimi and GLM profiles,
  and `FAMILIES = ("kimi", "glm", "gpt")` for runtime validation.
- Expose backend `kimi_cli` for Kimi/GLM catalog entries and `codex_native` for
  native GPT, independently of model family. Score GLM by documented task-fit
  heuristics alongside Kimi/GPT; capability and access claims remain unverified
  until observed. No image/video capability is inferred from a suffix alone.
- Keep unknown models, DeepSeek and unrelated providers unsupported. Redact
  credentials and provider connection details as before.

## Runtime and MCP (GPT implementation, Kimi tests)

- GLM uses existing detached CLI workers, stable `kimi:` IDs, profiles and
  captured sessions. The ID prefix identifies the CLI backend, not the model
  family. Use the actual catalog family for claims and agent records.
- Every CLI worker, including GLM, shares the existing `kimi_concurrency` cap
  and lock files. Preserve numeric/unlimited configuration and native GPT
  overflow only when family constraints permit it. Do not change the live cap.
- Author/avoid-family validation accepts `glm`. For paired implementation/tests,
  all variants within one family remain equivalent; different families are
  required regardless of dispatch order. All lifecycle operations, dead-worker
  recovery, cancellation and pruning apply to GLM as to Kimi.
- Equal top-score families rotate through every eligible family, rather than
  oscillating between two and starving the third. Capacity and family constraints
  outrank rotation; selection remains advisory.
- Idle follow-up may switch GLM variants while keeping the session. Revalidate
  the selected configured alias and family before slot/queue mutation. Reject
  cross-family switches for implementation/tests because their persistent
  authorship must not be rewritten; exploration/review may switch CLI families.
  Never silently reinterpret an existing paired-role claim after config changes.
- Doctor recognizes GLM-only valid configurations; readiness is still offline
  and never proves authentication, quota or model entitlement. Update MCP schema
  descriptions for CLI workers and model-family choices.

## Delivery and verification

- Update Skill, README, project guidance, behavioral spec and model-selection
  references; preserve cross-family authorship and explicit-provider selection; SDD is primary,
  optional TDD uses red-to-green with no mandatory refactor phase.
- Bump plugin/runtime manifests together to 0.3.0; preserve data compatibility
  and installed global guidance, and produce a checked shareable ZIP.
- Catalog tests use GPT; runtime/MCP tests use Kimi. Assign disjoint files.
  Exercise GLM aliases, secret exclusion, mixed CLI capacity, both directions of
  family conflict, three-family rotation, follow-up family safety and lifecycle.
- Run offline tests locally and on Linux validation host. Install the final package there,
  verify real Codex MCP discovery/handshake and doctor, then run one bounded
  tool-free GLM task and same-session follow-up through the installed bridge.
  Report exact successful/failed aliases and remaining verification limits.
