# 证据引擎设计准则

本文档总结 Factum 证据引擎的目标抽象、分层边界与设计准则。

状态：draft design。本文是面向证据引擎重构讨论的设计原则文档，不表示文中所有结构都已实现。

## 目的

Factum 的证据引擎不应只是把步骤结果串成 `observation -> claim -> recommendation` 的线性链路。

在 `docs/analysis/` 的 typed analysis intent 设计下，证据引擎的职责应当是：

- 接住 typed intent 产出的完整工件
- 以确定性方式抽取事实单元
- 把“事实”“命题”“评估”“动作候选”分层表达
- 为 agent 提供低歧义、可规划、可局部读取的分析状态

本文的目标，是在后续调整证据引擎时，保证其：

- 与 typed intent / artifact / projection 设计保持一致
- 保持证据边界清楚，避免概念漂移
- 优先服务 agent，而不是模仿人类分析报告
- 为未来 intent 扩展保留稳定抽象

## 设计立场

### Factum 的主要用户是 agent

证据引擎的抽象不应首先围绕“如何生成一段好读的结论”，而应围绕“如何给 agent 一个稳定、可决策、可追溯的状态接口”。

因此，证据引擎的设计标准优先级如下：

1. agent 易消费
2. agent 易决策
3. 系统可验证
4. 抽象可扩展

### 证据引擎是分析状态层，不是报告生成层

证据引擎的核心产物应是机器可读的分析状态（machine-readable analysis state），而不是自由文本报告。

它可以为 UI 或 agent 提供投影，但这些投影不得反向定义底层证据语义。

### Recommendation 不是核心证据

Recommendation / action proposal 属于外层动作支持接口，而不是核心证据主体。

证据引擎必须允许 agent 在完全绕过 recommendation 的情况下，仅依赖 facts + assessments 自主规划下一步动作。

## 核心问题

当前常见的线性抽象：

`observation -> claim -> recommendation`

适合简单 demo，但对 agent-first 系统存在以下问题：

### 1. `observation` 过窄

在新的 atomic intents 设计中，`compare`、`decompose`、`detect`、`test`、`forecast` 的核心结果不都适合被视为 observation。

如果把所有 typed step 结果都强行压成 observation，会损失原始分析语义。

### 2. `claim` 混合了多个层级

很多现有 claim 同时承载：

- 命题内容
- 支持 / 反驳证据
- confidence
- inference level
- tentative / confirmed / insufficient 状态

这会混淆“要判断什么”和“当前判断到什么程度”。

### 3. `recommendation` 出现过早

对 agent 来说，最核心的问题通常不是“系统推荐做什么”，而是：

- 已知什么
- 缺什么
- 哪个命题最值得继续验证
- 哪个动作的信息增益最高

因此 recommendation 应是外层投影，而不是底层主语义。

### 4. 线性链路不符合 agent 的工作方式

agent 需要的是：

- 稳定引用
- 显式不确定性
- 显式证据缺口
- 显式下一步可推导空间

这更像状态机，而不是一条“观测到建议”的流水线。

## 总体抽象

证据引擎推荐采用分层推断模型，而不是固定线性链路。

推荐的规范抽象链路为：

`artifact -> finding -> proposition -> assessment -> action proposal`

其中 `inference` 不是孤立的一层展示对象，而是驱动 proposition/assessment 演化的显式规则过程；在需要可审计、可回溯的规范状态时，应持久化为独立的 `inference_record`。

## 抽象层级

### 1. 工件层

Artifact 是 typed intent 执行后的完整工件。

职责：

- 保存完整、可复现、可审计的步骤结果
- 承载下游步骤引用的权威对象
- 为 provenance、调试、重放提供稳定基础

边界：

- artifact 不是 agent 的主决策接口
- artifact 不负责表达命题或动作建议
- artifact 不应为了上下文预算而牺牲语义完整性

### 2. 事实层

事实层推荐使用 `finding` 作为总类，而不是继续让 `observation` 统称一切事实单元。

finding 表示从 artifact 中以确定性方式抽取出的、可被单独引用的事实单元。

`finding` 的具体规范 Schema 见 `docs/analysis/finding.md`。

可能的 finding 子类型包括：

- `observation`
- `delta`
- `decomposition_item`
- `anomaly_candidate`
- `correlation_result`
- `test_result`
- `forecast_point`

职责：

- 表达系统确定知道的东西
- 保留 typed intent 的原生结果差异
- 为后续 proposition / assessment 提供结构化证据输入

边界：

- finding 不应直接承载动作建议
- finding 不应直接声称因果
- finding 必须 deterministic、可重放、可引用

### 3. 判断层

判断层必须把“命题本身”和“对命题的当前评估”分开。

#### Proposition

proposition 表示一个待评估的结构化命题。

`proposition` 的具体规范 Schema 见 `docs/analysis/proposition.md`。

示例：

- 某指标在某 slice 上发生变化
- 某变化主要集中在某维度
- 两个序列存在统计关联
- 某异常候选值得继续验证

职责：

- 提供稳定的推理对象
- 作为 findings 聚合、对抗、验证的中心对象

边界：

- proposition 不包含 confidence
- proposition 不包含 tentative / confirmed / insufficient 等状态
- proposition 表达的是“要判断什么”，不是“已经判断成什么”

#### Assessment

assessment 表示系统当前对 proposition 的评估状态。

典型字段可包括：

- `status`
- `confidence`
- `inference_grade`
- `supporting_finding_ids`
- `opposing_finding_ids`
- `missing_requirements`
- `applied_rule_ids`

职责：

- 向 agent 暴露“当前判断到什么程度”
- 显式表达不确定性、缺口与推进条件
- 随新 findings 到来而更新

边界：

- assessment 是会话内局部、时态性的推断状态
- assessment 不是不可变事实
- assessment 不应退化成只有一个总分的黑盒对象

### 4. 动作支持层

最外层是 action proposal，而不是 recommendation 作为核心证据层节点。

action proposal 表示系统基于当前 assessments 给出的动作候选。

示例类型：

- `investigate`
- `validate`
- `mitigate`
- `monitor`
- `escalate`

职责：

- 为 agent 提供可采纳的动作候选
- 显式说明动作服务于哪个 proposition / assessment
- 暴露风险、成本、预期收益或信息增益

边界：

- action proposal 不是事实
- action proposal 不反向定义 proposition 或 finding
- action proposal 应被视为 projection / policy output，而不是证据本体

## 关键实体边界

证据引擎至少要保持以下三条边界清楚：

### 1. `Finding` 和 `Proposition` 不能混

finding 是事实单元；proposition 是待评估命题。

如果一个对象既在说“观察到了什么”，又在说“因此得出什么结论”，说明抽象已经混层。

### 2. `Proposition` 和 `Assessment` 不能混

proposition 是静态命题；assessment 是系统当前对该命题的动态评估。

如果一个对象同时承载：

- 命题内容
- confidence
- supporting / contradicting evidence
- status

则通常说明 proposition 与 assessment 被错误地塞进了同一个实体。

### 3. `Assessment` 和 `ActionProposal` 不能混

assessment 回答“当前判断到什么程度”；action proposal 回答“下一步做什么可能最有价值”。

两者服务的 agent 任务不同，不应混成一个“带建议的结论对象”。

## Agent-First 设计准则

### 1. 优先暴露可决策状态，而不是可读叙述

对 agent 来说，最重要的不是长文本总结，而是：

- 当前有哪些 active propositions
- 它们各自的 assessment 如何
- 哪些 findings 是核心依据
- 哪些 gaps 阻塞了下一步
- 哪些动作候选最值得执行

因此主接口应优先提供结构化 state，而不是 narrative。

### 2. 显式暴露不确定性和证据缺口

agent 需要知道的不只是“现在相信什么”，还包括：

- 为什么还不能更强地相信
- 缺少哪类证据
- 哪个缺口最阻塞当前任务

这类信息应进入 assessment，而不是只存在于自由文本 explanation 中。

### 3. 保留 typed intent 的原生语义

证据引擎不能为了统一而抹平：

- compare 的 delta 语义
- decompose 的 contribution 语义
- test 的 inferential 语义
- detect 的 candidate 语义
- forecast 的 prediction 语义

统一抽象应建立在共享上位概念之上，而不是牺牲原始类型差异。

### 4. 支持局部最小闭包读取

agent 通常不会一次性消费整个 evidence graph，而是围绕某个 metric、slice、intent 或 proposition 拉取局部上下文。

因此证据引擎应支持围绕某个主题返回最小必要闭包：

- relevant findings
- target proposition
- current assessment
- blocking gaps
- backing artifact refs

### 5. Recommendation 只能是可选 shortcut

对 agent 来说，action proposal 可以提高效率，但不能成为唯一可用的 planning 接口。

系统必须允许 agent：

- 不看 recommendation
- 直接读取 proposition + assessment
- 自主生成下一步计划

### 6. 所有判断都要能回溯到确定性证据和规则

assessment 中的每个关键判断都应能回溯到：

- 哪些 findings 参与了评估
- 哪些规则被触发
- 哪些规则未满足

如果一个判断无法解释其证据来源和规则来源，它就不应成为规范状态的一部分。

## 面向 Agent 的状态读取接口准则

从 agent interaction contract 的角度看，Evidence Engine 主要定义的是分析状态面（analysis state surface），而不是 analysis action surface。

这意味着面向 agent 的主读取接口应优先回答：

- 当前有哪些值得关注的 propositions
- 每个 proposition 的 latest assessment 是什么
- 哪些 findings 构成了当前判断依据
- 哪些 gaps 阻塞了进一步升级
- 哪些 action proposals 只是 shortcut，而不是唯一入口

相应地，状态读取契约应遵守以下边界：

### 1. 主读取面应围绕 proposition-centered canonical state，而不是围绕 artifact 明细

主读取面默认应以：

- `active_propositions`
- `latest_assessments`
- `blocking_gaps`
- 可选的 `recommended_next_actions`

作为主骨架。

原因是：

- proposition 是 agent 的稳定 judgment anchor
- latest assessment 是 agent 的稳定决策入口
- gaps / inference / action proposals 都围绕 proposition 演化
- artifact 与 subject 更适合作为溯源与导航入口，而不是主决策骨架

artifact 是权威溯源入口，但不是 agent 的默认决策入口。

默认读取应围绕：

- `finding`
- `proposition`
- `latest assessment`
- `evidence gaps`
- `inference records`
- `action proposal`

组织，而不是要求 agent 回读每个完整 artifact 后自己拼状态。

subject-centered 视图可以作为辅助读取面存在，用于：

- 以 metric / entity / slice 找到相关 propositions
- 做 focus subject 导航
- 聚合同主题对象

但它不应替代 proposition-centered 主视图。

### 2. 必须支持局部最小闭包，而不是全图遍历

agent 通常只需要围绕某个 proposition 或当前任务焦点读取局部上下文。

因此读取面应优先支持：

- target proposition
- latest assessment
- supporting findings
- opposing findings
- blocking / non-blocking gaps
- applied inference records
- backing artifact refs

这类可组成局部最小闭包的稳定视图。

### 3. projection 是消费视图，不得反向定义 evidence semantics

reflection context、top-k focus view、recommended next actions 等都可以是有效 projection，但它们只能压缩 canonical state，不能：

- 重新定义 proposition / assessment 语义
- 隐藏会影响 agent 决策的阻塞条件
- 用 projection ref 替代 canonical source ref

### 4. recommendation 只能是 planning shortcut

action proposal 可以帮助 agent 更快行动，但系统必须允许 agent 完全绕过 recommendation，仅依赖 findings + propositions + assessments 自主规划下一步。

如果 agent 不读取 action proposal 就无法恢复当前 state 的主语义，说明接口分层已经错误。

### 5. authored proposition 必须进入统一判断轨道

当 agent 显式提出 hypothesis 时，Evidence Engine 应允许其以 `agent_authored proposition` 的形式进入 canonical state。

但 authored proposition 不应绕过 judgment layer 的规范机制：

- authored proposition 进入 proposition registry
- 相关 assessment 仍由 inference / rules 生成
- 相关 gaps 仍由 assessment/inference 打开
- action proposals 仍围绕 latest assessment 生成

这保证了：

- agent 有开放式分析入口
- canonical evidence state 仍保持 deterministic 结构
- system-seeded 与 agent-authored proposition 能被同一读取面消费

关于三层交互面的总纲，见 [`agent-interaction-contract-principles.md`](agent-interaction-contract-principles.md)。

## Evidence State Lifecycle

Evidence Engine 的运行时更新应遵循固定生命周期，而不是把 step completion 与 judgment update 混成单一黑箱过程。

本文只给出生命周期原则与顺序概览；关于创建、增量更新、replay、soft invalidation、幂等与 latest/live 读取绑定的正式规则，见 [`evidence-engine-runtime-lifecycle.md`](evidence-engine-runtime-lifecycle.md)。

推荐顺序如下：

### 1. Artifact Commit

- step 执行完成
- canonical artifact 提交并固定 lineage / version boundary

### 2. Finding Extraction

- 从 artifact 中确定性抽取 findings
- 记录 provenance 与 artifact item refs

### 3. Proposition Registration

- findings 触发 system-seeded proposition template
- agent-authored proposition 注册到同一会话判断空间

### 4. Assessment Evaluation

- inference rules 基于 findings / propositions / history 运行
- 生成新的 assessment snapshot
- 打开 / 关闭 evidence gaps
- 写入 inference records

### 5. State Refresh

- 刷新 proposition-centered 主视图
- 刷新 context closures
- 刷新 action proposals 与 compact projections

### 生命周期要求

- 任何 assessment 都必须建立在已提交 artifact 与已提交 findings 之上
- assessment 只能追加新 snapshot，不得原地重写历史状态
- action proposal 的刷新必须晚于 latest assessment 的确定
- 对外读取应基于单一 `state_version` 或等价边界，避免混合读取半更新状态

## Inference 的角色

inference 不应只是 claim 上的一个附属字段，而应被视为显式的规则过程。

无论 inference 最终是否以独立实体持久化，设计上都应满足：

- inference 依赖显式规则，而不是自由文本解释
- inference 输入是 findings、propositions、已有 assessments
- inference 输出是 assessment 的建立、更新或升级
- inference 必须暴露规则命中与未命中条件

若后续需要更强的可审计性，可以将 inference 持久化为 `inference_record`，记录：

- `rule_id`
- `input_refs`
- `output_assessment_id`
- `result`
- `justification_tokens`

## 工件、状态与投影

证据引擎应至少区分三类输出：

### 1. Canonical artifacts

供复现、审计、下游 typed reference 使用。

### 2. 规范分析状态

供 agent 决策使用，主要由：

- findings
- propositions
- assessments
- gaps
- inference records

组成。

### 3. 消费者投影

面向具体消费者的压缩视图，例如：

- UI 摘要
- recommendation / action proposal 列表
- reflection context
- agent 侧 top-k focus view

projection 不得：

- 发明规范状态中不存在的新事实
- 重定义 proposition 或 assessment 语义
- 用自由文本覆盖结构化状态
- 退化为裸字符串引用、隐式排序或不可审计的 black-box priority

## 规范 Schema 通用准则

证据引擎的所有规范实体（finding, proposition, assessment, action proposal）都必须遵守 Factum 的规范 Schema 设计原则。

详见 [`canonical-schema-principles.md`](canonical-schema-principles.md)。

其中 `action proposal` 虽然属于 projection / policy output，仍必须遵守同一组 canonical 约束：稳定 typed refs、克制的 identity 输入、显式 priority derivation、以及可复用的局部最小闭包读取。

本文档后续章节将专注于证据引擎特定的抽象层级和实体边界。

## 最小推荐实体集

如果只保留最必要、边界最清晰的一套实体，推荐如下：

- `Artifact`
- `Finding`
- `Proposition`
- `Assessment`
- `ActionProposal`

这是比 `Observation -> Claim -> Recommendation` 更适合 Factum 长期演进的最小集合。

其中：

- `Observation` 应下沉为 `Finding` 的一个 subtype
- `Claim` 应拆分为 `Proposition + Assessment`
- `Recommendation` 应外移为 `ActionProposal`

若需要把 assessment 的阻塞条件与规则过程显式暴露给 agent，应在上述最小集合外补充：

- `EvidenceGap`
- `InferenceRecord`

## 对外接口建议

面向 agent 的主接口，不应要求其直接遍历整个 evidence graph。

更推荐提供以 session state 为中心的稳定视图，至少包括：

- `focus_subjects`
- `active_propositions`
- `assessments`
- `blocking_gaps`
- `applied_inference_records`
- 可选的 `recommended_next_actions`
- `backing_findings`
- `artifact_refs`

其中：

- `active_propositions + assessments` 是主视图
- `backing_findings` 是证据详情
- `blocking_gaps + applied_inference_records` 是 assessment 可决策性的关键支撑视图
- `artifact_refs` 是溯源入口
- `recommended_next_actions` 是可选 shortcut；若某个 v1 state schema 尚未定义稳定的 session-level ranking policy，可以暂不纳入主状态默认字段，但其成员、排序和 cross-reference 仍应回到 canonical action proposal contract 解释

## 非目标

证据引擎的设计不应退化为以下方向：

- 面向人类报告的自由文本摘要系统
- 自动根因叙事生成器
- 把 recommendation 当作核心证据节点的 workflow 系统
- 用一个超大 claim 对象承载所有语义层级的混合模型
- 为统一而抹平 typed intent 结果差异的过度抽象

## 经验法则

在设计新实体或新边类型时，可用以下问题做自检：

1. 这是事实，还是判断，还是动作候选？
2. 它会随着新证据变化吗？
3. agent 会读取它，还是操作它，还是只在溯源时查看它？
4. 它是否混合了承诺不同生命周期的字段？
5. 它是否可以不依赖自由文本而被稳定消费？

若无法清晰回答，通常说明抽象边界还不够干净。

在撰写具体 schema 文档时，还应额外检查以下 checklist：

1. 这个对象的 identity 绑定什么？
2. 它绑定哪条谱系？
3. 它是 immutable，还是会随 session 演化？
4. 哪些字段允许 `null`？每个 `null` 各自表示什么？
5. 空对象 / 空数组在该 schema 中表示什么？
6. 哪些字段属于 base？哪些字段属于 subtype？理由是什么？
7. provenance 是否结构化且 machine-readable？
8. agent 如何查询、排序、截断和引用它？
9. source contract 变化时，它通过什么 version boundary 保持可解释？

若一篇 schema 文档无法回答这些问题，则它通常还不算 decision-complete 的 canonical contract。
