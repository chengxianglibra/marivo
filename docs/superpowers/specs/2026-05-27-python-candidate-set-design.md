# Python `candidate_set` 解析与下游衔接设计

状态：draft design。

本文设计 `marivo.analysis_py` 中 `candidate_set` 的目标态实现：扩展 `mv.discover` 覆盖
spec 中的 6 个 objective、把 `CandidateSet` 的 item schema 落到 union-of-columns、引入
`mv.select` 作为 typed read 出口，并把 `recommended_followups` / `blocking_issues` /
`confidence_scope` 升级为 typed pydantic 模型。

设计依据：[`docs/specs/analysis/python-analysis-operator-design.md`](../../specs/analysis/python-analysis-operator-design.md)。本设计不考虑兼容/迁移。

## 1. 模块边界与新增表面

变更只落在 `marivo.analysis_py`，公开面新增一个算子 `mv.select` 与一组 typed pydantic
模型；`mv.discover` 扩展 source / objective / strategy 但保持单入口。session、persistence、
frame 注册不引入新概念，按现有模式扩列。

新增 / 暴露的 public 符号：

```text
mv.select(candidate_set, *, rank=..., field=...) -> typed value (read method)
mv.FollowupAction, mv.BlockingIssue, mv.ConfidenceScope (Pydantic, frozen=True, extra=forbid)
mv.CandidateShape (Literal alias)
CandidateSet.as_point_anomaly()
CandidateSet.as_period_shift()
CandidateSet.as_driver_axis()
CandidateSet.as_slice()
CandidateSet.as_window()
CandidateSet.as_cross_sectional_outlier()
```

`mv.discover` 签名扩展为接受 `MetricFrame | DeltaFrame`，内部按
`(objective, source-kind, semantic_kind)` 派发。`as_*()` 与 `select()` 都是 read 方法 —
不写盘、不创建 job、不进入 lineage。

不动：`MetricFrame` / `DeltaFrame` / `AttributionFrame` 的 meta 与 columns；现有
`decompose` / `transform` / `assess_quality` / `observe` / `compare` 签名；session
持久化布局；`_FRAME_CLASSES` 仍只有一行 `candidate_set`。

`AssociationResult` / `HypothesisTestResult` / `QualityReport` / `ForecastFrame` 上的
`recommended_followups: list[dict[str, Any]]` 在本轮保持现状；它们升级到 typed
`FollowupAction` / `BlockingIssue` 是后续 PR 的范围。

## 2. CandidateSet 数据模型

`CandidateSet` 仍是同一个 dataclass。差别在 `_df` 列布局采用 union-of-columns，以及
`CandidateSetMeta` 的字段补齐与 typed 化。

### 2.1 `CandidateSetMeta` 字段

```python
class CandidateSetMeta(BaseFrameMeta):
    kind: Literal["candidate_set"] = "candidate_set"

    # shape gate — 由 discover 设置，artifact 内不可变
    shape: Literal[
        "point_anomaly",
        "period_shift",
        "driver_axis",
        "slice",
        "window",
        "cross_sectional_outlier",
    ]
    objective: Literal[
        "point_anomalies",
        "period_shifts",
        "driver_axes",
        "interesting_slices",
        "interesting_windows",
        "cross_sectional_outliers",
    ]
    strategy: Literal[
        "zscore",                # point_anomalies (default)
        "delta_window_zscore",   # period_shifts
        "variance_explained",    # driver_axes
        "delta_magnitude",       # interesting_slices
        "rolling_zscore",        # interesting_windows
        "mad",                   # cross_sectional_outliers
    ]

    # provenance & semantic context
    source_ref: str
    source_kind: Literal["metric_frame", "delta_frame"]
    metric_ids: list[str]
    semantic_kind: Literal[
        "scalar", "time_series", "segmented", "panel",
        "scalar_delta", "time_series_delta",
        "segmented_delta", "panel_delta",
    ]
    semantic_model: str

    # spec § "Result artifact follow-up contract"
    source_refs: list[str]
    recommended_followups: list[FollowupAction] = []
    blocking_issues: list[BlockingIssue] = []
    confidence_scope: ConfidenceScope | None = None

    params: dict[str, Any]
```

`shape` 是 closed enum，由 `discover` 在创建时写入并持久化；`as_<shape>()` 校验它，
`select` 用它派发字段解析。

### 2.2 `_df` 列布局：union-of-columns

固定列顺序，每一列要么是该 shape 必填、要么对该 shape 不适用并填 NA：

```text
item_id              string                  required (all shapes)
score                float64                 required (all shapes)
direction            string                  required for point_anomaly,
                                              period_shift, cross_sectional_outlier;
                                              NA for others
reason_codes_json    string                  JSON array of strings; "[]" if none
source_refs_json     string                  JSON array of provenance refs
selector_json        string                  JSON object; key=dim id, value=scalar
keys_json            string                  JSON object; same encoding as
                                              selector_json but interpreted as
                                              item-identifying keys
window_start         datetime64[ns, UTC]     NaT if not applicable
window_end           datetime64[ns, UTC]     NaT if not applicable
baseline_window_start datetime64[ns, UTC]    NaT if not applicable
baseline_window_end   datetime64[ns, UTC]    NaT if not applicable
axis                 string                  NA if not applicable
peer_scope_json      string                  "" if not applicable
followups_json       string                  "[]" if none — per-item typed list
```

JSON 列存储沿用现 `discover.py` 的 `keys_json` 模式（`reason_codes` / `source_refs` /
`selector` / `keys` / `peer_scope` / per-item `followups` 是 list 或 map，parquet
不天然存）；`window` / `baseline_window` 用两列 datetime（已有 `AbsoluteWindow.start/end`）；
`axis` 是单字符串列，直接对应 `DimensionRef.id`。

### 2.3 各 shape 必填列矩阵

| Shape | 必填列（在公共列 `item_id` / `score` / `reason_codes_json` / `source_refs_json` / `followups_json` 之外）|
|---|---|
| `point_anomaly` | `window_start` / `window_end`, `direction` |
| `period_shift` | `window_start` / `window_end`, `baseline_window_start` / `baseline_window_end`, `direction` |
| `driver_axis` | `axis` |
| `slice` | `selector_json`, `keys_json` |
| `window` | `window_start` / `window_end` |
| `cross_sectional_outlier` | `keys_json`, `direction`（`peer_scope_json` 可选）|

`discover` 在写盘前调用 `_validate_shape_columns(shape, df)` 强校验：必填列非空、不必填
列填 NA / `""` / `"[]"`。

### 2.4 内部读路径

`select` 与 `as_*()` 的取值实现：

```python
item = candidate_set._df.iloc[rank - 1]
selector = json.loads(item["selector_json"])  # {"country": "US", ...}
keys = json.loads(item["keys_json"])          # {"country": "US"}
window = AbsoluteWindow(start=item["window_start"], end=item["window_end"], ...)
axis = DimensionRef(item["axis"])
```

这些是内部解析逻辑，不暴露给调用方。

### 2.5 `load_frame` / `_FRAME_CLASSES`

不变。`candidate_set` 仍是单一类；新增列由 `read_frame_from_disk` 自动 round-trip
（不考虑迁移）。`CandidateSetMeta` 借助 pydantic 在 `meta_cls(**meta)` 时自动把
`recommended_followups` / `blocking_issues` 反序列化为 typed list。

## 3. `discover` 派发与每 objective 算法

### 3.1 入口与签名

```python
def discover(
    source: MetricFrame | DeltaFrame,
    *,
    objective: CandidateObjective,
    strategy: CandidateStrategy | None = None,
    value: str | None = None,
    threshold: float | None = None,           # objective-specific 默认
    sensitivity: Literal["strict", "balanced", "loose"] = "balanced",
    limit: int | None = None,
    search_space: list[DimensionRef] | None = None,    # driver_axes / interesting_slices
    peer_scope: list[DimensionRef] | None = None,      # cross_sectional_outliers
    session: Session | None = None,
) -> CandidateSet: ...
```

### 3.2 派发表

`(objective, source_kind, semantic_kind)` 三元组到内部 scorer：

| objective | source_kind | semantic_kind | scorer | shape |
|---|---|---|---|---|
| `point_anomalies` | `metric_frame` | `time_series` / `panel` | `_score_point_anomalies` | `point_anomaly` |
| `period_shifts` | `delta_frame` | `time_series_delta` / `panel_delta` | `_score_period_shifts` | `period_shift` |
| `driver_axes` | `delta_frame` | 任意 | `_score_driver_axes` | `driver_axis` |
| `interesting_slices` | `metric_frame` / `delta_frame` | 任意 | `_score_interesting_slices` | `slice` |
| `interesting_windows` | `metric_frame` | `time_series` / `panel` | `_score_interesting_windows` | `window` |
| `interesting_windows` | `delta_frame` | `time_series_delta` / `panel_delta` | `_score_interesting_windows` | `window` |
| `cross_sectional_outliers` | `metric_frame` | `segmented` / `panel` | `_score_cross_sectional_outliers` | `cross_sectional_outlier` |

非法组合一律 `SemanticKindMismatchError`，沿现有错误模板。`strategy=None` 时填该 objective
的默认值；显式传非默认值在本次实现里直接报 `unsupported strategy`（保留 enum，不实现）。

### 3.3 各 objective 默认 strategy 与算法骨架

所有 scorer 用纯 pandas / numpy 实现，不引入 `scipy`。统一在 `to_pandas()` 上算，输出
union-of-columns 行，调 `_validate_shape_columns` 后 `write_frame_to_disk`。

**`point_anomalies` / `zscore`**

不变（已实现）。补：

- 算 `window_start` / `window_end` 时取 `metric_frame` 中 time bucket 列。
- `direction` 沿用现有 high / low。
- `source_refs_json = [source.ref + "#row=" + i]`。

**`period_shifts` / `delta_window_zscore`**

- 输入 `DeltaFrame[time_series_delta | panel_delta]`，按 `bucket_column` 滚动窗口
  （`window_size = max(7, len // 10)`），对 `delta` 列计算窗口均值的 z-score。
- `|z| ≥ threshold(default=2.0)` 的窗口算一个候选；连续命中合并成一段（左闭右闭）。
- `window` = 命中段的起止 bucket；`baseline_window` = 该段之前同长度窗口；`direction`
  由段均值符号决定。
- panel：按 `_panel_dimension_columns` group，每组独立扫，`keys_json` 记录该组的
  dimension 取值。

**`driver_axes` / `variance_explained`**

- 输入 `DeltaFrame[*]`，必须 `search_space` 非空。
- 对 search_space 里每个 `DimensionRef`：按 axis groupby `delta`，contributions 排序求
  cumulative `|delta|` / total `|delta|`；用 “前 K 个 axis 值贡献占比超过 50%” 时的 K
  作为可解释性度量（K 越小越好），评分 = `1 / (K + axis_cardinality / 1000)`，从而
  优先排 K 小、再次排 cardinality 小的 axis。
- 排名前 N（min(len(search_space), limit or len)）个 axis 写出，每行一个
  `axis = DimensionRef.id`，`reason_codes` 含 `"top_k_share=…"` / `"axis_cardinality=…"`。
- panel / time_series_delta 输入：先按时间 sum 一次再算（spec § "decompose" 同思路）。

**`interesting_slices` / `delta_magnitude`**

- 输入 metric 或 delta；衡量单位：metric 用 `|value - mean| / std`，delta 用 `|delta|`。
- 候选 = 每个非空 `(axis subset, value)` 组合中得分超过 threshold 的；
  `selector_json = {axis_id: value, ...}`，`keys_json` 同。
- `search_space` 限定哪些 axes 参与；缺省取 frame 已有 dimensions，最多 2 维组合，避免
  fanout 爆炸。
- `direction` 留 NA。

**`interesting_windows` / `rolling_zscore`**

- 输入 `MetricFrame[time_series | panel]` 或
  `DeltaFrame[time_series_delta | panel_delta]`。
- 滚动窗口对值列做 z-score；连续命中合并成段；段输出 `window_start` / `window_end`，
  `score = max|z|` within segment。
- 与 `period_shifts` 区别：`period_shifts` 必须在 delta 上找“周期级别变化”；
  `interesting_windows` 在 metric 或 delta 上找“值得复看的时间段”，没有
  `baseline_window`，`direction` 取段均值符号或 NA。

**`cross_sectional_outliers` / `mad`**

- 输入 `MetricFrame[segmented | panel]`；用 median + MAD 算每个 segment 的稳健 z-score。
- panel 按 bucket 分组算（每个 bucket 内做截面）；segmented 直接做。
- `peer_scope` = 限定参与对比的 dimension subset；`peer_scope_json` 写入。
- 候选行：`keys_json = segment 的 dimension 取值`，`direction = "high" / "low"`。
- MAD 自实现：`np.median` / `np.median(abs(x - med))`。

### 3.4 共享 driver code

替换现有 `discover.py` 尾部的 driver code：

```text
1. resolve_session / ensure_session_writable / ensure_frame_in_session
2. _check_dispatch(objective, source) -> (shape, scorer, strategy_used)
3. df, item_meta = scorer(source, params)        # item_meta 含 reason_codes 等
4. rows = _build_union_columns(shape, df, item_meta)
5. _validate_shape_columns(shape, rows)
6. compose meta (含 shape, source_kind, semantic_kind, source_refs, params)
7. persist + job record + return CandidateSet
```

`recommended_followups` / `blocking_issues` / `confidence_scope` 在 §2 的字段都默认空
list / None — scorer 不主动填 followups。但 scorer 可在 `reason_codes_json` 里写机器
可读 token；`select` / agent 后续可以基于此填 followup。

## 4. `mv.select` 与下游消费

### 4.1 函数签名

```python
def select(
    candidate_set: CandidateSet,
    *,
    rank: int = 1,
    field: Literal[
        "axis", "selector", "window", "baseline_window",
        "direction", "score", "item_id",
    ] | str,    # 也接受 "keys.<dim>" / "selector.<dim>" 形式
) -> Any: ...
```

`select` 是 read 方法。不写盘、不创建 job、不进入 lineage。`rank` 1-indexed；越界抛
`SemanticKindMismatchError`。

同名也实现为 `CandidateSet.projection().select(...)`，二者共用同一份解析函数。

### 4.2 字段返回类型矩阵

| shape | `axis` | `selector` | `window` | `baseline_window` | `direction` | `keys.<k>` / `selector.<k>` |
|---|---|---|---|---|---|---|
| `point_anomaly` | ✗ | ✗ | `AbsoluteWindow` | ✗ | `str` | `selector` 不可用；`keys.<k>` 标量（panel）|
| `period_shift` | ✗ | ✗ | `AbsoluteWindow` | `AbsoluteWindow` | `str` | 同上 |
| `driver_axis` | `DimensionRef` | ✗ | ✗ | ✗ | ✗ | ✗ |
| `slice` | ✗ | `dict[str, Any]` | optional | ✗ | optional | `selector.<k>` / `keys.<k>` 标量 |
| `window` | ✗ | ✗ | `AbsoluteWindow` | ✗ | optional | optional |
| `cross_sectional_outlier` | ✗ | ✗ | ✗ | ✗ | `str` | `keys.<k>` 标量 |

✗ 表示该 field 在该 shape 上无意义，`select` 抛 `SemanticKindMismatchError`，`details`
包含 `shape` 与 `field`。

`keys.<k>` 与 `selector.<k>` 的点路径只解一层，标量返回。不存在的 key 抛错而非返回
`None`，避免 silent miss。

### 4.3 与下游算子的衔接

不改 `decompose` / `transform` / `observe` / `compare` 的签名，靠 select 的返回类型直接
喂：

```python
# spec § "不知道沿哪个轴看"
delta = session.compare(current, baseline)
axis_candidates = session.discover(
    delta,
    objective="driver_axes",
    search_space=[DimensionRef("country"), DimensionRef("platform")],
)
selected_axis = mv.select(axis_candidates, rank=1, field="axis")   # -> DimensionRef
drivers = session.decompose(delta, axis=selected_axis)             # 现有签名直收

# spec § "找异常并复查"
anomalies = session.discover(series, objective="point_anomalies")
window = mv.select(anomalies, rank=1, field="window")              # -> AbsoluteWindow
local = session.transform(series, op="window", window=window)      # 现有签名直收

# spec § "分层 drilldown"
country_attr = session.decompose(delta, axis=DimensionRef("country"))
top_country = mv.select(country_attr, rank=1, field="keys.country")  # 见 §4.4
country_delta = session.transform(
    delta, op="slice", where={DimensionRef("country"): top_country}
)

# slice candidate -> transform(slice)
slice_cands = session.discover(
    delta,
    objective="interesting_slices",
    search_space=[DimensionRef("country"), DimensionRef("platform")],
)
selector = mv.select(slice_cands, rank=1, field="selector")        # -> dict[str, Any]
focus = session.transform(delta, op="slice", where=selector)
```

`transform(op="slice", where=...)` 已经接受 `dict[str, Any]`（`transform.py:1600` 处
selector 用法），让 `select(field="selector")` 直接返回该 dict 形式即可，零改动。

### 4.4 `AttributionFrame` 的 selector 路径

上面 drilldown 例子中的 `mv.select(country_attr, …)` 是 spec § "组合方式" 的写法，但
`AttributionFrame` 不是 `CandidateSet`。本次实现 `select` 严格只支持 `CandidateSet`；
`AttributionFrame` 上的 select 推广是后续 PR 范围。

drilldown 在本轮实现里仍能工作：agent 用 `country_attr.to_pandas().iloc[0][axis]` 取
top key（已可行），或用 `interesting_slices` 候选直接绕过 attribution 取 selector。

### 4.5 `as_<shape>()` accessor

```python
def as_driver_axis(self) -> CandidateSet:
    if self.meta.shape != "driver_axis":
        raise SemanticKindMismatchError(
            message="CandidateSet shape mismatch",
            details={"got_shape": self.meta.shape, "expected_shape": "driver_axis"},
        )
    return self
```

与 `select` 互补：`select` 取一行的某字段；`as_*()` 是整 frame 的 shape 断言（spec §
"Typed shape narrowing"）。返回 `self`，不复制；调用方拿到的是已经 narrow 过的引用。
6 个方法都是单行实现。

## 5. Typed pydantic 模型与错误模型

### 5.1 `FollowupAction` / `BlockingIssue` / `ConfidenceScope`

放在新文件 `marivo/analysis_py/followups.py`（与 `policies.py` 同级），从 `__init__.py`
导出。所有模型 `frozen=True, extra="forbid"`，与 `_RefBase` 一致：

```python
class FollowupAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action_id: str
    kind: Literal["submit_step", "open_projection",
                  "adjust_policy", "request_semantic_input"]
    operator: str | None = None                # core/composite operator id
    input_refs: list[str] = []                 # artifact / candidate / source refs
    params: dict[str, Any] = {}                # 已解析 typed params；本次保持 dict
    preconditions: list[str] = []
    expected_output_family: Literal[
        "metric_frame", "delta_frame", "attribution_frame",
        "candidate_set", "association_result", "hypothesis_test_result",
        "forecast_frame", "forecast_evaluation_result",
        "quality_report", "diagnosis_result",
    ] | None = None


class BlockingIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    issue_id: str
    kind: Literal["quality", "sample_size", "comparability",
                  "definition_drift", "missing_semantic_ref",
                  "cost", "permission"]
    severity: Literal["warning", "blocking"]
    source_refs: list[str] = []
    message: str
    remediation_followups: list[FollowupAction] = []


class ConfidenceScope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    metric_ids: list[str] = []
    segment_keys: dict[str, Any] = {}
    window: dict[str, Any] | None = None        # AbsoluteWindow.model_dump(mode="json")
    assumptions: list[str] = []
```

`params` 在本轮保持 `dict[str, Any]` — spec 要求 typed 但目前没有跨 operator 的 typed
params 注册表；强 typed params 是单独的 PR。`expected_output_family` 用 closed enum
已经满足 spec 关键约束。

### 5.2 `CandidateSetMeta` 的 typed 字段

```python
class CandidateSetMeta(BaseFrameMeta):
    ...
    recommended_followups: list[FollowupAction] = []
    blocking_issues: list[BlockingIssue] = []
    confidence_scope: ConfidenceScope | None = None
```

pydantic 自动 round-trip — `read_frame_from_disk` 已用 `meta_cls(**meta)`
（`_load.py:69`），dict → typed model 在 `model_validate` 时直接发生，写盘则经
`model_dump(mode="json")`。

### 5.3 错误模型

不引入新错误类型；复用 `SemanticKindMismatchError` + `details` 模板。新增失败路径：

| 场景 | 抛错 | details 关键字段 |
|---|---|---|
| 未知 objective | `SemanticKindMismatchError` | `expected_kind`, `got_kind` |
| objective 与 source kind / shape 不匹配 | `SemanticKindMismatchError` | `objective`, `source_kind`, `semantic_kind`, `expected_kind` |
| objective 默认 strategy 之外的 strategy | `SemanticKindMismatchError` | `expected_kind`, `got_kind` |
| `driver_axes` 缺 `search_space` | `SemanticKindMismatchError` | `objective`, `missing`: `"search_space"` |
| `select(rank=...)` 越界 | `SemanticKindMismatchError` | `row_count`, `requested_rank` |
| `select(field=...)` 与 shape 不兼容 | `SemanticKindMismatchError` | `shape`, `field` |
| `as_driver_axis()` 与 shape 不匹配 | `SemanticKindMismatchError` | `got_shape`, `expected_shape` |
| `_validate_shape_columns` 必填列空 / 不必填列非空 | `FrameMetaInvalidError` | `shape`, `column`, `reason` |

错误模板字段（`fix_snippet` / `doc`）按 spec 在 `errors.py` 增补到现有
`SemanticKindMismatchError._template_fields()` 里 — 沿用已有
`expected_kind == "MetricRef"` / `expected_kind == "metric_frame"` 的分支风格。

`_validate_shape_columns` 用 `FrameMetaInvalidError` 因为它是 invariant 检查，不是
用户输入错误。

### 5.4 与现有 Frame 不变性

`CandidateSet` 仍继承 `BaseFrame`：`to_pandas()` 返回 copy、`__setitem__` 抛
`FrameMutationError`、`select` 不修改 `_df`。spec § "Frame immutability is a public
contract" 不放松。

## 6. 测试矩阵、skill 示例、文档更新

### 6.1 测试组织

新增三个测试文件，沿用现有 `tests/test_analysis_py_*.py` 风格（fixtures、
`session_attach._reset_process_state()`、tmp_path）：

```text
tests/test_analysis_py_discover_objectives.py    # §3 dispatch + 6 个 scorer
tests/test_analysis_py_candidate_select.py       # §4 select / as_*
tests/test_analysis_py_candidate_followups.py    # §5 typed 模型 round-trip
```

现有 `tests/test_analysis_py_discover.py` 保留，专责 `point_anomalies` × `zscore` 的
回归。

### 6.2 `test_analysis_py_discover_objectives.py` 矩阵

每个 objective 一个固定输入 + 期望命中 fixture（构造小数据集，用 numpy 已知答案校验）：

| 用例 | 验证 |
|---|---|
| `point_anomalies` panel 输入 | 现有 + 多列 `direction` 与 `window_start/end` 写回 |
| `period_shifts` 命中 + 段合并 | `window` / `baseline_window` / `direction` 三列；连续命中合并成单行 |
| `period_shifts` 输入 `MetricFrame` 报错 | dispatch gate |
| `driver_axes` 排名稳定 | rank=1 的 axis 即 search_space 中绝对 sum 最大者 |
| `driver_axes` 缺 `search_space` 报错 | argument gate |
| `interesting_slices` selector dict 还原 | `selector_json` 反序列化等于 `{axis: value}` |
| `interesting_windows` metric vs delta 输入 | 两条入口都跑通；段合并 |
| `cross_sectional_outliers` panel / segmented | `peer_scope_json` 写回；`direction` high / low |
| 全 6 objective × 错误 source kind | 一律 `SemanticKindMismatchError` |
| 全 6 objective × 非默认 strategy | 一律 `SemanticKindMismatchError` |
| 持久化 round-trip | `mv.load_frame(out.ref)` 后 `meta.shape`、列布局完全一致 |
| `meta.recommended_followups` 默认空 list | typed 默认值 |

### 6.3 `test_analysis_py_candidate_select.py` 矩阵

| 用例 | 验证 |
|---|---|
| `select(driver_axes, field="axis")` | 返回 `DimensionRef`，`isinstance(..., DimensionRef)` |
| `select(point_anomalies, field="window")` | 返回 `AbsoluteWindow`，start / end 与底层列一致 |
| `select(period_shifts, field="baseline_window")` | 返回 `AbsoluteWindow` |
| `select(slice, field="selector")` | 返回 `dict[str, Any]`，可直接喂 `transform(op="slice", where=...)` |
| `select(slice, field="keys.country")` | 返回标量 |
| `select(point_anomalies, field="axis")` | 抛 `SemanticKindMismatchError`，details 含 `shape` 与 `field` |
| `select(rank=999)` 越界 | 抛 `SemanticKindMismatchError`，details 含 `row_count` / `requested_rank` |
| `select(field="keys.unknown")` | 抛 `SemanticKindMismatchError` |
| `as_driver_axis()` 正确 / 错误 shape | 通过 / `SemanticKindMismatchError` |
| `select` 不入 lineage、不写 job | `session.jobs()` 数量不变 |

### 6.4 `test_analysis_py_candidate_followups.py` 矩阵

| 用例 | 验证 |
|---|---|
| `FollowupAction(extra="reject")` | pydantic `extra="forbid"` 抛 `ValidationError` |
| `FollowupAction.model_dump(mode="json")` round-trip | typed → json → typed 一致 |
| `BlockingIssue.remediation_followups` 嵌套 | 双层 typed 反序列化 |
| `CandidateSetMeta` round-trip via `mv.load_frame` | 磁盘上是 list[dict]，读回是 list[FollowupAction] |
| `expected_output_family` 非法值 | 抛 `ValidationError` |

### 6.5 端到端 spec 组合用例

在 `tests/test_analysis_py_candidate_select.py` 加 4 个端到端串联测试，覆盖 spec §
"组合方式" 的几条主路径：

| 组合（spec 章节） | 测试名 |
|---|---|
| §组合 3 “不知道沿哪个轴看” | `test_select_axis_feeds_decompose` |
| §组合 4 “找异常并复查” | `test_select_window_feeds_transform_window` |
| §组合 5 slice candidate 接 transform(slice) | `test_select_selector_feeds_transform_slice` |
| §组合 5 drilldown 用 keys.* | `test_select_keys_dot_path_drilldown`（用 `interesting_slices` 候选取 selector，再喂 `transform(op="slice")`；不依赖 `AttributionFrame.select`，与 §4.4 一致）|

每条都跑到 “下游 frame 持久化、`artifact_id` 存在” 为止；不要求结果数值正确（数值由
各 objective 单测兜底）。

### 6.6 Skill 示例

`marivo-skill/marivo-py-analysis/` 是 spec 钉的可执行 SDK 契约
（`agent-guide.md` 写明 `make examples-check` 是 gate）。本轮触及的 public 符号必须同
步：

新增示例：

```text
marivo-skill/marivo-py-analysis/references/examples/
  08_discover_driver_axes.py
  09_discover_period_shifts.py
  10_discover_interesting_slices.py
  11_discover_cross_sectional.py
  12_select_window_drilldown.py
```

`04_detect_anomaly.py` 不动（仍是 `point_anomalies` × `zscore` 最小 demo）。

### 6.7 SKILL.md / cheatsheet / pitfalls 更新

- `SKILL.md` 中现写的 “discover 仅支持 `point_anomalies`” 改为 6 个 objective 的对照表；
  加入 `mv.select` 与 `as_<shape>()` 的最小说明；保持 600 行上限。
- `references/cheatsheet.md` 中 `mv.discover` 行扩展为按 objective 分行。
- `references/pitfalls.md` 新增条目：`select(field=...) shape 不匹配`、`driver_axes 缺
  search_space`、`select(rank=...) 越界`。

### 6.8 文档同步

按 `agent-guide.md` § "Documentation Updates"：

- `docs/specs/analysis/python-analysis-operator-design.md` 不改 — 它是 design 源，
  本设计正是依据它做的。
- `docs/specs/analysis/intents/discover.md`（若存在）按 spec 把 6 个 objective 的实现
  细节同步过去；本设计不擅自动它，待 plan 阶段确认。
- `marivo-skill/marivo-py-analysis/SKILL.md` 同 §6.7。

不动 frontend、HTTP API、MCP — 题目限定在 Python 库（spec § "No new transports in
regular PRs"）。

### 6.9 Make targets / lint / typecheck

按 `agent-guide.md`：`make typecheck` / `make lint` / `make test`。新代码全 typed
（`Literal` / pydantic / `DimensionRef` 等），不引入新 `cast(...)` 例外（除非沿用
`discover.py` 头部已有的 pandas `# mypy: disable-error-code=import-untyped` pattern）。

## 非目标

- 不把 typed `FollowupAction` 推广到 `AssociationResult` / `HypothesisTestResult` /
  `QualityReport` / `ForecastFrame`。
- 不把 `mv.select` 推广到 `AttributionFrame` / `MetricFrame` / `DeltaFrame` 上的 row
  selector。
- 不引入新的 `discover` strategy（仅每 objective 一个默认）。
- 不强 typed `FollowupAction.params` schema（本轮保持 `dict[str, Any]`）。
- 不动 frontend、HTTP、MCP 任何 transport。
- 不考虑现有 `point_anomalies` 候选行布局的迁移；按题目约束直接换列。
