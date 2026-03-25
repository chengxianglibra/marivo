# Time-Scope Unification 执行任务清单

> 基于 [`plan/time-scope-unification-rfc.md`](/Users/lichengxiang/source/oss/factum/plan/time-scope-unification-rfc.md) 拆解。
> 目标：把 `compare_metric` 与 `aggregate_query` 统一到同一套 `time_scope` / `scope` / `time_axis` 契约与编译路径。
> 原则：不保留旧接口兼容；以“可分批提交、可单独验收、依赖清晰”为第一优先级。

状态标记：`[ ]` 未开始 · `[~]` 进行中 · `[x]` 已完成 · `[!]` 阻塞

---

## 0. 总体策略

这次改动不是单点参数替换，而是一次跨层重构，必须一起覆盖：

1. step contract
2. planner validation
3. service-layer normalization / resolution
4. shared SQL compilation
5. observation window 产出
6. time capability metadata / heuristics
7. API 文档、示例、测试、UI

推荐按 6 个 implementation tracks 推进：

- Track A：契约与校验
- Track B：共享时间解析与编译内核
- Track C：`aggregate_query` 迁移
- Track D：`compare_metric` 迁移
- Track E：metadata / pruning / engine 兼容
- Track F：测试、文档、清理

---

## 1. 里程碑与切分建议

### Milestone 1: 新 contract 可落地但未接管旧实现

目标：

- 定义新请求模型与内部 resolved model
- planner / API 层能识别并校验新字段
- 旧执行路径暂不删除

建议作为 PR-1。

### Milestone 2: 共享时间解析与 compare compiler 可跑通

目标：

- 具备统一 `time_scope` 规范化
- 具备统一 `time_axis` 解析
- 具备单窗口 / 双窗口共享 SQL 骨架

建议作为 PR-2。

### Milestone 3: `aggregate_query` 完成迁移

目标：

- 新接口驱动 `aggregate_query`
- `measures` 替换 `select`
- 支持 `single_window` / `compare`

建议作为 PR-3。

### Milestone 4: `compare_metric` 完成迁移

目标：

- 新接口驱动 `compare_metric`
- 支持 `day` / `hour`
- 共享 Track B 的解析与编译路径

建议作为 PR-4。

### Milestone 5: metadata/pruning/doc/test 收口

目标：

- 三类时间布局全覆盖
- 文档、UI、示例、测试切到新 contract
- 删除遗留旧字段逻辑

建议作为 PR-5。

---

## 2. 执行清单

## Phase 1: P0 契约与内部模型

### TSU-01 新 step contract 与 API 模型

> 前置依赖：无 | 工作量：中 | 风险：高

- [x] **TSU-01.1** 定义共享子模型：`TimeWindow` / `TimeScope` / `Scope` / `TimeAxis`
  - 文件：`app/api/models.py`
  - 约束：
    - `time_scope.mode in {"single_window", "compare"}`
    - `time_scope.grain in {"day", "hour"}`
    - `baseline` 仅在 compare 模式出现
    - `scope.predicate` 只允许非时间条件
- [x] **TSU-01.2** 重定义 `compare_metric` 请求模型
  - 字段对齐 RFC：`table`、`metric`、`dimensions`、`time_scope`、`scope`、`time_axis`、`order`、`limit`
- [x] **TSU-01.3** 重定义 `aggregate_query` 请求模型
  - 字段对齐 RFC：`table`、`group_by`、`measures`、`time_scope`、`scope`、`time_axis`、`order`、`limit`
- [x] **TSU-01.4** 给 `measure` 定义强约束
  - 规则：必须是聚合表达式，且每项都必须显式 `as`
- [x] **TSU-01.5** 在 response / schema 描述里明确“旧字段已废弃且不再支持”
  - 重点字段：`period_start`、`period_end`、`baseline_start`、`baseline_end`、`comparison_type`、`compare_period`、`date_column`、`where`、`filter`

验收标准：

- API 模型层可以表达 RFC 中的两个新请求示例
- 旧字段不再被当作合法 contract 文档化

### TSU-02 内部 resolved model

> 前置依赖：TSU-01 | 工作量：中 | 风险：中

- [x] **TSU-02.1** 定义统一的 service-layer resolved request 结构
  - 建议位置：`app/models.py` 或新增 `app/time_scope.py`
  - 字段参考 RFC 第 6 节：`compare_kind`、`grouping`、`value_spec`、`resolved_time_axis`
- [x] **TSU-02.2** 为 `compare_metric` 与 `aggregate_query` 增加 normalize 入口
  - 输出统一 resolved object，而不是各自拼装 SQL 参数
- [x] **TSU-02.3** 明确 `analysis_time_expr` 与 `partition_pruning_predicate` 的职责边界
  - 注释和类型文档必须说明：
    - correctness 由 `analysis_time_expr` 控制
    - pruning 由 `partition_pruning_predicate` 控制

验收标准：

- service 层已经不需要直接依赖“某个 step 自己的时间参数命名”
- 统一 resolved object 可以同时承载 semantic metric 和 ad-hoc aggregate

### TSU-03 planner / IR / taxonomy 同步

> 前置依赖：TSU-01 | 工作量：中 | 风险：中

- [x] **TSU-03.1** 更新 [`app/analysis_core/primitives.py`](/Users/lichengxiang/source/oss/factum/app/analysis_core/primitives.py) 的 step 描述
  - 去掉旧 compare 参数说明
  - 明确 `aggregate_query` 现在也是 window-aware typed step
- [x] **TSU-03.2** 更新 `app/planning.py` 对 `compare_metric` / `aggregate_query` 的参数校验
  - 新必填项：
    - `compare_metric`: `table`, `metric`, `time_scope`
    - `aggregate_query`: `table`, `measures`, `time_scope`
- [x] **TSU-03.3** 增加对 `scope.predicate` 时间条件禁用的 plan-time 报错
  - 新 issue code 建议：`time_predicate_not_allowed_in_scope`
- [x] **TSU-03.4** 更新 `app/analysis_core/ir.py` 的 legacy-step 归一化逻辑
  - 避免 IR 还假设 `metric_name` / `table_name`
- [x] **TSU-03.5** 更新 plan explanation / costing / artifact contract 注释
  - 确保 planner 输出不再引用旧字段名

验收标准：

- plan validate 能正确拒绝旧 contract
- plan IR 中的两个 step 使用统一的新字段语义

---

## Phase 2: P0 共享时间解析与共享编译骨架

### TSU-04 TimeScopeResolver

> 前置依赖：TSU-02 | 工作量：中 | 风险：高

- [x] **TSU-04.1** 实现 `TimeScopeResolver`
  - 规范化成半开区间 `[start, end)`
- [x] **TSU-04.2** 实现 `day` / `hour` 边界合法性校验
  - `hour` 必须是 datetime-compatible
  - `day` 可接受 date-only 输入，但内部要规范化
- [x] **TSU-04.3** 实现 compare/single-window 统一输出
  - compare 输出 current + baseline
  - single-window 输出 current only
- [x] **TSU-04.4** 处理 unequal window 的明确策略
  - RFC 未禁止 unequal windows；需明确：
    - 是否允许
    - 是否仅 summary warning
    - 是否影响 delta 解释

验收标准：

- 所有下游组件只消费 normalized time windows
- 无任何 runner 继续手工解析时间窗口字符串

### TSU-05 TimeAxisResolver

> 前置依赖：TSU-02 | 工作量：大 | 风险：高

- [x] **TSU-05.1** 实现 metadata-first 的 time axis 解析
  - 优先读取 entity/source metadata 的 `time_capabilities`
- [x] **TSU-05.2** 实现 heuristic fallback
  - timestamp 候选：`event_time`、`timestamp`、`created_at`、`updated_at`、`time`
  - day 候选：`log_date`、`event_date`、`dt`、`date`、`day`
  - hour 候选：`log_hour`、`event_hour`、`hour`、`dt_hour`
- [x] **TSU-05.3** 产出统一 `ResolvedTimeAxis`
  - 字段至少包含：
    - `analysis_time_kind`
    - `analysis_time_expr`
    - `partition_pruning_predicate`
    - `observation_grain`
- [x] **TSU-05.4** 实现三类布局分支
  - partition-only
  - timestamp-only
  - timestamp + partition mixed
- [x] **TSU-05.5** 实现请求级 `time_axis` override
  - 明确 override 优先级高于 metadata / heuristics

验收标准：

- 在缺省请求下能稳定解析三类表结构
- mixed layout 默认“timestamp 做语义，partition 做 pruning”

### TSU-06 Shared compare compiler

> 前置依赖：TSU-04 + TSU-05 | 工作量：大 | 风险：高

- [x] **TSU-06.1** 提取共享 scoped/periodized CTE builder
  - 建议位置：`app/service.py` 拆出 helper，或新增 `app/query_compilation/time_scope.py`
- [x] **TSU-06.2** 实现 compare-mode SQL skeleton
  - 产出 `_period in {"current", "baseline"}`
- [x] **TSU-06.3** 实现 single-window SQL skeleton
  - 无 baseline，但仍走统一 scoped path
- [x] **TSU-06.4** 接入 `analysis_time_expr` + `partition_pruning_predicate`
- [x] **TSU-06.5** 保证 session constraints / session raw_filter 仍被自动注入
  - 且顺序明确：window filter、pruning、session constraints、scope constraints、scope predicate
- [x] **TSU-06.6** 统一 observation window 输出输入
  - compare by day -> `granularity="day"`
  - compare by hour -> `granularity="hour"`

验收标准：

- `compare_metric` 与 `aggregate_query` 可以共享同一套 window compilation helper
- Trino/Iceberg 这类要求分区裁剪的表在新骨架上仍可执行

---

## Phase 3: P0 `aggregate_query` 迁移

### TSU-07 `aggregate_query` 接口替换

> 前置依赖：TSU-01 + TSU-06 | 工作量：大 | 风险：高

- [ ] **TSU-07.1** 重写 [`app/service.py`](/Users/lichengxiang/source/oss/factum/app/service.py) 中 `_run_aggregate_query()`
  - 不再接受 `select`
  - 不再接受 `compare_period`
  - 不再接受 `date_column`
  - 不再接受 step-level `where`
- [ ] **TSU-07.2** 用 `measures` 替换原 `select` contract
  - 每个 measure 显式 alias
- [ ] **TSU-07.3** `group_by` 迁移到统一 grouping 流程
- [ ] **TSU-07.4** 支持 `time_scope.mode = single_window`
- [ ] **TSU-07.5** 支持 `time_scope.mode = compare`
- [ ] **TSU-07.6** `scope.constraints` / `scope.predicate` 注入新的 shared compiler
- [ ] **TSU-07.7** 拒绝 `scope.predicate` 中的时间条件
  - service 层与 planner 层双重防线

验收标准：

- `aggregate_query` 全部时间语义只能由 `time_scope` 表达
- compare 聚合与单窗口聚合共用相同编译主干

### TSU-08 `aggregate_query` observation / extractor 对齐

> 前置依赖：TSU-07 | 工作量：中 | 风险：中

- [ ] **TSU-08.1** 审计 `AggregateRowExtractor` 是否仍假设旧列名 / 旧 period 结构
- [ ] **TSU-08.2** 调整 observation window 注入逻辑
  - compare 模式和 single-window 模式都要输出与 `time_scope.grain` 一致的 window/granularity
- [ ] **TSU-08.3** 审计 temporal causal 相关 checker 是否依赖旧 aggregate 观察方式
  - 重点：`observed_window` 是否仍然来自 compare_period 或 group_by heuristic
- [ ] **TSU-08.4** 明确保留或删除 `observed_window_column` 机制
  - 若保留，需要说明其与 `time_scope` 的关系
  - 若删除，需要同步清理相关文档与测试

验收标准：

- 新 `aggregate_query` 产出的 observation 能稳定驱动现有 evidence / temporal logic

---

## Phase 4: P0 `compare_metric` 迁移

### TSU-09 `compare_metric` 接口替换

> 前置依赖：TSU-01 + TSU-06 | 工作量：大 | 风险：高

- [ ] **TSU-09.1** 重写 [`app/service.py`](/Users/lichengxiang/source/oss/factum/app/service.py) 中 `_run_compare_metric()`
  - 不再使用 `metric_name` / `table_name`
  - 不再使用 `period_start` / `period_end`
  - 不再使用 `baseline_start` / `baseline_end`
  - 不再使用 `comparison_type`
  - 不再使用 step-level `filter` / `where`
- [ ] **TSU-09.2** 切换到统一输入：`table`、`metric`、`dimensions`、`time_scope`、`scope`、`time_axis`
- [ ] **TSU-09.3** 复用 shared compare compiler，而不是 compare_metric 自己拼双窗口逻辑
- [ ] **TSU-09.4** 保持 semantic metric resolution 的职责边界不变
  - 只改时间合同与编译入口，不把 metric resolution 逻辑混进 resolver
- [ ] **TSU-09.5** 支持 `hour` grain compare
  - 包含 partition-only / timestamp-only / mixed 三类布局

验收标准：

- `compare_metric` 与 `aggregate_query(compare)` 在时间语义上完全共享同一 mental model
- compare_metric 不再有“filter 截断 baseline/current”的旧问题入口

### TSU-10 `compare_metric` 结果与 evidence 对齐

> 前置依赖：TSU-09 | 工作量：中 | 风险：中

- [ ] **TSU-10.1** 校验 comparison artifact 输出列是否仍满足 extractor 期望
- [ ] **TSU-10.2** 让 observation 的 `observed_window` 基于 resolved `time_scope`
- [ ] **TSU-10.3** 校验 `hour` compare 的 observation granularity 与 temporal checker 兼容
- [ ] **TSU-10.4** 审计 summary/debug 中所有旧字段表述
  - 例如 period wording、baseline wording、warning wording

验收标准：

- 新 compare contract 下 evidence graph 的 window 信息仍然可用于 L1/L2 checker

---

## Phase 5: P1 metadata / pruning / engine 兼容

### TSU-11 metadata schema 与解析来源

> 前置依赖：TSU-05 | 工作量：中 | 风险：中

- [ ] **TSU-11.1** 定义 `time_capabilities` 最小 schema
  - 参考 RFC 第 12 节
- [ ] **TSU-11.2** 决定 schema 先挂载在哪里
  - `entity.properties`
  - `source_object.properties`
  - 或双层策略
- [ ] **TSU-11.3** 实现 metadata 读取入口
  - 让 resolver 不直接散落访问各表 JSON 字段
- [ ] **TSU-11.4** 明确 timezone phase-1 策略
  - 若仍采用 session-consistent naive timestamps，需要文档写明限制

验收标准：

- resolver 已经支持显式 metadata 提示，不再完全靠字段名猜测

### TSU-12 engine-specific pruning

> 前置依赖：TSU-05 + TSU-06 | 工作量：大 | 风险：高

- [ ] **TSU-12.1** 实现 day-only partition pruning
  - 典型字段：`log_date`
- [ ] **TSU-12.2** 实现 day + hour partition pruning
  - 典型字段：`log_date` + `log_hour`
- [ ] **TSU-12.3** 实现 edge-day bounded hour pruning
  - 同一天内小时过滤
  - 跨天窗口的首尾日小时过滤
- [ ] **TSU-12.4** mixed layout 中同时注入 timestamp correctness + partition pruning
- [ ] **TSU-12.5** 审计 DuckDB / Trino 差异
  - expression compatibility
  - string/date casting
  - partition predicate formatting

验收标准：

- 三类时间布局在目标 engine 上都能生成可执行 SQL
- Trino/Iceberg 分区约束场景不回退成全表扫描或直接报错

---

## Phase 6: P0/P1 测试、文档、UI、遗留清理

### TSU-13 单元测试

> 前置依赖：TSU-04 ~ TSU-12 | 工作量：大 | 风险：中

- [ ] **TSU-13.1** 新增 `TimeScopeResolver` 单测
- [ ] **TSU-13.2** 新增 `TimeAxisResolver` 单测
- [ ] **TSU-13.3** 新增 shared compiler 单测
  - partition-only
  - timestamp-only
  - mixed
- [ ] **TSU-13.4** 新增 `scope.predicate` 时间条件拒绝测试
- [ ] **TSU-13.5** 新增 `day` / `hour` granularity observation 测试
- [ ] **TSU-13.6** 新增 metadata 优先于 heuristic 的解析测试

验收标准：

- 时间解析、编译、校验三层都有独立测试，不只靠 service-level 覆盖

### TSU-14 service / integration 测试迁移

> 前置依赖：TSU-07 + TSU-09 | 工作量：大 | 风险：高

- [ ] **TSU-14.1** 重写 `tests/test_aggregate_query.py`
  - 删除对 `select` / `where` / `compare_period` / `date_column` 的正向依赖
- [ ] **TSU-14.2** 重写 compare_metric 相关测试
  - 删除对 `period_start` / `period_end` / `comparison_type` / `filter` 的正向依赖
- [ ] **TSU-14.3** 更新 `tests/test_temporal_annotation.py`
  - 从“compare_period 推导 observed_window”切到“time_scope 推导 observed_window”
- [ ] **TSU-14.4** 更新 `tests/test_temporal_causal.py`
  - 覆盖日级与小时级 compare window
- [ ] **TSU-14.5** 更新 plan validation / planning / costing / IR 测试
  - 重点文件：
    - `tests/test_planning.py`
    - `tests/test_plan_validation.py`
    - `tests/test_analysis_ir.py`
    - `tests/test_costing.py`

验收标准：

- 不再存在“旧 contract 仍被测试视为正确行为”的情况

### TSU-15 API / guide / plan 文档更新

> 前置依赖：TSU-07 + TSU-09 完成 | 工作量：大 | 风险：中

- [ ] **TSU-15.1** 更新 [`docs/api/sessions.md`](/Users/lichengxiang/source/oss/factum/docs/api/sessions.md)
  - 完整替换 `compare_metric` / `aggregate_query` 请求示例
- [ ] **TSU-15.2** 更新 [`docs/api/planning.md`](/Users/lichengxiang/source/oss/factum/docs/api/planning.md)
  - plan validate 错误码和示例切到新字段
- [ ] **TSU-15.3** 更新 [`docs/api/quickstart.md`](/Users/lichengxiang/source/oss/factum/docs/api/quickstart.md)
  - 删除旧 curl 示例
- [ ] **TSU-15.4** 更新 [`docs/service/causal-inference.md`](/Users/lichengxiang/source/oss/factum/docs/service/causal-inference.md)
  - 去掉对 `period_start` / `observed_window_column` / `compare_period` 的过时描述
- [ ] **TSU-15.5** 更新 [`docs/agent-guide.md`](/Users/lichengxiang/source/oss/factum/docs/agent-guide.md)
  - 明确新的 step contract 与时间语义
- [ ] **TSU-15.6** 审计并更新 `plan/` 下所有还引用旧 compare 参数的文档
  - 重点关注：
    - `plan/task-list.md`
    - `plan/backlog-causal-inference-gaps.md`
    - `plan/factum-design-doc.zh.md`
    - `plan/evidence-engine-attribution-improvements.md`

验收标准：

- 仓库对外文档不再同时存在两套互相冲突的 compare/aggregate contract

### TSU-16 UI / examples / final cleanup

> 前置依赖：TSU-15 | 工作量：中 | 风险：低

- [ ] **TSU-16.1** 更新 [`app/static/user.html`](/Users/lichengxiang/source/oss/factum/app/static/user.html) 的 step 表单与示例
- [ ] **TSU-16.2** 如有 admin/debug 页面展示旧字段，也一并更新
- [ ] **TSU-16.3** 删除 service / planner / tests 中残留的 legacy 参数分支
- [ ] **TSU-16.3a** 清理 `app/service.py` 中 typed contract 到 legacy compiler params 的 bridge
  - 当前中间态：
    - `compare_metric` 仍通过 `_bridge_compare_metric_request()` 生成 `metric_name` / `table_name` / `period_*` / `filter`
    - `aggregate_query` 仍通过 `_bridge_aggregate_query_request()` 生成 `select` / `where` / `compare_period` / `order_by`
  - 清理目标：
    - service 层直接消费 resolved `time_scope` / `scope` / `time_axis`
    - 不再把新 contract 重新翻译回旧 SQL compiler 入参
- [ ] **TSU-16.3b** 删除 `app/time_scope.py` 中仅为 legacy bridge 存在的拒绝/映射逻辑
  - 当前中间态：
    - normalizer 仍显式拒绝 `metric_name` / `table_name` / `select` / `compare_period` / `order_by`
    - 这些校验现在有价值，但本质上是在保护“新入口 + 旧执行内核”的过渡架构
  - 清理目标：
    - 当执行内核完全切到新 contract 后，只保留对外 contract 自身需要的校验
    - 删除仅服务于 legacy bridge 的映射约束说明
- [ ] **TSU-16.3c** 删除 planner / IR 中的 legacy fallback 读取顺序
  - 当前中间态：
    - `app/analysis_core/ir.py` 仍允许 `table_name` / `metric_name` 作为 fallback
    - `app/planning.py` 仍保留 `legacy_param_not_supported` 相关判定与错误码
  - 清理目标：
    - IR helper 只认 `table` / `metric`
    - planner 不再需要“旧字段拒绝器”，因为旧字段路径会在系统中彻底消失
- [ ] **TSU-16.3d** 清理测试中的过渡断言
  - 当前中间态：
    - `tests/test_tsu02_time_scope.py` 仍显式断言 bridge 后的 legacy params，如 `order_by`、`select`、`period_*`
    - 这些断言是阶段性必要，但不应成为最终 contract
  - 清理目标：
    - service / integration 测试改为验证 shared compiler / resolved request / final artifact 行为
    - 不再把 legacy bridge 输出当成长期正确行为
- [ ] **TSU-16.4** 全量回归测试
  - 推荐最少执行：
    - `.venv/bin/pytest tests/test_aggregate_query.py`
    - `.venv/bin/pytest tests/test_temporal_annotation.py`
    - `.venv/bin/pytest tests/test_temporal_causal.py`
    - `.venv/bin/pytest tests/test_plan_validation.py`
    - `.venv/bin/pytest tests/test_planning.py`

验收标准：

- 代码、UI、文档、测试全部只认新 contract
- 不再存在“typed API / planner + legacy service/compiler bridge”这类中间态实现

---

## 3. 推荐 PR 拆分

为降低 review 和回归风险，建议按下面顺序拆 PR：

1. `PR-1`：TSU-01 ~ TSU-03
   - 只引入新模型、planner 校验、taxonomy/IR 更新
   - 暂不接管旧 service 执行逻辑
2. `PR-2`：TSU-04 ~ TSU-06
   - 落地共享 resolver 和 shared compiler
3. `PR-3`：TSU-07 ~ TSU-08
   - 完成 `aggregate_query` 迁移
4. `PR-4`：TSU-09 ~ TSU-10
   - 完成 `compare_metric` 迁移
5. `PR-5`：TSU-11 ~ TSU-16
   - metadata、pruning、测试、文档、UI、legacy cleanup

---

## 4. 关键风险

- [ ] **R-1** `aggregate_query` 现有 extractor / observed_window 逻辑可能深度依赖旧 `compare_period`
- [ ] **R-2** `compare_metric` 旧测试和 planner 校验大量绑定 `metric_name` / `table_name`
- [ ] **R-3** mixed layout 的 correctness 与 pruning 分离若实现不严谨，容易“能跑但时间语义错”
- [ ] **R-4** 文档面旧示例分布很广，如果不集中清理，仓库会长期处于双契约状态
- [ ] **R-5** timezone 策略若不在 phase 1 写清楚，小时级 compare 容易出现边界误判

---

## 5. 完成定义

满足以下条件才算这项 RFC 真正完成：

- [ ] `compare_metric` 与 `aggregate_query` 都只接受新 contract
- [ ] 两者共享同一套 `time_scope` / `scope` / `time_axis` 心智模型
- [ ] 两者共享同一套时间解析与 compare 编译主干
- [ ] `day` / `hour` 在 partition-only、timestamp-only、mixed 三类布局下都有测试覆盖
- [ ] observation `observed_window.granularity` 与 compare grain 一致
- [ ] planner、service、docs、UI、tests 中不再保留旧字段的正向支持

---

## 6. 建议先做的最小闭环

如果要先拿到一个最小但真实可运行的版本，建议优先完成以下闭环：

1. TSU-01 ~ TSU-06
2. TSU-07 + TSU-09
3. TSU-13.1 ~ TSU-13.5
4. TSU-14.1 ~ TSU-14.4
5. TSU-15.1 + TSU-15.5

这个闭环完成后，系统已经可以：

- 用统一 contract 跑 `aggregate_query`
- 用统一 contract 跑 `compare_metric`
- 产出正确的 observation window
- 在核心文档里不再误导 agent 使用旧字段
