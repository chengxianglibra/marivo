# Proposition Seeding Contract

本文档定义 Factum 规范模型中 `finding -> proposition` 注册路径的独立契约。

状态：draft design。本文是 `docs/analysis/` 下的规范设计文档，不表示对应 HTTP endpoint、持久化结构、调度器或当前实现已经完成。

## 目的

在 canonical evidence chain 已固定为：

`artifact -> finding -> proposition -> assessment -> action proposal`

之后，系统还需要一份独立 contract 明确回答：

- seed template registry 至少要声明哪些稳定字段
- `system_seeded proposition` 何时允许自动注册
- 单 finding 与多 finding 组合模板如何做 deterministic matching
- proposition seeding 的标识归一化（identity normalization）边界是什么
- `agent_authored proposition` 允许落在哪些 typed family，以及如何校验
- `system_seeded` 与 `agent_authored` 在 judgment semantics 重合时如何处理 registry

本文目标是让 [`proposition.md`](proposition.md)、[`evidence-engine-runtime-lifecycle.md`](evidence-engine-runtime-lifecycle.md)、[`finding.md`](finding.md) 与读取面文档共享同一套 seeding 语义，而不是分别定义“何时注册 proposition”“什么算同一命题”“authored 是否可绕过 family schema”的含义。

## 非目标

本文不定义：

- 对外 HTTP wire contract、状态码或错误响应 shape
- seed template 的持久化表、索引、事务或调度实现
- assessment recompute 的规则内容、gap 聚合或 confidence 计算
- 跨 session proposition registry 或跨 session seeding
- 新的 proposition family；v1 authored / seeded 都只使用已定义 family
- 用自由文本 prompt 或模型输出决定 seed template 选择

## Core Rules

### 1. seeding 只消费 committed findings

proposition seeding 的唯一上游输入是 committed findings。

因此：

- extraction pending 的 artifact 不得参与 seeding
- extraction failed 的 artifact 不得参与 seeding
- `mandatory extraction artifact` 的 successful empty result 仍然非法，因此不存在“0 finding 但继续 seeding”
- replay 必须以 committed finding 集为权威输入，而不是以旧 projection 或旧 state surface 拼装输入

### 2. seed template 必须完全 deterministic

seed template 选择、输入匹配、creation condition 判断、seed role 赋值、以及 proposition payload 构造都必须是确定性的。

不允许：

- 使用自由文本总结决定是否注册 proposition
- 使用模型决定 template 选择或 seed role
- 依赖 projection 排序、top-k 截断或 UI 展示顺序
- 依赖当前 `latest_assessment` 结果来决定是否允许创建 proposition

### 3. v1 允许单 finding 与多 finding 组合模板

v1 的 template 输入模型固定为：

- 单 finding 模板：`1` 个 finding 满足条件即可注册
- 组合模板：`2..N` 个 findings 共同满足条件后注册

组合模板必须额外满足：

- 输入 slot 全部是 typed slots，而不是“任意再找一些相关 findings”
- 每个 slot 都要声明允许的 `finding_type` 与 subject 约束
- template 必须声明 canonical slot 顺序，用于 seed role 写入与 tie-break
- 模板未满足完整输入前，不得写入半成品 proposition

### 4. proposition identity 基于 judgment semantics，而不是 seed 批次

`proposition_id` 绑定 session-local judgment semantics。

因此：

- `seed_finding_refs` 的数量、顺序或后续缺失，不单独决定 proposition identity
- 同一 source kind 下，重复命中相同 judgment semantics 时必须复用同一 proposition
- 新 finding 若只为既有 proposition 提供更多候选 seed 组合，不得回写或重建既有 proposition

### 5. 同源去重，跨源不去重

v1 registry 决策固定为：

- `system_seeded` 与 `system_seeded` 之间按 judgment semantics 去重
- `agent_authored` 与 `agent_authored` 之间按 judgment semantics 去重
- `system_seeded` 与 `agent_authored` 即使 judgment semantics 相同，也不共享 proposition identity

因此 registry lookup key 至少包含：

- `session_id`
- `origin.kind`
- `proposition_type`
- 该 family 的 identity normalization 结果

这意味着 authored 与 seeded 可以并存为两个 proposition，并各自进入同类 assessment family。

### 6. authored proposition 不能绕过 typed family schema

`agent_authored proposition` 不经过 seed template registry，但仍受 proposition family 的强校验。

v1 固定只允许以下 family：

- `change`
- `decomposition`
- `anomaly`
- `correlation`
- `test_hypothesis`
- `forecast`

不允许：

- authored 一个未定义 family 的 proposition
- 用 authored 入口跳过 `subject` / payload / ref / lineage 的 family schema 校验
- 把自由文本 hypothesis 直接作为 canonical proposition payload

### 7. creation-time seed refs 只在首次注册时写入

`seed_finding_refs` 是 creation-time provenance，不是可增量维护的实时证据集合。

因此：

- proposition 首次注册成功时写入 seed refs
- 同源重复命中同一 proposition 时，不得为了“更完整种子”改写既有 `seed_finding_refs`
- 后续 live evidence membership 变化只进入 assessment，不回写 proposition

## Seed Template Registry Contract

`system_seeded proposition` 的 registry entry 至少必须声明以下字段：

- `template_id`
- `template_version`
- `proposition_type`
- `assessment_type`
- `input_slots`
- `creation_condition`
- `identity_normalization`
- `seed_output`
- `lineage_output`

### `input_slots`

`input_slots` 定义 template 需要的 finding 输入形状。

每个 slot 至少包含：

- `slot_id`：模板内稳定名称
- `finding_types`：允许命中的 `finding_type` 集合
- `role`：写入 `PropositionSeedRef.role` 时使用的 `primary | secondary | context`
- `cardinality`：v1 固定为 `exactly_one`
- `subject_constraints`：如 metric / entity / analysis_axis / window / slice 的 typed 约束

规则：

- v1 不允许 `one_or_more`、`0..N` 这类开放 cardinality
- 同一 finding 可被不同 templates 消费，也可被同一 template 的不同 registry match 消费
- 一个 slot 若声明 `exactly_one`，则 matching 阶段必须选出单个 canonical finding，不能把多个 findings 打包塞进一个 slot

### `creation_condition`

`creation_condition` 是对已匹配 slot 的 typed predicate。

它必须：

- 只读取 slot 中已绑定的 finding payload、subject、window、typed refs 与显式版本边界
- 以结构化 predicate 表达，而不是 narrative text
- 在相同输入下稳定返回相同结果

它不得：

- 读取 `latest_assessment`、action proposal 或 projection state
- 依赖 session 中其他未被 slot 绑定的“周边 findings”
- 因模板执行时间不同而改变结果

### `identity_normalization`

`identity_normalization` 定义 seeding registry 如何从匹配输入导出 proposition identity。

它必须：

- 只保留 judgment semantics 所需字段
- 排除 `finding_id`、slot 命中顺序、seed 数量、artifact replay 次数等非语义输入
- 产生与目标 proposition family schema 一致的 canonical subject / payload 规范化结果

它不得：

- 把 `seed_finding_refs` 本身当作 identity 输入
- 把 `template_version` 之外的非 breaking extractor 细节纳入 identity
- 把 authored label、projection label 或摘要文本纳入 identity

### `seed_output`

`seed_output` 定义 proposition 首次创建时如何写入 `seed_finding_refs`。

规则：

- 每个 matched slot 生成一个 `PropositionSeedRef`
- 输出顺序必须遵循 template 声明的 canonical slot 顺序
- 同一 slot 选中的 finding 必须稳定映射到同一个 `role`
- 同一 proposition family 若允许 `context` seed，也必须显式声明该 slot，而不是由读取层后补

### `lineage_output`

`lineage_output` 定义 proposition 首次创建时如何写入：

- `origin.kind = "system_seeded"`
- `origin.template_id`
- `origin.template_version`
- `lineage.creation_mode = "seeded"`
- `lineage.source_artifact_lineages`
- `lineage.source_step_refs`

这些字段都必须只从 matched findings 及其 provenance 确定性导出。

## Matching And Registration

### Matching Pipeline

`system_seeded` proposition 的注册流程固定为：

1. 收集新进入 committed state 的 findings
2. 按 registry entry 的 `finding_types` 与 `subject_constraints` 生成 slot 候选
3. 对单个 template 枚举满足全部 required slots 的候选组合
4. 对每个组合执行 `creation_condition`
5. 对通过条件的组合执行 `identity_normalization`
6. 以 `session_id + origin.kind + proposition_type + normalized semantics` 查询 registry
7. 若不存在则创建 proposition；若已存在则 no-op

### Multi-Finding Tie-Break

若同一 template 有多个候选组合在同一 source kind 下归一化到同一 judgment semantics，v1 固定：

- 只创建一个 proposition
- 首次创建时使用 canonical 最小组合写入 `seed_finding_refs`
- canonical 最小组合按 `input_slots` 顺序比较各 slot 绑定的 `finding_id` lexical order 决定
- proposition 一旦创建，后续命中的更优或更多组合都不得改写既有 `seed_finding_refs`

### Failure Semantics

以下情况都不得注册 proposition：

- required slot 缺失
- `creation_condition` 返回 false
- 模板无法稳定导出 target family 的 canonical subject / payload
- 生成的 ref 不是 typed ref、跨 session，或指向 projection
- 归一化结果与 family schema 冲突

这些失败都不应被解释成：

- 创建半成品 proposition，稍后补全
- 先写 proposition，再等待 assessment 否决
- 交给读取层用 warning 或 narrative 自行修正

## Agent-Authored Registration Contract

`agent_authored proposition` 不走 seed template matching，但其注册必须满足以下规则：

- 必须显式选择一个已定义 proposition family
- 必须通过该 family 的完整 schema 校验
- `origin.kind` 固定为 `agent_authored`
- `lineage.creation_mode` 固定为 `authored`
- 若提供 `seed_finding_refs`，每个 ref 都必须是同 session 的合法 `FindingRef`
- 若提供 `authored_input_ref`，只允许使用 [`proposition.md`](proposition.md) 中已声明的 `PropositionAuthoredInputRef`

### Authored Identity

`agent_authored` registry 的 identity normalization 规则固定为：

- 以 proposition family 的 canonical judgment semantics 为准
- `origin.authored_label` 不进入 identity
- `origin.authored_input_ref` 不进入 identity
- `seed_finding_refs` 的数量、顺序或是否为空，不单独改变 identity

因此，同一 session 中重复 authored 同一 hypothesis semantics 时，应复用同一 `agent_authored proposition_id`。

### Authored vs Seeded Collision

若一个 `agent_authored proposition` 与某个 `system_seeded proposition` 在 judgment semantics 上完全相同：

- 二者必须并存为两个 proposition
- 不共享 `proposition_id`
- 各自保留自己的 `origin`、`lineage` 与 `seed_finding_refs`
- 二者进入同一 `assessment_type` family，但 assessment chain 不合并

v1 不提供 authored / seeded 自动对齐、自动 supersede、或自动 provenance 合流。

## Read And Replay Consequences

本 contract 对读取与 replay 的直接后果如下：

- `seed_entries` 只 hydration proposition 首次创建时写入的 `seed_finding_refs`
- finding replay 若不改变 canonical fact boundary，不得导致 proposition registry 漂移
- seed finding 后续不可解引用时，已注册 proposition 仍必须可读取，并通过 `seed_entries.finding = null` 暴露缺失
- 同源重复命中只影响后续 assessment readiness / recompute，不得反向修改 proposition origin 或 seed provenance

## Acceptance Scenarios

后续实现至少应满足以下 contract-level 验收场景：

1. 单 finding 命中 template 后，finding committed 即注册 `system_seeded proposition`。
2. 多 finding template 在 required slots 未齐时不得注册 proposition。
3. 多 finding template 有多个候选组合但归一化后语义相同时，只创建一个 proposition。
4. 同一 `system_seeded` judgment semantics 被重复命中时，复用同一 proposition，不改写既有 `seed_finding_refs`。
5. 同一 `agent_authored` judgment semantics 被重复提交时，复用同一 authored proposition。
6. `system_seeded` 与 `agent_authored` judgment semantics 相同但来源不同，必须生成两个 proposition。
7. `seed_finding_refs` 顺序变化、finding replay 次数变化、或后续 seed 缺失，不单独改变 proposition identity。
8. extraction failed、pending、或 empty result 的 artifact 不得触发 proposition registration。
9. template 若无法稳定导出 target family 的 canonical payload，应判为 seeding failure，而不是注册弱类型 proposition。
10. `agent_authored` proposition 若 family 不在六个内建 family 中，或 family payload 不合法，必须拒绝注册。

## 与其他文档的关系

- [`artifact-finding-extraction-contract.md`](artifact-finding-extraction-contract.md) 负责定义 committed finding 的来源边界；本文只消费 committed findings
- [`finding.md`](finding.md) 负责定义 finding schema、`finding_type` 与 provenance
- [`proposition.md`](proposition.md) 负责定义 proposition schema、origin、lineage 与 `seed_finding_refs`
- [`evidence-engine-runtime-lifecycle.md`](evidence-engine-runtime-lifecycle.md) 负责定义 registration / replay / recompute 的 runtime 时序
- [`assessment.md`](assessment.md) 负责定义 proposition 注册后的 assessment lifecycle；本文不定义 assessment 规则内容
