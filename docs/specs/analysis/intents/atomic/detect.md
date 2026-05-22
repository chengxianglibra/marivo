# detect 原子意图 Schema

本文档定义 `detect` 原子意图的类型契约。

状态：active design。`detect` 已切换为 artifact-input 操作，并返回 `candidate_set` artifact。

## 目的

`detect` 用于扫描已提交的 AOI frame artifact，并返回排序后的异常候选集合。

目标：

- 让 `detect` 聚焦候选发现（candidate discovery），不承担诊断或解释。
- 只读取已提交 typed artifact，不在内部重新查询源表、解析 metric execution context 或构造隐藏 baseline。
- 由输入 artifact family/shape 决定检测语义，避免请求里出现冲突的 `strategy` 状态。
- 暴露稳定的 candidate item id 和 source frame point reference，方便 evidence、diagnose 和 UI 下游追踪。

## Request Shape

```json
{
  "source_artifact_id": "art_metric_or_delta_123",
  "sensitivity": "balanced",
  "limit": 5
}
```

## Typed Schema

```ts
type DetectRequest = {
  source_artifact_id: string;
  sensitivity?: "conservative" | "balanced" | "aggressive";
  limit?: number;
};
```

说明：

- `source_artifact_id` 必须指向当前 session 中已提交的 AOI artifact。
- 省略 `sensitivity` 时默认为 `"aggressive"`。
- `limit` 可选；提供时必须大于 0。
- `strategy` 不是请求字段。runtime 从输入 artifact 推导策略：

```text
metric_frame(time_series | panel) -> point_anomaly
 delta_frame(time_series_delta | panel_delta) -> period_shift
```

Output type: `candidate_set`。

## 支持的输入 artifact

| Source family | Source shape | Strategy | Output shape |
| --- | --- | --- | --- |
| `metric_frame` | `time_series` | `point_anomaly` | `point_anomaly_candidates` |
| `metric_frame` | `panel` | `point_anomaly` | `point_anomaly_candidates` |
| `delta_frame` | `time_series_delta` | `period_shift` | `period_shift_candidates` |
| `delta_frame` | `panel_delta` | `period_shift` | `period_shift_candidates` |

## v1 不支持的输入

- `metric`
- `time_scope`
- `granularity`
- `filter`
- `dimension`
- `strategy`
- `metric_frame(scalar | segmented)`
- `delta_frame(scalar_delta | segmented_delta)`
- `attribution_frame`
- `candidate_set`
- hidden previous-adjacent baseline queries inside `detect`

推荐错误码：`INVALID_ARGUMENT` 或 `ARTIFACT_NOT_FOUND`。

## Response Shape

```ts
type CandidateSetArtifact = {
  artifact_id: string;
  artifact_family: "candidate_set";
  shape: "point_anomaly_candidates" | "period_shift_candidates";
  subject: {
    kind: "candidate_scan";
    metric_ref: string;
    source_artifact_id: string;
    source_artifact_family: "metric_frame" | "delta_frame";
    source_shape: string;
  };
  axes: Array<{ kind: "time"; grain: string } | { kind: "dimension"; name: string }>;
  measures: Array<{ id: string; value_type: "number"; nullable: boolean; unit?: string | null }>;
  capabilities: ["filterable"];
  lineage: {
    operation: "detect";
    source_artifact_ids: string[];
    strategy: "point_anomaly" | "period_shift";
  };
  payload: {
    items: CandidateItem[];
    scan_summary: {
      scanned_series_count: number;
      total_candidate_count: number;
    };
    truncation: {
      returned_candidate_count: number;
      total_candidate_count: number;
      truncated: boolean;
    };
    quality: {
      status: "detectable" | "needs_attention";
      issues: Array<{ code: string; severity: "warning" | "error"; message: string }>;
    };
  };
};

type FramePointRef = {
  artifact_id: string;
  series_index: number;
  point_index: number;
  series_keys: Record<string, string>;
  point_key: string;
};

type CandidateItem = {
  item_id: string;
  source_point_ref?: FramePointRef;
  source_delta_point_ref?: FramePointRef;
  window: { start: string; end: string };
  baseline_window?: { start: string; end: string } | null;
  keys: Record<string, string> | null;
  value: number | null;
  baseline_value?: number | null;
  delta_abs?: number | null;
  delta_pct?: number | null;
  score: number;
  direction: "increase" | "decrease" | "unknown";
};
```

规则：

- `point_anomaly_candidates` item 必须带 `source_point_ref`。
- `period_shift_candidates` item 必须带 `source_delta_point_ref`。
- `period_shift_candidates` 可带 `baseline_window`；该字段来自源 `delta_frame` 的对齐 bucket，供 diagnose follow-up 沿用同一个 baseline。
- `keys = null` 表示 overall series；panel 输入使用 dimension key map。
- 成功时即使没有候选，也提交 `candidate_set`，其中 `payload.items = []`。

## Scoring Semantics

- `score` 是同一个 candidate set 内部的非负排序值。
- `point_anomaly` score 来自 metric frame 内的点异常扫描。
- `period_shift` score 来自 delta frame 中已有的 current/baseline/delta 值。
- score 不是跨 artifact 可比的严重程度标签。

## Evidence Boundary

Evidence extraction 从 `candidate_set.payload.items[]` 生成 canonical `anomaly_candidate` findings。`detect` 本身只表示“候选值得关注”，不表示异常已经被证明。
