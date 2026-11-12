# Codex Mixed Agents

**English** | [简体中文](./README.zh-CN.md)

Delegate work to **Kimi, GLM and native GPT subagents** from one Codex workflow.
Mixed Agents is a local plugin that combines Kimi Code CLI workers with Codex's
native collaboration tools, with separate contexts and shared resource controls.

## What it does

- **Choose models by task fit.** Kimi, GLM and GPT are peer candidates; selection
  considers capabilities, context, availability and authorship constraints.
- **Separate implementation and testing.** Define behavior and acceptance criteria
  first, then assign implementation and independent tests to different model families.
- **Keep work resumable.** Start, inspect, message, follow up and interrupt agents
  using stable IDs. Session history and pending messages survive MCP reconnection.
- **Control resources locally.** Configure shared concurrency, account caps,
  optional quota guards and off-peak preferences through a local console or MCP tools.
- **Save project configuration.** Bind projects to templates with explicit
  Provider/model permissions; each CLI turn records its configuration snapshot.

Kimi and GLM run through the Kimi Code CLI. GPT uses native subagent tools exposed
by the Codex host. External CLI sessions do not appear as native Codex subagent
threads. The local configuration console currently has a Chinese UI.

## Quick start

Requires **macOS or Linux**, **Python 3.11+**, a Codex CLI with local plugin support,
and an installed Kimi Code CLI. There are no additional Python runtime dependencies.

Authenticate with Kimi, then clone and install the plugin:

```sh
kimi login
git clone https://github.com/kikifrost/codex-mixed-agents.git
cd codex-mixed-agents
codex plugin marketplace add .
codex plugin add mixed-agents@kai-mixed-agents
codex plugin list --marketplace kai-mixed-agents --json
```

Open a new Codex chat, or restart the app if needed, to load the plugin's Skill
and MCP tools. Run the offline self-check from the repository root:

```sh
python3 plugins/mixed-agents/bridge/server.py call doctor '{}'
```

The check reports local setup issues; it does not run inference or verify quota.
Model availability depends on your configured aliases and account access. See
[installation](docs/installation.md) for login paths, plugin layouts and upgrades.

## Usage

Ask Codex to use Mixed Agents, for example:

> Use mixed agents to implement CSV export for this project. First define the
> expected behavior, edge cases and acceptance criteria. Choose models by task
> fit, and use different model families for implementation and independent tests.
> Report the changes, verification results and any unmet acceptance criteria.

The [Skill](plugins/mixed-agents/skills/mixed-agents/SKILL.md) handles model
selection, capacity and agent lifecycle. You can follow up in the same chat to
inspect progress, send additional instructions or continue a stored session.

To open the local configuration console, run this from the repository root:

```sh
python3 plugins/mixed-agents/bridge/console.py --open
```

The console uses `http://127.0.0.1:8765` by default; add `--port 0` to select a
free port. It stays running until you press Ctrl-C. Configure model permissions,
concurrency and quota policies there. See the [configuration guide](docs/configuration.md)
for defaults and detailed examples.

## Documentation

| Guide | Contents |
| --- | --- |
| [Installation](docs/installation.md) | Requirements, login, plugin manifests, upgrades and uninstall |
| [Configuration](docs/configuration.md) | Models, templates, project bindings, concurrency, quota guards and off-peak settings |
| [Operations and troubleshooting](docs/operations.md) | Runtime behavior, diagnostics, session recovery, logs and local data |
| [Development](docs/development.md) | Contributor workflow, checks and release packaging |
| [Behavior specification](docs/SPEC.md) | Contracts and invariants |
| [Validation record](docs/VALIDATION.md) | Checks performed and remaining validation gaps |

## Contributing

Start with the [development guide](docs/development.md) and [project guidance](AGENTS.md).
Keep specifications proportional to the change and use different model families
for implementation and test authorship. Run the relevant offline checks before
submitting a pull request.

## License and security

[MIT License](LICENSE). See the [changelog](CHANGELOG.md) for release notes and
[security policy](SECURITY.md) for private vulnerability reporting and data handling.
