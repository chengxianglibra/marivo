# Semantic Compiler 规范（intent / metric -> IR）

本文定义 Marivo semantic compiler 的落地规范，说明 `intent` 与 `metric` 如何被归一化、校验、展开并编译为 IR。

本文是 compiler-facing design spec，不是 HTTP wire spec，也不是 engine implementation doc。相关背景与上游契约见：

- `specs/semantic/dimension-schema-contract.zh.md`
- `specs/semantic/metric-process-contract.zh.md`
- `specs/semantic/metric-v2-schema.zh.md`
- `specs/semantic/time-schema-contract.zh.md`
- `specs/semantic/ir-schema-contract.zh.md`
- `specs/analysis/intents/primitive-intent-design.md`
- `specs/analysis/intents/derived-intent-design.md`

注意：`typed-binding-contract.zh.md`、`process-object-schema.zh.md`、`compiler-compatibility-profile.zh.md`、`predicate-schema-contract.zh.md`、`enum-set-schema-contract.zh.md` 和 `entity-centric-object-model.zh.md` 已被标记为 SUPERSEDED，本文不再引用其内容作为活跃设计依据。

## Purpose

本文回答以下问题：

- compiler 接收的最小语义输入是什么
- `metric` / `intent` 各自在哪一阶段参与编译
- atomic intent 与 derived intent 分别如何进入 IR
- typed refs、artifact lineage、validation gates 如何进入编译产物
- 哪些组合应被接受，哪些组合应在 compiler 阶段明确拒绝

一句话总结：

> Marivo compiler 的职责不是把请求直接拼成 SQL，而是把类型化分析表达式编译为 validation-aware、lineage-aware 的 IR plan。

## 非目标

本文不定义以下内容：

- HTTP endpoint 形状
- 最终 SQL / DuckDB / Trino 方言细节
- engine adapter 的内部重写算法
- 具体存储 DDL
- 自由形式 planner / template 工作流

## 核心设计结论

compiler 必须保持以下分工：

- `metric` 提供 measurement contract（OSI Metric + MARIVO extensions）
- `dataset` / `field` 提供 dataset-native physical grounding（Dataset.source + Field.expression）
- `relationship` 提供跨数据集连接声明
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
2. metric contract（OSI Metric + MARIVO extensions）
3. dataset / field contracts（由 semantic model 解析得到，包含物理接地信息）
4. catalog metadata / governance context

其中：

- typed intent request 是唯一外部动作入口
- `metric` 是 catalog 中受治理的度量语义对象
- dataset/field 是 compiler 在对象解析阶段补全的 grounding 输入；metric 直接引用 dataset name 和 field name，不通过独立 binding 层
- governance context 包括 quality / freshness / readiness / policy 信息

### 输入槽位

compiler 需要先把请求归入以下槽位之一：

- `metric`
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

其中 `request_dimensions` 应先被归一化为 field names，再结合 dimension 属性与 metric 约束做交叉校验。

## 全局编译规则

所有 intent 在进入 compiler 时都必须遵守以下规则。

### 1. `time_scope` / `scope` 严格分层

- `time_scope` 是唯一时间语义入口
- `scope` 只承载非时间总体约束
- `scope.predicate` 中出现显式时间 SQL 构造必须硬失败；compiler/runtime 不根据
  `event_time`、`log_date` 等列名猜测 predicate 是否为时间条件
- 编译后的 normalized input 必须保留两者分离
- `time_scope` 只表达本次请求的观察范围，不重写 catalog 中的时间语义；合法时间锚点仍由 metric 和 field 属性决定
- compiler 必须把 `time_scope` 解析到单一的 `resolved_filter_time_field`
- `scope.predicate` 若存在，应与 metric filter 对齐为同一受限表达式形态，而不是平行 DSL
- request `scope` 只能进一步收窄 metric filters 已声明的过滤约束；无法证明 narrowing 时应 fail closed

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
- `diagnose.candidate_limit`
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
- intent schema 自洽
- catalog 中不存在明显双重真相字段

若 Phase 0 未完成，Phase 1-5 需要承担更多防御性校验，但不应改变本文的编译边界。

## Phase 1：请求归一化

目标是把意图请求转成统一的 normalized request shape。

### 输出

```ts
type NormalizedRequest = {
  intent_kind: string;
  request_class: "root_metric" | "typed_ref" | "derived_macro";
  metric_name?: string | null;
  upstream_refs?: string[] | null;
  request_scope?: {
    predicate_ref?: string | null;       // 引用声明 request_scope usage 的 predicate.*
    constraints?: Record<string, unknown> | null;  // 请求级 non-time population narrowing
  } | null;
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

#### 1. `root_metric`

直接以 root semantic anchors 发起，例如：

- `observe(metric, time_scope, scope, ...)`
- `diagnose(metric, time_scope, scope, ...)`

#### 2. `typed_ref`

消费上游 canonical artifacts，例如：

- `compare(left_artifact_id, right_artifact_id)`
- `detect(source_artifact_id, sensitivity?, limit?)`
- `decompose(compare_artifact_id, dimension)`
- `correlate(left_artifact_id, right_artifact_id)`
- `test(current_sample_artifact_id, baseline_sample_artifact_id, hypothesis)`
- `forecast(source_artifact_id, ...)`
- 当前 AOI runtime 请求使用 `*_artifact_id` 字段；上述 typed refs 是输出
  lineage / compiler IR 中的语义绑定，不是 HTTP/MCP 请求形状。

#### 3. `derived_macro`

外部请求不直接包含内部 refs，但编译后会展开出 atomic refs，例如：

- `attribute`
- `diagnose`
- `validate`

## Phase 2：对象解析与 normalized contracts 生成

这一步把 catalog 对象转成 compiler 可消费的统一 contract。

### metric -> normalized measurement contract

最少应抽取：

- `metric_name`（OSI name）
- `additivity`（additive_dimensions, decomposition_semantics）
- `filters`

以下能力由 compiler 从核心字段推导：

| 推导能力 | 推导规则 |
|---------|---------|
| `supports_observe` | 始终为 true |
| `supports_compare` | 始终为 true |
| `supports_decompose` | `additive_dimensions` 非空 |
| `supports_attribute` | `supports_compare` AND `supports_decompose` |
| `supports_detect` | process 的 `anchor_time_ref` 存在 |

注：时间语义通过 AOI `time_scope.field` 在请求时解析，不作为 metric extension 字段。

### dataset/field -> normalized grounding contract

物理接地上内联在 Dataset 和 Field 中，不需要独立的 binding 解析。最少应抽取：

- `dataset_name`（OSI name）
- `source`（关系 FQN）
- `datasource_id`（MARIVO extension）
- 引用的 field name 及其 `expression`、`is_time`、`data_type`
- relationship 连接声明（from/to dataset, column pairs, cardinality）

### dimension -> normalized dimension contract

Dimension 作为 Field 属性表达，不再是独立对象。compiler 抽取：

- field name（在 dataset 中声明 dimension 属性）
- `is_time`（是否为时间维度）
- `data_type`

### intent request -> normalized action contract

最少应抽取：

- `intent_kind`
- root / ref / derived 类型
- 输入槽位绑定
- `resolved_filter_time_ref`
- 输出 artifact kind
- 受控默认值解析策略
- 该 intent 的硬失败条件与软告警面

### relationship -> normalized join contract

最少应抽取：

- `relationship_name`
- `from_dataset` / `to_dataset`
- `from_columns` / `to_columns`
- `cardinality`

### intent request -> normalized action contract

## Phase 3：组合期校验链

这一阶段是 semantic compiler 的主 gate。

建议按以下顺序执行。

### Gate 1：请求形状校验

校验：

- 必填字段是否存在
- 枚举是否合法
- `scope` 是否含非法时间条件
- `qualifier_refs` / `default_predicate_refs` / `scope.predicate_ref` 引用的 filter 是否可解析
- filter effective scope 合成是否违反扁平化禁止规则
- 结果模式与 shape 组合是否非法
- ref 类型是否匹配当前 intent

### Gate 2：intent 支持校验

校验：

- metric 是否支持该 intent（由 compiler 从 `additive_dimensions`、`decomposition_semantics` 推导）
- dataset grounding 是否足以支撑该 intent
- ref artifact 类型是否允许被该 intent 消费

若 compiler 无法从 object contract 推导某条关键规则，且该 intent 明确依赖它，应返回 `DATASET_GROUNDING_MISSING`。

### Gate 3：dataset-grounding compatibility gate

重点校验：

- dataset 的 `source` 和 `datasource_id` 是否可解析
- metric expression 是否能基于 dataset relationship graph 做解析/试跑
- AOI `time_scope.field` 引用的 field 是否声明了 `is_time: true`（时间语义由 AOI `time_scope.field` 解析，编译时校验该 field 存在于 dataset 中且具有 `is_time: true`）
- 跨 dataset 查询是否有 relationship 连接声明
- datasource 是否可达（readiness 检查）

### Gate 5：dimension compatibility gate

重点校验：

- `request_dimensions` 中的 field names 是否存在于 metric 可通过 relationship graph 访问的 dataset 中
- 对 `metric` 发起的请求中，dimensions 必须属于 metric 可消费的 field 集合
- 跨 dataset 的 dimension 引用必须有 relationship 连接声明
- 只有声明了 dimension 属性的 field 才能进入通用分组位

### Gate 5.5：filter contract gate

校验：

- metric `filters` 中的表达式是否可解析
- filter expression 是否 deterministic、non-time
- request scope 是否只收窄 metric 已声明的过滤约束
- 跨层 filter 冲突检测

### Gate 6：intent-specific semantic gate

例如：

- `compare` 的 comparability gate
- `decompose` / `attribute` 的 additivity gate
- `test` / `validate` 的 inference gate
- `detect` / `forecast` 的 time projection gate

## Gate 7：governance gate

包括：

- quality gate
- freshness gate
- policy gate

### Gate 8：lowerability precheck

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
3. `compare(left_artifact_id, right_artifact_id)`
4. 对 `dimensions` 按请求顺序逐个展开：
   - `decompose(compare_artifact_id, dimension, limit)`

关键 gate：

- `left` / `right` 必须能归一化为 scalar observe
- metric 必须满足 compare + decompose 兼容性
- dimension 必须可归因，且满足 grouping / time-anchor / governance 约束
- `decomposition_limit` 必须有界

### `diagnose`

外部输入锚点：

- `metric`
- detect 风格 `time_scope`
- `filter`
- `scan_dimension`：detect 阶段的单维扫描拆分轴；省略表示扫描整体序列
- `dimensions`：候选发现后的归因维度列表；与 `scan_dimension` 相互独立
- `strategy`
- `sensitivity`
- `candidate_limit`：端到端跟进诊断的异常候选数量
- `decomposition_limit`：每个候选、每个归因维度返回的 driver rows 数量

展开为：

1. `detect(...)`
2. 按 detect canonical order 选择前 `candidate_limit` 个 candidates
3. 对每个 candidate：
   - 推导 `current_window = candidate.window`
   - 推导 `baseline_window = previous_adjacent_equal_length(current_window)`
   - 组合 `combined_scope = request.filter + candidate.slice`
   - `observe(current scalar)`
   - `observe(baseline scalar)`
   - `compare(current_artifact_id, baseline_artifact_id)`
   - 对 `dimensions` 按请求顺序逐个展开：
     - `decompose(compare_artifact_id, dimension, limit)`

关键 gate：

- detect fan-out 必须有界
- baseline policy 必须固定可推导
- compare 必须可比
- decompose 必须可归因，并满足 grouping / time-anchor / governance 约束

### `validate`

外部输入锚点：

- `metric`
- `current(time_scope, filter?)`
- `baseline(time_scope, filter?)`
- `granularity`
- `hypothesis`

展开为：

1. `observe(current, granularity)` 生成当前 `metric_frame`
2. `observe(baseline, granularity)` 生成基准 `metric_frame`
3. `sample_summary(current_metric_frame)` 生成当前 `sample_frame`
4. `sample_summary(baseline_metric_frame)` 生成基准 `sample_frame`
5. `test(current_sample_artifact_id, baseline_sample_artifact_id, hypothesis)`

关键 gate：

- `granularity` 只作用于上游 `observe`；`sample_summary` 继承 `metric_frame` 轴，不接收独立 `grain`
- 左右侧必须生成可检验的 time-series `metric_frame`
- `test` 的 canonical 输入是两个 `sample_frame` artifact refs，不读取 semantic metric，也不在内部生成 sample summary

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
- `IntentNode`

同时还必须显式产出：

- `IrArtifact`
- `InputBinding` / `OutputBinding`
- `CompileReport`

### 最小装配映射

#### root intent

典型映射：

- 一个或多个 `MeasurementNode`
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

- root anchors：`metric`
- request bindings：`time_scope`、`scope`、`result_mode`、`granularity`、`dimensions`

### 编译要求

- 解析 metric measurement contract
- 根据 `result_mode + granularity + dimensions` 判定 observation shape
- 生成 `IntentNode(intent_kind="observe")`
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
- 校验 metric `additive_dimensions`（非空列表表示可在列出维度上分解，空列表表示不可分解）和 `decomposition_semantics`
- 校验 dimension 可归因性
- 不得依赖 dimension 自声明 `supports_decomposition` 一类跨对象标签；合法性必须由 metric 与 dimension 属性联合推导
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

- 两侧都必须解析为同形状 canonical `metric_frame`（`scalar`、`time_series`、`segmented` 或 `panel`）
- 必须做 alignment gate
- `min_pairs` 是合法性阈值，而不是任意 tuning knob

## `detect`

### 输入锚点

- typed ref anchors：`source_artifact_id`
- request bindings：`sensitivity`、`limit`

### 编译要求

- `source_artifact_id` 必须解析为已提交 artifact
- 支持 `metric_frame(time_series | panel)` 与 `delta_frame(time_series_delta | panel_delta)`
- 检测策略由 artifact family/shape 推导，不接受请求级 `strategy`
- `metric`、`time_scope`、`scope`、`split_by`、`max_series` 不属于 atomic detect 编译输入

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

- typed artifact ID：`source_artifact_id`
- request bindings：`horizon`

### 编译要求

- source 必须是 canonical `metric_frame(time_series|panel)`
- 需要 metric 的 time projection 能力
- horizon 必须有界且在系统上限内
- `source_artifact_id` 解析出的 observe artifact 必须通过显式 `InputBinding` 进入当前节点

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
- `predicate_usage_mismatch`
- `predicate_scope_flattening`
- `predicate_narrowing_unprovable`
- `unsupported_result_mode`
- `metric_intent_not_supported`
- `dataset_grounding_not_satisfied`
- `grain_mismatch`
- `comparability_failed`
- `inference_not_supported`
- `sample_kind_ambiguous`
- `time_projection_not_supported`
- `non_additive_metric_not_decomposable`
- `COMPILER_DIMENSION_NOT_EXPORTED`
- `COMPILER_DIMENSION_IMPORT_MISSING`
- `COMPILER_DIMENSION_IMPORT_AMBIGUOUS`
- `unsupported_ref_type`
- `lowering_path_unavailable`

其中 dimension 相关错误的归因固定为：

- `COMPILER_DIMENSION_NOT_EXPORTED`：`dimension.*` 已存在，但不属于当前 metric 的自身导出集合，也不属于 imported bridge 集合
- `COMPILER_DIMENSION_IMPORT_MISSING`：请求依赖 dimension bridge，但当前 metric/process 组合无法确定可提供该 dimension 的 entity field 来源
- `COMPILER_DIMENSION_IMPORT_AMBIGUOUS`：同一 `dimension_ref` 存在多个可用 entity field 来源，compiler 无法唯一确定 bridge lineage

## 典型编译样例

## 例 1：`conversion_rate + experiment_context + validate`

编译结果至少应包含：

- `MeasurementNode(metric_name="conversion_rate")`
- 顶层 `IntentNode(intent_kind="validate")`
- 两个内部 `IntentNode(intent_kind="observe")`
- 一个内部 `IntentNode(intent_kind="test")`
- 左右 observation artifact、test artifact 与对应 bindings
- `CompileReport.validation_trace` 中的 `inference_gate`

## 例 2：`retention_rate + cohort_definition + compare`

编译结果至少应包含：

- 左右两侧 dataset/field resolution
- `MeasurementNode(metric_name="retention_rate")`
- `IntentNode(intent_kind="compare")`
- `left_ref` / `right_ref` 的显式 bindings
- `CompileReport.validation_trace` 中的 `comparability_gate`

## 例 3：`metric_frame artifact + detect`

编译结果至少应包含：

- `ArtifactRefNode(artifact_id="art_metric_frame_123")`
- `IntentNode(intent_kind="detect")`
- `source_artifact_id` 的显式 binding
- 输出 `candidate_set` artifact
- `CompileReport.validation_trace` 中的 `lowerability_precheck`

## 与现有文档的关系

- `metric-v2-schema.zh.md` 负责 measurement contract
- `metric-process-contract.zh.md` 负责 metric 分工原则
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

Marivo compiler 的规范落点应是：

- 先把 typed intent request 归一化
- 再把 `metric` / `dataset/field` 解析为 normalized contracts
- 通过显式 gates 校验组合合法性
- 对 derived intent 做确定性 atomic expansion
- 最后装配为 validation-aware、lineage-aware 的 IR

一句话总结：

> 在 Marivo 中，compiler 不是 SQL 生成器的前置包装层，而是 typed analysis expression 到 IR 的语义编译器。
