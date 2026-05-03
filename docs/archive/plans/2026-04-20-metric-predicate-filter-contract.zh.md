# Metric Predicate / Filter Contract 决策记录

本文记录 `metric predicate / filter contract` 的设计收敛结果，用于解释为什么 Factum 需要引入统一的 `predicate.*` 语义对象，以及相关正式规范落到了哪些文档。

本文不是正式 schema 规约，也不是实现说明。正式规范以 [`docs/semantic/predicate-schema-contract.zh.md`](/Users/lichengxiang/source/oss/factum/docs/semantic/predicate-schema-contract.zh.md) 为准。

## 背景

Factum 中已经存在三类过滤入口：

- `MeasurementComponent.qualifier_refs`
- `CarrierBinding.row_filter_refs`
- `scope.constraints` / `scope.predicate`

如果这些入口各自维持独立的过滤语义，会导致：

- metric identity 与请求级 scope 混淆
- binding 不变量与 metric 业务口径互相污染
- 多 component metric 的 sample basis 无法稳定冻结
- compiler 难以统一做 resolvability、conflict detection 与 narrowing 校验

因此，本轮收敛的目标是把这些过滤入口统一到受治理的 `predicate.*` contract 上。

## 收敛结论

### 1. `predicate.*` 成为一等 semantic object

- 业务过滤的主表达统一为受治理的 `predicate.*`
- metric、binding、request scope 通过 ref 消费 predicate，而不是内联 SQL
- predicate contract 只表达受限、deterministic、non-time 的过滤语义

正式规范：

- [`docs/semantic/predicate-schema-contract.zh.md`](/Users/lichengxiang/source/oss/factum/docs/semantic/predicate-schema-contract.zh.md)

### 2. metric / binding / request scope 过滤职责分层

- metric business predicates 属于 metric identity
- carrier row filters 属于 binding consumption invariants
- request scope 只表达本次分析的临时 non-time narrowing
- governance filters 优先且不可覆盖

相关规范：

- [`docs/semantic/metric-v2-schema.zh.md`](/Users/lichengxiang/source/oss/factum/docs/semantic/metric-v2-schema.zh.md)
- [`docs/semantic/typed-binding-contract.zh.md`](/Users/lichengxiang/source/oss/factum/docs/semantic/typed-binding-contract.zh.md)
- [`docs/semantic/compiler-spec.zh.md`](/Users/lichengxiang/source/oss/factum/docs/semantic/compiler-spec.zh.md)

### 3. 多 component metric 必须保留过滤 lineage

- numerator / denominator 的 `qualifier_refs` 不能被压平成单个全局 predicate
- 编译期应区分 shared scope、metric defaults 与 component qualifiers
- artifact 应冻结 shared scope 与 per-component lineage

正式规范：

- [`docs/semantic/predicate-schema-contract.zh.md`](/Users/lichengxiang/source/oss/factum/docs/semantic/predicate-schema-contract.zh.md)
- [`docs/semantic/metric-v2-schema.zh.md`](/Users/lichengxiang/source/oss/factum/docs/semantic/metric-v2-schema.zh.md)

### 4. 时间过滤不进入 predicate contract

- `time_scope` 仍是唯一时间窗口入口
- `scope.predicate` 中出现时间条件必须视为越界
- 相对时间窗口与过程型时间规则属于 process object / metric-process contract，而不是普通 predicate

正式规范：

- [`docs/semantic/predicate-schema-contract.zh.md`](/Users/lichengxiang/source/oss/factum/docs/semantic/predicate-schema-contract.zh.md)
- [`docs/semantic/time-schema-contract.zh.md`](/Users/lichengxiang/source/oss/factum/docs/semantic/time-schema-contract.zh.md)
- [`docs/semantic/compiler-spec.zh.md`](/Users/lichengxiang/source/oss/factum/docs/semantic/compiler-spec.zh.md)

## 本轮产出

本轮文档收敛后，仓库中的正式规范来源如下：

- `predicate contract`：
  - [`docs/semantic/predicate-schema-contract.zh.md`](/Users/lichengxiang/source/oss/factum/docs/semantic/predicate-schema-contract.zh.md)
- `metric 中 qualifier 的语义边界`：
  - [`docs/semantic/metric-v2-schema.zh.md`](/Users/lichengxiang/source/oss/factum/docs/semantic/metric-v2-schema.zh.md)
- `binding 中 row filter 的语义边界`：
  - [`docs/semantic/typed-binding-contract.zh.md`](/Users/lichengxiang/source/oss/factum/docs/semantic/typed-binding-contract.zh.md)
- `scope / time_scope 编译边界`：
  - [`docs/semantic/compiler-spec.zh.md`](/Users/lichengxiang/source/oss/factum/docs/semantic/compiler-spec.zh.md)
- `目录导航与统一原则`：
  - [`docs/semantic/overview.md`](/Users/lichengxiang/source/oss/factum/docs/semantic/overview.md)

## v1 范围冻结

`predicate.*` v1 产品边界与范围冻结说明见 [`docs/semantic/predicate-v1-scope-note.zh.md`](/Users/lichengxiang/source/oss/marivo/docs/semantic/predicate-v1-scope-note.zh.md)。契约文档已从草案正式化为 v1 正式规范。

## 不再保留的内容

原计划稿中以下内容不再作为正式规范正文保留：

- API route 设计
- 错误码表
- 实施阶段划分
- 迁移排期
- Open Questions

原因是这些内容属于实现计划或运行时细节，不应与 `docs/semantic` 下的 object contract 混写，避免形成“两套规范真相”。
