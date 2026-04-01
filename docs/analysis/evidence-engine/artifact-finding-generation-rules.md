# Artifact -> Finding 生成规则提案

本文档定义 `artifact -> finding` 的目标态生成协议与分意图抽取规则提案。

状态：draft proposal。本文是对 [`runtime-pipeline.md`](runtime-pipeline.md) 与 [`schemas/finding.md`](schemas/finding.md) 的补充设计稿，目的是先收敛生成规则与审批点，不直接改写现有 canonical 主文档。

当前审批结果：

- D1 approved：extractor 采用 `(artifact_type, artifact_schema_version)` 路由
- D2 approved：`canonical_item_key` 采用稳定 key 优先，index 仅作 contract-backed fallback
- D3 approved：`attribute` 不新增独立 canonical finding family
- D4 approved：空 finding set 改为 family-specific contract；`observe` / `detect` 允许 success-empty，`compare` / `decompose` / `correlate` / `test` / `forecast` success 必须 non-empty
- D5 approved：`correlate` / `test` 在 v1 固定 `1 artifact -> 1 finding`

## 目的

固定以下问题的统一答案：

- finding 在运行时何时生成
- extractor 的 authority boundary 是什么
- 什么是 canonical item boundary
- `finding_id` 与 `artifact_item_ref` 应如何稳定生成
- 不同 typed intent family 应如何从 artifact 映射到 finding
- 哪些点需要先由人批准，再进入 canonical 规范

## 设计目标

- 保持 `artifact -> finding` 完全确定性
- 让 finding 成为 proposition seeding 的唯一权威输入
- 避免把 UI summary、projection top-k 或自由文本误写成 canonical fact
- 让 replay、idempotency、soft invalidation 都能围绕稳定 item boundary 工作
- 尽量复用现有 extractor seam，但不继承当前实现中不稳定或过度汇总的部分

## 非目标

本文不定义：

- proposition template 选择规则
- assessment judgment policy
- 对外 HTTP wire contract
- 当前实现的完整迁移计划

## 统一生成协议

### 1. 触发时机

对 `mandatory extraction artifact family`：

- `observe`
- `compare`
- `decompose`
- `detect`
- `correlate`
- `test`
- `forecast`

artifact 写入流程应固定为：

1. 写入 staged artifact
2. 根据 artifact contract 选择 extractor
3. 生成 committed finding set
4. 仅当 artifact 与 findings 同时成功落入 canonical store 时，才提交 committed state

因此，目标态不允许：

- artifact 已 committed，但 findings 仍 pending
- artifact 已 committed，但 extraction failed
- 在 family contract 未允许时提交 empty finding set

目标态 family-level 规则固定为：

- `observe`：允许 committed success-empty
- `detect`：允许 committed success-empty
- `compare`：success 必须至少产出 1 个 finding
- `decompose`：success 必须至少产出 1 个 finding
- `correlate`：success 必须至少产出 1 个 finding
- `test`：success 必须至少产出 1 个 finding
- `forecast`：success 必须至少产出 1 个 finding

### 2. Extractor authority boundary

extractor 只允许读取以下输入：

- artifact header
- artifact payload
- artifact contract metadata
- extractor version

extractor 不允许读取：

- session 其他 artifact / finding / proposition 的当前状态
- UI projection、top-k、排序截断结果
- summary / explanation / narrative text
- 模型输出

### 3. Extractor registry

提案：extractor 的选择键固定为：

- `artifact_type`
- `artifact_schema_version`

可选补充键：

- `finding_schema_version`

不建议直接仅按 `step_type` 路由，因为同一 step family 后续可能演化出多个 artifact contract。

### 4. 输出 contract

每次 extraction 必须返回：

- `findings`
- `extractor_name`
- `extractor_version`
- `artifact_schema_version`
- `finding_count`

并满足：

- `finding_count` 的最小值由对应 artifact family contract 决定
- 同一 `artifact_id + canonical_item_key + finding_type` 至多生成一个 finding

## Canonical Item Boundary

### 总规则

finding 的 canonical item boundary 必须直接绑定 artifact contract 中最小可引用结果项，而不是绑定消费视图。

固定规则：

- scalar item -> 1 finding
- row item -> 1 finding
- bucket item -> 1 finding
- candidate item -> 1 finding
- test result item -> 1 finding

以下对象不得直接成为 canonical finding：

- artifact summary
- UI headline
- recommendation text
- top-k projection item
- “worst offender” 这类二次摘要对象
- 无法稳定回指 artifact item 的派生文本

### canonical_item_key 生成优先级

提案按以下优先级生成 `canonical_item_key`：

1. artifact contract 显式提供的稳定 item key
2. 可由 item 主键字段确定性归一化得到的 key
3. 在 canonical 排序已被 contract 固定时，使用 `collection + index`

规则：

- 若存在稳定 key，不得退回 index
- 若只能使用 index，则 artifact contract 必须显式声明 canonical order
- projection order 不得进入 canonical item identity

## Finding Identity And Provenance

### finding_id

提案：

`finding_id = stable_hash(artifact_id, finding_type, canonical_item_key)`

不得进入 `finding_id` 的字段：

- `extractor_version`
- `artifact_schema_version`
- `projection_ref`
- summary text
- rank
- explanation text

### artifact_item_ref

`artifact_item_ref` 必须使用结构化 ref：

```ts
type ArtifactItemRef = {
  collection: "value" | "rows" | "buckets" | "candidates" | "points" | "result";
  index: number | null;
  key: string | null;
};
```

规则：

- 有稳定 key 时，`key` 必须非空
- 只有在 contract 不提供稳定 key 且 canonical order 已固定时，才允许 `index`
- `index` 不能来自 projection 截断后的局部顺序

### 公共字段生成

每条 finding 都应由 extractor 直接生成以下字段：

- `step_ref`
- `subject`
- `observed_window`
- `quality`
- `provenance.artifact_item_ref`

其中：

- `subject` 必须来自 artifact 的 typed semantics，而不是后续推理猜测
- `observed_window` 能确定时必须写入；不能确定时返回 `null`
- `quality` 只表达数据质量与可消费性，不表达 judgment

## Family Rules

### observe

`observe` 的 finding subtype 固定为 `observation`。

抽取规则：

- `scalar` artifact -> 1 个 `observation` finding
- `time_series` artifact -> 每个 bucket 1 个 `observation` finding
- `segmented` artifact -> 每个 row 1 个 `observation` finding
- `numeric_sample_summary` artifact -> 1 个 `observation` finding
- `rate_sample_summary` artifact -> 1 个 `observation` finding

`observation_kind` 由 request profile 唯一决定，不允许单个 artifact 混出多个 family。

建议的 `canonical_item_key`：

- scalar: `value`
- time bucket: bucket boundary key
- segment: normalized segment key
- sample summary: `result`

empty semantics：

- `observe` 允许 success-empty
- empty artifact 表示：scope 已解析、执行已完成，但不存在 canonical value / bucket / segment / summary item
- `no data` 与 `empty population` 在 v1 有意折叠为同一种 legal success-empty
- success-empty 的 `observe` artifact 可作为合法上游 artifact outcome 存在，但不得被下游 typed intents 视为“已有可用 canonical 数据”

### compare

`compare` 的 finding subtype 固定为 `delta`。

抽取规则：

- `scalar_delta` artifact -> 1 个 `delta` finding
- `segmented_delta` artifact -> 每个 delta row 1 个 `delta` finding

payload 必须直接携带：

- `left_ref`
- `right_ref`
- `left_value`
- `right_value`
- `absolute_delta`
- `relative_delta`
- `direction`
- `presence`
- `unit`

建议的 `canonical_item_key`：

- scalar delta: `result`
- segmented delta: normalized segment key

empty semantics：

- `compare` 不允许 success-empty
- 若无法形成任何 canonical `delta` item，应在 validation 或 execution 阶段以 `not_comparable` 或等价错误失败
- 空或不足的上游 observation prerequisite 不得导致 empty committed artifact；请求应直接失败

### decompose

`decompose` 的 finding subtype 固定为 `decomposition_item`。

抽取规则：

- 每个 contribution row 1 个 `decomposition_item` finding
- `scope_delta_ref` 必须显式指向上游 compare 抽出的 canonical delta finding

payload 必须直接携带：

- `dimension`
- `keys`
- `contribution_value`
- `contribution_share`
- `direction`
- `scope_delta_ref`

建议的 `canonical_item_key`：

- `dimension + normalized key tuple`

不建议继续沿用“整列 contribution 压成一条 observation”的实现方式，因为这会把多个 item boundary 合并成一个 fact。

empty semantics：

- `decompose` 不允许 success-empty
- 若没有 contribution rows，则请求失败，并以 `not_attributable` 或等价错误结束
- 不得提交 empty committed artifact

### attribute

提案：`attribute` 不引入新的 canonical finding family。

规则：

- `attribute_bundle` 仍可作为 derived artifact / read object 存在
- canonical facts 只复用内部原子产物：
  - compare 产出的 `delta`
  - decompose 产出的 `decomposition_item`

理由：

- 避免 proposition seeding 依赖 derived bundle
- 保持统一的 finding family taxon
- 让 `attribute` 只是编排层 shortcut，而不是新的事实层 authority

### detect

`detect` 的 finding subtype 固定为 `anomaly_candidate`。

抽取规则：

- 每个 candidate 1 个 `anomaly_candidate` finding
- `candidate_ref` 必须指向 artifact 内对应 candidate item

payload 必须直接携带：

- `candidate_ref`
- `score`
- `flag_level`
- `actual_value`
- `expected_value`
- `deviation_absolute`
- `deviation_relative`

建议的 `canonical_item_key`：

- candidate stable key
- 若 contract 无 key，则使用 canonical candidate order 下的 `index`

empty semantics：

- `detect` 允许 success-empty
- empty artifact 表示 scan 已完成，且 `total_candidate_count = 0`
- success-empty 的 `detect` artifact 不会 seed proposition，也不得被回放为 synthetic finding

### correlate

`correlate` 的 finding subtype 固定为 `correlation_result`。

提案：

- v1 中每个 `pairwise_time_series_association` artifact 只生成 1 个 `correlation_result` finding

payload 必须直接携带：

- `left_ref`
- `right_ref`
- `method`
- `coefficient`
- `p_value`
- `n`
- `join_basis`

建议的 `canonical_item_key`：

- `result`

empty semantics：

- `correlate` 不允许 success-empty
- v1 仅在得到 `aligned` 或 `needs-attention` 的合法相关性结果时提交 1 个 finding
- 若 alignment 不足、有效样本对不足或无法形成 defensible result，则请求失败，不提交 empty artifact

### test

`test` 的 finding subtype 固定为 `test_result`。

提案：

- v1 中每个 `hypothesis_test` artifact 只生成 1 个 `test_result` finding

payload 必须直接携带：

- `left_ref`
- `right_ref`
- `method`
- `estimate_value`
- `statistic_name`
- `statistic_value`
- `p_value`
- `reject_null`
- `alpha`

建议的 `canonical_item_key`：

- `result`

empty semantics：

- `test` 不允许 success-empty
- v1 对每个合法 `hypothesis_test` artifact 提交 1 个 `test_result` finding
- 若 test 请求无效，或无法形成可消费 test result，则请求失败，不提交 empty artifact

### forecast

`forecast` 的 finding subtype 固定为 `forecast_point`。

抽取规则：

- 每个 future bucket / point 1 个 `forecast_point` finding

payload 必须直接携带：

- `bucket_start`
- `bucket_end`
- `predicted_value`
- `prediction_interval`
- `horizon_index`

建议的 `canonical_item_key`：

- forecast bucket boundary key

empty semantics：

- `forecast` 不允许 success-empty
- 只有在能够生成可辩护的 point forecast 时才允许 success
- 若历史不足、模型条件不满足或无法形成 defensible forecast point，则请求失败，不提交 empty artifact

## Downstream Semantics And Commit Path

### downstream semantics

- empty committed finding set 仅作为 artifact outcome 具有权威性，不作为 proposition seed
- `observe` / `detect` 的 success-empty artifact 是合法 committed outcome，但不会注册 proposition seed
- 下游 typed intents 遇到 empty upstream prerequisite 时，必须在 validation 或 execution 阶段拒绝，而不是把该空上游结果继续包装成新的 empty artifact
- v1 不引入 synthetic `no results found` / negative-result finding family

### commit path

eventual code path 建议固定为：

1. 创建 staged artifact
2. 基于 `(artifact_type, artifact_schema_version)` 选择 extractor
3. 运行 deterministic extraction，得到 finding set
4. 在 commit finalization 前执行 family-specific empty / non-empty validation
5. 只有 artifact 与 findings 同时满足 family contract 时，才提交 committed canonical state

不建议保留单一全局规则去判定所有 mandatory family 必须 non-empty；empty/non-empty 的合法性应由 family contract 明确给出。

## Failure And Replay Rules

### failure semantics

对 mandatory family：

- 若 artifact payload 缺少 extraction 所需字段，应 extraction failure
- 若 item collection 为空，是否允许 committed empty finding set 由对应 artifact family contract 决定
- 若同一 item 映射出多个 `finding_id`，应 extraction failure
- 若上游 prerequisite 为空，且当前 family contract 要求 non-empty success，应 validation failure 或 execution failure，而不是提交 empty artifact

### replay semantics

replay 时：

- 同一 `artifact_id + canonical_item_key + finding_type` 必须得到同一 `finding_id`
- extractor 升级但 item boundary 未变化时，不得漂移 `finding_id`
- extractor 升级造成 payload 解释变化时，只能通过 version / audit 暴露，不得静默覆盖旧 finding

## 与当前实现的边界

当前代码中的 observation seam 可以作为过渡参考，但不建议直接视为目标态 finding 规则，主要原因是：

- 当前 `observation_id` 为随机值，不满足 replay 稳定性
- 当前 extraction 发生在 artifact insert 前后交错，提交边界不够硬
- 当前部分 extractor 会把多 item 汇总成一条 observation，而不是逐 item 生成 fact

因此，提案的目标不是“把 observation 改个名字”，而是把事实层 authority 从当前实现中重新收束到稳定 item boundary。

## 待批准技术决策

以下点建议先审批，再进入 canonical 主文档：

### D1. Extractor 路由键

提案：

- 采用 `(artifact_type, artifact_schema_version)` 路由 extractor

审批状态：

- approved

备选：

- 继续按 `step_type` 路由

### D2. canonical_item_key 优先级

提案：

- 稳定 key 优先，只有在 contract 明确固定 canonical order 时才允许 index

审批状态：

- approved

备选：

- 统一使用 index

### D3. `attribute` 是否拥有独立 canonical finding family

提案：

- 不新增 family，只复用 `delta` 与 `decomposition_item`

审批状态：

- approved

备选：

- 引入新的 `attribute_driver` finding

### D4. mandatory family 的空结果语义

提案：

- empty finding set 是否合法由对应 artifact family contract 定义，不再全局一刀切：
  - `observe` / `detect` 允许 success-empty
  - `compare` / `decompose` / `correlate` / `test` / `forecast` success 必须 non-empty

审批状态：

- approved

备选：

- 继续要求所有 mandatory family committed non-empty

### D5. correlate / test 的粒度

提案：

- v1 一律 `1 artifact -> 1 finding`

审批状态：

- approved

备选：

- 把对齐样本对、assumption checks 或 method variants 继续拆成多个 finding

## 建议的下一步

在上述决策获批后，再做两件事：

1. 把本文吸收到 `runtime-pipeline.md` 与 `schemas/finding.md`
2. 基于批准结果补实现级迁移方案，明确当前 observation seam 如何过渡到 finding seam

## 实现状态（Phase 4a-4）

D1～D5 均已批准，以下基础合同已落地：

- **D1 (extractor dispatch key)**：`artifacts.artifact_schema_version TEXT` 列已通过 migration 补入 `app/storage/schema.py`；NULL 值按 `'v1'` 约定处理。extractor dispatch 路由键固定为 `(artifact_type, artifact_schema_version)`。
- **D4 (family empty semantics)**：`app/evidence_engine/family_contract.py` 将 D4 规则编码为 `FAMILY_ALLOWS_EMPTY` dict（`observe`/`detect` = True；其余 5 个 family = False）。commit path 应调用 `check_finding_count(family, count)` 代替散落的 if/else；未知 family 默认按 non-empty-required 处理（fail-safe）。
- **Extractor output contract**：`app/evidence_engine/canonical_finding.py` 新增 `FindingExtractionResult` TypedDict（`findings`, `extractor_name`, `extractor_version`, `artifact_schema_version`, `finding_count`）作为所有 extractor 的统一返回结构。
- **具名 finding subtypes**：`canonical_finding.py` 新增 7 个具名 TypedDict（`ObservationFinding`, `DeltaFinding`, `DecompositionItemFinding`, `AnomalyCandidateFinding`, `CorrelationResultFinding`, `TestResultFinding`, `ForecastPointFinding`）和 `AnyFinding` union，供 extractor 实现做静态类型标注。

## 实现状态（Phase 4b-2）

- **D1 (extractor registry)**：`app/evidence_engine/finding_extractor_registry.py` 实现 `FindingExtractor` ABC 和 `FindingExtractorRegistry`，以 `(artifact_type, artifact_schema_version)` 为 dispatch key。`default_finding_registry` 模块级单例在 import 时为空；4d-* extractor 模块调用 `default_finding_registry.register(...)` 填充。`find(artifact_type, None)` 将 NULL 归一化为 `'v1'`（lenient lookup）；`get(artifact_type, version)` 严格匹配，不做归一化。`snapshot()` 返回按 `(artifact_type, artifact_schema_version)` 排序的可审计快照，包含 `extractor_name`、`extractor_version`、`finding_schema_version`，支持 replay / 版本变更审计。

## 实现状态（Phase 4b-3）

- **D2 (identity helper consolidation)**：`app/evidence_engine/canonical_finding.py` 新增两个 helper：
  - `make_artifact_item_ref(collection, key, index)` → `ArtifactItemRef`：与 `make_canonical_item_key` 采用相同的 D2 优先级规则（stable key > index > bare collection），确保两者始终对齐；当 key 非 None 时，`index` 字段强制置 None。
  - `make_item_identity(collection, key, index)` → `tuple[str, ArtifactItemRef]`：单次调用原子性地产出 `canonical_item_key` 与 `artifact_item_ref`，消除各 family extractor 分别调用时可能选择不同优先级分支的风险。4d-* extractor 应调用此函数而非分别调用两个底层 helper。
- **公共断言工具**：`tests/finding_identity_testutil.py` 提供三个无状态断言函数：`assert_finding_id_stable`、`assert_stable_key_beats_index`、`assert_projection_order_excluded`，供 4d-* extractor 测试套件直接 import。
- **测试**：`tests/test_finding_identity_helper.py` 30 个测试，全部通过。
