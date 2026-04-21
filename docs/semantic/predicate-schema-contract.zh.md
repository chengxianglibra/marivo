# Semantic Layer Predicate Schema Contract（草案）

本文定义 Factum semantic layer 中 `predicate.*` 的目标 schema contract。

本文是**语义契约设计文档**，不是当前实现说明，也不是最终 HTTP wire spec。它与以下文档配套：

- `docs/semantic/dimension-schema-contract.zh.md`
- `docs/semantic/entity-schema-contract.zh.md`
- `docs/semantic/metric-v2-schema.zh.md`
- `docs/semantic/process-object-schema.zh.md`
- `docs/semantic/time-schema-contract.zh.md`
- `docs/semantic/typed-binding-contract.zh.md`
- `docs/semantic/compiler-spec.zh.md`
- `docs/semantic/ir-schema-contract.zh.md`

本文重点回答：

- `predicate.*` 如何成为可复用、受治理的过滤语义对象
- metric qualifier、binding row filter、request scope、governance policy 分别应承担什么过滤职责
- 哪些过滤语义属于 public semantic contract，哪些必须留在 compiler / binding / execution 层
- 多 component metric 的过滤 lineage 应如何保留，而不退化成单个全局 predicate

## Purpose

本文用于为 `predicate.*` 提供一套更稳定、更受治理的 typed contract，使其能够：

- 表达可复用的 non-time filter semantics，而不是重复内联 SQL 条件
- 为 `MeasurementComponent.qualifier_refs`、`CarrierBinding.row_filter_refs` 与请求级 `scope` 提供统一的语义引用
- 让 compiler 在进入 lowering 前完成 filter-aware validation、conflict detection 与 narrowing 校验
- 为 observation artifact 提供可冻结、可解释、可比较的过滤 lineage

本文不定义：

- 最终数据库 DDL
- 最终 REST endpoint shape
- 最终 SQL / engine lowering 模板
- 运行时错误码枚举

## 背景

Factum 现有设计中已经存在多类过滤入口：

- `MeasurementComponent.qualifier_refs`
- `CarrierBinding.row_filter_refs`
- `scope.constraints` / `scope.predicate`

如果这些过滤语义长期停留在对象各自的局部字段中，会产生几个问题：

- 同一业务口径无法稳定复用
- metric identity 与请求级临时 scope 容易混淆
- binding 不变量与 metric 业务过滤容易相互污染
- 多 component metric 的 sample basis 难以稳定冻结
- compiler 很难在 lowering 前判断冲突、可解析性与 narrowing 边界

因此，Factum 需要把“过滤条件”从零散字段收敛为受治理的 `predicate.*` contract。

## 设计目标

predicate contract 应同时满足：

- **语义稳定**：以 `predicate_ref` 作为可发布、可引用的稳定标识
- **职责单一**：表达过滤语义，不表达 SQL、join plan、窗口本体或 process 构造
- **可组合**：可被 metric、binding、request scope、governance policy 以受约束方式复用
- **可校验**：compiler 能判断 resolvability、usage 合法性、value-domain 兼容性与 narrowing 边界
- **可解释**：artifact 能冻结 shared scope 与 component lineage，而不是只留下执行后 SQL

## 非目标

本文明确不追求：

- 把 predicate contract 设计成 SQL AST 或通用 DSL
- 支持任意函数、UDF、子查询或 engine-specific predicate 语法
- 让 predicate 直接表达 cohort / funnel / session / attribution 规则
- 让 request scope 覆盖或放宽 metric / binding / governance 已声明的过滤约束
- 让 predicate 重新定义时间窗口契约

## 核心设计结论

`predicate.*` 的公共 contract 应回答四个问题：

1. 这个过滤语义在语义层中的稳定标识是什么
2. 它约束的主要语义主体是什么
3. 它由哪些受治理 semantic refs 组成，允许以什么位置被复用
4. 它是否满足 non-time、deterministic、可 lowering 的边界

它**不应**回答：

- 底层字段名是什么
- 最终 SQL 应怎么写
- join 顺序、CTE 形状、optimizer hint 如何选择
- 观察窗口、归因窗口、late-arrival policy 如何定义

一句话总结：

> predicate 应声明“什么过滤语义被稳定复用”，而不是声明“最终 WHERE 子句应长什么样”。

## 统一 ref taxonomy

semantic layer 中的过滤 contract 必须使用独立命名空间，不能继续复用裸字符串或物理列名。

建议采用以下稳定前缀：

- `predicate.*`：受治理过滤语义
- `dimension.*`：共享分析轴
- `entity.*`：稳定业务实体
- `subject.*`：总体主体
- `key.*`：身份键语义
- `time.*`：共享时间语义

这意味着：

- `predicate_ref` 只能指向 `predicate.*`
- `PredicateAtom.target_ref` 必须指向已受治理的 semantic ref
- `"status"`、`"country"`、`"order_status"` 这类裸字符串不应再作为跨对象 public ref
- 裸物理列名只能作为兼容层输入存在，不应成为新的 contract 设计

## 统一建模原则

### 1. predicate 是一等过滤语义对象，不是 metric 或 binding 的附属字符串

metric、binding、request scope 都可能消费过滤语义，但过滤语义本身应由 `predicate.*` 统一建模。

因此：

- `qualifier_refs` 应引用 `predicate.*`
- `row_filter_refs` 应引用 `predicate.*`
- 请求级 `scope.predicate` 在语义上应对齐同一表达式 contract，而不是另发明一套 DSL

### 2. predicate 公共 contract 不暴露执行 DSL

以下内容不应进入 predicate public schema：

- 原始 SQL predicate string
- 物理字段名
- engine-specific function
- adapter 私有重写策略

可以保留的，是结构化后的稳定语义，例如：

- `target_ref`
- 受限操作符
- 结构化 value
- `allowed_usage`
- `time_policy`

### 3. 时间过滤不属于 predicate 主 contract

`time_scope` 是请求层唯一时间窗口入口。

因此：

- v1 predicate 默认只允许 non-time 过滤
- `PredicateAtom.target_ref` 若指向 `time.*`，应视为越界
- “注册后 7 天内”“曝光后 24 小时内”这类窗口语义应进入 process object / metric-process contract，而不是普通 predicate

### 4. request scope 只能进一步收窄，不能重写上游过滤语义

请求级 `scope` 的职责是表达本次分析的临时 non-time population scope，而不是改写 metric identity 或 carrier invariant。

稳定规则应为：

- request scope 与 metric / binding / governance filters 之间是 `AND` 关系
- request scope 只能进一步收窄
- 无法静态证明为 narrowing 时应 fail closed
- request scope 不得覆盖、移除或放宽上游过滤

### 5. 多 component metric 的过滤 lineage 必须分层保留

对 rate / average 等多 component metric，编译期不应把所有 qualifier 折叠为单个全局 predicate。

应严格区分：

- `shared_effective_scope`
- `metric default predicates`
- `component qualifier predicates`

否则：

- numerator / denominator 的 sample basis 会被错误合并
- compare / validate / test 无法稳定解释 observation 的可比性
- artifact replay 无法恢复每个 component 的 lineage

## 概念分层

### 1. Carrier Row Filter

`CarrierBinding.row_filter_refs` 表示 carrier consumption invariants。

适合放这里的过滤：

- `predicate.exclude_test_data`
- `predicate.not_soft_deleted`
- `predicate.tenant_guardrail`

不适合放这里的过滤：

- `predicate.successful_order`
- `predicate.user_converted`
- `predicate.effective_play`

原因是这些属于 metric business semantics，而不是 carrier 本身的不变量。

### 2. Metric Business Predicate

metric business predicate 是 metric identity 的一部分，即“这个 metric 到底量什么”。

优先放置规则：

- 单 component measurement 的业务过滤，放在对应 `MeasurementComponent.qualifier_refs`
- rate / average 的 numerator 与 denominator，分别保留各自 `qualifier_refs`
- 所有 component 共用的总体限制，可通过 metric-level `default_predicate_refs` 表达，但不替代 component lineage

### 3. Request Scope

`scope` 表示本次分析的临时 non-time population scope。

适合放这里的过滤：

- `dimension.country = "CN"`
- `dimension.platform in ["ios", "android"]`
- 某个业务线、实验组、渠道的临时收窄

不适合放这里的过滤：

- metric 固有业务口径
- carrier 数据卫生过滤
- 时间窗口

### 4. Governance Policy Filter

governance / privacy / tenant isolation 不是普通业务过滤。

它们的稳定特征是：

- 永远优先于 metric 与 request scope
- 不可被调用方覆盖
- 与 metric predicate 冲突时，应视为 policy / readiness 失败，而不是静默返回空 population

## 通用 Schema

### 公共头部

```python
from typing import NotRequired, TypedDict


class PredicateHeader(TypedDict):
    predicate_ref: str
    display_name: NotRequired[str | None]
    description: NotRequired[str | None]
    subject_ref: str
    predicate_contract_version: str
```

与其他 semantic object 一样，catalog 生命周期、版本序号、搜索别名、quality gates 等信息应进入单独 metadata envelope，而不是 predicate 主契约。

### 字段说明

| Field | Type | Required | 说明 |
| --- | --- | --- | --- |
| `predicate_ref` | string | yes | predicate 的稳定公共引用；必须使用 `predicate.*`；也是 public contract 主标识 |
| `display_name` | string | no | 人类可读显示名 |
| `description` | string | no | 业务语义说明 |
| `subject_ref` | string | yes | predicate 主要约束的语义主体；通常是 `entity.*` 或 `subject.*` |
| `predicate_contract_version` | string | yes | predicate 契约版本 |

### 公共子结构

#### PredicateUsage

```python
from typing import Literal


PredicateUsage = Literal[
    "metric_qualifier",
    "carrier_row_filter",
    "request_scope",
    "governance_policy",
]
```

#### PredicateTimePolicy

```python
from typing import Literal


PredicateTimePolicy = Literal["non_time_only"]
```

#### PredicateAtom

```python
from typing import Literal, NotRequired, TypedDict


class PredicateAtom(TypedDict):
    target_ref: str
    op: Literal[
        "eq",
        "neq",
        "in",
        "not_in",
        "gt",
        "gte",
        "lt",
        "lte",
        "between",
        "is_null",
        "is_not_null",
    ]
    value: NotRequired[
        str | int | float | bool | None | list[str | int | float | bool | None]
    ]
```

#### PredicateExpression

```python
from typing import TypeAlias, TypedDict


class PredicateConjunction(TypedDict):
    op: str
    items: list["PredicateExpression"]


PredicateExpression: TypeAlias = PredicateConjunction | PredicateAtom
```

#### PredicatePayload

```python
from typing import TypedDict


class PredicatePayload(TypedDict):
    expression: PredicateExpression
    allowed_usage: list[PredicateUsage]
    time_policy: PredicateTimePolicy
```

## v1 表达式约束

`predicate.v1` 只支持可确定性 lowering、可做 narrowing 校验的有限表达式子集。

### 允许的结构

- 原子谓词
- `and` 连接的 conjunctive expression

### 不允许的结构

- `or`
- `not`
- 动态变量
- `now()`、随机函数等非确定性输入

### 值约束

- `between` 必须提供两个边界值
- `in` / `not_in` 必须提供非空数组
- `is_null` / `is_not_null` 不得提供 `value`
- `value` 必须与 `target_ref` 的 value domain 兼容

## Effective Scope 合成

编译阶段不应把多 component metric 的 qualifiers 折叠成单个全局 predicate。应区分 shared scope 与 component lineage：

```text
shared_effective_scope =
  governance_policy_filters
  AND carrier_row_filters
  AND request_scope_constraints
  AND request_scope_predicate

component_effective_scope(component) =
  shared_effective_scope
  AND metric_default_predicates
  AND component_qualifier_predicates(component)
```

其中：

- governance filters 是不可绕过的强制策略
- carrier row filters 是 binding 不变量
- metric default predicates 是 metric identity 的一部分
- component qualifier predicates 是 component identity 的一部分
- request scope 是调用方本次分析的临时收窄条件

这意味着：

- 单 component metric 的 sample basis 是 `shared_effective_scope AND metric_default_predicates AND component_qualifier_predicates`
- 多 component metric 不存在一个可替代所有 component 的全局 `effective_scope`
- artifact 必须能分别冻结 shared scope 与 per-component lineage

## 校验与治理规则

### Predicate Contract 校验

创建或发布 `predicate.*` 时，建议至少校验：

- `predicate_ref` 必须以 `predicate.` 开头
- `subject_ref` 必须可解析
- `expression` 不能为空
- `PredicateAtom.target_ref` 必须是已受治理 semantic ref
- `op` 必须位于白名单中
- `value` 必须与 target value domain 兼容
- `allowed_usage` 必须非空
- `time_policy` 在 v1 必须为 `non_time_only`
- expression 必须 deterministic

### Usage 校验

predicate 被不同对象消费时，compiler / validator 还应校验：

- `qualifier_refs` 使用的 predicate 必须声明 `metric_qualifier`
- `row_filter_refs` 使用的 predicate 必须声明 `carrier_row_filter`
- request scope 使用的 predicate 必须声明 `request_scope`
- governance policy 使用的 predicate 必须声明 `governance_policy`

### Scope 校验

typed intent 入口应至少保证：

- `scope` 只表达 non-time population constraints
- `scope.predicate` 若存在，应符合同一表达式 contract
- request scope 只允许 conjunctive expression
- request scope 不得包含时间条件
- request scope 必须属于 metric 的 semantic scope
- request scope 只能进一步收窄；无法证明时应拒绝

## 与其他对象的边界

### 与 Metric 的边界

- metric 通过 `qualifier_refs` 或 `default_predicate_refs` 引用 predicate
- predicate 是 metric identity 的组成部分，但不是 metric family payload 的执行 DSL
- metric 不应内联 predicate AST 或 SQL predicate string

### 与 Binding 的边界

- binding 通过 `row_filter_refs` 引用 predicate
- `row_filter_refs` 只表达 carrier invariants，不表达某个 metric 的业务口径
- predicate 本身不负责说明如何落到底层字段；该职责属于 binding surfaces 与 lowering

### 与 Time 的边界

- predicate 不重新定义时间窗口
- `time_scope` 是唯一时间范围入口
- 相对时间窗口与过程型时间规则应建模在 process object / metric-process contract

### 与 Compiler / IR 的边界

- compiler 负责 normalization、conflict detection、narrowing proof 与 lowering 前校验
- IR 负责引用 resolved lineage，而不是复制 predicate 对象全文
- predicate schema 本身不是 engine plan，也不是 SQL AST

## Artifact Lineage 原则

`observe` artifact 应能冻结：

- shared scope
- metric default predicates
- per-component qualifier lineage
- component-level effective scope fingerprint

不要把 metric defaults、numerator predicates、denominator predicates 压平成单一 `metric_predicates` 列表。

否则：

- compare 无法稳定判断 sample basis
- validate / test 无法确认 numerator 与 denominator 的 comparability
- 下游无法直接从 artifact 恢复过滤 lineage

## 与实现的边界

本文是 predicate contract 的正式语义规范。以下内容属于实现层或单独文档，不在本文展开：

- 具体 API route
- 具体错误码
- 迁移顺序
- engine-specific SQL lowering 细节

本文件是 `predicate.*` contract 的唯一规范来源；其他 semantic 文档只应引用这里的分层结论，而不重复发明局部 filter DSL。
