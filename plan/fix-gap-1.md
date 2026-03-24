## 修复 GAP-1: 统一增量证据装配并补足 plan 执行回归

### Summary
- 采用 `SemanticLayerService` 服务内默认注入 `IncrementalSynthesizer` 的方案。
- 回归范围覆盖 direct service、plan execution 和 `synthesize_findings` 联动。

本次修复目标是消除 HTTP `create_app()` 路径与直接构造 service 路径的行为差异，确保 plan 执行中的 primitive step 会稳定写入 session evidence store，并能被后续 `synthesize_findings` 消费。

### Key Changes
- 调整 `SemanticLayerService` 的依赖装配方式。
  在构造函数中引入显式 `incremental_synthesizer` 参数，并将默认行为改为自动基于 `metadata_store` 创建 `IncrementalSynthesizer`；只有显式传入 `None` 时才关闭。
- 去掉 `app/api/app_factory.py` 中对 `service._incremental_synthesizer` 的手工后置赋值，避免 app 路径和非 app 路径产生不同默认行为。
- 保持 `PlanningService.execute_plan()` 继续通过 `service.run_step(...)` 执行 step，不新增 plan 专用的 evidence 写入分支。
- 保留 `run_step()` 中现有的后处理顺序：
  primitive step 执行完成后先触发 `IncrementalSynthesizer.process(session_id)`，再计算 `readiness` 和 `live_claims`。
- 在测试中明确覆盖“直接构造 service 也能增量写 claims”的前提，不再默认依赖 `create_app()` 注入。

### Tests
- 在 `tests/test_planning.py` 增加回归测试：
  直接构造 `SemanticLayerService(metadata, analytics)`，执行包含 `compare_metric` 或 `aggregate_query` 的 plan，断言执行后 session 存在 observations/claims，且不再是全程 `live_claims` 为空的状态。
- 增加 direct service 回归测试：
  不经 `create_app()`，直接 `service.run_step(...)` 后应返回 `readiness`、`live_claims`，并在元数据库中写入 observation 与 tentative claim。
- 增加 plan + synthesis 联动测试：
  plan 先执行若干 primitive steps，再执行 `synthesize_findings`，断言它消费到了前序 evidence，而不是因为无 tentative claims 返回空结果。
- 保留一个显式关闭模式测试：
  `SemanticLayerService(..., incremental_synthesizer=None)` 时，primitive step 不进行增量综合，`synthesize_findings` 仍走 fallback 路径。
- 验证命令：
  `.venv/bin/pytest tests/test_planning.py -v`
  `.venv/bin/pytest tests/test_incremental_synthesis.py -v`
  `.venv/bin/pytest tests/test_evidence.py -k incremental -v`

### Public Interfaces
- `SemanticLayerService.__init__` 新增 `incremental_synthesizer` 参数，作为显式依赖控制点。
- 不修改 REST API、plan payload、step payload、response schema。

### Assumptions
- 本次关键 tradeoff 已确定：
  通过 service 默认装配来统一行为，而不是要求所有调用方都显式注入。
- 本次主要修 GAP-1；若补充 “plan + synthesis” 测试时遇到其他独立缺陷，应单独处理，不混入本次修复目标。
- 本次修复不包含 BUG-1、BUG-2、BUG-3，也不处理 GAP-2；这些问题需要单独变更和回归验证。
