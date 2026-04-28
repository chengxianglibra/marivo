# Predicate Filter Lineage Golden Cases

状态：accepted acceptance note。本文冻结 `predicate filter lineage` 的最小验收样例集，用作人工验收、回归核对与后续自动化扩展的统一基线。

## 目的

本样例集只覆盖 v1 最小验收面，不尝试替代完整单元测试或集成测试。

它回答三个问题：

- 给定一个典型业务场景，observation artifact 的 `predicate_filter_lineage` 应包含什么
- 每个场景测试的是哪条 contract 边界或回归风险
- 仓库里哪条现有自动化测试是这个样例的真值锚点

## 使用规则

- 样例只冻结关键行为级预期，不枚举全量 lineage 结构
- 每个样例都必须绑定明确的 contract 边界或回归风险
- 每个样例都必须能追溯到现有测试锚点，避免形成第二套脱节真值
- 若未来 runtime 行为变更，必须同步更新本文档与对应测试

## 最小样例集

| case_id | 场景 | lineage layers active | 预期 lineage 行为 | contract 边界 / 回归风险 | test anchors |
| --- | --- | --- | --- | --- | --- |
| `single_component_success` | count metric 仅有 carrier row filter | shared (carrier) | `component_qualifier_lineages` 含一个 `count_target`；`component_effective_scopes` 的 `effective_scope_refs` 包含 carrier ref | 基线：单 component metric 能正确产出 lineage | `tests/test_observe_artifact_lineage.py::TestSharedEffectiveScopeInArtifact::test_carrier_row_filter_in_shared_effective_scope` |
| `rate_metric_dual_component` | rate metric 的 numerator 和 denominator 各有不同 qualifier_refs | shared + per-component qualifiers | 两个 component 均出现在 `component_qualifier_lineages`；qualifier 不跨 component 泄漏；fingerprint 不同 | 隔离性：component qualifier 独立，不压平 | `tests/test_predicate_lineage.py::TestBuildPredicateFilterLineage::test_dual_qualifiers_on_both_components` |
| `request_narrowing_success` | observe 携带 request_scope predicate 收窄 shared scope | shared (carrier + request_scope) | `request_scope_ref` 出现在 `shared_effective_scope`，并纳入所有 component 的 `effective_scope_refs` | 收窄：request scope 可与其他 shared layer 组合 | `tests/test_observe_artifact_lineage.py::TestSharedEffectiveScopeInArtifact::test_request_scope_in_shared_effective_scope` |
| `time_predicate_illegal` | 使用 time policy 不等于 `non_time_only` 的 predicate 作为 metric qualifier | N/A (compile 阶段拦截) | compiler 拒绝，返回 time policy 校验错误 | 时间排除：时间依赖 predicate 不可作为非时间过滤 | `tests/test_predicate_contract_validator.py::TestTimePolicy::test_other_value_fails` |
| `usage_illegal` | 声明 `governance_policy` 用途的 predicate 被用作 `metric_qualifier` | N/A (compile 阶段拦截) | compiler 拒绝，返回 usage mismatch 错误 | 用途强制：每个消费点必须匹配 `allowed_usage` | `tests/test_predicate_usage_validation.py::MetricPredicateUsageTests::test_qualifier_refs_reject_carrier_row_filter_usage` |
| `binding_invariant_vs_metric_business_mixed` | rate metric 同时有 `default_predicate_refs`（不变量）和 per-component `qualifier_refs`（业务语义） | shared + defaults + per-component qualifiers | default refs 出现在每个 component 的 `effective_scope_refs`；qualifier refs 仅出现在对应 component；无压平 | 层次分离：defaults（跨 component 不变量）与 qualifiers（per-component 业务语义）不可混淆 | `tests/test_predicate_lineage.py::TestBuildPredicateFilterLineage::test_no_flattening_defaults_vs_qualifiers` |
| `compare_lineage_reuse_success` | 同一 metric 两次 observe 后执行 compare | 两侧 observe 均冻结 lineage | compare 的 `resolved_input_summary.predicate_lineage` 携带 refs + fingerprint，不含 expression / SQL | 复用合约：compare-like intent 直接复用上游冻结 lineage，不重新计算 | `tests/test_observe_compare_lineage_reuse.py::TestCompareReusesObservationLineage::test_compare_does_not_recalculate_predicate_semantics` |
| `compare_lineage_mismatch_rejection` | compare 的一侧 observe 有 lineage，另一侧没有 | 不对称 | compare 返回 NOT_COMPARABLE，提示 lineage 缺失 | 不匹配拒绝：一侧缺失 lineage 是致命错误 | `tests/test_observe_compare_lineage_reuse.py::TestCompareLineageMismatchRejection::test_compare_rejects_lineage_metadata_mismatch` |

## 样例解释

### `single_component_success`

- 用于验收最基础的单 component lineage 路径
- 若该样例不能稳定产出 carrier ref 在 `shared_effective_scope` 中的 lineage，说明 shared scope 冻结链路已回归

### `rate_metric_dual_component`

- 用于验收多 component metric 的 qualifier 隔离性
- 关键验收点是：numerator 的 qualifier 不出现在 denominator 的 `effective_scope_refs` 中，反之亦然
- 本样例同时验证 fingerprint 区分度：不同 qualifier 组合必须产出不同 fingerprint

### `request_narrowing_success`

- 用于验收 request scope 在 shared scope 中的可组合性
- 验收时关注点是：`request_scope_ref` 被纳入 shared_effective_scope 而非 component qualifier
- 若 request scope 被错误归入 component qualifier 层，说明 layer 归类逻辑已回归

### `time_predicate_illegal`

- 用于验收 v1 不支持时间 predicate 的硬边界
- 验收时关注点是：compiler 必须在 compile 阶段拦截，不允许 time predicate 进入 metric qualifier 或 request scope

### `usage_illegal`

- 用于验收 `allowed_usage` 与消费点的强制匹配
- 验收时关注点是：usage 不匹配时 compiler 必须稳定失败，不做静默降级

### `binding_invariant_vs_metric_business_mixed`

- 用于验收 `default_predicate_refs`（不变量层）与 `qualifier_refs`（业务语义层）的层次分离
- 验收时关注点是：default refs 出现在所有 component 的 `effective_scope_refs` 但不出现在任何 component 的 `qualifier_refs` 中
- 若 defaults 被压平到 qualifier_refs 或 qualifier 泄漏到其他 component，说明层次分离已回归

### `compare_lineage_reuse_success`

- 用于验收 compare-like intent 直接复用上游冻结 lineage 的核心合约
- 验收时关注点是：compare artifact 的 `resolved_input_summary.predicate_lineage` 不含 expression / SQL / lowering_template 等执行层细节
- 若 compare 重新计算了 predicate 语义或在 lineage 中暴露了执行层字段，说明复用合约已回归

### `compare_lineage_mismatch_rejection`

- 用于验收一侧缺失 lineage 时 compare 的 fail-closed 行为
- 验收时关注点是：返回 NOT_COMPARABLE 而非静默跳过 lineage 校验

## 维护要求

- 新增 predicate usage 类别或修改 lineage 结构时，必须评估本文档是否需要增补
- 若测试锚点重命名，必须同步更新本文档中的 test anchors
- 若某个样例需要靠 warning / fallback 才能表达其核心语义，应单列为扩展样例，不覆盖本文的 happy-path / fail-closed 最小集
