# OmniDB Phase 4 设计文档

## 1. 文档目的

本文档定义 OmniDB 在 Phase 3 完成之后进入 Phase 4 的目标、范围与迁移策略。Phase 3 已经把 analysis IR、primitive/composite taxonomy、composite workflow runtime、evidence pluginization、execution orchestrator 做成了真实 runtime seam，但系统距离 roadmap / blueprint 描述的 IR-first semantic runtime 仍有几块明显缺口：

- semantic object 还缺少足够强的执行语义
- analysis IR 还没有完整覆盖 request / plan / execution plan 层次
- validation / governance / costing / routing 仍只有一部分真正围绕 IR 工作
- evidence scoring 与 recommendation policy 仍偏固定实现

因此，Phase 4 的重点不是继续扩充业务 workflow，而是把 **semantic runtime + IR-first 主链** 做实，为后续的 capability-aware routing、federation、API/MCP 拆层打基础。

相关配套文档：

- `docs/omnidb-improvement-roadmap.md`
- `docs/omnidb-vnext-architecture-blueprint.md`
- `docs/omnidb-module-refactor-checklist.md`
- `docs/omnidb-phase4-implementation-task-list.md`
- `docs/omnidb-phase5-design-doc.md`

## 2. Phase 3 之后的当前状态

截至 Phase 3 收尾，仓库已经具备以下基础：

- `app/analysis_core/ir.py` 已支持 richer `AnalysisStepIR`
- `app/analysis_core/primitives.py` 已提供 primitive / composite taxonomy
- `app/analysis_core/workflows/` 已承接 data-driven composite workflow runtime
- `app/evidence_engine/` 已具备 extractor / synthesizer seam
- `app/execution/orchestrator.py` 已接管 `watch_time_drop` workflow 的 replanning state machine
- `SemanticLayerService` 已明显向 facade / compatibility layer 收敛

但以下结构性缺口仍然存在：

### 2.1 semantic runtime 还不是完整执行语义层

当前 semantic runtime 已有 resolution / planner context / catalog query seam，但 metric / entity 元数据仍偏“可查”，不是“可执行”。典型缺项包括：

- grain / level
- aggregation kind / measure type
- allowed dimensions
- join constraints / legal join path
- lineage / upstream dependencies
- quality expectations

### 2.2 analysis IR 仍然只有 step-centered 主契约

当前 `AnalysisStepIR` 已经够支撑单步与 workflow runtime，但 roadmap / blueprint 中更完整的层次仍未落地：

- `AnalysisRequest`
- `ExecutionPlanIR`
- semantic request -> execution plan 的统一转换边界

这意味着 planner / validator / governance / costing 仍会在 IR 和 legacy param 结构之间来回切换。

### 2.3 IR-first 主链还没有彻底闭合

目前已有的状态更接近：

```text
request
  -> partial IR
  -> partial governance / costing / routing integration
  -> compile / execute
```

而不是 blueprint 目标中的：

```text
AnalysisRequest
  -> semantic resolution
  -> governance application
  -> costing
  -> routing
  -> ExecutionPlanIR
  -> compile / execute
  -> evidence scoring / recommendation policy
```

### 2.4 evidence scoring 仍然不够模块化

Phase 3 已完成 extractor / synthesizer 插件化，但以下能力还没有独立 seam：

- `ConfidenceScorer` / `ConfidenceModel`
- `RecommendationPolicy`
- 按 metric / domain 选择 confidence strategy

## 3. Phase 4 设计目标

Phase 4 聚焦四条主线：

### 3.1 把 semantic runtime 做成可执行语义层

让 semantic object 不只是 CRUD 元数据，而是真正能为 planner / compiler / validator 提供结构化执行语义。重点包括：

- grain / level
- measure / aggregation semantics
- dimension compatibility
- lineage / upstream dependency metadata
- quality expectations

### 3.2 补齐 request / plan / execution plan IR

Phase 4 要把当前以 `AnalysisStepIR` 为核心的结构扩展为更完整的 IR 家族：

- `AnalysisRequest`
- richer `AnalysisStepIR`
- `ExecutionPlanIR`

目标不是一次性做最终 planner，而是先把运行时真正需要共享的请求层和执行层合同建立起来。

### 3.3 让 validation / governance / costing / routing 更彻底地消费 IR

当前这些子系统已有 seam，但还不是完全 IR-first。Phase 4 的目标是让它们：

- 接收统一 request / step / execution IR
- 输出结构化 transforms / cost / policy decisions
- 减少对 legacy `step_type + params` 直读的依赖

### 3.4 把 evidence scoring 与 recommendation policy 做成真正扩展点

Phase 4 将在 extractor / synthesizer 之上继续拆出：

- `ConfidenceScorer`
- `RecommendationPolicy`

使 evidence pipeline 不仅能决定“抽什么 observation / 合成什么 claim”，也能决定“如何评估置信度 / 如何派生 recommendation 策略”。

## 4. 范围与非目标

### 4.1 Phase 4 范围

Phase 4 纳入以下工作：

1. semantic object 元数据增强与 resolution 契约增强
2. `AnalysisRequest` / `ExecutionPlanIR` 设计与落地
3. validator / governance / costing / routing 的 IR-first 接线
4. confidence scoring / recommendation policy seam
5. 为后续 capability-aware routing 预留更清晰输入结构

### 4.2 Phase 4 非目标

以下事项不作为 Phase 4 主目标：

- engine capability model 完整落地
- cross-engine federation
- `app/api/` / `app/mcp/` 的正式拆层
- 完整替换所有 legacy step runner
- 全量 storage repository 改造

这些事项会在下一阶段继续推进。

## 5. 目标模块边界

Phase 4 结束后，希望核心边界至少演进到如下形态：

```text
app/
  analysis_core/
    ir.py
    request_models.py          # 可与 ir.py 合并，取决于规模
  semantic_runtime/
    semantic_models.py
    resolution.py
    planner_context.py
  planner/
    validation.py
    replanning.py
    budget.py
  governance/
    policy_application.py      # 可先保留在现有包下
  evidence_engine/
    pipeline.py
    scoring.py
    recommendation_policy.py
  execution/
    orchestrator.py
    costing.py
    routing_runtime.py
```

各层职责如下：

### 5.1 semantic_runtime

- 暴露 metric / entity / dimension 的执行语义
- 解析合法 grain / dimension 组合
- 为 planner / compiler 提供 semantic resolution
- 向 planner context 输出结构化语义上下文

### 5.2 analysis_core IR

- 承载 request / step / execution plan 三层核心合同
- 解耦用户意图与 engine / SQL 细节
- 作为 validator / governance / costing / routing 的共享输入

### 5.3 planner / governance / costing

- 更少依赖散点 helper
- 更少直接解析 legacy params
- 更明确地产出结构化 validation issue / policy transform / cost estimate

### 5.4 evidence scoring

- 与 extractor / synthesizer 平级成为正式扩展位
- 支持按 metric / domain / artifact type 选择 scoring strategy

## 6. 迁移策略

Phase 4 继续遵循兼容优先策略：

### 6.1 先增强 semantic metadata，再让 runtime 消费

先在 semantic models / resolution 中增加字段与默认行为，再逐步让 planner / compiler / validator 使用这些字段，避免一次性改动全链路。

### 6.2 先补 IR 类型，再让子系统迁移输入

先定义：

- `AnalysisRequest`
- `ExecutionPlanIR`

再逐步迁移 validator / governance / costing / routing 的输入，不强迫旧入口在第一步就全部改写。

### 6.3 scoring seam 增量接线

先保持当前 confidence 算法输出语义兼容，再把其包装成 `ConfidenceScorer`，让 pipeline 通过 registry 或默认实现驱动。

### 6.4 保留 facade 与 legacy adapter

`SemanticLayerService`、legacy `step_type + params` 入口、现有 HTTP / MCP / UI 接口都继续保留，通过 adapter 逐步接到新 IR 上。

## 7. 测试与兼容要求

Phase 4 每个任务包都需要满足：

- HTTP / MCP / UI 对外接口不变
- 旧 `unittest` 回归持续通过
- 新增模块必须有独立单测
- semantic metadata 增强不得破坏现有 CRUD 兼容

优先关注的测试面：

- semantic resolution tests
- IR request / execution plan tests
- validation / governance / costing integration tests
- confidence scoring / recommendation policy tests

## 8. 风险与控制

### 8.1 风险：semantic 字段先加后不用

控制方式：每个新元数据字段必须至少有一个真实消费者（resolution / validator / compiler / planner context）。

### 8.2 风险：IR 类型增加但没有减少 legacy 依赖

控制方式：要求每个任务包明确列出“哪个子系统改为直接消费 IR”。

### 8.3 风险：scoring seam 只是函数搬家

控制方式：必须定义 scorer 接口与选择逻辑，而不只是把现有函数移动目录。

## 9. 完成标志

Phase 4 完成后，希望至少达到以下状态：

- semantic object 能为 planner / compiler 输出更强执行语义
- request / step / execution plan 三层 IR 至少有第一版真实实现
- validator / governance / costing / routing 的主要输入显式转向 IR
- confidence scoring 不再是固定内嵌实现

一句话概括：

**把 OmniDB 从“analysis core 已成型”推进到“semantic runtime 与 IR-first 主链真正闭合”。**
