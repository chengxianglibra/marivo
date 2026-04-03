# Evidence Engine Runtime Status Surface

本文档定义 Factum Evidence Engine 的 operator-facing runtime status surface。

状态：draft design。本文只定义运行时观测与排障视角的状态面，不重写 canonical evidence object schema，也不把调度状态混入 `SessionStateView` 或 `PropositionContextView`。

## 目的

固定以下问题的统一答案：

- operator 如何判断某个 session / artifact / proposition 当前卡在哪个 stage
- `latest_assessment = null` 时，如何区分“尚未触发 / 正在执行 / 执行失败 / 合法 no-op”
- claim、lease、attempt、retry、backlog、backpressure 应进入哪一层可见性
- 哪些运行时状态可以暴露给 operator，哪些不能进入 agent-facing canonical read surface

## 主题位置

Evidence Engine 中存在两类不同的 truth：

- canonical truth：`artifact -> finding -> proposition -> assessment -> action proposal`
- runtime truth：stage work item、attempt、claim、lease、retry、backlog、publish progression

固定边界：

- canonical truth 由 `session`、`state surface`、`context surface` 对 agent 暴露
- runtime truth 由 runtime status surface 对 operator 暴露
- runtime truth 可以解释“为什么还没得到 canonical 结果”，但不能替代 canonical 结果本身

## Non-goals

本文不定义：

- canonical evidence objects 的字段 schema
- session root 的对外 HTTP endpoint 细节
- worker deployment topology、队列中间件或监控系统选型
- narrative incident report 或 UI 呈现文案

## Fixed Design Decisions

### 1. runtime status surface 不是 canonical read surface 的扩展字段

以下状态不得进入 `SessionStateView` / `PropositionContextView`：

- attempt 是否失败
- 当前 claim owner 是谁
- lease 何时到期
- backlog 是否积压
- 某次 publish 切换是否正在重试

原因：

- 这些都是运行时调度事实，不是 evidence semantics
- 它们会频繁变化，不适合作为 canonical object identity 的一部分
- agent-facing consumer 应该看到稳定的 externally visible bundle，而不是半稳定操作状态

### 2. operator-facing status 必须能回答“卡在哪一层”

v1 至少要能在以下粒度提供状态：

- session 粒度：当前 backlog 是否积压、是否存在 blocked stage、最后成功推进到哪一层
- artifact 粒度：artifact 是否尚未完成 extraction / seeding handoff
- proposition 粒度：assessment / proposal publish path 当前在哪个 stage、最近一次失败原因是什么

### 3. status object 必须区分 stage truth 与 canonical truth

例如 proposition runtime status 可以报告：

- `assessment_recompute` 尚未开始
- `assessment_recompute` 正在执行
- `assessment_recompute` 失败
- `proposal_refresh` 已 committed 但 publish 切换未完成

但它不能直接声称：

- proposition 当前被 support
- 当前 assessment 是什么结论
- 哪些 findings 属于 live support / oppose

这些仍然必须由 canonical read surface 回答。

### 4. runtime status 必须稳定暴露 attempt lineage

为支持恢复与排障，v1 runtime status 至少要暴露：

- `correlation_id`
- `attempt_id`
- `last_successful_stage`
- `current_stage`
- `last_failure_reason`
- `last_failure_at`

这样 operator 才能区分：

- 还没触发
- 已触发但排队中
- 已执行但失败
- 已形成 committed 输出但 publish 尚未完成

### 5. backlog / backpressure 必须是显式状态，而不是隐式猜测

当 runtime 为了限流而延后推进时，status surface 必须能显式说明：

- 是否在等待 claim
- 是否被 queue backlog 延后
- 是否被 policy / dependency / migration gate 阻塞
- 是否只是因为 proposal refresh 尚未完成，所以最新 bundle 尚未切换

## Runtime Status Model

### Session-level status

```ts
type SessionRuntimeStatus = {
  session_id: string;
  overall_status: "idle" | "running" | "blocked" | "degraded";
  last_successful_stage:
    | "artifact_commit"
    | "finding_extraction"
    | "proposition_seeding"
    | "assessment_recompute"
    | "proposal_refresh"
    | "publish";
  blocked_reason:
    | "none"
    | "backpressure"
    | "claim_conflict"
    | "dependency_wait"
    | "retry_exhausted"
    | "migration_required"
    | "policy_blocked";
  backlog_summary: RuntimeBacklogSummary;
  updated_at: string;
  schema_version: "session_runtime_status.v1";
};
```

**v1 implementation constraints:**

- `overall_status` only emits `"idle"` or `"running"`. `"blocked"` and `"degraded"` require a real queue/lease/retry system which does not exist in v1 (synchronous pipeline). They are reserved for future versions.
- `blocked_reason` is always `"none"` in v1 for the same reason.
- `backpressured_propositions` and `failed_items` are always `0` in v1; no backpressure or per-item failure tracking is implemented.
- `updated_at` reflects the session row's `updated_at` column (set at creation time and on any session root update). It does not yet reflect the freshest write across downstream pipeline objects.
- `queued_artifacts` excludes D4-allows-empty artifact types (`observation`, `anomaly_candidates`). In the v1 synchronous pipeline, zero findings is a committed outcome for those families and is indistinguishable from an unprocessed artifact without an extraction-status column.

### Artifact-level status

```ts
type ArtifactRuntimeStatus = {
  session_id: string;
  artifact_id: string;
  artifact_stage:
    | "staged"
    | "extracting"
    | "findings_committed"
    | "seeding_handoff_pending"
    | "failed";
  extractor_key: {
    artifact_type: string;
    artifact_schema_version: string | null;
    extractor_version: string | null;
  };
  correlation_id: string;
  attempt_id: string | null;
  last_failure_reason: string | null;
  last_failure_at: string | null;
  schema_version: "artifact_runtime_status.v1";
};
```

### Proposition-level status

```ts
type PropositionRuntimeStatus = {
  session_id: string;
  proposition_id: string;
  current_stage:
    | "queued"
    | "assessment_recompute"
    | "assessment_committed"
    | "proposal_refresh"
    | "publish_ready"
    | "externally_visible"
    | "failed";
  last_successful_stage:
    | "assessment_recompute"
    | "assessment_committed"
    | "proposal_refresh"
    | "publish";
  current_assessment_id: string | null;
  current_attempt: RuntimeAttemptRef | null;
  backlog_state: "none" | "queued" | "backpressured";
  last_failure_reason:
    | "none"
    | "claim_lost"
    | "retry_exhausted"
    | "rule_execution_failed"
    | "proposal_materialization_failed"
    | "publish_switch_failed"
    | "migration_mismatch"
    | "dependency_missing";
  last_failure_at: string | null;
  schema_version: "proposition_runtime_status.v1";
};
```

**v1 implementation constraints (proposition-level):**

- `current_stage` is derived from committed canonical DB state: no assessment committed → `"queued"`; assessment committed but no proposals → `"assessment_committed"`; assessment and proposals exist but no publish switch → `"publish_ready"`; publish switch executed → `"externally_visible"`. `"externally_visible"` is stable once set — a later re-triggered assessment does not revert the stage until `execute_publish_switch` fires again for the new assessment. Operators can compare `current_assessment_id` with the session state surface to detect pending re-publish work. The `"assessment_recompute"`, `"proposal_refresh"`, and `"failed"` stages are reserved for future versions with real in-flight state tracking.
- `current_attempt` is always `null` in v1. The synchronous pipeline does not maintain claim/lease/retry records.
- `backlog_state` is always `"none"` in v1. No backpressure or queue depth tracking is implemented.
- `last_failure_reason` is always `"none"` and `last_failure_at` is always `null` in v1. Per-item failure tracking is not implemented in the synchronous pipeline.

### Shared attempt object

```ts
type RuntimeAttemptRef = {
  correlation_id: string;
  attempt_id: string;
  claim_owner: string | null;
  claimed_at: string | null;
  lease_expires_at: string | null;
};

type RuntimeBacklogSummary = {
  queued_artifacts: number;
  queued_propositions: number;
  backpressured_propositions: number;
  failed_items: number;
};
```

## Status Semantics

### `queued`

- work item 已进入 runtime truth
- 尚未获得执行 claim
- 不能据此推断 canonical output 是否变化

### `assessment_committed`

- 新 assessment snapshot 已 committed
- proposal refresh 或 publish 切换尚未完全完成
- operator 可以据此知道“canonical 历史已写入，但 externally visible bundle 尚未切换”

### `publish_ready`

- proposition-local bundle 已完整生成
- 缺的只是 externally visible 切换
- 若 runtime 在此卡住，operator 应优先排查 publish switch / claim 冲突，而不是怀疑 rule engine

### `externally_visible`

- 新 bundle 已成为 agent-facing canonical read surface 的当前状态
- 这时 runtime status 与 canonical read surface 才在“最新结果”上重新对齐

## Failure Visibility Rules

runtime status surface 至少要能区分以下 failure families：

- input acquisition failure：上游 committed input 缺失或不可解引用
- execution failure：extractor / rule engine / proposal materializer 执行失败
- claim / lease failure：执行权争用、lease 过期、owner 丢失
- publish failure：bundle 已 ready，但切换 externally visible 失败
- policy / migration block：不是程序崩溃，而是被显式策略或 version gate 阻塞

固定要求：

- failure reason 必须 machine-readable，不能只留自由文本
- 自由文本 explanation 可以作为补充，但不能替代结构化 reason code
- operator 必须能看到最后一次成功推进到哪一层

## Read Path Boundary

当 operator 需要排障时：

- 先看 runtime status surface，判断 work item 是否仍在排队、执行、失败或等待 publish
- 再看 canonical `state/context`，确认当前 externally visible evidence state 是什么

当 agent 需要做分析时：

- 默认只看 canonical read surface
- 除非显式进入 operator / maintenance 模式，否则不应依赖 runtime status surface 做 judgment

## Related Documents

- [`overview.md`](overview.md)
- [`runtime-lifecycle.md`](runtime-lifecycle.md)
- [`runtime-pipeline.md`](runtime-pipeline.md)
- [`read-surfaces.md`](read-surfaces.md)
- [`schemas/session.md`](schemas/session.md)
