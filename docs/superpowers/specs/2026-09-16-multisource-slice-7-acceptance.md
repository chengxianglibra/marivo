# Slice 7: independently implemented relational and date methods

## Baseline and ownership

Baseline: `6cd66f20df6d18556b2c3cd48b0ba79cd1a02db0` (Slice 6 committed,
clean worktree at implementation start). PostgreSQL, SQLite, MySQL, Trino and
ClickHouse each had a separate implementation agent owning its adapter, support
rules and method tests. The coordinating agent owned shared admission, placement
diagnostics, recovery coverage and documentation. A separate read-only review
agent reviewed the actual increments and integration; it did not implement them.
No commit, push, release or Slice 8 installed-package acceptance was performed.

## Qualified matrix

All entries retain each engine's existing physical types, table scope and read-only
account requirements. Every participating table is checked; same-source does not
mean arbitrary cross-datasource joins. These are individual shapes, not whole-group
or whole-engine feature flags.

| Method or shape | PostgreSQL | MySQL | SQLite | Trino Iceberg | ClickHouse MergeTree |
| --- | --- | --- | --- | --- | --- |
| Direct-column mean, weighted mean, ratio | enabled | enabled | enabled | enabled | enabled |
| Same-source relationships, contribution grain and missing coordinates | enabled | enabled | enabled | enabled | enabled |
| Native-date day/week/month/quarter/year, one unit | enabled | enabled | enabled | enabled | enabled |
| Exact snapshot membership | enabled | enabled | enabled | enabled | enabled |
| Native-date validity, closed-open, NULL end | enabled | enabled | enabled | enabled | enabled |
| Closed-closed validity and configured open-end sentinels | enabled | rejected | rejected | rejected | rejected |
| Non-Entity comparison and existing retained-axis attribution | enabled | enabled | enabled | enabled | enabled |
| Complete aggregate to local Forecast, Kendall and time discovery | enabled | enabled | enabled | enabled | enabled |

Mean preserves sum/count components; ratio preserves numerator and denominator
component state; weighted means preserve their numerator and weight sum. Rollup recomputes from that state, not from displayed
group means. Validation still checks zero-denominator policy, null/empty inputs,
identity and fanout, exact snapshot availability, and validity well-formedness and
overlap. Required validation failures prevent publication. Negative and zero
weights follow the existing method contract rather than a new backend policy.

Remaining unsupported entries have concrete admission reasons:

| Entry | Reason |
| --- | --- |
| Composed generic Decimal result | Existing owner requires resolved exact precision/scale; no invented rounding or precision |
| Timestamp/timezone/DST or string-parser time axes | Only native civil-date temporal lowering has been qualified |
| Multi-unit buckets, semantic calendars, cumulative and status-time folds | Additional temporal/state implementation is not qualified |
| Linear Metric graphs | No per-backend numerical and retained-state qualification in this slice |
| Hidden-axis expanded attribution | Requires an additional source preparation path |
| Sampling and Entity correlation | Require source-owned single evaluation/private pair preparation |
| Exact distinct, quantile/distribution, Entity candidates and source driver screening | Existing private-state and numerical contracts lack a read-only remote implementation |
| Event/Lifecycle | Exact ordering, replay and source-private preparation are not implemented remotely |
| Remote retained import | Existing execution authority does not permit upload/import |

A Decimal input whose composed result is already typed floating is separate from
an unresolved Decimal result. PostgreSQL and ClickHouse tests explicitly cover
that distinction. Diagnostic database/driver versions never determine eligibility.

## Implementation and observed execution

One pure scalar/relational admission owner checks the full dependency graph and
returns concrete rejection reasons to placement and adapters. The superseded
Group A admission implementation was removed, not retained as a parallel route.
No public callable, Store version, migration or retained-import permission changed.

PostgreSQL lowers boolean-to-integer conversions through int32 because native
PostgreSQL does not accept the emitted boolean-to-bigint cast. MySQL implements
Monday-based date weeks by subtracting the weekday interval. SQLite, Trino and
ClickHouse needed no new execution route. No adapter creates remote tables, uploads
rows or registers UDFs for the enabled methods.

[Execution receipts](2026-09-16-multisource-slice-7-execution-receipts.json)
contain 15 real Dataset reductions: three composed methods on each of five
backends. Each reduces five source rows to two grouped rows in one primary query;
mean/weighted mean/ratio transfer 74/90/106 Arrow bytes respectively, including
sufficient-state columns. Metadata and validation queries are recorded separately.
The independent grouped oracles are `[15, 35]`, `[17.5, 30]`, and `[15, 35]`;
retained rollups are `25`, `130/6`, and `25`. Runtime statement inventories do not
claim to include every driver-internal metadata call. ClickHouse also records
actual driver submissions. Wire bytes and server scan/pruning metrics are unavailable.

Other fixtures prove exact bucket coordinates, one-to-many cross-root ratio
without fanout inflation, missing and duplicate targets, version boundary errors,
complete four-row source histories before local kernels, and cold source-offline
reads. Administrative fixture writes are separate from SELECT-only execution.

## Validation and independent review

- PostgreSQL: 32 new methods plus 14 existing adapter tests, **46 passed**.
- SQLite: **29 new methods passed**, plus 14 existing Runtime tests passed;
  five strengthened exact-bucket assertions passed separately.
- MySQL: 29 new plus 24 existing Runtime tests, **53 passed**.
- Trino: 32 new plus 35 existing Runtime tests, **67 passed**; 15 transport tests passed.
- ClickHouse: **33 new methods passed**; existing Runtime and shared recovery,
  **67 passed, 28 skipped** (other backend opt-ins).
- Shared composed-state crash/cold tests cover transfer, before rename, before
  metadata commit and after commit on all five backends. Trino's four cases passed
  separately; ClickHouse's four are included in its recovery gate. The final
  PostgreSQL/MySQL/SQLite capture gate passed **24 tests, 8 skipped**, including
  their twelve crash/cold cases and additional composed-method receipts.
- Independent source-offline producer/consumer tests verify mean state on SQLite,
  MySQL, Trino and ClickHouse. PostgreSQL has new independent-process ratio-part
  recovery and cold rollup. Warm cache checks are not labeled cold evidence.
- Focused admission/dispatch regression: **94 passed**, followed by **15 passed**
  for the strengthened shared admission suite. The new bilingual mean-rollup
  example executes successfully.
- Production typing/lint and focused new-test typing passed.
- Final `make check-agent`: **5,055 passed**, 347 production modules typechecked,
  lint/import contracts and API documentation build passed.
- Site content verification: **343 required files**; Astro **0 errors / 0 warnings**,
  **321 pages built**; standard and Chinese installer output checks passed.
- Updated execution-economics Runtime regression: **6 passed**.

Independent review found and resolved a timestamp-axis admission bypass before
activation; reviewed the removal of unqualified linear admission, exact version
limits, the MySQL week expression and atomic part-publication recovery. Its final implementation and receipt review reported **No findings**. Broad and
site gates passed; the conclusion covers this bounded Slice 7, not release or Slice 8.

## Review follow-up

The exact datasource-binding rule belongs to
`compiler.placement.source_binding`, before source-stage placement. Five new
regressions use actual relationship graphs with identical connection fields but
distinct datasource identities, and verify rejection by both `source_binding`
and `place` without source I/O. Method admission does not duplicate that rule.

The shared admission module is now `operators.scalar_support`. Backend policy
flags centralize explicit Decimal-source and closed-open/NULL validity checks;
the registry uses one backend-to-rejection-resolver map for both eligibility and
diagnostics. Redundant `supports` delegates were removed. ClickHouse fixtures now
read credentials through an environment-level helper instead of importing a
MySQL fixture. PostgreSQL and MySQL retain their standalone setup-script behavior.
These changes preserve eligibility, rejection ordering and the early retained-input
guard. Group A test names still identify the baseline subset and remain unchanged.

Validation rerun for this follow-up: **101 focused tests passed**, **35 SQLite
methods and execution-economics Runtime tests passed**, and `make check-agent`
passed with **5,060 tests**, typing, lint/import contracts and API documentation.
Independent review reported **No findings**. The live remote-backend counts and
site checks above are prior implementation evidence, not reruns during this
follow-up; this refactor does not change source SQL or transport behavior.

## Reproduction

Tests never start services. Use the existing dedicated `marivo-multisource`
manager and reader setup; Trino and ClickHouse service groups must run serially.
All fixture tables are disposable and removed by their owners. Credentials remain
private environment references and are absent from receipts.

```bash
MARIVO_POSTGRES_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_lazy_postgres_methods.py tests/test_lazy_postgres_recovery.py'
make runtime-test TESTS='tests/test_lazy_sqlite_methods.py tests/test_lazy_sqlite_runtime.py'
MARIVO_MYSQL_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_lazy_mysql_methods.py tests/test_lazy_mysql_runtime.py' RUNTIME_WORKERS=1
MARIVO_TRINO_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_lazy_trino_methods.py tests/test_lazy_trino_runtime.py' RUNTIME_WORKERS=1
MARIVO_CLICKHOUSE_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_lazy_clickhouse_methods.py tests/test_lazy_clickhouse_runtime.py tests/test_lazy_scalar_recovery.py' RUNTIME_WORKERS=1
make check-agent
```

To recapture Runtime statements without extra queries, load
`-p tests.lazy_acceptance_capture` and set `MARIVO_SLICE9B_EVIDENCE_DIR` to a fresh
output directory. The existing variable name selects the observer, not an earlier
slice's acceptance authority. ClickHouse's method tests additionally accept
`MARIVO_SLICE7_RECEIPTS` for driver submissions.
