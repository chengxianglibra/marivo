# Factum 问题清单（来源：oneservice 集群分析，2026-03-24）

本次通过实际分析任务（调查 oneservice 集群昨天 vs 一周前指标变化与用户体验风险）发现以下问题。

---

## P0 — 功能性 Bug（核心流程损坏）

### BUG-1: `synthesize_findings` 因 schema 不一致崩溃

**现象**

plan 执行阶段 synthesize_findings 步骤失败，报错：

```
table recommendations has no column named entity_patch_json
```

直接对 session 调用 `POST /sessions/{id}/steps/synthesize_findings` 也返回空结果（0 claims / 0 recs / 0 gaps），无报错但实际无法工作。

**根因假设**

某次提交（疑为 G-5 entity patch 功能）在代码层引入了对 `recommendations.entity_patch_json` 的读写，但未同步更新 `app/storage/schema.py` 中的 DDL，导致已初始化的数据库实例缺少该列。

**影响范围**

- Plan 模式下 synthesize_findings 步骤必定失败
- 直接调用时无报错但结果为空，静默失效

**修复方向**

1. 在 `app/storage/schema.py` 的 `recommendations` 表 DDL 中补充 `entity_patch_json TEXT` 列
2. 编写数据库迁移脚本（ALTER TABLE 或重建），保证已有数据库实例兼容
3. 补充集成测试：`synthesize_findings` 在有 live_claims 的 session 上必须返回非空 claims

---

### BUG-2: `GET /sessions/{id}/evidence` 返回 500

**现象**

```
HTTP/1.1 500 Internal Server Error
Internal Server Error
```

**根因假设**

evidence 端点在序列化阶段尝试读取 `entity_patch_json` 字段，与 BUG-1 同根因。

**影响范围**

整个 evidence graph 不可访问：observations、claims、causal edges、recommendations 均无法通过 API 获取，agent 无法检查任何已产出的结构化证据。

**修复方向**

与 BUG-1 同批修复。修复后补充 `GET /sessions/{id}/evidence` 的集成测试，验证至少包含一个 step 的 session 返回合法 JSON。

---

### BUG-3: `GET /sessions/{id}/reflection-context` 返回 500

**现象**

```
HTTP/1.1 500 Internal Server Error
```

**根因假设**

reflection-context 依赖 evidence graph 构建，级联失败于 BUG-2。

**影响范围**

agent 无法通过标准 handoff 接口获取状态摘要（readiness_signal、tentative_claims、evidence_gaps），必须绕过走原始 aggregate_query，丧失 Factum 的增量推理价值。

**修复方向**

与 BUG-1/BUG-2 同批修复。

---

## P1 — 证据管道失效（核心设计未达预期）

### GAP-1: Plan 执行步骤不向 session evidence store 写入数据

**现象**

- plan 中 10 个步骤（compare_metric × 8、aggregate_query × 2）全部 `completed`
- 整个执行过程中 `live_claims` 始终为空
- 直接调用 `synthesize_findings` 确认：0 tentative claims 可供 promote
- 对比：在 session 上直接执行同类 step（不经 plan），artifact 正常产出，但 live_claims 仍为空

**根因假设**

Plan executor 执行步骤时，可能走了与直接 step 不同的代码路径，导致：

- `IncrementalSynthesizer` 未被触发，或
- step 结果未写入 session 级别的 `observations` 表，或
- `compare_metric` 步骤在 plan 路径下产出的 artifact 没有经过 extractor 处理

**影响范围**

Plan 模式下 evidence-driven 工作流完全失效：
- readiness 维度永远不更新
- causal checker 无原料可运行
- synthesize_findings 无 claims 可 promote
- 推荐动作 `suggested_action` 始终是 `continue_exploring`，失去决策价值

**修复方向**

1. 确认 `PlanningService.execute()` 调用步骤时是否经过与 `SemanticLayerService.execute_step()` 相同的 post-step 钩子（incremental synthesis 触发点）
2. 如果 plan executor 有独立执行路径，确保该路径也调用 `IncrementalSynthesizer.process()`
3. 增加集成测试：plan 执行完成后，session `live_claims` 数量应大于 0

---

### GAP-2: Plan 对象执行后不暴露步骤结果

**现象**

`GET /sessions/{id}/plans/{plan_id}` 的每个已完成步骤中，`result` 字段为 null 或空。无法通过 plan API 检索步骤输出（rows、artifact_id、summary 等）。

**影响范围**

- Plan 从"可复查的分析记录"退化为"只有状态的执行日志"
- agent 必须重新运行等价的 aggregate_query 才能看到数据，造成重复查询和额外延迟
- 尤其影响 compare_metric：步骤显示 `completed`，但完全不知道"比出了什么"

**修复方向**

plan 步骤记录中应持久化 `result`（至少包含 `summary`、`artifact_id`、`rows` 预览前 N 行）。可参考直接执行 step 时的响应结构，写入 plan steps 的 `result_json` 列。

---

## P2 — 可用性问题

### UX-1: `trino` 包未包含在默认依赖中

**现象**

首次执行 Trino 表的 aggregate_query 时报错：

```
{"detail": "No module named 'trino'"}
```

需手动 `pip install trino` 后才能正常使用。

**修复方向**

在 `pyproject.toml` 或 `setup.py` 的依赖列表中加入 `trino`，或在 `TrinoAnalyticsEngine` 初始化时给出可操作的错误提示（"请运行 pip install trino"）。

---

## 问题汇总

| ID | 优先级 | 分类 | 一句话描述 |
|----|--------|------|-----------|
| BUG-1 | P0 | Schema 缺陷 | `entity_patch_json` 列缺失导致 synthesize_findings 崩溃 |
| BUG-2 | P0 | 级联失败 | evidence 端点 500，evidence graph 完全不可访问 |
| BUG-3 | P0 | 级联失败 | reflection-context 端点 500，agent handoff 失效 |
| GAP-1 | P1 | 管道断裂 | Plan 执行步骤不触发 IncrementalSynthesizer，live_claims 永远为空 |
| GAP-2 | P1 | 数据丢失 | Plan 步骤执行结果不持久化，执行后无法检索输出 |
| UX-1  | P2 | 依赖缺失 | `trino` 包未在默认依赖中，首次使用报模块缺失 |

---

## 修复优先级建议

**第一批（同一 PR）**：BUG-1 + BUG-2 + BUG-3

三个问题同根，修复 schema + 迁移脚本即可解除级联。修复后跑 `tests/test_incremental_synthesis.py` 和 `tests/test_causal_integration.py` 验证回归。

**第二批**：GAP-1

这是最影响 Factum 核心价值的问题。Plan 模式是推荐的 agent 工作流，但 evidence 管道在该模式下不工作，等于核心差异化功能在生产路径上静默失效。

**第三批**：GAP-2 + UX-1

GAP-2 提升 plan 的可用性；UX-1 改善初次上手体验，改动小影响大。
