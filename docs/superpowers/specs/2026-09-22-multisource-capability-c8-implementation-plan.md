# C8: read-only advanced method completion

Date: 2026-09-22. Baseline: `ff9a4ad096` on `lazy-dataset`, with a clean worktree. This plan implements C8 of the [capability design](2026-09-16-multi-datasource-capability-completion-design-and-plan.md); C7 is accepted in `2026-09-22-multisource-capability-c7-acceptance.md`.

## Scope and entry gates

Probe and qualify PostgreSQL, MySQL, SQLite, Trino, and ClickHouse separately for Entity Pearson/Spearman correlation pair preparation, Entity outlier candidates, driver-axis screening, and hidden-axis expanded additive/component attribution. DuckDB is the existing oracle. Distribution Shapley, Event/Lifecycle, remote retained import, and cross-source joins are excluded. An unqualified method/backend cell remains rejected before source I/O with an explicit reason.

Before admission changes, establish each backend's read-only preparation mechanism. The admitted methods must preserve complete Entity identity, pairing, hidden-axis alignment, exact numeric rules, and private transfer boundaries without temporary tables, uploads, UDF installation, or raw Entity rows crossing as generic local inputs. If a proof is unavailable, record the physical blocker and keep that cell closed.

## Entity sampling removal (2026-09-22)

The user removed Entity sampling from the Analysis capability scope. Remove `population.sample`, `engine_sample`, their public type, compiler fences, Runtime preparation, private state, Help routes, and current documentation. Preserve the datasource inspection method `inspection.sample()`, which has a separate contract. Existing Store v5 field positions remain reserved so ordinary artifacts retain their format; non-null Entity sampling receipts fail decoding. No backend or DuckDB sampling admission remains. Bucket-aware physical sampling is a separate future design, not a replacement hidden behind the removed API.

## Owners and order

1. `operators/registry.py` and the five concrete `*_support.py` modules own exact method/backend admission. Static rejection must precede profile and source access.
2. `compiler/correlation.py::prepare_pairs` and `lower_correlate`, `entity_candidate.py::lower_entity_candidate`, `driver_candidate.py::lower_driver_candidate`, and `attribution.py::prepare_expanded_attribute`/`lower_expanded_attribute` own the existing method equations. `materialization/admission.py` owns source preparation order, proof collection, independent private parts, and failure cleanup. Preserve existing publication and Store contracts.
3. Start each method with SQLite if it can prove the needed path; otherwise use the first physically viable backend. Probe the others serially and open only evidenced cells. Sync `docs/specs/analysis/python-analysis-design.md`, native Help/contract guidance if its text changes, and the English/Chinese latest analysis workflow for opened cells. Do not edit packaged skills without separate explicit approval.

## Fixtures, acceptance, and commands

Reuse `tests/lazy_correlation_fixtures.py`, `tests/lazy_entity_candidate_fixtures.py`, existing driver/attribution Runtime acceptance fixtures, and each backend's `test_lazy_<backend>_methods.py` fixture. Add independent hand-calculated endpoints, source-offline cold continuation where the contract allows it, private-row transport guards, duplicate/NULL/empty/overflow counterexamples, and failure-after-preparation cleanup assertions. Administrator fixture setup and reader execution remain separate. Record each method/backend as open, rejected with observed reason, or environment-blocked in a dated C8 acceptance file; capture actual submitted SQL and roles for positive cells.

```bash
make test TESTS='tests/test_lazy_correlation_compiler.py tests/test_lazy_entity_candidate_compiler.py tests/test_lazy_driver_compiler.py tests/test_lazy_attribution_masks.py tests/test_lazy_backend_dispatch.py'
make runtime-test TESTS='tests/test_lazy_correlation_runtime.py tests/test_lazy_entity_candidate_runtime_acceptance.py tests/test_lazy_driver_runtime.py tests/test_lazy_attribution_runtime_acceptance.py' RUNTIME_WORKERS=1
MARIVO_POSTGRES_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_lazy_postgres_methods.py tests/test_lazy_postgres_runtime.py' RUNTIME_WORKERS=1
MARIVO_MYSQL_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_lazy_mysql_methods.py tests/test_lazy_mysql_runtime.py' RUNTIME_WORKERS=1
make runtime-test TESTS='tests/test_lazy_sqlite_methods.py tests/test_lazy_sqlite_runtime.py' RUNTIME_WORKERS=1
MARIVO_TRINO_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_lazy_trino_methods.py tests/test_lazy_trino_runtime.py' RUNTIME_WORKERS=1
MARIVO_CLICKHOUSE_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_lazy_clickhouse_methods.py tests/test_lazy_clickhouse_runtime.py' RUNTIME_WORKERS=1
make typecheck TYPECHECK_TARGETS='marivo/analysis'
make lint-agent LINT_TARGETS='marivo/analysis tests/test_lazy_correlation_compiler.py tests/test_lazy_entity_candidate_compiler.py tests/test_lazy_driver_compiler.py tests/test_lazy_attribution_masks.py'
make check-agent
npm --prefix site run verify:content && npm --prefix site run build
git diff --check
```

Run the live backend groups only when their existing opt-in services are available; Trino and ClickHouse remain serial. Do not claim an unrun cell as accepted. The original delivery excluded commit, push, and publish; the later explicit local commit request supersedes only the commit exclusion.
