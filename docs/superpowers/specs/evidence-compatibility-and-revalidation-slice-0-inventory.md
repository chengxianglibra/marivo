# Evidence Compatibility and Revalidation — Slice 0 Contract Inventory

状态：已实现

## 范围与结论

本 inventory 只盘点当前 `analysis-artifact/v10` contract，不增加公共 API，也不实现
`ArtifactAuthorityContext`、compatibility、revalidation 或 operator admission。

结论：当前 13 个 Artifact family、23 个可恢复 FrameMeta variant 都已经具备以下两种路径
之一：

1. 根 Artifact 直接保留 canonical scoped semantic fingerprint；
2. derived Artifact 保留 exact source Artifact ref，可沿 committed source lineage 恢复根
   authority。

因此 Slice 2 不需要 Artifact schema cutover，只需要 normalization。健康的当前 schema 不需要
通过 catalog-only 猜测补全 scoped authority；source Artifact 缺失或损坏仍是 identity/integrity
错误，不能降级成 catalog-only authority。

对应的可执行 inventory 位于：

- `marivo/analysis/_authority_inventory.py`；
- `tests/test_analysis_authority_inventory.py`。

## Closed evidence vocabulary

### EvidenceSubject

| discriminator | type |
| --- | --- |
| `metric` | `Subject` |
| `event` | `EventSubject` |
| `lifecycle` | `LifecycleSubject` |
| `subject_set` | `SubjectSetSubject` |

### EvidenceScope

| discriminator | type |
| --- | --- |
| `metric` | `AnalysisScope` |
| `event` | `EventAnalysisScope` |
| `event_funnel` | `EventFunnelAnalysisScope` |
| `event_time_to_event` | `EventTimeToEventAnalysisScope` |
| `lifecycle` | `LifecycleAnalysisScope` |
| `subject_set` | `SubjectSetAnalysisScope` |

### EpistemicKind

完整 closed literal 为：

```text
observed | algebraic | estimated | tested | predicted | candidate
```

## Artifact family 与 FrameMeta inventory

所有 FrameMeta 共享以下 identity：

```text
artifact_ref = artifact_id or ref
session_id
content_hash
lineage
evidence_digest.fingerprint
```

`artifact_id` 是 committed Artifact 的首选 canonical identity；当 metadata 尚未携带
`artifact_id` 时，extractor 必须使用必有的 `ref`，不能生成空 `artifact_ref`。

`lineage.steps[].inputs` 和 `lineage.external_inputs` 同时可能包含 semantic ref 与 Artifact ref，
不能仅凭字符串形状猜测类别。后续 extractor 必须优先使用下表的 typed source 字段，再使用
lineage 验证 dependency closure。

| Artifact family | concrete FrameMeta variant | catalog identity | direct scoped identity | typed source identity | Slice 2 action |
| --- | --- | --- | --- | --- | --- |
| `MetricFrame` | `MetricFrameMeta` | `catalog_definition_fingerprint` | `semantic_dependency_digest`，其中 entries 保留 exact ref 和 body digest | optional `cohort` | normalization |
| `EventFrame[journey]` | `EventFrameMeta` | `catalog_definition_fingerprint` | `event_fingerprints[event_ref]` | optional `cohort` | normalization |
| `EventFrame[funnel]` | `EventFunnelFrameMeta` | `catalog_definition_fingerprint` | inherited `event_fingerprints` | `source_journey_ref` + `source_journey_fingerprint`，optional `cohort` | normalization |
| `EventFrame[time_to_event]` | `EventTimeToEventFrameMeta` | `catalog_definition_fingerprint` | inherited `event_fingerprints` | `source_journey_ref` + `source_journey_fingerprint`，optional `cohort` | normalization |
| `LifecycleFrame[history]` | `LifecycleHistoryFrameMeta` | `catalog_definition_fingerprint` | `state_model_ref` + `state_model_fingerprint`；`event_fingerprints[event_ref]` | optional `cohort` | normalization |
| `LifecycleFrame[distribution]` | `LifecycleDistributionFrameMeta` | `catalog_definition_fingerprint` | `state_model_ref` + `state_model_fingerprint` | `source_history_ref` + `source_history_fingerprint` | normalization |
| `LifecycleFrame[transitions]` | `LifecycleTransitionsFrameMeta` | `catalog_definition_fingerprint` | `state_model_ref` + `state_model_fingerprint` | `source_history_ref` + `source_history_fingerprint` | normalization |
| `LifecycleFrame[dwell]` | `LifecycleDwellFrameMeta` | `catalog_definition_fingerprint` | `state_model_ref` + `state_model_fingerprint` | `source_history_ref` + `source_history_fingerprint` | normalization |
| `LifecycleFrame[violations]` | `LifecycleViolationsFrameMeta` | `catalog_definition_fingerprint` | `state_model_ref` + `state_model_fingerprint` | `source_history_ref` + `source_history_fingerprint` | normalization |
| `SubjectSet` | `SubjectSetMeta` | `catalog_definition_fingerprint` | none；authority 来自 source closure | `source.artifact_ref` + `source.artifact_fingerprint` | normalization |
| `DeltaFrame[metric]` | `DeltaFrameMeta` | `catalog_definition_fingerprint` | `source_dependency_digests`，并保留 `comparison_identity` | `source_current_ref` + `source_baseline_ref` | normalization |
| `DeltaFrame[cumulative metric]` | `CumulativeDeltaFrameMetaV1` | inherited `catalog_definition_fingerprint` | inherited `source_dependency_digests` | inherited current/baseline refs | normalization |
| `DeltaFrame[funnel]` | `FunnelDeltaFrameMeta` | `catalog_definition_fingerprint` | none；authority 来自两个 source closures | current/baseline funnel refs + content fingerprints + journey refs | normalization |
| `AttributionFrame[metric]` | `AttributionFrameMeta` | none | none；authority 来自 source closure | `source_refs`，optional `scope_delta_ref` / `source_attribution_ref` | normalization |
| `AttributionFrame[funnel_loss_rate]` | `FunnelAttributionFrameMeta` | `catalog_definition_fingerprint` | none；authority 来自 source closure | delta ref + fingerprint + current/baseline journey refs | normalization |
| `ForecastFrame` | `ForecastFrameMeta` | none | none；authority 来自 source closure | `source_refs` | normalization |
| `QualityReport` | `QualityReportMeta` | none | lifecycle target 可额外保留 `target_state_model_ref` + fingerprint，但这是条件型信息，不改变 family 的 source-lineage extraction mode | `source_refs` | normalization |
| `CandidateSet[scored]` | `ScoredCandidateSetMeta` | none | none；authority 来自 source closure | `source_ref` + `source_refs` | normalization |
| `CandidateSet[semantic_hypothesis]` | `SemanticHypothesisCandidateSetMeta` | semantic + ontology catalog fingerprints | `readiness_bindings[metric_ref, fingerprint]` | `source_ref` | normalization |
| `AssociationResult` | `AssociationResultMeta` | none | none；authority 来自 source closures | `source_refs` | normalization |
| `ComponentFrame` | `ComponentFrameMeta` | none | typed metric/component/axis refs，无 scoped fingerprint | `parent_ref` | normalization |
| `CoverageFrame` | `CoverageFrameMeta` | none | none；authority 来自 source closure | `parent_ref` | normalization |
| `HypothesisTestResult` | `HypothesisTestResultMeta` | none | none；authority 来自 source closures | `source_refs` | normalization |

`catalog_definition_fingerprint` 只能作为 recorded catalog identity 和诊断信息。存在 scoped
identity 时，后续 revalidation 必须优先比较 dependency closure，不能因为 whole-catalog
fingerprint 改变就把无关 Artifact 判为 stale。

## 当前 catalog/definition drift producers

当前 producer 分散且没有统一 error vocabulary：

| producer | 当前检查 | 当前结果 |
| --- | --- | --- |
| `intents/_replay.py::ObserveReplay.call_observe` | 重新 lower Metric inputs，并比较 active 与 recorded `semantic_dependency_digest.digest` | `AttributionMaterializationError(recoverability_status="semantic_dependency_changed")` |
| `intents/observe_candidate.py::observe_candidate` | ontology catalog fingerprint、semantic catalog fingerprint、Metric readiness fingerprint 和 inherited scope | `CandidateNotObservableError` |
| `intents/_subject_cohort.py::resolve_subject_cohort` | SubjectSet whole-catalog fingerprint 和当前 subject identity signature | `SubjectSetMismatchError` |
| `intents/subjects.py::_require_ownership` | Event/Lifecycle source whole-catalog fingerprint | `SubjectSetMismatchError` |
| `intents/subjects.py::_require_lifecycle_source` | current StateModel definition fingerprint | `ModelStateMismatchError` |
| `intents/lifecycle_reducers.py::_require_history_source` | Lifecycle history whole-catalog fingerprint 和 current StateModel definition fingerprint | `SubjectSetMismatchError` |
| `intents/funnel_compare.py::_require_compatible` | 两个 funnel 的 recorded catalog fingerprint；这是 pairwise comparability，不是相对 current catalog 的校验 | `FunnelComparisonMismatchError` |
| `frames/candidate.py::CandidateSet.contract` | live semantic/ontology catalogs 与 persisted candidate metadata | 不抛异常；把既有 `CandidateResolutionIssue` 投影为 historical，并给出 rerun repair |
| `_semantic_persistence.py::job_semantics_from_frames` | 多输入 Artifact 的 recorded catalog fingerprints 是否一致 | bare `ValueError`；这是 commit-time envelope coherence，不是 currentness |

此外，`ComparabilityIssueKind` 已声明 `definition_drift_detected`，但当前没有 producer 创建该
issue。Slice 1/2/3 必须复用一个 authority comparator，不能把上表路径继续复制成新的
family-specific 检查。

Lifecycle quality 的 source-history check 还会比较 source content hash、StateModel ref 和
StateModel fingerprint；它验证 report/source 内部 integrity，不读取 current catalog，因此不属于
current semantic drift producer。

## Extractor coverage contract

`tests/test_analysis_authority_inventory.py` 固定并交叉验证：

1. 4 个 EvidenceSubject variant、6 个 EvidenceScope variant 和 6 个 EpistemicKind；
2. capability kernel 的 13 个 `ARTIFACT_FAMILIES` 与 session loader 的 13 个 frame kind 一致；
3. recursive `BaseFrameMeta` concrete subclasses 与 inventory 的 23 个 meta variant 完全一致；
4. inventory 中声明的每个 authority/source 字段真实存在于对应 Pydantic model；
5. Artifact ref 固定按 `artifact_id or ref` normalization；
6. 每个 variant 至少有 direct scoped identity 或 typed source identity，且
   `QualityReport` 以 source lineage 为主、只把 Lifecycle StateModel identity 作为条件型补充；
7. 所有当前 variant 的 schema action 都是 `normalization_only`；
8. inventory 类型、常量和 normalization helper 不进入 `marivo.analysis.__all__`。

新增 Evidence variant、Artifact family 或 concrete FrameMeta variant 未登记时，coverage test
必须失败。后续 Slice 2 应消费或替换这份 private inventory 来实现穷尽 dispatcher，而不是再
维护第二份 family 名单。
