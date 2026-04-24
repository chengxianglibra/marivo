# Predicate Lineage Artifact vs Read Surface Boundary v1

状态：accepted design note。

配套文档：
- [Predicate Schema Contract](predicate-schema-contract.zh.md)
- [Metric v2 Schema](metric-v2-schema.zh.md)

## 1. Purpose

本文回答的是：

- artifact 中 `predicate_filter_lineage` 存储了什么（refs vs expressions）
- read surface（SessionStateView、PropositionContextView）暴露了什么
- compare/test artifact 中的 predicate lineage summary 包含什么
- 二者之间的边界如何执行

本文不定义：

- predicate expression 的内部结构
- read surface 上是否需要新增 predicate lineage 暴露面
- evidence engine finding payload 中 predicate lineage 的完整 schema

## 2. v1 Decision

v1 固定采用 **"refs-only in artifact, handles-only on read surface"** 语义：

### 2.1 Artifact 存储边界

- `predicate_filter_lineage` 只存储 refs（`predicate.gov1`、`predicate.car1` 等）和 scope fingerprint
- `scope_fingerprint` 是 ref 集合的确定性摘要（SHA-256 前 16 hex 字符），不是 expression tree
- 不存储 predicate expression、SQL 片段、物理列名或 lowering 模板
- 这已经是当前实现的现实状态，本 note 正式冻结

### 2.2 Read Surface 暴露边界

- SessionStateView 和 PropositionContextView 从 findings/propositions/assessments 组装
- 唯一 artifact 数据在 read surface 上是 `artifact_refs`（`{artifact_id, step_ref}` 对）
- Findings 的 `subject_json` 和 `payload_json` 不包含 predicate lineage 详情
- 如果未来 read surface 需要 predicate lineage 摘要，应使用 ref-based summary，不复制 expression tree

### 2.3 Compare/Test Artifact 中的 Predicate Lineage Summary

- compare/test artifacts 的 `resolved_input_summary.predicate_lineage`（compare）或 `source_lineage.predicate_lineage`（test）是从上游冻结 lineage 派生的 refs-only 摘要
- 摘要内容：`reuse_source`、`metric_default_predicate_refs`、`component_fields`、`left/right_shared_effective_scope`（仅 refs）、`left/right_scope_fingerprints`
- 不包含 predicate expression 或 lowering 细节
- 摘要结构由 `PredicateLineageReuseSummary` TypedDict 强制

## 3. Why This Boundary

原因固定如下：

- predicate expression 属于 compiler/lowering 内部细节，暴露到 artifact 会破坏 compiler/lowering 解耦
- read surface 的消费者（agent、UI）不需要 predicate expression 来做决策；它们需要的是 ref-based provenance（"这次观测受到了哪些 predicate 过滤"）
- 如果 expression tree 泄漏到 read surface，会导致 surface 与 compiler 内部结构强耦合，compiler 无法自由演进 expression 表示

## 4. Boundary Enforcement

v1 的执行手段：

1. **TypedDict 结构强制**：`predicate_filter_lineage` 的 TypedDict（ir.py）只包含 ref 列表和 fingerprint，不包含 expression 字段
2. **RefBoundary 已有强制**：`ref_boundary.py` 的 `assert_no_semantic_refs_in_canonical_payload` 已阻止 semantic refs 进入 canonical payload
3. **Extractor 不复制 expression**：observe_extractor、compare_extractor、test_extractor 均不复制 predicate expression 到 findings — 已验证
4. **Runtime boundary assertion**：`assert_predicate_lineage_refs_only()` 递归检查 lineage 结构不含 `expression`、`sql`、`lowering_template`、`physical_column` 等禁止键，在测试中使用
5. **Reuse summary 结构强制**：`PredicateLineageReuseSummary` TypedDict 字段均为 refs 和 fingerprints，无 expression 字段

## 5. Revisit Trigger

只有以下条件出现时才重新评估：

- read surface 需要展示 predicate expression 供 operator 审计
- compare/test 需要携带 predicate expression 做跨 session 重放
- 下游 consumer 需要 expression tree 做独立 lowering

在这些条件未出现前，保持 refs-only 结论不变。
