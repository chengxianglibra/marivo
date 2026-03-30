# 前提条件门槛契约

本文档定义 Factum 推断规则引擎中 `precondition_gate` 规则族的拟议契约。

状态：draft design。本文是 `docs/analysis/` 下的规范 family-level 设计提案，不表示对应实现、持久化模型或 HTTP endpoint 已经存在。

## 目的

[`inference-rule-engine-contract.md`](inference-rule-engine-contract.md) 已定义 `precondition_gate` 属于固定评估顺序中的门槛族，并约束其不能越权写最终判断。

本文补足的是 `precondition_gate` 自身的 family-level 契约，回答以下问题：

- `precondition_gate` 允许读取哪些规范输入
- 哪些最低前提属于它的判断范围
- 它如何把结果稳定映射到 `InferenceRecord` 与 `EvidenceGap`
- 哪些条件 token 必须结构化写入 `matched_conditions` / `unmatched_conditions`
- 它如何与注册表、缺口生命周期与仅变化快照策略对齐

其中 gap identity convergence、`open / keep / resolve / reopen` 生命周期，以及 snapshot-owned `blocking` / `severity` classification 的总规则，以 [`gap-management-contract.md`](gap-management-contract.md) 为准；本文只定义 `precondition_gate` 如何贡献 requirement-level candidates 与 record 语义。

本文不负责：

- 新增或改写规则族
- 定义评估特定的判断门槛
- 枚举每个 `assessment_type` 的完整必需前提条件
- 定义规则实现代码组织方式

## Non-goals

本文不定义：

- 最终 `Assessment.status` 的判断策略
- `quality_gate` 或 `comparability_gate` 的门槛
- 评估特定要求目录的完整枚举
- 规则加载方式、执行器实现、存储表结构或对外 HTTP 契约

## 核心设计决策

### 1. `precondition_gate` 是既有 gate family，不是新增阶段

`precondition_gate` 已是 v1 固定 `rule_family` 之一。

本文只细化该 family 的 schema contract，不新增新的 family、cluster 层级或执行顺序。

### 2. 输入边界固定为单 proposition canonical evaluation context

`precondition_gate` 只能读取当前 target proposition 的 canonical evaluation context：

- target `proposition`
- 当前 proposition closure 中可解引用的 `findings`
- 同一 proposition 的 `prior_assessments`
- 同一 proposition 当前仍为 `open` 的 `EvidenceGap`

不允许读取：

- 其他 proposition 或其他 proposition 的 assessment / gap
- projection summary、自由文本 evidence description
- 临时评分对象、黑盒模型输出或 out-of-band telemetry

### 3. 职责是判断最低进入条件，不是决定最终结论

`precondition_gate` 负责回答的问题只有：

- 当前 proposition 是否具备进入该 `assessment_type` judgment track 的最低输入条件
- 哪些前提已经满足
- 哪些前提缺失或仅部分满足
- 哪些缺失前提需要被 materialize 成可追溯 gap

`precondition_gate` 不负责：

- 决定最终 `Assessment.status`
- 决定 support / oppose membership
- 直接给出高 confidence 的 `supported` / `contradicted`
- 通过未结构化说明隐式解决 gap

### 4. family 关注四类前提

v1 中 `precondition_gate` 允许判断的前提类别固定为：

1. required finding family
2. subject coverage
3. time coverage
4. rule-specific structured preconditions

其中：

- required finding family 用于回答“缺没缺最基本的事实输入”
- subject coverage 用于回答“当前 finding 是否覆盖 proposition 所锚定的 subject 轴”
- time coverage 用于回答“当前 finding 是否覆盖判断所需时间窗口”
- rule-specific structured preconditions 用于表达无法降成前三类、但仍属稳定结构化前提的条件

若某项条件本质上是数据质量、样本量、null rate 或可比性前提，应分别进入 `quality_gate` 或 `comparability_gate`，不得回流到 `precondition_gate`。

## Schema Position

`precondition_gate` 位于固定 evaluation order 的最前段 gate 阶段，并先于：

- `quality_gate`
- `comparability_gate`
- `support_evidence`
- `oppose_evidence`
- `status_resolution`

它的输出只为后续 family 提供结构化前提状态，不形成逆序依赖。

## Typed Design Sketch

以下类型仅用于说明 family-level contract，不替代 canonical schema。

```ts
type PreconditionRequirementKind =
  | "finding_family"
  | "subject_coverage"
  | "time_coverage"
  | "rule_precondition";

type PreconditionRequirementRef = {
  requirement_kind: PreconditionRequirementKind;
  requirement_key: string;
};

type PreconditionEvaluationResult = {
  rule_id: string;
  result: "hit" | "miss" | "partial";
  matched_condition_tokens: string[];
  unmatched_condition_tokens: string[];
  opened_gap_refs: PreconditionRequirementRef[];
  resolved_gap_refs: PreconditionRequirementRef[];
};
```

## Input Contract

### proposition 输入

`precondition_gate` 必须从 target proposition 读取：

- `proposition_id`
- `assessment_anchor`
- canonical subject 信息
- seed finding refs

它不得改写 proposition identity，也不得发明额外的判断锚点。

### finding 输入

`precondition_gate` 只能基于 canonical finding identity 与结构化 finding payload 判断前提是否满足。

要求：

- 输入 finding 必须已提交到 canonical finding layer
- 读取必须依赖稳定 `finding_id`
- finding 是否满足前提，必须能解释回 proposition subject / family / seed / prior inference dependency

### prior assessment 与 open gap 输入

`prior_assessments` 与 `open_gaps` 仅用于：

- 判断某项 precondition gap 是否继续保持
- 判断某项旧 gap 是否在本次被显式解决
- 在不改变最终 status 决议权限的前提下，为后续 family 提供保守前提状态

它们不能被用来“继承旧结论”，也不能把历史命中过的强结论当作当前前提满足的替代品。

## Requirement Mapping Contract

### 1. finding family 缺失

当最低所需 finding family 不足时：

- gap 应优先映射到 `gap_kind = "missing_finding"`
- `missing_requirement.requirement_type = "finding_family"`
- `requirement_key` 必须稳定标识该 finding family requirement

### 2. subject coverage 缺失

当 proposition 所需的 metric/entity/slice/grain 轴未被最低输入覆盖时：

- gap 应映射到 `gap_kind = "missing_slice"`
- `missing_requirement.requirement_type = "subject_coverage"`
- `requirement_params.missing_axis` 必须明确是 `metric`、`entity`、`slice` 或 `grain`

### 3. time coverage 缺失

当当前 finding 无法覆盖进入判断所需的最小时间窗口时：

- gap 应映射到 `gap_kind = "missing_time_coverage"`
- `missing_requirement.requirement_type = "time_coverage"`
- `requirement_params` 必须携带稳定的窗口边界与粒度

### 4. structured rule precondition 缺失

当某项前提无法归入 finding family、subject coverage、time coverage，但仍属稳定结构化 requirement 时：

- gap 应映射到 `gap_kind = "missing_rule_precondition"`
- `missing_requirement.requirement_type = "rule_precondition"`
- `requirement_params.rule_id` 必须指向当前 rule
- `requirement_params.missing_condition` 必须是稳定 condition token，而不是自由文本

要求：

- 本 family 不新增新的 `gap_kind`
- 不能把质量或可比性失败伪装成 `missing_rule_precondition`
- gap identity 必须绑定 proposition 与 requirement semantics，而不是绑定一次运行实例

### 5. `requirement_key` 采用稳定语义命名，不默认内嵌实现细节

`requirement_key` 应优先表达“这是哪一个稳定 precondition requirement”，而不是直接复制实现参数。

推荐约束：

- 使用小写 ASCII `snake_case`
- 禁止包含 `:`
- 优先表达 requirement 语义或消费场景，而不是目录名、类名、临时文案或调试编号
- 只有当同一 proposition 下需要并存多个同类 requirement，且仅靠 requirement kind 无法区分语义时，才允许把稳定限定词并入 key

示例：

- 推荐：`metric_snapshot_present`
- 推荐：`target_subject_covered`
- 推荐：`comparison_window_covered`
- 推荐：`paired_baseline_available`
- 不推荐：`precondition-check-01`
- 不推荐：`subject:baseline`
- 不推荐：`ruleA_tmp_fix`

## Record Mapping Contract

### `result` 判定

`precondition_gate` 的 `InferenceRecord.result` 约束如下：

- `hit`：当前 rule 所要求的最低前提全部满足，且这一通过对当前 snapshot 的 rule coverage 或 gap resolve 有解释价值
- `miss`：核心前提不满足，且该失败足以解释当前 `insufficient`、gap 保持或保守降级
- `partial`：部分前提满足，能贡献局部可用输入或 caveat，但仍不足以视为完全通过

更细规则：

- 当某条 rule 的核心 blocking requirement 缺失，且该 rule 无法再为后续 family 提供可用的最低输入时，应产出 `miss`
- 当核心 requirement 已满足，但仍有次级 requirement、覆盖范围或前提 caveat 未满足，且这些未满足状态需要被后续 family 消费时，应产出 `partial`
- `partial` 不表示“几乎等于通过”；它表示“当前可进入后续判断，但必须保留结构化 caveat 或未满足前提”
- `partial` 与 `miss` 的分界，不取决于文案强弱，而取决于当前输入是否仍是该 judgment track 的合法最低输入
- `partial` 可以伴随 blocking gap、non-blocking gap，也可以完全不打开 gap；是否 materialize gap 取决于该 caveat 是否需要被稳定追踪为 requirement-level 缺口
- 同一 `precondition_gate` family 下，多条 rule 可以在一次 recompute 中分别对不同 requirement 发出 `partial`；canonical 读取面应保留逐条 `rule_id` 审计，不把它们折叠成单个 family-level 布尔值
- downstream family 不直接消费自由文本；它们只消费 `result`、已打开或已解决的 gap、以及 `matched_conditions` / `unmatched_conditions` 中的稳定 token

示例边界：

- `partial`：required finding family 已满足，但 subject coverage 只覆盖 target subject 的核心 metric，缺少次级 slice 轴；当前仍可进入后续判断，但必须保留 coverage caveat
- `partial`：time coverage 已覆盖主判断窗口，但缺少一个次级对照子窗口；当前仍是合法最低输入，但应保留未满足时间前提
- `partial`：某条 rule-specific structured precondition 只缺少非 blocking 的补充条件，当前 finding 仍可作为最低输入
- `miss`：required finding family 本身缺失，导致该 proposition 尚未具备进入 judgment track 的最低事实输入
- `miss`：time coverage 缺失到当前 finding 已不能覆盖最小判断窗口，后续 family 不应继续把它当作该 track 的合法最低输入

要求：

- 不允许只在 `hit` 时写 record，而忽略有解释价值的 `miss` / `partial`
- 仅凭 `hit` 不得直接推出高 confidence 的单向强结论
- 若某个 `partial` 没有 materialize gap，downstream 必须仍能仅凭 `result` 与 condition tokens 消费该 caveat，不得依赖自由文本补充语义

### gap 字段

`precondition_gate` 打开或解决 gap 时，必须显式通过 record 驱动：

- 新打开的 gap 进入 `opened_gap_ids`
- 已满足原 requirement 的旧 gap 进入 `resolved_gap_ids`

解决归属规则：

- gap 是否解决，由当前 recompute 中命中该 requirement semantics 的 rule 决定；它不要求必须是“当初打开该 gap 的同一条 rule”，但必须属于同一 proposition 的 `precondition_gate` family，并能稳定指向同一个 `requirement_key`
- 不允许由其他 `assessment_type` 的 `precondition_gate` 直接解决当前 proposition / assessment track 的 gap；跨 `assessment_type` 若需要共享前提，必须先共享 requirement semantics，而不是跨 track 直接关闭 gap
- 若某个 gap 被解决，但同一 snapshot 中其他 preconditions 仍失败，则只解决已满足的那一个 gap；当前 assessment 仍可因剩余 preconditions 保持 `insufficient`、维持其他 open gaps，或继续形成保守输出
- 若同一 recompute 中有多条 rule 指向同一个 precondition requirement semantics，它们可以分别产出 record，但 candidate gap membership 必须按 gap identity 做集合归并；不得因 rule 顺序产生不同结果
- 若多条 rule 对同一 gap identity 给出相互冲突的 open / resolve 候选，实现必须先按同一 `requirement_key` 的 canonical requirement semantics 收敛后再 materialize，不能采用“最后一条规则覆盖前一条”的隐式优先级

最小收敛规则：

- rule 只负责产出 record-level open / resolve 候选，不以执行顺序直接决定最终 gap membership
- open / resolve 候选必须先按 proposition 与 gap identity 归并，再决定最终 materialization
- 同一 gap identity 只要仍存在未满足的 canonical precondition，就不得在本次 snapshot 中 resolve
- 只有当该 gap identity 对应的 canonical requirement semantics 已被满足时，才允许 resolve

不允许：

- 仅因“这次没再提旧 gap”就视为已解决
- 同一 gap 在同一 snapshot 内同时出现在 open 与 resolve 两个集合

### status transition 字段

`precondition_gate` 不是最终状态决议 family。

因此：

- 默认不应单独写 `produced_status_transition`
- 若最终 snapshot 发生状态变化，应由参与最终状态决议的 record 写入 transition
- `precondition_gate` 的贡献应通过 `result`、gap 字段与 condition tokens 被后续 family 消费

## Condition Token Contract

### 目标

`matched_conditions` 与 `unmatched_conditions` 必须承载 `precondition_gate` 的主语义，做到：

- 稳定可比较
- 可被审计
- 可被 replay / compatibility 检查复用
- 可追溯回具体 requirement semantics

`notes` 只用于少量补充说明，不承载唯一主语义。

### token 命名规范

v1 先固定 token 组织原则，不在本文枚举所有 assessment-specific token。

token 应满足：

- 表达前提类别，而不是实现细节
- 可稳定对应到 `requirement_key` 或缺失条件键
- 不依赖目录名、类名、临时字符串拼接策略

推荐形态：

- finding family：`finding_family:<requirement_key>:present|missing|partial`
- subject coverage：`subject_coverage:<requirement_key>:covered|missing`
- time coverage：`time_coverage:<requirement_key>:covered|missing|partial`
- rule precondition：`rule_precondition:<requirement_key>:met|missing`

示例：

```yaml
matched_conditions:
  - "finding_family:metric_snapshot:present"
  - "subject_coverage:target_metric:covered"
  - "time_coverage:comparison_window:covered"

unmatched_conditions:
  - "subject_coverage:baseline_metric:missing"
```

要求：

- `matched_conditions` 只写满足的稳定 token
- `unmatched_conditions` 只写缺失或失败的稳定 token
- 若某项 precondition 被 materialize 为 gap，相关 token 必须能回指同一 `requirement_key`
- token 中嵌入的 `requirement_key` 必须满足本节保留字符约束，避免与 `:` 分隔符冲突

assessment-specific profile 后续若要新增 requirement catalog，必须复用这套 token 语义，而不是为每条 rule 自发明自由文本。

## Registry Contract

`precondition_gate` 的 family 归属仍完全依赖 [`rule-registry-contract.md`](rule-registry-contract.md)：

- `InferenceRecord` 不新增 `rule_family` 字段
- `rule_id -> rule_family -> assessment_type` 必须由 registry 显式解引用
- 读取面不得通过字符串前缀、目录路径或类名推断 family

当某条 rule 注册到 `precondition_gate` 时，至少应满足：

- `rule_family = "precondition_gate"`
- `assessment_type` 明确
- `rule_cluster` 仅用于更细业务分组，不改变 family 语义
- `rule_version` 变更时，不得破坏已持久化 token / gap 语义的可解释性

安全演进要求：

- 若仅新增 token 或 requirement key，且不改变既有 token 的既有语义，可视为 backward-compatible 变更
- 若某个 token 将废弃，应至少保留一个兼容窗口：旧 token 仍可被 replay / 审计解释，新增实现可同时产出旧新映射，或在 registry / 兼容文档中声明等价关系
- 若必须改变既有 token 的语义或 requirement semantics，应视为 breaking change，并伴随新的 `rule_version`；breaking 变更不得复用旧 token 字面值去表达新语义
- 对已持久化 records，不要求原地迁移 token 文本；但 replay、compatibility 检查与读取面必须仍能通过对应版本边界解释旧 token

## Upstream Contract Violations

以下情形属于上游 schema / engine / registry contract 问题，不应被 `precondition_gate` 降格成普通 `miss` / `partial`：

- target proposition 或 finding 不满足其 canonical schema，导致 requirement semantics 无法稳定判定
- `rule_id` 无法通过 registry 稳定解引用到 `precondition_gate`
- open gap payload 无法稳定解引用到 proposition + requirement semantics
- 某个 finding 虽可读取，但无法解释为当前 proposition 的合法 canonical 输入关系

这些问题应由上游 contract 中止、升级处理或单独修复，而不是 materialize 成普通 precondition failure record 或 gap。

## Snapshot And Lifecycle Constraints

`precondition_gate` 必须兼容 change-only snapshot policy：

- 相同 canonical inputs 重算，不得仅因 token 顺序、说明文本或实现细节变化制造新 snapshot
- canonical outcome 未变时，本轮 candidate records 必须整体丢弃
- 若 gap membership 改变，即使 status 不变，也必须允许形成新 snapshot
- 若关键 finding 当前不可解引用而导致前提重新缺失，必须允许 downgrade 或 gap reopen

`precondition_gate` 不得假设状态只会单调增强。

对 `precondition_gate` 而言，canonical outcome 至少包括：

- 每条 rule 的 `result`
- `matched_conditions` / `unmatched_conditions` 的成员集合
- `opened_gap_ids` / `resolved_gap_ids` 的成员集合
- 是否因此改变了当前 candidate assessment 的 blocking / non-blocking gap membership

不应进入 canonical equality 判断的内容包括：

- `notes` 的措辞
- record / gap 的展示顺序
- 时间戳或其他非 judgment 语义的运行时元数据

## Acceptance Scenarios

1. proposition 首次进入评估，但 required finding family 不足时，`precondition_gate` 写入 `miss` record，并打开 blocking `missing_finding` gap。
2. finding 已到达但 subject coverage 不完整时，`precondition_gate` 写入 `partial` record，并把缺失轴映射到 `subject_coverage` requirement。
3. time coverage 不满足时，`precondition_gate` 打开的 gap 能稳定追溯到窗口边界与粒度，而不是依赖自由文本描述。
4. 某条旧 `missing_rule_precondition` gap 在新 finding 到达后被满足时，本轮必须通过 `resolved_gap_ids` 显式解决该 gap。
5. 某条 `partial` record 即使没有 materialize gap，downstream 仍必须能仅凭 `result` 与 condition tokens 消费该 caveat，而不是依赖自由文本。
6. 若同一 recompute 中两条 rule 指向同一个 `requirement_key`，其中一条给出 open 候选、另一条给出 resolve 候选，最终 gap membership 必须先按 requirement semantics 收敛，而不是依赖 rule 执行顺序。
7. 相同 canonical inputs 重算时，condition token、gap identity 与 record result 保持稳定，不制造额外 snapshot。
8. malformed proposition / finding、registry 无法解引用或 gap payload 破损时，应按上游 contract violation 处理，而不是压成普通 precondition `miss`。
9. `precondition_gate` 全部命中通过时，系统仍需经过后续 evidence 与 `status_resolution` family 才能形成最终 judgment。

## 与其他文档的关系

- [`inference-rule-engine-contract.md`](inference-rule-engine-contract.md) 定义 rule family 的固定 evaluation order、通用职责边界与 `InferenceRecord` 写入总规则
- [`assessment.md`](assessment.md) 定义 `EvidenceGap` 与 `InferenceRecord` 的 canonical schema
- [`rule-registry-contract.md`](rule-registry-contract.md) 定义 `rule_id -> rule_family -> assessment_type` 的稳定解引用
- [`assessment-judgment-policy.md`](assessment-judgment-policy.md) 定义不同 `assessment_type` 的最终 judgment policy
- [`rule-family-design-checklist.md`](rule-family-design-checklist.md) 提供 family-level 设计评审 checklist
