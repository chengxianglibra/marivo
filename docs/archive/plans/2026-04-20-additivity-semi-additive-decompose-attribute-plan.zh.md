# Factum Additivity Constraints / Decompose-Attribute 改造计划

## 1. 背景

本次围绕 `metric.ott_tv_user_count_enriched` 的 OTT DAU 分析，暴露了 Factum 在 metric additivity 建模、能力暴露和归因执行上的一组系统性问题。

当前表现为：

- `count_distinct(buvid)` 这类窗口去重指标可能被标记为 `additive`
- `decompose` / `attribute` 的执行路径默认假设 metric 可直接做 `delta_share`
- runtime / readiness / catalog / compare artifact 对归因能力和 additivity basis 的表达不一致
- 当前 `header.additivity` 把 additivity 当作一等三态枚举，但没有足够完整的 contract 来表达“哪些维度上可加，以及时间轴上是否可加”
- `decompose` 当前实际检查的是 legacy `measure_type`，typed metric 可能绕过该 gate，直到 SQL 或 reconciliation 阶段才暴露问题

这些问题会导致两类后果：

1. 系统把本不该严格分解的指标暴露为可归因，结果在运行时出现 `needs_attention`、`attribution_not_reconcilable` 或高 unexplained share。
2. 真正适合“条件可加”处理的指标没有稳定 schema 和编译规则，无法在 contract 层表达“在什么维度上允许 `decompose` / `attribute`，以及时间轴上是否允许 rollup / 聚合”。

本文目标是为下一步改造提供一份实现计划，而不是直接修改现有 contract。

## 2. 本文决策摘要

为避免后续实现阶段在 schema、runtime gate 和错误语义之间反复漂移，本计划先固定以下决策：

| 编号 | 决策点 | 对齐结论 |
| --- | --- | --- |
| D1 | additivity 主表达位置 | `header.additivity_constraints` 是唯一行为真源；它属于 metric-level contract，不属于 family-specific payload。 |
| D2 | legacy additivity 处理 | 当前服务未上线，不做向前兼容；删除 `header.additivity` 与 storage `additivity` column，不保留读写兼容逻辑。 |
| D3 | additivity 判断方式 | 不再保留或派生 `semi_additive` / `non_additive` 行为分类标签；runtime、readiness、compiler 只依据 constraints 判定具体能力。 |
| D4 | disallowed dimension 错误分类 | 请求结构错误走 schema validation；维度存在但不满足 metric additivity contract 时走 request-level contract violation，建议 HTTP `409`。 |
| D5 | distinct / DAU / UV 指标建模策略 | 默认声明为无可执行归因能力；只有在业务和 binding 均能确认稳定、互斥、可分组维度时，才声明对应 `dimension_policy` 与 allowed dimensions。 |
| D6 | `attribute` mixed dimensions 行为 | 只要存在任一 disallowed dimension，整个 `attribute` 请求失败；错误响应必须同时列出 allowed 与 disallowed dimensions，方便调用方自动重试 allowed 子集。 |
| D7 | runtime 与 readiness 复用边界 | compiler、readiness evaluator、`decompose`、`attribute`、compare artifact metadata 必须复用同一套 additivity capability 判定，避免再次出现 catalog/runtime 不一致。 |

对应风险控制原则：

- contract-first：先落 schema 和 validation，再开放 runtime 执行路径。
- fail-closed：信息不足时标记 `not_ready` 或拒绝请求，不降级成“尝试执行后再解释”。
- typed-error-only：对外返回结构化错误码、allowed/disallowed dimension 列表、时间轴边界和建模修复建议，不暴露 raw SQL 或依赖自由文本解释。
- breaking-cleanup：当前服务未上线，本轮按目标 schema 一次性收敛，不设计兼容层、双写、旧值迁移或 staged rollout。

## 3. 当前实现事实

### 3.1 `decompose` 当前 gate 不是 `header.additivity`

`app/intents/decompose.py` 当前检查的是 `resolved_metric.measure_type`：

```python
measure_type = resolved_metric.measure_type
if measure_type in {"semi_additive", "non_additive"}:
    raise ValueError(...)
```

`measure_type` 来自 `app/semantic_runtime/resolution.py` 中的 `family_payload.get("measure_type")`。通过 v2 typed API 创建的 metric payload 通常不包含 `measure_type` 字段，因此 `measure_type` 会是 `None`，当前 gate 对 typed metrics 会被绕过。

这意味着现状不是“已有可靠 additivity gate 需要改宽”，而是：

- legacy metric 可能被 `measure_type` 粗粒度拒绝
- typed metric 可能绕过 `decompose` additivity gate
- `attribute -> observe -> compare -> decompose` 对 typed metric 也可能绕过入口限制，直到 SQL 执行或 reconciliation 阶段才返回 `needs_attention` / `attribution_not_reconcilable`

因此 P1/P4/P5 的核心不是修补 `header.additivity` 三态枚举，而是引入 constraints-based shared gate，并替换 legacy `measure_type` gate。

### 3.2 当前 `MetricPayload` 是 family 判别联合体

`app/api/models/metric.py` 中 `MetricPayload` 是按 `metric_family` 判别的联合体：

- `CountMetricPayload`
- `SumMetricPayload`
- `RateMetricPayload`
- `AverageMetricPayload`
- `DistributionMetricPayload`
- `ScoreMetricPayload`
- `SurvivalMetricPayload`

additivity constraints 是横切 metric contract，不应塞进每个 family payload，也不应为此重构判别联合体。正确位置是 `MetricHeader`，即新增 `header.additivity_constraints`。

### 3.3 当前 storage 仍强制 `additivity NOT NULL`

`semantic_metric_contracts` 当前包含：

```sql
additivity TEXT NOT NULL CHECK (
    additivity IN ('additive', 'semi_additive', 'non_additive')
)
```

由于当前服务未上线，本轮不需要为该 column 设计兼容或迁移。应直接做破坏性 schema 收敛：

- 删除 storage 中的 `additivity` column 及三态枚举约束
- 新增 constraints 的规范存储位置和 DDL
- 所有 metric contract 的行为判断只读取 `header.additivity_constraints`
- 对历史 seed / fixture / 示例数据直接改写为新 contract，不保留旧值映射逻辑

### 3.4 当前 capability 命名和推导分裂

当前至少存在两套相关能力字段和规则：

- readiness evaluator 暴露 `supports_attribute`
- compiler / capability profile 暴露 `supports_compare`
- 两者当前都主要从 `additivity` / `primary_time_ref` 推导，但条件不完全覆盖真实 runtime gate

后续应统一 capability basis 和命名解释。对外 capability 字段可以继续存在，但必须由同一个 shared helper 生成，并在输出中携带 constraints basis。

### 3.5 当前 count 推导过粗

`app/semantic_service/common.py` 的 `_infer_metric_contract_axes` 会把 `measure_type == "count"` 映射为：

```python
("count_metric", "numeric", "count", "additive")
```

但 `CountMetricPayload.count_target.aggregation` 可以是 `count_distinct`。因此任何 metric 创建或规范化逻辑都不能再把所有 count 视为 unrestricted additive。`count_distinct`、DAU、UV 类指标默认应 fail-closed，除非显式补齐 constraints。

### 3.6 `compare` artifact 仍有 additivity 默认值

`app/intents/compare.py` 当前会在 artifact metadata 中把 `metric_additivity` 默认为 `"additive"`。在 additivity 模型重构后，compare artifact 也必须回显 constraints/additivity basis，不能继续给下游 `decompose` / `attribute` 暴露错误默认值。

## 4. 本轮目标

本轮计划覆盖 5 项改造目标：

1. 修正 `supports_attribute`、`supports_compare`、`supports_decompose` 与实际 runtime gate 不一致的问题。
2. 重构 additivity schema，使 `header.additivity_constraints` 成为唯一行为真源，并能表达“哪些维度上 additive，以及时间轴是否 additive”。
3. 让 `decompose` 支持满足 constraints 的 metric。
4. 让 `attribute` 在满足 constraints 时支持这类 metric，并在不满足条件时返回明确、结构化的拒绝原因。
5. 修正 compare artifact、metric 创建规范化、typed `count_distinct` 校验中的 additivity 漂移。

## 5. 非目标

本轮不做以下内容：

- 不引入新的归因算法，仍以 `delta_share` 为主
- 不把非 additive metric 全面扩展到任意因果解释或 planner-style 自动降级
- 不重写 metric family 主模型或 `MetricPayload` 判别联合体
- 不在本轮解决所有 distinct metric 的完美可归因问题
- 不把受限 additivity 扩展为跨多轴、跨层级的通用代数系统
- 不设计向前兼容、旧数据迁移、双写或旧 API 保留路径；当前服务未上线，本轮允许破坏性修改

## 6. 设计原则

- contract 必须比 runtime 更先收敛，不能继续依赖“运行时报错后再解释”
- `supports_*` 暴露必须与实际执行 gate 一致
- additivity 的真实边界必须由 constraints 表达，而不是依赖粗粒度标签
- 必须区分“按维度可分解”和“沿时间轴可聚合”这两个独立能力，不能互相推导
- `decompose` / `attribute` 对受限 additivity metric 的支持必须是条件支持，不能退化成“只要存在某个分类标签就都支持”
- 对不满足条件的请求，优先在编译或 intent validation 阶段失败，而不是执行一半后返回 reconciliation 问题
- API contract、storage row、readiness、runtime artifact 必须共享同一个 additivity basis

## 7. 建议语义模型

### 7.1 additivity 的长期干净模型

本计划采用长期干净模型：

- `header.additivity_constraints` 是唯一行为真源
- 删除 `header.additivity`，不保留 legacy/storage compatibility 字段
- 不再保留或派生 `additive` / `semi_additive` / `non_additive` 这类行为分类标签

从行为角度，真正需要表达的是两件事：

- 这个 metric 是否存在可执行的 `decompose` / `attribute` 路径
- 这些路径在 dimensions 和 time axis 上的边界是什么

### 7.2 为什么 constraints 放在 header

`additivity_constraints` 逻辑上属于 metric-level contract：

- 它不依赖某个特定 metric family
- 它会被 compiler、readiness、runtime intent gate、artifact metadata 共同消费
- 它描述的是 public semantic capability，而不是 family payload 的内部参数

因此新增字段应落在 `MetricHeader`：

```python
class MetricHeader(...):
    additivity_constraints: AdditivityConstraints
```

其中 `additivity_constraints` 是唯一行为真源。若 draft 创建阶段允许暂存不完整 contract，也只能表现为 readiness/validation blocker，不能引入 `additivity` fallback。

### 7.3 规范化后的能力判定

能力判定直接来自 constraints：

- `dimension_policy = "all"`：允许对所有已声明且可 groupable 的 semantic dimensions 做归因
- `dimension_policy = "subset"`：只允许对 `additive_dimensions` 中列出的 dimensions 做归因
- `dimension_policy = "none"`：不允许做维度归因
- `time_axis_policy = "additive"`：允许沿声明的时间语义做时间轴聚合
- `time_axis_policy = "non_additive"`：不允许跨 time bucket / period 直接 rollup

不存在单独的 `additive` / `semi_additive` / `non_additive` 行为状态。

### 7.4 需要拆开的两个独立能力

本计划要求把以下两件事明确拆开，而不是都含糊地塞进单一 additivity 枚举：

- `dimension decomposability`
- `time-axis additivity`

二者不能互相推导：

- 允许按某个 dimension 做 `decompose`，不代表该 metric 可以跨 time bucket 相加
- 时间轴上可做某种聚合，也不代表该 metric 对任意 dimension 都可稳定归因

### 7.5 `additivity_constraints` 需要表达的最小信息

最小可落地版本：

- `header.additivity_constraints.dimension_policy: "all" | "subset" | "none"`
- `header.additivity_constraints.additive_dimensions: list[str] | null`
- `header.additivity_constraints.time_axis_policy: "additive" | "non_additive"`
- `header.additivity_constraints.notes: str | null`

字段规则：

- `dimension_policy = "all"` 时，`additive_dimensions` 必须为 `null` 或缺失
- `dimension_policy = "subset"` 时，`additive_dimensions` 必须为非空列表
- `dimension_policy = "none"` 时，`additive_dimensions` 必须为 `null` 或缺失，表示“明确声明无维度可加”
- `additivity_constraints` 整体缺失表示 contract 不完整，不得推断为 `all` 或 `none`
- 对 distinct / DAU / UV 这类受限 additivity metric，本轮默认 `time_axis_policy = "non_additive"`

不建议继续使用弱表达的 `non_additive_axes = ["time"]` 作为主 contract，因为它无法明确区分“time 轴不可加”与“未来是否支持更细时间策略”。

### 7.6 time axis v2 扩展预留

v1 `time_axis_policy` 先收敛为二值：

- `additive`
- `non_additive`

但 schema 命名必须允许 v2 扩展，例如：

- 粒度敏感策略：日级可加、月级不可加
- 窗口敏感策略：固定窗口内可比较、跨窗口不可 rollup
- calendar policy 敏感策略：只允许特定对齐策略下比较

因此 runtime 不应把 `non_additive` 硬编码为永久唯一的非加性时间语义，而应通过 shared helper 暴露 `time_rollup_allowed` 和 future-compatible basis。

## 8. 计划范围

### 8.1 Contract / Schema

- metric create / update / get contract
- `MetricHeader` API model
- storage schema destructive cleanup
- semantic API docs
- compiler spec
- semantic-layer readiness / capability docs

### 8.2 Runtime / Compiler

- capability 推导
- readiness evaluator
- compare artifact metadata
- `decompose` intent gate
- `attribute` derived intent gate
- request-level contract violation 错误码

### 8.3 Validation / Errors

- 新增 `additivity_constraints` 合法性校验
- 新增 `MeasurementComponent.aggregation` 与 constraints 的交叉校验
- 新增“dimension 不在 additive_dimensions 中”的错误语义
- 新增 `attribute` 对多维请求的维度级 gate

### 8.4 Tests

- schema / model tests
- storage schema cleanup tests
- capability derivation tests
- compare artifact metadata tests
- decompose intent tests
- attribute intent tests
- regression tests for current DAU-like metrics

## 9. 建议实施顺序

1. P1 修 capability / readiness / compare metadata 一致性
2. P2 定义 `header.additivity_constraints` schema 与 storage cleanup
3. P3 编译与 readiness 支持基于 constraints 的受限 additivity
4. P4 `decompose` 支持满足 constraints 的 metric
5. P5 `attribute` 支持满足 constraints 的 metric
6. P6 文档、错误码、样例和回归测试收尾

说明：

- P1 应先做，避免继续对外暴露错误能力。
- P2 是 P3-P5 的前置，没有 schema 就无法做稳定 gate。
- P4 先于 P5，因为 `attribute` 是 `decompose` 的编排层扩展。

## 10. 分阶段任务

## P1. 修正 capability / readiness / metadata 一致性

### 目标

让 catalog / readiness / capability profile / compare artifact 暴露的能力与实际 intent 支持完全一致。

### 具体工作

- 新增 shared additivity capability helper，统一生成：
  - `supports_compare`
  - `supports_decompose`
  - `supports_attribute`
  - `allowed_dimensions`
  - `disallowed_dimensions`
  - `time_rollup_allowed`
  - blocker / error code
  - remediation hint
- 收敛 `supports_attribute` 的推导规则
- 规则改为依赖 compare + decompose 的共同可用性，而不是仅依赖 `additivity` 非空
- 识别并修复 `supports_attribute` vs `supports_compare` 命名分裂：对外 capability 字段必须来自同一 basis
- 检查并更新：
  - readiness evaluator
  - analysis capability profile
  - API 返回的 capability 字段
  - compare artifact metadata
  - UI / admin 是否依赖旧布尔语义

### 预期结果

- 无可执行归因能力的 metric 不再被标成支持 `attribute`
- 受限 additivity metric 的 `supports_attribute` 只在 contract 足够完整时为 true
- catalog / resolve 在返回布尔 capability 的同时，回显 `additivity_constraints`，让调用方知道该能力的适用边界
- compare artifact 不再默认写死 `metric_additivity = "additive"`，而是回显 constraints/additivity basis

### 验收标准

- 对无可执行归因能力的 metric，catalog / readiness / resolve / runtime 行为一致
- 调用 `attribute` 不会再出现“catalog 说支持，runtime 才失败”的错位
- 对受限 additivity metric，catalog 的 `supports_attribute = true` 必须伴随可机读的 allowed dimensions，且 disallowed dimensions 会在入口 validation 稳定失败
- compare artifact 的 additivity metadata 不会与 runtime gate basis 冲突

## P2. 定义 `header.additivity_constraints` schema 与 storage cleanup

### 目标

为受限 additivity 提供最小但完整的 public contract，使其能表达“允许 additive 的 dimensions”，并把 DB / API 一次性收敛到目标模型。

### 具体工作

- 扩展 `MetricHeader`，新增 `additivity_constraints`
- 删除 `MetricHeader.additivity`，避免继续暴露三态分类标签
- 不修改 `MetricPayload` 判别联合体，不把 constraints 加到 7 个 family payload 上
- 明确 create / update / get / list 的序列化行为
- 定义 storage cleanup：
  - 新增 constraints 存储列或等价 JSON 存储位置
  - 删除现有 `additivity TEXT NOT NULL CHECK (...)` column
  - 定义读取时的 canonical contract 组装规则
  - 定义写入时的 canonical persistence 规则，不再派生或保存三态 `additivity`
- 直接改写 seed / fixture / 示例 metric：
  - 普通 sum/count 且无 distinct/window/non-linear aggregation 的 metric 显式声明 `dimension_policy = "all"`
  - distinct / UV / DAU 类 metric 默认声明 `dimension_policy = "none"`，除非已经确认 allowed dimensions
  - rate / ratio / avg / percentile / score / survival 等 metric 默认声明 `dimension_policy = "none"`
- 定义校验规则：
  - 只要声明 `additivity_constraints`，就必须显式提供 `time_axis_policy`
  - 只要声明 `additivity_constraints`，就必须显式提供 `dimension_policy`
  - `dimension_policy = "subset"` 时，`additive_dimensions` 必须是 metric 已声明、已绑定、可分组的 semantic dimensions
  - `dimension_policy = "none"` 时，`additive_dimensions` 必须为 `null` 或缺失
  - 若要暴露 `supports_decompose` / `supports_attribute`，`dimension_policy` 不得为 `none`
  - `MeasurementComponent.aggregation = "count_distinct"` 时不得默认允许 `dimension_policy = "all"`；只有显式证明稳定互斥维度时才能使用 `subset`

### 建议字段

最小版本：

```json
{
  "header": {
    "additivity_constraints": {
      "dimension_policy": "subset",
      "additive_dimensions": ["dimension.country"],
      "time_axis_policy": "non_additive",
      "notes": "distinct user count is only decomposable across stable mutually exclusive descriptors"
    }
  }
}
```

### 验收标准

- OpenAPI / model / docs 对 `additivity_constraints` 的约束一致
- 非法输入会在 schema 或 semantic validation 阶段被稳定拒绝
- storage 中不存在三态 `additivity` 行为字段或 NOT NULL 约束
- 缺少完整 constraints 的 metric 不会继续暴露虚假 `attribute` 能力

## P3. compiler / readiness 支持基于 constraints 的受限 additivity

### 目标

让 constraints 成为真实可消费的 contract，而不是依赖静态枚举或 legacy `measure_type`。

### 具体工作

- capability derivation 读取 `header.additivity_constraints`
- `supports_decompose`
  - `dimension_policy in {"all", "subset"}` 时可为 true
- `supports_attribute`
  - 必须建立在 `supports_compare = true` 且 `supports_decompose = true` 基础上
  - 不得因为支持 `attribute` 就推导出时间轴可 rollup
- capability 输出必须携带 additivity basis，至少包含：
  - `dimension_policy`
  - `additive_dimensions`
  - `time_axis_policy`
  - `time_rollup_allowed`
  - `capability_condition = "dimension_must_be_allowed"`
- readiness evaluator 增加 blocker：
  - `ADDITIVITY_CONSTRAINTS_MISSING`
  - `ADDITIVITY_CONSTRAINTS_DIMENSION_POLICY_MISSING`
  - `ADDITIVITY_CONSTRAINTS_TIME_AXIS_POLICY_MISSING`
  - `ADDITIVITY_CONSTRAINTS_DIMENSION_UNDECLARED`
  - `ADDITIVITY_CONSTRAINTS_DIMENSION_NOT_GROUPABLE`
  - `ADDITIVITY_CONSTRAINTS_AGGREGATION_CONFLICT`
- resolve / catalog 输出明确 blocker 和 capability

### 验收标准

- 一个具有完整 constraints 的受限 additivity metric 可以进入 ready
- 一个 schema 不完整的受限 additivity metric 会被标成 `not_ready`
- typed `count_distinct` 不会因为缺少 `measure_type` 或旧三态 additivity fallback 暴露 unrestricted decompose/attribute

## P4. `decompose` 支持满足 constraints 的 metric

### 目标

允许 `decompose` 对受限 additivity metric 在被允许的维度上执行。

### 具体工作

- 替换当前 `measure_type in {"semi_additive", "non_additive"}` gate
- `decompose` gate 改为调用 shared additivity capability helper：
  - `dimension_policy = "all"`：所有已声明且可 groupable 的 semantic dimensions 都支持
  - `dimension_policy = "subset"`：仅当 `dimension in additive_dimensions` 时支持
  - `dimension_policy = "none"`：拒绝
  - 缺失完整 constraints：拒绝
- 明确 `decompose` 的时间边界语义：
  - `decompose` 只在上游 `compare` 已冻结的 left/right `time_scope` 边界内解释 delta
  - 不新增任何“沿 time 轴再次聚合”的能力
- 新增稳定错误码：
  - `ADDITIVITY_CONSTRAINT_DIMENSION_NOT_ALLOWED`
  - 或 `UNSUPPORTED_DECOMPOSITION_DIMENSION_FOR_METRIC`
- 错误 payload 至少包含：
  - `metric`
  - `requested_dimension`
  - `dimension_policy`
  - `allowed_dimensions`
  - `disallowed_dimensions`
  - `time_axis_policy`
  - `remediation_hint`
- 在 artifact metadata 中回显本次 decompose 的 additivity basis：
  - `dimension_policy`
  - `decomposition_constraint`
  - `allowed_dimension_basis`
  - `time_axis_policy`
  - `time_rollup_allowed`

### 关键注意点

- 本阶段不自动保证“任何 declared additive dimension 都一定可完美 reconciliation”
- 只解决 contract-level allowed / disallowed 边界
- 对运行期仍可能出现的 reconciliation issue，保留现有 `needs_attention` 语义

### 验收标准

- 满足 constraints 的 metric 在 allowed dimension 上可成功 `decompose`
- 在 disallowed dimension 上稳定失败，且错误信息可直接指导建模修复
- typed metrics 不再因缺少 `measure_type` 绕过 additivity gate

## P5. `attribute` 支持满足 constraints 的 metric

### 目标

使 `attribute` 能在受限 additivity metric 上安全工作，并在不满足条件时尽早失败。

### 具体工作

- `attribute` 入口增加前置 gate：
  - metric 必须支持 compare
  - metric 必须支持 decompose
  - 请求中的每个 dimension 都必须被 constraints 允许
- 前置 gate 必须发生在内部 `observe -> compare -> decompose` 编排之前
- gate 必须调用与 compiler / readiness / `decompose` 相同的 helper
- 明确 `attribute` 的时间边界语义：
  - `attribute` 复用内部 `observe` / `compare` 已解析出的 canonical `time_scope`
  - 成功执行只表示“可在该固定时间边界内做归因”，不表示 metric 支持时间轴聚合
- 对 mixed request 做明确拒绝：
  - 如果 dimensions 中部分允许、部分不允许，整个请求失败
  - HTTP 409 payload 同时列出 `allowed_dimensions` 和 `disallowed_dimensions`
  - 调用方可以据此自动重试只包含 allowed dimensions 的请求
- `attribute_bundle` projection 中回显 additivity basis，便于调用方解释结果边界

### 关键注意点

- `attribute` 不应依赖内部 `decompose` 逐个失败后再拼错误
- 应在 derived intent 入口先做 compile-time precheck
- 当前 typed metrics 可能绕过 `decompose` 旧 gate，因此 P5 不能假设 P4 之前已有可靠入口拒绝

### 验收标准

- 满足 constraints 的 metric 在全量 allowed dimensions 上可成功运行 `attribute`
- 若任一 dimension 不被允许，则 `attribute` 在入口阶段失败，并返回结构化错误
- mixed dimensions 失败响应足以让调用方自动构造 allowed-only retry

## P6. 文档和测试收尾

### 目标

把 schema、运行时规则、能力暴露、用户文档和测试矩阵统一收口。

### 具体工作

- 更新 API 文档和 semantic docs 中对 `additivity_constraints` 的说明，并删除 `additivity` 三态字段说明
- 更新 compiler spec、readiness docs、intent docs 中的 capability basis
- 新增 distinct DAU / UV 类指标的建模建议
- 明确：
  - 什么情况下使用 `dimension_policy = "all"`
  - 什么情况下使用 `dimension_policy = "subset"`
  - 什么情况下使用 `dimension_policy = "none"`
  - 维度可分解能力不等于时间轴可 additive
- 确认共享 agent guide 是否需要更新；若只是具体实现细节，不应写入 `docs/agent-guide.md`

### 测试矩阵

- `dimension_policy = "all"` metric:
  - decompose success
  - attribute success
  - capability 暴露 `time_rollup_allowed` 与 `time_axis_policy` 一致
- `dimension_policy = "subset"` metric:
  - allowed dimension decompose success
  - disallowed dimension decompose fail
  - allowed dimensions attribute success
  - mixed dimensions attribute fail
  - capability 暴露 `time_rollup_allowed = false` when `time_axis_policy = "non_additive"`
- `dimension_policy = "none"` metric:
  - decompose fail
  - attribute fail
  - allowed dimensions 为空且语义明确
- capability / readiness:
  - catalog 暴露与 runtime gate 一致
  - dimension 边界与 time-axis 语义一致
  - `supports_compare`、`supports_decompose`、`supports_attribute` 由同一 basis 推导
- storage / API cleanup:
  - `MetricHeader.additivity` 不再出现在 create / update / get / list contract 中
  - storage 不再要求或保存三态 `additivity`
  - count metric 创建不再无条件等价 unrestricted additive
- typed API regression:
  - `count_distinct` metric 默认不会暴露 unrestricted attribution
  - `MeasurementComponent.aggregation` 与 constraints 冲突会被拒绝或标记 blocker
- compare artifact:
  - 不再默认 `metric_additivity = "additive"`
  - artifact metadata 回显 constraints/additivity basis

## 11. 建模建议

### 11.1 Metric constraints 声明策略

所有 metric 都必须显式声明 constraints：

- 维度与时间轴都可加的 metric
  - `dimension_policy = "all"`
  - `time_axis_policy = "additive"`
  - 仅适用于确认为普通 sum/count 且不存在 distinct/window/non-linear aggregation 的 metric
- distinct / UV / DAU 类但可在稳定互斥维度上拆分的 metric
  - `dimension_policy = "subset"`
  - 通过 `additive_dimensions` 声明 allowed dimensions
  - 默认 `time_axis_policy = "non_additive"`
- rate / ratio / avg / percentile / score / survival 等
  - `dimension_policy = "none"`
  - `time_axis_policy = "non_additive"`

不再读取或派生旧 `header.additivity` 值。若 fixture、seed 或文档样例仍包含该字段，应直接改写为 `header.additivity_constraints`。

### 11.2 对 OTT DAU 的建议

对 `metric.ott_tv_user_count_enriched` 和 `metric.ott_tv_user_count`：

- 若当前无法确认稳定互斥且可分组的 allowed dimensions：
  - `dimension_policy = "none"`
  - `time_axis_policy = "non_additive"`
  - 同步修 capability / readiness / attribute gate
- 若业务确认存在一组稳定互斥维度可安全分解：
  - 补齐 `header.additivity_constraints`
  - `dimension_policy = "subset"`
  - 仅把这些维度列入 `additive_dimensions`
  - 显式声明 `time_axis_policy = "non_additive"`

## 12. 风险控制与决策点

### 风险一：把分类标签误当成行为真源

若继续使用 `additive` / `semi_additive` / `non_additive` 分类标签作为 runtime 行为真源，而不是用 constraints 表达真实边界，系统仍然不知道“允许在哪些维度上分解”，问题只会后移。

风险控制：

- 本计划不接受“只有分类标签、没有约束字段”的 ready 状态。
- 缺少 `header.additivity_constraints.dimension_policy` 或 `time_axis_policy` 时，validation / readiness 必须 fail-closed。
- `dimension_policy = "subset"` 但缺少非空 `additive_dimensions` 时，validation / readiness 必须 fail-closed。
- capability、artifact metadata 和错误 payload 都必须回显 additivity basis，避免调用方把条件能力误读为全局能力。

### 风险二：把 reconciliation 问题误当成 schema 问题

即使 dimension 在 `additive_dimensions` 中，运行时仍可能因为底层数据特征或 binding 粒度出现 `needs_attention`。本轮不能承诺“allowed dimension 一定完全可重构”，只能保证 contract-level allowed boundary 更准确。

风险控制：

- `additive_dimensions` 只表示 contract-level allowed boundary，不表示运行期一定 perfect reconciliation。
- `decompose` / `attribute` 成功产物必须保留现有 reconciliation quality 信号。
- 对 `needs_attention`、高 unexplained share、`attribution_not_reconcilable` 不做静默降级或二次改写。

### 风险三：破坏性 cleanup 漏改

本轮删除 `header.additivity` 与 storage `additivity` column，如果 seed、fixture、docs、UI 或测试仍引用旧字段，会导致 contract 不一致或测试失败。

风险控制：

- P2 必须同步改写 seed / fixture / docs / UI 中的旧字段引用。
- 添加 contract shape 测试，确保 create / update / get / list 不再暴露 `header.additivity`。
- 回归测试必须覆盖 `dimension_policy = "all"`、`subset`、`none` 三类 metric 的 catalog/runtime 一致性。

### 风险四：把维度可分解误读为时间轴可加

若只向外暴露 `allowed_dimensions`，agent 或调用方很容易把“可按维度归因”误解为“也支持按天/周/月再累计”。

风险控制：

- capability 与 artifact metadata 必须同时回显 `allowed_dimensions` 和 `time_axis_policy`。
- 对 `time_axis_policy = "non_additive"` 的 metric，建议同时暴露 `time_rollup_allowed = false`，避免 agent 只看布尔能力误判。
- 文档明确禁止从 `supports_decompose = true` 或 `supports_attribute = true` 推导出时间轴可 additive。

### 风险五：多个实现点重复写 gate 造成再次漂移

compiler、readiness evaluator、`decompose`、`attribute`、compare artifact 如果各自实现 additivity 判断，后续很容易再次出现 catalog 声称支持但 runtime 拒绝的状态。

风险控制：

- 抽出 shared additivity capability helper，统一返回 capability 布尔值、allowed/disallowed dimensions、blocker/error code 和 remediation hint。
- compiler、readiness 和 runtime intent validation 都基于同一 helper 或同一组 golden tests。
- compare artifact metadata 也必须来自同一 helper，不再手写默认值。

### 已对齐决策点

1. `header.additivity_constraints` 是唯一行为真源；删除 `header.additivity`。
2. `dimension_policy` 与 `time_axis_policy` 必须显式声明；本轮默认对 distinct / DAU / UV 类 metric 采用 `time_axis_policy = "non_additive"`。
3. 请求结构或字段类型错误走 schema validation；维度合法但不满足 metric contract 时走 `409 contract violation`。
4. distinct user count / DAU / UV 类 metric 默认声明为 `dimension_policy = "none"`；只有确认稳定互斥且可分组的维度后，才改为 `dimension_policy = "subset"`。
5. `attribute` 对 mixed dimensions 整体失败，并在错误中同时列出 allowed 与 disallowed dimensions。
6. `supports_attribute = true` 对受限 additivity metric 表示“存在受约束的可执行路径”，必须与 `additivity_constraints` 一起解释，不能单独展示为无条件能力，更不能被解读为时间轴可 rollup。

## 13. 验收摘要

本计划完成后，应达到以下状态：

- 无可执行归因能力的 metric 不再暴露虚假的 `attribute` 能力
- `header.additivity_constraints` 成为唯一行为真源，`header.additivity` 从 API/storage contract 中删除
- 受限 additivity 的 contract 同时表达 `dimension_policy`、allowed dimensions 与 `time_axis_policy`
- `decompose` 能对满足 constraints 的 metric 在允许维度上工作
- `attribute` 能对满足 constraints 的 metric 在允许维度集合上工作，并对 mixed disallowed request 返回可机读 409
- typed `count_distinct` 不再因为缺少旧 `measure_type` 绕过 gate
- count / additivity 规范化不再无条件生成 unrestricted additive capability
- compare artifact metadata 不再默认或暗示错误 additivity
- 这类 metric 的维度可分解能力不会再被误解为时间轴可 additive
- catalog / readiness / compiler / runtime / docs 对 additivity 语义保持一致
