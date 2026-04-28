# Compiler Compatibility Profile 契约（v1）

本文定义 Marivo 中 **Compiler Compatibility Profile v1** 的最小 schema。

它回答的问题只有一个：

> 某个 `metric` 或 `process` 在编译期需要哪些无法从 object contract 稳定推导的约束？

本文是 **compiler-facing catalog contract**，不是 HTTP wire spec，也不是 engine implementation doc。

相关背景建议配合阅读：

- `spec/semantic/overview.md`
- `spec/semantic/metric-v2-schema.zh.md`
- `spec/semantic/process-object-schema.zh.md`
- `spec/semantic/compiler-spec.zh.md`

## Purpose

Compiler compatibility profile v1 的职责只有一个：

> 把那些**无法从 object contract 推导**、但**影响编译期组合合法性**的约束，表达为独立、typed、可发布的 artifact。

## 非目标

本文不定义以下内容：

- pairwise compatibility（metric/process 组合时的双侧约束）—— 留 v2
- runtime 样本量阈值 —— 由 governance context 或 quality gate 执行
- method family allowlist —— 由 engine adapter 在 lowering 时决策
- object public contract 本体
- HTTP 接口形状
- 最终统计方法选择与执行参数

因此，profile **不是**：

- `metric` / `process` identity 的一部分
- engine plan
- runtime policy document
- capability 总表

## 设计原则

### 1. 只补充无法推导的内容

以下由 object contract 推导，**不进入 profile**：

| 推导内容 | 推导来源 |
|---------|---------|
| `supports_observe` | 始终为 true |
| `supports_compare` | `metric.additivity_constraints` 存在 AND `metric.primary_time_ref exists` |
| `supports_decompose` | `metric.additivity_constraints.dimension_policy` in [“all”, “subset”] |
| `supports_test` | `metric.sample_kind in [“numeric”, “rate”, “binary”]` |
| `supports_detect` | `process.anchor_time_ref exists` OR `metric.primary_time_ref exists` |
| `supports_validate` | `metric.sample_kind == “rate”` AND `process.anchor_time_ref exists` |
| `supports_time_projection` | `process.anchor_time_ref exists` |
| `supports_experiment_inference` | `process.context_kind == “experiment_split”` |
| `supports_cohort_inference` | `process.context_kind == “cohort_membership”` |

以下**无法推导**，需要 profile 补充：

| 补充内容 | 说明 |
|---------|------|
| `metric.requirement.contract_modes` | metric 对 process 接口类型的要求 |
| `metric.requirement.context_kinds` | metric 对 process context kind 的要求 |
| `metric.requirement.entity_refs` | metric 对 process 实体流类型的要求 |
| `process.capability.inferential_ready` | process 是否可进入检验流程 |
| `process.capability.supported_sample_summaries` | process 支持哪种 inferential summary |

### 2. 固定缺失时的 compiler 语义

```text
1. profile 缺失 → compiler 优先从 object contract 推导
2. 无法推导 + intent 明确依赖 → 硬失败，返回 PROFILE_MISSING
3. profile 声明不匹配 → 硬失败，返回 PROFILE_NOT_SATISFIED
4. capability 未声明 → 不等于”不支持”，只是”未显式约束”
```

这四个原则不需要配置字段表达，由 compiler 实现。

## 最小 Schema

### Typed 子结构（本文唯一主定义）

```python
from typing import Literal, NotRequired, TypedDict


class ProcessRequirement(TypedDict, total=False):
    “””metric 对 process 的最小编译期要求

    这是本文唯一主定义。metric-v2-schema.zh.md 和 process-object-schema.zh.md
    只引用本文，不重复定义。
    “””
    contract_modes: list[Literal[“context_provider”, “entity_stream”]]
    context_kinds: list[Literal[“experiment_split”, “cohort_membership”]]
    entity_refs: list[str]  # 若需要特定实体流，使用 entity.*
    population_subject_refs: list[str]  # 若需要特定总体主体，使用 subject.*


class ProcessCapability(TypedDict, total=False):
    “””process 可稳定声明的编译期能力

    这是本文唯一主定义。process-object-schema.zh.md 只引用本文，不重复定义。
    “””
    inferential_ready: bool  # 是否可进入 validate/test 流程
    supported_sample_summaries: list[
        Literal[“numeric_sample_summary”, “rate_sample_summary”]
    ]
```

### Profile Envelope

```python
from typing import Literal, NotRequired, TypedDict


ProfileSubjectKind = Literal[“metric”, “process”, “binding”]
ProfileKind = Literal[“requirement”, “capability”]


class CompilerCompatibilityProfile(TypedDict):
    “””第一版最小 profile envelope”””
    profile_ref: str  # 独立命名空间，建议使用 compiler_profile.*
    profile_kind: ProfileKind
    schema_version: Literal[“v1”]
    subject_kind: ProfileSubjectKind
    subject_ref: str  # 唯一目标对象
    subject_revision: NotRequired[int | None]  # publish 时冻结的 subject revision

    # 根据 profile_kind 填充对应字段
    requirement: NotRequired[ProcessRequirement | None]
    capability: NotRequired[ProcessCapability | None]
```

### 字段说明

| Field | Type | Required | 说明 |
|-------|------|----------|------|
| `profile_ref` | string | yes | profile 独立引用，建议 `compiler_profile.*` |
| `profile_kind` | enum | yes | `requirement` 或 `capability` |
| `schema_version` | string | yes | 固定 `”v1”` |
| `subject_kind` | enum | yes | `metric`、`process`、`binding` |
| `subject_ref` | string | yes | profile 作用的唯一对象 |
| `subject_revision` | int | publish 后必有 | profile 最近一次 publish 时绑定的 subject revision；draft 可为空 |
| `requirement` | ProcessRequirement | no | 仅 `profile_kind = requirement` 时填充 |
| `capability` | ProcessCapability | no | 仅 `profile_kind = capability` 时填充 |

### 合法组合

| subject_kind | 合法 profile_kind |
|--------------|-------------------|
| `metric` | `requirement` |
| `process` | `capability` |
| `binding` | `capability` |

第一版不支持：
- `process_pair` 作为 subject_kind
- `pairwise_compatibility` 作为 profile_kind

这些留 v2。

## 示例

### Metric Requirement Profile

```json
{
  “profile_ref”: “compiler_profile.conversion_rate_requirement”,
  “profile_kind”: “requirement”,
  “schema_version”: “v1”,
  “subject_kind”: “metric”,
  “subject_ref”: “metric.conversion_rate”,
  “requirement”: {
    “contract_modes”: [“context_provider”],
    “context_kinds”: [“experiment_split”],
    “population_subject_refs”: [“subject.user”]
  }
}
```

含义：`conversion_rate` metric 要求组合的 process 必须是 `experiment_split` context provider，且总体主体为 `user`。

### Process Capability Profile

```json
{
  “profile_ref”: “compiler_profile.experiment_exp123_capability”,
  “profile_kind”: “capability”,
  “schema_version”: “v1”,
  “subject_kind”: “process”,
  “subject_ref”: “process.exp_123”,
  “capability”: {
    “inferential_ready”: true,
    “supported_sample_summaries”: [“rate_sample_summary”]
  }
}
```

含义：`exp_123` process 可进入 validate/test 流程，支持 rate_sample_summary inferential summary。

### Binding Capability Profile

```json
{
  “profile_ref”: “compiler_profile.binding_user_activity_capability”,
  “profile_kind”: “capability”,
  “schema_version”: “v1”,
  “subject_kind”: “binding”,
  “subject_ref”: “binding.user_activity”,
  “capability”: {
    “inferential_ready”: false
  }
}
```

含义：该 binding 不支持 inferential 流程。

## Compiler 消费语义

compiler 在 Phase 3（组合期校验）消费 profile：

1. 先完成 object public contract 的 normalization
2. 根据 `subject_ref` 加载对应 profile
3. 校验 `subject_revision` 是否与当前 resolved subject revision 一致
4. 按固定原则处理缺失或不匹配
5. 校验结果进入 compile report，不复制 profile 内容

### 错误码

```text
COMPILER_PROFILE_MISSING          # 无法从 object contract 推导 + intent 明确依赖
COMPILER_PROFILE_REVISION_MISMATCH # profile 绑定 revision 与当前 published subject revision 不一致
COMPILER_PROFILE_NOT_SATISFIED    # profile 声明与实际 object contract 不匹配
```

### Compile Report 记录

编译产物只保留：

```python
class ProfileTrace(TypedDict):
    subject_ref: str
    profile_ref: str
    subject_revision: NotRequired[int | None]
    resolved_subject_revision: NotRequired[int | None]
    applied: bool
    reason: Literal[
        “satisfied”, “missing”, “revision_mismatch”, “not_satisfied”, “not_required”
    ]
```

不复制 profile 内容。

## 发布形态

profile 应作为**独立 catalog artifact** 发布：

- 有独立 `profile_ref`
- 有独立 `schema_version`
- 可在不修改 object public contract 的前提下替换或升级
- object 可通过 metadata 层引用 profile，但不把 profile 内容复制回 object schema
- profile 通过 HTTP 显式登记与 publish；对象 publish 不自动生成 profile
- profile publish 时必须绑定当前 published `subject_revision`

## Versioning

profile 升级规则：

1. profile 升级不改变 object identity
2. `schema_version` 变化时，compiler 必须拒绝无法识别的版本
3. v1 → v2 若只增加字段，属于 forward compatible
4. v1 → v2 若改变字段语义或删除字段，需要 migration

## v2 扩展点（预留）

以下内容不在 v1 实现，但预留扩展方向：

```python
# v2 预留：pairwise compatibility
ProfileSubjectKindV2 = Literal[“metric”, “process”, “binding”, “process_pair”]
ProfileKindV2 = Literal[“requirement”, “capability”, “pairwise_compatibility”]

class PairwiseCompatibility(TypedDict, total=False):
    requires_same_anchor_family: bool
    requires_same_partition_semantics: bool

# Envelope 扩展
class CompilerCompatibilityProfileV2(CompilerCompatibilityProfile):
    subject_kind: ProfileSubjectKindV2
    profile_kind: ProfileKindV2
    left_subject_ref: NotRequired[str | None]  # 仅 process_pair
    right_subject_ref: NotRequired[str | None]  # 仅 process_pair
    pairwise: NotRequired[PairwiseCompatibility | None]
```

## 与其他文档的关系

本文建立后：

- `metric-v2-schema.zh.md`：只保留 measurement contract；requirement 结构引用本文，不重复定义
- `process-object-schema.zh.md`：只保留 process contract；capability 结构引用本文，不重复定义
- `compiler-spec.zh.md`：说明 compiler 在 Phase 3 如何消费 profile

## 一句话总结

> 第一版 profile 只做两件事：metric 声明对 process 的最小结构要求，process 声明 inferential-ready 能力；其余全部由 object contract 推导或留给 v2。
