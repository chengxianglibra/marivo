# Analysis 设计文档

本目录存放 Factum 的内部分析设计文档，覆盖类型化分析意图（typed analysis intents）及相关分析步骤（analysis step）语义。这些文档属于内部设计说明，不属于对外 HTTP API 参考。

## 术语与阅读建议

- [中英文术语对照表](terminology.md) — 本目录统一术语、推荐译法与使用约定
- [Agent 交互分析接口设计准则](agent-interaction-contract-principles.md) — `docs/analysis/` 下关于 agent 交互契约的总纲；说明分析动作面（analysis action surface）、分析状态面（analysis state surface）与消费者投影面（consumer projection surface）应如何分层
- [Agent-First Intent Architecture](agent-first-intent-architecture.md) — 当 Agent 是唯一用户时，原子意图、派生意图与模板层应如何分工
- [证据引擎设计准则](evidence-engine-design.md) — 证据引擎（Evidence Engine）的分层抽象、实体边界与 agent-first 设计原则
- [Evidence Engine Runtime Lifecycle](evidence-engine-runtime-lifecycle.md) — 规范 `artifact -> finding -> proposition -> assessment -> action proposal` 的创建、增量更新、重放、失效与幂等规则
- [Artifact → Finding Extraction Contract](artifact-finding-extraction-contract.md) — 规范哪些 artifact family 必须进入 canonical finding layer、successful extraction 的提交边界，以及 successful empty result 的禁止规则
- [Session Schema](session.md) — 分析容器根对象 `session` 的类型契约草案；定义 session-level typed 非时间约束、治理/生命周期边界，以及进入规范读取面的最小入口
- [Finding Schema](finding.md) — 事实层（Fact Layer）中规范 `finding` 的类型契约草案
- [Proposition Schema](proposition.md) — 判断层（Judgment Layer）中规范 `proposition` 的类型契约草案
- [Assessment Schema](assessment.md) — 判断层（Judgment Layer）中规范 `assessment` 及 `evidence_gap` / `inference_record` 的类型契约草案
- [Evidence Graph Edge Semantics](evidence-graph-edge-semantics.md) — 规范证据对象（canonical evidence objects）之间允许的 edge type、方向、创建 authority 与 lifecycle 语义
- [Inference Rule Engine Contract](inference-rule-engine-contract.md) — 推断规则引擎（inference rule engine）的规范契约；定义规则族（rule family）、固定 evaluation order、升级/降级、冲突处理与 `InferenceRecord` 写入规则
- [Precondition Gate Contract](precondition-gate-contract.md) — `precondition_gate` 规则族的规范契约；定义最低输入前提、gap 映射、condition token 与 record 写法
- [Quality Gate Contract](quality-gate-contract.md) — `quality_gate` 规则族的规范契约；定义质量门槛、`data_quality_risk` gap 映射、condition token 与结构化质量影响写法
- [Comparability Gate Contract](comparability-gate-contract.md) — `comparability_gate` 规则族的规范契约；定义 comparability requirement、`comparability_risk` gap 映射、condition token 与结构化 comparability impact 写法
- [Rule Family Design Checklist](rule-family-design-checklist.md) — 面向设计评审的规则族审查清单；把规则族的命名、输入边界、阶段职责、record / registry / snapshot 约束整理成可检查条目
- [Rule Registry Contract](rule-registry-contract.md) — 规则注册表（rule registry）的规范元数据契约；定义 `rule_id -> rule_family -> assessment_type` 的稳定解引用边界
- [Assessment Judgment Policy](assessment-judgment-policy.md) — 不同 `assessment_type` 的最小判断策略（judgment policy）；定义实质 support / oppose、`mixed` 与 `insufficient` 的判断口径
- [State Schema Index](state-schema.md) — state/context 两个读取面文档的索引页
- [State Surface Schema](state-surface-schema.md) — 分析状态面（analysis state surface）的规范读取契约草案；定义会话状态视图（`SessionStateView`）这一 proposition-centered 默认主读取面
- [Context Surface Schema](context-surface-schema.md) — 上下文面（context surface）的规范读取契约草案；定义命题上下文视图（`PropositionContextView`）这一 proposition-centered 局部最小闭包读取面
- [Action Proposal Schema](action-proposal.md) — 动作支持层（Action-Support Layer）中规范 `action proposal` 的类型契约草案；强调 typed refs、多轴优先级推导与稳定 focus-view 读取
- 阅读代码块和类型定义时，保留英文标识符；正文解释统一使用中文，必要时附英文术语

## 证据引擎文档地图

当前证据引擎（Evidence Engine）的设计文档已经分散到 principles、规范 schema、capability roadmap 和现有 API 几个层面。阅读时建议按下面顺序进入。

### 已存在的设计文档

- [Agent 交互分析接口设计准则](agent-interaction-contract-principles.md) — 统一说明面向 agent 的分析动作面（analysis action surface）、分析状态面（analysis state surface）与消费者投影面（consumer projection surface）应如何分层；这是 `docs/analysis/` 内 intent / evidence / projection 文档共享的交互设计基线，不等价于 `docs/api/` 下的 HTTP 契约
- [证据引擎设计准则](evidence-engine-design.md) — 说明为什么要从 `observation -> claim -> recommendation` 迁移到 `artifact -> finding -> proposition -> assessment -> action proposal`
- [Evidence Engine Runtime Lifecycle](evidence-engine-runtime-lifecycle.md) — 说明规范证据对象（canonical evidence objects）在目标态中的创建、增量更新、replay、soft invalidation 与 idempotency 规则
- [Artifact → Finding Extraction Contract](artifact-finding-extraction-contract.md) — 说明哪些 artifact family 属于 mandatory extraction、artifact 与 finding 的 committed 边界如何绑定、以及为什么 successful empty result 在 v1 非法
- [Session Schema](session.md) — 说明分析容器根对象 `session` 的 typed 非时间约束、治理与生命周期边界、写入协调，以及进入 state surface 的最小入口
- [Finding Schema](finding.md) — 事实层规范 `finding` 契约
- [Proposition Schema](proposition.md) — 判断对象 `proposition` 契约
- [Assessment Schema](assessment.md) — 判断状态 `assessment`，以及 `evidence_gap` / `inference_record` 契约
- [Evidence Graph Edge Semantics](evidence-graph-edge-semantics.md) — 说明规范证据对象（canonical evidence objects）之间允许哪些 relation / edge family，以及这些 edges 的方向、创建 authority 与 runtime 语义
- [Inference Rule Engine Contract](inference-rule-engine-contract.md) — 说明推断规则引擎（inference rule engine）如何围绕单个 proposition 运行、如何组织规则族（rule family）、以及如何把 rule 结果写入 assessment / gap / inference record
- [Precondition Gate Contract](precondition-gate-contract.md) — 说明 `precondition_gate` 如何判断最低输入前提，并稳定映射到 gap、condition token 与 `InferenceRecord`
- [Quality Gate Contract](quality-gate-contract.md) — 说明 `quality_gate` 如何判断质量门槛，并稳定映射到 `data_quality_risk` gap、condition token 与结构化质量影响
- [Comparability Gate Contract](comparability-gate-contract.md) — 说明 `comparability_gate` 如何判断双边可比性，并稳定映射到 `comparability_risk` gap、condition token 与结构化 comparability impact
- [Rule Family Design Checklist](rule-family-design-checklist.md) — 把规则族的通用硬约束整理成评审 checklist，帮助判断某个新增设计应落为 family 还是 cluster
- [Rule Registry Contract](rule-registry-contract.md) — 说明 `InferenceRecord.rule_id` 如何稳定解引用到规则族（`rule_family`）、`assessment_type` 与版本边界
- [Assessment Judgment Policy](assessment-judgment-policy.md) — 说明不同 `assessment_type` 的判断策略（judgment policy）与判断门槛，不把核心状态口径留给实现层临时决定
- [State Schema Index](state-schema.md) — state surface 与 context surface 的导航索引
- [State Surface Schema](state-surface-schema.md) — 以 proposition 为中心的分析状态面规范读取契约
- [Context Surface Schema](context-surface-schema.md) — 以 proposition 为中心的上下文面规范读取契约；v1 中固定通过 `PropositionRef` 拉取单命题局部最小闭包
- [Action Proposal Schema](action-proposal.md) — 动作支持层 `action proposal` 契约；规定 typed refs、priority policy 和稳定截断/闭包读取规则

### 仍缺正式设计文档的主题

以下主题已在现有原则文档、schema 草案或 runtime 设计中被提到，但尚未形成 decision-complete 的正式设计文档。为避免把不同性质的缺口混在一起，暂按“补充到已有文档”“从已有文档拆分”“新增文档”三类整理。

#### 1. 可补充至已有文档的主题

- Assessment snapshot transition details：可继续补入 [`assessment.md`](assessment.md)、[`evidence-engine-runtime-lifecycle.md`](evidence-engine-runtime-lifecycle.md) 与 [`inference-rule-engine-contract.md`](inference-rule-engine-contract.md)，明确 `latest_assessment` 的稳定选择规则、`status` 转换矩阵、以及何时必须形成 superseding snapshot
- Session lifecycle transition details：可继续补入 [`session.md`](session.md)，明确 budget / timeout / 执行失败等事件如何自动推进到 `closed` / `aborted`，以及各 `terminal_reason` 的触发来源

#### 2. 适合从已有文档中拆分为独立 contract 的主题

- Proposition seeding contract：当前规则分散在 [`proposition.md`](proposition.md) 与 [`evidence-engine-runtime-lifecycle.md`](evidence-engine-runtime-lifecycle.md)；后续宜拆为独立文档，统一定义 seed template registry、creation condition、system-seeded proposition 自动注册规则，以及 agent-authored proposition 的 typed family 校验边界
- Gap management contract：当前规则分散在 [`assessment.md`](assessment.md)、[`inference-rule-engine-contract.md`](inference-rule-engine-contract.md)、[`precondition-gate-contract.md`](precondition-gate-contract.md)、[`quality-gate-contract.md`](quality-gate-contract.md) 与 [`comparability-gate-contract.md`](comparability-gate-contract.md)；后续宜拆为独立文档，统一定义 gap open / keep / resolve / reopen、blocking 与 non-blocking membership 收敛、以及 family-level 候选结果如何汇总为 canonical gap state
- Reference integrity contract：当前规则分散在 [`finding.md`](finding.md)、[`proposition.md`](proposition.md)、[`assessment.md`](assessment.md)、[`state-surface-schema.md`](state-surface-schema.md) 与 [`context-surface-schema.md`](context-surface-schema.md)；后续宜拆为独立文档，统一定义 hard refs / soft refs、悬空 ref 的读取语义、写入时的 ref 校验，以及跨 session canonical ref 的禁止边界

#### 3. 需要新增文档的主题

- Session-state HTTP contract：如何把 [`state-surface-schema.md`](state-surface-schema.md) 与 [`context-surface-schema.md`](context-surface-schema.md) 中已定型的 canonical view 绑定到具体 HTTP path、query 参数、分页与兼容策略；该主题属于外部 wire contract，正式文档应写入 `docs/api/`
- Migration / compatibility design：现有 `claim` / `recommendation` 持久化和 API 如何迁移到新的 canonical model

当前 [`evidence-graph-edge-semantics.md`](evidence-graph-edge-semantics.md) 已收敛 v1 的对象内 relation / edge 语义，但仍明确不纳入跨命题推断（cross-proposition inference）。若后续需要引入跨 proposition relation，应在其基础上继续扩展规范模型（canonical model），而不是在 engine contract 中隐式开放跨 proposition 读取。

若后续为上述主题新增正式文档，应优先在本节补充链接，并把对应条目从“缺失主题”移动到“已存在的设计文档”。

## 规范命名基线

为避免上下游文档各自发明平行命名，`docs/analysis/` 统一采用以下基线：

- request type 直接使用各文档中声明的规范名称：`ObserveRequest`、`CompareRequest`、`DecomposeRequest`、`CorrelateRequest`、`DetectRequest`、`TestRequest`、`ForecastRequest`、`AttributeRequest`、`DiagnoseRequest`、`ValidateRequest`
- 原子工件（artifact）的 subtype 由 artifact 本体上的 discriminator 决定，例如 `observation_type`、`comparison_type`、`decomposition_type`、`artifact_type = “anomaly_candidates”`、`result_type = “hypothesis_test”`、`observation_type = “forecast_series”`
- typed ref 必须引用真实存在的规范对象（canonical object）；下游 guard 应写成”`step_type + artifact_id + subtype discriminator`”，不得要求上游产出未定义的 `artifact_type` 字面值
- `scope` 一律复用规范结构化 scope：`constraints + predicate AST`；不得引入字符串 predicate 变体
- truncation、top-k、紧凑视图与派生 bundle 的展示限制只属于投影（projection）/ projection metadata，不属于规范 source identity

## 原子分析意图

- [原子分析意图设计](primitive-intent-design.md) — 原子分析意图家族的跨步骤设计规则
- [`observe` 步骤 Schema](observe.md) — 规划中的 `observe` 原子意图类型契约草案
- [`compare` 步骤 Schema](compare.md) — 规划中的 `compare` 原子意图类型契约草案
- [`decompose` 步骤 Schema](decompose.md) — 规划中的 `decompose` 原子意图类型契约草案
- [`correlate` 步骤 Schema](correlate.md) — 规划中的 `correlate` 原子意图类型契约草案
- [`detect` 步骤 Schema](detect.md) — 规划中的 `detect` 原子意图类型契约草案
- [`test` 步骤 Schema](test.md) — 规划中的 `test` 原子意图类型契约草案
- [`forecast` 步骤 Schema](forecast.md) — 规划中的 `forecast` 原子意图类型契约草案

## 派生分析意图

- [派生分析意图设计](derived-intent-design.md) — 可执行高层意图如何展开为确定性的原子步骤 DAG
- [`attribute` 派生意图 Schema](attribute.md) — 规划中的变化归因派生意图类型契约草案
- [`diagnose` 派生意图 Schema](diagnose.md) — 规划中的异常诊断派生意图类型契约草案
- [`validate` 派生意图 Schema](validate.md) — 规划中的假设验证派生意图类型契约草案

## 补充说明

- 原子步骤命名与 v1 范围以 [`primitive-intent-design.md`](primitive-intent-design.md) 为准；当前目录中不再维护单独的 `naming-rationale.md`
- `docs/analysis/` 负责设计原则、canonical schema 与 typed intent 语义；若需要对外 HTTP wire contract，应写入 `docs/api/`，而不是在本目录把设计准则写成接口参考
