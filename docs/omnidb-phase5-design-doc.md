# OmniDB Phase 5 设计文档

## 1. 文档目的

本文档定义 OmniDB 在 Phase 4 之后进入 Phase 5 的目标、范围与迁移策略。假设 Phase 4 已把 semantic runtime 与 IR-first 主链做实，Phase 5 的重点将转向两个更深的方向：

- 把 execution substrate 从“有 routing seam”推进为“有 capability-aware routing / translation / federation hook 的真实执行层”
- 把外围平台边界从“兼容入口集中在旧文件”推进为“更清晰的 registry / governance / API / MCP / platform boundary”

因此，Phase 5 的主题可以概括为：

**execution substrate + platform boundary refactor**

相关配套文档：

- `docs/omnidb-improvement-roadmap.md`
- `docs/omnidb-vnext-architecture-blueprint.md`
- `docs/omnidb-module-refactor-checklist.md`
- `docs/omnidb-phase4-design-doc.md`
- `docs/omnidb-phase5-implementation-task-list.md`

## 2. Phase 4 之后的预期状态

进入 Phase 5 时，希望仓库已经具备以下前提：

- semantic runtime 能提供更强执行语义
- request / step / execution-plan IR 已成型
- validator / governance / costing / routing 对 IR 的依赖更明确
- evidence scoring 已具备可扩展 seam

在此基础上，剩余的主要结构性缺口将集中在以下几类：

### 2.1 execution 还缺少 honest multi-engine substrate

当前 execution 已有：

- routing runtime
- cost model
- feedback / errors
- workflow orchestrator

但仍缺少：

- engine capability profile
- semantic-driven routing input
- translation layer skeleton
- federation hook

### 2.2 routing 仍偏 table-centric

当前路由仍主要围绕：

- table resolution
- binding availability
- default fallback

而 blueprint 目标需要综合：

- semantic operation type
- policy requirements
- estimated cost / latency
- freshness / quality guarantees

### 2.3 代码结构仍保留大量 Phase 1-3 的兼容集中点

虽然内核已经更清晰，但外围边界仍偏旧结构：

- `app/main.py` 仍承载 app factory + routes
- `app/mcp_server.py` 仍集中承载 MCP wrapper
- `app/sources.py` / `app/engines.py` / `app/bindings.py` / `app/sync.py` 仍未正式收敛为 registry layer
- `app/governance.py` / `app/approvals.py` 仍未完全对齐为更明确的 governance chain

## 3. Phase 5 设计目标

### 3.1 建立 engine capability model

让每个 engine 对外显式暴露：

- supported SQL / compilation features
- supported step primitives
- materialization / staging capability
- policy support level
- latency / cost class
- federation support level

### 3.2 让 routing 以 semantic intent 驱动

路由不应只回答“表在哪个 engine 上”，还应回答：

- 这个 semantic operation 需要哪些能力
- 哪个 engine 更符合 budget / latency / governance constraints
- 是否需要 staged handoff / future federation

### 3.3 预留 translation / federation execution substrate

Phase 5 不要求做完整跨引擎执行，但应建立至少可扩展的骨架：

- translation seam
- staged handoff schema
- federated merge skeleton

### 3.4 推进平台边界清晰化

在执行底座稳定之后，开始系统推进：

- registry layer
- governance engine boundary
- API / app factory split
- MCP split

## 4. 范围与非目标

### 4.1 Phase 5 范围

Phase 5 纳入以下工作：

1. engine capability profile / capability-aware routing
2. translation / federation skeleton
3. registry 与 governance boundary 收敛
4. API / MCP / app factory 的协议层拆分
5. jobs / approvals / observability / storage 的外围清理

### 4.2 Phase 5 非目标

以下事项不作为 Phase 5 主目标：

- 完整 cross-engine federated execution 生产化
- 全部 legacy endpoint 一次性替换
- UI 大规模重写
- 所有 service 一次性彻底迁目录

## 5. 目标模块边界

Phase 5 结束后，希望代码结构至少向以下边界明显靠拢：

```text
app/
  api/
  mcp/
  registry/
  governance_engine/
  execution/
    capabilities.py
    routing.py
    translation.py
    federation.py
    orchestrator.py
  analysis_core/
  semantic_runtime/
  evidence_engine/
  storage/
```

### 5.1 execution

- capability profile
- semantic-driven routing
- translation
- federation hook
- execution orchestration

### 5.2 registry

- source registry
- engine registry
- binding registry
- sync coordination

### 5.3 governance_engine

- policy service
- quality service
- policy application
- approvals / audit alignment

### 5.4 protocol adapters

- `app/api/`：HTTP 路由与 request/response models
- `app/mcp/`：MCP tool wrapper / models / renderers
- `app/main.py`：只保留极薄兼容入口

## 6. 迁移策略

### 6.1 先 execution substrate，后协议层

只有 capability-aware routing 与 execution substrate 稳定之后，再大规模拆 API / MCP，才能避免协议层跟着中间层反复改。

### 6.2 先 capability profile，后 routing rewrite

先把 engine capability 明确建模，再让 routing 使用 capability / cost / policy 输入，而不是反过来边写 routing 边猜 capability shape。

### 6.3 federation 先立骨架，不强求一次做通

Phase 5 应先建立：

- staged handoff data model
- federated merge skeleton
- error / provenance / audit contract

而不是追求立即支持复杂跨引擎查询。

### 6.4 保留兼容 facade

在 Phase 5 中，以下兼容入口仍应继续保留至少一个阶段：

- `app.main.create_app`
- `app.service.SemanticLayerService`
- `app.mcp_server`

## 7. 测试与兼容要求

Phase 5 每个任务包都需要满足：

- 现有 HTTP endpoint 与 MCP tool 名称保持兼容
- execution capability / routing 有独立单测
- registry / governance / API / MCP 拆层后，既有回归仍通过

优先关注的测试面：

- engine capability tests
- routing decision tests
- translation / federation contract tests
- API compatibility regression
- MCP compatibility regression

## 8. 风险与控制

### 8.1 风险：过早大拆协议层

控制方式：要求 execution substrate 相关任务先完成，再推进 API / MCP split。

### 8.2 风险：capability model 过空泛

控制方式：要求 capability profile 至少被 routing / costing / policy 三个真实消费者使用。

### 8.3 风险：federation skeleton 没有真实落点

控制方式：要求 staged handoff / federated merge 的 contract 至少在设计和测试中有明确使用场景。

## 9. 完成标志

Phase 5 完成后，希望至少达到以下状态：

- execution 层不再只是 routing helper，而是 capability-aware substrate
- 路由能显式基于 semantic intent / cost / policy 选择执行路径
- translation / federation 至少有稳定骨架
- API / MCP / registry / governance 边界明显更清晰

一句话概括：

**把 OmniDB 从“IR-first runtime 已成型”推进到“具备 honest multi-engine substrate 与清晰平台边界的系统”。**
