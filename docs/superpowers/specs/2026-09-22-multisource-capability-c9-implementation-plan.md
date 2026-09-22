# C9 Event and Lifecycle source capability

Date: 2026-09-22. Baseline: `lazy-dataset` after the local C8 commit; the worktree was clean before C9 edits. This is the independent implementation plan required by the [capability design](2026-09-16-multi-datasource-capability-completion-design-and-plan.md). The accepted [Subject, Event, and Lifecycle contract](2026-09-01-lazy-analysis-subject-event-lifecycle-design.md) owns business semantics.

## Exact target and admission order

Attempt PostgreSQL first, then SQLite, MySQL, Trino, and ClickHouse. For each backend, qualify Event journey matching before Lifecycle history, then Event funnel, time-to-event, selection, Lifecycle distribution/transition/dwell/violation reducers and selection, and complete retained funnel comparison/attribution. The DuckDB implementation is a regression oracle, not a substitute for independent expected rows. Open only an exact method/backend/shape cell with real read-only source execution and a complete proof. A failed or unavailable cell remains rejected before source I/O.

No new public methods, result shapes, Store revision, cross-source join, remote retained upload, approximate match, temporary source table, installed UDF, or source account write permission belongs to C9. Packaged skills require separate explicit approval before editing.

## Owners and implementation gates

1. `operators/registry.py` owns exact registrations; the concrete backend support modules own static shape and type rejection. `compiler/placement.py::source_binding` must prove one exact source domain before I/O. Keep truthful, specific pre-I/O diagnostics for cells that remain closed.
2. `compiler/event_sources.py` and `compiler/event.py::compile_event_match` own occurrence identity, participant versioning, ordering, assignment, tie ambiguity, dense rows, digest, and output proof. The current Event lowerer creates `CompiledRelationFence` preparations, while every remote adapter rejects `table_statement`. A read-only lowering may re-evaluate deterministic relations, but each assertion that certifies an output must observe the same source version as that output. Use a statement-level proof, a suitable transaction snapshot, or an equivalent source-side mechanism; separate earlier checks alone cannot certify later rows after concurrent writes. Independent output queries may observe different committed versions. Preserve the existing journey ID encoding, execute every required assertion even for empty primary output, and retain the atomic publication gate. SQL compilation alone is insufficient.
3. `compiler/lifecycle.py::compile_replay` owns ordered state replay and equal-time confluence. Its current recursive replay and confluence SQL are DuckDB-specific `SQLStringView` expressions. Implement a backend-specific read-only lowering that proves transition legality, complete interval/ledger/violation parts, and ambiguity rejection against each output's source version; no Python replay of transferred identity rows.
4. `materialization/admission.py` and the execution adapters own actual submission, streaming, validation ordering, source-private state, atomic publication, cancellation and cleanup. `event_publication.py`, `lifecycle_publication.py`, their reducer codecs and Store receipts remain the authority for cold recovery. Derived methods may use the existing complete retained local path only when its input contract admits it.

If a backend cannot meet a gate under query-only permissions, leave that exact cell closed and record the observed blocker. Do not add a generic remote Event switch or a second capability registry.

## Fixtures and verification

Reuse `tests/lazy_event_fixtures.py::make_event_registry`, `tests/lazy_lifecycle_fixtures.py::lifecycle_registry`, Event/Lifecycle runtime acceptance fixtures, and the existing per-backend methods/runtime fixtures. Extend them with source-owned tables containing independent hand-calculated journeys and histories: equal-time compatible and ambiguous order, out-of-order insertion, repeated/null identities, missing and duplicate participants, half-open windows, coverage censoring, empty output with failing validation, illegal and terminal transitions, and multiple starts with exclusive completion. Keep administrator fixture setup separate from read-only reader actions.

Capture actual reader-submitted SQL and roles independently of Runtime receipts. Check source-private identity transfer, full untruncated state, cold-process continuation, failure after preparation, and no publication on any required assertion failure. Record each cell as open, rejected with observed reason, or environment-blocked in the C9 acceptance file. The current negative admission test is `tests/test_lazy_c9_admission.py`.

```bash
make test TESTS='tests/test_lazy_event_compiler.py tests/test_lazy_event_contracts.py tests/test_lazy_lifecycle_contracts.py tests/test_lazy_c9_admission.py'
make runtime-test TESTS='tests/test_lazy_event_runtime_acceptance.py tests/test_lazy_lifecycle_runtime_acceptance.py tests/test_lazy_event_reducer_runtime_acceptance.py tests/test_lazy_lifecycle_reducer_runtime_acceptance.py tests/test_lazy_event_comparison_runtime.py' RUNTIME_WORKERS=1
MARIVO_POSTGRES_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_lazy_postgres_methods.py tests/test_lazy_postgres_runtime.py' RUNTIME_WORKERS=1
make runtime-test TESTS='tests/test_lazy_sqlite_methods.py tests/test_lazy_sqlite_runtime.py' RUNTIME_WORKERS=1
MARIVO_MYSQL_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_lazy_mysql_methods.py tests/test_lazy_mysql_runtime.py' RUNTIME_WORKERS=1
MARIVO_TRINO_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_lazy_trino_methods.py tests/test_lazy_trino_runtime.py' RUNTIME_WORKERS=1
MARIVO_CLICKHOUSE_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_lazy_clickhouse_methods.py tests/test_lazy_clickhouse_runtime.py' RUNTIME_WORKERS=1
make typecheck TYPECHECK_TARGETS='marivo/analysis'
make lint-agent LINT_TARGETS='marivo/analysis/operators/registry.py tests/test_lazy_c9_admission.py'
make check-agent
npm --prefix site run verify:content
npm --prefix site run build
git diff --check
```

Run live service groups only when their existing opt-in readers are available, and run Trino and ClickHouse serially. Align the current Analysis spec, native Help and dynamic contract guidance, examples, and both latest site languages for every newly opened cell. No push or publication is part of this phase.
