# OmniDB 代码级模块改造清单

## 1. 文档目的

本文档基于 `docs/omnidb-vnext-architecture-blueprint.md`，进一步拆解出一份可执行的**代码级模块改造清单**。目标不是直接重写仓库，而是给出一条可渐进落地的重构路径，使 OmniDB 从当前的 MVP 代码组织演进到更清晰的 vNext 模块边界。

相关配套文档：

- `docs/omnidb-improvement-roadmap.md`：说明为何按当前阶段顺序推进
- `docs/omnidb-vnext-architecture-blueprint.md`：定义目标架构边界与执行链
- `docs/omnidb-phase1-implementation-task-list.md`：把第一阶段工作拆成可落地任务包
- `docs/omnidb-phase2-implementation-task-list.md`：把第二阶段工作拆成可落地任务包
- `docs/omnidb-phase3-design-doc.md`：定义第三阶段的分析内核目标与迁移策略
- `docs/omnidb-phase3-implementation-task-list.md`：把第三阶段工作拆成可落地任务包

当前实施状态（截至 Phase 2 收尾）：

- `app.service.SemanticLayerService` 仍保留 facade 角色，但已主要收敛为 orchestration / compatibility 层。
- `app.planner/` 与 `app.execution/` 已出现真实 runtime seam：
  - `app/planner/replanning.py`
  - `app/execution/costing.py`
  - `app/execution/errors.py`
  - `app/execution/feedback.py`
  - `app/execution/routing_runtime.py`
- 第三阶段将继续以本清单中的 P0 项为主线，优先落 IR、step taxonomy、evidence pipeline 与 orchestration seam，而不是优先拆协议层。

本文档回答三个问题：

- 当前 `app/` 下的文件应如何映射到未来模块
- 每个模块的代码改造动作是什么
- 重构时应如何保持兼容、测试可控、演进有序

## 2. 当前代码布局概览

当前 `app/` 目录的主要模块为：

- `main.py`：应用装配与全部 FastAPI 路由
- `service.py`：主要 orchestration 与 step runner
- `planning.py`：plan CRUD / validation / execution
- `semantic.py`：semantic CRUD
- `governance.py`：policy / quality rule / enforcement
- `routing.py`：QueryRouter
- `bindings.py`：source-engine bindings
- `engines.py`：engine registry
- `sources.py`：source registry
- `sync.py`：catalog sync
- `evidence.py`：observation / claim synthesis
- `mcp_server.py`、`mcp_client.py`：MCP wrapper
- `models.py`：FastAPI request bodies
- `jobs.py`：后台 job orchestration
- `approvals.py`、`observability.py`：审批与可观测性
- `storage/`：metadata store、analytics engine、schema

这些模块在概念层面已具备雏形，但职责仍较集中，尤其是 `main.py` 与 `service.py`。

## 3. 目标代码结构

建议的目标目录边界如下：

```text
app/
  api/                  # FastAPI 路由与 HTTP request/response models
  mcp/                  # MCP tools, client wrappers, MCP input models
  session/              # session manager, workflow runtime, checkpoints
  planner/              # plan draft/validate/replan/budget
  semantic_runtime/     # semantic resolution, catalog query, semantic models
  governance_engine/    # policies, quality, approvals hooks, audit decisions
  analysis_core/        # analysis IR, step primitives, compiler, executor
  evidence_engine/      # artifact schemas, extractors, synthesizers, scoring
  execution/            # routing, engine capability, dialect translation, federation hooks
  registry/             # source/engine/binding registries, sync coordination
  storage/              # metadata store, analytics backends, schema, repositories
  ui/                   # UI serving helpers and static asset wiring
```

短期内不要求一次性改到这个结构，但所有新增抽象都应朝这个边界收敛。

## 4. 改造总原则

### 4.1 先“抽”再“移”

先把逻辑从大文件中抽成可独立测试的模块，再调整 import 路径和包结构。不要先大规模移动文件再找逻辑。

### 4.2 保留兼容层

旧的公开入口应短期保留，例如：

- `app.service.SemanticLayerService`
- `app.main.create_app`
- `app.mcp_server`

可以内部委托给新模块，但不要一开始就移除。

### 4.3 以测试边界驱动重构

每次改造都要明确：

- 哪些现有测试应继续通过
- 哪些测试要拆分或迁移
- 哪些新模块需要新增单测

### 4.4 优先拆“领域边界”，其次拆“协议边界”

先拆 analysis / evidence / planner / governance 这些内核职责，再拆 HTTP / MCP / UI 层。

## 5. 模块映射总表

| 当前文件/模块 | 目标模块 | 改造方式 | 优先级 |
|---|---|---|---|
| `app/main.py` | `app/api/`, `app/ui/`, app composition root | 拆路由与装配 | P1 |
| `app/service.py` | `app/session/`, `app/analysis_core/`, `app/evidence_engine/` | 核心拆分 | P0 |
| `app/planning.py` | `app/planner/` | 扩容并拆 validator/costing/replanning | P0 |
| `app/semantic.py` | `app/semantic_runtime/` | 从 CRUD 升级为 runtime | P0 |
| `app/evidence.py` | `app/evidence_engine/` | 拆 extractors/synthesizers/scoring | P0 |
| `app/governance.py` | `app/governance_engine/` | 拆 policy/quality/application/audit | P1 |
| `app/routing.py` + `app/dialect.py` | `app/execution/` | 合并为 execution substrate | P1 |
| `app/engines.py` + `app/bindings.py` + `app/sources.py` + `app/sync.py` | `app/registry/` + `app/execution/` | registry 与 execution 分离 | P1 |
| `app/mcp_server.py` + `app/mcp_client.py` | `app/mcp/` | 协议层下沉 | P2 |
| `app/models.py` | `app/api/models.py` | HTTP model 归位 | P2 |
| `app/jobs.py` | `app/session/` 或 `app/orchestration/` | 贴近 runtime | P2 |
| `app/approvals.py` | `app/governance_engine/` | 并入治理/审批链 | P2 |
| `app/catalog_query.py` | `app/semantic_runtime/` | 贴近 semantic runtime | P1 |
| `app/observability.py` | `app/platform/` 或保留原位 | 保持独立 | P3 |

## 6. P0 改造清单：先拆分析内核

这部分决定 OmniDB 后续是否还能继续健康演化。

### 6.1 拆 `app/service.py`

#### 当前职责

- session CRUD
- step dispatch
- workflow orchestration
- engine resolution fallback
- SQL construction and execution
- artifact persistence
- observation insertion
- claim synthesis

#### 目标拆分

建议拆为：

- `app/session/session_manager.py`
- `app/session/workflow_runtime.py`
- `app/analysis_core/step_runner.py`
- `app/analysis_core/query_compiler.py`
- `app/analysis_core/executor.py`
- `app/evidence_engine/pipeline.py`

#### 具体动作清单

- [ ] 从 `SemanticLayerService` 中抽出 session CRUD 到 `SessionManager`
- [ ] 从 `run_step()` dispatcher 抽出 `StepRunnerRegistry`
- [ ] 把 `_run_compare_watch_time` 等 handler 收敛为独立 step runner 类或函数模块
- [ ] 抽出 `_resolve_engine()` 到 execution 层
- [ ] 抽出 SQL 生成逻辑到 compiler 层
- [ ] 抽出 artifact / observation / claim 持久化到专门 persistence helper 或 repository
- [ ] 让 `SemanticLayerService` 暂时变成 facade，内部委托给新模块

#### 完成标准

- `service.py` 不再直接承载大段 SQL 和 evidence 组装逻辑
- step 执行可单测，无需通过整个 FastAPI app 才能验证
- `SemanticLayerService` 文件大小明显下降，并以 orchestration 为主

#### 受影响测试

- `tests/test_mvp.py`
- `tests/test_evidence.py`
- `tests/test_planning.py`
- 任何直接 import `SemanticLayerService` 的测试

---

### 6.2 建立 `analysis_core/ir.py`

#### 新模块

- `app/analysis_core/ir.py`
- `app/analysis_core/primitives.py`

#### 具体动作清单

- [ ] 定义 `AnalysisRequest`
- [ ] 定义 `AnalysisStepIR`
- [ ] 定义 `ExecutionPlanIR`
- [ ] 为现有 step 类型建立从 `step_type + params` 到 `AnalysisStepIR` 的转换器
- [ ] 为 primitive step 建立枚举或 registry
- [ ] 给旧工作流增加 “compile to IR” 适配层

#### 完成标准

- planning、routing、compiler 至少共享同一组 IR 类型
- 新增 step 不再必须直接编写 SQL handler

---

### 6.3 拆 `app/evidence.py`

#### 目标结构

- `app/evidence_engine/schemas.py`
- `app/evidence_engine/extractors.py`
- `app/evidence_engine/synthesizers.py`
- `app/evidence_engine/scoring.py`

#### 具体动作清单

- [ ] 抽出 artifact / observation / claim 的 typed schema
- [ ] 抽出 `score_confidence()` 到独立 scoring 模块
- [ ] 将 `make_observation()` 系列收敛为 extractor framework
- [ ] 将 `synthesize_claims()` 拆为可注册 synthesizer
- [ ] 增加 extractor/synthesizer 注册点，支持按 observation type 或 domain 选择

#### 完成标准

- `evidence.py` 不再是单文件堆叠所有证据逻辑
- observation 与 claim 生成逻辑可以按 domain 插件化

#### 受影响测试

- `tests/test_evidence.py`
- `tests/test_mvp.py`

---

### 6.4 强化 `app/semantic.py` 为 `semantic_runtime`

#### 目标结构

- `app/semantic_runtime/catalog_service.py`
- `app/semantic_runtime/resolution.py`
- `app/semantic_runtime/semantic_models.py`
- `app/semantic_runtime/mappings.py`

#### 具体动作清单

- [ ] 保留现有 entity / metric / mapping CRUD
- [ ] 增加 semantic resolution 层，用于把 metric/entity/dimension 解析为执行语义
- [ ] 为 metric 增加 grain / allowed dimensions / quality expectations / lineage metadata
- [ ] 把 `catalog_query.py` 并入 semantic runtime
- [ ] 定义 planner context provider

#### 完成标准

- semantic 层不只提供 CRUD，还能为 planning 和 compiler 提供结构化解析结果

## 7. P1 改造清单：打通 governance / routing / execution

### 7.1 拆 `app/governance.py`

#### 目标结构

- `app/governance_engine/policy_service.py`
- `app/governance_engine/quality_service.py`
- `app/governance_engine/policy_application.py`
- `app/governance_engine/audit.py`

#### 具体动作清单

- [ ] 把 policy CRUD 与 enforcement 分离
- [ ] 把 quality rule 执行与 policy check 分离
- [ ] 新增 `PolicyDecision` / `PolicyApplicationResult`
- [ ] 让 governance 既能做 pre-check，也能参与 compile-time rewrite
- [ ] 增加审计事件持久化设计

#### 完成标准

- governance 不再只是 “return passed/violations”
- 编译链可消费 policy decision

---

### 7.2 收敛 execution 层

#### 当前散落位置

- `app/routing.py`
- `app/dialect.py`
- `app/engines.py`

#### 目标结构

- `app/execution/routing.py`
- `app/execution/capabilities.py`
- `app/execution/translation.py`
- `app/execution/federation.py`（可先为空骨架）

#### 具体动作清单

- [ ] 从 `EngineService` 中抽出 capability profile 定义
- [ ] 为每种 engine 增加 capability builder
- [ ] 让 QueryRouter 基于 capability / cost / policy 输入做决策
- [ ] 将 SQL translate 逻辑下沉为 compiler/execution 依赖，而不是 service helper
- [ ] 预留联邦执行模式的数据结构

#### 完成标准

- execution 决策不只由 “有没有 binding” 驱动
- 路由代码与 service orchestration 解耦

---

### 7.3 重构 registry 层

#### 当前散落位置

- `app/sources.py`
- `app/engines.py`
- `app/bindings.py`
- `app/sync.py`

#### 目标结构

- `app/registry/source_registry.py`
- `app/registry/engine_registry.py`
- `app/registry/binding_registry.py`
- `app/registry/sync_service.py`

#### 具体动作清单

- [ ] 将 registry CRUD 与 runtime execution 决策明确分离
- [ ] 保留 source/engine/binding 的现有 API 语义
- [ ] 为 sync 结果输出统一 snapshot metadata
- [ ] 明确 registry 与 semantic runtime 的交界

#### 完成标准

- registry 只负责登记与同步，不负责分析时决策

## 8. P2 改造清单：拆协议层与运行时

### 8.1 拆 `app/main.py`

#### 目标结构

- `app/api/app_factory.py`
- `app/api/routes/sessions.py`
- `app/api/routes/plans.py`
- `app/api/routes/semantic.py`
- `app/api/routes/sources.py`
- `app/api/routes/engines.py`
- `app/api/routes/governance.py`
- `app/api/routes/jobs.py`
- `app/api/routes/approvals.py`
- `app/api/routes/catalog.py`
- `app/api/models.py`
- `app/ui/routes.py`

#### 具体动作清单

- [ ] 抽出 app factory
- [ ] 按领域拆路由文件
- [ ] 把 HTTP request models 从 `app/models.py` 挪到 `app/api/models.py`
- [ ] UI 路由和静态挂载逻辑移到 `app/ui/routes.py`
- [ ] 保留 `create_app()` 对外签名不变

#### 完成标准

- `main.py` 只保留兼容入口或极薄封装
- HTTP 层不再混杂业务逻辑

---

### 8.2 拆 `mcp` 层

#### 当前散落位置

- `app/mcp_server.py`
- `app/mcp_client.py`

#### 目标结构

- `app/mcp/server.py`
- `app/mcp/client.py`
- `app/mcp/models.py`
- `app/mcp/renderers.py`

#### 具体动作清单

- [ ] MCP input models 从 server 文件中拆出
- [ ] markdown/json renderer 拆出
- [ ] 保持 MCP 仍为 thin proxy
- [ ] 不把领域逻辑搬到 MCP 层

#### 完成标准

- MCP 层只做协议适配、参数校验和结果格式化

---

### 8.3 把 jobs 更贴近 runtime

#### 目标位置

- `app/session/jobs.py` 或 `app/orchestration/jobs.py`

#### 具体动作清单

- [ ] 让 `JobService` 依赖 runtime orchestration 接口，而不是直接依赖具体 service 大对象
- [ ] 抽出 job payload schema
- [ ] 增加 cancellation / retry / partial result hooks 的接口位

## 9. P3 改造清单：平台支撑与清理

### 9.1 approvals 并入 governance chain

- [ ] 将 `approvals.py` 与 governance engine 的 policy/audit 流对齐
- [ ] 让 recommendation approval 成为 evidence/governance 链的一部分

### 9.2 observability 独立化

- [ ] 保留 `observability.py` 独立
- [ ] 为 planner / compiler / executor / governance 增加更细粒度指标标签

### 9.3 storage 层补 repository 边界

- [ ] 在保留 `MetadataStore` / `AnalyticsEngine` 抽象的基础上，逐步增加 repository 层
- [ ] 避免 service 层继续直接散写 SQL

## 10. 兼容层清单

为避免大面积破坏，以下兼容层建议至少保留一个阶段：

- [ ] `app.service.SemanticLayerService` facade
- [ ] `app.main.create_app` facade
- [ ] `app.mcp_server.main` facade
- [ ] `app.models` 向 `app.api.models` 的兼容 re-export
- [ ] 原有 step_type 到 primitive/composite step 的映射表

## 11. 测试改造清单

### 11.1 保持现有测试先绿

在重构早期，不优先重写所有测试，而是优先维持：

- `tests/test_mvp.py`
- `tests/test_planning.py`
- `tests/test_evidence.py`
- `tests/test_bindings.py`
- `tests/test_sources.py`
- `tests/test_governance.py`

### 11.2 新增模块级单测

建议补充：

- [ ] IR model tests
- [ ] compiler tests
- [ ] extractor/synthesizer tests
- [ ] policy application tests
- [ ] engine capability / routing tests
- [ ] session runtime tests

### 11.3 API 与 MCP 回归测试

- [ ] 路由拆分后保持现有 endpoint 兼容
- [ ] MCP 工具名称、输入输出结构不变

## 12. 推荐执行顺序

建议按以下顺序推进：

1. `service.py` 抽 facade + step runner registry
2. 新增 `analysis_core/ir.py`
3. 拆 `evidence.py`
4. 扩展 `semantic.py` 为 semantic runtime
5. 重构 `planning.py` 以消费 IR
6. 重构 `governance.py` 为 policy application pipeline
7. 收敛 `routing.py` / `dialect.py` / engine capability
8. 重构 registry 层
9. 拆 `main.py` 路由与 app factory
10. 拆 `mcp` 层
11. 调整 jobs / approvals / observability

这个顺序的核心逻辑是：**先把内核抽象建立起来，再拆协议层。**

## 13. 每一步的完成定义

每次模块改造都建议用相同的完成定义：

- [ ] 旧公开接口仍可用
- [ ] 新模块有独立单测
- [ ] 旧测试仍通过
- [ ] 文档更新
- [ ] 没有把领域逻辑上移到 API / MCP / UI 层

## 14. 结论

这份清单的重点不是“如何把文件换个目录”，而是**如何把 OmniDB 的核心职责重新分层**。只要按这个顺序推进，代码结构就会逐步向蓝图收敛，而不会因为一次性重构而失控。

一句话总结：

**先把 `service.py`、`planning.py`、`semantic.py`、`evidence.py` 四个核心模块拆稳，再去拆 `main.py` 和 MCP。**
