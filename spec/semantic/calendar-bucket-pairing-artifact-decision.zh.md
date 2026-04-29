# Calendar Bucket Pairing Artifact Decision v1

> **Superseded** by `docs/superpowers/specs/2026-04-29-calendar-data-policy-redesign-design.md`.
> This document describes the pre-redesign architecture and is kept for historical reference.

状态：accepted design note。本文冻结 `calendar alignment policy` 在 v1 对 `bucket_pairing` 暴露面的治理结论：`bucket_pairing plan` 只作为 observation artifact 中 `resolved_policy_summary` 的 metadata 输出，不升格为可单独引用的一等 artifact。

配套文档：

- `spec/semantic/calendar-alignment-policy.zh.md`
- `spec/analysis/intents/atomic/observe.md`
- `docs/api/intent-steps.md`

## 1. Purpose

本文回答的是：

- v1 是否需要把 `bucket_pairing` 单独建模为可复用 artifact
- 下游 compare-like intents 应该复用哪一个冻结面
- operator / caller 应该从哪里读取 pairing provenance

本文不定义：

- pairing 算法本身
- comparability gate 的阻断规则
- 新的 artifact family 或独立读取 API

## 2. v1 Decision

v1 固定采用“metadata only”语义：

- `bucket_pairing` 保留在 observation artifact 的 `resolved_policy_summary` 中
- `bucket_pairing` 不产生新的 `artifact_id`
- `bucket_pairing` 不提供独立 typed ref
- `bucket_pairing` 不新增单独读取、列表或 lineage surface

换句话说，v1 的可复用边界是“带有 frozen alignment metadata 的 observation artifact”，而不是“独立的 pairing artifact”。

## 3. Why Not First-Class Artifact

v1 不升格为一等 artifact，原因固定如下：

- pairing plan 只服务于解释 observation 如何得到 comparability semantics，本身不是独立分析产物
- compare、attribute、validate、test 的输入边界已经稳定在 observation typed ref，继续拆出平行 ref 会扩大 intent contract
- 当前下游复用只需要读取同一份 frozen summary，不需要对 pairing plan 做独立生命周期治理
- 若把 pairing plan 抽成新 artifact，需要同时定义 identity、storage、typed ref、state/context 可见性与 lineage 归属，超出 v1 必要范围

## 4. Canonical Reuse Surface

v1 的冻结与复用边界如下：

- artifact-level reuse surface：`observe` 返回体与持久化 payload 中的 `resolved_policy_summary`
- operator-facing provenance surface：`step_metadata.typed_semantic_snapshot.compile_context.calendar_policy_binding`

其中：

- 下游 compare-like intents 只能通过上游 observation 的 `resolved_policy_summary` 复用 pairing plan
- lineage / metadata 负责回答“这次最终绑定了哪个 policy、哪个 calendar source/version”
- 二者都不引入新的独立 artifact family

## 5. Minimum Contract Implications

对现有 contract 的最小影响固定如下：

- `ResolvedPolicySummary.bucket_pairing` 继续作为 canonical field 保留
- compare-like intents 不新增 `bucket_pairing_ref`、`calendar_alignment_ref` 或其他平行 typed ref
- 文档中若提到“future first-class artifact”，应改为“v1 明确不做，后续版本如需引入需单独立项”

## 6. Revisit Trigger

只有在出现以下新增需求时，才重新评估是否升格为一等 artifact：

- pairing plan 需要被 observation 之外的多个 artifact 独立引用
- pairing plan 需要独立缓存、独立权限控制或独立生命周期
- pairing plan 需要独立暴露读取 API 供 operator/UI 直接浏览，而不仅是作为 observation 明细字段

在这些条件未出现前，保持 `metadata only` 结论不变。
