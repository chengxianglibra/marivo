# Semantic Compiler 规范（intent / metric / process -> IR）

本文定义 Marivo semantic compiler 的落地规范，说明 `intent`、`metric` 与 `process object` 如何被归一化、校验、展开并编译为 IR。

本文是 compiler-facing design spec，不是 HTTP wire spec，也不是 engine implementation doc。相关背景与上游契约见：

- `docs/semantic/dimension-schema-contract.zh.md`
- `docs/semantic/enum-set-schema-contract.zh.md`
- `docs/semantic/metric-process-contract.zh.md`
- `docs/semantic/metric-v2-schema.zh.md`
- `docs/semantic/predicate-schema-contract.zh.md`
- `docs/semantic/process-object-schema.zh.md`
- `docs/semantic/compiler-compatibility-profile.zh.md`
- `docs/semantic/time-schema-contract.zh.md`
- `docs/semantic/typed-binding-contract.zh.md`
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

- `metric` 提供 measurement contract
- `process object` 提供 process/interface contract
- `binding` 同时提供 semantic grounding 与 physical carrier contract
- `intent` 提供 analysis action contract
- compiler 负责 normalization、validation、expansion、IR assembly
- engine adapter 负责 lowering、capability matching 与执行

这意味着：

1. 外部 contract 以 typed intent schema 为准，而不是 SQL shape。
2. compiler 必须先完成语义校验，再进入 engine-specific plan。
3. IR 必须承接的是“分析动作语义”，不是“SQL 结构语义”。

## 编译输入模型

一次编译的输入由五部分组成：

1. typed intent request
2. metric contract
3. process contract（可为单侧、双侧或空）
4. binding contracts（由 catalog 解析得到，内部包含 carrier 信息）
5. catalog metadata / governance context / optional compiler profiles

其中：

- typed intent request 是唯一外部动作入口
- `metric` 与 `process object` 是 catalog 中受治理的语义对象
- `binding` 是 compiler 在对象解析阶段补全的 grounding 输入，并携带所需 carrier metadata
- governance context 包括 quality / freshness / engine capability / policy 信息
- compiler compatibility profile 的 envelope、发布形态与缺失时语义由 `docs/semantic/compiler-compatibility-profile.zh.md` 定义
- object public contract 与 compiler compatibility profile 应显式分层，不能混作同一份 schema

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

其中 `request_dimensions` 应先被归一化为 `dimension.*`，再结合 dimension contract、metric 约束、process 导出接口与 entity 稳定属性接口做交叉校验。

## 全局编译规则

所有 intent 在进入 compiler 时都必须遵守以下规则。

### 1. `time_scope` / `scope` 严格分层

- `time_scope` 是唯一时间语义入口
- `scope` 只承载非时间总体约束
- `scope.predicate` 中出现时间条件必须硬失败
- 编译后的 normalized input 必须保留两者分离
- `time_scope` 只表达本次请求的观察范围，不重写 catalog 中的 `time.*` taxonomy；合法时间锚点仍由 metric / process / dimension contract 决定
- compiler 必须把 `time_scope` 解析到单一的 `resolved_filter_time_ref`，但同时保留 metric / process / window 各自的时间引用
- `scope.predicate` 若存在，应与 `predicate.*` contract 对齐为同一受限表达式形态，而不是平行 DSL
- request `scope` 只能进一步收窄 metric / binding / governance 已声明的过滤约束；无法证明 narrowing 时应 fail closed

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
- `population_subject_ref`
- `observed_entity_ref`
- `observation_grain_ref`
- `primary_time_ref`
- `sample_kind`
- `value_semantics`
- `additivity_constraints`

以下能力由 compiler 从核心字段推导，不进入 profile：

| 推导能力 | 推导规则 |
|---------|---------|
| `supports_observe` | 始终为 true |
| `supports_compare` | `additivity_constraints` 存在 AND `primary_time_ref exists` |
| `supports_test` | `sample_kind in ["numeric", "rate", "binary"]` |
| `supports_decompose` | `additivity_constraints.dimension_policy` in ["all", "subset"] |
| `supports_detect` | `anchor_time_ref exists` in process OR metric |
| `supports_validate` | `sample_kind == "rate"` AND `anchor_time_ref exists` in process |

若需要声明对 process 的编译期要求（如 `contract_modes`、`context_kinds`），应通过独立的 compiler compatibility profile 发布，见 `docs/semantic/compiler-compatibility-profile.zh.md`。
profile 继续采用显式登记策略；对象 publish 不自动生成 profile，profile publish 会冻结当前 subject revision，供 compiler 在消费时执行 revision 一致性校验。

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

以下能力由 compiler 从核心字段推导，不进入 profile：

| 推导能力 | 推导规则 |
|---------|---------|
| `supports_time_projection` | `anchor_time_ref exists` |
| `supports_experiment_inference` | `context_kind == "experiment_split"` |
| `supports_cohort_inference` | `context_kind == "cohort_membership"` |

若需要声明 inferential 能力（如 `inferential_ready`、`supported_sample_summaries`），应通过独立的 compiler compatibility profile 发布，见 `docs/semantic/compiler-compatibility-profile.zh.md`。

### dimension -> normalized dimension contract

最少应抽取：

- `dimension_ref`
- `semantic_kind`
- `supports_grouping`
- `required_time_anchor_ref`
- `hierarchy_type`
- `parent_dimension_ref`
- `domain_kind`
- `enum_set_ref`
- `enum_version`

其中 `enum_set_ref` / `enum_version` 所引用的值域本体与版本边界，见 [`enum-set-schema-contract.zh.md`](./enum-set-schema-contract.zh.md)。compiler 只负责抽取与校验，不把值域治理策略重新写回 normalized dimension 主 contract。

更细的枚举兼容、null/tail 处理策略若需要参与编译，应来自 governance context，而不是假定它们属于 dimension 主 contract。

### intent request -> normalized action contract

最少应抽取：

- `intent_kind`
- root / ref / derived 类型
- 输入槽位绑定
- `resolved_filter_time_ref`
- 输出 artifact kind
- 受控默认值解析策略
- 该 intent 的硬失败条件与软告警面

### binding -> normalized grounding contract

最少应抽取：

- `binding_ref`
- `bound_object_ref`
- `carrier_bindings`
- `BindingTarget -> semantic_ref -> surface_ref` 映射
- `join_relations`
- `consumption_policies`

### carrier/source_object -> normalized grounding contract

最少应抽取：

- `binding_key`
- `source_object_ref`（或由 `carrier_locator` 解析到已同步 `source_objects`）
- `carrier_kind`
- `carrier_locator`
- `carrier_capabilities`
- `field_surfaces`
- `time_surfaces`
- `relation_surfaces`

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

- metric 是否支持该 intent（由 compiler 从 `sample_kind`、`additivity_constraints`、`primary_time_ref` 推导）
- process contract 是否足以支撑该 intent
- ref artifact 类型是否允许被该 intent 消费

若 compiler 无法从 object contract 推导某条关键规则，且该 intent 明确依赖它，应返回 `COMPILER_PROFILE_MISSING`。profile 缺失时的固定原则见 `docs/semantic/compiler-compatibility-profile.zh.md`。

### Gate 3：metric-process 兼容校验

重点校验：

- `population_subject_ref` 是否兼容
- `context_kind` / `entity_ref` 是否兼容
- `observation_grain_ref` / `emitted_grain_ref` 是否兼容（若 process 暴露实体流）
- `primary_time_ref` / `anchor_time_ref` / window `anchor_ref` 是否可被解析为合法 `time.*`，且 request `time_scope` 是否可稳定作用于该组合并产出单一 `resolved_filter_time_ref`
- 若存在 compiler compatibility profile，则其中的 `requirement` 字段是否被 process 满足

### Gate 4：binding-grounding compatibility gate

重点校验：

- 所需 `binding_ref` 是否存在
- 所需 `carrier_binding` 是否存在
- `binding` 引用的 `surface_ref` 是否都存在于对应 carrier
- `source_object_ref` 是否存在，或 `carrier_locator` 是否可稳定解析到已同步 `source_objects`
- 所需 relation surface / time surface 是否存在
- `carrier_kind` 是否满足 binding 与 intent 的消费前提

### Gate 5：dimension compatibility gate

重点校验：

- `request_dimensions` 是否都已归一化为存在的 `dimension.*`
- 对 `metric` 发起的请求中，`dimension.*` 必须属于当前 metric 的可消费集合；该集合由 metric 自身已导出的维度集合与 imported dimension bridge 集合共同组成
- metric imported dimension bridge 的 entity anchor 判定顺序固定为：
  1. 优先使用 `metric.header.observed_entity_ref`
  2. 若为空，则回退到 `metric.header.population_subject_ref`
  3. 若二者都为空，则该 metric 不具备 imported dimension bridge 能力
- imported dimension bridge 只允许消费满足以下条件的来源：
  - 来自当前 metric binding 显式 `imports` 的 `entity` scope binding
  - imported binding 的 `bound_object_ref` 与 metric entity anchor 匹配
  - imported binding 中 `target.target_kind = "stable_descriptor"` 且 `semantic_ref` 为 `dimension.*` 的 public contract target
  - 只支持一跳 bridge，不递归合并 imported binding 能力
- 同一 `dimension_ref` 若由多个 imported entity bindings 提供，compiler 必须在本 gate fail-fast；不得按 import order、`import_key` 或其他隐式优先级裁决来源
- 若请求使用的 `dimension.*` 既不属于 metric 自身可消费集合，也不属于 imported bridge 集合，则必须在 compiler 阶段失败，而不是推迟到 planner 或 engine
- 只有 `supports_grouping = true` 的维度才能进入通用分组位
- 若 `semantic_kind = time_derived`，则消费方必须暴露满足 `required_time_anchor_ref` 的 `primary_time_ref` 或 `anchor_time_ref`
- hierarchy 只信任 `parent_dimension_ref` 这一套主真相，不依赖 child/drill-down 镜像字段
- 若 governance context 定义了额外的枚举/空值/长尾策略，则请求 shape 是否与之兼容

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
3. `compare(left_ref, right_ref, mode="scalar")`
4. 对 `dimensions` 按请求顺序逐个展开：
   - `decompose(compare_ref, dimension, method, limit)`

关键 gate：

- `left` / `right` 必须能归一化为 scalar observe
- metric 必须满足 compare + decompose 兼容性
- dimension 必须可归因，且满足 grouping / time-anchor / governance 约束
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
- decompose 必须可归因，并满足 grouping / time-anchor / governance 约束

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
- 校验 metric `additivity_constraints`（`dimension_policy` 决定可分解性，`time_axis_policy` 决定时间轴可加性）
- 校验 dimension 可归因性
- 不得依赖 dimension 自声明 `supports_decomposition` 一类跨对象标签；合法性必须由 metric / process / entity 与 dimension 主契约联合推导
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
- `split_by` / follow-up dimension 必须满足 dimension compatibility gate，而不是依赖旧式 capability 标签

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
- `COMPILER_DIMENSION_NOT_EXPORTED`
- `COMPILER_DIMENSION_IMPORT_MISSING`
- `COMPILER_DIMENSION_IMPORT_AMBIGUOUS`
- `unsupported_ref_type`
- `lowering_path_unavailable`

其中 dimension 相关错误的归因固定为：

- `COMPILER_DIMENSION_NOT_EXPORTED`：`dimension.*` 已存在，但不属于当前 metric 的自身导出集合，也不属于 imported bridge 集合
- `COMPILER_DIMENSION_IMPORT_MISSING`：请求依赖 imported dimension bridge，但当前 metric 在其 entity anchor 下没有可匹配的 imported entity binding 来源
- `COMPILER_DIMENSION_IMPORT_AMBIGUOUS`：同一 `dimension_ref` 存在多个 imported entity binding 来源，compiler 无法唯一确定 bridge lineage

## 典型编译样例

## 例 1：`conversion_rate + experiment_context + validate`

编译结果至少应包含：

- `MeasurementNode(sample_kind="rate", inferential_summary_mode="rate_sample_summary")`
- 一个 `ProcessNode(contract_mode="context_provider", context_kind="experiment_split")`
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
- 一个 `ProcessNode(contract_mode="entity_stream", entity_ref="entity.session")`
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

Marivo compiler 的规范落点应是：

- 先把 typed intent request 归一化
- 再把 `metric` / `process object` 解析为 normalized contracts
- 通过显式 gates 校验组合合法性
- 对 derived intent 做确定性 atomic expansion
- 最后装配为 validation-aware、lineage-aware 的 IR

一句话总结：

> 在 Marivo 中，compiler 不是 SQL 生成器的前置包装层，而是 typed analysis expression 到 IR 的语义编译器。
