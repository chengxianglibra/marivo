# OmniDB Phase 3 设计文档

## 1. 文档目的

本文档定义 OmniDB 在 Phase 2 之后进入 Phase 3 的目标、范围与演进策略。Phase 2 已经把 validation、policy pipeline、cost model、replanning、execution feedback 等运行时契约做成了真实代码，但系统内核仍然缺少三块关键能力：

- 足够表达语义意图与执行上下文的 analysis IR
- 区分 primitive / composite / domain workflow 的 step 体系
- 可插拔的 evidence pipeline 与更明确的 execution orchestration

因此，Phase 3 的重点不是再补一个新的业务流程，而是把分析内核真正做实，让后续的 planner、governance、multi-engine、protocol 拆分都建立在更稳固的中间层之上。

相关配套文档：

- `docs/omnidb-improvement-roadmap.md`
- `docs/omnidb-vnext-architecture-blueprint.md`
- `docs/omnidb-module-refactor-checklist.md`
- `docs/omnidb-phase2-implementation-task-list.md`
- `docs/omnidb-phase3-implementation-task-list.md`

## 2. Phase 2 之后的当前状态

截至 Phase 2 收尾，仓库已经具备以下能力：

- `app/runtime_contracts.py` 提供结构化 validation / governance / costing / execution feedback / replanning 契约
- `app/planner/replanning.py` 提供 deterministic replanning hook
- `app/execution/` 已出现 costing / feedback / routing runtime 等 seam
- `app/semantic_runtime/` 已承接 catalog / planner context / semantic resolution 基础能力
- `SemanticLayerService` 仍作为对外 facade，HTTP / MCP / UI 入口保持兼容

但当前实现仍存在以下结构性问题：

### 2.1 IR 仍然过薄

`app/analysis_core/ir.py` 里的 `AnalysisStepIR` 目前只有：

- `index`
- `step_type`
- `params`
- `dependencies`

这足以支撑 Phase 2 的兼容演进，但不足以承载：

- semantic intent
- expected artifact shape
- evidence extraction hints
- governance / budget / execution strategy context
- composite workflow expansion 与 lineage

### 2.2 step 体系仍然是“兼容枚举”，不是“可复用原语”

当前 step type 还混合着三类东西：

- generic primitive：`compare_metric`、`profile_table`、`sample_rows`
- domain-specific analysis：`analyze_qoe`、`analyze_ads`、`analyze_recommendation`
- workflow tail step：`synthesize_findings`

这会导致新业务场景仍倾向于继续往 `service.py` 或 step runner 里加 domain handler，而不是在稳定原语上组合。

### 2.3 evidence pipeline 仍然偏硬编码

虽然已经有 `app/evidence_engine/` compatibility seam，但核心 observation / claim 生成逻辑仍主要集中在 `app/evidence.py` 的固定函数中。当前缺少：

- `ObservationExtractor` 合同
- `ClaimSynthesizer` 合同
- extractor / synthesizer registry
- 按 artifact type / domain 选择策略的机制

### 2.4 orchestration 仍主要停留在 facade 内部

`SemanticLayerService` 仍负责太多串联动作：

- governance pre-check
- step dispatch
- routing / compile / execute 反馈收集
- evidence persistence
- watch-time workflow orchestration

Phase 2 已经补齐了契约，但 orchestration 还没有被抽成明确层次。

## 3. Phase 3 设计目标

Phase 3 目标分成四条主线：

### 3.1 把 IR 从“兼容载体”升级为“分析主契约”

Phase 3 要让 `AnalysisStepIR` 能表达：

- step category（primitive / composite / workflow）
- semantic intent（metric / dimension / filters / time windows）
- expected artifact / evidence hints
- governance context / execution hints
- upstream dependency intent，而不仅是 step index

目标不是一次性完成最终态 IR，而是先把后续模块真正会消费的字段补出来。

### 3.2 建立 primitive / composite / workflow 三层 step 体系

建议把 step 分层为：

- **Primitive**：原子分析动作，如 compare / aggregate / rank / profile / sample
- **Composite**：由多个 primitive 组合而成的复用分析模式
- **Workflow**：面向业务场景的模板，例如 `watch_time_drop`

Phase 3 不追求一口气迁完所有场景，而是先把分类与 runtime 骨架建立起来，并让现有 watch-time workflow 成为第一批消费者。

### 3.3 把 evidence pipeline 做成插件式框架

Phase 3 的 evidence 目标不是推翻现有 observation / claim 模型，而是把“如何从 artifact 中抽 observation、如何从 observation 合成 claim”变成可注册、可测试、可扩展的框架。

### 3.4 引入显式 execution orchestration

当前 facade 里已经具备很多执行链片段，但 Phase 3 需要把以下串联关系变成明确 runtime seam：

```text
step/workflow request
  -> semantic resolution
  -> governance application
  -> routing
  -> compilation
  -> execution
  -> evidence extraction / synthesis
  -> persistence / provenance
```

这里的目标不是马上彻底清空 `service.py`，而是先引入新的 orchestration 对象并逐步让 facade 委托。

## 4. 范围与非目标

### 4.1 Phase 3 范围

Phase 3 纳入以下工作：

1. IR 增强与 legacy step -> IR 转换器增强
2. primitive / composite step taxonomy 与 registry
3. composite workflow runtime 骨架
4. evidence extractor / synthesizer 插件化
5. execution orchestrator 落地并接管主要串联路径

### 4.2 Phase 3 非目标

以下事项不作为 Phase 3 的主目标：

- 完整拆分 `app/main.py` 的所有 HTTP route
- 完整拆分 MCP 层
- 完整多引擎联邦执行
- 审批、jobs、observability 的全面产品化
- 用新架构一次性替换所有 legacy handler

这些事项仍然重要，但它们依赖更稳定的分析内核，适合在后续阶段继续推进。

## 5. 目标模块边界

Phase 3 结束后，希望核心边界至少演进到如下形态：

```text
app/
  analysis_core/
    ir.py
    primitives.py
    composites.py
    step_runners/
    workflows/
  evidence_engine/
    pipeline.py
    extractors/
    synthesizers/
    scoring.py
  execution/
    orchestrator.py
    costing.py
    routing_runtime.py
    feedback.py
  semantic_runtime/
  planner/
  service.py          # facade + compatibility
```

各层职责如下：

### 5.1 analysis_core

- 定义增强后的 IR
- 维护 primitive / composite taxonomy
- 提供 composite expansion runtime
- 保持编译 / 执行接口的稳定输入

### 5.2 evidence_engine

- 根据 artifact type / domain 选择 observation extractor
- 根据 observation 集合选择 claim synthesizer
- 统一生成 edges / synthesis summary

### 5.3 execution

- 负责显式 orchestration，而不是只提供零散 helper
- 串联 semantic / governance / routing / compile / execute / evidence
- 为 facade 与 future planner 提供统一执行入口

### 5.4 service facade

- 保留兼容入口
- 管理 session 语义与 persistence glue
- 调用 orchestration 层
- 不再长期承载大段 domain-specific handler 逻辑

## 6. 迁移策略

Phase 3 继续遵循兼容优先的迁移策略：

### 6.1 先增量引入新合同，再迁移旧调用方

例如：

- 先增强 `AnalysisStepIR`
- 再让 `PlanningService` / workflow runtime / orchestrator 消费新字段
- 旧入口继续通过 `from_legacy_step()` 获得兼容 IR

### 6.2 先拆 taxonomy，再迁 workflow

primitive / composite 分类先建立，再让 `watch_time_drop` 迁到 composite runtime 上。这样能减少一次性重写的风险。

### 6.3 先插件化 evidence，再收口 facade

只有当 evidence pipeline 真正有 extractor / synthesizer 合同后，`service.py` 中的 persistence glue 才能进一步收敛。

### 6.4 先落 orchestrator，再考虑协议层拆分

HTTP / MCP / UI 的拆分仍然重要，但要排在分析内核稳定之后。

## 7. 测试与兼容要求

Phase 3 每个任务包都需要满足以下要求：

- 现有 HTTP / MCP / UI 对外接口不变
- `SemanticLayerService` 仍保留 facade 角色
- 新模块必须补模块级单测
- 既有 `unittest` 回归持续通过

优先关注的测试面：

- IR model / legacy conversion tests
- primitive / composite runtime tests
- evidence extractor / synthesizer tests
- end-to-end workflow regression

## 8. 风险与控制

### 8.1 风险：一次性改太多路径

控制方式：每次只迁一类职责，并保留 compatibility layer。

### 8.2 风险：taxonomy 建出来但没有真实消费者

控制方式：要求 `watch_time_drop` workflow 成为 composite runtime 的首个落点。

### 8.3 风险：evidence 插件化只是“换目录”

控制方式：必须定义 extractor / synthesizer 合同，并让现有逻辑通过 registry 运行，而不是只复制函数。

## 9. 结论

Phase 3 的本质，是把 OmniDB 从“已经有 Phase 2 runtime seam 的兼容 facade”继续推进为“真正围绕 IR、原语层和 evidence pipeline 运转的分析内核”。

一句话总结：

**Phase 3 先做实 analysis IR、primitive/composite step 体系、evidence pluginization 与 execution orchestration，再为后续协议层与平台层重构创造条件。**
