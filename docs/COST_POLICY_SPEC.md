# Off-peak model preference (0.3.1)

The user approved a configurable off-peak preference for the exact alias
`zai-coding-plan/glm-5.3`. Z.ai Coding Plan currently defines peak hours as
Monday–Friday 14:00–18:00 UTC+8; other hours, including weekends, consume half
the standard model credits. Source checked 2026-10-11:
https://docs.z.ai/devpack/overview

This is a selection preference, not a billing estimator or scheduling service.
Do not add a deferred queue, daemon, automation, inference retries or changes to
running agents. Do not transfer the rule to other aliases/providers by model name.

## Configuration and API

- Add `set_cost_policy(enabled, model=None, timezone=None, peak_start=None,
  peak_end=None, peak_weekdays=None)` to runtime/MCP/recovery CLI. `enabled` is
  required and strictly boolean. Other omitted/None arguments retain the stored
  setting, or defaults when none exists.
- Persist `cost_policy` in the existing shared state `config.json`, alongside
  `kimi_concurrency`. Use the existing transaction and private atomic replace
  pattern. Each setter preserves the other feature and unknown top-level keys.
  No credentials or connection details enter this configuration.
- Portable default is disabled. Defaults: model `zai-coding-plan/glm-5.3`,
  timezone `Asia/Shanghai`, peak_start `14:00`, peak_end `18:00`, peak_weekdays
  `[1,2,3,4,5]` (ISO weekdays). No inference/account probing in the setter.
- On enabling/changing the target while enabled, require an exact supported
  configured CLI alias. Disabled policy may retain a currently absent alias.
  Different complete aliases are never substituted automatically.
- Allow an IANA timezone, strict HH:MM times with start < end (same-day window),
  and a nonempty unique array of integer ISO weekdays 1–7 (bools are invalid).
  Reject invalid arguments without modifying disk/config/capacity/rotation.
  Stored null policy objects/field values are invalid (setter optional None is
  a separate omission rule); missing stored fields may use defaults. Invalid
  manually edited policy objects fail with `invalid_config`, without
  exposing raw settings or switching to a guessed timezone. Missing policy
  preserves all existing behavior. Use Python stdlib datetime/zoneinfo.
- Keep evaluation clock internal: no MCP request may forge the pricing time.
  A pure helper may accept an aware datetime for deterministic offline tests.
  Zoneinfo data is required when enabled; disabled default must work without it.
- Expose configured policy plus evaluated period in `model_catalog` and
  `select_model`, including `enabled`, exact `model`, timezone/window/weekdays,
  `period` (`disabled`, `peak`, `off_peak`) and boolean `model_available`. Selection
  also reports boolean `cost_preference_applied`. This does not claim live quota,
  account eligibility, monetary prices or a locked rate for a whole agent run.

## Selection behavior

- Evaluate the current policy once per selection in its configured timezone;
  start is inclusive, end is exclusive. Weekend handling follows ISO weekdays
  in that timezone, regardless of host/client timezone or DST elsewhere.
- Preserve model-fit scores, context feasibility, avoid-family/authorship
  filtering, shared capacity and native overflow rules before applying cost.
- Outside peak, prefer only the configured exact alias when it is eligible and
  tied at the highest fit score. Never boost it above a better fitting model.
  Explicit `spawn_agent(model=...)` remains unchanged by policy.
- The target may not be the catalog's `routing_default` (e.g. `ark/glm-5.3`
  currently wins deduplication). Admit the exact target as an additional
  automatic candidate only while the preference is active, without altering
  catalog defaults or giving every GLM alias the same discount. Matching also
  requires the CLI backend when a supplied native GPT name shares that alias.
- At peak, disabled, missing target, infeasible target, excluded family or full
  CLI capacity, preserve existing eligible-family rotation/overflow behavior.
  Price preference cannot bypass capacity or implementation/test independence.
- Update actual selected `last_family` consistently. Explain applied preference
  in selection reason; retain normal rationale when not applied. No worker,
  session/model change, cancellation or deferred launch is triggered by time.

## Delivery

- GLM-5.3 implements `bridge/cost_policy.py`, runtime and MCP integration.
  Kimi independently authors `tests/test_cost_policy.py` and
  `tests/test_cost_policy_runtime.py` with disjoint ownership.
  Parent owns spec/docs/Skill/release metadata. Writers have disjoint files.
- Test peak boundaries, UTC conversion/day rollover, weekends, policy toggles,
  custom windows/timezones/weekdays, strict validation and no mutation on errors,
  persistence/cross-manager visibility, interleaving capacity updates, exact alias
  against duplicate-provider defaults, fit/context/family/capacity precedence,
  ordinary rotation when inactive, no forged MCP clock, and MCP/CLI discovery.
- Install 0.3.1 locally and on Linux validation host, enable this user's exact-alias policy
  with the default schedule without changing capacity, verify genuine installed
  MCP transport/config/selection and one bounded GLM-5.3 connectivity check.
  Keep packaging portable/disabled by default; preserve local commit and ZIP.
