# Inference And Gap Engine

本文档定义 Evidence Engine 中 `assessment` 重算、rule engine、gap management 与 judgment policy 的主题级契约。

状态：draft design。本文吸收原 `inference-rule-engine-contract`、`gap-management-contract`、`assessment-judgment-policy` 与 `rule-registry-contract` 的主线职责。

## 目的

本文统一回答：

- assessment recompute 的评估单元是什么
- rule families 如何组织与排序
- 何时 materialize `InferenceRecord` 与 `EvidenceGap`
- `blocking` / `severity` / `reopen` 如何解释
- 各 `assessment_type` 的最低 judgment policy 从哪里定义
- `rule_id -> rule_family -> assessment_type` 如何稳定解引用

## Evaluation Unit

一次推断评估固定针对单个 `proposition_id` 的单次 recompute。

输入边界固定为：

- target proposition
- 当前 proposition closure 中可解引用的 findings
- 同 proposition 的 prior assessments
- 同 proposition 当前 open gaps

v1 不允许 rule engine 直接跨 proposition 读取其他命题的 assessment 或 gap 状态。

其中：

- evaluation context 的 authority boundary、candidate finding assembly 与 related finding change 映射，以 [`assessment-evaluation-context.md`](assessment-evaluation-context.md) 为准
- 读取面暴露的 `relevant_findings` 仍是 committed latest assessment closure，而不是 recompute candidate set

## Fixed Evaluation Order

对外可观察顺序固定为：

1. 装载 evaluation context
2. 预分配 candidate assessment identity
3. 运行 gate families
4. 聚合 support / oppose evidence
5. 执行 status resolution
6. 执行 gap management
7. 执行 confidence shaping
8. 执行 assessment transition finalization
9. 仅在判断输出变化时提交新的 assessment snapshot

## Rule Family Baseline

v1 固定 rule family 集合：

- `precondition_gate`
- `quality_gate`
- `comparability_gate`
- `support_evidence`
- `oppose_evidence`
- `status_resolution`
- `gap_management`
- `confidence_shaping`
- `assessment_transition`

命名层级固定为：

`assessment_type -> rule_family -> rule_id`

rule family 是稳定阶段分组；更细的业务分类只能落在 rule cluster 或 rule metadata，不得伪装成新 family。

## InferenceRecord Materialization

`InferenceRecord` 用于记录当前 assessment snapshot 采用的显式规则过程。

固定要求：

- 对当前快照有判断贡献的规则结果必须实体化到 record
- `hit / miss / partial` 必须按稳定结构化字段写入
- 不允许把主要语义藏进 narrative notes
- 若候选 assessment 最终未提交，则本轮 candidate records 一并丢弃

rule family grouping 与版本解释必须通过稳定 registry 解引用，不得由消费者通过字符串前缀推断。

## Gap Management

`gap_management` 是唯一 canonical materialization owner。

v1 固定规则：

- gap identity 按 `gap_kind + missing_requirement` 等 requirement semantics 收敛
- `blocking` / `severity` 属于 snapshot-owned classification
- keep 是 materialized result，不是独立 object transition
- resolved gap 再次出现时，打开新的 gap instance

这意味着：

- gap object 的 identity 与 snapshot 上的 blocking/severity 分类分离
- 同一 requirement semantics 在不同 snapshot 中可有不同分类
- reopen 不复用旧 gap instance

## Judgment Policy

`assessment_type` 的最低 judgment policy 不由实现层临时决定，而由主题文档显式固定。

当前 assessment families：

- `change_assessment`
- `decomposition_assessment`
- `anomaly_assessment`
- `correlation_assessment`
- `test_hypothesis_assessment`
- `forecast_assessment`

共同基线：

- `mixed` 表示存在结构化对立证据，不等价于低置信度
- `insufficient` 表示已评估但当前不足以形成更强判断
- 阻塞性 gap 可以和 `supported` / `contradicted` 并存，但不能掩盖缺失条件

family-specific 门槛与例子，保留在独立 judgment policy 文档中。

## Rule Registry

稳定 registry 用于把 `InferenceRecord.rule_id` 解引用到：

- `rule_family`
- `assessment_type`
- `rule_version`
- `registry_version`

registry 是规范元数据契约，不是执行日志，也不承载 judgment policy。

## Implementation-Level Supplements

以下文档把本主题补成可直接实施的 contract：

- [`assessment-evaluation-context.md`](assessment-evaluation-context.md)：evaluation context assembly、candidate finding set、related finding changes
- [`support-oppose-and-status-resolution.md`](support-oppose-and-status-resolution.md)：directional evidence aggregation 与最终 status resolution
- [`gap-confidence-and-transition-materialization.md`](gap-confidence-and-transition-materialization.md)：gap / confidence / transition materialization 与 candidate output discard 规则

## Family-Level Extensions

以下文档是本主题的 family-level extensions：

- [`../precondition-gate-contract.md`](rules/precondition-gate-contract.md)
- [`../quality-gate-contract.md`](rules/quality-gate-contract.md)
- [`../comparability-gate-contract.md`](rules/comparability-gate-contract.md)
- [`../rule-family-design-checklist.md`](rules/rule-family-design-checklist.md)

这些文档细化各 family 的 requirement mapping、record mapping、condition token 与 checklist，但不改写本文已经固定的 evaluation order、family boundary 或 snapshot policy。

## Related Documents

- [`assessment-evaluation-context.md`](assessment-evaluation-context.md)：assessment recompute 的输入组装
- [`support-oppose-and-status-resolution.md`](support-oppose-and-status-resolution.md)：directional evidence 与 status 决议
- [`gap-confidence-and-transition-materialization.md`](gap-confidence-and-transition-materialization.md)：gap / confidence / transition materialization
- [`runtime-pipeline.md`](runtime-pipeline.md)：assessment recompute 在主流水线中的位置
- [`graph-and-reference-semantics.md`](graph-and-reference-semantics.md)：assessment/gap/record 的 edge 与 ref 语义
- [`../assessment.md`](schemas/assessment.md)：assessment、gap、record 的字段 schema
