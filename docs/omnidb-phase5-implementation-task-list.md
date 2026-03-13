# OmniDB Phase 5 实施任务清单

## 1. 文档目的

本文档把 `docs/omnidb-phase5-design-doc.md` 继续拆解为可执行任务包。Phase 5 的核心目标是把 execution substrate 做成 capability-aware、多引擎诚实建模的执行层，并开始系统拆分 registry / governance / API / MCP 等外围边界。

## 2. 实施原则

- 先稳定 execution substrate，再拆 protocol adapters
- 先 capability model，再改 routing 决策
- federation 先建骨架，不强求一步到位做通
- 每个任务包完成后都需要：
  - 保持兼容入口
  - 增加相应模块级测试
  - 更新文档 / plan / checkpoint

## 3. 任务包总览

| Task | 目标 | 依赖 |
|---|---|---|
| P5-1 | Phase 5 设计文档与执行清单 | 无 |
| P5-2 | 建立 engine capability profile | P5-1 |
| P5-3 | 让 routing 以 semantic intent / capability 驱动 | P5-2 |
| P5-4 | 引入 translation / federation skeleton | P5-2, P5-3 |
| P5-5 | 重构 registry / governance 边界 | P5-2, P5-3 |
| P5-6 | 拆 API / app factory 协议层 | P5-5 |
| P5-7 | 拆 MCP 与外围 platform cleanup | P5-5, P5-6 |

---

## 4. 任务包明细

### P5-1：设计文档与执行基线

#### 目标

固定 Phase 5 的问题定义、模块边界、迁移顺序与任务依赖。

#### 交付物

- `docs/omnidb-phase5-design-doc.md`
- `docs/omnidb-phase5-implementation-task-list.md`

#### 完成标准

- execution substrate 与 protocol-boundary 的先后顺序清晰
- Phase 5 内部任务依赖明确

---

### P5-2：建立 engine capability profile

#### 目标

为每种 engine 建立结构化 capability schema，让 routing / costing / governance 能共享同一能力描述。

#### 拟改动模块

- `app/execution/capabilities.py`
- `app/engines.py` 或后续 engine registry
- `app/execution/costing.py`

#### 建议动作

- 定义 capability profile 数据结构
- 为 DuckDB / Trino / future engines 提供 capability builder
- 让 costing / routing 能直接读取 capability profile

#### 完成标准

- engine capability 有明确 schema
- 至少两个 runtime 消费 capability profile

#### 已完成实现

- 新增 `app/execution/capabilities.py`，定义正式的 `EngineCapabilityProfile`
- 为 `duckdb` / `trino` / `spark_connect` / `spark_thrift` 提供默认 capability builder
- `EngineService` 现在会把 engine capabilities 规范化为 capability profile，而不是裸 `capabilities_json`
- `QueryRouter` 现在在 priority 相同的情况下，使用 capability score 作为次级 tiebreaker
- `CostModel` 现在会在 estimate detail 中附带 `engine_capabilities`，让 capability profile 进入成本可观测面
- 已补充 `tests/test_engines.py` / `tests/test_bindings.py` / `tests/test_costing.py` 回归覆盖

---

### P5-3：让 routing 以 semantic intent / capability 驱动

#### 目标

把 routing 从 table-centric 逻辑推进到结合 semantic intent、capability、cost、policy 的执行选择。

#### 拟改动模块

- `app/execution/routing.py` 或现有 routing runtime / router
- `app/execution/orchestrator.py`
- `app/runtime_contracts.py`

#### 建议动作

- 增加 semantic-driven routing input
- 引入 capability / cost / policy-aware 决策逻辑
- 保留现有 binding / default fallback 的兼容行为

#### 完成标准

- routing 决策不再只由 table binding 驱动
- 可解释为何选择某个 engine / fallback path

#### 已完成实现

- `app/routing.py` 现在引入正式的 `RoutingIntent` 输入，并把 candidate score / selection reason / strategy 收敛到 `ResolvedRoute.routing_detail`
- routing 选择现在同时考虑 binding priority、capability score、semantic fit、policy fit 与轻量 cost heuristic，而不再只是 table binding + priority
- `app/planning.py` 现在会把 step semantic resolution + session policy 组装成 routing intent，并把解释信息写回 `ExecutionTargetIR`
- `ExecutionTargetIR` 现已补充 `routing_reason` / `routing_detail` / `capability_profile`，让 plan IR 可以解释 engine 选择路径
- `/routing/resolve` 现在接受可选 `routing_intent`，并返回 `selection_reason`、`routing_detail` 与 `capability_profile`
- 已补充 `tests/test_bindings.py` / `tests/test_planning.py` 回归覆盖 semantic-driven routing 场景

---

### P5-4：引入 translation / federation skeleton

#### 目标

为多引擎与未来 cross-engine execution 建立最小但真实的 substrate 骨架。

#### 拟改动模块

- `app/execution/translation.py`
- `app/execution/federation.py`
- `app/execution/errors.py`

#### 建议动作

- 建立 translation seam
- 定义 staged handoff / federated merge 的 contract
- 为 future federation 预留 provenance / audit / error shape

#### 完成标准

- translation / federation 至少有稳定骨架与测试
- 不要求当期完成完整跨引擎执行

---

### P5-5：重构 registry / governance 边界

#### 目标

把 source / engine / binding / sync 与 governance / approvals 进一步收敛到更清晰的边界。

#### 拟改动模块

- `app/registry/`
- `app/governance_engine/`
- 现有 `app/sources.py` / `app/engines.py` / `app/bindings.py` / `app/sync.py`
- 现有 `app/governance.py` / `app/approvals.py`

#### 建议动作

- 提取 registry layer
- 把 approvals 对齐到 governance / audit chain
- 保留现有 API 语义与 compatibility facade

#### 完成标准

- registry 只负责登记 / 同步
- governance 更接近 policy application / audit chain

---

### P5-6：拆 API / app factory 协议层

#### 目标

把 `main.py` 中的路由与 app composition root 拆开，让 HTTP 层真正变薄。

#### 拟改动模块

- `app/api/`
- `app/ui/`
- `app/main.py`

#### 建议动作

- 提取 app factory
- 按领域拆路由
- 迁移 HTTP models 到 `app/api/models.py`
- 保留 `create_app()` 兼容签名

#### 完成标准

- `main.py` 不再承载全部路由与装配细节
- HTTP 层不再混杂领域逻辑

---

### P5-7：拆 MCP 与外围 platform cleanup

#### 目标

在 API 拆层之后继续清理 MCP 与外围平台支撑边界。

#### 拟改动模块

- `app/mcp/`
- `app/jobs.py`
- `app/observability.py`
- `app/storage/`

#### 建议动作

- 拆 MCP models / renderers / server wrapper
- 让 jobs 更贴近 runtime 接口
- 为 observability 增加 planner / compiler / executor / governance 维度标签
- 为 storage 补 repository seam

#### 完成标准

- MCP 保持 thin proxy 但边界更清晰
- platform cleanup 为后续产品化 / 运维化留下稳定基础

## 5. 推荐执行顺序

建议按以下顺序推进：

1. P5-1 设计与执行基线
2. P5-2 engine capability profile
3. P5-3 semantic-driven routing
4. P5-4 translation / federation skeleton
5. P5-5 registry / governance boundary
6. P5-6 API / app factory split
7. P5-7 MCP / platform cleanup

这个顺序的核心逻辑是：

**先把 execution substrate 建稳，再拆 registry / governance / API / MCP 外围边界。**

## 6. 当前实施状态

- [done] P5-1 设计与执行基线
- [done] P5-2 建立 engine capability profile
- [done] P5-3 让 routing 以 semantic intent / capability 驱动
- [pending] P5-4 引入 translation / federation skeleton
- [pending] P5-5 重构 registry / governance 边界
- [pending] P5-6 拆 API / app factory 协议层
- [pending] P5-7 拆 MCP 与外围 platform cleanup
