# Development and release checks

[English README](../README.md) | [简体中文 README](../README.zh-CN.md)

Contributor workflow, offline checks and release packaging.

- [Workflow](#workflow)

## Workflow

Use spec-driven development (SDD): describe the intended behavior and acceptance
criteria before implementation. Keep the spec proportional to the change: a
small fix may need a few sentences; a stateful feature needs its failure and
concurrency boundaries recorded.

Assign implementation and tests to different model families. The test author
derives meaningful checks from the spec and may inspect the code for overlooked
edge cases. Coordinate dependencies and keep concurrent writers in disjoint
files; independent authorship does not require simultaneous implementation.

Where useful, borrow TDD's test-first red-to-green loop: observe a test failing
for the intended missing behavior, implement that behavior, then observe it
passing. Implement-then-test is also allowed. Write clear, maintainable code
throughout; make necessary cleanup when it helps correctness or readability,
without requiring a separate refactor phase.

Run offline tests with `python3 -m unittest discover -s tests -v`. Fake CLI
processes should exercise real locking and lifecycle behavior without API costs.
Build a shareable ZIP after checks pass:

```sh
python3 scripts/check_release.py --output dist/mixed-agents-0.7.4.zip
```

Extract the ZIP and run the [installation commands](installation.md) from its
root. Both README languages and the detailed guides are included in the archive.

Run a bounded live smoke after those pass. Record the chosen model IDs and
validation boundaries, rather than assuming all configured aliases work.

Validate packaging with:

```sh
python3 scripts/check_release.py
```

This scans manifests, history and tracked files for credential-like patterns and
release-blockers. It prints categories and locations only, never matched values.

Authorship in this build: Kimi implemented `catalog.py`; GPT implemented
`runtime.py`/`server.py`. GPT authored catalog tests; Kimi authored runtime/MCP
tests. GLM-5.3 implements the off-peak policy; Kimi independently authors its
tests. Earlier releases also received native GPT review of workflow and
concurrency fixes. See [validation](VALIDATION.md) for this release's checks.
The staged configuration module and runtime/MCP integration are GPT-authored;
their independent lifecycle/API tests are Kimi-authored. Stage 2 Provider storage
includes Kimi contributions completed by GPT; the console is GPT-authored. GLM-5.3
independently authored stage 2 resource/API/HTTP/browser acceptance tests. Stage 3
uses GPT dispatch implementation/Kimi acceptance tests and Kimi materialization
implementation/GPT acceptance tests.
