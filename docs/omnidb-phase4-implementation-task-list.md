# OmniDB Phase 4 实施任务清单

## 1. 文档目的

本文档把 `docs/omnidb-phase4-design-doc.md` 继续拆解为可执行任务包。Phase 4 的核心目标是把 semantic runtime 做成真正可执行的语义层，并补齐 request / execution-plan IR，让 validation、governance、costing、routing、evidence scoring 更彻底地围绕统一 IR 工作。

## 2. 实施原则

- 先补 shared contract，再迁消费方
- 先补 semantic metadata，再让 validator / compiler / planner context 消费
- 先包装现有 scoring，再逐步引入 domain-specific strategy
- 每个任务包完成后都需要：
  - 保持兼容入口
  - 增加相应模块级测试
  - 更新文档 / plan / checkpoint

## 3. 任务包总览

| Task | 目标 | 依赖 |
|---|---|---|
| P4-1 | Phase 4 设计文档与执行清单 | 无 |
| P4-2 | 增强 semantic object 执行语义 | P4-1 |
| P4-3 | 补齐 AnalysisRequest / ExecutionPlanIR | P4-1, P4-2 |
| P4-4 | 让 validator / governance / costing 更彻底消费 IR | P4-2, P4-3 |
| P4-5 | 引入 confidence scorer / recommendation policy seam | P4-3 |
| P4-6 | 收口 semantic-runtime / persistence glue | P4-4, P4-5 |

---

## 4. 任务包明细

### P4-1：设计文档与执行基线

#### 目标

固定 Phase 4 的问题定义、模块边界、迁移顺序与任务依赖，并把 session 执行状态切换到 Phase 4 / 5 规划阶段。

#### 交付物

- `docs/omnidb-phase4-design-doc.md`
- `docs/omnidb-phase4-implementation-task-list.md`
- `docs/omnidb-phase5-design-doc.md`
- `docs/omnidb-phase5-implementation-task-list.md`

#### 完成标准

- Phase 4 / 5 边界清楚
- Phase 4 内部任务顺序明确
- 后续实现可直接按任务包推进

---

### P4-2：增强 semantic object 执行语义

#### 目标

让 metric / entity / dimension 元数据不再只是“可查询字段”，而是 planner / compiler / validator 能直接消费的执行语义。

#### 拟改动模块

- `app/storage/schema.py`
- `app/semantic.py`
- `app/semantic_runtime/semantic_metadata.py`
- `app/semantic_runtime/resolution.py`
- `app/semantic_runtime/planner_context.py`
- `app/semantic_runtime/catalog.py`
- `app/models.py` 或相关语义输入模型

#### 建议动作

- 为 metric 增加 `grain` / `measure_type` / `allowed_dimensions`
- 为 metric / entity 增加 `lineage` / `quality_expectations`
- 为 resolution 结果增加 dimension compatibility / legal grain 信息
- 让 planner context 暴露这些结构化字段

#### 完成标准

- semantic runtime 可返回结构化执行语义
- 至少一个 validator / compiler / planner-context 消费新字段
- 现有 semantic CRUD 兼容不变

#### 已完成实现

- 为 `semantic_entities` / `semantic_metrics` 增加一等执行语义列，而不是继续复用 `properties_json`
- 为 entity 增加 `level` / `join_constraints` / `upstream_dependencies` / `lineage` / `quality_expectations`
- 为 metric 增加 `grain` / `measure_type` / `allowed_dimensions` / `lineage` / `quality_expectations`
- semantic CRUD、resolver、planner context、catalog resolve 已全部读写这些字段
- 已补充 `tests/test_semantic.py`、`tests/test_semantic_runtime.py`、`tests/test_catalog_query.py` 回归覆盖

---

### P4-3：补齐 AnalysisRequest / ExecutionPlanIR

#### 目标

让 analysis core 从 step-only IR 扩展为 request / step / execution-plan 三层合同。

#### 拟改动模块

- `app/analysis_core/ir.py`
- `app/planning.py` 或 `app/planner/`
- `app/execution/`
- 相关测试

#### 建议动作

- 定义 `AnalysisRequest`
- 定义 `ExecutionPlanIR`
- 明确 semantic resolution / policy transforms / target engine 在 IR 中的落点
- 增加 legacy request / step -> 新 IR 的兼容适配

#### 完成标准

- request / step / execution-plan IR 三层至少有 v1 落地
- 旧入口仍可适配到新 IR
- 新 IR 有直接单测

#### 已完成实现

- 扩展 `AnalysisRequest`，显式承载 `plan_id`、`requested_step_types`、`requested_metrics`、`requested_tables`
- 扩展 `ExecutionPlanIR`，显式承载：
  - plan/session/status
  - request-level IR
  - per-step `semantic_resolutions`
  - per-step `execution_targets`
  - request-derived `policy_transforms`
- 新增 legacy session/step -> richer IR 的适配器 `request_from_legacy_session(...)`
- `PlanningService.get_execution_plan_ir()` 已不再只返回 step list，而是返回真正 enriched 的 execution-plan IR
- semantic resolution / target engine / policy transform 的 v1 落点已经进入 plan IR
- 已补充 `tests/test_analysis_ir.py` 与 `tests/test_planning.py` 直接覆盖

---

### P4-4：让 validator / governance / costing 更彻底消费 IR

#### 目标

减少这些子系统对 legacy `step_type + params` 的直接解析，让它们更多地消费 IR 合同与结构化 semantic resolution。

#### 拟改动模块

- `app/planner/validation.py`
- `app/governance.py` 或后续 policy application seam
- `app/execution/costing.py`
- `app/execution/routing_runtime.py`

#### 建议动作

- validator 直接校验 semantic compatibility / grain / dimensions
- governance 使用 IR 上的 semantic target / policy context
- cost model 使用 execution intent / engine hints
- routing runtime 预留 execution-plan 输入

#### 完成标准

- validation / governance / costing 的主要输入显式依赖 IR
- 错误信息更多体现语义原因，而不是参数缺失
- 兼容回归通过

#### 已完成实现

- `PlanningService` 的 validate / estimate-cost / budget-check / execute-plan 主路径现在复用 enriched `ExecutionPlanIR`
- semantic 校验不再重新查 resolver，而是消费 `SemanticResolutionIR`
- governance 校验不再重新拼 session/table 上下文，而是消费 request IR + execution target
- routing 校验不再重新 resolve route，而是消费 `ExecutionTargetIR` 中的 routing result / fallback reason
- cost model 现在可直接消费 `ExecutionTargetIR`，不必重复解析 routing
- 新增 unsupported dimension 回归，确保 semantic validation 走 IR 主链

---

### P4-5：引入 confidence scorer / recommendation policy seam

#### 目标

在 extractor / synthesizer 之外补齐 evidence scoring 与 recommendation derivation 的正式扩展位。

#### 拟改动模块

- `app/evidence_engine/scoring.py`
- `app/evidence_engine/pipeline.py`
- 新增 `app/evidence_engine/recommendation_policy.py`（必要时）
- `app/evidence.py`

#### 建议动作

- 定义 `ConfidenceScorer`
- 定义 `RecommendationPolicy`
- 保留当前默认算法，外包成默认实现
- 支持按 metric / domain / artifact type 注册 scorer / policy

#### 完成标准

- 置信度与 recommendation 策略不再是固定内嵌实现
- 默认输出保持兼容
- scoring / policy 有独立单测

#### 已完成实现

- `app/evidence_engine/scoring.py` 现在除了保留 `score_confidence(...)` 公式，还提供正式的 `ConfidenceScorer` 合同与默认实现 `DefaultConfidenceScorer`
- `app/evidence_engine/recommendation_policy.py` 新增 `RecommendationPolicy` 合同与默认实现 `DefaultRecommendationPolicy`
- `EvidencePipeline` 现在在 synthesizer 之后显式执行：
  - claim scoring
  - recommendation derivation
  并支持按名称切换 scorer / policy
- `synthesize_claims()` 不再内嵌 recommendation 构造逻辑，只负责产出 claims；默认 recommendation 政策由 pipeline 后置应用
- 默认 counter-hypothesis / root-cause confidence 计算逻辑已迁到默认 scorer 中，保持原有输出语义
- 已补充 `tests/test_evidence_plugins.py` 的 scorer / policy seam 回归，并保持 `tests/test_evidence.py` / service integration 通过

---

### P4-6：收口 semantic-runtime / persistence glue

#### 目标

在不大规模重构 storage 的前提下，减少新 semantic / IR 消费方继续直接散写 persistence glue。

#### 拟改动模块

- `app/service.py`
- `app/semantic_runtime/`
- `app/storage/`

#### 建议动作

- 为 semantic runtime 新 consumers 提供更稳定 helper / repository seam
- 避免新 validator / planner / evidence scoring 继续直接依赖 facade 私有 helper
- 为后续 repository boundary 做准备

#### 完成标准

- Phase 4 新能力主要通过 runtime seam 接线，而不是继续加大 `service.py`
- 后续 Phase 5 的 boundary refactor 有更清晰落点

## 5. 推荐执行顺序

建议按以下顺序推进：

1. P4-1 设计与执行基线
2. P4-2 semantic object 执行语义
3. P4-3 request / execution-plan IR
4. P4-4 validator / governance / costing 接 IR
5. P4-5 confidence scorer / recommendation policy
6. P4-6 semantic-runtime / persistence glue 收口

这个顺序的核心逻辑是：

**先补 semantic 与 IR 共同契约，再迁控制链，最后收口新的 runtime glue。**

## 6. 当前实施状态

- [done] P4-1 设计与执行基线
- [done] P4-2 增强 semantic object 执行语义
- [done] P4-3 补齐 AnalysisRequest / ExecutionPlanIR
- [done] P4-4 让 validator / governance / costing 更彻底消费 IR
- [done] P4-5 引入 confidence scorer / recommendation policy seam
- [pending] P4-6 收口 semantic-runtime / persistence glue
