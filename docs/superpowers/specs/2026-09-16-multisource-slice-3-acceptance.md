# Multi-datasource Slice 3: PostgreSQL Group A

Date: 2026-09-16.

Status: complete for the declared PostgreSQL Group A scope. Implementation,
independent review, focused Runtime, broad checks and site validation passed.
No release or commit is included.

## Implemented boundary

Baseline: `f43c86430203a2c5814ef2d4d0294a71a0581eed`, clean `lazy-dataset` checkout.

PostgreSQL now has an exact backend registration for a source-only, single-table,
unversioned Entity and its direct-column sum/count/min/max Metric closure.
Population, scoped observation, same-Entity dimensions, aggregation, filtering,
projection, deterministic ranking and limit share the existing lowerer. The full
logical dependency graph is checked, including predicates and Metric slices.
Mean, ratio, relationship paths, temporal buckets/version selection, sampling,
source-private state and retained imports are not enabled on PostgreSQL. Other
remote backends remain unregistered. Current type details live in the analysis
spec and latest bilingual workflow documentation.

The concrete adapter uses ordinary Ibis compilation, parameter substitution and
applicable pre-execution hooks, followed by psycopg named server cursors. The
cursor and its transaction belong to that stream; separate validation and output
reads do not promise a common snapshot. Binary transfer retains typed PostgreSQL
RECORD fields, avoiding text parsing and float conversion of identities. The
adapter closes actual cursors on exhaustion, explicit early close and failures.
Assertion rows detach from a closed driver cursor before validation decoding.

Schema lookup follows PostgreSQL relation OID resolution through `to_regclass`,
including the actual search path. The shared Runtime records the concrete
adapter's metadata SQL instead of an unexecuted DuckDB `DESCRIBE`. Timezone
queries use the datasource profile's exact SQL. Source query capture counts
validation, metadata and primary output separately. No version gate, execution
budget, source upload, alternate backend retry or public API/Store change is added.

## Real environment and journeys

The dedicated `marivo-multisource` Colima environment has a separate
`postgres-analysis` service, PostgreSQL 17.11, loopback port 15432, database
`analysis`, and its own volume. Existing ClickHouse continued running; the Trino
metadata service and volume were not modified. Versions are diagnostic facts.

Fixture administration uses `analysis_admin`; every Dataset uses
`analysis_reader`. The latter has no database CREATE/TEMP, schema CREATE or data
write privileges. Actual INSERT, CREATE TABLE and CREATE TEMP TABLE attempts were
rejected even with `default_transaction_read_only` disabled. Credentials remain
in the existing private environment file; declarations use an environment
reference. Disposable fixture tables are removed in `finally`.

- Decimal Top-N: two independently expected 30.75 groups, one primary query and
  two primary rows; assertions and physical schema queries counted separately.
  The source-side plan remains one stage. Source-free retained rollup gives 61.50.
- Large input: 20,000 source rows, four reducers and ten groups produce two primary
  rows (362 Arrow bytes) without local handoffs. Expected values use independent
  Python arithmetic.
  Actual primary SQL contains aggregation and Top-N. A separate read-only
  `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` diagnostic recorded five sequential
  scan nodes, each returning 20,000 rows in one loop, and two final output rows.
  Each scan recorded 148 shared buffer hits and no shared buffer reads; root
  shared buffer hits were 758. The measured diagnostic took 113.979 ms. These
  are post-Run diagnostic metrics, not telemetry from the original Run or a
  claim of physical partition pruning.
- Numeric and identity checks include signed integers above 2**53, float32/64,
  Decimal, NULLs, date scopes, empty results, binary RECORD identities, strings
  with delimiters/quotes/backslashes, and exact typed nested transport. Nested
  semantic source types remain unregistered; that adapter probe is not activation.
- Duplicate source identities fail before publication even when the output filter
  would select no rows. Repairing the fixture permits explicit re-execution.
- A fresh process reads the retained Artifact and reconstructs an exact binding
  hit with the source connection seam forbidden and the credential absent.
- Multi-batch transfer ignores the ambient Ibis default limit. Real cursor and
  transaction inspection verifies early/partial close, cancellation during an
  observed `PgSleep` execution and FETCH, decode failure and connection termination.
- Six producer-process exits cover before output submission, after named-cursor
  acknowledgement, transfer, before rename, before commit and after commit. Fresh
  processes reconcile failed pre-commit runs or read committed results without
  source queries, then successfully execute subsequent Session work.
- Injected writer faults preserve their original exception, release the entire
  checkpoint and never publish a partial Artifact. Failed cleanup reports unknown
  remote status without preventing locally safe subsequent work.

The [live PostgreSQL receipt](2026-09-16-multisource-slice-3-live-postgres.json)
records the disposable fixture, exact executed query texts, role counts, Arrow
transfer bytes, read-only privileges, independent expected output and diagnostic
versions. It also retains the complete separately labeled EXPLAIN plan.
Reproduce both with `.venv/bin/python -m tests.multisource_environment.postgres_receipt --output /tmp/marivo-postgres-receipt.json`.

## Verification

| Check | Result |
| --- | --- |
| Independent admission tests | 19 passed |
| Independent real PostgreSQL adapter tests | 14 passed |
| PostgreSQL Dataset journeys | 13 passed |
| PostgreSQL process recovery/cleanup journeys | 7 passed |
| Combined final PostgreSQL and affected DuckDB Runtime selection | 86 passed in 42.05s, one worker |
| Focused typing with explicit package bases | 14 files passed |
| Focused lint/format/import contracts | Passed |
| Focused disclosure and bilingual examples | 39 passed |
| Site content validation | 343 required files verified |
| Full site build | 321 pages, no Astro errors/warnings/hints; API docs and install-script checks passed |
| Final `make check-agent` | Lint/import contracts, typing of 336 source files, 4940 default tests in 70.77s and API docs passed |

Final Runtime command:

```sh
MARIVO_POSTGRES_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_lazy_postgres_runtime.py tests/test_lazy_postgres_execution_adapter.py tests/test_lazy_postgres_recovery.py tests/test_lazy_duckdb_execution_adapter.py tests/test_lazy_materialization_execution.py tests/test_lazy_backend_dispatch.py tests/test_lazy_dispatch_authority.py tests/test_lazy_execution_economics.py tests/test_lazy_retained_runtime.py' RUNTIME_WORKERS=1
```

The PostgreSQL tests are explicitly opt-in. Without
`MARIVO_POSTGRES_ANALYSIS_TEST=1`, skipped tests are not live acceptance evidence.
The fixture service is started explicitly, never by ordinary pytest execution.
No full release Runtime suite or object-store service was started.

## Independent review and corrections

The independent `review_slice3` agent reviewed the production and disclosure
changes, separately ran the 19 admission and 14 real adapter tests, and returned
**No findings** after correction. Dataset/process/broad/site results are team
verification, not claimed as independent reruns by that reviewer.

The review reproduced and closed three defects: text RECORD decoding lost typed
identity fields; predicate/slice dimensions could bypass the parse admission
check; and metadata resolution incorrectly assumed `current_schema()`. A final
SQL-recording mismatch was corrected by using the profile-owned timezone query.

The initial broad gate had four obsolete DuckDB-only expectations (4937 other
tests passed). Those tests now reflect the exact PostgreSQL activation while
preserving other backend rejections and PostgreSQL unsupported-method coverage.
The initial affected Runtime selection also exposed an existing stale patch of
`admission.open_native_backend`: that name is absent in baseline HEAD. The test
now forbids the actual `duckdb_execution.open_native_backend` owner; no production
compatibility alias was introduced. The final 86-case rerun passed.

This slice does not establish MySQL/SQLite/Trino/ClickHouse execution, advanced
method parity, installed-package release acceptance, or completion of older
Slice 9 journeys.


## Supplied review follow-up

The supplied review was checked against the actual owners and implementation.

| Suggestion | Decision |
| --- | --- |
| Merge registry declarations and Runtime factory dispatch | Keep the layers separate: registry owns pure eligibility/retained authority; Runtime owns concrete factories. Compiler imports must not acquire Runtime publication dependencies. Add a contract test across the closed BackendName set so missing factories or inconsistent retained availability fail. Neither map alone is a second source of method eligibility. |
| Conflicting DuckDB-only status | Fixed all three current-plan passages to state their historical Slice 0/1/2 boundaries; PostgreSQL activation refers to Slice 3 evidence. |
| Generic PostgreSQL errors and discarded run_ref | Adopted. Closed/foreign/parameter/preparation/schema/timezone/shape/unsupported-operation failures have distinct expected, received and repair fields. Binding and adapter errors retain run_ref without exposing source values. |
| Unreachable reject_retained factory | Removed. Runtime represents an absent factory as None and rejects it before reserving resources. |
| Tie values do not establish order | Adopted. The equal-30.75 journey now asserts channel order a, b; rank is checked if retained by projection. |
| Missing PostgreSQL rejection fields | Adopted. Unsupported mean asserts PostgreSQL backend, session.observe operator, Metric shape and actionable registered-backend repair, with no source connection. |
| Missing available scan metrics | Adopted. The reproducible receipt command now runs a separately labeled, read-only physical-plan diagnostic. |
| Split the whole execution protocol; rename admit_dataset; remove reserve; hide driver call chains | Deferred. Concrete adapters necessarily implement different preparation and timezone behaviors; reserve is the shared factory callback and PostgreSQL intentionally reserves no preparation. No incorrect execution or missing guard was demonstrated that warrants a cross-adapter interface migration in this slice. |
| Remove binary RECORD tests as scope creep | Not adopted. Flat RECORD conversion is necessary for admitted Entity identity, including compound keys. The small recursive/nested test guards this private codec, without admitting nested semantic source types or adding a public capability. |

Follow-up verification:

- `make check-agent`: lint/format/import contracts, typing of 336 source files,
  4946 default tests in 72.34s, and API documentation all passed.
- The same combined 86-case PostgreSQL/DuckDB Runtime selection passed in 43.38s,
  one worker. After the final RECORD error run-ref adjustment, all 14 PostgreSQL
  adapter Runtime cases passed again in 2.46s.
- Seven touched implementation/test/helper modules passed explicit-package typing.
  The worker's 20 admission checks, real tie-order test, and EXPLAIN receipt passed.
- Independent reviewer ran 34 dispatch/authority/error checks, then read back the
  final historical-status and run-ref fixes and returned **No findings**. It
  agreed with the registry/factory ownership distinction and checked this decision
  table against the implementation.
- `git diff --check` passed. No commit, push, release, other backend activation or
  service restart was performed for this follow-up.
