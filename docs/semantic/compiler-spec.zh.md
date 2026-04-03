# Semantic Compiler 规范（intent / metric / process -> IR）

本文定义 Factum semantic compiler 的落地规范，说明 `intent`、`metric` 与 `process object` 如何被归一化、校验、展开并编译为 IR。

本文是 compiler-facing design spec，不是 HTTP wire spec，也不是 engine implementation doc。相关背景与上游契约见：

- `docs/semantic/metric-process-contract.zh.md`
- `docs/semantic/metric-v2-schema.zh.md`
- `docs/semantic/process-object-schema.zh.md`
- `docs/semantic/ir-schema-contract.zh.md`
- `docs/analysis/intents/primitive-intent-design.md`
- `docs/analysis/intents/derived-intent-design.md`

## Purpose

本文回答以下问题：

- compiler 接收的最小语义输入是什么
- `metric` / `process object` / `intent` 各自在哪一阶段参与编译
- atomic intent 与 derived intent 分别如何进入 IR
- typed refs、artifact lineage、validation gates 如何进入编译产物
- 哪些组合应被接受，哪些组合应在 compiler 阶段明确拒绝

一句话总结：

> Factum compiler 的职责不是把请求直接拼成 SQL，而是把类型化分析表达式编译为 validation-aware、lineage-aware 的 IR plan。

## 非目标

本文不定义以下内容：

- HTTP endpoint 形状
- 最终 SQL / DuckDB / Trino 方言细节
- engine adapter 的内部重写算法
- 具体存储 DDL
- 自由形式 planner / template 工作流

## 核心设计结论

compiler 必须保持以下分工：

- `metric` 提供 measurement contract
- `process object` 提供 process/interface contract
- `intent` 提供 analysis action contract
- compiler 负责 normalization、validation、expansion、IR assembly
- engine adapter 负责 lowering、capability matching 与执行

这意味着：

1. 外部 contract 以 typed intent schema 为准，而不是 SQL shape。
2. compiler 必须先完成语义校验，再进入 engine-specific plan。
3. IR 必须承接的是“分析动作语义”，不是“SQL 结构语义”。

## 编译输入模型

一次编译的输入由四部分组成：

1. typed intent request
2. metric contract
3. process contract（可为单侧、双侧或空）
4. catalog / governance context

其中：

- typed intent request 是唯一外部动作入口
- `metric` 与 `process object` 是 catalog 中受治理的语义对象
- governance context 包括 quality / freshness / engine capability / policy 信息

### 输入槽位

compiler 需要先把请求归入以下槽位之一：

- `metric`
- `process`
- `left_process`
- `right_process`
- `upstream_refs`
- `request_scope`
- `request_time_scope`
- `request_dimensions`
- `request_result_mode`

并非每个 intent 都会填满所有槽位。

### 三类输入锚点

按 compiler 视角，intent 的请求锚点只有三类：

### 1. Root semantic anchors

直接从 catalog 解析：

- `metric`
- `process`
- `left_process`
- `right_process`

### 2. Request-time bindings

由本次请求提供：

- `time_scope`
- `scope`
- `dimensions`
- `granularity`
- `result_mode`
- method / profile / sensitivity / limit 等受控语义参数

### 3. Typed upstream refs

从上游 canonical artifact 继承：

- `ObservationRef`
- `CompareRef`
- `DetectCandidateRef`
- 其他 canonical typed refs

compiler 必须优先复用 typed refs，而不是在下游请求里重建上游 `metric + scope + time_scope`。

## 全局编译规则

所有 intent 在进入 compiler 时都必须遵守以下规则。

### 1. `time_scope` / `scope` 严格分层

- `time_scope` 是唯一时间语义入口
- `scope` 只承载非时间总体约束
- `scope.predicate` 中出现时间条件必须硬失败
- 编译后的 normalized input 必须保留两者分离

### 2. artifact 与 projection 严格分层

- compiler 生成和引用的对象必须是 canonical artifact
- projection 只能由 artifact 确定性派生
- projection 永远不是下游 typed ref 的合法目标

### 3. typed refs 是组合边界

- atomic 组合优先通过 typed refs 完成
- derived intent 的内部展开也必须连到 canonical artifact refs
- 下游不得通过 projection 或自由 scope 重建上游语义

### 4. validation 是编译的一部分

compiler 不得把语义不兼容问题留到 engine 层暴露为 SQL 错误。至少应在 compiler 阶段返回：

- typed semantic error
- machine-readable reason code
- 明确的失败 gate

### 5. 有界输出规则进入编译

只要 intent 有界输出会影响 artifact semantics 或 follow-up fan-out，相关参数就属于 compiler 输入，而不是纯展示选项。例如：

- `detect.max_series`
- `detect.limit`
- `diagnose.followup_limit`
- `attribute.decomposition_limit`
- `forecast.horizon`

### 6. derived intent 必须确定性展开

给定相同请求与相同 catalog 状态，derived intent 必须展开为同一条 atomic DAG。若中途需要“看结果再决定下一步”，则不属于 compiler 可执行 intent。

## Compiler phases

建议编译流程显式分为六个阶段。

## Phase 0：发布期校验

这一步在单次请求之外执行，但 compiler 可以依赖其结果。

需要保证：

- metric contract 自洽
- process object subtype 与 `interface_contract` 自洽
- intent schema 自洽
- catalog 中不存在明显双重真相字段

若 Phase 0 未完成，Phase 1-5 需要承担更多防御性校验，但不应改变本文的编译边界。

## Phase 1：请求归一化

目标是把意图请求转成统一的 normalized request shape。

### 输出

```ts
type NormalizedRequest = {
  intent_kind: string;
  request_class: "root_metric_process" | "typed_ref" | "derived_macro";
  metric_ref?: string | null;
  process_ref?: string | null;
  left_process_ref?: string | null;
  right_process_ref?: string | null;
  upstream_refs?: string[] | null;
  request_scope?: object | null;
  request_time_scope?: object | null;
  request_dimensions?: string[] | null;
  request_result_mode?: string | null;
  request_options?: Record<string, unknown> | null;
};
```

### 归一化要求

- 省略值必须补成单一语义默认值
- 列表字段必须去重并保持稳定顺序
- 左右侧字段必须保留请求顺序
- `auto` 值必须保留到可确定解析的阶段，不能过早猜测

### request class 划分

#### 1. `root_metric_process`

直接以 root semantic anchors 发起，例如：

- `observe(metric, process?, time_scope, scope, ...)`
- `detect(metric, process?, time_scope, scope, ...)`

#### 2. `typed_ref`

消费上游 canonical artifacts，例如：

- `compare(left_ref, right_ref)`
- `decompose(compare_ref, dimension)`
- `correlate(left_ref, right_ref)`
- `test(left_ref, right_ref, hypothesis, method)`
- `forecast(source_ref, ...)`

#### 3. `derived_macro`

外部请求不直接包含内部 refs，但编译后会展开出 atomic refs，例如：

- `attribute`
- `diagnose`
- `validate`

## Phase 2：对象解析与 normalized contracts 生成

这一步把 catalog 对象转成 compiler 可消费的统一 contract。

### metric -> normalized measurement contract

最少应抽取：

- `metric_ref`
- `metric_family`
- `observed_entity_ref`
- `observation_grain_ref`
- `sample_kind`
- `value_semantics`
- `additivity`
- `supported_intents`
- `supported_result_modes`
- `comparability_group`
- `required_dimensions`
- `forbidden_dimensions`
- `required_process_contract`
- `inferential_capabilities`
- `quality_gate_refs`
- `freshness_tolerance`

### process object -> normalized process contract

最少应抽取：

- `process_ref`
- `process_type`
- `contract_mode`
- `population_subject_ref`
- `context_kind` 或 `entity_ref`
- `emitted_grain_ref`（若 `contract_mode = entity_stream`）
- `membership_cardinality` 或 `subject_cardinality`
- `anchor_time_ref`
- `exported_dimension_refs`
- `provided_capabilities`
- `comparison_contract`
- `time_projection_support`
- `inference_support`
- request-time bindings 应用后的实例化结果

### intent request -> normalized action contract

最少应抽取：

- `intent_kind`
- root / ref / derived 类型
- 输入槽位绑定
- 输出 artifact kind
- 受控默认值解析策略
- 该 intent 的硬失败条件与软告警面

## Phase 3：组合期校验链

这一阶段是 semantic compiler 的主 gate。

建议按以下顺序执行。

### Gate 1：请求形状校验

校验：

- 必填字段是否存在
- 枚举是否合法
- `scope` 是否含非法时间条件
- 结果模式与 shape 组合是否非法
- ref 类型是否匹配当前 intent

### Gate 2：intent 支持校验

校验：

- metric 是否支持该 intent
- process contract 是否足以支撑该 intent
- ref artifact 类型是否允许被该 intent 消费

### Gate 3：metric-process 兼容校验

重点校验：

- `required_process_contract` 是否被满足
- `population_subject_ref` 是否兼容
- `context_kind` / `entity_ref` 是否兼容
- `observation_grain_ref` / `emitted_grain_ref` 是否兼容（若 process 暴露实体流）
- comparison / inference / time projection 能力是否匹配

### Gate 4：intent-specific semantic gate

例如：

- `compare` 的 comparability gate
- `decompose` / `attribute` 的 additivity gate
- `test` / `validate` 的 inference gate
- `detect` / `forecast` 的 time projection gate

### Gate 5：governance gate

包括：

- quality gate
- freshness gate
- policy gate

### Gate 6：lowerability precheck

仅校验“是否存在可 lower 的路径”，不执行真正 lowering，也不把目标引擎决策写回 Semantic IR。

## Phase 4：derived intent expansion

只有 `derived_macro` 类型会进入这一阶段。

输出是一个确定性的 atomic DAG 模板，其中每个节点都指向 canonical atomic intent contract。

### expansion 规则

- 内部步骤创建顺序必须稳定
- typed refs 的 wiring 必须稳定
- 左右侧语义必须继承外部请求顺序
- 排序 / 截断 / follow-up 规则必须固定
- expansion 不得引入 ad hoc planner 决策

### `attribute`

外部输入锚点：

- `metric`
- `left(time_scope, scope)`
- `right(time_scope, scope)`
- `dimensions`
- `decomposition_method`
- `decomposition_limit`

展开为：

1. `observe(left scalar)`
2. `observe(right scalar)`
3. `compare(left_ref, right_ref, mode="scalar")`
4. 对 `dimensions` 按请求顺序逐个展开：
   - `decompose(compare_ref, dimension, method, limit)`

关键 gate：

- `left` / `right` 必须能归一化为 scalar observe
- metric 必须满足 compare + decompose 兼容性
- dimension 必须可归因
- `decomposition_limit` 必须有界

### `diagnose`

外部输入锚点：

- `metric`
- detect 风格 `time_scope`
- `scope`
- `detect_split_by`
- `candidate_dimensions`
- `profile`
- `sensitivity`
- `candidate_limit`
- `followup_limit`
- `decomposition_limit`

展开为：

1. `detect(...)`
2. 按 detect canonical order 选择前 `followup_limit` 个 candidates
3. 对每个 candidate：
   - 推导 `current_window = candidate.window`
   - 推导 `baseline_window = previous_adjacent_equal_length(current_window)`
   - 组合 `combined_scope = request.scope + candidate.slice`
   - `observe(current scalar)`
   - `observe(baseline scalar)`
   - `compare(current_ref, baseline_ref, mode="scalar")`
   - 对 `candidate_dimensions` 按请求顺序逐个展开：
     - `decompose(compare_ref, dimension, limit)`

关键 gate：

- detect fan-out 必须有界
- baseline policy 必须固定可推导
- compare 必须可比
- decompose 必须可归因

### `validate`

外部输入锚点：

- `metric`
- `left(time_scope, scope)`
- `right(time_scope, scope)`
- `sample_kind`
- `hypothesis`
- `method`

展开为：

1. 解析 inferential summary mode
2. `observe(left, result_mode=inferred_summary_mode)`
3. `observe(right, result_mode=inferred_summary_mode)`
4. `test(left_ref, right_ref, hypothesis, method)`

关键 gate：

- `sample_kind = auto` 必须唯一可解，否则失败
- 左右侧必须都可归一化为 inferential-ready scalar observe
- method 兼容性完全继承 `test`

## Phase 5：IR 装配

在所有 gates 通过、必要的 derived expansion 完成之后，compiler 装配 Semantic IR 与 compile report。

### IR 装配原则

1. 每个计划必须保留 typed input snapshot。
2. 每个语义职责进入固定 node family。
3. artifact 与 slot binding 必须是一等结构，而不是只靠 `depends_on`。
4. validation 决策必须进入 compile report，而不是塞成失败节点。
5. engine-specific lowering 决策必须留在 lowering report，而不是写进 IR。

### Node families

compiler 至少需要装配以下语义节点：

- `MeasurementNode`
- `ProcessNode`
- `IntentNode`

同时还必须显式产出：

- `IrArtifact`
- `InputBinding` / `OutputBinding`
- `CompileReport`

### 最小装配映射

#### root intent

典型映射：

- 一个或多个 `MeasurementNode`
- 零个、一个或多个 `ProcessNode`
- 至少一个 `IntentNode`
- 零个或多个输出 `IrArtifact`
- 一份 `CompileReport`

#### ref-based atomic intent

除了本次 intent 自身的 `IntentNode` 外，还必须在 typed input snapshot 与 bindings 中保留上游 artifact lineage：

- source artifact ids
- source step refs
- source normalized semantic summary
- 当前节点消费这些 artifact 时的 slot 语义

但 compiler 不应把上游 artifact 内容复制为新的 public contract。

#### derived intent

derived intent 需要两层表达：

1. 顶层派生 `IntentNode`
2. 内部 atomic expansion nodes

建议保留：

- 派生层 `IntentNode(intent_kind="attribute" | "diagnose" | "validate", intent_level="root")`
- 每个内部 atomic intent 的 `IntentNode(intent_level="expanded_atomic")`
- wiring 到内部 canonical refs 的 artifact / binding 关系

这样既能保留“用户请求的是派生动作”的语义，也能审计其 atomic 展开。

## 各 intent 的编译规则

下面给出编译器必须遵守的每意图规则。

## `observe`

### 输入锚点

- root anchors：`metric`，可选 `process`
- request bindings：`time_scope`、`scope`、`result_mode`、`granularity`、`dimensions`

### 编译要求

- 解析 metric measurement contract
- 若存在 `process`，则将其解析为 process-aware observation context
- 根据 `result_mode + granularity + dimensions` 判定 observation shape
- 生成 `IntentNode(intent_kind="observe")`
- 若存在 process-aware logic，生成 `ProcessNode`
- 在需要时生成 inferential summary 的 `MeasurementNode.inferential_summary_mode`

### artifact 结果

`observe` 产出的 canonical artifact 必须可被以下下游消费：

- `compare`
- `correlate`
- `test`
- `forecast`
- derived intents 的内部 expansion

## `compare`

### 输入锚点

- typed refs：`left_ref`、`right_ref`

### 编译要求

- 解析左右 observation artifact lineage
- 继承左/右 measurement semantics
- 执行 comparability gate
- 生成 `IntentNode(intent_kind="compare")`
- 在 `CompileReport.validation_trace` 中记录 `comparability_gate`
- 为 `left_ref` / `right_ref` 生成显式 `InputBinding`

### 禁止行为

- 不得接受新的平行 `metric/time_scope/scope` 输入
- 不得通过 ref 之外的临时输入重建 comparison sides

## `decompose`

### 输入锚点

- typed ref：`compare_ref`
- request bindings：`dimension`、`method`

### 编译要求

- 解析 compare artifact 的 delta semantics
- 校验 metric `additivity`
- 校验 dimension 可归因性
- 生成 `IntentNode(intent_kind="decompose")`
- 在 `CompileReport.validation_trace` 中记录相关 gates
- 为 `compare_ref` 生成显式 `InputBinding`

### 禁止行为

- 不得绕过 compare 直接接收两侧 scopes
- 不得把 segmented compare 当作 v1 decompose 主输入

## `correlate`

### 输入锚点

- typed refs：`left_ref`、`right_ref`
- request bindings：`method`、`min_pairs`

### 编译要求

- 两侧都必须解析为 canonical `observe(time_series)`
- 必须做 alignment gate
- `min_pairs` 是合法性阈值，而不是任意 tuning knob

## `detect`

### 输入锚点

- root anchors：`metric`，可选 `process`
- request bindings：detect 风格 `time_scope`、`scope`、`split_by`、`profile`、`sensitivity`、`limit`、`max_series`

### 编译要求

- 需要 process/metric 联合判断是否支持稳定时间投影
- 需要在编译阶段确定 scan surface 是否有界
- `split_by` 是扫描轴，不是 filter scope

## `test`

### 输入锚点

- typed refs：`left_ref`、`right_ref`
- request bindings：`hypothesis`、`method`

### 编译要求

- 左右 ref 必须是 inferential-ready observe artifacts
- auto method 解析必须稳定
- 需要 inference gate + sample sufficiency gate
- 两个输入都必须通过 typed `InputBinding` 标识为 `test_left` / `test_right`

## `forecast`

### 输入锚点

- typed ref：`source_ref`
- request bindings：`horizon`、`profile`、`interval_level`

### 编译要求

- source 必须是 canonical `observe(time_series)`
- 需要 process/metric 联合判断 time projection 能力
- horizon 必须有界且在系统上限内
- `source_ref` 必须通过显式 `InputBinding` 进入当前节点

## 错误语义

compiler 应返回 typed semantic errors，而不是 engine errors。

建议最少包含：

```ts
type CompilerError = {
  phase:
    | "request_normalization"
    | "object_resolution"
    | "compatibility_validation"
    | "derived_expansion"
    | "ir_assembly"
    | "lowering_precheck";
  reason_code: string;
  message: string;
  details?: Record<string, unknown> | null;
};
```

### 常见错误类别

- `invalid_scope_shape`
- `time_predicate_in_scope`
- `unsupported_result_mode`
- `metric_intent_not_supported`
- `process_contract_not_satisfied`
- `grain_mismatch`
- `comparability_failed`
- `inference_not_supported`
- `sample_kind_ambiguous`
- `time_projection_not_supported`
- `non_additive_metric_not_decomposable`
- `unsupported_ref_type`
- `lowering_path_unavailable`

## 典型编译样例

## 例 1：`conversion_rate + experiment_context + validate`

编译结果至少应包含：

- `MeasurementNode(sample_kind="rate", inferential_summary_mode="rate_sample_summary")`
- `ProcessNode(provided_capabilities` 至少覆盖 `variant_split`、`population_membership`、`sample_summary_prep`)`
- 顶层 `IntentNode(intent_kind="validate")`
- 两个内部 `IntentNode(intent_kind="observe")`
- 一个内部 `IntentNode(intent_kind="test")`
- 左右 observation artifact、test artifact 与对应 bindings
- `CompileReport.validation_trace` 中的 `inference_gate`

## 例 2：`retention_rate + cohort_definition + compare`

编译结果至少应包含：

- 左右两侧 process resolution（若 compare 直接消费 refs，则这部分体现在上游 observe lineage 中）
- `MeasurementNode(sample_kind="survival" | "rate"，取决于具体 metric contract)`
- `IntentNode(intent_kind="compare")`
- `left_ref` / `right_ref` 的显式 bindings
- `CompileReport.validation_trace` 中的 `comparability_gate`

## 例 3：`avg_watch_time + session_contract + detect`

编译结果至少应包含：

- `MeasurementNode(sample_kind="numeric")`
- `ProcessNode(provided_capabilities` 至少覆盖 `session_partition`、`time_projection`)`
- `IntentNode(intent_kind="detect")`
- 输出 time-series artifact
- `CompileReport.validation_trace` 中的 `lowerability_precheck`

## 与现有文档的关系

- `metric-v2-schema.zh.md` 负责 measurement contract
- `process-object-schema.zh.md` 负责 process contract 与 interface contract
- `metric-process-contract.zh.md` 负责三层分工原则
- `ir-schema-contract.zh.md` 负责 Semantic IR、artifact/binding 模型，以及 compile/lowering 分层
- 本文负责“它们如何组合成 compiler 行为”

换句话说：

- 上游文档定义对象
- intent 文档定义动作
- 本文定义编译逻辑
- IR 文档定义编译目标

## v1 Scope Limits

本文只覆盖以下 v1 编译子集：

- HTTP-only typed intent 系统
- 原子意图与已定义的派生意图
- canonical artifact refs 组合
- 确定性 expansion
- Semantic IR assembly + compile report

本文明确不覆盖：

- 模板型开放式探索工作流
- 任意多跳 agent planner
- SQL 作为外部 contract
- 非确定性 candidate picking / dimension picking
- projection 作为 typed input target

## 总结

Factum compiler 的规范落点应是：

- 先把 typed intent request 归一化
- 再把 `metric` / `process object` 解析为 normalized contracts
- 通过显式 gates 校验组合合法性
- 对 derived intent 做确定性 atomic expansion
- 最后装配为 validation-aware、lineage-aware 的 IR

一句话总结：

> 在 Factum 中，compiler 不是 SQL 生成器的前置包装层，而是 typed analysis expression 到 IR 的语义编译器。
