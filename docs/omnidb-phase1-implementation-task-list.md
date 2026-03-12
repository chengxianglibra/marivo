# OmniDB 第一阶段实际改造任务列表

## 1. 文档目的

本文档将 `docs/omnidb-improvement-roadmap.md` 中的 **Phase A：夯实分析内核**，进一步拆解为一份可以直接执行的**第一阶段实际改造任务列表**。

这里的“实际”指：

- 以当前仓库结构为起点
- 以 PR / 任务包 为粒度
- 每项任务都写清楚目标文件、改造动作、依赖顺序、验收标准与测试影响

本文档默认服务于第一轮内核改造，不覆盖后续 governance 深度重构、多引擎 capability model 和完整 API 拆分。

## 2. 第一阶段范围

### 2.1 第一阶段目标

第一阶段只做一件事：

**把 OmniDB 的分析内核从“集中在 `service.py` 的场景化逻辑”重构为“可继续演进的模块化内核”。**

### 2.2 第一阶段包含

- 从 `service.py` 中抽离 session / step runner / compiler / executor 的边界
- 引入 analysis IR 的最小版本
- 拆分 evidence engine 的最小可扩展结构
- 为 semantic runtime 建立最小骨架与 planner context provider
- 保持现有 HTTP、MCP、UI 和主要测试兼容

### 2.3 第一阶段不包含

- 完整的 re-planning loop
- 完整 policy compile-time rewrite
- 完整 engine capability model
- FastAPI 路由全面拆包
- MCP 层目录重构
- 生产级 async worker / queue

## 3. 第一阶段完成定义

第一阶段完成时，应满足以下条件：

- `app/service.py` 不再同时承载 session、step dispatch、SQL 构造、证据组装等所有职责
- 存在独立的 `analysis_core`、`evidence_engine`、`semantic_runtime` 初始模块
- Planning、routing、step execution 至少能共享同一组 IR 类型
- 旧公开入口仍然可用：
  - `app.main.create_app`
  - `app.service.SemanticLayerService`
  - `app.mcp_server.main`
- 现有核心测试仍通过，尤其是：
  - `tests/test_mvp.py`
  - `tests/test_planning.py`
  - `tests/test_evidence.py`
  - `tests/test_bindings.py`

## 4. 执行顺序总览

建议按下面顺序推进：

1. 任务包 0：建立重构基线与目录骨架
2. 任务包 1：从 `service.py` 中抽 session 边界
3. 任务包 2：抽 StepRunnerRegistry 与 step runners
4. 任务包 3：引入 analysis IR 最小版本
5. 任务包 4：抽 compiler / executor 接缝
6. 任务包 5：拆 evidence engine
7. 任务包 6：建立 semantic runtime 最小骨架
8. 任务包 7：补测试、兼容层与文档收尾

这个顺序的核心逻辑是：

**先把“怎么跑”拆开，再把“跑什么”抽象出来。**

## 5. 任务包清单

---

## 任务包 0：重构基线与目录骨架

### 目标

先搭出最小目录骨架和兼容策略，避免后续每个 PR 都在边改代码边改结构。

### 新增目录

- `app/analysis_core/`
- `app/evidence_engine/`
- `app/semantic_runtime/`
- `app/session/`

### 具体动作

- [ ] 创建上述目录与 `__init__.py`
- [ ] 新增注释性空模块或最小骨架文件：
  - `app/analysis_core/ir.py`
  - `app/analysis_core/step_registry.py`
  - `app/analysis_core/compiler.py`
  - `app/analysis_core/executor.py`
  - `app/evidence_engine/scoring.py`
  - `app/evidence_engine/schemas.py`
  - `app/evidence_engine/pipeline.py`
  - `app/semantic_runtime/resolution.py`
  - `app/semantic_runtime/planner_context.py`
  - `app/session/session_manager.py`
- [ ] 在不破坏旧 import 的前提下，为后续抽取预留新位置

### 修改文件

- 不要求大改业务逻辑
- 只允许对 import / 注释 / 极少量 facade 做准备性调整

### 验收标准

- 目录骨架存在
- 旧测试不应因目录新增而失败
- 没有业务行为变化

### 测试范围

- `python3 -m unittest tests.test_mvp -v`
- `python3 -m unittest tests.test_evidence -v`

### 依赖

- 无

---

## 任务包 1：抽出 SessionManager

### 目标

先把 `service.py` 中最稳定、最不依赖复杂执行逻辑的部分抽出来：session CRUD。

### 迁移来源

- `app/service.py`

### 目标文件

- `app/session/session_manager.py`

### 具体动作

- [ ] 抽出 `create_session()`
- [ ] 抽出 `list_sessions()`
- [ ] 抽出 `get_session()`
- [ ] 抽出 `_assert_session_exists()`
- [ ] 让 `SemanticLayerService` 通过组合调用 `SessionManager`
- [ ] 保持返回结构不变

### 兼容要求

- `SemanticLayerService.create_session/list_sessions/get_session` 继续存在
- 对外 API 返回值不变化

### 验收标准

- session 相关逻辑不再直接耦合 step execution
- `SemanticLayerService` 通过内部 delegation 完成 session 读写

### 测试范围

- `tests/test_mvp.py`
- 任何覆盖 `/sessions` 接口的测试

### 依赖

- 任务包 0

---

## 任务包 2：抽出 StepRunnerRegistry 与独立 step runners

### 目标

把 `run_step()` 从“集中 if/dispatch + 巨型私有函数集合”改造成 registry + runner 模式。

### 迁移来源

- `app/service.py`

### 目标文件

- `app/analysis_core/step_registry.py`
- `app/analysis_core/step_runners/watch_time.py`
- `app/analysis_core/step_runners/qoe.py`
- `app/analysis_core/step_runners/ads.py`
- `app/analysis_core/step_runners/recommendation.py`
- `app/analysis_core/step_runners/generic.py`

### 具体动作

- [ ] 定义 `StepRunner` 协议或基类
- [ ] 定义 `StepRunnerRegistry`
- [ ] 将 `_run_compare_watch_time` 抽到独立模块
- [ ] 将 `_run_qoe_analysis` 抽到独立模块
- [ ] 将 `_run_ad_analysis` 抽到独立模块
- [ ] 将 `_run_recommendation_analysis` 抽到独立模块
- [ ] 将 `compare_metric/profile_table/sample_rows` 收拢到 generic runner 模块
- [ ] 让 `SemanticLayerService.run_step()` 改为委托 registry

### 兼容要求

- 现有 step type 名称不变
- `PlanningService.VALID_STEP_TYPES` 暂时不改名

### 验收标准

- `service.py` 不再直接承载所有 step handler 实现
- 新增 step 时不再需要继续往 `service.py` 里堆函数

### 测试范围

- `tests/test_mvp.py`
- `tests/test_planning.py`

### 依赖

- 任务包 1

---

## 任务包 3：引入 Analysis IR 最小版本

### 目标

建立一套最小但真实可用的 IR，先让 planning、step execution、routing 有共同语言。

### 目标文件

- `app/analysis_core/ir.py`
- `app/analysis_core/primitives.py`

### 具体动作

- [ ] 定义 `AnalysisRequest`
- [ ] 定义 `AnalysisStepIR`
- [ ] 定义 `ExecutionPlanIR`
- [ ] 定义 primitive step 枚举或 registry
- [ ] 为现有 step 增加 `step_type + params -> AnalysisStepIR` 转换器
- [ ] 在 `PlanningService` 中优先存储兼容结构，但增加 IR 转换入口
- [ ] 在 step runner 中接收 IR 或兼容适配对象，而不是直接散用 params dict

### 兼容要求

- `plans.steps_json` 暂时仍可保持原格式
- 旧 API 不要求立刻暴露 IR

### 验收标准

- 至少 planning 与 step execution 共用同一组 IR 类型
- 新增一个 primitive step 时，不需要从零定义一套新的 handler 参数约定

### 测试范围

- `tests/test_planning.py`
- 新增 IR 单测

### 依赖

- 任务包 2

---

## 任务包 4：抽 compiler / executor 接缝

### 目标

不要一步做完整 query compiler，而是先把 SQL 生成、engine 选择、执行调用从 runner 中抽出明确接口。

### 迁移来源

- `app/service.py`
- `app/routing.py`
- `app/dialect.py`

### 目标文件

- `app/analysis_core/compiler.py`
- `app/analysis_core/executor.py`

### 具体动作

- [ ] 抽出 `compile_step(ir, semantic_context, engine_type)` 接口
- [ ] 抽出 `execute_compiled(plan, engine)` 接口
- [ ] 将 `_resolve_engine()` 逻辑改为从 runner 中调用 executor/execution helper
- [ ] 将 `translate()` 的调用收敛到 compiler/executor 接缝，而不是散落在多个 runner 内
- [ ] 保留旧 SQL 逻辑，但迁移到 compiler 内部

### 兼容要求

- 先不改 QueryRouter 的公共行为
- 先不做 capability-aware routing

### 验收标准

- runner 不直接同时负责“生成 SQL + 选 engine + 执行 SQL + 组装结果”
- 编译与执行至少在代码边界上分离

### 测试范围

- `tests/test_mvp.py`
- `tests/test_dialect.py`
- `tests/test_bindings.py`

### 依赖

- 任务包 3

---

## 任务包 5：拆 Evidence Engine

### 目标

把 `evidence.py` 从“单文件工具集”变成“可扩展证据管线”。

### 目标文件

- `app/evidence_engine/schemas.py`
- `app/evidence_engine/scoring.py`
- `app/evidence_engine/extractors.py`
- `app/evidence_engine/synthesizers.py`
- `app/evidence_engine/pipeline.py`

### 具体动作

- [ ] 抽出 observation / claim / recommendation 的 typed schema
- [ ] 将 `score_confidence()` 移到 `scoring.py`
- [ ] 将 `make_observation()` 系列收敛为 extractor helpers
- [ ] 将 `synthesize_claims()` 拆到 synthesizer 模块
- [ ] 定义 `EvidencePipeline`，负责：
  - artifact -> observations
  - observations -> claims
  - claims -> recommendations
- [ ] 让 step runner 或 workflow runtime 调用 pipeline，而不是直接拼 evidence 逻辑

### 兼容要求

- 先保留 `app.evidence` 作为兼容 re-export 或 facade

### 验收标准

- 新增 observation 类型时，不需要继续在一个单文件里扩写所有逻辑
- evidence 逻辑至少被拆成 schema / scoring / synthesis 三层

### 测试范围

- `tests/test_evidence.py`
- `tests/test_mvp.py`

### 依赖

- 任务包 2

---

## 任务包 6：建立 Semantic Runtime 最小骨架

### 目标

这一步不追求完整 semantic execution，只做第一阶段需要的最小支撑：resolution 接口、planner context、catalog query 归位。

### 迁移来源

- `app/semantic.py`
- `app/catalog_query.py`

### 目标文件

- `app/semantic_runtime/catalog_service.py`
- `app/semantic_runtime/resolution.py`
- `app/semantic_runtime/planner_context.py`
- `app/semantic_runtime/semantic_models.py`

### 具体动作

- [ ] 保留现有 CRUD 行为
- [ ] 增加 `resolve_metric(metric_name)` 接口骨架
- [ ] 增加 `resolve_entity(entity_name)` 接口骨架
- [ ] 增加 `build_planner_context(session_id)` provider
- [ ] 将 `catalog_query.py` 的职责迁到 semantic runtime
- [ ] 为后续 metric grain / dimension constraints 预留字段和接口

### 兼容要求

- 旧 semantic CRUD API 保持不变
- 若短期不迁 schema，可先用内存/转换层表达扩展字段

### 验收标准

- planner 和 compiler 不必再直接依赖零散 metadata 查询
- semantic runtime 可提供统一 planner context

### 测试范围

- `tests/test_semantic.py`
- `tests/test_catalog_query.py`
- `tests/test_mvp.py`

### 依赖

- 任务包 3

---

## 任务包 7：测试、兼容层与阶段收尾

### 目标

确保第一阶段完成后，代码结构已经变好，但外部行为没有意外破坏。

### 具体动作

- [ ] 保留 `SemanticLayerService` facade
- [ ] 保留 `app.evidence` 兼容入口
- [ ] 为新目录补 `__init__.py` 暴露最小公共接口
- [ ] 补充新模块级单测：
  - IR model tests
  - step registry tests
  - evidence pipeline tests
  - semantic resolution skeleton tests
- [ ] 更新三份设计文档之间的交叉引用：
  - `omnidb-improvement-roadmap.md`
  - `omnidb-vnext-architecture-blueprint.md`
  - `omnidb-module-refactor-checklist.md`

### 验收标准

- 现有主要测试通过
- 新模块有基本单测
- 开发者能看懂“旧入口 vs 新边界”的关系

### 推荐回归命令

```bash
python3 -m unittest tests.test_mvp -v
python3 -m unittest tests.test_planning -v
python3 -m unittest tests.test_evidence -v
python3 -m unittest tests.test_bindings -v
python3 -m unittest tests.test_semantic -v
python3 -m unittest tests.test_catalog_query -v
```

### 依赖

- 任务包 4
- 任务包 5
- 任务包 6

## 6. 推荐的 PR 切分

为了控制风险，建议不要把第一阶段一次性提交，而是切成 6-8 个 PR：

1. PR1：目录骨架 + 兼容准备
2. PR2：SessionManager 抽取
3. PR3：StepRunnerRegistry + runner 拆分
4. PR4：IR 最小版本接入 planning/runner
5. PR5：compiler/executor 接缝
6. PR6：evidence engine 拆分
7. PR7：semantic runtime 最小骨架
8. PR8：测试/兼容层/文档收尾

## 7. 风险控制点

第一阶段最容易出问题的地方有三个：

### 7.1 抽模块时误改外部行为

控制方式：

- 先 facade，后替换调用点
- 先保返回结构，后重构内部对象

### 7.2 IR 设计过大

控制方式：

- 第一阶段只做最小 IR
- 不在第一阶段追求完整 planner language

### 7.3 evidence 拆分过度

控制方式：

- 先拆层次，不先追求完全插件化
- 保证 `tests/test_evidence.py` 先稳定

## 8. 第一阶段结束后的下一步

第一阶段完成后，才适合进入下一轮工作：

- 强化 planning validation
- 引入 re-planning
- 让 governance 进入 compile-time pipeline
- 建立 engine capability model

## 9. 结论

第一阶段不是“把所有设计都实现”，而是**把后续所有设计得以成立的内核边界先建立起来**。

一句话总结：

**第一阶段先拆 `service.py`，立 IR，拆 evidence，补 semantic runtime 骨架，其余都先延后。**
