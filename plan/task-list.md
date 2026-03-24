# Factum 改进任务清单

> 从 `improvement-roadmap.md` 生成，按依赖关系和优先级排序。
> 状态标记：`[ ]` 未开始 · `[~]` 进行中 · `[x]` 已完成 · `[!]` 阻塞

---

## Phase 1: P0 — 结构性基础（~6-8 周）✅ 已完成

### Sprint 1（Week 1-2）：控制流边界 + 因果层级字段

#### M-12 Factum-Agent 控制流边界约定

> 前置依赖：无 | 工作量：小 | 影响范围：API 文档 + response 审计

- [x] **M-12.1** 梳理所有 endpoint response，编制「信号 vs 决策」分类表
  - 文件：`app/api/sessions.py`, `app/api/planning.py`, 所有 router 模块
  - 产出：标注文档，区分哪些字段是 Factum 信号（observations、confidence、contradictions）、哪些隐含了 Agent 决策
- [x] **M-12.2** 审计并修正隐含决策逻辑的 response
  - 移除或标记任何 Factum 端"自动决定下一步"的字段
  - 确保 `synthesize_findings` 不自动触发
- [x] **M-12.3** 在 API 文档（或 response schema 注释）中标注信号/决策边界
  - 更新 `app/api/models.py` 中的 Pydantic 模型描述

#### M-02 inference_level 字段（可与 M-12 并行）

> 前置依赖：无 | 工作量：中 | 影响范围：schema、evidence engine、models

- [x] **M-02.1** Claims 表 DDL 变更
  - 文件：`app/storage/schema.py`
  - 增加列：`inference_level TEXT DEFAULT 'L0'`
  - 增加列：`inference_justification_json TEXT`
- [x] **M-02.2** 更新 Claim 数据模型
  - 文件：`app/models.py`
  - Claim 模型增加 `inference_level` 和 `inference_justification` 字段
- [x] **M-02.3** Claim 生成逻辑默认初始化为 L0
  - 文件：`app/evidence_engine/synthesizers/`
  - 所有新建 Claim 默认 `inference_level='L0'`, `inference_justification=[]`
- [x] **M-02.4** API response 暴露 inference_level
  - 文件：`app/api/models.py`, `app/api/sessions.py`
  - 证据图 API 响应中包含 inference_level
- [x] **M-02.5** 单元测试
  - 文件：`tests/test_evidence.py` 或新建 `tests/test_inference_level.py`
  - 验证：DDL 迁移、L0 默认值、API 输出

---

### Sprint 2（Week 2-4）：增量合成重构

#### M-03 增量合成重构

> 前置依赖：M-12 | 工作量：大 | 影响范围：evidence engine 核心逻辑

- [x] **M-03.1** 设计增量合成数据流
  - 输入：新 step 产生的 observations
  - 输出：tentative claims 列表、contradiction 检测结果、confidence 更新
  - 明确与现有 `synthesize_findings` 的关系
- [x] **M-03.2** Claim 表 DDL 增加 `status` 字段
  - 文件：`app/storage/schema.py`
  - 新增列：`status TEXT DEFAULT 'tentative'`（枚举值：`tentative` / `confirmed` / `insufficient`）
- [x] **M-03.3** 实现 IncrementalSynthesizer
  - 新建文件：`app/evidence_engine/incremental_synthesizer.py`
  - 核心逻辑：
    1. Scope 匹配：新 observation 归入已有 claim 的 scope
    2. Tentative claim 创建：无匹配 scope 时新建
    3. 矛盾检测：同 scope 下方向冲突的 observations
    4. Confidence 增量更新
- [x] **M-03.4** Step runner post-execution hook
  - 文件：`app/analysis_core/step_runners/generic.py`, `app/service.py`
  - 每个 primitive step 执行完成后自动调用 `IncrementalSynthesizer.process()`
  - 不影响 step 的 return value（增量合成是 side-effect）
- [x] **M-03.5** 重写 synthesize_findings 为 promotion 操作
  - 文件：`app/analysis_core/step_runners/synthesis.py`
  - 原逻辑：从零开始聚合所有 observations 生成 claims
  - 新逻辑：将 `tentative` claims promote 为 `confirmed`，标记 `insufficient`
- [x] **M-03.6** 迁移测试
  - 确保 `tests/test_evidence.py` 中现有 synthesize_findings 测试通过
  - 新增测试：增量合成流程、tentative→confirmed 提升、矛盾检测
  - 新增测试：多步骤累积后的 claim 状态正确性

---

### Sprint 3（Week 4-6）：Readiness Signal + Extractor Registry

#### M-04 Readiness Signal API

> 前置依赖：M-03 | 工作量：中 | 影响范围：API 层 + evidence engine

- [x] **M-04.1** 实现 Readiness 计算器
  - 新建文件：`app/evidence_engine/readiness.py`
  - 五维信号计算：
    - `goal_coverage`：session goal 被 claims 覆盖的比例
    - `evidence_sufficiency`：每个 claim 的支撑 observation 数量
    - `contradiction_resolution`：未解决矛盾占比
    - `budget_remaining`：session budget 剩余比例
    - `diminishing_returns`：最近 N 步的新 observation 增量趋势
- [x] **M-04.2** 定义 Readiness response 模型
  - 文件：`app/api/models.py`
  - 结构：`ReadinessSignal { goal_coverage, evidence_sufficiency, contradiction_resolution, budget_remaining, diminishing_returns }`
  - `SuggestedAction { action: str, reason: str, priority: float }`
  - 可选 action 类型：`continue_exploring`, `synthesize`, `resolve_contradiction`, `stop`
- [x] **M-04.3** Step response 增加 readiness 字段
  - 文件：`app/service.py`, `app/api/sessions.py`
  - 每次 step 执行后计算 readiness，附加到 response
  - 同时返回当前 session 的 `live_claims`（tentative + confirmed）
- [x] **M-04.4** 单元测试
  - 新建 `tests/test_readiness.py`
  - 场景：空 session、单步后、多步后、矛盾存在时、budget 耗尽时

#### M-01 Extractor 注册表（可与 M-04 并行）

> 前置依赖：无（但 M-03 完成后集成更顺畅） | 工作量：大

- [x] **M-01.1** 设计 ExtractorContract 基类
  - 新建文件：`app/evidence_engine/registry.py`
  - 基类字段：`artifact_type`, `observation_types`, `preconditions`
  - 注册表：`ExtractorRegistry` — 支持注册、查找、按 step type 匹配
- [x] **M-01.2** 迁移现有 Extractor 到 Registry
  - 将 `ComparisonRowExtractor` 和 `AggregateRowExtractor` 注册到 Registry
  - 更新 step runner 中的 extractor 调用：从硬编码改为 registry 查找
- [x] **M-01.3** 实现 FunnelExtractor
  - 新建文件：`app/evidence_engine/extractors/funnel.py`
  - 产出 observation 类型：`funnel_drop`
  - 从漏斗类聚合结果中提取转化率下降点
- [x] **M-01.4** 实现 AnomalyExtractor
  - 新建文件：`app/evidence_engine/extractors/anomaly.py`
  - 产出 observation 类型：`anomaly_detection`
  - 基于统计方法（Z-score / IQR）检测异常值
- [x] **M-01.5** 实现 ContributionShiftExtractor
  - 新建文件：`app/evidence_engine/extractors/contribution_shift.py`
  - 产出 observation 类型：`contribution_shift`
  - 从 AggregateRowExtractor 分化，专注于维度贡献度变化
- [x] **M-01.6** 单元测试
  - 每个新 Extractor 至少 5 个测试用例
  - Registry 查找/匹配测试
  - 集成测试：step 执行 → registry 查找 → extractor 调用 → observation 生成

---

### Sprint 4（Week 6-8）：集成测试 + 文档

- [x] **P1-INT.1** 端到端集成测试：多步分析 session
  - 创建 session → 执行 3+ 步骤 → 验证增量合成 → 检查 readiness → promote
  - 验证 inference_level 默认为 L0
  - 验证 live_claims 正确返回
- [x] **P1-INT.2** API 文档更新
  - 更新 response schema 文档
  - 记录 readiness signal 各维度的含义和取值范围
  - 记录 suggested_actions 的语义
- [x] **P1-INT.3** 回归测试
  - 运行完整测试套件，确保无回归
  - 重点关注：`test_evidence.py`, `test_mvp.py`, `test_semantic_runtime.py`

---

## Phase 2: P1 — 因果推理与可解释性（~6-8 周）

> 前置依赖：Phase 1 全部完成

### Sprint 5（Week 1-3）：Evidence Edge 扩展 + 时序标注

#### M-07 扩展 Evidence Edge 类型

> 前置依赖：M-02 | 工作量：中

- [x] **M-07.1** Edge 类型枚举扩展
  - 文件：`app/storage/schema.py`, `app/evidence_engine/`
  - 基础层保留：`supports`, `contradicts`, `justifies`
  - 新增因果增强层：
    - `correlates_with`（L0/L1）
    - `temporally_precedes`（L2）
    - `mechanistically_explains`（L3）
    - `eliminates_alternative`（L4）
    - `experimentally_confirms`（L5）
- [x] **M-07.2** Synthesizer 自动推断 inference_level
  - 文件：`app/evidence_engine/synthesizers/`
  - 根据 claim 关联的 edge 类型分布，自动设置 inference_level
  - 规则：最高级别 edge 类型决定上限，数量决定置信度
- [x] **M-07.3** 向后兼容测试
  - 确保基础层 edge（supports/contradicts/justifies）的行为不变
  - 新 edge 类型的 CRUD 测试

#### M-08 时序标注

> 前置依赖：M-07 | 工作量：中

- [x] **M-08.1** Observation 表 DDL 变更
  - 文件：`app/storage/schema.py`
  - 增加列：`observed_window_json TEXT`（时间窗口 `{start, end, granularity}`）
  - 增加列：`temporal_order INTEGER`（session 内发现顺序）
- [x] **M-08.2** Step runner 填充时序信息
  - 文件：`app/service.py`（`_annotate_temporal`, `_observation_count`）
  - `compare_metric`：从 `current_start/current_end` 提取 observed_window（granularity=day）
  - `aggregate_query`：compare_period 模式下提取 period window；普通聚合 observed_window=null
  - `temporal_order`：session 内全局自增序号（基于已有 observation count）
- [x] **M-08.3** 时序信息 API 暴露
  - `_load_observations` 包含 `observed_window` 和 `temporal_order`
  - 证据图 response 中自动包含时序标注（ORDER BY temporal_order）
- [x] **M-08.4** 单元测试
  - 文件：`tests/test_temporal_annotation.py`（13 个测试）
  - 验证 DDL 列存在、temporal_order 递增、compare_metric window 正确、aggregate 无 window、证据图包含时序字段

---

### Sprint 6（Week 3-6）：因果检验器

#### M-09 确定性因果检验器

> 前置依赖：M-07 + M-08 | 工作量：大

- [x] **M-09.1** 因果检验器框架
  - 新建文件：`app/evidence_engine/causal_checkers.py`
  - 基类：`CausalChecker { check(claims, observations, edges) → LevelUpgrade[] }`
  - 注册机制：按检验类型注册，按优先级执行
- [x] **M-09.2** CrossSliceConsistencyChecker（L0→L1）
  - 逻辑：将 observations 按 dimension 分组，统计效应方向一致率
  - 阈值：一致率 > 80% → 升级为 L1
  - 产出：`inference_justification` 记录分组详情
- [x] **M-09.3** TemporalPrecedenceChecker（L1→L2）
  - 逻辑：比较关联 observations 的 `observed_window`，检测时间先后和 lag 一致性
  - 条件：原因事件持续早于结果事件 → 升级为 L2
- [x] **M-09.4** DoseResponseChecker（L1 bonus）
  - 逻辑：对维度值和指标变化计算 Spearman 相关系数
  - 条件：|ρ| > 0.7 → 增加 `dose_response` justification
- [x] **M-09.5** ReversalChecker（L2 bonus）
  - 逻辑：检测干预前后指标方向反转
  - 条件：反转持续 ≥ 2 个周期 → 增加 `reversal` justification
- [x] **M-09.6** 集成到增量合成流程
  - 文件：`app/evidence_engine/incremental_synthesizer.py`
  - 每次增量合成后运行因果检验器链，自动升级 inference_level
- [x] **M-09.7** 全面单元测试
  - 每个 checker 至少 5 个测试：正例、负例、边界条件
  - 集成测试：多步骤 → 增量合成 → 因果检验 → level 升级

---

### Sprint 7（Week 4-6，与 Sprint 6 并行）

#### M-06 Synthesizer 拆分

> 前置依赖：M-03 | 工作量：中 | 可独立推进

- [x] **M-06.1** 拆分为三阶段
  - 文件：`app/evidence_engine/synthesizers/`
  - Stage 1：`ScopeClusterer` — 按 metric/entity/dimension 聚类 observations
  - Stage 2：`SignalAligner` — 对齐同 scope 内的信号方向和强度
  - Stage 3：`ClaimFormulator` — 从对齐结果生成 claim 文本和 confidence
- [x] **M-06.2** 审计日志
  - 每阶段输出结构化日志：`scope_clusters`, `alignment_scores`, `formulation_decisions`
  - 日志持久化到 step artifacts（JSON 格式）
- [x] **M-06.3** 测试
  - 三阶段独立测试 + 管线集成测试
  - 审计日志格式和内容验证

#### M-05 置信度校准（并行启动，持续迭代）

> 前置依赖：M-02 | 工作量：中（基础设施）

- [ ] **M-05.1** Confidence 对象结构变更
  - 文件：`app/evidence_engine/scoring.py`, `app/models.py`
  - 从标量 `float` 改为 `{ raw_score: float, calibrated_confidence: float | null }`
  - 向后兼容：API 默认返回 `raw_score`，`calibrated_confidence` 初始为 null
- [ ] **M-05.2** 校准基础设施
  - 新建文件：`app/evidence_engine/calibration.py`
  - 预留 isotonic regression / 分箱映射接口
  - 收集人类判断的 API endpoint（可选，低优先级）
- [ ] **M-05.3** 测试
  - Confidence 结构变更的向后兼容测试
  - 校准接口的 stub 测试

---

### Sprint 8（Week 6-8）：Phase 2 集成

- [x] **P2-INT.1** 因果推理端到端测试
  - 场景：多步分析 → 跨切片一致性 → L0→L1 升级 → 时序检验 → L1→L2 升级
  - 验证 evidence edge 类型正确分配
  - 验证 synthesizer 审计日志完整
  - 文件：`tests/test_phase2_integration.py`（13 个测试：3 直接注入 + 5 HTTP + 5 schema 常量）
- [x] **P2-INT.2** Confidence 校准 baseline 收集方案文档化
  - 文件：`docs/confidence_calibration.md`
- [x] **P2-INT.3** 回归测试
  - 全套 636 tests，0 failures（skipped=7）

---

## Phase 3: P2 — 工程优化与 Reflection Loop（~4-6 周）

> 前置依赖：Phase 2 全部完成

### Sprint 9（Week 1-2）

#### M-10 Recommendation 因果标签

> 前置依赖：M-02 + M-09 | 工作量：小

- [x] **M-10.1** DDL 变更
  - 文件：`app/storage/schema.py`
  - recommendations 表增加列：`causal_basis_json TEXT`
- [x] **M-10.2** Recommendation 生成逻辑
  - 文件：`app/evidence_engine/schemas.py` (`_build_causal_basis()`), `app/evidence_engine/pipeline.py`
  - 在 M-07 因果升级后 attach causal_basis，确保使用最终 inference_level
  - 包含：`inference_level`, `strongest_evidence_summary`, `unresolved_confounders`, `suggested_validation`
- [x] **M-10.3** API 暴露 + 测试
  - `app/service.py`：`_insert_recommendation()` + `get_evidence_graph()` 更新
  - `tests/test_evidence.py`：`CausalBasisTests`（9 个测试：4 单元 + 2 管线 + 3 集成）

---

### Sprint 10（Week 2-6）

#### M-11 Reflection Context API

> 前置依赖：Phase 2 完成 | 工作量：中
>
> **设计原则**：Factum 负责确定性地产出结构化证据缺口摘要（facts by code），LLM 推断和"下一步做什么"的判断由调用方 agent 完成（language by model）。Factum 不内嵌 LLM backend。

- [x] **M-11.1** Reflection Context 端点
  - 新建文件：`app/reflection/context.py`
  - 实现 `build_reflection_context(session_id, plan_id)` → 结构化摘要，包含：
    - `readiness_signal` + `readiness_score`
    - `tentative_claims`：inference_level < L2 的 claims，附 `unresolved_confounders`
    - `evidence_gaps`：有 `suggested_validation` 但尚未覆盖的维度
    - `available_step_types`：当前 session 可用的 step type 列表（供 agent 选择）
  - 格式要求：紧凑 JSON，token 友好，无冗余字段
  - 新增 API 端点：`GET /sessions/{session_id}/reflection-context`
- [x] **M-11.2** Plan Patch 端点
  - 文件：`app/api/planning.py`、`app/planning.py`
  - 新增端点：`POST /sessions/{session_id}/plans/{plan_id}/patch`
  - 接受 agent 提交的 patch：新增 steps、修改现有 step params、标记 step 跳过
  - 验证 patch 合法性（step type 存在、params 符合 schema、依赖关系无环）
  - patch 走现有 planning 审批流程（auto-approve 或人工审批，与现有逻辑一致）
- [x] **M-11.3** ReplanningService 精简
  - 文件：`app/planner/replanning.py`
  - 新增 `apply_patch(plan_id, patch, planning_service) → updated_plan` 方法
  - 注意：decide_* 方法保留（WorkflowOrchestrator 依赖）
- [x] **M-11.4** 配置和开关
  - 文件：`app/config.py`
  - 新增 `reflection` 配置段：`enabled`（控制 reflection-context 端点是否开放）
- [x] **M-11.5** 端到端测试
  - 文件：`tests/test_reflection.py`（16 个测试，663 total, 0 failures）
  - 测试 `GET /sessions/{id}/reflection-context` 返回格式正确
  - 测试 `POST /plans/{id}/patch` 合法 patch 通过、非法 patch 拒绝
  - 测试 patch 后 plan 走审批流程的行为
  - 无需 mock LLM；全链路纯确定性

---

## 持续 / 未来工作（未排期）

| 编号 | 项目 | 状态 | 前置依赖 | 备注 |
|------|------|------|----------|------|
| F-01 | Auth / RBAC | 未开始 | 无 | 生产部署前必须完成 |
| F-02 | 生产级 job queue | 未开始 | 无 | 替换当前 `threading` + sync 降级 |
| F-03 | Streaming step execution | 未开始 | 无 | 大表场景需要 |
| F-04 | Lineage graph | 未开始 | Phase 1 | 已有 provenance 基础 |
| F-05 | Snowflake adapter | 未开始 | 无 | 优先级低于证据层深化 |

---

## 依赖关系图

```
M-12 ──→ M-03 ──→ M-04
              ╰──→ M-06 (P1)
M-02 ──→ M-07 ──→ M-08 ──→ M-09
     ╰──→ M-05 (P1, 并行)
     ╰──→ M-10 (P2, 需 M-09)
M-01 (独立，但集成依赖 M-03)
M-11 (P2, 依赖 Phase 2 全部)
```

## 验收检查点

### Phase 1 Done 定义
- [x] 每个 step response 包含 `live_claims` 和 `readiness` 字段
- [x] `synthesize_findings` 表现为 promotion 操作（tentative → confirmed）
- [x] 每个 Claim 包含 `inference_level` 和 `inference_justification`
- [x] Extractor Registry 至少注册 4 种 extractor
- [x] 所有现有测试通过 + 新增测试覆盖率 > 80%

### Phase 2 Done 定义
- [x] Evidence edge 支持因果增强类型（5 种新 edge）
- [x] Observation 包含时序标注（observed_window + temporal_order）
- [x] L0→L1→L2 升级路径有确定性检验器支持
- [x] Synthesizer 拆分为三阶段并产出审计日志
- [ ] Confidence 对象暴露 `{ raw_score, calibrated_confidence }`（M-05 暂缓）

### Phase 3 Done 定义
- [x] Recommendation 包含 `causal_basis`
- [x] `GET /sessions/{id}/reflection-context` 返回结构化证据缺口摘要（readiness、tentative claims、evidence gaps）
- [x] `POST /plans/{id}/patch` 接受 agent 提交的 plan patch，验证合法性后走审批流程执行
