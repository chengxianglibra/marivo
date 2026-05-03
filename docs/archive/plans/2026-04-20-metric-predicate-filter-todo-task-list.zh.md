# Metric Predicate / Filter 实施 Todo Task List

## 概述

本文将 `metric predicate / filter contract` 设计稿拆解为可落地实施的任务清单，目标是在 **`predicate.*` 成为一等 semantic object、metric/binding/request scope 统一消费受治理过滤语义、时间过滤继续留在 `time_scope`** 的前提下，完成一套边界清晰、可验证、可直接按目标态实现的交付方案。

一句话结论：

- v1 先做“**predicate contract 冻结 + consumer 接口收敛 + compiler 校验 + artifact lineage 冻结 + compare-like intents 复用**”。
- 不做开放式 SQL predicate DSL，不把时间条件塞进 `predicate` contract，也不让 metric、binding、request scope 各自维护平行过滤语义。
- 任务拆解必须围绕六个交付面推进：predicate object、consumer surfaces、compiler validation、artifact lineage、runtime/lowering 边界、测试与文档。

## 文档依据

- [`docs/semantic/predicate-schema-contract.zh.md`](/Users/lichengxiang/source/oss/factum/docs/semantic/predicate-schema-contract.zh.md)
- [`plan/2026-04-20-metric-predicate-filter-contract.zh.md`](/Users/lichengxiang/source/oss/factum/plan/2026-04-20-metric-predicate-filter-contract.zh.md)
- [`docs/semantic/metric-v2-schema.zh.md`](/Users/lichengxiang/source/oss/factum/docs/semantic/metric-v2-schema.zh.md)
- [`docs/semantic/typed-binding-contract.zh.md`](/Users/lichengxiang/source/oss/factum/docs/semantic/typed-binding-contract.zh.md)
- [`docs/semantic/compiler-spec.zh.md`](/Users/lichengxiang/source/oss/factum/docs/semantic/compiler-spec.zh.md)
- [`docs/semantic/overview.md`](/Users/lichengxiang/source/oss/factum/docs/semantic/overview.md)

## 实施范围

### 本次必须覆盖

- 落地 `predicate.*` 的最小 semantic object contract 与治理边界
- 为 metric qualifier、binding row filter、request scope 提供统一过滤语义入口
- 在 compiler 中实现 predicate resolvability、usage 合法性、non-time 边界、narrowing 与冲突校验
- 在 observation artifact / lineage 中冻结 shared scope、metric defaults 与 per-component qualifier lineage
- 让 compare-like intents 复用 observation 中冻结的 filtering lineage，而不是重建或猜测
- 提供最小可用的测试、文档和治理说明

### 本次明确不做

- 开放式 SQL predicate string 或通用 filter DSL
- 在 `predicate` contract 中支持时间过滤、相对时间窗口或 process 语义
- 面向调用方开放自定义 predicate authoring UI 或独立治理后台
- 在 public contract 中暴露 engine-specific lowering 模板、物理列名或 SQL 片段
- 错误码全表、数据库 DDL 细节、engine-specific 优化实现

## 交付原则

- 粒度以“单个 owner 可独立完成并验收”为准，避免写成泛泛的“支持 predicate”。
- 边界以 contract 分层为准，不让 metric、binding、scope、compiler、artifact 职责互相渗透。
- 每个任务都必须有明确交付物和验收标准，避免“完成实现但无法判断是否完成”。
- 先冻结 predicate object 与 usage 规则，再打通 compiler / runtime 主链路，最后补 lineage、测试和文档。

## 建议实施顺序

1. T1 冻结 `predicate.*` v1 scope 与最小 contract
2. T2 建立 predicate object 的 catalog / storage / lifecycle 基础
3. T3 收敛 metric / binding / request scope 的 consumer surfaces
4. T4 在 compiler 落 usage、narrowing、冲突与 non-time 校验
5. T5 打通 artifact lineage 与 compare-like intent 复用
6. T6 收 runtime / lowering 边界与失败面
7. T7 补测试矩阵与 golden cases
8. T8 文档与治理收尾

说明：

- T2 是 T3/T4 的前置，因为 consumer 与 compiler 都依赖稳定的 `predicate.*` 可解析对象。
- T3 与 T4 可并行推进，但都必须以同一份 usage taxonomy 和 expression contract 为准。
- T5 必须在 T4 之后完成，否则 artifact 无法冻结稳定的 effective scope lineage。
- T6 只收敛 contract-to-lowering 边界，不要求本轮做完整 engine 优化。

## Todo Task List

## 一、Scope 与 Contract 冻结

- [x] 任务 1.1：冻结 `predicate.*` 的 v1 产品边界
  - 交付物：scope note 或 decision record
  - 关键内容：只支持 deterministic、non-time、conjunctive expression；不支持 SQL AST、`or`、`not`、动态变量、时间条件
  - 验收标准：团队对”predicate 表达什么、明确不表达什么”无歧义

- [x] 任务 1.2：冻结 `PredicateHeader` 最小字段集
  - 交付物：schema 草案
  - 最小字段：`predicate_ref`、`display_name`、`description`、`subject_ref`、`predicate_contract_version`
  - 验收标准：字段足以支撑 catalog identity、显示、解析与版本治理

- [x] 任务 1.3：冻结 `PredicatePayload` 最小字段集
  - 交付物：payload schema 草案
  - 最小字段：`expression`、`allowed_usage`、`time_policy`
  - 验收标准：字段足以支撑 compiler 校验与 consumer 使用，不引入执行层细节

- [x] 任务 1.4：冻结 `allowed_usage` taxonomy
  - 交付物：usage 字典
  - 范围：`metric_qualifier`、`carrier_row_filter`、`request_scope`、`governance_policy`
  - 验收标准：每种 usage 都有明确消费方、禁止场景与 compiler 校验语义

- [x] 任务 1.5：冻结 v1 表达式子集与值域约束
  - 交付物：expression constraints 说明
  - 范围：`PredicateAtom`、`PredicateConjunction`、白名单操作符、`between`/`in`/`is_null` 等值约束
  - 验收标准：validator 可直接按该说明实现，不需要二次猜测

- [x] 任务 1.6：冻结 effective scope 合成规则
  - 交付物：compiler 语义说明
  - 关键内容：区分 `shared_effective_scope`、`metric_default_predicates`、`component_qualifier_predicates`
  - 验收标准：多 component metric 不再被允许压平成单一全局 predicate

## 二、Predicate Object 与 Catalog 基础设施

- [x] 任务 2.1：定义 predicate object 的存储与序列化模型
  - 交付物：内部 model / persistence schema 草案
  - 验收标准：对象可被创建、读取、版本化，且主契约与 metadata envelope 分层明确

- [x] 任务 2.2：确定 predicate lifecycle 与现有 semantic lifecycle 的对齐方式
  - 交付物：生命周期说明
  - 范围：draft / active / deprecated 与 validate / publish/activate 的最小动作
  - 验收标准：predicate 不引入一套平行生命周期语义

- [x] 任务 2.3：实现 predicate catalog list/get/resolve 最小读面
  - 交付物：catalog/read API 或 service 能力
  - 验收标准：metric、binding、compiler 可稳定按 `predicate_ref` 解析到对象

- [x] 任务 2.4：实现 predicate validate 最小检查
  - 交付物：validate 逻辑
  - 范围：ref 前缀、subject 可解析、target refs 可解析、expression 非空、operator 白名单、time policy 合法
  - 验收标准：非法 predicate 在进入 active/ready 前会被拦截

- [x] 任务 2.5：明确 predicate readiness 与 runtime 消费边界
  - 交付物：readiness 约定
  - 验收标准：runtime 默认只消费 active + ready predicate；未就绪对象不会被静默采用

## 三、Metric / Binding / Request Scope 消费面收敛

- [x] 任务 3.1：收敛 `MeasurementComponent.qualifier_refs`
  - 交付物：metric contract/service 改造
  - 关键内容：`qualifier_refs` 只引用 `predicate.*`，不再接受局部 SQL predicate string 或平行 DSL
  - 验收标准：component business semantics 全部走受治理 predicate refs

- [x] 任务 3.2：落 metric-level `default_predicate_refs`
  - 交付物：metric schema/service 变更
  - 验收标准：共享过滤与 component qualifier 有稳定分层，不再靠重复复制 qualifier 表达

- [x] 任务 3.3：收敛 `CarrierBinding.row_filter_refs`
  - 交付物：binding contract/service 改造
  - 关键内容：只表达 carrier invariants，不表达某个 metric 的 business semantics
  - 验收标准：`row_filter_refs` 与 `qualifier_refs` 的职责边界在 schema 和 validator 中都可执行

- [x] 任务 3.4：收敛 request `scope.predicate`
  - 交付物：typed intent 输入约束更新
  - 关键内容：请求级 predicate 与 `predicate.*` 对齐为同一表达式 contract，而不是平行 filter 语言
  - 验收标准：request scope 可被 compiler 统一解析与校验

- [x] 任务 3.5：明确 governance filters 的接入边界
  - 交付物：governance integration note
  - 验收标准：policy filters 的优先级、不可覆盖性、冲突处理方式清晰稳定

## 四、Compiler 校验与 Narrowing 主链路

- [x] 任务 4.1：实现 predicate contract 级 validate
  - 交付物：validator 实现
  - 校验范围：`predicate_ref` 前缀、`subject_ref` 可解析、`target_ref` 可解析、`allowed_usage` 非空、`time_policy=non_time_only`、expression deterministic
  - 验收标准：contract 自身不合法时，不会进入后续 compile 流程

- [ ] 任务 4.2：实现 usage-level validation
  - 交付物：compiler/validator 校验逻辑
  - 范围：metric qualifier 只能用 `metric_qualifier`、binding row filter 只能用 `carrier_row_filter`、request scope 只能用 `request_scope`
  - 验收标准：usage 不匹配时稳定失败，且错误归因清晰

- [ ] 任务 4.3：实现 request scope non-time / conjunctive 校验
  - 交付物：scope validation 逻辑
  - 验收标准：时间条件、`or`、`not`、动态变量都会 fail closed

- [ ] 任务 4.4：实现 request scope narrowing proof 或 fail-closed 规则
  - 交付物：narrowing 校验逻辑
  - 关键内容：request scope 只能进一步收窄，不能覆盖、移除或放宽 metric / binding / governance filters
  - 验收标准：无法静态证明为 narrowing 时稳定拒绝

- [ ] 任务 4.5：实现 predicate conflict detection
  - 交付物：compiler 诊断逻辑
  - 范围：shared scope、metric defaults、component qualifiers、row filters、governance filters 之间的冲突与不可解析组合
  - 验收标准：不会把明显冲突静默下推到 engine 执行后才暴露

- [ ] 任务 4.6：定义 resolved predicate lineage 的 compiler 输出
  - 交付物：IR / internal summary schema
  - 验收标准：lowering 前已得到可冻结、可解释、可比较的过滤 lineage，而不是只剩执行后 SQL

## 五、Artifact Lineage 与 Downstream 复用

- [ ] 任务 5.1：在 observation artifact 中冻结 shared scope
  - 交付物：artifact schema 扩展
  - 验收标准：governance、carrier、request scope 合成后的 shared scope 可稳定重放

- [ ] 任务 5.2：在 artifact 中冻结 metric default predicates
  - 交付物：lineage 字段扩展
  - 验收标准：metric identity 中的共享过滤不再丢失

- [ ] 任务 5.3：在 artifact 中冻结 per-component qualifier lineage
  - 交付物：component lineage 扩展
  - 验收标准：numerator / denominator 或其他多 component metric 的 sample basis 可单独追溯

- [ ] 任务 5.4：补 component effective scope fingerprint 或等价摘要
  - 交付物：fingerprint / summary 字段
  - 验收标准：下游能快速判断 component 间与跨 observation 间的可比性

- [ ] 任务 5.5：让 compare-like intents 复用 frozen predicate lineage
  - 交付物：compare / validate / test / diagnose 复用逻辑
  - 验收标准：下游不再重建、猜测或重新解析 metric predicate 语义

- [ ] 任务 5.6：冻结 artifact 与 read surface 的边界
  - 交付物：read-model decision note
  - 验收标准：artifact 不复制 predicate 对象全文；read surface 暴露的是 lineage 摘要而不是执行 DSL

## 六、Runtime / Lowering 边界收口

- [ ] 任务 6.1：明确 predicate 到 binding surface 的映射责任
  - 交付物：lowering responsibility note
  - 验收标准：predicate 不负责物理列名，binding surface 才负责 grounding

- [ ] 任务 6.2：实现 lowering 前的 normalized predicate input
  - 交付物：runtime/compiler 内部数据结构
  - 验收标准：execution 层消费的是已解析、已校验、已分层的 predicate lineage

- [ ] 任务 6.3：补齐多 component metric 的 component-by-component lowering 输入
  - 交付物：runtime 输入改造
  - 验收标准：不同 component 的 qualifier lineage 不会在 lowering 前被压平

- [ ] 任务 6.4：定义不支持场景的失败策略
  - 交付物：boundary note 与结构化 diagnostics
  - 范围：无法解析 target value domain、binding 无法 grounding、narrowing 无法证明、component lineage 丢失
  - 验收标准：不支持时显式失败，不做静默降级

## 七、测试与验收样例

- [x] 任务 7.1：补 predicate schema / validator 单元测试
  - 交付物：unit tests
  - 验收标准：非法 ref、空 expression、非法 op、time target、值域不匹配都能稳定失败

- [x] 任务 7.2：补 usage 校验测试
  - 交付物：unit tests
  - 场景：metric 错用 carrier filter、binding 错用 metric qualifier、request scope 错用 governance predicate
  - 验收标准：每类 usage 误用都有稳定失败面

- [x] 任务 7.3：补 request scope narrowing / conflict detection 测试
  - 交付物：compiler tests
  - 验收标准：收窄成功、放宽失败、冲突失败、无法证明失败都有明确样例

- [x] 任务 7.4：补多 component metric lineage 测试
  - 交付物：metric/compiler/integration tests
  - 场景：rate metric、average metric、共享 defaults + component qualifiers
  - 验收标准：artifact 中能分别看到 shared scope、defaults、per-component qualifiers

- [x] 任务 7.5：补 observe -> compare-like intent 复用测试
  - 交付物：integration tests
  - 验收标准：下游 compare-like intents 使用上游冻结 lineage，而不是重算 predicate 语义

- [x] 任务 7.6：准备最小 golden cases
  - 交付物：golden cases 文档或样例集
  - 建议样例：单 component 成功路径、rate metric 双 component、request narrowing 成功、time predicate 非法、usage 非法、binding invariant 与 metric business predicate 混用失败
  - 验收标准：每个 case 都能映射到一个明确 contract 边界或回归风险

## 八、文档与治理收尾

- [ ] 任务 8.1：更新共享语义文档
  - 交付物：文档 PR 清单
  - 最少涉及：`metric-v2-schema`、`typed-binding-contract`、`compiler-spec`、`overview`
  - 验收标准：所有正式规范只引用同一套 predicate contract 结论，不再各自发明局部 filter 语义

- [ ] 任务 8.2：更新共享 agent 指南
  - 交付物：[`docs/agent-guide.md`](/Users/lichengxiang/source/oss/factum/docs/agent-guide.md) 更新项
  - 验收标准：agent 层不会继续把 filter 问题错误下沉成 ad-hoc SQL 或时间 predicate

- [ ] 任务 8.3：补对象治理说明
  - 交付物：governance note
  - 内容：predicate 的 authoring 边界、validate/publish 约束、catalog 使用约定、哪些过滤语义必须建模为 `predicate.*`
  - 验收标准：团队对 predicate 的创建、发布、消费边界有统一操作约定

## 建议验收门槛

- 至少一个 `predicate.*` object 能完成 create -> validate -> activate -> resolve -> compile -> artifact freeze 全链路验证
- 单 component 与多 component metric 都能稳定保留 filtering lineage
- request scope 只能收窄、不能放宽的规则已由自动化测试覆盖
- compare-like intents 已明确复用上游冻结 lineage，而不是重建 predicate 语义
- 正式规范、任务清单和共享 guide 之间不存在两套相互冲突的边界描述
