# Gap Management Contract

本文档定义 Factum 证据引擎中 `gap_management` 阶段与 `EvidenceGap` 生命周期的拟议总契约。

状态：draft design。本文是 `docs/analysis/` 下的规范设计提案，不表示对应实现、持久化模型或 HTTP endpoint 已经存在。

## 目的

[`assessment.md`](assessment.md) 已定义 `Assessment` / `EvidenceGap` / `InferenceRecord` 的 canonical schema；[`inference-rule-engine-contract.md`](inference-rule-engine-contract.md) 已定义固定 rule family 顺序；[`precondition-gate-contract.md`](precondition-gate-contract.md)、[`quality-gate-contract.md`](quality-gate-contract.md)、[`comparability-gate-contract.md`](comparability-gate-contract.md) 已定义各自 family 如何产出 requirement-level gap 结果。

本文补足的是跨 family 的 gap 总契约，回答以下问题：

- gap identity 按什么语义收敛
- 哪些 family 可以提出 open / resolve candidates
- candidate 如何在 `gap_management` 阶段收敛并 materialize 为 canonical gap state
- `open / keep / resolve / reopen` 的生命周期如何表达
- `blocking` / `severity` 应归属 gap object 还是 assessment snapshot
- state / context / proposal 读取面如何稳定消费当前 gap state

本文不负责：

- 枚举每个 `assessment_type` 的完整 requirement 目录
- 定义任何 family 的具体条件 token 或 impact payload
- 定义对外 HTTP wire contract
- 兼容当前实现或旧持久化模型

## 核心设计决策

### 1. `gap_management` 是唯一的 canonical materialization owner

各 gate family 可以判断 requirement semantics 是否满足，也可以在 record 级别提出 open / resolve 候选；但 canonical gap state 的最终 materialization 只由 `gap_management` 阶段负责。

要求：

- 非 `gap_management` family 不得 out-of-band 创建、关闭或重写 `EvidenceGap`
- family 级 rule 结果只负责表达“哪个 requirement 失败了 / 被满足了”
- `gap_management` 必须把 family 输出先收敛，再决定是否复用旧 gap、打开新 gap、解决旧 gap、以及本轮 snapshot 的 gap classification

### 2. gap identity 按 requirement semantics 收敛

v1 中 gap identity 绑定：

- `session_id`
- `proposition_id`
- `gap_kind`
- `missing_requirement`

其中 `missing_requirement` 必须使用 canonical 的结构化 requirement semantics。

不纳入 gap identity 的内容：

- opening rule 的具体 `rule_id`
- `blocking`
- `severity`
- title / description 文案
- 当前 snapshot 的排序或消费优先级

因此：

- 同一 proposition 下，多条 rule 若指向同一个 `gap_kind + missing_requirement` 语义，必须收敛为同一个 gap identity
- 同一个 requirement semantics 在持续 open 期间跨多个 snapshots 复用同一个 `gap_id`
- classification 变化不会单独触发新的 gap instance

### 3. `blocking` / `severity` 属于 snapshot-owned classification

`EvidenceGap` 本体只表达 requirement-level 缺失事件与其生命周期。

`blocking` / `severity` 不属于 gap object，而属于当前 assessment snapshot 对该 gap 的消费分类。

因此：

- 同一个 open gap 可以在不同 snapshots 中改变 blocking / severity
- classification 变化会改变 assessment snapshot，但不会改写 gap identity
- 读取面上的 `blocking_gaps` / `non_blocking_gaps` 必须来自 latest assessment 的 gap membership 投影

### 4. keep 是 materialized result，不是独立 object transition

`open` 与 `resolve` 是 gap object 生命周期事件；`keep` 不是第三种 object status。

`keep` 表示：

- 某个 requirement semantics 在本轮 recompute 后仍未满足
- 且已存在匹配的 open gap instance
- 因此当前 snapshot 继续引用该 gap，并允许更新 snapshot-owned classification

### 5. reopen 建模为新的 gap instance

v1 中 reopen 以“相同 requirement semantics 再次缺失”为事件语义，而不是旧 gap object 的状态回退。

规则：

- 单个 gap object 的生命周期只允许 `open -> resolved`
- 已 resolved 的 gap 不得被改回 `open`
- 相同 requirement semantics 后续再次缺失时，必须创建新的 `gap_id`
- 旧 resolved gap 保留为历史对象，不重新进入当前 gap membership

## Schema Position

规范判断链路保持：

`finding -> proposition -> inference rules -> gap_management -> assessment / evidence_gap / inference_record`

其中：

- gate families 负责产出 requirement-level candidates
- `gap_management` 负责 candidate convergence 与 canonical materialization
- `EvidenceGap` 负责 requirement-level lifecycle
- `GapMembershipEntry` 负责 snapshot-owned classification
- `Assessment` 负责持有当前 snapshot 的 `gap_memberships`

## Typed Design Sketch

以下类型仅用于说明 contract，不替代 canonical schema。

```ts
type GapIdentityKey = {
  proposition_id: string;
  gap_kind: EvidenceGap["gap_kind"];
  missing_requirement: GapRequirement;
};

type GapResolutionCandidate = {
  source_inference_record_id: string;
  proposition_id: string;
  gap_kind: EvidenceGap["gap_kind"];
  missing_requirement: GapRequirement;
  action: "open" | "resolve";
};

type GapMembershipEntry = {
  gap_ref: EvidenceGapRef;
  blocking: boolean;
  severity: "low" | "medium" | "high" | "critical";
};

type GapMaterializationOutcome = {
  gap_objects: EvidenceGap[];
  gap_memberships: GapMembershipEntry[];
  updated_inference_records: InferenceRecord[];
};
```

约束：

- `GapResolutionCandidate` 的 identity 由 `gap_kind + missing_requirement` 收敛，不由 `rule_id` 收敛
- `GapMembershipEntry` 只描述当前 snapshot 的 live classification
- 同一个 `gap_ref` 在单个 snapshot 内最多出现一次

## Input Contract

`gap_management` 至少读取：

- target proposition
- 本次 recompute 中各 family 生成的 `InferenceRecord`
- 同一 proposition 的 prior assessments
- same proposition latest assessment 中仍为 open 的 `EvidenceGap`

它不得读取：

- 其他 proposition 的 gaps
- projection-only summaries
- 自由文本 recommendation 或非 canonical state

## Candidate Convergence Rules

### 1. family 只提交 requirement-level candidates

`precondition_gate`、`quality_gate`、`comparability_gate` 可以针对各自职责范围内的 requirement 提出：

- open candidate
- resolve candidate

它们不能直接决定：

- 复用哪个已有 `gap_id`
- 是否因为 classification 变化而新建 gap instance
- 当前 snapshot 中最终属于 blocking 还是 non-blocking

### 2. 收敛主键固定为 canonical requirement semantics

对同一 proposition，`gap_management` 必须先按以下主键收敛所有 candidates：

- `gap_kind`
- `missing_requirement.requirement_type`
- `missing_requirement.requirement_key`
- `missing_requirement.requirement_params`

要求：

- 多条 rule 指向同一 requirement semantics 时，执行顺序不得影响结果
- 不允许因为同一语义被不同 family 内 rule 重复提及，就 materialize 多个 gap objects
- 若文案不同但 requirement semantics 相同，仍视为同一 identity

### 3. resolve 优先级受 requirement satisfaction 约束

某个 existing open gap 只有在以下条件同时满足时才允许 resolve：

- 本轮对同一 requirement semantics 形成了结构化“已满足”结论
- 至少一条 `InferenceRecord` 显式把该 gap 写入 `resolved_gap_ids`

不允许：

- 仅因“本轮没有再提到它”就视为 resolved
- 仅因另一条无关 rule 通过，就顺带解决该 gap

### 4. keep 与 resolve/open 可以同时发生在不同 identities 上

同一 recompute 中允许：

- resolve 一个旧 gap
- keep 另一个旧 gap
- open 一个新的 gap

但不允许对同一 gap identity 在同一 snapshot 中同时 materialize open 与 resolve。

## Materialization Rules

### Open

当某个 requirement semantics 当前未满足，且 latest assessment 中不存在匹配的 open gap 时：

- 创建新的 `EvidenceGap`
- `opened_by_inference_record_id` 必须指向本轮某条 opening record
- 该 gap 是否进入本轮 snapshot，由 `gap_memberships` 决定

### Keep

当某个 requirement semantics 当前未满足，且 latest assessment 中已存在匹配的 open gap 时：

- 复用既有 `gap_id`
- 不创建新的 gap object
- 在新的 assessment snapshot 中重新写入该 gap 的 `GapMembershipEntry`
- 允许 `blocking` / `severity` 与上一 snapshot 不同

### Resolve

当某个 requirement semantics 当前已满足，且 latest assessment 中存在匹配的 open gap 时：

- 将该 gap object 标记为 `resolved`
- `resolved_by_inference_record_id` 必须指向本轮某条 resolving record
- 新 snapshot 不再把该 gap 纳入 `gap_memberships`

### Reopen

当某个 requirement semantics 的旧 gap 已 resolved，而后续 recompute 中再次未满足时：

- 创建新的 `EvidenceGap`
- 不得复用旧 `gap_id`
- 不得通过把旧 gap 的 `status` 改回 `open` 表达 reopen

## Snapshot Classification Rules

### GapMembershipEntry

`GapMembershipEntry` 是 assessment snapshot 内唯一的 canonical gap membership 载体。

最低字段：

- `gap_ref`
- `blocking`
- `severity`

要求：

- `gap_ref` 必须指向同一 proposition 的 gap
- 单个 snapshot 内同一 `gap_ref` 只出现一次
- `blocking = true` 表示该 gap 当前阻止更强状态升级或稳定收敛
- `blocking = false` 表示该 gap 当前只是 caveat，不阻塞当前可用判断

### severity

`severity` 只表达当前 snapshot 对 gap pressure 的分类，不表达 gap identity。

建议枚举：

- `low`
- `medium`
- `high`
- `critical`

要求：

- severity 可以跨 snapshots 变化
- severity 变化必须触发新的 assessment snapshot
- severity 不能单独决定 gap 是否新建或 resolved

## Read Surface Binding

state / context / proposal 读取面必须遵守：

- `blocking_gaps` 只来自 latest assessment 中 `gap_memberships` 里 `blocking = true` 的 members
- `non_blocking_gaps` 只来自 latest assessment 中 `gap_memberships` 里 `blocking = false` 的 members
- `served_gap_refs` 只能引用 canonical gap objects；若 consumer 需要当前 blocking / severity，必须回到 primary assessment 的 gap memberships 读取
- 读取面不得把 snapshot-owned classification 回写到 `EvidenceGap` 本体

## Acceptance Scenarios

后续实现至少应满足以下场景：

1. 同一 recompute 中两条 rule 指向同一 `gap_kind + missing_requirement`，最终只 materialize 一个 gap identity。
2. 某个 open gap 在下一次 recompute 中仍未满足 requirement semantics 时复用 `gap_id`，但允许 severity 从 `medium` 变为 `high`。
3. 某个 open gap 在 requirement 被满足且存在显式 resolving record 时转为 `resolved`，并从新 snapshot 的 `gap_memberships` 中移除。
4. 某个 resolved gap 在更后续 recompute 中再次失败时创建新的 `gap_id`。
5. 同一 snapshot 中允许 “resolve 一个 gap，同时 open 另一个 gap”，但不允许同一 identity 同时 open 和 resolve。
6. state/context 读取面中的 blocking / non-blocking gap 集合必须可完全由 latest assessment 的 `gap_memberships` 投影得到。

## Cross References

- [`assessment.md`](assessment.md)
- [`inference-rule-engine-contract.md`](inference-rule-engine-contract.md)
- [`precondition-gate-contract.md`](precondition-gate-contract.md)
- [`quality-gate-contract.md`](quality-gate-contract.md)
- [`comparability-gate-contract.md`](comparability-gate-contract.md)
