# 测试目录分层迁移计划

本文档记录 `tests/` 目录分层迁移方案。目标是在不改变测试语义的前提下，将根目录下混合放置的测试逐步迁移到与 `marivo/` 架构边界一致的子目录中。

## 背景

当前 `tests/` 已经存在以下子目录：

- `tests/core/`
- `tests/runtime/`
- `tests/runtime/intents/`
- `tests/runtime/semantic/`
- `tests/transports/`
- `tests/transports/mcp/`
- `tests/contracts/`
- `tests/adapters/`
- `tests/config/`
- `tests/profiles/`
- `tests/local/`

但根目录仍包含大量跨领域测试文件。迁移后，测试位置应表达其主要验证边界，降低查找、维护和定向执行成本。

## 执行记录

截至当前迁移轮次，已完成以下落地项：

- T01 Core：已迁入 `tests/core/`，定向校验通过。
- T02 Contracts：已迁入 `tests/contracts/`，定向校验通过。
- T03 Adapters：已迁入 `tests/adapters/`，定向校验通过。
- T04 Runtime Evidence：已迁入 `tests/runtime/evidence/`，定向校验通过。
- T05 Runtime Semantic：已迁入 `tests/runtime/semantic/`，定向校验通过。
- T06 Runtime Session 与 Execution：已迁入 `tests/runtime/`，定向校验通过。
- T07 Runtime Intent：原有 `tests/runtime/intents/` 布局保持，已随 `tests/runtime` 目录校验覆盖。
- T08 HTTP Transport：已迁入 `tests/transports/http/`，定向校验通过。
- T09 MCP Transport：已迁入 `tests/transports/mcp/`，定向校验通过。
- T10 CLI Transport：已迁入 `tests/transports/cli/`，定向校验通过。
- T11 Config：已迁入 `tests/config/`，定向校验通过。
- T12 Profiles：原有 `tests/profiles/` 布局保持，已随 config/profiles/local 组合校验覆盖。
- T13 Local：原有 `tests/local/` 布局保持，已随 config/profiles/local 组合校验覆盖。
- T14 Integration/System：已迁入 `tests/integration/`，定向校验通过。
- T15 Test Support：`tests/shared_fixtures.py` 等 helper 暂留根目录；helper 自测迁入 `tests/support/`。
- T16 收尾：根目录仅保留需拆分的混合边界测试 `tests/test_none_user_paths.py` 以及共享 helper 模块。

迁移过程中顺手归位的补充项：

- `tests/test_api_package.py` -> `tests/transports/http/test_api_package.py`
- `tests/test_timing_middleware.py` -> `tests/transports/http/test_timing_middleware.py`
- `tests/test_osi_models.py` -> `tests/contracts/test_osi_models.py`
- `tests/test_ports_init.py` -> `tests/contracts/test_ports_init.py`
- `tests/test_identity.py` -> `tests/core/test_identity.py`
- `tests/test_redaction.py` -> `tests/core/test_redaction.py`
- `tests/test_observability.py` -> `tests/runtime/test_observability.py`
- `tests/test_soft_invalidation.py` -> `tests/runtime/evidence/test_soft_invalidation.py`
- `tests/test_shared_fixtures.py` -> `tests/support/test_shared_fixtures.py`

保留项：

- `tests/test_none_user_paths.py` 混合 identity、local runtime、datasource registry、HTTP no-user 行为，需要先拆分再迁移。
- `tests/shared_fixtures.py`、`tests/semantic_test_helpers.py`、`tests/finding_identity_testutil.py` 继续作为跨目录共享 helper。

## 迁移原则

- 一个子模块对应一个独立任务，避免一次性大规模路径变更。
- 优先使用 `git mv` 移动文件；除必要 import 修正外，不改变测试语义。
- 暂时保留根目录共享 helper：
  - `tests/shared_fixtures.py`
  - `tests/semantic_test_helpers.py`
  - `tests/finding_identity_testutil.py`
- 共享 helper 如需迁移到 `tests/support/`，应作为最后的单独任务，并保留兼容 re-export。
- 定向测试使用仓库入口：`make test TESTS='...'`。
- 不使用裸 `pytest`、`python`、`mypy` 或 `ruff`。
- 拆分大测试文件时，关注 `pytest-xdist --dist=loadscope` 下的类级 fixture 复用和执行时间变化。

## 目标目录约定

| 目录 | 放置范围 |
| --- | --- |
| `tests/core/` | 纯领域逻辑、semantic IR、calendar、scope、canonical refs、finding/proposition、intent registry |
| `tests/contracts/` | DTO、ids、values、errors、envelope、generated models、port protocol contract |
| `tests/adapters/` | DuckDB、SQLite/MySQL metadata、repositories、storage、metadata DDL/schema |
| `tests/runtime/` | 运行时编排、session、execution、step registry、跨 core/adapter 的服务行为 |
| `tests/runtime/evidence/` | evidence pipeline、extractor registry、finding extractor、artifact commit boundary |
| `tests/runtime/semantic/` | semantic service、compiler/executor、calendar data runtime、import/export、normalization |
| `tests/runtime/intents/` | 非 HTTP 的 intent runner 与 intent 执行逻辑 |
| `tests/transports/http/` | HTTP API、middleware、OpenAPI、HTTP model、app factory HTTP 边界 |
| `tests/transports/mcp/` | MCP adapter、tool parity、stdio/http MCP e2e、async bridge |
| `tests/transports/cli/` | CLI 命令、输出、exit code、doctor/runtime 命令 |
| `tests/config/` | config、calendar config、metadata config、profile field |
| `tests/profiles/` | profile resolver、server factory、profile path/default |
| `tests/local/` | local runtime factory、marivo init、本地 session rebuild、本地并发 |
| `tests/integration/` | 跨层端到端行为、OSI/AOI e2e、sessions、datasources、lineage、proposal/proposition run |

## 独立迁移任务

### T01 Core 语义与领域测试

目标目录：`tests/core/`

范围：

- additivity 与 additivity capabilities
- semantic IR 与 analysis IR
- calendar policy/alignment
- scope resolution、value expression、time scope
- canonical finding、canonical refs、family contract
- proposition normalizer/context 中纯 core 逻辑部分

候选文件：

- `tests/test_additivity_capabilities.py`
- `tests/test_analysis_ir.py`
- `tests/core/test_calendar.py`
- `tests/test_canonical_finding.py`
- `tests/test_canonical_refs.py`
- `tests/test_family_contract.py`
- `tests/test_primitives.py`
- `tests/test_proposition_normalizer.py`
- `tests/test_time_scope_resolution.py`
- `tests/test_value_expr.py`

校验：

```bash
make test TESTS='tests/core'
```

### T02 Contracts 契约测试

目标目录：`tests/contracts/`

范围：

- contract ids、values、errors、domain DTO
- envelope 与 generated models
- port protocol contract
- contracts package 初始化

候选文件：

- `tests/test_contracts_domain.py`
- `tests/test_contracts_errors.py`
- `tests/test_contracts_ids.py`
- `tests/test_contracts_init.py`
- `tests/test_contracts_values.py`
- `tests/test_envelope.py`
- `tests/test_generated_models.py`
- `tests/test_ports_protocols.py`

校验：

```bash
make test TESTS='tests/contracts'
```

### T03 Adapters 存储与外部适配测试

目标目录：`tests/adapters/`

范围：

- DuckDB/Trino adapter
- SQLite/MySQL metadata store
- repositories 与 evidence repositories
- metadata dialect、schema bootstrap、storage roundtrip

候选文件：

- `tests/test_adapters.py`
- `tests/test_dialect.py`
- `tests/test_evidence_repositories.py`
- `tests/test_metadata_dialect.py`
- `tests/test_metadata_schema_bootstrap.py`
- `tests/test_mysql_metadata_ddl.py`
- `tests/test_mysql_metadata_store.py`
- `tests/test_osi_storage_roundtrip.py`
- `tests/test_repositories.py`
- `tests/test_storage.py`

校验：

```bash
make test TESTS='tests/adapters'
```

### T04 Runtime Evidence 测试

目标目录：`tests/runtime/evidence/`

范围：

- finding extractor registry
- observe/detect/compare/decompose/correlate/forecast extractor
- evidence pipeline family behavior
- artifact commit boundary
- canonical downstream、assessment recompute/evaluation context

候选文件：

- `tests/test_artifact_commit_boundary.py`
- `tests/test_assessment_evaluation_context.py`
- `tests/test_assessment_recompute.py`
- `tests/test_canonical_downstream.py`
- `tests/test_compare_decompose_extractor.py`
- `tests/test_correlate_test_forecast_extractor.py`
- `tests/test_detect_extractor.py`
- `tests/test_evidence_pipeline_family_behaviors.py`
- `tests/test_finding_extractor_registry.py`
- `tests/test_finding_identity_helper.py`
- `tests/test_observe_extractor.py`
- `tests/test_observe_lineage_extraction.py`

校验：

```bash
make test TESTS='tests/runtime/evidence'
```

### T05 Runtime Semantic 测试

目标目录：`tests/runtime/semantic/`

范围：

- semantic v2 service 中非 HTTP 的服务层行为
- compiler/executor
- calendar data runtime
- composites 与 semantic normalization
- ref boundary、time axis metadata

候选文件：

- `tests/test_calendar_data_runtime.py`
- `tests/test_compiler_executor.py`
- `tests/test_composites.py`
- `tests/test_normalization.py`
- `tests/test_ref_boundary.py`
- `tests/test_semantic_v2_service.py`
- `tests/test_time_axis_metadata.py`

校验：

```bash
make test TESTS='tests/runtime/semantic'
```

### T06 Runtime Session 与 Execution 测试

目标目录：`tests/runtime/`

范围：

- session state
- execution feedback/substrate
- step registry
- replay recovery 中运行时部分
- proposition registration/seeding/refresh 中非端到端部分

候选文件：

- `tests/test_execution_feedback.py`
- `tests/test_execution_substrate.py`
- `tests/test_proposal_refresh_run.py`
- `tests/test_proposition_context.py`
- `tests/test_proposition_registration.py`
- `tests/test_proposition_seed_registry.py`
- `tests/test_proposition_seeding_run.py`
- `tests/test_replay_recovery.py`
- `tests/test_session_state.py`
- `tests/test_step_registry.py`

校验：

```bash
make test TESTS='tests/runtime'
```

### T07 Runtime Intent 测试

目标目录：`tests/runtime/intents/`

范围：

- 已有 intent runner 测试保持在该目录
- 迁入非 HTTP 的 intent 执行测试
- 保持 `_runner_fixtures.py` 在 intent 子目录内

候选文件：

- 现有 `tests/runtime/intents/test_*_runner.py`
- 从根目录迁入经确认不依赖 HTTP client 的 intent 执行测试

校验：

```bash
make test TESTS='tests/runtime/intents'
```

### T08 HTTP Transport 测试

目标目录：`tests/transports/http/`

范围：

- HTTP intent API
- semantic API
- middleware
- OpenAPI fragments/schema quality
- HTTP API models
- app factory HTTP 边界

候选文件：

- `tests/test_api_models_base.py`
- `tests/test_app_factory_metadata.py`
- `tests/test_intent_api.py`
- `tests/test_middleware.py`
- `tests/test_openapi_fragments.py`
- `tests/test_openapi_schema_quality.py`
- `tests/test_semantic_v2_api.py`
- 现有 `tests/transports/test_http_*`

校验：

```bash
make test TESTS='tests/transports/http'
make test TESTS='tests/transports/test_http_aoi_intents.py tests/transports/test_http_detect_intent.py tests/transports/test_http_diagnose_intent.py tests/transports/test_http_forecast_intent.py'
```

后续可将现有 `tests/transports/test_http_*` 一并迁入 `tests/transports/http/`。

### T09 MCP Transport 测试

目标目录：`tests/transports/mcp/`

范围：

- MCP adapter
- MCP tool parity
- stdio/http MCP e2e
- async bridge 与 user passthrough

候选文件：

- `tests/test_mcp_aoi_adapter.py`
- 现有 `tests/transports/mcp/test_*.py`

校验：

```bash
make test TESTS='tests/transports/mcp'
```

### T10 CLI Transport 测试

目标目录：`tests/transports/cli/`

范围：

- CLI 命令
- CLI 输出格式
- exit code
- doctor/runtime 命令

候选文件：

- `tests/test_cli.py`

校验：

```bash
make test TESTS='tests/transports/cli'
```

### T11 Config 测试

目标目录：`tests/config/`

范围：

- config model 与默认值
- calendar config
- metadata config
- config profile field

候选文件：

- `tests/test_calendar_config.py`
- `tests/test_config.py`
- 现有 `tests/config/test_config_profile_field.py`

校验：

```bash
make test TESTS='tests/config'
```

### T12 Profiles 测试

目标目录：`tests/profiles/`

范围：

- profile resolver
- server factory
- profile path/default

候选文件：

- 现有 `tests/profiles/test_resolver.py`
- 现有 `tests/profiles/test_server_factory.py`
- `tests/test_none_user_paths.py` 中 profile 相关部分，如需拆分再迁移

校验：

```bash
make test TESTS='tests/profiles'
```

### T13 Local 模式测试

目标目录：`tests/local/`

范围：

- local runtime factory
- local init
- local commit step result
- local concurrency
- local session rebuild

候选文件：

- 现有 `tests/local/test_*.py`
- `tests/test_sessions.py` 中明确属于 local runtime 的部分，如需拆分再迁移

校验：

```bash
make test TESTS='tests/local'
```

### T14 Integration/System 跨层测试

目标目录：`tests/integration/`

范围：

- OSI/AOI e2e
- sessions、datasources 跨层行为
- observe/compare lineage reuse
- proposal refresh 与 proposition seeding 的端到端流程
- MySQL live integration 可进一步放到 `tests/integration/mysql/`

候选文件：

- `tests/test_datasources.py`
- `tests/test_e2e_osi_aoi.py`
- `tests/test_mysql_metadata_integration.py`
- `tests/test_observe_compare_lineage_reuse.py`
- `tests/test_sessions.py`
- 需要保留跨层语义的 `tests/test_proposal_refresh_run.py`
- 需要保留跨层语义的 `tests/test_proposition_seeding_run.py`

校验：

```bash
make test TESTS='tests/integration'
```

MySQL 测试如依赖 live DSN，应保持 `mysql` marker，并按现有集成测试约定执行。

### T15 Test Support 收尾

目标目录：`tests/support/` 可选

范围：

- 评估共享 helper 是否需要迁移：
  - `tests/shared_fixtures.py`
  - `tests/semantic_test_helpers.py`
  - `tests/finding_identity_testutil.py`
- 如果迁移，先在根目录保留 re-export，避免同一 PR 中大规模修改所有 import。
- 后续再单独清理 re-export。

校验：

```bash
make test TESTS='tests'
```

### T16 文档与全量校验收尾

范围：

- 更新必要的测试目录说明。
- 检查是否还有根目录测试文件应迁未迁。
- 跑全量测试与 lint。

校验：

```bash
make test
make lint
```

## 建议执行顺序

1. T01 Core
2. T02 Contracts
3. T11 Config
4. T12 Profiles
5. T13 Local
6. T03 Adapters
7. T04 Runtime Evidence
8. T05 Runtime Semantic
9. T06 Runtime Session 与 Execution
10. T07 Runtime Intent
11. T08 HTTP Transport
12. T09 MCP Transport
13. T10 CLI Transport
14. T14 Integration/System
15. T15 Test Support
16. T16 文档与全量校验

## 每个任务的完成标准

- 目标文件已移动到对应子目录。
- 必要 import 已修正，优先保持 `from tests.shared_fixtures import ...` 这类稳定绝对导入。
- 未引入测试语义或产品行为变化。
- 对应目录或文件的定向测试通过。
- 若拆分大文件，确认类级 fixture 没有被意外重复构建导致明显性能退化。
- 若迁移 MySQL 或 slow 测试，确认 marker 与执行说明仍然清晰。

## 风险与注意事项

- `pytest.ini` 已配置 `testpaths = tests` 与 `pythonpath = .`，子目录迁移不会影响 pytest 收集根。
- 当前 `addopts = -n auto --dist=loadscope`，拆分测试类可能改变并行分配和 fixture 生命周期。
- 根目录 helper 被多个测试引用，迁移 helper 应作为单独收尾任务。
- 有些文件同时覆盖多个层次，迁移前应按“主要验证边界”判断；如文件内部确实混合多个边界，优先拆分后再迁移。
- 计划迁移期间，避免重命名测试类和测试函数，降低 review 噪音。
