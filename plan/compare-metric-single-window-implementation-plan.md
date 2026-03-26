# `compare_metric` 支持 `single_window` 实施计划

> 日期：2026-03-26
> 目标：让 `compare_metric` 在保持 typed contract 的前提下，真正支持 `time_scope.mode = single_window` 执行，并把 API / UI / 测试 / 文档收口到一致状态。
> 原则：不把“请求模型接受”误当成“运行时支持”；优先保证外部契约清晰、证据抽取稳定、与现有 compare 模式兼容。
> 对齐原则：`compare_metric(single_window)` 与 `aggregate_query(single_window)` 都应表达“当前窗口 observation”，只保留 semantic metric 与 ad-hoc measure 的差异，不保留额外的单窗口语义分叉。

状态标记：`[ ]` 未开始 · `[~]` 进行中 · `[x]` 已完成 · `[!]` 阻塞

---

## 0. 结论先行

当前仓库里，`compare_metric` 对 `single_window` 的支持处于“半完成”状态：

- `time_scope` 规范化和 API 校验已接受 `single_window`
- shared scoped-query builder 已能表达 `single_window`
- `compare_metric` service 仍在执行入口硬性要求 `mode = compare`
- compare 结果、debug、summary、extractor payload 仍默认假设存在 baseline/current/delta
- 对外文档也明确写了“请求模型接受，但 service layer 还不能执行”

所以这次改动不是单点删一个 guard，而是一次跨 service / compiler / evidence / docs / tests 的收口。

---

## 1. 已确认的变更范围

### 1.1 已具备基础能力

- [x] `app/time_scope.py`
  - `normalize_compare_metric_request()` 已接受 `time_scope.mode in {"single_window", "compare"}`
- [x] `app/api/models.py`
  - API 模型已允许 `single_window`
- [x] `app/analysis_core/compiler.py`
  - `_build_scoped_query_parts()` 已能在 `single_window` 模式下注入 `_period = 'current'`

### 1.2 当前阻塞点

- [ ] `app/service.py`
  - `_run_compare_metric()` 在执行前直接拒绝 `single_window`
  - `_normalize_comparison_rows()` 只接受 compare-shaped rows
  - `_compare_metric_debug_payload()` 强依赖 baseline window
  - `_compare_metric_summary()` 只会生成 comparison wording
  - `rows = [row for row in all_rows if row.get("delta_pct") is not None]` 会把 single-window 结果全部丢掉
- [ ] `app/analysis_core/compiler.py`
  - `build_comparison_query()` 仍固定产出 compare-shaped SQL 列：`current_value` / `baseline_value` / `delta_pct` / `current_sessions` / `baseline_sessions`
- [ ] `app/evidence_engine/extractors/comparison.py`
  - `ComparisonRowExtractor` 把 compare payload 列设为 hard requirement
- [ ] 文档 / UI / 示例
  - `docs/api/sessions.md` 仍声明 compare-only execution
  - `app/static/user.html` 的 `compare_metric` 模板仍只展示 compare 示例
- [ ] 测试
  - 现有 `compare_metric` service / extractor / compiler 测试主要覆盖 compare-only shape

### 1.3 受影响但未必需要首批改动的下游

- [ ] evidence / causal checkers
  - 大量逻辑消费 `metric_change.payload.delta_pct`
  - 但也已有只依赖 `current_value + observed_window` 的时序测试
- [ ] readiness / reflection / synthesis
  - 如果 `single_window` 仍产出 `metric_change`，需要明确这类 observation 是否允许没有 `delta_pct`

---

## 2. 与 `aggregate_query` 的对齐原则

这次设计不再把 `compare_metric(single_window)` 看成“比较能力的降级版”，而是明确看成和 `aggregate_query(single_window)` 同类的 windowed observation primitive。

### A-1 必须对齐的语义

- [ ] 两者在 `single_window` 下都表示“当前窗口 observation”
- [ ] 两者都不伪造 `baseline_*` / `delta_pct`
- [ ] 两者都把 request-level `observed_window` 绑定到 `time_scope.current`
- [ ] 两者都允许下游只消费 `current_value + observed_window`
- [ ] 两者都只在真实存在 compare payload 时才参与 compare-only inference

### A-2 应保留的差异

- [ ] `compare_metric`
  - 值定义来自 published semantic metric
  - `metric` 是语义层指标名
- [ ] `aggregate_query`
  - 值定义来自请求中的 `measures`
  - 一个 step 可以返回多个 measure 视角

### A-3 不应强行对齐的部分

- [ ] 不要求两个 step 的 response shape 完全同构
- [ ] 不要求 `compare_metric` 复用 `aggregate_query` 的多-measure contract
- [ ] 不要求为了“完全一致”引入新的 shared observation type

结论：

- 要对齐的是“single-window 的时间语义、证据语义、下游推断规则”
- 不必对齐到“字段命名和返回结构逐字段一致”

---

## 3. 关键设计决策

在动手前先固定 4 个决策，否则实现会反复返工。

### D-1 `single_window` 的结果语义

- 建议：`compare_metric(single_window)` 与 `aggregate_query(single_window)` 对齐，产出当前窗口 semantic-metric observation rows，不伪造 baseline/delta
- 建议结果列最小集合：
  - `current_value`
  - `current_sessions`
  - slice dimensions
- 不建议伪造：
  - `baseline_value = null`
  - `baseline_sessions = null`
  - `delta_pct = null`

原因：`aggregate_query(single_window)` 已经证明“当前窗口 observation”是可成立的语义。伪造 compare 字段会让 `compare_metric(single_window)` 变成一种异常 comparison，而不是正常 observation。

### D-2 observation type 是否仍使用 `metric_change`

- 建议：首批仍用 `metric_change`，与 `aggregate_query` 当前行为保持一致，但允许 payload 缺少 compare-only 字段
- 同时在 extractor / causal checker 中明确：
  - `delta_pct` 缺失时，不参与 compare-only 推断
  - `current_value` + `observed_window` 仍可参与时间序列类分析

替代方案是新建 `metric_observation` 类型，但那会把改动面扩大到 synthesis / schemas / recommendation / contradiction logic，不适合作为首批交付。

### D-3 排序 contract

- compare 模式继续只接受 `delta_pct ASC|DESC`
- single-window 模式建议支持：
  - `current_value ASC|DESC`
  - `current_sessions ASC|DESC`
- 这个方向应与 `aggregate_query(single_window)` 一致：都按“当前窗口可见输出列”排序，而不是沿用 compare-mode 的 `delta_pct`
- 若短期不想扩展完整排序语法，则至少：
  - compare 模式保留现状
  - single-window 下 `order` 为空时给默认排序
  - single-window 下收到 `delta_pct ...` 明确报错

### D-4 artifact type 与 extractor contract

- 建议沿用 `aggregate_query` 的思路，把 artifact/extractor 看成“mode-aware row extractor”，而不是 compare-only extractor
- 两个可行方案：
  1. 扩展 `comparison_rows` extractor，让 required payload keys 由 context 决定
  2. 新增单窗口 artifact/extractor，例如 `metric_rows`

建议选方案 1。这样变更最小，service 层只需按模式传入不同 `payload_fields` / required keys。

---

## 4. 实施计划

## Phase 1: 固定外部契约与内部结果模型

### CM-SW-01 明确 `compare_metric(single_window)` 的返回契约

> 前置依赖：无 | 工作量：中 | 风险：高

- [x] 在文档里定义 single-window 响应 shape，并明确其语义与 `aggregate_query(single_window)` 对齐
  - 必含：`step_type`、`metric_name`、`summary`、`artifact_id`、`observations`
  - row payload 至少包含：`current_value`、`current_sessions`
  - 不再承诺返回 `baseline_value` / `delta_pct`
- [x] 明确 compare 模式和 single-window 模式的差异
  - compare：comparison semantics
  - single-window：current-window observation semantics，与 `aggregate_query(single_window)` 一致
- [x] 明确 `observed_window` 继续继承 `time_scope.current`

验收标准：

- `docs/api/sessions.md` 能给出 compare 和 single-window 两套示例
- 用户从文档上能看清哪些字段是 mode-specific

### CM-SW-02 固定 service 内部 row contract

> 前置依赖：CM-SW-01 | 工作量：中 | 风险：中

- [ ] 为 `compare_metric` 定义 mode-aware payload mapping，并保持与 `aggregate_query` 的 single-window payload 习惯一致
  - compare:
    - `current_value`
    - `baseline_value`
    - `delta_pct`
    - `current_sessions`
    - `baseline_sessions`
  - single_window:
    - `current_value`
    - `current_sessions`
- [ ] 把“required row fields”从 service 常量改成按 mode 选择
- [ ] 把 evidence context 的 required payload keys 下沉到 extractor contract，而不是散落在 service 分支里

验收标准：

- service 层不再把 compare-required 字段当成 single-window 必填

---

## Phase 2: 打通执行链路

### CM-SW-03 编译器支持 single-window compare_metric SQL

> 前置依赖：CM-SW-02 | 工作量：中 | 风险：高

- [ ] 重构 `app/analysis_core/compiler.py:build_comparison_query()`
  - 在 `scoped_query.mode = single_window` 时生成单窗口聚合 SQL
  - 输出列与 CM-SW-02 对齐
  - 单窗口结果 shape 要和 `aggregate_query(single_window)` 的“当前值可直接抽 observation”模式兼容
- [ ] 保持 compare SQL 产物不回归
- [ ] 如果保留函数名 `build_comparison_query()`，需补注释说明它同时支持 compare / single-window；否则改成更准确的命名并同步调用点

验收标准：

- 新增 compiler 单测覆盖 single-window 生成 SQL
- compare 相关现有编译测试全部继续通过

### CM-SW-04 service 解除 compare-only 执行限制

> 前置依赖：CM-SW-03 | 工作量：中 | 风险：高

- [ ] 修改 `app/service.py:_run_compare_metric()`
  - 去掉 compare-only guard
  - 按 mode 走不同 row normalization / filtering 逻辑
- [ ] compare 模式继续过滤 `delta_pct is not None`
- [ ] single-window 模式改成保留所有合法行，不再按 `delta_pct` 过滤
- [ ] mode-aware 生成 artifact 内容

验收标准：

- `POST /sessions/{id}/steps/compare_metric` 传 `single_window` 时返回 200
- compare 模式行为不变

### CM-SW-05 summary / debug / order 行为按 mode 分流

> 前置依赖：CM-SW-04 | 工作量：中 | 风险：中

- [ ] 拆分 `_compare_metric_debug_payload()`
  - compare: 当前逻辑保留
  - single-window: 只返回 `current_window` + `current_has_data`
- [ ] 拆分 `_compare_metric_summary()`
  - compare: 保留 comparison wording
  - single-window: 改成 observation wording，例如“top value / highest slice / no data in current window”，与 `aggregate_query(single_window)` 的语义一致
- [ ] 扩展 `_normalize_compare_metric_order()`
  - mode-aware 校验合法排序字段
  - 给 single-window 定一个清晰的默认顺序
  - 规则应遵循“按当前窗口输出列排序”，与 `aggregate_query(single_window)` 保持一致

验收标准：

- single-window summary 中不再出现 `baseline` / `comparison`
- 非法 `order` 在 mode 对应语义下报错明确

---

## Phase 3: 证据抽取与下游兼容

### CM-SW-06 ComparisonRowExtractor 改为 mode-aware

> 前置依赖：CM-SW-02 | 工作量：中 | 风险：高

- [ ] 修改 `app/evidence_engine/extractors/comparison.py`
  - required payload keys 改为由 context 指定
  - compare 维持严格校验
  - single-window 允许只有 `current_value` / `current_sessions`
  - single-window 抽取行为尽量向 `AggregateRowExtractor` 的 payload 习惯靠拢
- [ ] 补单元测试覆盖 compare / single-window 两种 payload 约束

验收标准：

- extractor 不再因为缺少 baseline/delta 而拒绝 single-window rows
- compare 缺字段仍会报错

### CM-SW-07 检查 causal / synthesis 对缺失 `delta_pct` 的容忍度

> 前置依赖：CM-SW-06 | 工作量：中 | 风险：中

- [ ] 逐个确认依赖 `metric_change.payload.delta_pct` 的路径
  - causal checkers
  - contradiction / confidence / recommendation aggregation
  - reflection context
- [ ] 对 compare-only 推断逻辑补保护
  - 无 `delta_pct` 时跳过，不报错
- [ ] 保留时间序列类能力
  - `current_value + observed_window` 仍能参与 temporal reasoning
  - 行为要与当前 `aggregate_query(single_window)` 产出的 observation 一致

验收标准：

- single-window observations 不会触发下游异常
- compare-only inference 不会把无 `delta_pct` observation 当成“0 变化”

---

## Phase 4: 文档、UI、测试收口

### CM-SW-08 API/UI/共享指南同步

> 前置依赖：CM-SW-05 | 工作量：中 | 风险：低

- [ ] 更新 `docs/api/sessions.md`
  - 删除“single_window 仅请求模型接受、service 不支持”的说明
  - 增加 single-window 示例与字段说明
- [ ] 更新 `docs/agent-guide.md`
  - 把 `compare_metric` 描述改成真正支持 `single_window` / `compare`
- [ ] 更新 `app/static/user.html`
  - `compare_metric` 默认模板改成更能体现双模式的示例
  - hint 文案说明 `time_scope.mode` 可选 `single_window` / `compare`
- [ ] 视情况同步 README / planning docs 中的旧说法

验收标准：

- 仓库内不再存在“compare_metric 执行只支持 compare”的过期文档

### CM-SW-09 补齐测试矩阵

> 前置依赖：CM-SW-04 + CM-SW-06 | 工作量：大 | 风险：高

- [ ] `tests/test_step_api_contract.py`
  - 保留 route-level `single_window` 接受测试
  - 增加响应 contract 示例校验
- [ ] `tests/test_compiler_executor.py`
  - 新增 `compare_metric(single_window)` 编译 SQL 测试
  - 覆盖默认排序 / 显式排序
- [ ] `tests/test_time_scope_resolution.py`
  - 新增 service 执行 `single_window` 的 typed request 测试
  - 校验 `scoped_query.mode = single_window`
  - 校验 `observed_window` 继承 `time_scope.current`
- [ ] `tests/test_compare_metric.py`
  - 新增真实 API happy path
  - 新增 no-data path
  - 新增 invalid single-window order path
- [ ] `tests/test_evidence_plugins.py`
  - extractor 支持 single-window payload
- [ ] 必要时补下游回归测试
  - temporal causal
  - evidence synthesis

验收标准：

- single-window 至少覆盖：contract、compiler、service、API、extractor、下游兼容
- compare 现有回归测试全部保持通过

---

## 5. 推荐 PR 切分

### PR-1 契约与编译器

- `app/analysis_core/compiler.py`
- `tests/test_compiler_executor.py`
- 必要的 service helper 常量调整

目标：single-window SQL 能正确生成，但还未完全收口 evidence / docs。

### PR-2 service 与 extractor

- `app/service.py`
- `app/evidence_engine/extractors/comparison.py`
- `tests/test_time_scope_resolution.py`
- `tests/test_compare_metric.py`
- `tests/test_evidence_plugins.py`

目标：HTTP 路径真正可执行。

### PR-3 文档与收口

- `docs/api/sessions.md`
- `docs/agent-guide.md`
- `app/static/user.html`
- README / 其它引用 compare-only 行为的文档

目标：移除仓库中的过期说明。

---

## 6. 风险与注意事项

### R-1 语义混淆风险

如果 single-window 仍沿用 `metric_change` 命名，用户可能误以为一定有 delta。必须在响应示例和 summary wording 上把“observation”与“comparison”区分开，并明确它与 `aggregate_query(single_window)` 同类。

### R-2 下游隐式假设风险

当前大量逻辑默认 `metric_change` 常带 `delta_pct`。这次实现不能只看 service happy path，必须验证因果/综合链路不会把缺失字段当作错误或零值。

### R-3 排序契约风险

如果不先定义 single-window 的 `order` 规则，前端和 planner 很容易继续发 `delta_pct DESC`，导致运行时行为含混。这里应直接对齐 `aggregate_query(single_window)` 的“当前输出列排序”原则。

### R-4 文档漂移风险

仓库里已经有一部分文档说“接受 single_window”，另一部分说“不可执行”。功能上线时必须同步收口，避免再次出现“模型支持但执行不支持”的状态。

---

## 7. 最小可交付定义

满足以下条件即可认为 `compare_metric(single_window)` 已上线：

- [ ] API 能成功执行 `compare_metric` with `time_scope.mode = single_window`
- [ ] 返回 rows / observations / summary，不依赖 baseline/delta 字段
- [ ] 单窗口语义与 `aggregate_query(single_window)` 对齐：都表达当前窗口 observation
- [ ] compare 模式无行为回归
- [ ] evidence pipeline 能接收 single-window observations，不因缺失 `delta_pct` 报错
- [ ] API 文档、agent guide、UI 示例全部更新
- [ ] 至少一组端到端测试证明 single-window 请求可执行

---

## 8. 建议实现顺序

1. 先定 single-window 的 row / observation / order contract
   先以 `aggregate_query(single_window)` 为基线定义对齐原则
2. 再改 compiler 输出
3. 再改 service 执行分支
4. 再改 extractor 与下游兼容
5. 最后统一文档、UI、测试

不要反过来从 service 入口直接删 guard 开始。那样只会把 compare-only 假设扩散到运行时错误。
