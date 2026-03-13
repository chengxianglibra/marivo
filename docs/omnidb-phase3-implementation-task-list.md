# OmniDB Phase 3 实施任务清单

## 1. 文档目的

本文档把 `docs/omnidb-phase3-design-doc.md` 继续拆解为可执行任务包。Phase 3 的核心目标是把 analysis IR、step taxonomy、evidence pipeline 与 execution orchestration 做成真实可复用的运行时能力，同时保持现有 HTTP / MCP / UI 兼容。

## 2. 实施原则

- 先稳住 contracts，再迁移调用方
- 先抽 primitive / composite taxonomy，再迁业务 workflow
- 先插件化 evidence，再进一步收口 facade
- 每个任务包完成后都需要：
  - 保持兼容入口
  - 增加相应模块级测试
  - 更新文档 / plan / checkpoint

## 3. 任务包总览

| Task | 目标 | 依赖 |
|---|---|---|
| P3-1 | Phase 3 设计文档与执行清单 | 无 |
| P3-2 | 增强 analysis IR | P3-1 |
| P3-3 | 拆分 primitive / composite taxonomy | P3-2 |
| P3-4 | 引入 composite workflow runtime | P3-3 |
| P3-5 | evidence pipeline 插件化 | P3-2 |
| P3-6 | 引入 execution orchestrator 并收口 facade | P3-4, P3-5 |

---

## 4. 任务包明细

### P3-1：设计文档与执行基线

#### 目标

固定 Phase 3 的问题定义、模块边界、迁移顺序与任务依赖，并把 session 执行状态切换到 Phase 3。

#### 交付物

- `docs/omnidb-phase3-design-doc.md`
- `docs/omnidb-phase3-implementation-task-list.md`
- `plan.md` Phase 3 版本
- SQL todos / deps

#### 完成标准

- Phase 3 范围和非目标明确
- Phase 3 任务包顺序明确
- Session 执行状态可追踪

---

### P3-2：增强 analysis IR

#### 目标

把 `AnalysisStepIR` 从兼容载体增强为更明确的分析主契约，同时继续支持 legacy step payload。

#### 拟改动模块

- `app/analysis_core/ir.py`
- `app/planning.py`
- `app/service.py`
- 新增或补充相关测试

#### 建议动作

- 增加 step category / semantic intent / expected artifact / evidence hints / execution hints
- 增加 legacy step -> rich IR 的转换辅助
- 保留旧字段与旧入口，避免破坏现有 planner / workflow 调用

#### 完成标准

- IR 可以表达基础语义意图与 artifact 预期
- legacy workflow 仍可正常转换为 IR
- 新 IR 有直接单测覆盖

---

### P3-3：拆分 primitive / composite taxonomy

#### 目标

把当前混合的 step type 划分成明确层次，让 generic primitive 与 domain workflow 不再混在同一个注册表语义里。

#### 拟改动模块

- `app/analysis_core/primitives.py`
- `app/analysis_core/step_runners/__init__.py`
- 必要时补充 `app/analysis_core/composites.py`

#### 建议动作

- 定义 primitive step 列表与 registry
- 定义 composite / workflow step 分类常量
- 让现有 generic step runner 先成为 primitive 的首批消费者

#### 完成标准

- primitive 与 composite 的边界明确
- 现有 generic step 能通过新 taxonomy 继续运行
- 对外 step_type 兼容不变

---

### P3-4：引入 composite workflow runtime

#### 目标

让复合分析流程可以通过数据驱动 spec 展开为一组 IR step，而不是继续靠 facade 内部硬编码 workflow 过程。

#### 拟改动模块

- `app/analysis_core/composites.py`
- `app/analysis_core/workflows/`
- `app/service.py`

#### 建议动作

- 定义 composite spec 数据结构
- 实现 composite expansion runtime
- 先让 `watch_time_drop` workflow 成为第一个落点

#### 完成标准

- composite workflow 可以展开成有依赖关系的 IR plan
- watch-time workflow 至少部分迁到 composite runtime
- 原工作流入口保持兼容

---

### P3-5：evidence pipeline 插件化

#### 目标

把 observation / claim 生成改造成 extractor / synthesizer 驱动的 pipeline。

#### 拟改动模块

- `app/evidence_engine/pipeline.py`
- `app/evidence_engine/extractors/`
- `app/evidence_engine/synthesizers/`
- `app/evidence.py`
- `app/service.py`

#### 建议动作

- 定义 `ObservationExtractor` / `ClaimSynthesizer` 合同
- 为 comparison / profile 等 artifact 建第一批 extractor
- 把现有 claim synthesis 逻辑迁到默认 synthesizer

#### 完成标准

- evidence pipeline 可按 extractor / synthesizer registry 运行
- 现有 evidence 结果结构保持兼容
- 新 pipeline 有独立单测

---

### P3-6：execution orchestrator 与 facade 收口

#### 目标

把当前分散在 facade 内的串联路径收敛到明确的 execution orchestrator 中，让 `SemanticLayerService` 进一步变薄。

#### 拟改动模块

- `app/execution/orchestrator.py`
- `app/service.py`
- 必要时补充 planner / routing / governance glue

#### 建议动作

- 增加统一执行入口，串联 semantic / governance / routing / compile / execute / evidence
- 让 facade 负责兼容接口与 persistence glue
- 清理重复 orchestration 逻辑

#### 完成标准

- 主要执行链有显式 orchestrator
- `SemanticLayerService` 继续作为 facade，但内部逻辑明显收敛
- 兼容 API / workflow 回归通过

## 5. 推荐执行顺序

建议按以下顺序推进：

1. P3-1 设计与执行基线
2. P3-2 增强 IR
3. P3-3 taxonomy 拆分
4. P3-4 composite runtime
5. P3-5 evidence pluginization
6. P3-6 execution orchestrator

这个顺序的核心逻辑是：

**先把分析主契约与 step 分层做出来，再迁 workflow 和 evidence，最后收口 facade。**

## 6. 当前实施状态

- [done] P3-1 设计与执行基线
- [done] P3-2 增强 analysis IR
- [in_progress] P3-3 primitive / composite taxonomy
- [pending] P3-4 composite workflow runtime
- [pending] P3-5 evidence pipeline 插件化
- [pending] P3-6 execution orchestrator 与 facade 收口
