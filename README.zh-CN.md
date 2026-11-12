# Codex Mixed Agents

[English](./README.md) | **简体中文**

在同一个 Codex 工作流中，把任务交给 **Kimi、GLM 和原生 GPT 子代理**。
Mixed Agents 是一个本地插件，将 Kimi Code CLI worker 与 Codex 原生协作工具接入同一套流程，
为各个代理保留独立 context，并统一管理资源限制。

## 主要功能

- **按任务选择模型。** Kimi、GLM 和 GPT 是平等的候选，选择时综合考虑能力、context、
  可用性以及实现与测试的分工约束。
- **分开实现与测试。** 先定义行为和验收条件，再让不同模型系列分别负责实现和独立测试。
- **保留可继续的工作。** 通过稳定的代理 ID 启动、查看、发消息、继续或中断任务。
  MCP 重连后仍保留会话历史和待处理消息。
- **本地管理资源。** 在配置控制台或 MCP 工具中设置共享并发、账号上限、可选的额度保护和错峰偏好。
- **保存项目配置。** 为项目绑定模板，明确允许使用的 Provider 和模型；每轮 CLI 执行都会记录配置快照。

Kimi 和 GLM 通过 Kimi Code CLI 运行；GPT 使用 Codex 客户端提供的原生子代理工具。
外部 CLI 会话不会显示为 Codex 原生子代理线程。目前本地配置控制台为中文界面。

## 快速开始

需要 **macOS 或 Linux**、**Python 3.11+**、支持本地插件的 Codex CLI，以及已安装的 Kimi Code CLI。
无需额外的 Python 运行时依赖。

先登录 Kimi，再克隆仓库并安装插件：

```sh
kimi login
git clone https://github.com/kikifrost/codex-mixed-agents.git
cd codex-mixed-agents
codex plugin marketplace add .
codex plugin add mixed-agents@kai-mixed-agents
codex plugin list --marketplace kai-mixed-agents --json
```

新建一个 Codex 聊天，让插件的 Skill 和 MCP 工具加载；必要时重启应用。
在仓库根目录运行离线自检：

```sh
python3 plugins/mixed-agents/bridge/server.py call doctor '{}'
```

自检会报告本地配置问题，不会调用模型推理或验证账号额度。
模型是否可用取决于已配置的别名和账号权限。
登录路径、插件目录结构和升级方法见[安装指南](docs/installation.md)。

## 使用方式

在 Codex 中直接要求使用 Mixed Agents，例如：

> 用 mixed agents 给这个项目实现 CSV 导出。先明确预期行为、边界情况和验收条件，
> 再按任务适配程度选模型，让不同模型系列分别负责实现和独立测试。
> 最后说明改动、验证结果，以及尚未满足的验收条件。

[Skill](plugins/mixed-agents/skills/mixed-agents/SKILL.md) 会处理模型选择、并发容量和代理生命周期。
你可以在同一个聊天中继续查看进度、补充要求，或继续已有会话。

在仓库根目录运行以下命令，打开本地配置控制台：

```sh
python3 plugins/mixed-agents/bridge/console.py --open
```

控制台默认使用 `http://127.0.0.1:8765`，加上 `--port 0` 可以自动选择空闲端口。
命令运行期间服务保持开启，按 Ctrl-C 关闭。可以在页面中设置模型权限、并发和额度策略。
默认值与详细示例见[配置指南](docs/configuration.md)。

## 文档

以下详细指南和行为规范目前以英文维护。

| 文档 | 内容 |
| --- | --- |
| [安装指南](docs/installation.md) | 环境要求、登录、插件清单、升级与卸载 |
| [配置指南](docs/configuration.md) | 模型、模板、项目绑定、并发、额度保护与错峰设置 |
| [运维与故障排查](docs/operations.md) | 运行行为、自检、会话恢复、日志与本地数据 |
| [开发指南](docs/development.md) | 贡献流程、检查与发布打包 |
| [行为规范](docs/SPEC.md) | 行为约定与不变条件 |
| [验证记录](docs/VALIDATION.md) | 已执行的检查及尚未验证的范围 |

## 参与贡献

请先阅读[开发指南](docs/development.md)和[项目约定](AGENTS.md)。
根据改动规模编写 spec，让不同模型系列分别编写实现和测试，提交 PR 前运行相关离线检查。

## 许可与安全

采用 [MIT License](LICENSE)。版本变化见[更新日志](CHANGELOG.md)，
漏洞私密报告方式与数据处理说明见[安全政策](SECURITY.md)。
