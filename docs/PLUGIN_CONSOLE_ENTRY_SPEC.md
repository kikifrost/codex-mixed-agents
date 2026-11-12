# Plugin detail console entry — 2026-10-14

## Behavior

Mixed Agents contributes a native **打开配置控制台** action to the plugin detail
page through the documented `openai/settings` MCP extension. The read tool accepts
`{}`, returns an empty primitive schema/values and a tool-action group, and does
not start a server, open a browser, request usage/auth, run inference or save policy.
The mandatory update tool accepts only an empty `set` object; unknown fields fail
without mutation. Configuration continues to use the existing console.

Clicking the action starts the existing loopback-only console on a free port,
using the MCP process's exact manager/config/state. It asks the OS to open that
URL in the default browser. Repeat/concurrent clicks reuse one live server per
manager in that process. The URL is allocated at click time, never written into
the plugin manifest. Browser failure returns a usable local URL and an explicit
manual-open message rather than claiming success. Server startup errors are safe.
MCP shutdown stops and closes the console; the next process gets a fresh service.
This is a desktop local entry, not a hosted ChatGPT web console. Direct recovery
CLI calls must not return a URL to an immediately exiting server: use the existing
`console.py --open` command instead.

## Acceptance

- Initialization advertises read/update tools via `openai/settings`; support
  `2025-11-25` while preserving existing older-version negotiation.
- All three tools have strict schemas; the action has a Chinese tool title.
  The read tool declares the required output schema and returns structured content.
- Opening settings is read-only, including no HTTP service/browser/auth/inference.
- Explicit action opens a working console with the existing Host/Origin/token/CSP
  protections and no expanded console method allowlist. Policy bytes are unchanged.
- Repeated/concurrent launch, shutdown/reopen, browser failure/exception and
  startup failure are covered with temporary state and fake browser calls.
- Installed stdio discovery/read/launch plus an actual browser open are checked.
  Codex itself is blocked from Computer Use; the native detail-page click remains
  a stated manual verification boundary, not a claimed completed UI check.
- Refresh the installed package, run release checks, commit and push.

## Source and authorship

- [OpenAI Plugin Extensions](https://developers.openai.com/plugins/build/extensions)
  links the OpenAI MCP Extensions structured-settings protocol: native tool actions
  are allowed in the plugin details page and accept `{}`.
- Parent GPT implements `bridge/plugin_settings.py` and transport integration.
  An independent eligible Kimi/GLM author owns the new acceptance tests and any
  necessary old server negotiation expectation changes. Existing production files
  and test files have separate writers. No new dependencies or console redesign.
