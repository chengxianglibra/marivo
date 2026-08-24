# Marivo Evidence Compatibility and Revalidation — 设计

状态：设计提案，尚未实现

## 文档目的

本文只定义 Marivo 自身需要进行的重构和公共能力变更，范围限定在：

- `marivo.semantic` 的 current definition identity；
- `marivo.analysis` 的 Artifact、Finding、Evidence、Session 和 operator admission。

本文不涉及 Marivo 之外的系统。

## 核心问题

Marivo 当前能够精确恢复 content-addressed Artifact，并提供 typed Finding、bounded
ArtifactDigest、InferenceBoundary、ArtifactIssue 和 `.contract()`。但以下三个问题仍缺少
统一公共契约：

1. 多个 Finding 在 subject、scope、semantic definition、quality 和 epistemic kind 上
   是否可以机械组合；
2. 一个可精确恢复的 Artifact 相对于当前 semantic catalog 是否仍然有效，其持久化
   evidence 是否仍然完整一致；
3. 不同 Artifact family 中分散的 authority identity 如何被统一读取和校验。

必须区分两个正交事实：

```text
identity integrity
  这个 ref 是否仍恢复为原来的 content-addressed Artifact

current semantic admissibility
  这个原 Artifact 相对于当前 authority 是否仍然成立
```

`content_hash` 解决第一类问题，不能单独证明第二类问题。

## 设计目标

1. 为跨 Finding/Artifact 组合提供唯一、typed、fail-closed 的兼容性校验入口；
2. 为 Artifact 提供独立的 identity/semantic/evidence 重新校验结果；
3. 复用当前 `catalog.definition_fingerprint`、Artifact metadata、lineage 和 evidence
   store，不新建平行事实系统；
4. 将分散在各 operator 中的 catalog/definition admission 收敛到一个共享实现；
5. 保持 Artifact immutable 和 content-addressed，不用重新校验结果改写历史 Artifact；
6. 未知 scope、缺失 authority identity 或不可用 evidence store 均明确表达，不静默视为
   compatible/admissible；
7. 新公共结果遵守 bounded `repr`、`show()`/`render()`、closed types、focused help 和
   structured repair 契约。

## 非目标

本设计不包含：

- 自然语言 Claim 校验；
- proposition、confidence、assessment 或 recommendation；
- session-level factual summary 或 planner；
- 全局 capability manifest；
- source currency、freshness revalidation、freshness cache 或自动后台 refresh；
- 跨 Marivo Session 的 evidence merge；
- revision、lease、writer election 或多进程调度协议；
- report、dashboard 或 publication；
- 对旧 schema 的双读、alias 或隐式迁移。

## Marivo 内部分层

```text
marivo.semantic
  definition fingerprint / semantic refs
  │
  ▼
marivo.analysis
  typed computation / Artifact / Finding / Evidence / Compatibility / Revalidation
```

层级规则：

- semantic 拥有 reusable business definition 和 catalog fingerprint；
- analysis 消费 semantic identity，不重新实现 definition fingerprint；
- evidence compatibility 只处理 typed identity、scope、quality、derivation 和 epistemic
  boundary，不推导业务含义；
- analysis 不得反向修改 semantic readiness。

Source currency 继续由独立的 `catalog.source_health(...)` 契约负责，不进入 V1
`ArtifactRevalidation`，也不影响其状态。

## 当前基础

### 已实现

- `ArtifactContract` / `ArtifactAffordance` 描述当前 Artifact 的机械 continuation；
- `Finding` 携带 `finding_id`、`artifact_id`、`session_id`、subject、derivation、
  observed window、quality status 和 epistemic kind；
- `ArtifactDigest` 携带 operator、subject、scope、quality、boundary、omission、fallback 和
  fingerprint；
- `session.evidence` 支持 bounded findings/digests 和精确 finding/digest/trace 读取；
- `session.get_frame(ref)` 支持 cold-start rehydration，并对 cross-session ownership、
  missing ref 和 cache corruption fail closed；
- `ArtifactState` 记录 materialization 和 content hash；
- semantic catalog 已经拥有 `definition_fingerprint`；
- evidence ledger 中的 Artifact record、Finding、Digest 和 Issue 事务提交；持久化 Frame
  文件使用原子替换。

### 现有缺口

- `EvidenceScope` 没有公共兼容性操作；
- `ComparabilityIssueKind` 已包含 definition drift，但缺少一个覆盖所有 Artifact family 的
  统一 producer；
- 不同 FrameMeta 对 catalog fingerprint 和 semantic refs 的布局不一致；
- 一些 operator 自己检查 catalog fingerprint，规则分散且容易出现 family drift；
- `ArtifactState` 刻意不表达 stale 或 superseded；
- exact recovery 能证明 Artifact identity，但没有一次性返回其 semantic authority 和
  evidence integrity 状态。

## R1：Evidence Compatibility

### 公共入口

在现有 `Session.evidence` namespace 上增加唯一入口：

```python
compatibility = session.evidence.compatibility(
    finding_ids=[finding_a.finding_id, finding_b.finding_id],
)
```

公共签名：

```text
EvidenceNamespace.compatibility(
  finding_ids: Sequence[str],
) -> EvidenceCompatibility
```

V1 复用当前 `Finding.finding_id` 和 `session.evidence.finding(id)` 已建立的 identity
vocabulary，不引入 `FindingRef` alias。实现按 id 从当前 Session evidence store 一次性读取
canonical Finding；调用方不需要先恢复完整 Finding，也不重复传入 artifact/session/scope。

输入要求固定为：

- 非空、唯一的 finding ids；
- 最少 1 个、最多 20 个；
- input order 不表达语义；结果和 fingerprint 按 finding id 规范排序；
- 空 selection、重复 id 或超过 20 个均抛 `EvidenceSelectionError`；
- 任一未知 id 抛现有 `FindingNotFoundError`，不返回部分结果。

兼容性检查只回答 canonical Finding 集合内部是否存在 subject、scope、semantic
definition、quality 或 evidence contradiction。V1 没有 caller-authored requirement，也不把
`EpistemicKind` 当成 intended-use vocabulary。

不同 epistemic kind 可以出现在同一 selection 中。结果保留完整 kind 集合和 inference
boundaries，但不判断这些 Finding 能否支持某种外部表述。

### Selection 语义

`compatibility(...)` 是 selection-wide、all-or-nothing 的集合检查，不存在隐含的 anchor、
expected Finding 或 requirement：

- subject、scope 和 semantic authority 对 selection 中每一对 Finding 做对称比较；
- quality 和 evidence 对每个 Finding 做一元检查；
- selection 的 `status` 只有一个，任一 Finding 或 Finding pair 的 blocking contradiction
  都使整个 selection `incompatible`；
- 结果不返回“兼容子集”，每条 issue 通过 `finding_ids` 精确指出失败的一元 Finding 或二元
  Finding pair；调用方需要兼容子集时，应基于这些归因重新提交更小的 selection；
- `evaluated_pair_count` 固定为 `n * (n - 1) / 2`；单 Finding selection 的 pair count 为 0，
  但仍执行其一元 quality、evidence 和 authority 检查。

### EvidenceCompatibility

返回值是 immutable、bounded 的终端结果：

```text
EvidenceCompatibility
  compatibility_version
  status: compatible | incompatible | indeterminate
  finding_ids
  artifact_refs
  session_id
  subject_status: compatible | incompatible | indeterminate
  scope_status: compatible | incompatible | indeterminate
  semantic_status: compatible | incompatible | indeterminate
  evidence_status: complete | partial | unavailable
  quality_status: ready | needs_attention | not_ready | not_assessed
  epistemic_kinds
  issues: tuple[EvidenceCompatibilityIssue, ...]
  boundaries
  evaluated_pair_count
  omitted_issue_count
  omitted_issue_kinds
  fingerprint
```

全部 identity 从 canonical store records 推导。`status` 的规则固定为：

1. 任一 blocking subject/scope/definition/quality contradiction → `incompatible`；
2. 没有 contradiction，但存在未知 variant、未知 rule 或 authority 无法证明 →
   `indeterminate`；
3. 所有必要维度可以机械对齐 → `compatible`。

`compatible` 不表示 completeness、重要性、因果性或某种表述得到支持。混合
`observed/algebraic/tested` 可以 compatible；这些 kind 之间不会发生升级或折叠。

聚合状态复用现有 vocabulary：

- evidence：任一 `unavailable` → `unavailable`，否则任一 `partial` → `partial`，否则
  `complete`；
- quality：任一 `not_ready` → `not_ready`，否则任一 `needs_attention` →
  `needs_attention`，存在未评估 Finding → `not_assessed`，否则 `ready`；
- `not_assessed` 只披露质量未评估，不单独制造 incompatibility；blocking typed issue 或
  `not_ready` 才使整体 incompatible。

### Bounds、ordering 与 fallback

结构化结果和 render 使用不同但都明确的边界：

- `finding_ids` 最多 20 个，按 id 排序；
- `artifact_refs` 去重后按 ref 排序，最多 20 个；
- validator 检查完整 selection，不因展示边界跳过任何 pair；
- 每条 issue 只对应一个一元 Finding 或一个二元 Finding pair，不跨 pair 聚合而丢失归因；
- 全量 issues 按 detail kind、`finding_ids`、detail issue id 稳定排序，结构化结果保留前 20 个；
- `boundaries` 按 closed kind 去重，最多保留当前 vocabulary 的全部 distinct kinds；
- 如果全量 issues 超过结构化边界，`omitted_issue_count` 和 `omitted_issue_kinds` 精确记录省略；
  status 已基于全量检查计算，不能因为 omission 改变；
- `render()` 最多展示 5 个 finding ids、5 个 issues 和 3 个 boundaries，并明确 omission；
- exact fallback 是返回值中的 finding ids 和 artifact refs，可分别交给
  `session.evidence.finding(...)` 与 `session.get_frame(...)`。

`fingerprint` 基于完整 normalized selection、完整检查状态、全量 issues、boundaries 和
omission fields 计算，不包含 render 文本或调用顺序。

### Compatibility issue model

每条结果 issue 使用一个只负责 selection 归因的 immutable wrapper：

```text
EvidenceCompatibilityIssue
  finding_ids: tuple[str, ...]  # exactly one unary id or two pairwise ids
  artifact_refs: tuple[str, ...]
  detail: DataQualityIssue | ComparabilityIssue | EvidenceAvailabilityIssue | EvidenceRuleIssue
```

`finding_ids` 规范排序且不能重复；`artifact_refs` 必须从这些 canonical Finding 推导，调用方
不能提供。历史 Artifact 上已有的 issue 作为 `detail` 复用，不复制或改写其事实内容。wrapper
让同一 Artifact 内的多个 Finding 仍能被精确区分，也让 pairwise contradiction 能定位到准确
的两个输入。

`detail` 优先复用现有：

- `DataQualityIssue`；
- `ComparabilityIssue`；
- `EvidenceAvailabilityIssue`。

`InferenceBoundary` 保留在结果的 `boundaries` 字段，不作为 issue detail。仅为现有 issue
无法表达的健康负结果增加一个 closed variant：

```text
EvidenceRuleIssue
  issue_id
  kind:
    semantic_authority_unknown
    unknown_subject_rule
    unknown_scope_rule
    unknown_operator_evidence_rule
  severity: warning | blocking
  expected: str
  received: str
  repair: AnalysisRepair
```

`EvidenceRuleIssue` 不重复保存 Finding 或 Artifact identity；这些 identity 由外层
`EvidenceCompatibilityIssue` 唯一拥有。每个 detail 必须有稳定 `issue_id`，wrapper 的稳定
identity 由 detail issue id 与 normalized `finding_ids` 共同推导。

Missing Finding、unavailable store、source Artifact 丢失、sidecar/digest mismatch 或 broken
derivation 都不是 compatibility issue，而是 identity/store corruption，必须抛 typed error。
Compatibility issue 只表达 stores 健康、canonical records 完整时得到的机械负结果。

这些 issue 不回写历史 Artifact 的 `contract().issues`。ArtifactIssue 继续表示
commit-time、artifact-local 问题；CompatibilityIssue 表示一次当前集合检查的问题。

### 校验顺序

实现使用固定顺序，前一步失败不能被后一步掩盖：

1. **Store health**：evidence store 不可用时抛 typed error，不返回空 compatible result；
2. **Canonical load**：按 finding id 一次性读取 canonical Finding；
3. **Session ownership**：当前 session-local store 是唯一读取范围；来自其他 Session 的 id
   与未知 id 一样抛 `FindingNotFoundError`；
4. **Referential integrity**：source Artifact、derivation source ref 和 trace 必须可解析；
5. **Subject compatibility**：由一个集中、对称的 comparator 比较 closed EvidenceSubject
   variants；
6. **Scope compatibility**：对称比较 population、window、grain、内嵌 direction 及各 scope variant 的
   专属字段；
7. **Semantic identity**：比较 source Artifact 记录的 definition fingerprint，并相对当前
   Session catalog 的 scoped authority检查 drift；
8. **Quality/evidence**：读取 source Artifact 的 evidence status、typed issues 和
   Finding quality status；
9. **Epistemic projection**：保留所有 kind，不执行 intended-use admission；
10. **Inference boundary**：从 operator evidence rule 汇总完整 boundary，不能只依赖
    bounded digest 中保留的前三项；
11. **Result fingerprint**：对 normalized result 计算稳定 fingerprint。

任何未知 subject/scope variant、未知 operator evidence rule 或缺失 authority field 都返回
`indeterminate`，不能默认 compatible。

### 集中 compatibility matrix

兼容规则不得分散为每个 scope variant 的 `compatible_with()`。新增一个 analysis-owned
dispatcher，对完整 closed union 做穷尽匹配：

```text
compare_subject(left, right) -> CompatibilityDetail
compare_scope(left, right) -> CompatibilityDetail
compare_semantic_authority(left, right, current) -> CompatibilityDetail
```

这样可以通过类型检查和 snapshot test 强制每个新 variant 同时更新 compatibility matrix。
variant 未登记时 fail closed。

## R2：Artifact Revalidation

### 公共入口

在 `Session` 上增加唯一入口：

```python
result = session.revalidate(frame)
```

公共签名：

```text
Session.revalidate(
  frame: BaseFrame,
) -> ArtifactRevalidation
```

`frame` 必须属于当前 Session。调用者先通过 `session.get_frame(ref)` 完成 exact recovery；
missing、cross-session 和 content corruption 继续由现有 typed error 处理。对当前进程刚
返回的 committed Frame，也通过相同 canonical persistence path 校验 identity。

V1 只检查：

1. Artifact identity integrity；
2. 相对于当前 Session catalog 的 semantic authority；
3. Artifact sidecar 与 evidence store 的 evidence integrity。

它不接受 `SourceHealthReport`，不访问 datasource，也不判断 source currency。

### ArtifactRevalidation

返回值不使用含混的 `valid: bool`：

```text
ArtifactRevalidation
  revalidation_version
  artifact_ref
  session_id
  content_hash
  artifact_schema_version
  recorded_catalog_fingerprint
  current_catalog_fingerprint
  semantic_status: current | stale | indeterminate
  evidence_status: complete | partial | unavailable
  status: admissible | stale | indeterminate
  issues
  checked_at
  authority_fingerprint
  fingerprint
```

返回成功即表示 artifact ref、session ownership、sidecar 和 content hash 已通过 identity
integrity；identity missing/corruption 不降级成状态字段，而是使用现有 typed error fail
closed。

状态推导规则固定为：

1. semantic dependency closure 内存在 blocking definition drift → `stale`；
2. 没有 drift，但 semantic authority 无法证明，或健康 records 明确记录的 evidence 状态
   无法满足当前 Artifact family contract → `indeterminate`；
3. identity 已验证、semantic authority current，且 evidence 满足当前 Artifact family contract
   → `admissible`。

`partial` evidence 是否使 overall status `indeterminate`，由 Artifact family 的现有 evidence
contract 决定，不能全局假设 partial 一定不可用。

`admissible` 明确不表示 datasource 最新、freshness current 或 Artifact 适合回答“截至现在”
的问题。

### 状态词汇边界

- `indeterminate` 是 compatibility/revalidation aggregate status，表示当前受检问题无法机械
  判定；公共 aggregate status 不使用 `unknown` 或 `not_checked` 同义词；
- `semantic_authority_unknown` 与 `ArtifactAuthorityUnknownError` 是 issue/error kind，描述
  无法判定的原因，不是另一个 aggregate status；
- `complete | partial | unavailable` 只描述 evidence coverage/readability 维度，不与
  `indeterminate` 同义；它如何影响整体状态由 Artifact family contract 决定；
- V1 没有 `source_status` 字段，source currency 继续由独立的 `SourceHealthReport` 表达。

### Semantic revalidation

Semantic revalidation 必须：

- 从 FrameMeta/lineage 提取 Artifact 创建时的 catalog/definition fingerprint；
- 与 `session.catalog.definition_fingerprint` 比较；
- 对 scoped definition 使用现有 canonical fingerprint，不复制 fingerprint 算法；
- 对 derived Artifact 沿 lineage 定位 source Artifact authority；
- 对多 source Artifact 验证每个必要 semantic input；
- 缺少可证明 identity 时返回 `indeterminate`；
- definition drift 使用现有 `ComparabilityIssue(kind="definition_drift_detected")` 表达。

仅“全 catalog fingerprint 不同”不必自动判定所有 Artifact stale。如果 Marivo 已有精确
scoped definition fingerprint，应优先判断 Artifact 实际 dependency closure；只有无法进行
scoped 判断时才返回 `indeterminate`，不能把无关 catalog 变更扩大为 stale。

多 source Artifact 使用以下固定规则：

1. 按 `(semantic_ref, source_artifact_ref)` 保留每个必要 semantic dependency，不能把多个
   recorded fingerprint 压成一个无归属的集合值；
2. 每个 recorded scoped fingerprint 独立与当前 catalog 中同一 `semantic_ref` 的 canonical
   scoped fingerprint 比较；
3. 任一 dependency 已确认 drift，整体 `semantic_status=stale`，即使其他 dependency 仍
   current；confirmed stale 的优先级高于其他 dependency 的 indeterminate；
4. 没有 confirmed drift，但任一必要 dependency 缺少 recorded/current scoped authority，
   整体为 `indeterminate`；
5. 不同 semantic refs 的不同 fingerprint 不构成冲突；同一 semantic ref 出现多个 recorded
   fingerprint 时逐项执行规则 2，因此只要 current authority 可读，至少一个旧版本会被判
   stale；
6. 只有 catalog-level fingerprint 时，不用多个输入彼此不同直接推导 stale；若无法补出
   scoped authority，则按规则 4 返回 `indeterminate`。

### Evidence revalidation

Evidence revalidation 必须检查：

- Artifact sidecar digest 与 evidence store digest fingerprint 一致；
- Finding 的 artifact/session ownership 一致；
- evidence store schema 是当前唯一支持版本；
- Artifact 的 typed issues 和 evidence status 可读取；
- unavailable store 抛 typed error，不能转成空 findings；
- bounded digest omission 不影响 exact Finding 的存在性判断。

Artifact 自身记录的 `evidence_status=unavailable` 是一个健康、可读取的 terminal state，进入
`ArtifactRevalidation` 结果；evidence store 本身不可访问则抛
`EvidenceStoreUnavailableError`。两者不能合并。

### Source currency boundary

V1 source currency 保持独立：

- freshness 仍然只能通过显式 `ms.source_check.freshness(...)` 和
  `catalog.source_health(...)` 观察；
- `Session.revalidate(...)` 不接收或持久化 `SourceHealthReport`；
- `ArtifactRevalidation.status` 不受 source-health 结果影响；
- 不从 Artifact age、created time、operator params 或最近一次 source check 猜测 freshness；
- 如果未来需要 Artifact-level source currency，必须先建立 semantic-owned、与 exact refs 和
  scope 绑定的 freshness requirement，再单独设计公共契约。

### Immutable lifecycle

Revalidation 是 ephemeral terminal result：

- 不修改 `ArtifactState`；
- 不改写 FrameMeta、Digest 或 Finding；
- 不改变 content hash；
- 不把当前检查结果缓存成永久 current；
- `checked_at` 和 authority fingerprint 明确限定检查时点。

V1 不实现 `superseded`。在 Marivo 出现能够机械证明 predecessor/successor 关系的 producer
之前，不得用 creation time、相似 params 或相同 operator 猜测 supersession。未来引入时应
使用显式关系和独立 schema，而不是在 `stale` 上增加字符串备注。

## R3：Authority Identity Normalization

### 问题

当前 catalog fingerprint 已存在于多种 family-specific metadata 中，但字段布局和沿 lineage
的读取方式不统一。继续让每个 operator 自己读取这些字段，会造成：

- 新 Artifact family 忘记加入 current-authority 检查；
- derived Artifact 只检查直接输入而漏掉根 source；
- 相同 drift 在不同 operator 中得到不同 error；
- revalidation 被迫了解每个 FrameMeta 私有布局。

### 内部标准值

新增 analysis-internal normalized value：

```text
ArtifactAuthorityContext
  artifact_ref
  session_id
  content_hash
  semantic_dependencies: tuple[SemanticDependencyAuthority, ...]
  source_refs
  evidence_digest_fingerprint

SemanticDependencyAuthority =
  ScopedDependencyAuthority
    semantic_ref
    source_artifact_ref
    recorded_catalog_definition_fingerprint
    recorded_scoped_definition_fingerprint
  | CatalogOnlyDependencyAuthority
    semantic_ref
    source_artifact_ref
    recorded_catalog_definition_fingerprint
    scoped_authority_missing_reason
  | UnresolvedDependencyAuthority
    semantic_ref_if_known
    source_artifact_ref
    missing_authority_fields
```

closed dependency variants 保留 semantic ref、source Artifact 与 recorded authority 的关联，
不使用会发生位置错配的平行 fingerprint tuples。`CatalogOnly` 和 `Unresolved` 是可检查的
fail-closed 输入，不会被 normalization 填入 guessed/default fingerprint。

它由一个穷尽的 family dispatcher 从 typed FrameMeta 和 lineage 生成：

```text
authority_context(frame) -> ArtifactAuthorityContext
```

这是内部 normalization，不是新的 persisted snapshot，也不进入顶层 `__all__`。公共
`ArtifactRevalidation` 只投影必要、bounded 的 identity。

### Schema 策略

第一步先对当前所有 Artifact family 建立 extractor coverage test，证明现有 metadata 是否
足以生成 AuthorityContext。

- 如果全部必要 identity 已存在，只增加 normalization，不修改 Artifact schema；
- 如果某个 family 缺少不可推导字段，进行一次 clean schema cutover；
- 不为旧 sidecar 添加 dual-read、default fingerprint 或兼容 alias；
- schema cutover 必须同步 FrameMeta、content hash、loader、evidence record、recovery、help、
  docs 和 fixtures；
- 旧 Session 按现有 schema mismatch 规则要求新建分析 Session，不做静默迁移。

## R4：Operator Admission 收敛

### 当前问题

catalog/definition currentness 检查已出现在部分 intent 和 Frame family 中，但不是统一的
operator admission contract。新增 Artifact family 或 consumer 时容易遗漏。

### 目标结构

扩展现有 capability input validation：

```text
validate_capability_inputs(...)
  ├── artifact family / shape / session ownership
  ├── operator-specific preconditions
  └── validate_artifact_authority(...)
        ├── normalized AuthorityContext
        ├── current catalog/scoped definition
        └── typed drift/unknown repair
```

规则：

- 只有需要 current semantic authority 的 capability 才执行 semantic admission；
- 对完全消费已物化数值、且不重新解释 semantic meaning 的封闭操作，不因无关 catalog
  变化而阻塞；
- admission failure 使用统一 structured error，包含 expected、received、affected refs 和
  当前真实 repair；
- `.contract()` 继续描述 commit-time shape compatibility，不伪装成 current revalidation；
- 未登记新 Artifact family 或 capability authority policy 时 fail closed。

`ArtifactRevalidation` 是显式检查结果，operator admission 是执行前保护。两者复用同一
AuthorityContext 和 comparator，但不能各自实现一套规则。

## R5：Persistence 与并发边界

本设计不引入 revision 或 lease，但必须保持并验证以下 Marivo 保证：

- Session Store 和 evidence store 的写事务在 lock/timeout 时抛 typed error；
- Frame 文件继续使用 temp file + atomic replace；
- evidence ledger 中 Artifact record、Finding、Digest 和 Issue 保持单事务；
- 中断后不能出现被标记 complete、但 canonical Finding/Digest 不可读取的 Artifact；
- revalidation 和 compatibility 只读取 committed state；
- 并发写失败不能退化为 last-write-wins 或 silent retry；
- WAL/busy timeout 是存储实现细节，不等于多 writer 协议。

如果测试证明 Frame persistence 与 evidence commit 之间存在可观察的半提交窗口，应先补
recovery marker/commit protocol，而不是引入高层并发调度抽象。

## 公共错误模型

新增错误继续继承 `AnalysisError`，并遵守 expected / received / repair：

```text
EvidenceSelectionError
  empty selection
  duplicate finding ids
  more than 20 finding ids

EvidenceIntegrityError
  committed Finding references a missing/corrupt Artifact, Digest, or derivation source

ArtifactAuthorityUnknownError
  required authority identity cannot be derived

ArtifactStaleError
  operator requires current authority but revalidation found drift
```

结果与异常矩阵固定为：

| 场景 | 唯一结果 |
| --- | --- |
| 健康 stores 中的 compatible/incompatible/indeterminate | `EvidenceCompatibility` |
| 空、重复或超过 20 个 finding ids | `EvidenceSelectionError` |
| 未知 finding id | 现有 `FindingNotFoundError` |
| evidence store 不可访问 | 现有 `EvidenceStoreUnavailableError` |
| committed Finding 指向 missing/corrupt Artifact、Digest 或 derivation，或 sidecar/store fingerprint 不一致 | `EvidenceIntegrityError` |
| evidence store schema 不匹配 | 现有 `SchemaVersionMismatchError` |
| Artifact 自身健康地记录 `evidence_status=unavailable` | `ArtifactRevalidation` negative state |
| revalidate 输入 Frame missing/cross-session/corrupt | 现有 Frame typed error |
| semantic authority 无法证明 | `ArtifactRevalidation(status="indeterminate")` |
| operator admission 要求 current authority，但状态 stale/authority 无法建立 | `ArtifactStaleError` / `ArtifactAuthorityUnknownError` |

普通 `compatibility(...)` 和 `revalidate(...)` 返回 typed negative result；只有无效 selection、
store 不可用、corruption 或 execution admission 才抛异常。不能同时用 `None`、空 result 和
异常表达同一种失败。

## Public Surface 要求

V1 公共路径固定为：

```text
session.evidence.compatibility(finding_ids=[...]) -> mv.EvidenceCompatibility
session.revalidate(frame) -> mv.ArtifactRevalidation

marivo.help("analysis.session.evidence.compatibility")
marivo.help("analysis.session.revalidate")
```

这两个 public entry name 是 V1 已审定名称，不是待后续 public-surface review 决定的占位名。

`EvidenceCompatibility`、`EvidenceCompatibilityIssue`、`EvidenceRuleIssue`、`ArtifactRevalidation`、
`EvidenceSelectionError` 和 `EvidenceIntegrityError` 加入 `marivo.analysis.__all__`；结果
类型不提供平行 constructor。内部 comparator、AuthorityContext 和 persistence rows 不公开。

任何落地实现必须同步：

- concrete Python annotations；
- `marivo.analysis.__all__` snapshot；
- `marivo.help("analysis.<target>")` capability registry；
- bounded `repr`、`render()`、`show()`；
- structured errors 和 real-state repairs；
- `docs/specs/analysis/evidence-access-surface.md`；
- `docs/specs/analysis/session-state-and-runtime.md`；
- `docs/specs/analysis/python-analysis-design.md`；
- 英文和中文 latest site 文档；
- packaged analysis skill 中仅与 hard boundary 有关的内容；
- persistence、recovery、public surface 和 drift tests。

不得增加 `validate` alias、`FindingRef` alias、caller-authored requirement constructor 或
SourceHealthReport overload。

### 调用成本预算

公共接口必须满足以下固定成本：

- 已知 finding ids 时，一次 `compatibility(...)` 完成 canonical batch load 和全量检查；
- 不要求逐项调用 `session.evidence.finding(...)`，也不要求构造 EvidenceSubject、
  EvidenceScope 或 intended-use value；
- 已有 Frame 时，一次 `revalidate(frame)` 完成 identity、semantic 和 evidence 检查；
- 只有 ref 时，固定为一次 `session.get_frame(ref)` 加一次 `session.revalidate(frame)`；
- revalidation 不触发 datasource/source-health 查询；
- 两个入口各自只有一个 focused help target；
- 默认 `repr` 为单行，`show()`/`render()` 遵守 5/5/3 展示边界，不把完整 Finding、scope
  payload 或 Artifact rows 写入输出。

任何实现如果要求调用方恢复完整 Finding、重新填写 scope、按 epistemic kind 拆分多次
调用，或为了普通 revalidation 执行 source query，都违反 V1 公共契约。

## 实施切片

### Slice 0：Contract Inventory

- 穷举 EvidenceSubject、EvidenceScope、EpistemicKind 和 Artifact family；
- 列出每个 FrameMeta 中可用的 catalog/scoped definition/source identity；
- 列出当前所有 catalog drift producer 和 error；
- 建立 AuthorityContext extractor coverage test；
- 确认哪些缺口需要 schema cutover，哪些只需 normalization。

交付结果是准确 inventory 和测试，不新增公共 API。

### Slice 1：Evidence Compatibility

- 实现 subject/scope/semantic-authority comparator 和 epistemic projection；
- 实现 canonical Finding batch load 和 integrity checks；
- 增加 `EvidenceCompatibility`、逐 Finding/pair 归因的 `EvidenceCompatibilityIssue`、
  `EvidenceSelectionError`、`EvidenceIntegrityError` 和最小 rule issue variant；
- 增加 `session.evidence.compatibility(finding_ids=[...])`，落实 1..20 输入及 result/render
  bounds；
- 同步 help、exports、spec 和 focused tests。

### Slice 2：Artifact Revalidation

- 实现 AuthorityContext；
- 实现 identity、semantic 和 evidence revalidation；
- 增加 `ArtifactRevalidation` 与 `session.revalidate(...)`；
- 明确不接收 SourceHealthReport、不访问 datasource；
- 保持结果 ephemeral，不改写 Artifact；
- 根据 Slice 0 结论执行唯一一次必要 schema cutover。

### Slice 3：Operator Admission

- 将散落的 definition/catalog admission 迁移到共享 validator；
- 为每个 capability 声明是否要求 semantic-current；
- 删除旧的平行检查和错误路径；
- 对所有 Artifact family 做 continuation/admission matrix tests。

### Slice 4：Persistence Adversarial Tests

- 覆盖 evidence store unavailable、SQLite lock/timeout、文件中断写、sidecar corruption；
- 验证不存在 complete Artifact + missing canonical evidence 的静默状态；
- 只有测试证明现有 commit seam 不足时，设计 recovery marker/commit protocol。

## 验收标准

### Compatibility

- 同 Session、同 subject/scope/definition 的 Finding 得到 `compatible`；
- selection 是无 requirement anchor 的 all-or-nothing 检查，每条 issue 精确归因到一个
  Finding 或一个 Finding pair；
- 其他 Session 的 finding id 按 session-local store 边界得到 `FindingNotFoundError`；subject
  mismatch、window/grain/direction mismatch 得到 typed issue；
- `observed`、`algebraic`、`tested` 等不同 kind 可以在 scope/lineage 对齐时共同得到
  `compatible`，结果保留每一种 kind 和 boundary，不做 intended-use admission；
- blocking quality、partial/unavailable evidence 和 digest omission 分别处理；
- 新 EvidenceScope variant 未加入 matrix 时测试失败并在运行时 fail closed；
- 空、重复、未知和超过 20 个 finding ids 分别走规定的唯一异常；
- result 最多保留 20 个精确归因 issues，render 最多展示 5/5/3 个 ids/issues/boundaries，且
  omission count 准确；
- Compatibility fingerprint 与 input order 无关，在相同 normalized selection 下稳定；
- focused help 的唯一入口是 `analysis.session.evidence.compatibility`。

### Revalidation

- exact recovery 与 current semantic admissibility 分开测试；
- 无关 catalog 变化不把 scoped Artifact 错判 stale；
- dependency closure 内 definition drift 得到 `semantic_status=stale`；
- multi-source dependency 任一 confirmed drift 使整体 stale；只有 catalog-level identity 或
  任一必要 scoped authority 无法建立、且不存在 confirmed drift 时为 indeterminate；
- revalidation 不接受 SourceHealthReport、不访问 datasource，也不返回 source status；
- `status=admissible` 不被描述为 freshness/source-current；
- Artifact `evidence_status=unavailable` 与 evidence store unavailable 使用不同结果路径；
- evidence sidecar/store fingerprint drift fail closed；
- Revalidation 不修改 Artifact、content hash、Digest 或 Finding；
- focused help 的唯一入口是 `analysis.session.revalidate`。

### Operator admission

- 所有需要 current semantic authority 的 consumer 使用共享 validator；
- 封闭的 materialized-only 操作不被无关 catalog drift 阻塞；
- 新 capability 未声明 authority policy 时无法通过 registry/contract test；
- structured error 使用当前真实 fingerprint、refs 和 repair。

### Persistence

- store unavailable 与 empty result 可机械区分；
- interrupted write 不产生半 Artifact；
- lock/timeout 不静默重试或覆盖；
- schema 不匹配要求新 Session，无 legacy dual-read。

## 架构不变量

1. `datasource → semantic → analysis` 依赖方向不反转。
2. semantic definition fingerprint 只有一个实现权威。
3. Source currency 不进入 V1 revalidation；freshness 仍只由显式 source-health check 观察。
4. Evidence compatibility 不产生 proposition、confidence 或业务判断。
5. Artifact identity 与 current semantic admissibility 是两个不同契约。
6. Revalidation result 不修改 immutable Artifact。
7. `.contract()` 描述机械 continuation，不承担 currentness。
8. Compatibility、revalidation 和 operator admission 复用同一 comparator/AuthorityContext。
9. cross-session、unknown variant、unknown authority 和 unavailable store fail closed。
10. 公共结果 typed、bounded、可恢复；内部 registry 和 persistence row 不公开。

## 源码锚点

- Finding、Digest、InferenceBoundary、ArtifactIssue 和 evidence store：
  [evidence-access-surface.md](../../specs/analysis/evidence-access-surface.md)
- Session、content-addressed identity、cold-start recovery 和 cross-session ownership：
  [session-state-and-runtime.md](../../specs/analysis/session-state-and-runtime.md)
- ArtifactContract、ArtifactState 和 BaseFrameMeta：
  [frames/base.py](../../../marivo/analysis/frames/base.py)
- EvidenceNamespace 与 exact reads：
  [session/core.py](../../../marivo/analysis/session/core.py)
- Semantic source-health contract：
  [source_health.py](../../../marivo/semantic/source_health.py)
- Catalog-owned source-health public entry：
  [catalog.py](../../../marivo/semantic/catalog.py)
