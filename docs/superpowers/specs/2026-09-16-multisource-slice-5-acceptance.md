# Multi-datasource Slice 5: Trino Iceberg Group A

Date: 2026-09-16.

Status: complete for the declared Trino Iceberg Group A scope. Real execution,
independent review, focused Runtime, broad checks and site validation passed.

## Implemented boundary

Implementation began on `7f2f740341c54a37afd314131bb145f2f674b84a` plus the
existing Slice 4 working tree. During this task that prior work was committed
separately as `a473271d7672a649ea9d819ef3b168c7fcd73188`; that commit is the
review base. The reviewed Slice 5 target is that existing commit plus the
uncommitted tracked and untracked working-tree changes, not a claimed Slice 5
commit. This task does not commit, push or release changes.

Trino is registered for a single datasource and a single unversioned ordinary
Iceberg base table. Source-only Population, direct-column sum/count/min/max,
native DATE scopes, same-Entity dimensions, aggregation, filtering, projection,
rank and limit use the existing method registry and scalar projection. Physical
inputs are int32/int64, float32/64, VARCHAR, DATE and explicit Decimal up to
precision 38. Generic Decimal source declarations remain rejected; derived
Decimal Metric types use their explicit source schema. Declared floating columns
must be finite or NULL, with a separate source assertion and non-finite result
rejection. Other connectors, views, metadata tables, nested/Boolean/timestamp
inputs, relationships, sampling, version selection, advanced methods and remote
retained imports remain disabled. ClickHouse remains unenabled.

Metadata verifies the actual connector and table kind under the selected
catalog/schema qualification. Tuple, dotted and schema-only overrides retain the
existing datasource meaning for both metadata and compiled scans. Ordinary Ibis
compilation and hooks are retained. There is no snapshot-ID visitor, version
admission, source snapshot promise, compile-count gate, upload, remote DDL or
execution-budget enforcement. The public API and Store format do not change.

The concrete adapter registers every cursor before execute, including the first
page wait and metadata/assertion submissions. Scalar driver rows rebuild the
unchanged Arrow identity schema without pandas, JSON or floating conversion.
Cancellation targets owned cursors before connection close; failed close remains
owned for final cleanup. Closed cursors cannot submit or fetch again. Original
execution errors survive cleanup errors; safe local recovery does not require
proof of remote query termination. Publication occurs after source cleanup.

## Real environment and evidence

Live acceptance is explicitly opt-in and was run with
`MARIVO_TRINO_ANALYSIS_TEST=1`; a default-suite skip does not constitute live
validation. Completion refers to the actual environment and commands recorded
here, not automatic live coverage in default CI or certification of other versions.

The existing isolated `marivo-multisource` Trino/Iceberg/JDBC/local-warehouse
fixture was used. The user authorized stopping its ClickHouse service without
restoring it. PostgreSQL/MySQL analysis services were preserved. Trino 483,
Ibis 12.0.0, trino-python-client 0.337.0, Arrow 25.0.1 and SQLGlot 30.8.0 are
reproducibility diagnostics, not runtime version gates.

Fixture writes use `qualifier`; Dataset reads use `analysis_reader`. Server file
access rules allow the latter read-only Iceberg/system access. Actual CREATE,
INSERT, DELETE and DROP attempts were rejected. This loopback HTTP fixture tests
server authorization, not authentication against identity spoofing; production
requires its own authenticated read-only identity. No credentials are recorded.

The [live receipt](2026-09-16-multisource-slice-5-live-trino.json) captures a
20,000-row source grouped to two ranked rows, with independent expected values
`[(206000, "4"), (202000, "3")]` and 90 Arrow bytes. There is exactly one primary
query. Separate operations include a timezone query, three metadata statements,
a floating-finiteness source check and three semantic assertions. The cold driver
parameter-capability probe is an extra HTTP POST (`EXECUTE IMMEDIATE 'SELECT 1'`),
not another Dataset primary. The receipt preserves both cursor SQL/parameter
arguments and actual HTTP POST SQL, plus available server statistics. Wire bytes
are unavailable; no physical partition-pruning claim is inferred from SQL.

A second Dataset transfers all 20,000 rows with unique wide string dimensions.
The receipt records actual data-bearing HTTP pages per query, independently of
Arrow batching. It observes 39 primary data pages. A separate read-only driver
probe preserves compound integer/date/Unicode identities, exact Decimal cells,
1-row batches despite an ambient Ibis default limit, and a valid 2,000,000-character
cell. That probe is separate from the real single-table Dataset journeys.

Runtime coverage includes null/empty input, duplicate/null identities even when
an output filter selects no rows, non-finite source and overflowing aggregate
rejection, deterministic rank ties, all four reducers, exact numeric boundaries,
namespace overrides, non-Iceberg/view rejection and a controlled update between
validation and output. The latter succeeds without shared-snapshot enforcement.

Transport failure tests inject malformed JSON into a real HTTP response, a lost
HTTP GET, cursor cancellation acknowledgement loss and final disconnect failure.
They assert no publication, original errors, honest unknown remote status and
subsequent Session work. Slow fetch and blocked execute/fetch are deterministic
scheduling injections, not claims of an observed network stall. A live partial
multi-page stream sends cancellation to its owned query. Six producer-death
points cover before submission, acknowledgement, transfer, rename, commit and
post-commit recovery. Fresh processes verify atomic output and source-free cold
Artifact/binding reads; writer failures retain explicit retry semantics.

## Independent review

The independent `review_trino_plan` agent reviewed the owning contract, initial
plan and production increment against the refreshed Slice 4 base. Preliminary
reviews found no production defects; they identified two acceptance gaps that
were addressed: auditing HTTP POSTs to include the driver's cold parameter probe,
and testing a sole final-disconnect failure after otherwise successful reads.
Final independent review returned **No findings** against the current production,
test and disclosure increment. The reviewer independently ran the default transport
suite (15 passed), checked whitespace, and inspected the real 31-case Trino and
87-case shared Runtime logs plus the source-reduction/multi-page receipt. The
reviewer did not duplicate live service load; those Runtime executions are main-agent
evidence. The final owning-plan status and broad/site results were then synchronized.

## Verification

- Targeted default admission, dispatch, transport and bilingual documentation:
  75 passed before the final additional cursor guard tests.
- Real Trino plus shared MySQL/SQLite recovery, adapters and execution economics:
  87 passed, one intentional SQLite instance of a MySQL-only cancellation test
  skipped. No requested live service suite was skipped.
- Final Trino Runtime: **31 passed**, including the final closed-cursor guard,
  real owned-query cancellation, HTTP GET fault and sole finish failure.
- Independent default transport: **15 passed**.
- `make check-agent`: **4998 passed**, formatting/lint/import contracts passed,
  typing passed for **345 source files**, and API docs built successfully.
- Site: `npm run verify:content` verified **343** required files;
  `npm run build` completed **321** pages, Astro diagnostics and install-script
  verification. Pagefind's existing Chinese stemming notice is informational.
- Final `git diff --check` and receipt JSON validation passed.

Final commands (real service gates run serially):

```sh
MARIVO_TRINO_ANALYSIS_TEST=1 MARIVO_MYSQL_ANALYSIS_TEST=1 \
MARIVO_SLICE5_RECEIPTS=/tmp/marivo-slice5-receipts \
make runtime-test TESTS='tests/test_lazy_trino_runtime.py tests/test_lazy_scalar_recovery.py tests/test_lazy_scalar_execution_adapter.py tests/test_lazy_execution_economics.py' RUNTIME_WORKERS=1
MARIVO_TRINO_ANALYSIS_TEST=1 MARIVO_SLICE5_RECEIPTS=/tmp/marivo-slice5-receipts \
make runtime-test TESTS='tests/test_lazy_trino_runtime.py' RUNTIME_WORKERS=1
make check-agent
npm --prefix site run verify:content
npm --prefix site run build
```

Initial failures were repaired and are not counted as passing evidence. They
included stale blanket Trino rejection/disclosure assertions, dotted namespace
lowering and the derived generic Decimal Metric admission distinction. A manual
SQL probe was changed to explicit Decimal casts because Ibis parsing of bare
Decimal SQL literals loses scale; SQL expressions are not part of the admitted
single-table direct-column Dataset surface.

No full release Runtime suite, MinIO, package publication or installed-package
release acceptance is included. Later method expansion and Slice 8 remain separate.

## External review follow-up

Accepted coverage and accuracy corrections:

- Added positive and negative `DECIMAL(38,6)` SUM overflow and signed BIGINT SUM
  overflow journeys. Each uses individually representable values whose sum exceeds
  the result range. Tests require the original Trino `NUMERIC_VALUE_OUT_OF_RANGE`,
  a failed Run with no Artifact or leftover Parquet/resource, and exact successful
  same-Session execution after explicit source correction. No production behavior
  change was needed.
- Corrected the three shared scalar test-module descriptions to include Trino and
  added concise docstrings to its admission helpers. Numeric fixture declarations
  are shared locally between the existing exact-value and new overflow tests.
- Clarified the committed review base versus the uncommitted reviewed increment,
  and the actual opt-in live-evidence boundary.

Not adopted as defects:

- Updating plan status and linking evidence did not change the Task 5 acceptance
  requirements. Completion is supported by actual Runtime/broad/site logs and the
  independent review, not by the status line itself.
- The Slice 4 base commit exists; Slice 5 being uncommitted does not invalidate it.
- Stopping the local qualification ClickHouse service was explicitly authorized
  by the user and included in the approved implementation plan. It does not enable
  or implement the ClickHouse backend.
- Per-backend factories/type predicates and concrete cursor cleanup retain their
  existing ownership. Similar short clauses, connection literals and the mandatory
  unused factory argument do not demonstrate a behavioral defect; no cross-backend
  extraction or resource-hook abstraction was introduced solely for deduplication.

The independent reviewer checked the disputed evidence/scope claims and the
follow-up tests separately, returning **No findings**. Follow-up validation:

- Four new exact-overflow cases: **4 passed** on real Trino.
- Full updated Trino module: **35 passed**, no skips.
- Default admission/transport tests: **36 passed**.
- Focused formatting/lint/import contracts and admission-module typing passed;
  `git diff --check` passed.

```sh
MARIVO_TRINO_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_lazy_trino_runtime.py' RUNTIME_WORKERS=1
make test TESTS='tests/test_lazy_trino_transport.py tests/test_lazy_scalar_admission.py'
make lint-agent LINT_TARGETS='marivo/analysis/operators/trino_support.py tests/lazy_scalar_source_fixtures.py tests/test_lazy_scalar_admission.py tests/test_lazy_scalar_recovery.py tests/test_lazy_trino_runtime.py'
make typecheck TYPECHECK_TARGETS='marivo/analysis/operators/trino_support.py'
```

This follow-up changes coverage and explanatory text only. The earlier broad/site
results remain the initial implementation evidence; those gates were not rerun
or relabeled as new follow-up executions.
