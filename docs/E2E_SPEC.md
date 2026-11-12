# End-to-end acceptance

Validate the current checkout through its public browser and process interfaces.
Use temporary configuration and state for mutation scenarios; keep existing user
settings and credentials intact. No new unit tests are needed.

## Browser journey

Run the existing browser suite against the real loopback console and SQLite
backend. Cover template creation and revision conflicts, draft persistence,
Project registration/binding, Provider/model creation and disabling, independent
policy saves, concurrency inputs and 320-pixel layouts. A failed submission must
show its error within the current viewport while preserving the draft.

## MCP process journey

Use the real server entry point over JSON-RPC stdio, an isolated synthetic Kimi
installation, a PATH shim for the existing fake CLI, and temporary SQLite state.
Do not replace AgentManager, dispatch, worker processes or persistence with mocks.

- Initialize and discover tools through the subprocess transport.
- Create a managed Provider and model, authorize the model in a saved template,
  and register/bind a temporary Project through public tools.
- Offline doctor recognizes the managed model. Dispatch runs the fake CLI and
  returns its result with a captured session ID.
- Follow-up preserves the session and returns the new result.
- Restart the MCP process with the same state, retrieve the completed task and
  continue it without creating a replacement task.
- Start a slow fake task, interrupt it, and observe a terminal state and released
  capacity. Invalid requests return structured errors without killing the server.

## Real provider boundary

Use one bounded task through the current source bridge with the existing shared
registry, account caps and quota guards. Select an eligible configured model
first, perform only the selection's permitted recovery action if needed, and
record completion or the actual access failure separately from offline E2E.
No change to quota policies or credentials is part of this acceptance run.

## Local acceptance results

Verified against the application fixes in `1ae84dd`:

- Browser: **28 passed, 0 failed, 0 skipped**, including error visibility and
  retained form values after invalid model submission. Desktop and 320-pixel
  layouts were checked. The initial new assertion incorrectly required all
  unrelated drafts to disappear; it now checks only the target form.
- MCP stdio: **one complete sequential journey passed**. Initialization,
  tool discovery, managed configuration, Project permissions, doctor, dispatch,
  two follow-ups around a server restart, cancellation, released capacity and
  structured error handling all use actual subprocesses and temporary state.
- Existing managed-diagnostic and usage-recovery integration scenarios:
  **9 passed**. The usage scenarios replace only the provider HTTP boundary;
  they are recorded separately from real-provider checks.
- Regression sensitivity: the same new tests were copied into a temporary
  archive of `3fc347f` (before the fixes). MCP acceptance failed on managed-model
  doctor readiness. Browser acceptance had 27 passes and one expected failure:
  the error message was at y=-2624 outside the 800-pixel viewport. Current code
  passes both checks. No Git refs were changed by this comparison.
- A real Kimi recovery probe through the current source bridge recovered an
  expired local OAuth token and returned readable usage above the reserve.
  A bounded Kimi task through the same bridge completed; a short real follow-up
  returned the requested marker on turn 2 with the same captured session ID.
  GLM and native GPT live execution were not revalidated in this run.

The independent test authors are Kimi; the application fixes are GPT-authored.
Local logs, browser screenshots and the full browser replay are retained under
the ignored `.validation/` directory. No new repository was published.

Reproduce the process journey with:

```sh
python3 -m unittest tests.test_e2e_process -v
```

The browser suite requires Node.js, resolvable Playwright and its Chromium
installation; run `node tests/test_template_console_browser.js`. Its header
also documents the bundled local runtime command. These checks launch their own
temporary services and synthetic state. The browser suite remains a separate
local check; the Python journey is included in normal unittest discovery.
