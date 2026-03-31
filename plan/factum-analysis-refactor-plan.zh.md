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

> **实施说明**：本阶段拆分为三个顺序子任务（3a → 3b → 3c），后两个子任务均依赖 3a 确定的 artifact 持久化模型和 registry 接口。

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

### 阶段 3b：剩余 Atomic Runners（compare / decompose / correlate / test / detect / forecast）

#### 目标

在 3a 确定的 artifact 模型基础上，补全全部 6 个 atomic intent runners，最大化复用现有执行内核。

#### 任务

- `compare`：读取两个 `ObservationRef` artifact，计算 typed delta，产出 compare artifact
- `decompose`：读取 `compare` ArtifactRef，复用 `_run_attribute_change` 内核，切换输入契约
- `correlate`：读取两个 `ObservationRef`，复用 `_run_correlate_metrics` 内核，切换输入契约
- `test`：新 runner；实现有合法 artifact schema 和 finding 格式的最小统计假设检验
- `detect`：新 runner；实现有合法 artifact schema 和 finding 格式的最小异常扫描
- `forecast`：新 runner；实现有合法 artifact schema 和 finding 格式的最小时序预测
- 对所有 runner 统一 lineage / provenance 约束
- 同步更新文档与测试

#### 说明

- `compare`、`decompose`、`correlate` 有现有内核可复用，工作量相对较小
- `test`、`detect`、`forecast` 无现有内核，需从头实现；v1 以满足 canonical artifact schema 和 finding contract 为目标，算法实现可以为最小可用形式

#### 交付物

- 6 个 atomic intent runners（compare / decompose / correlate / test / detect / forecast）
- 统一的 lineage / provenance 约束实现
- 对应单元测试

#### 验收标准

- 全部 7 个 atomic intents（含 3a 的 observe）可独立执行
- 每个 runner 产出符合 artifact schema 的 committed artifact
- `ObservationRef` / `ArtifactRef` 跨 runner 的 ref 解析路径均正确

---

### 阶段 3c：Derived Intent Expansion（attribute / diagnose / validate）

#### 目标

在全部 atomic runners 就绪后，实现 3 个 derived intent 的 deterministic expansion 逻辑。

#### 任务

- `attribute`：展开为 `observe + observe + compare + decompose`
- `diagnose`：展开为 `detect + compare + decompose`（对 top-K candidates 执行）
- `validate`：展开为 `observe + test`
- 确保 expansion 规则满足：
  - 只依赖请求和系统状态
  - 不要求执行中外部补充决策
  - 不生成独立 plan 资源
- 同步更新文档与测试

#### 交付物

- `attribute`、`diagnose`、`validate` 的 expansion 实现
- 端到端 expansion 测试

#### 验收标准

- derived intents 可在单次请求中展开并执行完成
- expansion 不依赖 planner 或中间人工决策
- 展开后的 sub-step artifact ref 链路正确

---

## 阶段 4：重建 Canonical Evidence Pipeline

### 目标

将旧 observation/claim/recommendation 主链替换为新的 canonical evidence pipeline。

### 任务

- 在 metadata schema 中新增 canonical persistence：
  - `findings`
  - `propositions`
  - `assessments`
  - `action_proposals`
  - 必要的 membership/ref 表
- 保留并复用：
  - `sessions`
  - `steps`
  - `artifacts`
- 在 `app/evidence_engine/` 内按目标态重组运行时：
  - artifact materialization
  - deterministic finding extraction
  - proposition seeding / registration
  - assessment recompute
  - action proposal refresh
- 落实 family-level extraction / empty semantics：
  - `observe`：允许 success-empty committed artifact
  - `detect`：允许 success-empty committed artifact
  - `compare`：success 必须至少产出 1 个 finding
  - `decompose`：success 必须至少产出 1 个 finding
  - `correlate`：success 必须至少产出 1 个 finding
  - `test`：success 必须至少产出 1 个 finding
  - `forecast`：success 必须至少产出 1 个 finding
- 明确 commit boundary：
  - staged artifact 创建后，必须先完成 deterministic finding extraction，再进入最终 commit
  - `observe` / `detect` 之外的 family 不允许以 empty finding set 形式成功提交 canonical artifact

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

如需拆分 PR，建议最少拆为以下七组：

1. `remove-plan-path`
2. `intent-api-surface`
3. `intent-registry-artifact-model` （阶段 3a）
4. `intent-atomic-runners` （阶段 3b）
5. `intent-derived-expansion` （阶段 3c）
6. `canonical-evidence-pipeline`
7. `state-context-surface-and-cleanup`

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
