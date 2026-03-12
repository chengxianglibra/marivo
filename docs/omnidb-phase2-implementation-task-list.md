# OmniDB 第二阶段实际改造任务列表

## 1. 文档目的

本文档将 `docs/omnidb-improvement-roadmap.md` 中的 **Phase B：打通 planning / governance / execution 主链**，进一步拆解为一份可以直接执行的**第二阶段实际改造任务列表**。

这里的“第二阶段实际”指：

- 以前一阶段已经完成的 `analysis_core` / `evidence_engine` / `semantic_runtime` 初始接缝为起点
- 以可审查、可回归的任务包为粒度
- 每项任务都明确目标模块、核心动作、依赖顺序、验收标准与测试影响

本文档默认服务于第二轮运行时强化工作，不覆盖第三阶段的多引擎平台化、协议层大规模拆包和生产级异步基础设施。

## 2. 第二阶段范围

### 2.1 第二阶段目标

第二阶段只做一件事：

**把 OmniDB 从“已具备模块边界的分析框架”推进为“规划驱动、治理可介入、具备反馈闭环的分析运行时”。**

### 2.2 第二阶段包含

- 强化 plan validation，从参数检查升级为语义、治理、预算与执行可行性检查
- 为 governance 建立可进入编译与结果 shaping 主链的 decision / application pipeline
- 建立 v1 cost model，并将其接入 planning validation 与执行降级判断
- 引入最小 re-planning 闭环，支持证据不足、冲突、超预算、引擎不可用等反馈驱动调整
- 补 execution fallback / partial failure 语义，让 planner、executor、routing 可以共享反馈
- 保持现有 HTTP、MCP、UI 兼容，并增加更贴近 runtime 的模块级测试

### 2.3 第二阶段不包含

- 完整多引擎 capability matrix 与联邦执行
- 完整 API / MCP 目录重组
- 全量 async worker / queue 生产化
- UI 的大规模产品重做
- 完整 LLM planner/controller 接管

## 3. 第二阶段完成定义

第二阶段完成时，应满足以下条件：

- `PlanningService` 不再只做“格式校验 + 顺序执行”，而能给出结构化 validation 结论
- governance 不再只是 pre-check，而能产出可进入编译和结果 shaping 的 policy application
- 存在独立 cost model，可被 validation、executor、re-planning 共同消费
- 执行反馈可反向驱动下一步动作，而不只是记录 step status
- 旧公开入口仍然可用：
  - `app.main.create_app`
  - `app.service.SemanticLayerService`
  - `app.mcp_server.main`
- 现有核心测试继续通过，并新增覆盖：
  - planning validation
  - governance application
  - cost estimation
  - re-planning / execution fallback

## 4. 执行顺序总览

建议按下面顺序推进：

1. 任务包 0：第二阶段基线与 planner/gov/execution 现状清点
2. 任务包 1：增强 plan validation
3. 任务包 2：建立 governance decision / application pipeline
4. 任务包 3：接入 v1 cost model 与预算校验
5. 任务包 4：建立反馈驱动的 re-planning
6. 任务包 5：打通 execution fallback / failure semantics
7. 任务包 6：兼容层、测试与文档收尾

这个顺序的核心逻辑是：

**先让计划“可判定”，再让治理和成本进入主链，最后才让运行时根据反馈重规划。**

## 5. 任务包清单

---

## 任务包 0：第二阶段基线与现状清点

### 目标

在正式进入第二阶段前，先把当前 `planning.py`、`governance.py`、`routing.py`、`service.py` 中与主链相关的现状、边界和兼容约束稳定下来，避免后续一边引入新能力一边重复返工。

### 目标文件

- `app/planning.py`
- `app/governance.py`
- `app/service.py`
- `app/routing.py`
- `docs/omnidb-phase2-implementation-task-list.md`

### 具体动作

- [x] 梳理现有 validation 行为：参数、step type、plan persistence、execution 约束
- [x] 梳理 governance 当前能力：policy CRUD、pre-check、quality rule、approval hook
- [x] 梳理 routing / executor 当前失败语义：无路由、引擎不可用、翻译失败、执行失败
- [x] 明确第二阶段不会破坏的对外返回结构和错误语义
- [x] 为后续任务包列出共享术语：validation issue、policy decision、estimated cost、replan trigger、execution feedback

### 基线结论（已完成）

- `PlanningService.validate_plan()` 的对外兼容键仍保持为 `valid` / `errors`，但内部已落地结构化 `issues`，后续任务包 1 可以直接在此基础上扩展 semantic / budget / routing / governance 校验。
- `GovernanceService.check_policies()` / `check_step()` 的对外兼容键仍保持为 `passed` / `violations` / `warnings`，同时新增 `decisions` 作为结构化 policy decision 接缝。
- `QueryRouter.resolve_tables()` 当前失败语义仍是：
  - 表不存在时抛 `KeyError`
  - source 无 binding 或无公共 engine 时抛 `ValueError`
  - `SemanticLayerService._resolve_engine()` 会把这两类错误视为“可降级”，回退到默认 analytics engine
- `PlanningService.execute_plan()` 当前失败语义仍是：
  - 计划与当前 step 会被标记为 `failed`
  - 原始异常继续向上抛出，由 HTTP 层映射为现有 4xx/5xx
- 第二阶段共享术语已集中到 `app/runtime_contracts.py`：
  - `PlanValidationIssue`
  - `PlanValidationResult`
  - `PolicyDecision`
  - `PolicyApplicationResult`
  - `CostEstimate`
  - `ExecutionFeedback`
  - `ReplanTrigger`
  - `ReplanDecision`

### 验收标准

- 第二阶段所有任务包共享一套术语和兼容约束
- 后续子任务不需要反复回头定义 validation / policy / cost / feedback 的基本对象

### 测试范围

- 无新增行为时只需 smoke 检查现有核心测试

### 依赖

- Phase 1 已完成

---

## 任务包 1：增强 Plan Validation

### 目标

把 `PlanningService` 的 validation 从“格式正确”升级为“语义可执行、治理可接受、预算可解释”的结构化验证。

### 迁移来源

- `app/planning.py`
- `app/semantic_runtime/`
- `app/analysis_core/ir.py`

### 目标文件

- `app/planner/validator.py` 或 `app/planning.py` 内部先引入 validator seam
- `app/planner/validation_models.py`
- `app/planner/context.py`

### 具体动作

- [x] 定义 `PlanValidationIssue`、`PlanValidationResult`
- [ ] 校验 metric / entity / dimension 是否存在且已发布
- [ ] 校验 metric 与 dimensions / grain / filters 是否兼容
- [ ] 校验 step 依赖是否闭合、输入输出是否匹配
- [x] 校验 execution intent 是否被现有 compiler / executor 支持
- [x] 接入 governance / cost / routing 预检查入口，形成统一 validation pipeline
- [x] 为失败返回增加 machine-readable issue code，而不只是错误字符串

### 兼容要求

- 现有 plan CRUD API 保持不变
- 旧接口可以继续返回简单错误，但内部要先形成结构化 validation 结果

### 验收标准

- validation 失败时能明确区分 semantic、governance、budget、routing、dependency 等原因
- planner / executor 可共享同一份 validation 结果结构

### 测试范围

- `tests/test_planning.py`
- 新增 `tests/test_plan_validation.py`
- `tests/test_mvp.py`

### 依赖

- 任务包 0

### 当前实现（本轮已完成）

- `PlanningService` 已支持可注入的 `analytics / query_router / governance / semantic_resolver` 依赖，validation 不再局限于纯格式检查。
- `validate_plan()` 现同时返回：
  - 兼容旧入口的 `valid` / `errors`
  - 新增 machine-readable `issues`
  - 新增 `cost_estimates`
- 已接入的 validation 子项：
  - semantic metric 发布态检查（`compare_metric`）
  - dependency / required params 检查
  - governance pre-check
  - routing pre-check（当前以 warning 形式表达 default analytics fallback）
  - budget pre-check（基于行数估算）
- 新增直接测试：
  - `tests/test_plan_validation.py`
  - `tests/test_planning.py` 扩展结构化 issue 断言

---

## 任务包 2：建立 Governance Decision / Application Pipeline

### 目标

让 governance 从“检查器”演进为“可进入编译和结果 shaping 主链的治理引擎”。

### 迁移来源

- `app/governance.py`
- `app/approvals.py`
- `app/semantic_runtime/`

### 目标文件

- `app/governance_engine/policy_service.py`
- `app/governance_engine/policy_application.py`
- `app/governance_engine/quality_service.py`
- `app/governance_engine/models.py`

### 具体动作

- [x] 定义 `PolicyDecision`、`PolicyApplicationResult`
- [ ] 将 policy CRUD 与 enforcement / application 逻辑分离
- [ ] 在 semantic resolution 阶段引入 access boundary / policy tag 解析
- [ ] 在 compile 阶段支持 row filter / limit / masking / aggregate-only transform
- [ ] 在 result shaping 阶段执行 aggregate-only、sensitive field stripping 等约束
- [x] 在 provenance 中持久化 governance decisions
- [x] 与 approval flow 保持兼容，区分 hard constraint / soft signal

### 兼容要求

- 现有 `GovernanceService.check_step()` 保持可用
- HTTP / UI 的 policies、quality rules、approvals 入口短期保持稳定

### 验收标准

- governance 不再只返回 `passed/violations`
- compiler / executor / result shaping 均可消费 policy application 结果

### 测试范围

- `tests/test_governance.py`
- `tests/test_approvals.py`
- 新增 `tests/test_policy_application.py`
- `tests/test_mvp.py`

### 依赖

- 任务包 1

### 当前实现（本轮已完成）

- `GovernanceService.check_policies()` 已从简单 blocker 列表扩展为结构化 policy application 输出，当前返回：
  - `decisions`
  - `transforms`
  - `hard_constraints`
  - `soft_signals`
- 当前已落地的 application transforms：
  - `aggregate_only`
  - `masked_fields`
  - `row_filters`
  - `max_rows_scanned`
- `SemanticLayerService.run_step()` 现会：
  - 在执行前生成 governance application 结果
  - 在返回 payload 中附带 `governance`
  - 在 step provenance 中持久化 governance decisions / transforms / hard/soft 信号
- `run_watch_time_drop_workflow()` 已切回统一走 `run_step()`，让 workflow step 与单步执行共享同一 governance pipeline。
- 新增直接测试：
  - `tests/test_policy_application.py`
  - `tests/test_governance.py` 扩展 policy transform 断言

---

## 任务包 3：接入 v1 Cost Model 与预算校验

### 目标

建立最小但真实可用的 cost model，让预算检查不再只是占位字段。

### 迁移来源

- `app/planning.py`
- `app/routing.py`
- `app/storage/analytics.py`

### 目标文件

- `app/execution/costing.py`
- `app/execution/capability.py`（如需要）
- `app/planner/budgeting.py`

### 具体动作

- [x] 定义 `CostEstimate`、`BudgetCheckResult`
- [x] 估算 rows / bytes scanned、engine locality、join fanout risk、cache/materialization 信号
- [x] 基于 semantic resolution 与 compiled step 生成 cost hint
- [x] 在 plan validation 阶段接入预算预估
- [x] 在执行前记录 estimated cost，在执行后记录 actual-ish feedback（哪怕初版仍较粗）
- [x] 为后续 re-planning 提供 degrade / fallback 建议

### 兼容要求

- 不要求第二阶段就做高精度成本模型
- 无法估计时必须显式返回 low-confidence / unknown，而不是伪精确数字

### 验收标准

- validation 可以区分“预算明确超限”和“预算未知但风险高”
- executor / re-planner 可消费 cost estimate 作为决策输入

### 测试范围

- 新增 `tests/test_costing.py`
- `tests/test_planning.py`
- `tests/test_bindings.py`

### 当前实现（本轮已完成）

- 新增 `app/execution/costing.py`，引入独立 `CostModel`，把成本估算逻辑从 `PlanningService` 中抽离。
- `app/runtime_contracts.py` 中的 `CostEstimate` 已扩展为可复用运行时 contract，当前包含：
  - `estimated_rows`
  - `estimated_bytes`
  - `confidence`
  - `engine_id`
  - `engine_locality`
  - `join_fanout_risk`
  - `cache_signals`
  - `suggested_fallbacks`
- 新增 `BudgetCheckResult`，当前会返回：
  - `total_estimated_rows`
  - `total_estimated_bytes`
  - `within_budget`
  - `confidence`
  - `risk_level`
  - `unknown_subjects`
  - `suggested_fallbacks`
- `CostModel` 当前基于：
  - 默认 step → table 映射
  - `QueryRouter` 的 bound-route / fallback 信号
  - `AnalyticsEngine.table_row_count()` 的行数估算
  - step-type bytes-per-row 启发式
  来生成 v1 成本估算。
- `synthesize_findings` 这类纯 artifact step 现会明确返回 `artifact_only` / `no_scan`，避免把无扫描步骤伪装成未知成本。
- `PlanningService` 现已统一复用 `CostModel`：
  - `validate_plan()` 返回 `cost_estimates`
  - `estimate_costs()` 持久化 `estimated_cost_detail`
  - `check_budget()` 复用结构化 estimate，并输出 risk/confidence
  - `execute_plan()` 在 step 上记录 `estimated_cost_detail` 与 `actual_cost_feedback`
- 新增直接测试 `tests/test_costing.py`，并扩展 `tests/test_planning.py` 以覆盖：
  - route/locality hints
  - cache / fallback signals
  - budget unknown-risk 行为
  - execute-plan 的 actual feedback 持久化
- `tests/test_mvp.py`

### 依赖

- 任务包 1
- 任务包 2（如果 policy 会影响 estimated cost）

---

## 任务包 4：建立反馈驱动的 Re-Planning

### 目标

让计划具备最小闭环能力：不是执行完一个固定脚本，而是能根据证据、成本和错误反馈调整下一步。

### 迁移来源

- `app/planning.py`
- `app/service.py`
- `app/evidence_engine/`

### 目标文件

- `app/planner/replanning.py`
- `app/planner/feedback.py`
- `app/session/workflow_runtime.py`（若继续抽 runtime）

### 具体动作

- [x] 定义 `ExecutionFeedback`、`ReplanTrigger`、`ReplanDecision`
- [x] 识别证据不足、冲突证据、预算/路由风险、引擎不可用、compile failure 等 trigger
- [x] 为现有 watch-time workflow 增加最小 re-planning hook，而不是一次性固定步骤
- [x] 支持插入补充步骤、替换降级步骤、跳过高风险步骤
- [x] 保留 deterministic rule-based re-planning，暂不引入复杂 LLM planner loop
- [x] 记录 re-plan provenance，便于 UI / MCP 返回解释

### 兼容要求

- 旧 workflow API 保持可用
- 默认路径仍按固定 workflow 模板启动；只有命中 deterministic trigger 时才会插入 / 替换 / 跳过步骤

### 验收标准

- 至少一个真实 workflow 能在遇到反馈后调整后续步骤
- 重规划不是重跑全部，而是对现有 plan 做局部修正

### 测试范围

- 新增 `tests/test_replanning.py`
- `tests/test_planning.py`
- `tests/test_mvp.py`

### 依赖

- 任务包 1
- 任务包 2
- 任务包 3

### 当前实现（本轮已完成）

- 新增 `app/planner/replanning.py` 与 `app/planner/__init__.py`，引入最小规则式 `ReplanningService`。
- `ReplanningService` 当前复用 `CostModel` 并提供：
  - `estimate_step()`
  - `build_feedback()`
  - `decide_before_step()`
  - `decide_after_step()`
  - `decide_on_error()`
- 当前已落地的 trigger 类型：
  - `insufficient_evidence`
  - `conflicting_evidence`
  - `budget_or_routing_risk`
  - `engine_unavailable`
  - `compile_failure`
  - `step_execution_failed`
- 当前支持的局部重规划动作：
  - `insert_steps`
  - `replace_step`
  - `skip_step`
  - `abort`
- `SemanticLayerService.run_watch_time_drop_workflow()` 已不再是纯静态串行调用，现会：
  - 对每个候选 step 先做 pre-step replanning decision
  - 执行后根据 evidence / feedback 再做 post-step decision
  - 在必要时插入补充 `profile_table` step
  - 在高风险 sampling/optional step 上做 replace / skip
  - 在执行错误时按 deterministic fallback 继续或终止
- workflow 返回 payload 现新增 `replanning` 字段，包含：
  - `decisions`
  - `executed_step_types`
  - `final_plan`
- 触发重规划的 step 现会把 decision history 追加写入 `steps.provenance_json.replanning`，便于 evidence / UI / MCP 侧解释。
- 新增直接测试 `tests/test_replanning.py`，覆盖：
  - feedback 分类
  - insert / replace / skip / error fallback 规则
  - workflow 动态插入补充步骤
  - replanning provenance 持久化
- `tests/test_mvp.py` 已扩展断言真实 workflow API 返回 `replanning` 字段。

---

## 任务包 5：打通 Execution Fallback / Failure Semantics

### 目标

让 routing、compiler、executor、planner 在失败时共享同一套反馈语义，而不是各自抛出零散错误。

### 迁移来源

- `app/routing.py`
- `app/analysis_core/executor.py`
- `app/service.py`

### 目标文件

- `app/execution/errors.py`
- `app/execution/feedback.py`
- `app/execution/routing_runtime.py`

### 具体动作

- [x] 定义 execution failure taxonomy：routing error、capability mismatch、translation error、engine unavailable、partial result、timeout
- [x] 将 routing / executor / compiler 错误归一化为结构化 failure / feedback
- [x] 为 planner / re-planner 提供 fallback candidate 列表或 degrade signal
- [x] 明确哪些错误可重试、可重规划、必须失败
- [x] 在 evidence / provenance / observability 中暴露失败上下文

### 兼容要求

- 现有异常路径仍可映射回 HTTP 4xx/5xx
- 第二阶段先统一错误语义，不强求所有错误都拥有 UI 级展示

### 验收标准

- planner、executor、workflow runtime 可共享失败反馈对象
- 引擎不可用 / 无公共引擎 / 翻译失败等场景有明确后续动作

### 当前实现（本轮已完成）

- 新增 `app/execution/errors.py`，引入 `ExecutionFailure`，用统一结构承载：
  - `code`
  - `category`
  - `retryable`
  - `replan_candidate`
  - `fallback_candidates`
  - `detail`
- `app/runtime_contracts.py` 中的 `ExecutionFeedback` 已扩展 `fallback_candidates`，让 runtime / planner / replanner 可以共享同一反馈对象。
- 新增 `app/execution/feedback.py`，用于把离散异常归一化为结构化 failure / feedback，当前覆盖：
  - routing 失败
  - compiler 失败
  - SQL translation 失败
  - executor / engine query 失败
- 新增 `app/execution/routing_runtime.py`，把 `QueryRouter` 的 `KeyError` / `ValueError` 语义提升为：
  - `RoutingResolutionResult`
  - 可选 `feedback`
  - default-engine fallback 决策
- `app/analysis_core/executor.py` 现不再直接抛裸异常，translation / engine query 失败会统一抛出 `ExecutionFailure`。
- `SemanticLayerService` 当前已接入 shared execution feedback seam：
  - `_resolve_engine()` 通过 `RoutingRuntime` 解析并保留 routing feedback context
  - `_compile_step_with_feedback()` 将 compiler `ValueError` 归一化为 `ExecutionFailure`
  - `_make_provenance()` 现会把 routing fallback / failure context 写入 `provenance["routing"]`
- `ReplanningService.decide_on_error()` 现优先消费 `ExecutionFailure`，不再只依赖 message string matching。
- `app/execution/__init__.py` 现导出：
  - `CostModel`
  - `ExecutionFailure`
  - `RoutingRuntime`
- 新增 `tests/test_execution_feedback.py`，覆盖：
  - translator failure normalization
  - engine failure normalization
  - routing fallback feedback
  - provenance 注入 routing context

### 测试范围

- `tests/test_bindings.py`
- `tests/test_dialect.py`
- 新增 `tests/test_execution_feedback.py`
- `tests/test_mvp.py`

### 依赖

- 任务包 3
- 任务包 4

---

## 任务包 6：兼容层、测试与阶段收尾

### 目标

确保第二阶段完成后，系统主链更强，但外部入口仍稳定、测试更贴近 runtime、文档可以指导下一阶段继续演进。

### 具体动作

- [ ] 保留 `SemanticLayerService` facade，但让其主要负责 orchestration 与 compatibility
- [ ] 保留现有 HTTP / MCP / UI 入口，并最小化返回结构变化
- [ ] 为 planner / governance / costing / re-planning / execution feedback 增加模块级单测
- [ ] 更新设计文档交叉引用：
  - `omnidb-improvement-roadmap.md`
  - `omnidb-vnext-architecture-blueprint.md`
  - `omnidb-module-refactor-checklist.md`
  - `omnidb-phase2-implementation-task-list.md`
- [ ] 在 `plan.md` 与 SQL todos 中同步第二阶段执行状态

### 验收标准

- 现有主要测试继续通过
- 新主链能力有直接模块级测试，不只靠端到端 workflow 覆盖
- 开发者能清楚理解 “planner / governance / execution / feedback” 的边界关系

### 推荐回归命令

```bash
python3 -m unittest tests.test_planning -v
python3 -m unittest tests.test_governance -v
python3 -m unittest tests.test_approvals -v
python3 -m unittest tests.test_bindings -v
python3 -m unittest tests.test_dialect -v
python3 -m unittest tests.test_mvp -v
python3 -m unittest discover -s tests -v
```

### 依赖

- 任务包 1
- 任务包 2
- 任务包 3
- 任务包 4
- 任务包 5

## 6. 推荐的 PR 切分

为了控制风险，建议不要把第二阶段一次性提交，而是切成 6-8 个 PR：

1. PR1：validation models + enhanced validator seam
2. PR2：governance decision / application pipeline
3. PR3：v1 cost model + budget checks
4. PR4：execution feedback taxonomy + fallback signals
5. PR5：re-planning hook into one real workflow
6. PR6：compatibility cleanup + direct tests
7. PR7：文档与主链可观测性收尾

## 7. 风险控制点

第二阶段最容易出问题的地方有四个：

### 7.1 validation 过度耦合现有 handler

控制方式：

- 先围绕 IR、semantic resolution、policy decision 建结构化验证对象
- 不把所有校验继续塞回 `PlanningService.execute_plan()`

### 7.2 governance 改造成“全能模块”

控制方式：

- 分离 CRUD、decision、application、audit
- 先让 governance 进入 compile / result shaping 主链，再谈更复杂审批自动化

### 7.3 cost model 伪精确

控制方式：

- 明确 estimate confidence
- unknown / low-confidence 时返回风险提示，不装作精准

### 7.4 re-planning 过早复杂化

控制方式：

- 第二阶段先做 deterministic trigger + rule-based decision
- 先把反馈对象和闭环接起来，再考虑更复杂 planner loop

## 8. 第二阶段结束后的下一步

第二阶段完成后，才适合进入下一轮工作：

- 做实 execution capability model
- 推进多引擎 federation / translation
- 拆 HTTP / MCP / UI 目录与 composition root
- 推进 async runtime / background orchestration

## 9. 结论

第二阶段不是“再加更多工作流”，而是**让 planning、governance、costing、execution feedback 真的成为同一条运行时主链**。

一句话总结：

**第二阶段先把计划判定、治理应用、成本估算和反馈重规划接起来，再谈更大规模的平台化。**
