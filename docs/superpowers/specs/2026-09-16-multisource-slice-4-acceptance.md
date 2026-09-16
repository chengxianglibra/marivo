# Multi-datasource Slice 4: MySQL and SQLite Group A

Date: 2026-09-16.

Status: complete for the declared MySQL and SQLite Group A scopes. Real execution,
independent review, focused Runtime, broad checks and site validation passed.

## Implemented boundary

Baseline: clean `lazy-dataset` checkout at
`7f2f740341c54a37afd314131bb145f2f674b84a`, containing the completed Slice 3.
No commit, release or push is included in this slice.

MySQL and SQLite now have separate concrete execution adapters and exact Group A
registrations. A shared pure predicate preserves the existing PostgreSQL closure
while each engine owns its type predicate. The full dependency closure is checked,
including projected-away Metrics, predicates and Metric slices. Source-only,
single-table, unversioned Population and direct-column sum/count/min/max support
scoped observations, dimensions, aggregation, filtering, projection, ranking and
Top-N. The owning [analysis design](../../specs/analysis/python-analysis-design.md#mysql-and-sqlite-group-a)
contains the precise table/type scope. No relationship, temporal bucket, sampling,
source-private-state method or remote retained import is activated.

The private scalar projection pass expands identity structs throughout the Ibis
relation graph, including projections, grouping, joins, sorting and windows.
Transport reconstructs the original Arrow schema from exact scalar leaves;
identity meaning, public Dataset types, definition fingerprints and Store format
are unchanged. Ordinary Ibis parameter binding and applicable preparation hooks
remain in use. No pandas intermediate, source upload, remote DDL, alternate backend
retry, execution budget or version admission is introduced.

MySQL uses MySQLdb SSCursor, with direct fetchmany and strict conversion-warning
checks. Early close can drain unread responses. Numeric overflow remains a real
failure rather than a zero/saturated successful result. SQLite uses native cursor
fetches, source storage-class and canonical-date validation, and query-only mode.
Its DDL AST collation check examines declared columns rather than matching SQL
text. Undeclared physical JSON/BLOB columns do not expand the admitted data surface
or wrongly reject the declared scalar Dataset.

Both adapters preserve the original execution exception when cleanup also fails.
MySQL cancellation closes the exact owned connection and releases tracked streams;
its driver can report error 2006 when closing the now-disconnected cursor. That
response is not proof of remote termination. SQLite interruption is tested during
both execute and fetch. Local publication and recovery remain independent of
unknown remote read status.

## Real environments and journeys

The dedicated `marivo-multisource` environment has a new `mysql-analysis` service,
MySQL 8.4.11, digest-pinned image and separate volume. Host endpoint is
`127.0.0.1:23306`; port 13306 was found to belong to the separate Slice 9d VM and
was not reused. Existing PostgreSQL and ClickHouse services were preserved.
SQLite is 3.53.1, Ibis is 12.0.0 and the MySQL client library is 3.4.9.
Diagnostic versions do not gate production execution.

Fixture administration uses root only on the disposable MySQL service. Every
Dataset uses `analysis_reader`, with SELECT-only grants. Actual INSERT, DELETE,
ordinary CREATE and temporary CREATE attempts are denied. Credentials remain in
the private environment file and declarations use an environment reference.
UUID fixtures are removed in finally blocks. SQLite uses per-test persistent
files with isolated connections and query-only production execution.

- Both engines execute scoped grouped Top-N, all four reducers, Population and
  unaggregated Metric identity journeys, NULL and empty inputs, deterministic
  ties and complete multi-batch transfer. Composite identities preserve strings,
  dates and integers above 2**53 without encoding through float or JSON.
- MySQL adds exact Decimal/integer aggregate decoding, binary case/Unicode/trailing
  space checks and rejection of wrong engines, unsigned types and unsupported
  collations. SQLite rejects mixed storage classes, invalid dates, non-binary
  collation and integer overflow. Zero dates and zero years are rejected before
  output publication, including when an output filter would select no rows.
- The large-input journeys independently compute expected results from 20,000
  source rows. MySQL transfers two rows / 93 Arrow bytes; SQLite transfers two
  rows / 90 bytes, each with one primary query containing GROUP BY and LIMIT.
  Actual statement roles separate metadata, physical checks, semantic assertions
  and primary output. Server scan metrics are unavailable and are not reported
  as zero or inferred from transfer volume.
- Both engines cover partial cursor close, decode failure, actual closed-connection
  reads, writer faults and original-exception preservation. Six producer exits
  cover before output submission, after acknowledgement, transfer, before rename,
  before commit and after commit. Fresh processes reconcile pre-commit failures
  or read committed output, then execute subsequent Session work.
- Cold binding reconstruction forbids source connection creation and removes the
  MySQL credential environment reference. The exact Artifact is recovered without
  source queries. Retained rollup continues through the existing native reader.

Live receipts: [MySQL](2026-09-16-multisource-slice-4-live-mysql.json) and
[SQLite](2026-09-16-multisource-slice-4-live-sqlite.json). Runtime validation-operation
counters include a metadata resolution operation; the separate executed-statement
counts disclose the concrete metadata statements rather than treating an operation
counter as an exact server-query count.

## Independent review and corrections

The independent `review_task4_plan` agent reviewed the implementation and
owning/disclosure changes, ran 47 MySQL/SQLite Runtime and adapter cases, checked
seven new production modules with typing, and returned **No findings** after fixes.
It independently probed compound identity operations, numeric boundaries, dates,
partial MySQL cancellation and the final declared-column/DDL-check changes.
Recovery suites and broad gates are main-agent verification, not independent reruns.

Review reproduced two material defects and their corrections:

1. Native MySQL floating SUM overflow can serialize as zero without warnings;
   a later cast can saturate it to the largest finite value. The private lowering
   adds floating zero before conversion. Finite values retain their results;
   positive/negative overflow now raises native error 1690. Independent tests
   cover decimals represented as floats, DBL_MAX, subnormal values, negative zero,
   cancellation, NULL/empty input and overflow followed by cancellation.
2. SQLite accepted year zero, while MySQL zero/invalid dates could decode as NULL.
   Source checks now enforce valid Gregorian dates before publication. Valid leap
   days and maximum dates remain accepted.

Additional regressions cover quoted BINARY collation versus a misleading SQL
string/default, undeclared physical columns, conversion warnings, and driver
cleanup errors after a successful connection close. Earlier failing expectations
were updated only where the new exact registrations or bilingual example count
changed the owning contract.

## Verification

| Check | Result |
| --- | --- |
| Independent MySQL/SQLite Runtime and adapter selection | 47 passed in 9.03s, plus independent boundary probes |
| Final combined MySQL/SQLite/PostgreSQL/DuckDB Runtime selection | 160 passed in 85.62s; one inapplicable SQLite parameter of the MySQL cancellation test skipped |
| Supplemental MySQL float32 Dataset journey | 1 passed in 1.73s |
| Final `make check-agent` | Lint/import contracts, typing of 343 source files, 4960 default tests in 79.42s and API docs passed |
| Site content validation | 343 required files verified |
| Full site build | 321 pages; 0 errors, warnings or hints; API docs and install-script verification passed |
| Whitespace and receipt validation | `git diff --check` passed; both JSON receipts parse and contain no resolved credential |

No live MySQL or PostgreSQL service suite was skipped. The single skipped Runtime
parameter exercises a MySQL-specific cancellation test with the SQLite fixture;
SQLite execute/fetch interruption is covered by separate passing tests.

The final Runtime rerun includes the correction to the MySQL cancellation test:
a driver 2006 response after connection close is allowed only with the adapter
and stream closed and the tracking set empty. The last broad rerun also includes
the SQLite DDL parser typing correction. No failed run is counted as acceptance.

```sh
MARIVO_MYSQL_ANALYSIS_TEST=1 MARIVO_POSTGRES_ANALYSIS_TEST=1 \
MARIVO_SLICE4_RECEIPTS=/tmp/marivo-slice4-live-receipts \
make runtime-test TESTS='tests/test_lazy_mysql_runtime.py tests/test_lazy_sqlite_runtime.py tests/test_lazy_scalar_execution_adapter.py tests/test_lazy_scalar_recovery.py tests/test_lazy_postgres_runtime.py tests/test_lazy_postgres_execution_adapter.py tests/test_lazy_postgres_recovery.py tests/test_lazy_duckdb_execution_adapter.py tests/test_lazy_materialization_execution.py tests/test_lazy_backend_dispatch.py tests/test_lazy_dispatch_authority.py tests/test_lazy_execution_economics.py tests/test_lazy_retained_runtime.py' RUNTIME_WORKERS=1
```

The MySQL/PostgreSQL flags require explicitly started services. A skipped service
suite is not live acceptance. This slice does not establish Trino/ClickHouse
activation, later method groups, installed-package release acceptance or older
Slice 9 completion. No full release Runtime suite or MinIO service was started.

## External review follow-up

The supplied review was checked against the complete workspace, including the
owning specs, bilingual site changes and live receipts omitted from its input.

Accepted corrections:

- Identity projection guards and invalid date/non-integral or non-finite Decimal
  decode guards now raise structured `MaterializationError` with stage, repair and
  the owning Run reference. Native driver and Arrow failures retain their original
  exceptions; cleanup does not replace them.
- A service-free default suite covers guard failures, exact large-integer/date
  reconstruction, truncated driver rows, cleanup and single-submission behavior.
- Numerical/non-finite lowering now belongs to each concrete engine adapter.
  The shared identity projection has no backend switch.
- Both 20,000-row journeys retain independent arithmetic and add DuckDB as a
  comparator. Receipts now capture actual driver SQL and parameter tuples, including
  metadata placeholders. Source row counts come from the fixture inputs. These
  are acceptance-only captures, not new public statistics or Store fields.
- Slow real execution and controlled blocked-fetch cases cover retained ownership
  and successful continuation. The fetch gate is an injected scheduling boundary
  around a real cursor, not evidence of an actual network stall. Truncated-row
  coverage is fault injection, not deliberate corruption of engine storage pages.
- The environment README and runtime/site documentation now describe incremental
  SQLite stepping, full-cell decoding, row-based batches and engine-side sorting.
  Driver audit inspected MySQLdb's single query/fetch calls and default-disabled
  reconnect; the adapters do not invoke reconnect or replay. SQLite native busy
  waits and schema-change reprepare retries remain possible, separate from
  application retries; the README links their native SQLite contracts.

Not adopted:

- Removing source date/storage scans would admit invalid source semantics. SQLite
  source storage/date checks were explicitly requested; MySQL invalid dates can
  otherwise decode as NULL. Full-source validation is deliberate and disclosed.
- Validation undercount is stale: `engine_check.*` already increments the runtime
  validation-operation counter. Each receipt records five operations and separately
  records all concrete SQL statements, including metadata (eight MySQL, seven
  SQLite submissions in these journeys).
- Timezone relabeling would misrepresent the existing contract: the generic probe
  explicitly reports `system_fallback`, while DATE compilation uses `civil_date`
  with `read_timezone=None`. No timezone-bearing type is admitted.
- Missing real execution/readonly/spec evidence was a review-input gap. The tests,
  receipts, owning specs and both localized pages are present. Prior evidence is
  retained above rather than presented as a new run.
- Similar binding wrappers, typed backend aliases, shared protocol arguments,
  registry/factory ownership and Ibis execution hooks do not establish a defect.
  No general registry refactor or speculative abstraction was introduced. Python
  caches the optional driver module import; no per-cursor module reload occurs.

Independent follow-up review reported **No findings** in the code, with 14 default
transport checks and 12 targeted real Runtime cases independently passing. The
main-agent default transport suite subsequently added two exact-struct/truncated-row
cases and passed all 16. The initial follow-up engine selection passed 59 cases;
its one skip is the inapplicable SQLite parameter of MySQL cancellation.

Final follow-up gates: `make check-agent` passed with 4976 default tests in
100.46s, 343 typed source files, lint/import contracts and API documentation. The
combined Runtime command above passed 165 cases in 84.74s, with only the same
inapplicable SQLite/MySQL cancellation parameter skipped. Updated receipts include
actual parameters and independently asserted validation-operation counts.

Site follow-up: `npm run verify:content` verified 343 required files;
`npm run build` passed (321 pages; zero errors, warnings or hints), including API
docs and install-script checks. Final whitespace and credential-free receipt
checks passed. The independent reviewer closed the remaining receipt/disclosure
findings after reading the updated evidence.
