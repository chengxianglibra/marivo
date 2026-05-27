# Transform 算子 v1 实现设计

状态：design。本文是 `marivo.analysis_py.transform` 的首版实现设计，目标是把 [python-analysis-operator-design.md](../../specs/analysis/python-analysis-operator-design.md) 中的 `transform` 落地到代码与测试。该文档不替代算子总体设计，只描述实现选择与边界。

## 1. 范围

v1 实现以下 op，覆盖 `metric_frame` 与 `delta_frame` 两种输入 family：

- `filter`
- `slice`
- `rollup`
- `topk`
- `bottomk`
- `rank`
- `normalize`
- `window`

以下 op 留作 follow-up，本次不实现：

- `align_time`（需要先把 `AlignmentPolicy` 的全部 kind 在代码层完整定义）
- `dedupe` / `impute_nulls` / `winsorize` / `strip_outliers`（需要 cleaning policy lineage 字段与 quality warning 落地）

`attribution_frame` 作为输入也留作 follow-up；v1 直接以结构化错误拒绝。

## 2. 架构与模块结构

新文件 `marivo/analysis_py/intents/transform.py`，对外只暴露单个函数 `transform(...)`，内部按 op 分发：

```
marivo/analysis_py/intents/transform.py
  transform(frame, *, op, **kwargs) -> MetricFrame | DeltaFrame   # public dispatcher
  _OP_DISPATCH: dict[str, Callable]
  _op_filter / _op_slice / _op_rollup / _op_topk / _op_bottomk
  _op_rank / _op_normalize / _op_window                           # private per-op functions
  _validate_op_for_family(...)
  _recompute_axes(...)
  _persist_transform_frame(...)
  _params_digest(...)                                             # shared with observe-style digest
```

公共导出：

- `marivo/analysis_py/intents/__init__.py` 追加 `transform`
- `marivo/analysis_py/__init__.py` 追加 `transform` 与新增的错误类型
- `marivo/analysis_py/help.py` 注册 `transform`

不引入新 subpackage。所有 per-op 实现放在同一个文件内，便于 dispatch table 一次性读完。

## 3. 执行模型

Transform 在 `frame._df`（已经是 materialized pandas DataFrame）之上执行：

- 不重新构造 ibis 表达式、不回 backend。
- 不进入 `backend_cache`，不更新 `session.known_datasources`。
- 每次调用产出一个新的 `MetricFrame` / `DeltaFrame`，并通过现有 `write_frame_to_disk` + `write_job_record` 落盘。

理由：observe / compare 已经把结果固化为 pandas；transform v1 的 op 都能用 pandas 表达，且不会引入新的 backend 路径或额外的 ibis 依赖。

## 4. Public API

```python
def transform(
    frame: MetricFrame | DeltaFrame,
    *,
    op: Literal[
        "filter", "slice", "rollup",
        "topk", "bottomk", "rank",
        "normalize", "window",
    ],
    session: Session | None = None,
    # op-specific kwargs:
    where: dict[DimensionRef | str, Any] | None = None,
    predicate: Callable[[pd.DataFrame], pd.Series] | None = None,
    drop_axes: list[DimensionRef | Literal["time"]] | None = None,
    by: str | None = None,
    limit: int | None = None,
    direction: Literal["increase", "decrease"] | None = None,
    method: Literal["dense", "ordinal", "min", "max"] = "ordinal",
    rank_column: str = "rank",
    kind: Literal["index", "share", "pct_change", "per_unit", "z_score"] | None = None,
    base: dict[str, Any] | None = None,
    window: WindowInput = None,
) -> MetricFrame | DeltaFrame: ...
```

返回与输入相同 family 的新 frame。Shape（`semantic_kind`）可在 family 内变化（例如 `panel → time_series`）。

Session 解析逻辑与 `observe` 一致：

```python
if session is None:
    session = session_active()
ensure_session_writable(session)
if frame.meta.session_id != session.id:
    raise CrossSessionFrameError(...)
```

## 5. Op×family 兼容矩阵

| op | metric_frame | delta_frame | 备注 |
| --- | --- | --- | --- |
| filter | ✓ | ✓ | 谓词作用在所有列上 |
| slice | ✓ | ✓ | 必须命中现有 `meta.axes` 的 dim 列 |
| rollup | ✓ | ✓ | `drop_axes` 必须是 axes 子集 |
| topk / bottomk | ✓ | ✓ | `by` 必须是已有列 |
| rank | ✓ | ✓ | 追加 `rank_column` 列 |
| normalize | `index`/`share`/`pct_change`/`per_unit`/`z_score` | ✗ | DeltaFrame normalize v1 fail-closed，直到能同时保持 `current` / `baseline` / `delta` / `pct_change` 不变量 |
| window | 需要 `meta.axes["time"]` | 需要 `meta.axes["time"]` | 走现有 `windows.resolver` |

矩阵在 dispatcher 入口校验，未命中直接 raise，不进入 op 实现。

## 6. Op 语义详细规则

### 6.1 filter

- 必填：`predicate(df) -> pd.Series[bool]`。
- 不接受 `where`（那是 slice）。
- 实现：`new_df = df[predicate(df)]`。
- `axes` 不变；`row_count` 更新。

### 6.2 slice

- 必填：`where: dict[DimensionRef | str, Any | list | tuple[lo, hi]]`。
- `DimensionRef` 解析为其 `id`，要求 id 在 `frame.meta.axes` 中且 `role="dimension"`。
- 字符串 key 必须等于 `axes[*].column`。
- value 形态：
  - 标量 → 等值过滤
  - `list` / `tuple` → `isin`
  - `(lo, hi)` 形式如果是数值/时间 → `between(lo, hi, inclusive="both")`
- Shape 自动重算：若某 dim 被锁定为单值且该 dim 是仅剩的非 time 轴 → `panel → time_series` 或 `segmented → scalar`。

### 6.3 rollup

- 必填：`drop_axes: list[DimensionRef | "time"]`，必须是当前 axes 的真子集。
- 实现：保留的轴 group by + measure 列 sum。
- 不接受全部 axes 都被 drop 的请求；那等价于 `metric_frame[scalar]` 的总和，v1 走 follow-up（更可能用 projection / observe scalar 表达）。
- Shape 重算：根据剩余 axes 推导新的 `semantic_kind`。
- v1 聚合函数固定为 `sum`。其他聚合（mean、p95）待 metric IR 暴露 aggregation 元数据后再扩展。

### 6.4 topk / bottomk

- 必填：`by: str`（已有列名），`limit: int > 0`。
- 可选：`direction`（仅对 delta 提供 ergonomic alias，`direction="decrease"` 等价 bottomk）。
- 实现：`df.sort_values(by, ascending=<dir>).head(limit)`。
- `axes` / `semantic_kind` 不变；`row_count` 更新。

### 6.5 rank

- 必填：`by: str`。
- 可选：`method`（默认 `ordinal`），`rank_column`（默认 `"rank"`）。
- 追加 `rank_column` 整数列；`axes` 不变。

### 6.6 normalize

- 必填：`kind`。
- 可选：`base: dict[str, Any]` 用于声明基线（如 `{"bucket_start": "2025-01-01"}`）。

按 `kind` 的实现：

| kind | 公式 | 输出列 | 备注 |
| --- | --- | --- | --- |
| `index` | `m / base_value * 100` | 替换 measure 列 | 默认 base 为时间序列首点 |
| `share` | `m / sum(m within group)` | 替换 measure 列 | metric_frame only；group 由 axes 决定 |
| `pct_change` | `m.pct_change()` | 替换 measure 列 | metric_frame only；沿 time axis，缺 time axis 时 fail-closed |
| `per_unit` | `m / base_value` | 替换 measure 列 | metric_frame only；base 必填 |
| `z_score` | `(m − μ) / σ` | 替换 measure 列 | μ、σ 在每个 group 内独立计算 |

DeltaFrame normalize 在 v1 中整体拒绝；不能只改写 `delta`，否则会让 `current` / `baseline` / `pct_change` 与 `delta` 失去同族列一致性。

新增可选 meta 字段 `normalization: dict | None = None`，记录 `{kind, base, columns_affected}`。该字段加在 `MetricFrameMeta` 与 `DeltaFrameMeta` 上（带默认值，向后兼容已有 frame 读盘）。

### 6.7 window

- 必填：`window: WindowInput`（`AbsoluteWindow` / `RelativeWindow` / dict 形式，复用 `normalize_window_input`）。
- 要求 `meta.axes["time"]` 存在；否则 `TransformShapeUnsupportedError`。
- 使用现有 `coerce_as_of` + `resolve_to_absolute` 解析到绝对窗口，按 session tz 处理。
- 按 `axes["time"].column` 做 `[start, end)` 过滤。
- `meta.window` 更新为新窗口的 dump；不要清空原 `original_window`，把它放进 `meta.window["chained_from"]` 以便 lineage 追溯。

## 7. Frame meta 变更

`MetricFrameMeta` 与 `DeltaFrameMeta` 增加一个可选字段：

```python
normalization: dict[str, Any] | None = None
```

其他元字段（`metric_id`、`semantic_model`、`source_a_ref`、`source_b_ref`、`alignment`）原样透传到新 frame。

Axes 与 `semantic_kind` 由 `_recompute_axes` 集中重算：

```python
def _recompute_axes(axes: dict, drop: set[str]) -> tuple[dict, SemanticKind]:
    remaining = {k: v for k, v in axes.items() if k not in drop}
    has_time = "time" in remaining
    has_dim = any(v.get("role") == "dimension" for v in remaining.values())
    if has_time and has_dim: return remaining, "panel"
    if has_time:             return remaining, "time_series"
    if has_dim:              return remaining, "segmented"
    return remaining, "scalar"
```

## 8. Lineage 与 job record

新增一条 `LineageStep`：

```python
LineageStep(
    intent="transform",
    job_ref=new_job_ref,
    inputs=[parent.ref],
    params_digest=_params_digest({"op": op, **op_params_for_digest}),
)
```

新 frame 的 `meta.lineage`：

```python
Lineage(
    steps=[*parent.lineage.steps, new_step],
    external_inputs=parent.lineage.external_inputs,
)
```

Job record 与 observe 同构：

```python
{
    "id": job_ref,
    "session_id": session.id,
    "intent": "transform",
    "params": {"op": op, ...normalized_kwargs},
    "input_frame_refs": [parent.ref],
    "output_frame_ref": new_ref,
    "started_at": ..., "finished_at": ..., "duration_ms": ...,
    "status": "succeeded" | "failed",
    "error": None | structured_error,
    "semantic_project_root": session.semantic_project.root,
    "semantic_model": parent.meta.semantic_model,
}
```

`params_digest` 序列化策略：

- `DimensionRef` → `{"kind": "DimensionRef", "id": ref.id}`
- `WindowInput` → `dump_window(resolved)`
- callable predicate → `{"kind": "callable", "repr": repr(callable)}`（不试图哈希函数体，仅用于 lineage 显示，不当缓存键）
- 其他 → `json.dumps(..., default=str)`

## 9. 错误类型

新增（追加到 `marivo/analysis_py/errors.py`，全部继承 `AnalysisError` 模板）：

| 错误 | 触发条件 | hint |
| --- | --- | --- |
| `TransformOpUnsupportedError` | `op` 不在 dispatch table；或 op 对当前 family 不合法（含 attribution_frame） | 列出支持的 op |
| `TransformShapeUnsupportedError` | `window` 在无时间轴的 frame；`rollup` drop 全部 axes | 指出该 op 需要的 axes |
| `TransformArgError` | 缺 `by` / `limit` ≤ 0 / unknown 列 / `kind` 与 family 不兼容 | 给出最小可用调用片段 |
| `TransformDimensionNotFoundError` | `where` / `drop_axes` 指向不在 axes 的 dim | 列出当前 axes |

`CrossSessionFrameError` 已存在，直接复用。

## 10. 测试

新文件 `tests/test_analysis_py_transform.py`，复用 `marivo-test-fixtures` 的 session-scoped DuckDB 模板。覆盖：

- 每个 op 至少一个 happy path（含 metric_frame 与 delta_frame 各一例代表）
- shape transition：`slice` 锁定单值 → 降级；`rollup` 去掉 time → segmented；`rollup` 去掉 dim → time_series
- normalize 五种 kind（`share` / `per_unit` 仅在 metric_frame 上）
- window 在 time_series 上裁剪
- 错误路径：未知 op、op×family 不兼容、缺 `by`、`limit ≤ 0`、未知 dim、time 轴缺失下的 window、跨 session 复用
- Lineage 与 job record 持久化：parent ref 出现在 `input_frame_refs`，`lineage.steps` 末尾是 `intent="transform"`
- Frame immutability 在 transform 输出上仍然生效

测试通过 `make test TESTS='tests/test_analysis_py_transform.py'` 单独执行验证。

## 11. Skill examples

新增（在 `marivo-skill/marivo-py-analysis/references/examples/`）：

- `transform_slice.py` — metric_frame slice by `DimensionRef`
- `transform_rollup_panel.py` — panel → time_series
- `transform_topk_delta.py` — delta_frame 取 top N 下降
- `transform_normalize_share.py` — segmented frame 计算 share
- `transform_window.py` — time_series 裁剪到子窗口

`make examples-check` 会自动校验。`SKILL.md` 追加一个简短段落与算子目录中 `transform` 的一行说明，保持在 600 行以内。

## 12. 不做的事

- 不实现 `align_time`、`dedupe`、`impute_nulls`、`winsorize`、`strip_outliers`。
- 不接受 `attribution_frame` 输入。
- 不引入 session 方法包装（`session.transform(...)`）；保持与现有 intent 入口一致。
- 不引入 ibis re-execution 路径。
- 不引入 batch / lazy plan。
- 不修改 `python-analysis-operator-design.md`；该文档已经描述了 transform，本次实现是其落地子集。

## 13. 后续

后续 PR：

1. cleaning ops（dedupe / impute_nulls / winsorize / strip_outliers）+ cleaning policy lineage 字段
2. `align_time` + 完整 `AlignmentPolicy` 落地
3. `attribution_frame` 上的兼容 op 子集
4. rollup 的非 sum 聚合（依赖 metric IR aggregation 元数据）
