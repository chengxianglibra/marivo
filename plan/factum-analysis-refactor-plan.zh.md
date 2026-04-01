# Factum 分析流程重构升级计划

## 1. 背景

当前 Factum 的分析主流程仍然建立在旧的 step taxonomy、legacy step API、以及旧的 evidence 增量合成链路之上。`docs/analysis/` 下的新设计已经明确了新的目标态：

- Agent 直接在 `session` 内编排并提交 typed intent steps
- 不再保留独立的 `step plan` 资源
- 外部分析写面以 atomic / derived intents 为唯一正式契约
- Evidence Engine 按 canonical pipeline 运行：

`artifact -> finding -> proposition -> assessment -> action proposal`

- 读取面切换为：
  - session root
  - session state surface
  - proposition context surface

当前仓库处于预研阶段，尚未正式上线，因此本次升级按破坏式迭代执行，不以兼容历史对外契约为目标。

## 2. 目标

本次升级的目标是，在尽量复用现有语义层、查询编译、执行引擎和治理校验底座的前提下，将 Factum 的分析主路径切换到新的 intent + evidence-engine 架构。

升级完成后应满足：

- Agent 可以只依赖 `session` 和 typed intent steps 完成分析
- `plans` 不再是正式架构组成
- 新 intent API 替代旧 public step API
- canonical evidence pipeline 成为唯一正式证据主链
- `/sessions/{id}`、`/state`、`/context` 成为正式读取面

## 3. 范围

### 3.1 纳入范围

- 新的 session 内 intent step 写面
- intent 执行组织模型
- derived intent 的 deterministic expansion
- canonical evidence persistence 与 runtime pipeline
- session/state/context 读面
- 相关文档、测试与调试接口收敛

### 3.2 明确移除

- `plans` 资源及其生命周期
- 旧 public step contract
- 旧 observation/claim/recommendation 增量 synthesis 作为 canonical 主路径

### 3.3 复用前提

以下底座优先保留并复用：

- `semantic_runtime`
- `query_router`
- `analysis_core/compiler.py`
- `analysis_core/executor.py`
- 现有 analytics engine 抽象
- 现有 governance 校验与 metrics 采集能力

## 4. 实施原则

- Factum 仍然保持 HTTP-only，不引入 MCP 依赖
- typed intent 是外部主契约，不暴露 SQL-shaped 外部接口
- factual extraction 保持 deterministic
- derived intent 的内部展开是运行时结构，不重新引入 planner / plan 体系
- session root 不承载 step-level scope / time_scope
- 每个阶段完成后同步更新文档与测试，避免实现和设计脱节

## 5. 目标架构摘要

升级后的分析主路径如下：

1. 创建 session
2. Agent 在 session 内直接提交 atomic 或 derived intent step
3. 系统执行 step 并 materialize canonical artifact
4. 系统同步完成 finding extraction
5. 系统完成 proposition seeding / registration
6. 系统 recompute latest assessment
7. 系统刷新 action proposals
8. Agent 通过 `/sessions/{id}/state` 和 `/sessions/{id}/propositions/{pid}/context` 消费 canonical state

关键约束：

- `latest/live` 是读取层语义，不是对象本体 mutable flag
- mandatory extraction artifact 不允许进入 “artifact committed but finding missing” 的非法中间态
- derived intent expansion 只依赖请求和系统状态，不允许中间外部决策

## 6. 分阶段执行计划

## 阶段 0：基线收口

### 目标

先收口当前仓库里的旧主路径入口、依赖点和耦合边界，为后续重构提供统一基线。

### 任务

- 盘点旧分析入口和调用链：
  - `app/api/sessions.py`
  - `app/service.py`
  - `app/analysis_core/`
  - `app/api/planning.py`
  - `app/planning.py`
  - `app/planner/`
- 盘点旧 evidence 主路径：
  - `app/evidence_engine/`
  - `app/reflection/`
  - `app/storage/schema.py`
- 明确保留模块、替换模块、过渡复用模块
- 清点受影响的测试集合和文档集合

### 交付物

- 模块迁移清单
- 旧入口到新入口的映射关系
- 待删除或重写的测试清单

### 验收标准

- 团队对 “哪些模块保留、哪些模块下线、哪些模块仅保留底座能力” 没有歧义
- 对 `plans` 已被移除、legacy public steps 不再保留正式地位达成一致

---

## 阶段 1：移除 Plan 主路径

### 目标

先从正式架构中拿掉 `plans`，避免新 intent 主路径继续依赖旧 orchestration 模型。

### 任务

- 下线或移除 `app/api/planning.py` 的正式 API 路由
- 清理 `app/planning.py`、`app/planner/` 在主执行链上的依赖
- 调整 app/router/service 层，确保分析主路径只依赖 session + step submission
- 在文档中明确：
  - session 是唯一分析容器
  - agent 直接在 session 内顺序提交 steps
  - derived intent 的内部 DAG 不是 plan object

### 交付物

- 无 `plans` 依赖的主流程代码路径
- 更新后的架构文档

### 验收标准

- 主流程不再依赖 `/plans`
- 删除 `plans` 后，session 和 step 主路径仍可正常工作
- 文档中不再把 `plans` 作为目标态架构的一部分

---

## 阶段 2：建立新的 Intent Action Surface ✅ 已完成

### 目标

将对外 step 写面从旧 taxonomy 切换为新的 typed intent 契约。

### 任务

- 在 `app/api/models.py` 中引入或重构以下模型：
  - `ObserveRequest/Response`
  - `CompareRequest/Response`
  - `DecomposeRequest/Response`
  - `CorrelateRequest/Response`
  - `DetectRequest/Response`
  - `TestRequest/Response`
  - `ForecastRequest/Response`
  - `AttributeRequest/Response`
  - `DiagnoseRequest/Response`
  - `ValidateRequest/Response`
- 引入 typed ref 结构和 same-session 校验规则
- 在 `app/api/sessions.py` 中实现新的 intent endpoints
- 删除 legacy public endpoints：
  - `/steps/metric_query`
  - `/steps/aggregate_query`
  - `/steps/attribute_change`
  - generic `/steps/{step_type}`

### 交付物

- 新的 session 内 typed intent 写接口
- typed ref contract
- 更新后的 `docs/api/intent-steps.md`

### 验收标准

- 新 intent endpoints 可以完成 schema 校验和错误返回
- path 本身作为 intent discriminator，不再依赖 legacy `step_type`
- legacy public step endpoints 不再出现在正式 API 文档中

---

## 阶段 3：重构执行模型为 Intent Registry + Derived Expansion

> **实施说明**：本阶段拆分为顺序子任务（3a → 3b-1 → 3b-2 → 3b-3 → 3b-4 → 3b-5 → 3b-6 → 3c-1 → 3c-2 → 3c-3）。3b-1 ～ 3b-6 各对应一个 atomic runner，均依赖 3a 确定的 artifact 持久化模型和 registry 接口；3c-1 ～ 3c-3 各对应一个 derived intent，统一依赖全部 atomic runners 就绪。3c-1 已完成。

---

### 阶段 3a：Registry 基础 + Artifact 模型 + `observe` 全量 Runner

#### 目标

建立 intent 执行的架构基础：`IntentRunnerRegistry`、artifact 持久化 schema、以及第一个完整的 atomic runner（`observe`）。`observe` 是其他所有 runner 的上游依赖，须优先落地。

#### 任务

- 在 `app/analysis_core/` 中新建 `IntentRunnerRegistry`，平行于现有 `StepRunnerRegistry`
- 在 metadata schema 中建立 artifact 持久化结构（staged / committed lifecycle，关联 `step_id`）
- 将 `observe` 从当前的 translate stub 升级为完整 runner：
  - 产出 typed observation artifact（非 metric_query 代理结果）
  - 正确处理 time/scope 规范化
- 更新 `service.run_intent` 通过 `IntentRunnerRegistry` 分发，替代现有 if/else 分支
- 同步更新文档与测试

#### 交付物

- `IntentRunnerRegistry` 实现
- artifact 持久化 schema（staged/committed lifecycle）
- `observe` 全量 runner
- `run_intent` 走 registry 的分发逻辑
- 对应单元测试

#### 验收标准

- `observe` intent 可执行并持久化 committed artifact
- artifact schema 足以支撑后续 runner 的 ref 输入（ObservationRef 可解析）
- `run_intent` 不再包含硬编码的 intent if/else 分支

---

### 阶段 3b-1：`compare` Runner ✅ 已完成

#### 目标

实现第一个依赖 ObservationRef 的推断型 runner：从两个已有观测值计算 typed delta，产出 compare artifact。

#### 依赖

3a（ObservationRef artifact schema 已定义）

#### 任务

- 在 `service.py` 中实现 `_run_compare_intent`
- 输入：`ObservationRef` × 2（同 session）
- 计算 typed delta items（绝对差、相对差）
- 产出 committed compare artifact
- 注册到 `IntentRunnerRegistry`
- 同步更新文档与测试

#### 说明

无现有内核可复用；需新实现 delta 计算逻辑。Empty semantics：success 必须 non-empty（无法形成 delta 则以明确错误失败，不 commit success artifact）。

#### 交付物

- `_run_compare_intent` 实现
- compare artifact schema（delta items）
- 对应单元测试

#### 验收标准

- `compare` 可执行，产出 committed artifact
- 无法形成 delta 时返回明确错误，不 commit success artifact
- `ObservationRef` 跨 session 时被拒绝

---

### 阶段 3b-2：`decompose` Runner ✅ 已完成

#### 目标

在 compare artifact 基础上，复用 `_run_attribute_change` 内核实现维度分解。

#### 依赖

3b-1（compare ArtifactRef 作为输入前提）

#### 任务

- 在 `service.py` 中实现 `_run_decompose_intent`
- 输入：compare `ArtifactRef`（同 session）
- 从 compare artifact 提取维度分解参数，调用 `_run_attribute_change`（`service.py:2402`）内核
- 产出 committed decompose artifact（含 contribution items）
- 注册到 `IntentRunnerRegistry`
- 同步更新文档与测试

#### 说明

有现有内核 `_run_attribute_change` 可复用，主要工作是切换输入契约（ArtifactRef → 内核参数）。Empty semantics：同 compare，无 contribution rows 时失败。

#### 交付物

- `_run_decompose_intent` 实现
- 对应单元测试

#### 验收标准

- `decompose` 接受 compare ArtifactRef，产出 committed artifact
- 无 contribution rows 时失败，不 commit success artifact

---

### 阶段 3b-3：`correlate` Runner ✅ 已完成

#### 目标

复用 `_run_correlate_metrics` 内核，将输入契约切换为 ObservationRef × 2。

#### 依赖

3a（ObservationRef artifact schema）

#### 任务

- 在 `service.py` 中实现 `_run_correlate_intent`
- 输入：`ObservationRef` × 2（同 session）
- 从两个 artifact 提取时间序列数据，调用 `_run_correlate_metrics`（`service.py:2733`）内核
- 产出 committed correlate artifact（`1 artifact → 1 finding`）
- 注册到 `IntentRunnerRegistry`
- 同步更新文档与测试

#### 说明

有现有内核 `_run_correlate_metrics` 可复用，主要工作是适配输入契约。Empty semantics：alignment 或 pairs 不足时失败；有效结果恰好产出 1 个 finding。

#### 交付物

- `_run_correlate_intent` 实现
- 对应单元测试

#### 验收标准

- `correlate` 接受两个 ObservationRef，产出 committed artifact
- pairs 不足时失败；有效结果仅提交 1 个 finding

---

### 阶段 3b-4：`detect` Runner ✅ 已完成

#### 目标

实现最小可用的异常扫描 runner；这是唯一另一个允许 success-empty 的 atomic intent。

#### 依赖

3a（artifact schema）

#### 任务

- 在 `service.py` 中实现 `_run_detect_intent`
- 输入：metric + time_scope + 检测参数（阈值或统计规则）
- v1 实现：基于简单阈值或 z-score 扫描候选异常
- 产出 committed detect artifact（`total_candidate_count` 字段必须存在）
- 无候选时产出 empty artifact（`finding_count = 0`），仍算 success
- 注册到 `IntentRunnerRegistry`
- 同步更新文档与测试

#### 说明

无现有内核；需新实现。Empty semantics：**允许 success-empty**（与 `observe` 同类型，`total_candidate_count = 0` 时合法 commit）。

#### 交付物

- `_run_detect_intent` 实现
- 对应单元测试（含 empty 场景）

#### 验收标准

- 无候选时提交 empty success artifact（`finding_count = 0`）
- 有候选时产出对应 finding

---

### 阶段 3b-5：`test` Runner ✅ 已完成

#### 目标

实现最小可用的统计假设检验 runner。

#### 依赖

3a（artifact schema）

#### 任务

- 在 `service.py` 中实现 `_run_test_intent`
- 输入：hypothesis 定义 + 数据源（metric 或 ObservationRef）
- v1 实现：支持均值检验或比例检验（`scipy.stats` 或手写；不引入 ML 依赖）
- 产出 committed test artifact（`1 artifact → 1 finding`，结果为 `valid` 或 `needs-attention`）
- 注册到 `IntentRunnerRegistry`
- 同步更新文档与测试

#### 说明

无现有内核；需新实现。Empty semantics：success 必须 non-empty，输入无效时以明确错误失败。

#### 交付物

- `_run_test_intent` 实现
- 对应单元测试

#### 验收标准

- 输入无效时以明确错误失败
- 有效结果产出恰好 1 个 finding

---

### 阶段 3b-6：`forecast` Runner

#### 目标

实现最小可用的时序预测 runner。

#### 依赖

3a（artifact schema）；依赖 `observe` runner 产出的时序 artifact 作为历史输入

#### 任务

- 在 `service.py` 中实现 `_run_forecast_intent`
- 输入：metric + 历史时间窗（ObservationRef 或 time_scope）+ 预测 horizon
- v1 实现：线性外推或移动平均（不引入 ML 依赖）
- 产出 committed forecast artifact（含 forecast buckets；按 bucket 产出 finding）
- 注册到 `IntentRunnerRegistry`
- 同步更新文档与测试

#### 说明

无现有内核；需新实现。Empty semantics：success 必须 non-empty，历史不足或无法产生可辩护 point forecast 时失败。

#### 交付物

- `_run_forecast_intent` 实现
- 对应单元测试

#### 验收标准

- 历史不足时失败，不 commit success artifact
- 有效结果产出 committed artifact 和对应 finding

---

> **阶段 3b 整体验收**（3b-1 ～ 3b-6 全部完成后）：
> - 全部 7 个 atomic intents（含 3a 的 observe）可独立执行
> - 每个 runner 产出符合 artifact schema 的 committed artifact
> - `ObservationRef` / `ArtifactRef` 跨 runner 的 ref 解析路径均正确
> - lineage / provenance 约束统一覆盖

---

### 阶段 3c-1：`attribute` Derived Expansion ✅ 已完成

#### 目标

在全部 atomic runners 就绪后，先单独落地 `attribute` 的 deterministic expansion，使“变化量化 + 变化归因”形成一条可独立实现、测试和验收的 derived intent 路径。

#### 依赖

3a + 3b 全部完成（尤其 `observe`、`compare`、`decompose`）

#### 任务

- 将 `attribute` 固定展开为 `observe + observe + compare + decompose`
- 约束展开规则只依赖请求和系统状态，不要求执行中外部补充决策
- 为左右两侧 observation 建立稳定的 sub-step / artifact ref 链路
- 固定内部 `compare` 为 scalar compare，并按请求维度逐个执行 `decompose`
- 聚合 derived 层结果为 `attribute` bundle / projection，并暴露完整 lineage
- 同步更新文档与测试

#### 交付物

- `attribute` expansion 实现
- `attribute` 端到端 expansion 测试

#### 验收标准

- `attribute` 可在单次请求中展开并执行完成
- 同一请求生成稳定的子步骤集合，不依赖 planner 或中间人工决策
- 展开后的 `observe` / `compare` / `decompose` artifact ref 链路正确

---

### 阶段 3c-2：`diagnose` Derived Expansion

#### 目标

在 atomic runners 基础上，单独落地 `diagnose` 的候选跟进型 deterministic expansion，使异常发现、异常量化和归因拆解形成一条受控的 derived intent 路径。

#### 依赖

3a + 3b 全部完成（尤其 `detect`、`compare`、`decompose`）

#### 任务

- 将 `diagnose` 固定展开为 `detect + compare + decompose`
- 基于 `detect` artifact 中的稳定排序结果，只跟进 top-K candidates
- 对每个被跟进 candidate 使用固定策略推导 current / baseline，再执行后续 compare
- 对每个 candidate × dimension 执行 `decompose`
- 明确 candidate selection 属于 contract 内固定逻辑，不退化成开放式 planner
- 聚合 derived 层结果为 `diagnosis` bundle / projection，并披露未跟进候选
- 同步更新文档与测试

#### 交付物

- `diagnose` expansion 实现
- `diagnose` 端到端 expansion 测试

#### 验收标准

- `diagnose` 可在单次请求中展开并执行完成
- expansion 不依赖中间人工决策，top-K candidate follow-up 规则可重复验证
- 展开后的 `detect` / `compare` / `decompose` artifact ref 链路正确

---

### 阶段 3c-3：`validate` Derived Expansion

#### 目标

在 atomic runners 基础上，单独落地 `validate` 的 inferential-ready deterministic expansion，使样本准备与假设检验形成一条可独立验收的 derived intent 路径。

#### 依赖

3a + 3b 全部完成（尤其 `observe`、`test`）

#### 任务

- 将 `validate` 固定展开为 `observe + test`
- 以左右两侧 inferential-ready observation 作为唯一上游输入
- 对 `sample_kind`、`method` 和 hypothesis 默认值执行确定性推导或显式校验
- 构造内部 `test` 请求并聚合 derived 层结果为 `validation` bundle / projection
- 确保 expansion 规则只依赖请求和系统状态，不生成独立 plan 资源
- 同步更新文档与测试

#### 交付物

- `validate` expansion 实现
- `validate` 端到端 expansion 测试

#### 验收标准

- `validate` 可在单次请求中展开并执行完成
- `auto` 模式下的 inferential summary mode / method 选择具备确定性，无法唯一确定时明确失败
- 展开后的 `observe` / `test` artifact ref 链路正确

---

## 阶段 4：重建 Canonical Evidence Pipeline

### 目标

将旧 observation/claim/recommendation 主链替换为新的 canonical evidence pipeline。

> **实施说明**：本阶段拆分为顺序子任务（4a-1 → 4a-2 → 4a-3 → 4b-1 → 4b-2 → 4b-3/4b-4 → 4c-1 → 4c-2 → 4d-* → 4e-* → 4f-* → 4g-* → 4h-*）。其中 `4d-*` 可在统一 commit path 落地后按 family 并行推进；`4f-*` 与 `4g-*` 统一依赖 seeding 基线完成。

---

### 阶段 4a-1：Canonical Evidence DDL 骨架

#### 目标

先在 metadata schema 中建立新的 canonical persistence 骨架，为 finding / proposition / assessment / action proposal 链路提供正式持久化边界。

#### 任务

- 在 `app/storage/schema.py` 中新增 canonical persistence：
  - `findings`
  - `propositions`
  - `assessments`
  - `action_proposals`
  - 必要的 membership/ref 表
- 明确保留并复用：
  - `sessions`
  - `steps`
  - `artifacts`
- 确保 DDL 不破坏当前已存在的 metadata 初始化路径
- 同步更新与 schema 相关的实现说明文档

#### 交付物

- 新 canonical evidence DDL
- 表间引用关系与对象职责说明

#### 验收标准

- metadata store 可成功初始化新表
- `sessions` / `steps` / `artifacts` 不需要重建即可继续复用
- 新表结构足以承接后续 runtime pipeline 与 read surface 设计

---

### 阶段 4a-2：Finding 公共字段与 Provenance 持久化

#### 目标

把 `finding` 的公共字段、provenance 与 identity 所需的最小持久化结构补齐，为 extractor 输出提供稳定落点。

#### 依赖

4a-1

#### 任务

- 为 `finding` 持久化结构落实以下公共字段：
  - `finding_id`
  - `finding_type`
  - `artifact_id`
  - `step_ref`
  - `subject`
  - `observed_window`
  - `quality`
  - `provenance`
- 为 `artifact_item_ref`、`canonical_item_key`、extractor metadata 预留稳定字段
- 明确 `finding_id = stable_hash(artifact_id, finding_type, canonical_item_key)` 的实现边界
- 同步更新文档与测试

#### 交付物

- finding 持久化字段方案
- identity / provenance 对应测试

#### 验收标准

- finding 持久化结构足以表达 `artifact_item_ref`
- 同一 `artifact_id + item boundary + finding_type` 重放结果可稳定命中同一 `finding_id`
- `rank`、projection order、summary text 不进入 canonical identity

---

### 阶段 4a-3：Membership / Ref 表设计

#### 目标

补齐 proposition、assessment、action proposal 所需的 membership 与 ref 持久化结构，但不把实时 evidence membership 回写进 proposition 本体。

#### 依赖

4a-2

#### 任务

- 为以下关系建立持久化结构：
  - proposition `seed_finding_refs`
  - assessment membership / inference / gap / transition 关联
  - action proposal lineage / context refs
- 明确 proposition 仅保存 creation-time seed refs，不回写实时支持/反驳集合
- 对齐 `graph-and-reference-semantics` 的 typed ref 语义
- 同步更新文档与测试

#### 交付物

- membership/ref schema
- 对应对象关系图或说明

#### 验收标准

- proposition / assessment / action proposal 的 lineage 与 membership 可独立表达
- proposition 本体不承担实时 evidence membership 职责

---

### 阶段 4b-1：Canonical Evidence Repository 边界

#### 目标

为 canonical evidence objects 建立独立 repository seam，避免 runtime 逻辑直接散落 SQL。

#### 依赖

4a-3

#### 任务

- 在 `app/storage/` 中抽取或新增 evidence repositories：
  - findings repository
  - propositions repository
  - assessments repository
  - action proposals repository
- 定义 repository 层的 typed read/write contract
- 在 service/runtime 层接入 repository seam

#### 交付物

- canonical evidence repositories
- repository contract tests

#### 验收标准

- runtime 不再需要在多个模块中直接拼接分散 SQL
- repository API 足以支撑 extraction / seeding / recompute / refresh

---

### 阶段 4b-2：Finding Extractor Registry

#### 目标

建立 `(artifact_type, artifact_schema_version)` → extractor 的稳定路由，替代旧 observation-centric extractor 假设。

#### 依赖

4b-1

#### 任务

- 在 `app/evidence_engine/` 中建立 finding extractor registry
- 固定 extractor dispatch key：
  - `artifact_type`
  - `artifact_schema_version`
- 记录 extractor name / version / finding schema version
- 为 replay / 审计提供 registry snapshot 语义

#### 交付物

- finding extractor registry
- extractor contract tests

#### 验收标准

- extractor 路由不依赖 legacy `step_type` 作为唯一键
- registry 版本变化可审计、可回放

---

### 阶段 4b-3：Canonical Item Key / Finding Identity Helper

#### 目标

统一 `canonical_item_key`、`artifact_item_ref`、`finding_id` 的生成逻辑，避免各 family 各自实现导致 identity 漂移。

#### 依赖

4b-2

#### 任务

- 提供统一 helper 生成：
  - `canonical_item_key`
  - `artifact_item_ref`
  - `finding_id`
- 固定稳定 key 优先、index 仅作 contract-backed fallback
- 为 replay / idempotency 测试提供公共断言工具

#### 交付物

- finding identity helper
- identity stability tests

#### 验收标准

- 稳定 key 存在时不回退到 index
- projection/top-k 顺序不会进入 canonical identity

---

### 阶段 4b-4：Family-Level Empty Semantics Contract

#### 目标

将不同 artifact family 的 empty / non-empty success 规则独立建模，并作为 commit path 的统一约束输入。

#### 依赖

4b-2

#### 任务

- 建立 family-level empty semantics contract：
  - `observe`：允许 success-empty committed artifact
  - `detect`：允许 success-empty committed artifact
  - `compare`：success 必须至少产出 1 个 finding
  - `decompose`：success 必须至少产出 1 个 finding
  - `correlate`：success 必须至少产出 1 个 finding
  - `test`：success 必须至少产出 1 个 finding
  - `forecast`：success 必须至少产出 1 个 finding
- 把规则设计成 commit path 可复用 contract，而不是 scattered if/else
- 同步更新文档与测试

#### 交付物

- family contract 实现
- empty semantics 回归测试

#### 验收标准

- `observe` / `detect` 的 success-empty 作为合法 canonical outcome
- 其他 mandatory extraction family 不能提交 empty committed finding set

---

### 阶段 4c-1：Artifact Commit Boundary 重构

#### 目标

把 staged artifact → deterministic finding extraction → committed artifact/finding set 收束为统一 canonical commit boundary。

#### 依赖

4b-3、4b-4

#### 任务

- 在 artifact 提交流程中引入 extraction seam
- 固定 mandatory extraction artifact 的最小 committed 可见单元为：
  - `artifact + extracted findings`
- 明确并实现以下非法中间态禁止规则：
  - `artifact committed but extraction pending`
  - `artifact committed but extraction failed`
- 同步更新文档与测试

#### 交付物

- 统一 commit boundary 实现
- extraction transaction tests

#### 验收标准

- extraction failure 会阻止 committed canonical state 落库
- mandatory extraction artifact 不再出现 “只有 artifact、没有 finding set” 的 committed 状态

---

### 阶段 4c-2：Mandatory Extraction Intent 接入统一 Commit Path

#### 目标

让所有 mandatory extraction family 统一走新的 canonical commit path，而不是在各 intent runner 内各自完成最终提交。

#### 依赖

4c-1

#### 任务

- 将以下 runner 接入统一 commit path：
  - `observe`
  - `compare`
  - `decompose`
  - `detect`
  - `correlate`
  - `test`
  - `forecast`
- 清理 family-specific commit path 中重复的最终提交流程
- 保持 typed artifact payload contract 不变

#### 交付物

- 统一 commit path 接入后的 intent runners
- runner integration tests

#### 验收标准

- 所有 mandatory extraction family 的 committed 写入都经由统一 commit path
- family-specific empty semantics 在该路径上生效

---

### 阶段 4d-1：`observe` Finding Extractor

#### 目标

实现 `observe` artifact → `observation` finding 的完整 extractor。

#### 依赖

4c-2

#### 任务

- 为 `scalar` artifact 产出 1 个 `observation` finding
- 为 `time_series` artifact 按 bucket 产出 findings
- 为 `segmented` artifact 按 row 产出 findings
- 为 inferential summary artifact 产出单 finding
- 支持合法 success-empty，但不引入 synthetic `no results found` finding

#### 交付物

- `observe` finding extractor
- `observe` extraction tests

#### 验收标准

- 各 `observe` 模式均能按 canonical item boundary 稳定抽取
- success-empty artifact 不会 seed proposition

---

### 阶段 4d-2：`detect` Finding Extractor

#### 目标

实现 `detect` artifact → `anomaly_candidate` finding 的 extractor，并支持 success-empty。

#### 依赖

4c-2

#### 任务

- 将 detect candidate item 映射为 `anomaly_candidate` finding
- 支持 `total_candidate_count = 0` 的合法 committed empty finding set
- 保持 candidate key / item ref 稳定

#### 交付物

- `detect` finding extractor
- `detect` extraction tests

#### 验收标准

- detect 可提交 success-empty artifact
- 非空时每个 candidate 对应 1 个 canonical finding

---

### 阶段 4d-3：`compare` / `decompose` Finding Extractors

#### 目标

实现 `compare` 与 `decompose` 两类派生 finding family 的 extractor。

#### 依赖

4c-2

#### 任务

- `compare` artifact → `delta` finding
- `decompose` artifact → `decomposition_item` finding
- 保持 item boundary 稳定，与 artifact contract 对齐
- 无 canonical item 时不允许 success commit

#### 交付物

- `compare` / `decompose` finding extractors
- 对应 extraction tests

#### 验收标准

- `compare` / `decompose` success 必须 non-empty
- `attribute` 后续可直接复用 `delta` / `decomposition_item` finding family

---

### 阶段 4d-4：`correlate` / `test` / `forecast` Finding Extractors

#### 目标

完成剩余 finding family 的 extractor 落地。

#### 依赖

4c-2

#### 任务

- `correlate` artifact → `correlation_result` finding
- `test` artifact → `test_result` finding
- `forecast` artifact → `forecast_point` finding
- 保持 `correlate` / `test` 在 v1 为 `1 artifact -> 1 finding`
- `forecast` 仅在可生成可辩护 point forecast 时允许 success

#### 交付物

- `correlate` / `test` / `forecast` finding extractors
- 对应 extraction tests

#### 验收标准

- family-specific finding contract 与设计文档一致
- extraction 结果可作为 proposition seeding 的唯一权威输入

---

### 阶段 4e-1：Proposition Seeding Registry

#### 目标

建立 seed template registry，把 finding → proposition 的规则收敛为稳定 canonical contract。

#### 依赖

4d-1、4d-2、4d-3、4d-4

#### 任务

- 建立 seeding registry，至少声明：
  - `template_id`
  - `template_version`
  - `derivation_version`
  - match mode
  - seed slot schema
  - output proposition family
- v1 覆盖：
  - `delta`
  - `decomposition_item`
  - `anomaly_candidate`
  - `correlation_result`
  - `test_result`
  - `forecast_point`
- 明确 `observation` 默认不 seed proposition

#### 交付物

- proposition seeding registry
- template registry tests

#### 验收标准

- seeding 规则不再散落在临时 if/else 中
- registry 版本变化可审计、可 replay

---

### 阶段 4e-2：Proposition Identity Normalization 与 Registration

#### 目标

实现 system-seeded proposition 的 identity normalization、创建与注册去重逻辑。

#### 依赖

4e-1

#### 任务

- 按 judgment semantics 实现 proposition identity normalization
- 保证 `system_seeded` 与 `agent_authored` identity 分区
- proposition 首次注册时写入 `seed_finding_refs`
- 命中既有 proposition identity 时不回写、不追加 `seed_finding_refs`

#### 交付物

- proposition registration runtime
- registration / dedupe tests

#### 验收标准

- 同 family、同 judgment semantics 的 system-seeded proposition 稳定去重
- authored / seeded 不发生跨来源合并

---

### 阶段 4e-3：Seeding Run 结果与受影响 Proposition 集合

#### 目标

为 assessment recompute 提供稳定的 `affected_proposition_ids` 输出边界。

#### 依赖

4e-2

#### 任务

- 形成稳定的 seeding run output：
  - `created_proposition_ids`
  - `existing_proposition_ids`
  - `affected_proposition_ids`
- 固定 seeding run 的 transaction boundary 与 replay 语义
- 为 assessment runtime 暴露稳定输入

#### 交付物

- seeding run result contract
- seeding transaction tests

#### 验收标准

- 相同 finding snapshot + registry snapshot 不会制造不同 proposition 集合
- assessment recompute 可只依赖 `affected_proposition_ids`

---

### 阶段 4f-1：Assessment Evaluation Context

#### 目标

为 proposition family 组装统一 assessment evaluation context。

#### 依赖

4e-3

#### 任务

- 组装 proposition-local canonical closure
- 按 family 规则解析 supporting / opposing / missing inputs
- 只消费 committed canonical objects
- 对齐 `assessment-evaluation-context.md`

#### 交付物

- assessment context builder
- context assembly tests

#### 验收标准

- evaluation context 的输入边界可测试、可解释
- 不回读 projection、UI summary、自由文本 explanation

---

### 阶段 4f-2：Assessment Recompute 与 Snapshot Persistence

#### 目标

对受影响 proposition 执行评估重算，并持久化 immutable assessment snapshots。

#### 依赖

4f-1

#### 任务

- 实现 assessment recompute runtime
- 仅在 judgment output 发生变化时提交新的 assessment snapshot
- 通过 supersede 链或读取语义解释 latest，而不是 mutable flag
- 对齐 gap / confidence / transition materialization 设计

#### 交付物

- assessment recompute 实现
- assessment snapshot tests

#### 验收标准

- assessment 是 immutable snapshot
- judgment output 未变化时允许 no-op
- latest/live 不回写对象本体

---

### 阶段 4g-1：Action Proposal Refresh

#### 目标

基于 latest assessment 刷新 canonical action proposals。

#### 依赖

4f-2

#### 任务

- 实现 latest assessment → action proposal refresh
- 限定 refresh authority input 为：
  - committed `latest_assessment`
  - proposition-local canonical closure
  - 显式 policy context
- 支持空 proposal 集与 no-op refresh

#### 交付物

- action proposal refresh 实现
- proposal refresh tests

#### 验收标准

- proposal refresh 只在 latest assessment 可解引用后触发
- canonical proposal 集未变化时可 no-op
- proposal 不回写 judgment semantics

---

### 阶段 4g-2：切断 Legacy Claims / Recommendations 的 Canonical Authority

#### 目标

让新 canonical pipeline 独立成为 authority source，为阶段 5 的读取面切换做准备。

#### 依赖

4g-1

#### 任务

- 新 runtime 不再把旧 `claims` / `recommendations` 作为 canonical authority source
- 旧链路仅保留 legacy/debug 角色
- 清理 service/runtime 中对旧主链的隐式依赖

#### 交付物

- 不依赖旧 claim/recommendation authority 的新 pipeline
- 对应回归测试

#### 验收标准

- finding / proposition / assessment / action proposal 可独立跑通
- 旧对象不再承担 canonical authority 职责

---

### 阶段 4h-1：Replay / Idempotency / Soft Invalidation

#### 目标

补齐 canonical evidence pipeline 的 replay、幂等与 soft invalidation 规则。

#### 依赖

4g-2

#### 任务

- 为 extraction、seeding、assessment、proposal 实现 replay 规则
- 保证 source item boundary 不变时 finding identity 不漂移
- 实现 soft invalidation：
  - 历史 canonical objects 保留
  - 通过 missing refs、membership 收缩、gap reopen、latest 选择变化暴露影响

#### 交付物

- replay / idempotency / invalidation 实现
- 对应测试

#### 验收标准

- replay 不改写既有 artifact identity
- 不通过硬删除伪装成“从未发生”

---

### 阶段 4h-2：测试与文档收口

#### 目标

补齐阶段 4 的 contract / integration / replay 测试，并让设计文档与实现状态保持同步。

#### 依赖

4h-1

#### 任务

- 补齐以下测试：
  - family empty semantics
  - commit boundary
  - finding extraction
  - proposition seeding
  - assessment recompute
  - action proposal refresh
  - replay / idempotency
- 更新相关文档：
  - `docs/analysis/evidence-engine/*`
  - `docs/agent-guide.md`

#### 交付物

- 阶段 4 对应测试集合
- 更新后的 Evidence Engine 文档与 agent guide

#### 验收标准

- 阶段 4 的 canonical pipeline 具备完整回归覆盖
- 文档与实现不再脱节

### Artifact -> Finding Empty Semantics 与迁移细化

#### 摘要

目标态 fact pipeline 固定为以下两条规则：

- `observe` 和 `detect` 可以提交 successful artifact，且其 `finding_count = 0`
- `compare`、`decompose`、`correlate`、`test`、`forecast` 仅在成功产出 canonical finding 时才允许 success；若无法产出任何 canonical item，则应在 validation 或 execution 阶段失败，且不得提交 successful canonical artifact

该约束的设计目的，是让偏采集 / 扫描型 intent 可以表达“已完成执行，但没有发现 canonical 事实”的有效负结果，同时让偏派生 / 判断型 intent 只在真正形成 canonical fact 时才进入成功态。

#### 关键变更

- 在 Evidence Engine 文档中锁定 family-level empty semantics：
  - `observe`：允许 success-empty
  - `detect`：允许 success-empty
  - `compare` / `decompose` / `correlate` / `test` / `forecast`：success 必须 non-empty
- 保留已确认的 finding generation 规则：
  - extractor dispatch 以 `(artifact_type, artifact_schema_version)` 路由
  - `finding_id` 由稳定的 `canonical_item_key` 驱动；有稳定 key 时优先于 index
  - `attribute` 不引入新的 finding family，而是复用 `delta` 与 `decomposition_item`
  - `correlate` 和 `test` 在 v1 保持 `1 artifact -> 1 finding`
- 更新下游语义：
  - 已提交但为空的 finding set 不得 seed proposition
  - `observe` 的 success-empty artifact 属于合法上游结果，但下游 typed intents 不得将其视为“已有可用数据”
  - `compare` / `decompose` / `correlate` / `test` / `forecast` 遇到 empty upstream prerequisite 时，应在 validation 或 execution 阶段拒绝，而不是提交 empty artifact
- 收紧 family contract：
  - `observe`：empty artifact 表示“scope 已解析、执行已完成，但没有 canonical value / bucket / segment / summary item”
  - `detect`：empty artifact 表示“scan 已完成，且 `total_candidate_count = 0`”
  - `compare`：若无法形成任何 `delta` item，则以 `not_comparable` 或等价错误失败，不提交 empty artifact
  - `decompose`：沿用当前规则，无 contribution rows 时失败 / `not_attributable`
  - `correlate`：沿用当前规则，alignment 或 pairs 不足时失败；仅 `aligned` / `needs-attention` 结果提交 1 个 finding
  - `test`：沿用当前规则，无效 test 请求直接失败；`valid` / `needs-attention` 结果提交 1 个 finding
  - `forecast`：沿用当前规则，若无法生成可辩护的 point forecast，则请求失败
- 明确 eventual code path：
  - 在 staged artifact 创建后、commit finalization 前引入 finding extraction seam
  - extractor dispatch 以 `artifact_type` 和 `artifact_schema_version` 为 key
  - 将 family-specific empty / non-empty validation 放入 commit path，而不是使用单一全局规则
  - empty finding set 仅对 `observe` 和 `detect` 合法
  - 不引入 synthetic `no results found` finding

### 交付物

- 新的 evidence schema
- 新的 runtime pipeline
- replay / idempotency 规则对应的实现与测试
- 阶段 4 子任务拆分后的实施计划文档

### 验收标准

- `observe` / `detect` 成功后允许出现 `finding_count = 0` 的 committed artifact
- `compare` / `decompose` / `correlate` / `test` / `forecast` 只有在成功产出至少 1 个 finding 时才允许 commit success artifact
- empty committed finding set 不会 seed proposition；proposition / assessment / action proposal 仍由 finding 驱动
- 新 state/context 读取面不依赖旧 `claims/recommendations` 作为 canonical source

---

## 阶段 5：切换读取面到 Session / State / Context

### 目标

将新的读取面切换为 canonical baseline，替代旧 reflection/evidence summary 的主消费角色。

### 任务

- 重构 `GET /sessions/{session_id}`，仅返回轻量 session root：
  - `goal`
  - `governance`
  - `lifecycle`
  - `state_summary`
- 新增读取面：
  - `GET /sessions/{session_id}/state`
  - `POST /sessions/{session_id}/state/query`
  - `GET /sessions/{session_id}/propositions/{proposition_id}/context`
- 将 `reflection-context` 降级为 legacy compact summary
- 将 `/evidence`、`/debug` 明确为调试接口，不承担 canonical 读面职责

### 交付物

- session/state/context 读取接口
- 对应 view materialization 逻辑
- 更新后的：
  - `docs/api/session-state.md`
  - `docs/api/context-surface.md`

### 验收标准

- 新 client 可以仅依赖 session root + state surface + context surface 消费分析状态
- `reflection-context` 不再承载新 canonical 字段
- state/context 返回结构与 `docs/analysis/evidence-engine/schemas/*` 一致

---

## 阶段 6：清理 Legacy 实现并统一文档

### 目标

在新主链稳定后，删除旧路径残留，避免仓库长期维持双轨状态。

### 任务

- 停止 legacy public step contract 的正式支持
- 停止旧 observation/claim/recommendation 主链写入
- 清理与 `plans`、legacy steps、旧 evidence 主链有关的死代码
- 统一更新：
  - `docs/agent-guide.md`
  - `docs/api/intent-steps.md`
  - `docs/api/session-state.md`
  - `docs/api/context-surface.md`
  - 受影响测试和 API/UI 说明

### 交付物

- 清理后的代码树
- 无双轨歧义的仓库文档

### 验收标准

- 仓库中不存在两套并行的正式分析主路径
- Agent 指南、API 文档、实现、测试四者保持一致

## 7. 推荐实施顺序

建议按以下顺序提交，降低返工和冲突成本：

1. 基线收口与 `plans` 下线
2. 新 intent API model 与 endpoint
3. intent registry 与 derived expansion
4. canonical evidence schema 与 runtime pipeline
5. state/context 读面
6. legacy 清理与文档统一

如需拆分 PR，建议最少拆为以下十二组：

1. `remove-plan-path`
2. `intent-api-surface`
3. `intent-registry-artifact-model` （阶段 3a）
4. `intent-runner-compare` （阶段 3b-1）
5. `intent-runner-decompose` （阶段 3b-2）
6. `intent-runner-correlate` （阶段 3b-3）
7. `intent-runner-detect` （阶段 3b-4）
8. `intent-runner-test` （阶段 3b-5）
9. `intent-runner-forecast` （阶段 3b-6）
10. `intent-derived-attribute` （阶段 3c-1）
11. `intent-derived-diagnose` （阶段 3c-2）
12. `intent-derived-validate` （阶段 3c-3）
13. `canonical-evidence-pipeline`
14. `state-context-surface-and-cleanup`

## 8. 测试计划

### 8.1 API Contract Tests

覆盖以下内容：

- 每个 intent endpoint 的成功路径
- 非法参数组合
- typed ref 结构错误
- cross-session ref 拒绝
- 非 open session 拒绝写入

### 8.2 Derived Expansion Tests

覆盖以下内容：

- `attribute` 固定展开为 `observe + observe + compare + decompose`
- `diagnose` 对 top-K candidates 执行固定 follow-up
- `validate` 按设计文档定义的固定链路展开
- expansion 不依赖 planner 或中间选择

### 8.3 Evidence Pipeline Tests

覆盖以下内容：

- 文档一致性检查：
  - 不再保留“所有 mandatory family success 后都必须 non-empty”的全局表述
  - 所有 family 文档与 runtime pipeline、finding schema 保持一致
- family 行为场景：
  - `observe` 在无 rows / 无 buckets / 无 segments 时，仍可 commit success artifact，且 `finding_count = 0`
  - `detect` 在 `total_candidate_count = 0` 时，仍可 commit success artifact，且 `finding_count = 0`
  - `compare` 遇到 empty 或 insufficient upstream observation 时失败，且不得 commit success artifact
  - `decompose` 在无 contribution rows 时失败，且不得 commit success artifact
  - `correlate` 在有效 pairs 不足时失败；有效结果提交且仅提交 1 个 finding
  - `test` 在输入无效时失败；`valid` / `needs-attention` 结果提交且仅提交 1 个 finding
  - `forecast` 在历史不足或无法产生可辩护 point forecast 时失败；有效结果按 forecast bucket 提交 finding
- identity 与 replay 检查：
  - 当 `artifact_id + finding_type + canonical_item_key` 不变时，replay 保持 `finding_id` 稳定
  - 在存在稳定 item key 时，优先使用 stable key，而非 index-based identity
  - success-empty 的 `observe` / `detect` artifact replay 后仍应为空，不得生成 synthetic finding
- proposition seeding 的稳定性
- assessment snapshot 的 supersede 逻辑
- action proposal 基于 latest assessment 刷新

### 8.4 Read Surface Tests

覆盖以下内容：

- `GET /sessions/{id}` 返回轻量 session root
- `/state` 的过滤、分页与 closure 收缩
- `/context` 的 proposition 最小闭包
- `reflection-context` 仅保留 compact summary 语义

### 8.5 Regression Tests

覆盖以下内容：

- semantic layer 基础能力未退化
- query router 未退化
- compiler/executor 未退化
- governance checks 未退化
- 关键 `observe` path 的执行结果与旧底座一致

## 8.6 关键前提

- 对 `observe` 而言，`no data` 与 `empty population` 有意折叠为同一种 legal success-empty
- empty committed finding set 仅作为 artifact outcome 具有权威性，不作为 proposition seed
- v1 不引入 synthetic negative-result finding family
- `attribute` 继续作为 derived bundle / read object 存在，而不是新的 canonical fact family

## 9. 风险与控制

### 风险 1：新旧 evidence 模型并存时间过长

影响：

- 读面和写面混乱
- 测试和文档持续分叉

控制：

- 在 canonical evidence pipeline 落地后尽快切换读取面
- 不保留旧链路作为正式 canonical path

### 风险 2：derived intent 退化成 planner

影响：

- 系统重新引入复杂 orchestration 语义
- 与新设计偏离

控制：

- 所有 derived intent 必须有固定 expansion contract
- expansion 只能依赖请求和系统状态
- 不支持 patch、approve、re-plan

### 风险 3：执行底座被过度重写

影响：

- 重构周期拉长
- 与目标设计无关的返工增加

控制：

- 优先复用现有 semantic/runtime/compiler/executor
- 只在 public contract 和 canonical evidence 层做结构性重构

### 风险 4：文档和实现再次脱节

影响：

- 设计无法成为执行依据
- 后续演进继续累积偏差

控制：

- 每阶段合并前同步更新 agent guide 与 API 文档
- 使用 contract tests 锁住关键 endpoint 和 read surface 行为

## 10. 完成定义

本次升级在满足以下条件时视为完成：

- `plans` 已从正式架构中移除
- Agent 可以仅通过 session 内 intent step 提交完成分析
- 新 intent API 已替代 legacy public step API
- canonical evidence pipeline 已按 `artifact -> finding -> proposition -> assessment -> action proposal` 运行
- `/sessions/{id}`、`/state`、`/context` 成为新的正式读面
- 旧 public step 和旧 evidence 主链不再是正式路径
- 文档、实现、测试三者一致

## 11. 相关文档

- `docs/agent-guide.md`
- `docs/api/intent-steps.md`
- `docs/api/session-state.md`
- `docs/api/context-surface.md`
- `docs/analysis/README.md`
- `docs/analysis/intents/primitive-intent-design.md`
- `docs/analysis/intents/derived-intent-design.md`
- `docs/analysis/evidence-engine/runtime-pipeline.md`
- `docs/analysis/evidence-engine/read-surfaces.md`
