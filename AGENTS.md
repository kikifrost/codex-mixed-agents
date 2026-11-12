# Project guidance

This project provides a local Codex plugin for delegating work to Kimi, GLM and
native GPT subagents through the same workflow. Do not claim that external CLI
sessions appear as native Codex subagent threads.

- Use SDD as the primary workflow: define behavior and acceptance criteria first,
  proportional to the change. Implementation and tests have different model-family
  authors. Optional test-first red-to-green checks observe a meaningful failure,
  then a passing implementation. Keep code clear throughout; necessary cleanup is
  allowed without requiring a separate refactor phase. Test-after is also allowed.
- Select Kimi, GLM and GPT on task fit; never silently prefer GPT.
- Kimi CLI concurrency (including GLM workers) is shared and configurable:
  default 5, a nonnegative integer (0 means unlimited), or unlimited. Respect the effective configuration; finite overflow independent
  tasks use native GPT capacity. Lowering the limit does not cancel existing jobs.
- Z.ai GLM has a separate provider cap of 3; overflow uses eligible Kimi/GPT.
  With an enabled real-usage guard, stop at remaining 10%; unknown usage blocks
  Z.ai. Provider delays and in-flight spending prevent a hard reserve guarantee.
- Kimi Code has a separate observed five-hour reserve of 5%. Resolve usage for
  the exact account. Unknown idle admission may use one bounded tool-free probe
  per account per configurable cooldown (default 15 minutes), then require readable usage above 5%.
  Never repeat probes on exhausted/unknown accounts, or bootstrap active turns.
- Prefer kimi-code then kimi-for-coding within eligible Kimi profiles. Account
  caps and cooldown are configured through the local console or MCP setters;
  never bypass shared capacity through a backup account.
- Implementation and test authors for the same work unit must use different
  model families. All Kimi variants count as one family, all GLM variants as one,
  and all GPT variants as one. The CLI backend does not determine the family.
- Parallel writers own disjoint files or isolated worktrees. Follow repository
  instructions in the target workspace.
- Disable Kimi's own delegation tools so nested agents cannot evade the cap.
- Keep credentials in the existing Kimi installation; do not copy or log them.
- Prefer Python 3.11+ standard library. Run offline lifecycle/concurrency tests
  and a small live integration check. Record unverified model access separately.

## Optional shared guidance

If a personal or team `AGENTS.md` exists in the current environment, you may
read it for additional local conventions. This project never requires a specific
absolute personal path or replaces another user's global preferences.

## Implementation plan

1. Define the behavioral spec and CLI model profiles.
2. Select implementation and independent test authors among configured Kimi/GLM
   and native GPT by task fit, explicit requests and actual access. Keep different
   families for each work unit. Derive tests from the spec; coordinate dependent
   work rather than requiring simultaneous writes. Optional red-to-green checks
   use meaningful failures and maintainable implementation.
3. Validate model routing, cross-process capacity, follow-up, cancellation,
   restart recovery and MCP discovery.
4. Install the local plugin and verify Codex reads it. Preserve a local commit.
