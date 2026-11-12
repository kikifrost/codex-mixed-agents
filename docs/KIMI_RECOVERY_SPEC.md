# Kimi unknown-usage recovery before model exclusion

## Problem

The read-only usage reader reports `credential_expired` for an expired local
OAuth access token. The CLI can refresh it during the existing bounded probe.
Selection currently removes unknown-usage Kimi aliases before dispatch, so the
automatic idle-admission probe is unreachable when callers trust selection.
An aggregate catalog reading can also hide a healthy second account.

## Behavior and acceptance

- Keep catalog, selection and doctor free of inference and credential writes.
  Do not weaken the 5% reserve, account/shared/provider/Project caps, saved
  permissions, task fit or implementation/test family independence.
- Catalog and selection expose structured, actionable recovery guidance for
  exact Kimi aliases with unknown usage and enabled automatic bootstrap. The
  caller can invoke the existing `probe_kimi_usage` tool, then rerun selection.
  Expired local access tokens mean unknown usage pending CLI refresh, not proof
  of invalid login, expired subscription or permanently unusable Kimi.
- Read accounts individually; do not apply aggregate or historical errors to
  every Kimi account. Deduplicate recovery actions for aliases sharing an
  account. Never expose credentials, fingerprints, private paths or raw errors.
- Selection recovery guidance is limited to otherwise eligible Kimi choices:
  obey saved permissions, role/family/avoid-family, context feasibility and
  shared/account/Project capacity. Never suggest bypassing a hard constraint.
  Catalog actions are global diagnostics; the dispatch workflow first calls
  selection with the actual task context and executes only its scoped actions.
  The explicit probe accepts imported CLI aliases: do not substitute a
  template-denied source alias for a permitted managed alias.
  Keep unknown accounts out of runnable candidates until usage is verified.
  If no runnable candidate remains, include recovery guidance in the error.
- Recovery actions identify the exact public tool and arguments; no fabricated
  CLI sessions, no arbitrary prompt. The existing probe remains the authority
  for durable per-account cooldown and atomic capacity admission. Guidance does
  not reserve or consume a probe. Known reserve/exhausted accounts have no
  recovery action; disabled bootstrap has no automatic-recovery instruction.
- Put mandatory Kimi recovery preflight in the numbered skill dispatch flow,
  not only in the later quota-policy reference. Before excluding unknown Kimi
  or accepting a fallback for that reason, run at most one bounded probe per
  affected account, reread/rerank, and honor cooldown/unknown-after-probe stop.
  Keep family rotation among healthy equal-fit candidates.
- Update tool descriptions to explain the returned action and the meaning of
  `credential_expired`. Reinstall the plugin for subsequent chats after checks.

## Verification and authorship

Kimi authors production changes and workflow documentation; GPT independently
authors offline regression tests from this spec. Verify expired-token recovery
metadata, exact-account isolation/deduplication, reserve and policy boundaries,
family/context/capacity/template constraints, no inference during reads, and
no-candidate errors. Run existing offline lifecycle/concurrency checks and a
small live exact-alias probe/worker, without exhausting accounts. Preserve all
pre-existing workspace changes.
