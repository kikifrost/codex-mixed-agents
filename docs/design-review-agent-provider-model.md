# 独立验收 review：Agent / Provider / Model 存储与交互设计

- 验收作者：GLM 家族（独立验收）；父作者 GPT。
- 评审对象：`docs/agent-provider-model-design.md`（设计提案，生产未迁移）。
- 方法：只读静态审阅。对照项目/全局 AGENTS.md、`docs/console-spec.md`、`docs/COST_POLICY_SPEC.md`、`docs/SPEC.md`（关键词扫描）与 `plugins/mixed-agents/bridge/` 的 `catalog.py`、`runtime.py`、`kimi_usage.py`、`zai.py` 核对存储关系与行为承诺。
- 原型状态：fragment 与 preview 包裹已就绪。2026-10-12 用 Playwright（chromium 151）完成交互验收：**46/46 通过**，脚本与输出见可视化目录 `agent-provider-qa.cjs`。结果见 3.1 节。
- 供应商商业规则（Z.ai 半价窗口、Zcode 闲时队列、套餐权益数值）一律按「需供应商证据后启用」处理，本文不将其当作事实。

## 1. 总体结论

设计的核心判断成立：账号层（provider_accounts）、入口层（model_offerings）、运行组合（routes）、独立额度池（quota_pools）四层拆分与现有实现的 fingerprint/slot/observations 语义兼容，family 存 `models` 表且不可由 Agent/Provider 推导，与 `catalog.py` 的 exact-alias + backend/family 分离一致；unknown=NULL 不补 0/100 与 `kimi_usage.py` 的 fail-closed 解析一致；cost_policy 定位为 preference 而非 billing 与「规则/事实/偏好三者分开」一致。

但有 5 个高优先级边界问题会在实现中直接违反文档自己承诺的验收条件（池重复、探测状态无存储、guard 继承、跨 Agent 并发协调点、无读数池的默认绑定）。**2026-10-12 复核：父作者已修订 design doc，上述高/中优先级问题与 cache 字段均已落实（逐项核对见第 4 节）；原型交互验收 46/46 通过（见 3.1 节）。**

## 2. 数据模型与行为边界问题

### 高

**H1. `quota_pools` 缺唯一性约束，同一账号可长出两个五小时池。**
表格（design doc L56）未给 unique 约束。若同一 `(provider_id, account_id, metric, window_kind)` 允许两行（如两次发现流程各建一行），直接违反行为 #2「共享的额度池只展示、扣减一次」和 #3 的共同阻塞语义。
建议：`unique(provider_id, account_id, metric, window_kind)`（`account_id` 为 NULL 的组织池改为 `unique(provider_id, external_pool_ref)`），发现流程走 upsert。

**H2. 探测冷却与账号身份的存储缺失，两套身份概念未定义映射。**
新表清单没有任何探测状态实体；而全局 AGENTS.md 要求「同账号别名和进程重启共用可配置的探测冷却（默认 15 分钟）」。现状（`kimi_usage.py:478-578`）：账号身份 = OAuth 文件路径或 API key 值派生的 fingerprint，`claim_probe` 按 fingerprint 记账（`runtime.py:382-403`）。设计的 `external_subject_ref` 是供应商主体引用，与 credential 派生身份是两个概念：
- `external_subject_ref` 为 NULL（没有供应商主体 API）时不得合并池——设计只说「只对可信同账号身份复用」，未定义 NULL 的 fallback 行为；
- API key 轮换/第二个 key 会产生新 fingerprint → 同一真实账号获得第二份探测额度和第二套池读数（重复消耗共享池、双倍探测）。
建议：新增 `account_probe_state`（或等价实体）随 credential 派生身份持久化冷却；schema 文本写明「主体证据缺失 ⇒ 不合并、UI 标身份未验证」，并把 key 级冷却的残余风险列为已知限制。

**H3. guard 的启用开关与阈值继承未定义。**
现状是两套全局 setter：Kimi `usage_guard`（默认 false）+ `stop_remaining_percent=5`（`kimi_usage.py:137-155, 444-455`），Z.ai 默认 `limit=3` + `stop=10`（`zai.py:31-32, 118-139`）。设计把 `guard_threshold` 放在池行上，但：没有 guard 启用开关列（不能把「无 threshold」既当禁用又当未配置）；迁移后新发现的池从哪里继承全局阈值未定义（否则违反 step 2「新 UI 先调用现有 setters」的单值语义）。
建议：`quota_pools` 增加 `guard_enabled`，并提供 provider 级默认值来源（policy 实体或 providers 行），池行是快照，写明变更传播规则；guard 关闭时 unknown 不阻塞（与现行为一致），开启时 unknown 阻塞。

**H4. 跨 Agent 的账号级并发没有共享协调点。**
设计说「并发继续由现有 slot/claim 事务执行」（L74），但 slot 文件与 `slot_accounts` 都在单一 `self.state` 目录内（`runtime.py:90, 111, 150-171`）。行为 #2「一个 Provider 账号供两个 Agent 使用」「同账号的不同 Agent 不拥有各自额外的账号容量」要求账号级 limit 跨 runtime 原子结算；两个 adapter 各持 state dir 时无处结算。
建议：明确所有 CLI adapter 共用同一 state 目录，或把账号/provider 级 claim 迁入新配置库（单写点），并在 spec 写死该协调点。

**H5. 无读数来源的池（Kimi 周池）默认绑定会全面阻塞。**
`kimi_usage.py:359-361` 明确 weekly/total/token 数字永不替代五小时 remaining；当前 Kimi 只有五小时读数。若迁移时给 Kimi 账号默认绑定周池且 guard 开启，unknown 阻塞规则会让所有 Kimi 组合不可用。
建议：区分「池不可读（adapter 无此能力）」与「读数未知（该读失败）」两种状态；不可读池不自动绑定或默认 guard 关闭，UI 呈现原因而非阻塞。

### 中

**M1. immediate 模式没有运行快照落点。** 行为 #9 要求已运行任务固定组合与配置快照，但 `resolved_snapshot`/`route_revision` 只出现在未来的 `dispatches`（L80）；现有 `agents` 表（`runtime.py:93-102`）只存 model/config/profile/provider，无 revision。immediate 运行也需盖 `route_revision` + 生效阈值快照。

**M2. 迁移切换缺 fail-closed 机制。** L136 只说「旧版本进程在切换前需停止」。建议：cutover 后在 `meta` 表写 schema version（表已存在，`runtime.py:110`），旧进程在写 config.json 前检查到新版本标记即拒绝写入，否则旧 writer 会继续写旧 JSON 造成双主写。

**M3. step-2 阶段 alias prefix ↔ provider_accounts 映射未定义。** console-spec L17-18：现有 per-account limit 以「配置的 alias prefix」标识账号、同 credential 别名共享最严格 limit。新 UI 以 `provider_accounts.id` 为锚，两套标识的映射规则缺失会让「新 UI 调现有 setters」写错对象。

**M4. `concurrency_limits(scope_type, scope_id)` 的枚举与指向未定义。** backend scope 没有实体表（`catalog.py:39-40` 只有常量 `kimi_cli`/`codex_native`）。建议写死：backend→固定串、provider/account/runtime/offering/route→各表 id，配 CHECK；多态引用无 FK 需应用层校验。

**M5. `benefits.account_id` 必填，表达不了供应商级/组织级/托管权益。** 表格（L58）account_id 无 `?`，ER（L39）也是账号持有。若未来出现非账号权益只能造合成账号。建议 account_id 可空 + CHECK「至少一个限定维度非空」，或明文声明权益恒挂账号。

**M6. 池行缺身份锚点校验。** `account_id?` 与 `external_pool_ref?` 同时为 NULL 的行应被 CHECK 拒绝；托管池建议显式 `external_pool_ref='hosted'`，配合「不编造余额」（observations 恒 NULL）。

### 低 / 一致性

- **L1.** ER 图缺 `PROVIDER ||--o{ QUOTA_POOL`（组织池归属），与表格 `account_id?` 不一致（L38 vs L56）。
- **L2.** Cache 的「受限扩展字段」（L68）落点含糊，建议独立表或明确列，否则「范围未知不得声称暖 Cache」难以验收。
- **L3.** `models` 缺 `unique(developer, name, version)`。
- **L4.** 行为 #1 只说「两个 Provider」；同一 Provider 多 endpoint 的同 upstream 模型按 `unique(provider_id, endpoint_ref, upstream_model_id)`（L54）同样是两个入口，建议补一句（Cache namespace 已含 endpoint/region，天然不合并）。
- **L5.** UI 承诺「凭据状态」（L104），但 `provider_accounts` 无 credential 校验状态/时间列；需 adapter 校验事件写入或加列。
- **L6.** 项目 AGENTS.md 要求「Disable Kimi's own delegation tools」，设计中无记录位；`agent_runtimes.discovery_state` 可承载 adapter 上报，建议注明。

### 一致性确认（抽查通过）

- family 独立性存 `models.family`、不可由 Agent/Provider 推导：与 `catalog.py` backend≠family（Kimi 与 GLM 同走 `kimi_cli`）一致。
- unknown=NULL、fail-closed、30s 读数缓存（`kimi_usage.py:763-766`）：与 `quota_observations` 的 status/expires_at 设计兼容。
- 「observations 是历史调用结果、不是额度检查；不得跨 alias 套用一家失败」（`docs/SPEC.md:197-199`）：新类型化 `quota_observations` 分离存放，未冲突；但 design doc L72「沿用 observations」措辞易误读，建议改为「保留现有 alias 级 observations，另增池级 quota_observations」。
- native GPT=宿主托管入口、不伪装 CLI：与 `NATIVE_BACKEND` 一致。
- Z.ai off-peak 偏好（exact alias、selection-only、非 billing 估算）与「权益/事实/偏好三分」不冲突。

## 3. 原型交互验收场景与执行结果

场景定义 S1–S8（前提：静态 fragment、加载零请求、无凭据/token、全部读数标注「示例」）：

| # | 场景 | 步骤与预期 | 溯源 |
| --- | --- | --- | --- |
| S1 | 切换与可达性 | 仅键盘（Tab/方向键/Enter）完成导航四区与组合切换；320px 与 1024px 下无信息丢失、无横向溢出；切换组合后详情面板的可用状态、阻塞原因、行动按钮同步刷新；无任何隐式后台请求 | 行为 #10、#11 |
| S2 | 未知额度 | 某池读数显示「未知」+具体原因（读失败/不可读）+对应动作（读取用量；Kimi 受限探测为单独、带用量提示的按钮，绝不由页面加载或切换自动触发）；未知不得渲染为 0%/100%/满格；guard 启用且未知时组合标阻塞并给出原因 | 行为 #6；L108 |
| S3 | 共享池身份 | 同一账号的两个模型入口行内不重复画额度，仅显示「共用账号额度」链接；详情显示「与另 N 个组合共用」并链接账号页；账号页五小时/周池各一条读数+更新时间；两个 key 疑似同账号时显示「身份未验证」而非合并 | 行为 #2、#3；L100 |
| S4 | 同模型双 Provider | 同名模型在比较区并列两个 Provider 入口，实际版本、能力证据、额度池、Cache namespace 各自列出，互不合并；未知版本别名单独分组不并入已知版本 | 行为 #1；L118 |
| S5 | 未来 adapter | Zcode/Claude Code 组合只出现在明确预览区，support_state 显示「尚未接入/仅配置」并给原因与来源；预览项无「执行」动作、不可进入任何 dispatch 路径；未来 provider_queue 只读预览 | 行为 #5、#10；L84 |
| S6 | 不混淆免费队列 | 队列预览全部标「示例/未来能力」；闲时优惠显示为带证据来源的「偏好/半额积分（示例）」而非「免费」；任何文案不得把「已提交队列」等同「免费执行」；即刻执行需付费时弹出执行方式二次选择，无自动升级 | 行为 #7、#8；L82 |
| S7 | 等待与截止 | 未设截止时间时不出现排队选项；设置截止时间后才可选「允许等到截止」；本地等待与供应商队列在 UI 用不同词条区分并可解释取消/恢复/超时 | 行为 #8 |
| S8 | 支持性四态与阻塞归因 | 已验证/仅配置/未知/不支持四态视觉可辨且各有来源；额度未知、读数过期（超过 freshness/expires_at）、并发已满、凭据失效、尚未接入五类阻塞各有不同文案与对应动作，不混用 | 行为 #5、#6 |

### 3.1 执行结果（2026-10-12，Playwright + chromium 151，file:// preview，iframe locator 作用于 about:srcdoc）

场景 S1–S8 中本原型覆盖的部分由脚本 `agent-provider-qa.cjs`（T1–T6 六组，46 项断言）驱动真实浏览器执行，**46/46 通过**。真实额度读取、任务调度、取消与迁移不在这些断言的覆盖范围内：

- T1（S1）：四视图点击与键盘 Enter 切换均正确联动 `aria-pressed`/`hidden`；320px 无横向溢出（288/288）。
- T2（S3）：账号页「查看共用组合」过滤到该账号 2 条组合并排除其他账号行；详情显示「2 个当前组合共用此账号的池」；列表行无重复额度条/百分数；清除过滤恢复全部已接入组合。
- T3（S2）：勾选「模拟 Kimi 用量未知」后同账号两个 Kimi 组合均转为「用量未知 · 暂停」（blocked），保存按钮均禁用（「先处理阻塞原因」）；池读数显示「未知/暂无可信读数」而非 0%/100%，无进度条；详情理由含「未知读数阻止执行……不能当作未使用」；另一 Provider GLM 组合不受影响（72% 数值+进度条保留）。
- T4（S5/S6/S7）：默认隐藏未来 Agent（4 行）；勾选预览后出现 Zcode/Claude；Zcode 队列组合标「未来接入」、详情要求 adapter 验证、保存禁用（「尚未接入，暂不可选择」）；等待与费用文案 now 模式「队列不满足启动要求」、wait 模式「开始时间未知……不能保证免费」，免费队列不被混淆。
- T5（S4）：GLM-5.3 在模型页列出 Z.ai 与另一 Provider 两个入口，alias（zai-coding-plan/glm-5.3 与 other-provider/glm-5.3）、Cache 范围、池 id（za-5h 与 ar-month）互不合并；未验证账号组合被阻塞（「待验证」，理由含「账号身份与用量未知」），月度池读「未知」无数值。
- T6（卫生）：全流程（含保存）iframe 内零 fetch/XHR/sendBeacon 调用、零非模板网络请求、零未捕获异常；保存反馈「演示选择已保存……未提交任务」。注：宿主 visualization 模板自身从 unpkg 加载 floating-ui/lucide 属预览环境行为，已按来源排除，非原型请求；`remember()` 经宿主 widget-state API（`window.openai.setWidgetState`）持久化 UI 状态，是宿主 API 调用而非网络请求。

执行中发现并修正的测试侧问题：`个人主账号` selector 匹配两行导致 strict violation（改按行序精确断言）；preview shim 将 widget state 持久化到 file:// localStorage，跨组页面会 `restore()` 上一组勾选状态（改每组独立 browser context）；网络断言先误归因宿主模板库、后用「排除模板静态库 + iframe 内 hook fetch/XHR/beacon」双重验证。另有一处原型文案问题由 GPT 父作者修正：即刻启动模式的队列详情也需说明权益待核实、不能保证免费。

### 3.2 未测边界（原型）

- GLM 指出模型页遗漏 K3 的 256k 入口。GPT 父作者已改为从示例数据生成分组，并按账号+精确入口去重，Cache 范围标签解析到具体入口。复用原有 GLM 断言重跑；新入口展示由父作者读取浏览器内容確認，没有新增该分支的独立断言。
- preview widget state 经 localStorage 持久化：真实宿主中「模拟用量未知」等演示勾选可跨会话恢复，属预览 shim 行为，需在移交生产时移除模拟开关。
- 深色模式、reduced-motion、读屏完整遍历未测；仅验证键盘 Enter 激活与 320px 溢出。

## 4. 设计文档修订核对（2026-10-12 复核）

父作者已修订 `docs/agent-provider-model-design.md`，本 review 高/中 findings 逐项核对：

| Finding | 修订落点 | 状态 |
| --- | --- | --- |
| H1 池唯一性 | L57 `unique(provider_id, scope_key, pool_key)` + upsert；L63 CHECK 拒绝双空身份、同名/同窗口不构成池身份、同 Provider 多 endpoint 保留不同入口 | 已落实 |
| H2 探测状态/身份映射 | L65 `account_probe_state` + `account_aliases`（prefix/fingerprint 过渡映射）；换 key 不重置冷却；无主体证据不合并、标「身份未验证」；明示指纹只能识别部分同账号关系 | 已落实 |
| H3 guard 继承 | L81 guard 开关与阈值分离，NULL=继承/false=关闭；meta 存 Provider/套餐/窗口默认策略并迁移 5%/10%；调用快照存生效值 | 已落实 |
| H4 跨 Agent 协调 | L87 所有本机 CLI adapter 共用同一 bridge state root 与 SQLite 事务；scope 枚举与 ID 指向明确；明示单机边界 | 已落实 |
| H5 周池不可读 | L83 `reader_capability`（supported/unsupported/unknown）+ 观测 status 三态；不自动加强制周池；显式保护不可读池仍阻塞并给原因 | 已落实 |
| M1 immediate 快照 | L95 `run_attempts`（route_revision、resolved_snapshot、生效 guard/并发/权益，无凭据；每 turn 快照） | 已落实 |
| M2 cutover fail-closed | L153 先发理解标记的兼容 writer → cutover 后拒绝旧 JSON 写；明示完全不认识标记的历史二进制拦不住，回滚恢复双份快照 | 已落实 |
| M3 alias 映射 | L65 `account_aliases` 显式承载旧 prefix → 新账号 ID | 已落实 |
| L2 cache 字段 | L75 `cache_scope_template`/`cache_scope_verified_at`（入口）+ `resolved_cache_scope_ref`（组合） | 已落实 |

未复核项：修订后全文的一致性重读、新增 `scope_key`/`pool_key` 语义与 ER 图的逐字段比对（快速核对，未见矛盾）。低优先级 L1/L3–L6 未逐项确认是否同步修订（非阻塞）。

## 5. 未验证范围

- 原型未测边界见 3.2（模型页入口粒度、深色模式/读屏遍历等）；本 turn 未扩展新验收。
- `bridge/console.html` 未读；`docs/GLM_SPEC.md`、`KIMI_GUARD_SPEC.md`、`ZAI_GUARD_SPEC.md`、`SHARE_SPEC.md`、`VALIDATION.md` 未读；`docs/SPEC.md` 仅关键词扫描。
- 未运行生产 lifecycle/迁移测试，未启动生产 console。浏览器原型测试已执行；宿主静态资源请求不是模型调用，原型未发送推理请求。
- SQLite 迁移与各 CHECK/事务主张仅静态审阅 schema 文本，未实测。
- 供应商商业规则数值（半价时段、闲时队列资格、套餐权益）未向供应商复核；`COST_POLICY_SPEC.md` 引用的 docs.z.ai 链接本次未访问。

## 6. 实际执行的检查

第一轮（数据模型 review，只读）：

1. 通读 `docs/agent-provider-model-design.md` 全文。
2. 通读 `docs/console-spec.md`、`docs/COST_POLICY_SPEC.md`。
3. 通读 `plugins/mixed-agents/bridge/catalog.py`。
4. 读 `plugins/mixed-agents/bridge/runtime.py` L80–190（SQLite 初始化、config.json 校验），并检索 probe/cooldown/revision/snapshot 相关实现。
5. 读 `plugins/mixed-agents/bridge/kimi_usage.py` L440–580（账号身份、fingerprint、凭据、guard 判定），并检索 parse_usage/weekly/claim 语义。
6. 检索 `plugins/mixed-agents/bridge/zai.py` 的 limit/threshold 默认值与校验规则。
7. 关键词扫描 `docs/SPEC.md` 的存储/额度/并发承诺。
8. 一次性检查原型目录（当时为空）。

第二轮（原型交互验收）：

9. 通读 fragment `agent-provider-console.html` 全文与 preview 包裹的 iframe/srcdoc/shim 结构。
10. 编写并运行 `agent-provider-qa.cjs`（Playwright chromium 151，file:// preview，每组独立 browser context，iframe 内 locator/evaluate），最终完整运行 46/46 通过；测试定位、状态隔离和网络归因已修复；父作者修正的产品文案见第 3.1 节。
11. 快速核对修订版 design doc：H1–H5、M1–M3、L2 九项 findings 的修订落点（见第 4 节表）。
12. 项目内仅更新本文档；QA 脚本仅写入可视化目录。未修改生产代码，未运行推理。

交付前 GPT 父作者修订报告中与实测不一致的摘要、澄清覆盖范围并记录入口粒度修正；验收脚本与断言仍由 GLM 家族编写。
