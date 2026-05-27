# test / forecast / assess_quality 算子 v1 实现设计

状态：design。本文是 `marivo.analysis_py` 三个新 core operator 的首版实现设计，目标是把 [python-analysis-operator-design.md](../../specs/analysis/python-analysis-operator-design.md) 中的 `test` / `forecast` / `assess_quality` 落地到代码与测试。该文档不替代算子总体设计，只描述实现选择与边界。

## 1. 范围

v1 同批落地三个 core operator，均为最小可用子集：

- `test`：仅 `mean_changed` 假设；输入支持 `time_series` / `segmented` / `panel`；`scalar` fail-closed。
- `forecast`：模型 `naive` / `seasonal_naive` / `drift`；输入支持 `time_series` / `panel`。
- `assess_quality`：仅 `QualityReport[metric]`，其他 frame family v1.1+ 增量。

不在 v1 范围：

- 其他 hypothesis（`proportion_changed`、`distribution_changed`、`variance_changed` 等）。
- 其他 alignment kind（`dow_aligned`、`holiday_aligned`、`fiscal_period` 等）；v1 仅 `calendar_bucket`，与 `correlate` 现状对齐。
- `QualityReport[delta|candidate|forecast|attribution]`。
- ETS / ARIMA / Prophet 等需要外部库的 forecast 模型。
- `evaluate_forecast` composite operator（依赖 `ForecastFrame` 已存在，但属于 composite 层）。
- `FollowupAction` / `BlockingIssue` 的 pydantic 类型化（v1 用 plain dict 占位）。
- shape narrowing accessors（`as_metric()`、`as_candidate()` 等）。

## 2. 共享决策

### 2.1 依赖

新增 `scipy` 为 marivo 主依赖。用途：

- `scipy.stats.t.sf` / `t.ppf`：paired t-test p-value 与置信区间。
- `scipy.stats.norm.ppf`：forecast 区间分位点。

### 2.2 frame meta 通用扩展

设计文档 §"Result artifact follow-up contract" 要求所有 result 携带：

- `recommended_followups: list[dict]`
- `blocking_issues: list[dict]`

v1 在三个新 frame meta 上以 `list[dict[str, Any]]` 形式实现，schema 与设计文档 `FollowupAction` / `BlockingIssue` 字段名对齐；不引入 pydantic 类型，留待统一 typed migration。

### 2.3 共享 helpers

复用 `marivo/analysis_py/intents/_derived.py` 已有：

- `resolve_session`、`ensure_frame_in_session`、`gen_ref`、`params_digest`、`compose_lineage`、`require_numeric_column`。

不新增 helper 模块。`assess_quality` 的 check 实现单独放 `intents/_quality_checks.py`，因为它不走 backend、纯 DataFrame 巡检，风格与其他 intent 不同。

### 2.4 持久化

三个算子都沿用 `write_frame_to_disk` + `write_job_record`，job 记录字段与 `correlate` 等现有 intent 一致。

## 3. 三个新 frame family

### 3.1 `HypothesisTestResult` (`hypothesis_test_result`)

```python
class HypothesisTestResultMeta(BaseFrameMeta):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["hypothesis_test_result"] = "hypothesis_test_result"
    source_refs: list[str]                       # [current.ref, baseline.ref]
    metric_ids: list[str]
    semantic_kinds: list[Literal["scalar", "time_series", "segmented", "panel"]]
    semantic_models: list[str]
    hypothesis: Literal["mean_changed"]
    method: Literal["paired_t"]
    alignment: dict[str, Any]                    # AlignmentPolicy dump
    sampling: dict[str, Any]                     # SamplingPolicy dump
    alpha: float
    result_shape: Literal["single", "per_segment"]
    segment_dimensions: list[str]                # per_segment 时是 segment key 列名
    rejected_count: int
    not_enough_data_count: int
    recommended_followups: list[dict[str, Any]] = []
    blocking_issues: list[dict[str, Any]] = []
```

DataFrame 列（每行一个检验）：

```
test_statistic, p_value, df, sample_size,
mean_a, mean_b, mean_diff, ci_lower, ci_upper,
rejected, reason_code,
[segment_key_1, segment_key_2, ...]  # 仅 per_segment
```

`reason_code ∈ {"ok", "insufficient_pairs", "constant_diff"}`。

### 3.2 `ForecastFrame` (`forecast_frame`)

```python
class ForecastFrameMeta(BaseFrameMeta):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["forecast_frame"] = "forecast_frame"
    source_refs: list[str]                       # [history.ref]
    metric_id: str
    semantic_model: str
    semantic_kind: Literal["time_series", "panel"]
    measure: dict[str, Any]                      # 继承 history.meta.measure
    axes: dict[str, Any]                         # time + segment dims（panel）
    history_window: dict[str, Any]               # history.meta.window dump
    forecast_window: dict[str, Any]              # 推断的预测期 absolute window
    horizon: int
    horizon_unit: Literal["day", "week", "month", "quarter"]
    model: Literal["naive", "seasonal_naive", "drift"]
    seasonality_period: int | None
    interval_level: float
    interval_method: Literal["normal_residual"]
    train_row_count_per_segment: dict[str, int]  # ts: {"__all__": T}; panel: {key: T}
    segment_dimensions: list[str]
    recommended_followups: list[dict[str, Any]] = []
    blocking_issues: list[dict[str, Any]] = []
```

DataFrame 列：

```
time, predicted, lower, upper, residual_stddev, model, horizon_index, reason_code,
[segment_key_1, ...]  # 仅 panel
```

`reason_code ∈ {"ok", "insufficient_history", "constant_history"}`。

### 3.3 `QualityReport` (`quality_report`)

```python
class QualityReportMeta(BaseFrameMeta):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["quality_report"] = "quality_report"
    source_refs: list[str]                       # [target.ref]
    report_shape: Literal["metric"]              # v1.1+ 时扩展 Literal 值
    target_kind: Literal["metric_frame"]
    target_metric_id: str | None
    target_semantic_model: str | None
    target_semantic_kind: Literal["scalar", "time_series", "segmented", "panel"]
    checks_run: list[str]
    overall_status: Literal["ok", "warning", "blocking"]
    blocking_issue_count: int
    warning_count: int
    recommended_followups: list[dict[str, Any]] = []
    blocking_issues: list[dict[str, Any]] = []
```

DataFrame 列（long-format，每行一个 check 结果）：

```
check_id, check_kind, status, severity, message, details_json
```

- `check_kind ∈ {"row_count", "null_ratio", "time_coverage", "duplicate_keys"}`
- `status` 与 `severity` 同枚举 `{"ok", "warning", "blocking"}`
- `details_json` 用 `json.dumps(sort_keys=True)` 序列化，避免 pandas object dtype 漂移

## 4. `test` 算子

### 4.1 模块结构

新文件：

- `marivo/analysis_py/intents/test.py` — 内部函数名 `hypothesis_test`，对外通过 `intents/__init__.py` 与 `analysis_py/__init__.py` 以 `as test` 暴露。
- `marivo/analysis_py/frames/hypothesis.py` — `HypothesisTestResult` + `HypothesisTestResultMeta`。

函数源码取名 `hypothesis_test` 而非 `test`，避免 pytest collection 把任何顶层 `test_*` 模块符号误识为 test fixture / case。

### 4.2 `SamplingPolicy`

`marivo/analysis_py/policies.py` 追加：

```python
class SamplingPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    unit: Literal["bucket"] = "bucket"
    method: Literal["paired_numeric_summary"] = "paired_numeric_summary"
    pairing: Literal["calendar_bucket", "segment_key"] = "calendar_bucket"
    null_handling: Literal["drop_pair"] = "drop_pair"
    min_n: int = Field(default=3, ge=2)
```

字段全为单值 Literal（除 `pairing` 二选一与 `min_n` 数值），扩展靠新增 Literal 值。`pairing` 由 input shape 隐式决定，用户显式给出但与 shape 不匹配时报错。

### 4.3 函数签名

```python
def hypothesis_test(
    a: MetricFrame,
    b: MetricFrame,
    *,
    hypothesis: Literal["mean_changed"] = "mean_changed",
    value_a: str | None = None,
    value_b: str | None = None,
    alignment: AlignmentPolicy | None = None,
    sampling: SamplingPolicy | None = None,
    alpha: float = 0.05,
    session: Session | None = None,
) -> HypothesisTestResult:
```

- `alignment` 默认 `AlignmentPolicy(kind="calendar_bucket")`。v1 仅接受 `calendar_bucket`，与 `correlate` 现状对齐。
- `sampling` 默认 `SamplingPolicy()`。
- `alpha ∈ (0, 0.5]`，否则 `TestPolicyError`。
- 输入 `a`、`b` 必须同 `semantic_kind` 与 `semantic_model`。

### 4.4 Shape gate 与执行路径

`a.meta.semantic_kind == b.meta.semantic_kind` 是前置约束。

| Input shape | result_shape | pairing 单元 | 行为 |
|---|---|---|---|
| `scalar` | — | — | 抛 `TestShapeNotTestableError` |
| `time_series` | `single` | time bucket | 单行结果，paired t-test |
| `segmented` | `single` | segment key | 单行结果，paired t-test across segments |
| `panel` | `per_segment` | per-segment + time bucket | 每 segment 一行 |

`SamplingPolicy.pairing` 必须与 shape 一致：

- `time_series` / `panel` → `pairing="calendar_bucket"`
- `segmented` → `pairing="segment_key"`

不一致 → `TestPolicyError`。

### 4.5 paired t-test 公式

`d_i = a_i - b_i`（按 pairing 单元配对、应用 `null_handling="drop_pair"` 后）。

```
n = len(d)
mean_d = mean(d)
sd_d   = std(d, ddof=1)
t      = mean_d / (sd_d / sqrt(n))
df     = n - 1
p_two  = 2 * scipy.stats.t.sf(abs(t), df)
crit   = scipy.stats.t.ppf(1 - alpha/2, df)
ci_lo  = mean_d - crit * sd_d / sqrt(n)
ci_hi  = mean_d + crit * sd_d / sqrt(n)
rejected = p_two < alpha
```

边界：

- `n < min_n` → `reason_code="insufficient_pairs"`，统计字段 NaN，`rejected=False`。panel 时不抛错（行计入 `not_enough_data_count`），其他 shape 抛 `TestShapeNotTestableError`。
- `sd_d == 0`（所有配对差相等）→ `reason_code="constant_diff"`，统计字段 NaN，`rejected=False`。

### 4.6 panel 处理

按 `axes` segment dims 分组（与 `a`、`b` 一致）。每组独立 paired t-test。输出每个 segment 一行，segment key 列在 DataFrame 与 `meta.segment_dimensions` 中保留。

### 4.7 新错误

`marivo/analysis_py/errors.py` 追加：

| 错误类 | 触发 |
|---|---|
| `TestShapeNotTestableError` | scalar 输入；time_series/segmented 下 paired n < `min_n` |
| `TestPolicyError` | `SamplingPolicy.pairing` 与 input shape 不匹配；`alpha` 越界；`alignment.kind` 非 `calendar_bucket` |
| `TestAlignmentError(AlignmentFailedError)` | 对齐后无成对样本 |

每个新错误实现 `_template_fields()` 返回 `location/cause/fix_snippet/doc`，与 `MetricNotFoundError` 等现有错误一致。

### 4.8 Lineage

```python
LineageStep(
    intent="test",
    job_ref=job_ref,
    inputs=[a.ref, b.ref],
    params_digest=params_digest(params),
)

params = {
    "source_a_ref": a.ref,
    "source_b_ref": b.ref,
    "value_a": value_a_resolved,
    "value_b": value_b_resolved,
    "hypothesis": hypothesis,
    "method": "paired_t",
    "alignment": alignment.model_dump(mode="json"),
    "sampling": sampling.model_dump(mode="json"),
    "alpha": alpha,
    "result_shape": result_shape,
}
```

## 5. `forecast` 算子

### 5.1 模块结构

新文件：

- `marivo/analysis_py/intents/forecast.py` — 函数 `forecast`，按 `model` 分发到 naive / seasonal_naive / drift。
- `marivo/analysis_py/frames/forecast.py` — `ForecastFrame` + `ForecastFrameMeta`。

### 5.2 函数签名

```python
def forecast(
    history: MetricFrame,
    *,
    horizon: int,
    model: Literal["naive", "seasonal_naive", "drift"] = "seasonal_naive",
    seasonality_period: int | None = None,
    interval_level: float = 0.95,
    value: str | None = None,
    session: Session | None = None,
) -> ForecastFrame:
```

- `history.meta.semantic_kind ∈ {"time_series", "panel"}`，否则 `ForecastShapeUnsupportedError`。
- `horizon ≥ 1`，否则 `ForecastPolicyError`。
- `interval_level ∈ (0, 1)`，否则 `ForecastPolicyError`。
- `value` 行为同 `correlate`，通过 `require_numeric_column` 解析。

不引入 `ForecastPolicy` 包装；参数顶层暴露，与 `correlate` 风格一致。

### 5.3 时间轴与频率

从 `history.meta.axes["time"]` 取 time 列名与 grain。

频率映射：

| grain | pandas freq | default seasonality_period |
|---|---|---|
| `day` | `D` | 7 |
| `week` | `W-MON` | 52 |
| `month` | `MS` | 12 |
| `quarter` | `QS` | 4 |

`year` 与未列出的 grain → `ForecastShapeUnsupportedError`。

`forecast_window` = history 最后一个 bucket + 1 step 起，连续 `horizon` 个 bucket（pandas `date_range`，保留 history 的 timezone）。

### 5.4 三种模型

设 `y_1..y_T` 为按时间排序的历史值；`h ∈ 1..horizon`。

| Model | 预测 | residual_stddev |
|---|---|---|
| `naive` | `ŷ_{T+h} = y_T` | `std(y_t - y_{t-1}, ddof=1)` |
| `seasonal_naive` | `ŷ_{T+h} = y_{T - m + 1 + ((h - 1) mod m)}`（h > m 时按周期回绕；m = `seasonality_period`） | `std(y_t - y_{t-m}, ddof=1)` |
| `drift` | `ŷ_{T+h} = y_T + h × (y_T - y_1) / (T-1)` | `std(y_t - (y_1 + (t-1)(y_T-y_1)/(T-1)), ddof=1)` |

预测区间：

```
z = scipy.stats.norm.ppf((1 + interval_level) / 2)
lower = ŷ - z * residual_stddev * sqrt(h)
upper = ŷ + z * residual_stddev * sqrt(h)
```

`sqrt(h)` 项是 naive 随机游走标准做法；drift 同公式作为 v1 近似（精确公式含 trend 不确定性，留 v1.1+）。

### 5.5 最小数据要求

| Model | 最小 T |
|---|---|
| `naive` | 2 |
| `seasonal_naive` | `seasonality_period + 1` |
| `drift` | 3 |

`year` grain + seasonal_naive → `ForecastPolicyError`（seasonality_period 默认 1，无意义）。
显式 `seasonality_period ≤ 1` → `ForecastPolicyError`。

### 5.6 输入质量

- 历史含 NaN value → `ForecastInputQualityError`（让 agent 先 `transform(op="impute_nulls")`）。
- 时间 gap（实际 bucket 数 < expected）→ `ForecastInputQualityError`。

不在 forecast 内部静默补齐——与 cleaning policy 设计原则一致。

### 5.7 panel 处理

按 `axes` segment dims 分组；每组独立预测，per-segment fail-open：

- T 不足 → 该 segment 输出行：`predicted=NaN, lower=NaN, upper=NaN, residual_stddev=NaN, model="insufficient", reason_code="insufficient_history"`。
- 历史全常量（drift 或 seasonal_naive 残差为 0）→ `reason_code="constant_history"`，预测正常，区间宽度 = 0。
- 不抛错；`meta.train_row_count_per_segment` 记录每 segment 实际训练点数（不足时记 0）。

time_series 与 panel 行为不同：time_series T 不足直接抛 `ForecastInsufficientHistoryError`，因为整体只有一个序列，没有"degenerate 子集"的概念。

### 5.8 新错误

| 错误类 | 触发 |
|---|---|
| `ForecastShapeUnsupportedError` | scalar / segmented；不支持的 grain |
| `ForecastPolicyError` | `horizon < 1`；`interval_level` 越界；`year` grain 配 seasonal_naive；显式 `seasonality_period ≤ 1` |
| `ForecastInsufficientHistoryError` | time_series 下 T 不满足模型最小要求 |
| `ForecastInputQualityError` | history 有 NaN value 或时间 gap |

### 5.9 Lineage

```python
LineageStep(intent="forecast", inputs=[history.ref], params_digest=params_digest(params))

params = {
    "source_ref": history.ref,
    "value": value_resolved,
    "horizon": horizon,
    "model": model,
    "seasonality_period": effective_seasonality_period,
    "interval_level": interval_level,
}
```

## 6. `assess_quality` 算子

### 6.1 模块结构

新文件：

- `marivo/analysis_py/intents/assess_quality.py` — 函数 `assess_quality`，按 `type(target)` 内部分派。
- `marivo/analysis_py/intents/_quality_checks.py` — 4 个 check 实现。
- `marivo/analysis_py/frames/quality.py` — `QualityReport` + `QualityReportMeta`。

### 6.2 函数签名

```python
def assess_quality(
    target: BaseFrame,
    *,
    session: Session | None = None,
) -> QualityReport:
```

v1 仅接受 `MetricFrame`；其他 frame family → `QualityShapeUnsupportedError`，details 写明 "v1 仅支持 MetricFrame；其他 frame family 计划在 v1.1+"，hint 给出 fix snippet 提示对应未来路径。

### 6.3 检查目录

| Check id | 适用 shape | 阈值 |
|---|---|---|
| `row_count` | scalar / time_series / segmented / panel | `0` → blocking；`< 5` → warning；否则 ok |
| `null_ratio:<measure_column>` | 所有 | `> 0.5` → blocking；`> 0.1` → warning；否则 ok |
| `time_coverage` | time_series / panel | `coverage_ratio < 0.8` → blocking；`< 0.95` → warning；否则 ok |
| `duplicate_keys` | segmented / panel | 任何重复 → blocking |

每个 check 产出一行；`null_ratio` 对每个 numeric measure 列各产一行（`check_id=null_ratio:<column>`）。

### 6.4 检查实现细节

**time_coverage**：

- 从 `meta.axes["time"]` 取 time 列与 grain；从 `meta.window` 取 absolute window 起止。
- `expected_buckets = len(pandas.date_range(start, end, freq=grain_to_freq[grain]))`。
- `observed_buckets = df[time_column].nunique()`。
- `coverage_ratio = observed / expected`。
- panel 用**全局** distinct time bucket；per-segment 缺口留 v1.1+。
- details: `{expected_buckets, observed_buckets, coverage_ratio, missing_examples}`（missing 最多前 5 个）。

**duplicate_keys**：

- key tuple：panel = `(*segment_keys, time)`；segmented = `(*segment_keys)`。
- `details: {duplicate_count, examples}`（examples 最多前 5 个）。

**null_ratio**：

- 仅遍历 `meta.measure` 中声明的 numeric 列。
- `details: {column, null_count, null_ratio, threshold_warning: 0.1, threshold_blocking: 0.5}`。

**row_count**：

- `details: {row_count, threshold_warning: 5, threshold_blocking: 0}`。

### 6.5 `overall_status` 派生

- 任一 row `severity="blocking"` → `overall_status="blocking"`。
- 否则任一 `severity="warning"` → `overall_status="warning"`。
- 否则 `ok`。

`blocking_issue_count` 与 `warning_count` 按 severity 累计。

### 6.6 `recommended_followups` / `blocking_issues`

`meta` 上的 list[dict]，字段对齐设计文档 §"Result artifact follow-up contract"：

`FollowupAction` 字段：`action_id, kind, operator, input_refs, params, preconditions, expected_output_family`。

`BlockingIssue` 字段：`issue_id, kind, severity, source_refs, message, remediation_followups`。

v1 规则：

| Check 失败 | 生成 |
|---|---|
| `null_ratio` blocking | followup `{kind: "adjust_policy", operator: "transform", params: {"op": "impute_nulls"}, input_refs: [target.ref], expected_output_family: "metric_frame"}` |
| `time_coverage` blocking | followup `{kind: "adjust_policy", operator: "observe", params: {"narrow_window": true}, input_refs: [target.ref], expected_output_family: "metric_frame"}` |
| `duplicate_keys` blocking | blocking_issue `{kind: "quality", severity: "blocking", source_refs: [target.ref], message: "duplicate key tuples in metric frame"}`，无 followup（数据本身有 bug） |
| `row_count` blocking | blocking_issue `{kind: "sample_size", severity: "blocking", source_refs: [target.ref], message: "metric frame has zero rows"}` |

### 6.7 新错误

| 错误类 | 触发 |
|---|---|
| `QualityShapeUnsupportedError` | target 不是 `MetricFrame`（v1） |

### 6.8 Lineage

```python
LineageStep(intent="assess_quality", inputs=[target.ref], params_digest=params_digest(params))

params = {
    "source_ref": target.ref,
    "report_shape": "metric",
    "target_kind": target.meta.kind,
    "checks_run": checks_run,
}
```

## 7. 公共导出

`marivo/analysis_py/__init__.py` 追加（按字母排序）：

```python
from marivo.analysis_py.frames.forecast import ForecastFrame, ForecastFrameMeta
from marivo.analysis_py.frames.hypothesis import HypothesisTestResult, HypothesisTestResultMeta
from marivo.analysis_py.frames.quality import QualityReport, QualityReportMeta
from marivo.analysis_py.intents.assess_quality import assess_quality
from marivo.analysis_py.intents.forecast import forecast
from marivo.analysis_py.intents.test import hypothesis_test as test
from marivo.analysis_py.policies import SamplingPolicy
```

`__all__` 追加：`ForecastFrame, ForecastFrameMeta, HypothesisTestResult, HypothesisTestResultMeta, QualityReport, QualityReportMeta, SamplingPolicy, assess_quality, forecast, test`。

`marivo/analysis_py/intents/__init__.py` 追加 `assess_quality, forecast, hypothesis_test as test`。

`marivo/analysis_py/help.py` 注册三个新算子，每段 4~6 行简要说明 + 链接示例文件。

## 8. Skill examples（`make examples-check` 门禁）

`marivo-skill/marivo-py-analysis/references/examples/` 新增：

- `05_test_hypothesis.py` — `observe → observe → test`（time_series，单行 paired t-test）。
- `06_forecast_horizon.py` — `observe → forecast`（time_series，seasonal_naive，30 天）。
- `07_assess_metric_quality.py` — `observe → assess_quality`（time_series + 故意 sparse window 触发 warning）。

每个示例：完整可执行；使用 `_fixtures/tiny_semantic.py`；无打印副作用；末尾断言关键属性（与现有 `01_observe_single_window.py` 一致）以供 `make examples-check` 通过。

`references/pitfalls.md` 追加：

- `test`：scalar 不可检验、`SamplingPolicy.pairing` 与 shape 不匹配。
- `forecast`：`seasonal_naive` 历史不足、含 NaN 必须先 impute。
- `assess_quality`：v1 仅接受 MetricFrame。

`references/cheatsheet.md` 追加最终算子表三行，与设计文档 §"最终算子表"对齐。

`marivo-skill/marivo-py-analysis/SKILL.md` 保持在 600 行以内。

## 9. 测试

### 9.1 共享 fixture

`tests/shared_fixtures.py` 追加 `seeded_time_series_metric_frame(...)` 工厂：

```python
def seeded_time_series_metric_frame(
    *,
    session,
    grain: Literal["day", "week"] = "day",
    n_buckets: int = 30,
    segments: list[str] | None = None,
    value_pattern: Literal["constant", "linear", "seasonal_7", "noisy"] = "linear",
    seed: int = 42,
) -> MetricFrame: ...
```

直接走 `MetricFrame.from_dataframe`（不走 backend），快、确定。少数 end-to-end lineage 测试保留 `observe` 路径。

### 9.2 `tests/test_analysis_py_test.py`

- `test_mean_changed_time_series_basic`：已知差异 → `p_value < alpha`，`rejected=True`。
- `test_mean_changed_time_series_no_diff`：相同 series → `rejected=False`，`p_value ≈ 1`。
- `test_segmented_paired_across_segments`：segmented input，n = segment 数。
- `test_panel_per_segment_rows`：panel input，输出行数 = segment 数，列含 segment key。
- `test_panel_insufficient_pairs_in_one_segment`：某 segment paired n < `min_n` → `reason_code="insufficient_pairs"`，整体不抛错。
- `test_constant_diff_reason_code`：两 series 差为常量 → `reason_code="constant_diff"`，`rejected=False`。
- `test_scalar_raises`：scalar → `TestShapeNotTestableError`。
- `test_kind_mismatch_raises`：a/b semantic_kind 不同 → `SemanticKindMismatchError`。
- `test_cross_session_raises`：跨 session frame → `CrossSessionFrameError`。
- `test_alpha_out_of_range_raises`：`alpha=0` 或 `alpha > 0.5` → `TestPolicyError`。
- `test_pairing_mismatch_raises`：time_series + `pairing="segment_key"` → `TestPolicyError`。
- `test_alignment_kind_unsupported_raises`：非 `calendar_bucket` → `TestPolicyError`。
- `test_lineage_and_persistence`：输出可 `load_frame` 回读；lineage 含 observe + observe + test。

### 9.3 `tests/test_analysis_py_forecast.py`

- `test_naive_time_series`：常量 series → 预测全为该常量，区间宽度 = 0。
- `test_seasonal_naive_dow_period_7`：周期 7 合成数据 → 预测准确。
- `test_drift_trending_series`：线性趋势 series → 预测连续延伸。
- `test_interval_width_grows_with_horizon`：`h=30` 的 width > `h=1`（验证 sqrt(h)）。
- `test_panel_per_segment`：panel input，每 segment 独立预测，行数 = horizon × n_segments。
- `test_panel_segment_insufficient_history_writes_nan`：一个 segment T=1 → `predicted=NaN`，`model="insufficient"`，`reason_code="insufficient_history"`；其他 segment 正常。
- `test_scalar_raises`：scalar → `ForecastShapeUnsupportedError`。
- `test_segmented_raises`：segmented → `ForecastShapeUnsupportedError`。
- `test_year_grain_seasonal_naive_raises`：`ForecastPolicyError`。
- `test_seasonality_period_too_short_raises`：T < period+1 + seasonal_naive → `ForecastInsufficientHistoryError`。
- `test_history_with_nan_raises`：`ForecastInputQualityError`。
- `test_time_gap_raises`：`ForecastInputQualityError`。
- `test_horizon_zero_raises`：`ForecastPolicyError`。
- `test_interval_level_out_of_range_raises`：`ForecastPolicyError`。
- `test_lineage_and_persistence`。

### 9.4 `tests/test_analysis_py_assess_quality.py`

- `test_metric_scalar_ok`：scalar frame → 仅 row_count + null_ratio，`overall_status="ok"`。
- `test_metric_time_series_full_coverage`：完整 window，`coverage=1.0`，`overall=ok`。
- `test_metric_time_series_gap_warning`：缺 1 天 → time_coverage `severity=warning`，`overall=warning`。
- `test_metric_time_series_severe_gap_blocking`：缺 ≥ 20% → `blocking`。
- `test_metric_segmented_duplicate_keys_blocking`：构造 duplicate segment key → `blocking` + details.examples。
- `test_metric_panel_all_checks`：跑全部 4 个 check。
- `test_null_ratio_per_measure_row`：多个 measure 列 → 每列一行。
- `test_null_ratio_warning_and_blocking_thresholds`：分别命中两档阈值。
- `test_row_count_zero_blocking`：空 frame → blocking。
- `test_non_metric_frame_raises`：DeltaFrame → `QualityShapeUnsupportedError`。
- `test_recommended_followups_for_null_blocking`：`meta.recommended_followups` 非空，结构对齐 §6.6。
- `test_blocking_issues_for_duplicate_keys`：`meta.blocking_issues` 非空。
- `test_lineage_and_persistence`。

## 10. 类型与门禁

- 三个新 intent 模块顶部 `# mypy: disable-error-code=import-untyped`，与现有 intent 一致。
- 新 meta 用 `ConfigDict(extra="forbid")`。
- 新错误类继承 `AnalysisError`，实现 `_template_fields()` 返回 `location/cause/fix_snippet/doc`。
- `intents/__init__.py` 的 `__all__` 追加新名（按字母排序）。
- importlinter 不需要改：所有改动都在 `analysis_py` 内部。

PR 落地前必须通过：

- `make typecheck`
- `make lint`
- `make examples-check`
- `make test`（含三个新测试文件）

## 11. 与设计文档总章的偏差点

| 总章规则 | v1 实现 | 备注 |
|---|---|---|
| `test` accepts `MetricFrame[scalar]` | scalar fail-closed，抛 `TestShapeNotTestableError` | mean_changed t-test 需要 n ≥ 2 个观测；scalar 没有底层 sample，留待"scalar-as-summary"专题设计 |
| `AlignmentPolicy.kind` 多种 | 仅 `calendar_bucket` | 与 `correlate` 现状一致；其他 kind 留 v1.1+ |
| `SamplingPolicy` 完整字段集 | 子集（4 字段单值 Literal + min_n） | 字段名前向兼容，扩展靠新增 Literal 值 |
| `FollowupAction` / `BlockingIssue` typed | plain `list[dict]` | 字段名严格对齐，留待统一 typed migration |
| `QualityReport` 5 种 shape | 仅 `metric` | `report_shape: Literal["metric"]`，v1.1+ 扩 Literal 值 |
| panel forecast 失败语义 | per-segment fail-open + `reason_code="insufficient_history"` | time_series fail-closed；panel fail-open，避免污染 `evaluate_forecast` 的需求由 reason_code 列承担 |
