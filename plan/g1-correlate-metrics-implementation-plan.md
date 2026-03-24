# G-1 `correlate_metrics` implementation plan

> Status: planning only. Do not implement yet.

## Problem

`plan/backlog-causal-inference-gaps.md` 中的 G-1 本质问题不是“不会算相关系数”，而是 Factum 目前没有把“两个独立 step 产出的时间序列关系”建模成一等 evidence 的能力。

当前系统里：

- `aggregate_query` 只能把**单个 step 的单行结果**提炼成 observation。
- `DoseResponseChecker` 只会对 claim 的 supporting observations 重新做 Spearman，前提是这些 observations 已经存在于 evidence graph 中。
- `artifacts` 只负责持久化原始结果；代码里还没有“按 `artifact_id` / `step_id` 读取并对齐两个序列，再回写结构化 observation”的标准链路。

因此，像 “A 步骤里的日级 query count” 与 “B 步骤里的日级 failure rate” 这种跨步关系，即便人工算出了 `ρ=0.593, p<0.05`，也不会进入 Factum 的 observation / claim / recommendation 流程，自然也无法驱动 inference-level promotion。

## Current state summary

### 1. Step contract

- 已支持的 primitive step 只有 `compare_metric` / `profile_table` / `sample_rows` / `aggregate_query`。
- 定义位置：`app/analysis_core/primitives.py`
- runner 注册位置：`app/analysis_core/step_runners/generic.py`
- service 入口通过 `service.run_step()` 分发到各 `_run_*` 方法。

### 2. Evidence extraction path

- `aggregate_query` 在 `app/service.py` 中执行 SQL 后：
  - 把行结果写入 `artifacts`
  - 再通过 `AggregateRowExtractor` 生成 observations
- `ExtractorRegistry` 当前按 `artifact_type` / extractor name 注册，默认只有 5 个 extractor：
  - `comparison_rows`
  - `aggregate_rows`
  - `funnel_rows`
  - `anomaly_rows`
  - `contribution_shift_rows`
- 这意味着 G-1 文档里写的“按 `step_type == correlate_metrics` 注册 extractor”需要按现有架构改写成：
  - 新增 `correlation_rows` extractor
  - 由 `correlate_metrics` runner 显式调用它

### 3. Causal promotion path

- `DoseResponseChecker` 已有无依赖的 `_spearman_correlation()` / `_pearson_correlation()` helper。
- 但它只会读取 supporting observations 里的：
  - `subject.slice`
  - `payload.delta_pct`
- 它不会消费“已经预计算好的 correlation observation”。

### 4. Planner / UI coupling

- 计划校验的有效 step 类型来自 `SUPPORTED_STEP_TYPES`，所以加新 step 后 plan 校验会自动看到它。
- 但 `app/planning.py` 里只有 `compare_metric` / `profile_table` / `sample_rows` 的显式参数校验。
- `ExecutionTargetIR` 当前只把 `synthesize_findings` 当作 `artifact_only`；若新增 `correlate_metrics`，最好一起纳入这个分支。
- `app/static/user.html` 的 Run Step 下拉框和 Draft Plan modal 都需要补新 step。

## Recommended solution

我认为**最合理的方案**是把 G-1 做成一个新的、显式的、artifact-only primitive step：`correlate_metrics`，而不是继续把逻辑塞进 `DoseResponseChecker` 或 `aggregate_query`。

### Why this is the right shape

1. **保持 evidence graph 可审计**
   相关性是一次明确的分析动作，应该像 `aggregate_query` 一样留下 step、artifact、observation、summary 和 provenance，而不是在 causal checker 里隐式重算。

2. **复用现有架构，而不是绕开它**
   Factum 当前的核心抽象是 “step -> artifact -> observation -> claim upgrade”。G-1 的缺口就在这里，最稳妥的修复方式是把“跨步相关性”补进这条链，而不是加一段旁路逻辑。

3. **让后续 G-2 / G-3 能直接收益**
   一旦 `correlate_metrics` 产出带 `observed_window` 的 `correlation_result` observation，后续 temporal/confounder 逻辑就能围绕真实 evidence 工作，而不是围绕静态提示文案工作。

4. **避免引入重型科学计算依赖**
   仓库当前没有 `scipy` / `numpy`。首版更适合复用现有 rank/pearson helper，并补一个依赖-free 的近似 `p_value` 计算，而不是把运行时拖进新的科学计算栈。

## Proposed implementation shape

### A. 新增 `correlate_metrics` primitive step

新增 step 参数建议：

- `left_artifact_id` 或 `left_step_id`
- `right_artifact_id` 或 `right_step_id`
- `left_value_column`
- `right_value_column`
- `join_on`：用于对齐两个序列的共享键，**首版显式要求**
- `method`：`spearman` / `pearson` / `both`
- `min_pairs`：最少样本数，默认 3
- `metric` / `observation_type`：可选覆盖

实现要点：

- 在 `app/service.py` 增加 `_run_correlate_metrics()`
- 增加受 session scope 保护的 artifact loader helper，支持：
  - 通过 `artifact_id` 直接取内容
  - 通过 `step_id` 找到该 step 最新/唯一 artifact
- runner 读取两个 `aggregate_query` 产出的 rows，按 `join_on` 对齐后形成 `(x, y)` pairs
- 产出一个新的 correlation artifact（建议 `artifact_type="correlation"`）
- summary 里直接包含 `rho / p_value / n / method`

### B. 把统计结果写成结构化 observation

新增 `CorrelationObservationExtractor`：

- 文件：`app/evidence_engine/extractors/correlation.py`
- extractor name 建议：`correlation_rows`
- `artifact_type` 建议：`correlation`
- 输出 observation type：`correlation_result`

payload 建议至少包含：

- `rho`
- `p_value`
- `n`
- `method`
- `left_metric`
- `right_metric`
- `join_on`

subject 建议包含：

- `metric`: 以 outcome / primary metric 为主
- `slice`: 保留公共维度或空对象
- `related_metric`: 驱动序列名

`observed_window` 应取两条序列时间范围的并集；如果 `join_on` 是日/小时字段，则由最小值和最大值推断。

### C. 让 causal checker 消费预计算 observation

扩展 `DoseResponseChecker`，优先级建议如下：

1. 先看 claim 的 supporting observations 中是否已有 `correlation_result`
2. 如果有，就直接读取 `rho / method / n / p_value`
3. 如果没有，再保留现有基于 supporting observations 的重算路径

这样可以：

- 保持向后兼容
- 让 `correlate_metrics` 成为可审计的 first-class trigger
- 避免同一组序列被重复重算

### D. Planner / UI / validation 一起补齐

- `app/analysis_core/primitives.py`：加入 taxonomy
- `app/analysis_core/step_runners/correlation.py`：注册 runner
- `app/analysis_core/step_runners/__init__.py`：装配新 runner
- `app/planning.py`：
  - 给 `correlate_metrics` 增加参数校验
  - 把它标记为 `artifact_only`
- `app/static/user.html`：
  - Run Step 下拉框加入 `correlate_metrics`
  - Draft Plan modal 的 step 列表也加入

## Important design decisions

### 1. 不建议把 G-1 做成 `aggregate_query` 的一个 flag

原因：

- `aggregate_query` 是 SQL 执行 primitive；`correlate_metrics` 是 artifact-to-artifact 分析 primitive
- 混到一个 step 里会让 routing、governance、summary、测试边界都变模糊

### 2. 不建议只改 `DoseResponseChecker`

原因：

- 那样仍然没有新的 observation 落库
- 结果不可审计、不可复用、无法在 reflection context 中直接暴露
- 仍然违背 backlog 文档想补齐的“写回 evidence graph”目标

### 3. `join_on` 首版显式要求，不做自动猜测

这是 G-1 最重要的建模边界，现已确认采用该方案。

虽然可以尝试自动寻找两个 artifact 的公共列，但这很容易引入：

- 错误对齐
- 多键 join 歧义
- “同名但语义不同”的隐患

更稳妥的首版是：

- 首版要求调用方显式传 `join_on`
- 如果未来需要，再扩成 `left_join_on` / `right_join_on` 或多键 join

### 4. `p_value` 采用 dependency-free approximation

理由：

- 仓库当前不依赖 `scipy`
- G-1 的核心目标是 evidence write-back，不是建立完整统计库
- 首版用近似方法足以支持“通过阈值触发 causal bonus”的产品需求

## Files likely to change

- `app/analysis_core/primitives.py`
- `app/analysis_core/step_runners/__init__.py`
- `app/analysis_core/step_runners/correlation.py` (new)
- `app/service.py`
- `app/evidence_engine/extractors/correlation.py` (new)
- `app/evidence_engine/registry.py`
- `app/evidence_engine/causal_checkers.py`
- `app/planning.py`
- `app/static/user.html`

## Test plan

优先补这几类测试：

1. **unit**
   - `tests/test_causal_checkers.py`
   - `tests/test_extractor_registry.py`
   - `tests/test_step_registry.py`
   - 新增或扩展 correlation runner 的纯函数/服务层测试

2. **extractor / runner integration**
   - 新增一个 service-level 测试：
     - 先跑两个 `aggregate_query`
     - 再跑 `correlate_metrics`
     - 断言 artifact、observation、summary、`observed_window` 均已落库

3. **evidence promotion**
   - 扩展 `tests/test_phase2_integration.py` 或 `tests/test_evidence.py`
   - 断言 `correlation_result` 可以触发 `DoseResponseChecker` bonus path

4. **UI smoke**
   - `tests/test_ui.py` 至少覆盖新 step 在页面选项中可见

## Suggested execution order

1. 先补 step contract、runner 注册、planner validation
2. 再实现 artifact loader + `_run_correlate_metrics`
3. 再加 `CorrelationObservationExtractor`
4. 再扩 `DoseResponseChecker`
5. 最后补 UI 与测试

## Risks / notes

- backlog 文档把 extractor 触发条件写成 `step_type == "correlate_metrics"`，但现有代码是按 extractor name / artifact_type 工作；实现时应以**现有架构为准**。
- 如果 `correlate_metrics` 不被标成 `artifact_only`，plan validation / costing 输出会显得不准确。
- 若未来要支持非时间轴 join，建议把参数从 `join_on` 扩成 `left_join_on` / `right_join_on`，但这不适合首版。

## Todo outline

1. 定义 `correlate_metrics` 的 step contract 与 planner/UI 暴露
2. 实现 artifact 读取、序列对齐、统计计算与 correlation artifact 持久化
3. 新增 `CorrelationObservationExtractor` 并写回 `correlation_result`
4. 扩展 `DoseResponseChecker` 识别预计算 correlation evidence
5. 补齐单元、集成、UI smoke 测试
