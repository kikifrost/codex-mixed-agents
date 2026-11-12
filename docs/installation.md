# Installation

[English README](../README.md) | [简体中文 README](../README.zh-CN.md)

Requirements, plugin layouts, login, upgrades and removal. Run shell commands from the repository root unless stated otherwise.

- [Requirements and installation](#requirements-and-installation)
- [Login and Kimi configuration](#login-and-kimi-configuration)
- [Upgrades, uninstall and fresh environments](#upgrades-uninstall-and-fresh-environments)

## Requirements and installation

macOS/Linux, Python 3.11+, Codex CLI with local plugins, and an authenticated Kimi
Code CLI. No additional Python runtime dependencies. Keep credentials in the
existing Kimi installation.

Clone this repository and install from its root:

```sh
git clone https://github.com/kikifrost/codex-mixed-agents.git
cd codex-mixed-agents
codex plugin marketplace add .
codex plugin add mixed-agents@kai-mixed-agents
codex plugin list --marketplace kai-mixed-agents --json
```

This checkout includes two separate manifest formats:

- **Portable root manifests** (`plugin.json` + `mcp.json`) — canonical package
  layout. The root `plugin.json` carries the `extensions.com.openai.interface`
  only; the root `mcp.json` independently supplies the stdio MCP server with the
  minimal fields the portable loader accepts: `type`, `command`, `args`, `cwd`.
  It launches `./bridge/launch.py` using `python3` from PATH with `cwd: "./"`.
- **Native Codex overlay** (`.codex-plugin/plugin.json` + `.mcp.json`) —
  compatibility layer for the installed Codex CLI version used here. It can keep
  the native timeout keys and `cwd: "."` as that loader permits.

See the [OpenAI plugin docs](https://developers.openai.com/plugins/build/plugins)
for the current plugin schema. On a host where `python3` is not Python 3.11+,
install a supported Python and either make it the default `python3` or edit the
MCP manifests to the full path before installation.

Open a new Codex chat (or restart the app when needed) to load the plugin's Skill
and MCP tools. Example user request:

> Use mixed agents to implement [feature]. First state the intended behavior,
> important edge cases and acceptance criteria. Choose models by task fit;
> assign implementation and independent tests to different model families.
> Coordinate their work around the spec, then report the changes, verification
> results and any acceptance criteria that remain unmet.

This example asks for a concrete outcome. The Skill supplies lifecycle, capacity
and authorship rules; the user does not need to repeat those mechanics in every
request. Test-first work is optional; see the [development guide](development.md).

See [the Skill](../plugins/mixed-agents/skills/mixed-agents/SKILL.md) for model
selection and native-action handling. [SPEC.md](SPEC.md) records exact
semantics and invariant precedence. [VALIDATION.md](VALIDATION.md) records
checks actually run and limits still unverified.

## Login and Kimi configuration

The plugin does not manage Kimi credentials. Install and log in with the Kimi
Code CLI first:

```sh
kimi login
```

Current Kimi Code 2.1.1 reads `$KIMI_CODE_HOME/config.toml` and has no
`--config-file` option. The bridge resolves the config directory from
`$KIMI_CODE_HOME`, then `~/.kimi-code`, and sets a child-local `KIMI_CODE_HOME`
to that directory for each Kimi launch. It never copies credentials or mutates
the supervisor environment.

## Upgrades, uninstall and fresh environments

To upgrade after source changes, re-run:

```sh
codex plugin add mixed-agents@kai-mixed-agents
```

Then open a new Codex chat or restart the app so the updated Skill and MCP
tools are loaded. Existing chats may continue to use the old tool schema.

To uninstall:

```sh
codex plugin remove mixed-agents
```

The bridge does not remove its state directory automatically; delete
`~/.local/state/codex-mixed-agents` manually if you no longer need logs or
history. Uninstalling does not affect the Kimi Code CLI installation or its
credentials.

In a fresh environment, install Kimi Code CLI, authenticate, verify `python3` is
3.11+, install the plugin, open a new chat, and run `doctor()` before the first
delegation. Validation records in [VALIDATION.md](VALIDATION.md) show which
model aliases and platform combinations were actually exercised locally; do not
assume all aliases or all OS versions behave identically.
