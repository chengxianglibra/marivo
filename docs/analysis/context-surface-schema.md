# Context Surface Schema

本文档定义 Factum 在 agent-first 读取路径中的上下文面（`context surface`）拟议类型契约。

状态：draft design。本文是 `docs/analysis/` 下 上下文面 的 canonical schema 提案，不表示对应 HTTP endpoint 或持久化模型已经实现。

## 目的

上下文面 的职责，是围绕单个 proposition 交付稳定、可局部读取、可解释、可决策的最小 canonical 闭包，而不是让 agent 回读完整 session state、artifact 列表或 compact summary 自己拼装判断上下文。

设计目标：

- 将 proposition 级读取固定为局部最小闭包，而不是可任意裁剪的自由 projection
- 复用既有 canonical objects，而不是发明平行 context object
- 让 agent 能直接解释 最新评估 的 support / oppose / gap / inference 依据
- 保持 上下文面 与 authored proposition / seeded proposition 共用同一读取轨道

## 核心设计决策

### 1. 上下文面 是 命题中心 的局部最小闭包读取面

规范证据链 仍保持：

`artifact -> finding -> proposition -> assessment -> action proposal`

上下文面 不新增新的核心证据实体；它定义的是如何把已有 canonical objects 组织成围绕单个 proposition 的默认闭包。

因此：

- `PropositionContextView` 是面向 consumer 的规范视图
- view 中出现的对象身份仍来自 `finding / proposition / assessment / evidence_gap / inference_record`
- view 不得发明新事实、新判断或新 typed ref

### 2. v1 的 canonical target 仅由 `PropositionRef` 唯一确定

v1 的 context query 固定为：

- `PropositionContextQuery = { proposition_ref }`

不在 v1 加入：

- `profile`
- `mode`
- `include_findings`
- `include_gaps`
- `include_inference_records`

原因：

- 上下文面 的主问题是“围绕哪个 proposition 拉局部闭包”，不是“怎样裁剪一个半结构化 payload”
- 若在 canonical query shape 中加入 projection knobs，会把 consumer view 裁剪错误地提升为 schema 语义
- compact / audit / token-budget 读取如果未来需要，应通过 投影面 扩展

### 3. authored proposition 与 system-seeded proposition 共用同一 上下文面

`agent_authored proposition` 与 `system_seeded proposition` 共享同一 `PropositionContextView`。

要求：

- 不得拆成平行的 hypothesis context object
- 不得为 authored proposition 引入弱化版 assessment 旁路
- proposition 即使缺 seed、缺 assessment 或 gap 未闭合，也仍应可读取

## Schema Position

状态读取链路：

`canonical objects -> context view -> consumer projection`

其中：

- `finding / proposition / assessment / evidence_gap / inference_record` 负责 规范语义
- `PropositionContextView` 负责 proposition 级主读取组织
- compact context、audit context、token-budget 压缩属于 projection 扩展，不改写 view identity

## Typed Schema

```ts
type PropositionContextView = {
  proposition: Proposition;
  seed_entries: PropositionSeedEntry[];
  relevant_findings: Finding[];
  latest_assessment: Assessment | null;
  blocking_gaps: EvidenceGap[] | null;
  non_blocking_gaps: EvidenceGap[] | null;
  applied_inference_records: InferenceRecord[] | null;
  assessment_dependencies: Assessment[] | null;
  artifact_refs: StateArtifactRef[];
  schema_version: string;
};

type PropositionContextQuery = {
  proposition_ref: PropositionRef;
};

type PropositionRef = {
  session_id: string;
  proposition_id: string;
};

type PropositionSeedEntry = {
  seed_ref: PropositionSeedRef;
  finding: Finding | null;
};

type StateArtifactRef = {
  artifact_id: string;
  step_ref: StepRef;
};
```

## 字段语义

### PropositionContextView

`PropositionContextView` 是 上下文面 的 canonical 默认读取形状。

它回答的是：

- 围绕某个 proposition 当前正在判断什么
- 当前 最新评估 为什么成立、为什么未成立或为什么卡住
- 为了解释和继续决策，最少需要读取哪些 canonical objects

它不是：

- compact reflection summary
- session 级 subject 导航视图
- 历史 assessment 浏览器
- 带 `include_*` knobs 的可编程 projection 容器

#### proposition

`proposition` 是该局部闭包的唯一 target proposition。

要求：

- 它必须与 `PropositionContextQuery.proposition_ref` 一一对应
- authored proposition 与 system-seeded proposition 共享同一读取面，不得拆成平行的 hypothesis context object
- context view 不得因为 seed 缺失、assessment 缺失或 gap 未闭合而静默隐藏 proposition

#### 局部最小闭包边界

`PropositionContextView` 返回的是围绕单个 proposition 的局部最小闭包（minimal 局部闭包）。

它至少要让 agent 能够：

- 解释 proposition 的判断对象与 creation-time seed
- 解释 最新评估 当前为何是该状态
- 审计 support / oppose / gap / inference 的直接 canonical 依据
- 决定是否值得继续验证该 proposition

它不得混入：

- 与该 proposition 无关的 session findings
- 历史 assessment snapshots 的成员集合
- readiness、reflection、recommendation text 等 compact summary
- 仅为展示拼接出的 narrative fragments

#### seed_entries 与 relevant_findings

二者必须严格区分：

- `seed_entries`：按 `proposition.seed_finding_refs` 的 canonical 顺序返回的 creation-time seed hydration
- `relevant_findings`：来自 `latest_assessment` 及其 inference records 的 实时证据 set

两者可重叠，但不应因为重叠而合并为单一字段。

补充要求：

- `seed_entries` 的每个成员都必须保留原始 `seed_ref`，不得在 hydration 时丢失 `primary / secondary / context` 角色语义
- `seed_entries.finding = null` 表示该 seed ref 当前不可解引用；这不等价于 proposition 无效
- `seed_entries` 是 creation-time seed hydration 的唯一权威载荷；consumer 不应再从平行缺失字段反推 seed role
- `relevant_findings` 必须足以覆盖 `latest_assessment.supporting_finding_ids`、`latest_assessment.opposing_finding_ids` 以及当前 `applied_inference_records` 的直接 finding 输入
- `relevant_findings` 不得因为某条 finding 同时属于 seed 而被省略
- 若 `latest_assessment = null`，`relevant_findings` 只能为 `[]`，不得伪造 assessment-derived finding membership
- `seed_entries` 必须遵循 `proposition.seed_finding_refs` 的 canonical 顺序；同一 seed ref 的 hydration 不得重排
- `relevant_findings` 的默认排序应复用 `finding.md` 中的 canonical 稳定排序

#### seed 缺失语义

`seed_entries.finding = null` 用于显式暴露失效或不可解引用的 seed refs。

规则：

- proposition 仍可读取
- 不得因为部分 seed 缺失而静默隐藏 proposition
- 不得把缺失 seed 折叠成 narrative warning

该字段只表示“当前读取时无法解引用”，不直接断言永久损坏。

可能原因包括：

- 种子事实单元 所在 artifact 或谱系当前不可读
- 历史 artifact 已被移除
- 读取路径存在暂时性不一致

agent 应将其解释为 seed provenance 不完整信号，而不是 proposition 自动失效。

是否重试、何时认定永久失效，属于后续 lifecycle / runtime 设计，不在本文定义。

缺失 seed 的默认展示顺序必须与 `proposition.seed_finding_refs` 的原始 canonical 顺序兼容，不得因读取层重排而破坏 creation-time seed 角色语义。

#### latest_assessment / gaps / inference records / assessment dependencies

规则固定为：

- `latest_assessment = null` 表示尚未进入 assessment 流程
- 若 `latest_assessment = null`，则 `blocking_gaps`、`non_blocking_gaps`、`applied_inference_records`、`assessment_dependencies` 必须同时为 `null`
- 若 `latest_assessment` 存在但没有 blocking gaps，`blocking_gaps` 返回 `[]`
- 若 `latest_assessment` 存在但没有 inference records，`applied_inference_records` 返回 `[]`
- 若 `latest_assessment` 存在但当前 inference 不依赖历史 assessments，`assessment_dependencies` 返回 `[]`

`applied_inference_records` 是命题局部最小闭包中的完整规则过程载荷。

`assessment_dependencies` 承接 `applied_inference_records` 的直接 assessment 输入闭包。

补充要求：

- `blocking_gaps`、`non_blocking_gaps`、`applied_inference_records` 只允许来自 `latest_assessment`
- `assessment_dependencies` 只允许覆盖 `applied_inference_records.input_assessment_ids` 的直接 assessment 输入，不递归展开更早历史链
- 不得混入 superseded assessment 的 gap 或 推断记录
- `blocking_gaps` 与 `non_blocking_gaps` 的并集必须可完全由 `latest_assessment.blocking_gap_ids` 与 `latest_assessment.non_blocking_gap_ids` 解引用得到
- `applied_inference_records` 必须可完全由 `latest_assessment.applied_inference_record_ids` 解引用得到
- `assessment_dependencies` 必须可完全由 `applied_inference_records.input_assessment_ids` 稳定去重并解引用得到
- `blocking_gaps`、`non_blocking_gaps`、`applied_inference_records` 与 `assessment_dependencies` 的默认排序应分别复用 `assessment.md` 中相邻 规范对象 的稳定排序

agent 应通过它审计：

- assessment 使用了哪些 inference rules
- 每条规则是 `hit`、`miss` 还是 `partial`
- 当前判断直接依赖了哪些 findings / assessments
- 哪些 rule preconditions 未满足，从而导致 gap 保持打开或 assessment 无法升级

对 `applied_inference_records` 的 `rule family` grouping 与版本解释，必须复用 [`rule-registry-contract.md`](rule-registry-contract.md) 中定义的稳定 registry，不得由 上下文面 自行发明分类。

#### artifact_refs

`artifact_refs` 是该 proposition context 闭包涉及 evidence 的最小权威溯源入口。

要求：

- 成员只来自 returned `seed_entries.finding` 与 `relevant_findings` 的来源 artifact 去重集合
- 不因 proposition origin、assessment status 或 gap 状态额外引入新的 artifact 语义
- `artifact_refs` 只承担 查找句柄 职责，不重复完整 provenance payload
- 若同一 artifact 同时支撑多个 findings，在 context 中只能去重出现一次
- `assessment_dependencies` 不单独扩张 `artifact_refs` 的 inclusion boundary；若需要其 provenance，应通过 assessment 自身或其返回 findings 追溯

边界说明：

- 状态面 的 `artifact_refs` 只覆盖 returned `backing_findings` 的来源 artifact
- 上下文面 的 `artifact_refs` 才负责单 proposition 局部最小闭包所需的 artifact handles
- 两个读取面不得为了 convenience 互相扩大 inclusion boundary

consumer 若需要完整 provenance，应回到 artifact object 本身或各 `Finding.provenance` 字段。

## 默认排序与截断

### PropositionContextView 默认排序

`PropositionContextView` 本体不引入额外排序 policy；各集合应分别复用其 规范对象 的稳定排序。

推荐默认规则：

- `seed_entries`：遵循 `proposition.seed_finding_refs` 的 canonical 顺序；不单独引入新的业务排序键
- `relevant_findings`：复用 `finding.md` 的 canonical 稳定排序
- `blocking_gaps` / `non_blocking_gaps`：复用 `assessment.md` 中 `EvidenceGap` 的稳定排序
- `applied_inference_records`：复用 `assessment.md` 中 `InferenceRecord` 的稳定排序
- `assessment_dependencies`：复用 `assessment.md` 中 `Assessment` list 的默认稳定排序
- `artifact_refs`：按来源 findings 的 canonical 顺序稳定去重，不单独引入新的业务排序键

### 截断立场

v1 canonical context view 不定义独立 top-k、compact mode 或 token-budget knobs。

原因：

- 上下文面 的职责是交付围绕单个 proposition 的默认最小闭包
- 若在 规范视图 中加入裁剪 knobs，会把 projection 语义混入 schema
- 读取层应先保证闭包完整性，再由 projection 决定如何做 bounded consumer view

因此：

- `PropositionContextView` v1 不携带独立 `truncation` metadata
- 若未来出现 compact context / audit context，应作为 投影面 扩展定义
- 任何 compact 视图都不得改写 `PropositionContextQuery` 的 canonical target identity

## 查询语义

v1 上下文面 只支持：

- `proposition_ref`

原因：

- 上下文面 的主问题是“围绕哪个 proposition 拉局部闭包”，不是“按哪些筛选条件扫描 propositions”
- proposition-level query 一旦混入 `profile` 或 `include_*` 之类开关，就会把 projection 裁剪错误地提升为 canonical query shape

## 与现有读取面的关系

### 与 proposition / assessment / finding 文档的关系

- `proposition.md` 继续定义判断对象 identity 与 proposition-level focus closure 的 canonical 基线
- `assessment.md` 继续定义 latest snapshot、gap 与 推断记录 的规范语义
- `finding.md` 继续定义事实载荷、focus subject 与 artifact lineage 的基线

本文只定义这些对象在 proposition 局部读取中的组合方式。

### 与 reflection-context 的关系

本文定义的 上下文面 不等价于 `reflection-context`：

- `reflection-context` 是 compact summary
- 上下文面 是 命题中心 canonical 局部闭包
- readiness、tentative claims 与 compact evidence-gap summary 不并入 v1 canonical context object

HTTP 迁移、兼容期与废弃策略属于后续 API 设计，不在本文定义。

## Test Cases

后续实现至少应满足以下 schema-level 验收场景：

1. `PropositionContextQuery` 仅凭 `proposition_ref` 就能唯一确定 context target，不需要额外 `profile` 或 `include_*` 参数
2. `PropositionContextView` 能完整解释 最新评估 的 support / oppose / gap / inference 直接依据；若 inference 直接依赖历史 assessments，也无需再回读其他读取面
3. `seed_entries` 与 `relevant_findings` 的成员集合可重叠，但字段语义不合并
4. 种子事实单元 缺失时，对应 `seed_entries.finding = null`，且 proposition 仍可读取
5. `relevant_findings` 在 `latest_assessment = null` 时返回 `[]`，而不是混入伪造的 assessment-derived finding 集合
6. `assessment_dependencies` 是 `applied_inference_records.input_assessment_ids` 的直接、稳定去重闭包，不递归拉全历史链
7. `artifact_refs` 是 returned `seed_entries.finding` 与 `relevant_findings` 来源 artifact 的稳定去重集合，不重复 provenance payload
8. agent-authored proposition 与 system-seeded proposition 通过同一 `PropositionContextView` 读取，不新增平行 hypothesis context

负向场景至少覆盖：

1. 不允许在 `PropositionContextView` 中加入 `profile`、`mode` 或 `include_*` 一类 projection knobs 作为 canonical query 必选项
2. 不允许把与 target proposition 无关的 session findings、历史 assessment 成员或 reflection compact summary 混入局部闭包
3. 不允许 `latest_assessment = null` 时返回 `[]` 形式的 gap / 推断记录 / assessment dependency 字段
4. 不允许把 `assessment_dependencies` 扩张为递归历史 assessment 全量浏览
5. 不允许把 artifact 查找句柄 误写成 artifact provenance payload

## 非目标

本文不定义：

- HTTP endpoint、分页 token、cache key 或并发语义
- assessment 历史快照检索
- state runtime lifecycle、回滚与重放
- proposition context 的 compact / audit projection profile
- readiness / planning / reflection 的统一调度算法
