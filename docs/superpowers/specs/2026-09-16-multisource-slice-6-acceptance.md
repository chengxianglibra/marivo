# Slice 6: ClickHouse Group A acceptance

## Scope and baseline

Base: `b47dcd8abf466da9dfecdc3ec53cffb7c8ff7222`. Slice 5 was already
committed and the worktree was clean at implementation start. This change enables
only the declared single-source, single-unversioned ordinary local MergeTree
Group A closure. No public API, Store, retained-import or advanced-method
activation is included. Consolidated installed-package acceptance remains Slice 8.

Inputs: Int8/16/32/64, Float32/64, String, Date, explicit Decimal precision at most
38, and Nullable variants. Enum, UInt, Int128/256, LowCardinality, FixedString,
Date32, timestamp/timezone, nested values, views and other table engines reject.
Timestamp conversion is explicitly deferred beyond the approved Slice 6 scope.
Timestamp rejection and engine timezone metadata probing are not conversion
acceptance; Section 5 of the owning plan now states that qualification boundary.

## Real execution evidence

Environment: ClickHouse 26.3.33.24, Ibis 12.0.0, clickhouse-connect 1.3.0,
PyArrow 25.0.1. Versions are diagnostic only. The dedicated qualification service
was started through its existing manager, which stops its Trino group first.

The server-enforced SELECT-only `analysis_reader` cannot create ordinary or
temporary tables, insert, or drop tables. Its effective `join_use_nulls=1` was
verified both by settings and a real unmatched LEFT JOIN returning NULL.
Admin fixture creation and insertion are separate from Dataset execution.

[Captured execution receipt](2026-09-16-multisource-slice-6-live-clickhouse.json):

- A 20,000-row fixture produced the independently expected ranked groups
  `(206000.0, "4")` and `(202000.0, "3")`.
- Exactly one primary query transferred two rows / 90 Arrow bytes.
- Other recorded roles: source timezone 1, settings check 1, source schema 2,
  finite-value assertion 1, validation batches 3. These are not counted as the
  primary query. Wire capture additionally includes driver version/timezone
  discovery and system-settings initialization.
- A separate 200,000-row Population action yielded four actual primary Native
  blocks: 20,000, 65,409, 65,409 and 49,182 rows. The capture selects the primary
  response identity and excludes metadata blocks. Their sum is exactly 200,000.
- Server scan/partition metrics are unavailable; no physical pruning claim is made.
- Validation and output queries remain separate. A controlled between-query
  source insertion succeeds with the changed output, without a snapshot gate.

Typed Dataset checks cover exact large integer identities, signed-width inputs,
Decimal38 values, floating input, NULL/empty aggregates, duplicate/NULL identities,
non-finite values even with empty output, deterministic ranking and failed
publication on overflow. Integer/Decimal SUM and COUNT lower through Decimal256
before checked conversion. COUNT lowering is checked by inspecting compiled SQL;
a synthetic UInt64 literal at 2**63 executes the corresponding Decimal256-to-Int64
conversion on the real server and raises overflow. This is a live conversion
boundary probe, not a UInt64 source-input or enormous-COUNT Dataset journey.
Unsigned source inputs remain rejected. A separate real adapter transfer preserves a composite
Decimal identity and a 300,000-character cell.

## Lifetime and recovery

Service-free tests exercise foreign statements, closed cursors, submission/fetch
errors, malformed values, partial streams, interruption before a response arrives,
and preservation of the original exception when cleanup also fails.

Real-driver fault injection occurs after actual response creation and covers lost
submit acknowledgement, fetch failure and cleanup uncertainty. Each prevents
publication and permits a subsequent clean execution. A partially consumed real
Native stream is explicitly interrupted and releases its owned local stream/cursor.
Driver close may drain unread response bytes; these tests do not promise immediate
cancel latency or prove remote termination.

The shared recovery suite now includes ClickHouse producer process death before
submission, after submission, during transfer and at writer boundaries; atomic
publication, cold-process readback, source-free binding hits and safe recovery
remain covered. Unresolved local publication/writer conflicts still block.

## Validation and independent review

- Focused default regression: **86 passed** across transport, scalar admission,
  dispatch, PostgreSQL admission and execution-economics tests.
- ClickHouse plus shared process-recovery Runtime: **57 passed, 20 skipped**;
  skipped cases are the unrequested MySQL/Trino opt-in services.
- The final connection-operation receipt and real large-cell addition were
  checked separately: **2 passed**.
- Touched-module typing and lint/import checks passed.
- `make check-agent`: **5,027 passed**, full typing/lint/import and API docs green.
- Site `npm run verify:content`: **343 required files verified**.
- `npm run build --ignore-scripts`: Astro **0 errors / 0 warnings**, **321 pages**
  built; the already-passed API build was reused. The separate install-script
  verifier also passed.
- Final test-only refinements: transport **18 passed** (including blocked fetch
  interruption), empty/all-NULL sum/count/min/max **2 passed**.

Commands:

```bash
make check-agent
MARIVO_CLICKHOUSE_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_lazy_clickhouse_runtime.py tests/test_lazy_scalar_recovery.py'
make test TESTS='tests/test_lazy_clickhouse_transport.py'
MARIVO_CLICKHOUSE_ANALYSIS_TEST=1 .venv/bin/pytest -q -n 0 -m runtime tests/test_lazy_clickhouse_runtime.py -k null_empty --tb=short
```

An independent subagent reviewed the actual increment against the baseline,
including real read-only probes. Its three findings were fixed and re-reviewed:
closed physical-type admission (Enum ordering), checked COUNT conversion, and
primary-only Native block evidence. Final verdict: **No findings**. The reviewer
independently reran the live synthetic COUNT-conversion boundary test (**1 passed**). This verdict
does not substitute for the final broad gate.

No commit, push, release, or production deployment was performed.

## Review follow-up

The subsequent review was assessed against the approved Slice 6 scope:

- Common scalar/Decimal admission and the explicit-source-Decimal rule now have
  one implementation. Per-backend wrappers retain PostgreSQL Boolean/timestamp
  and generic-source Decimal behavior, Trino's integer-width restrictions, and
  SQLite's separate type set. Independent comparison of 6,575 type strings per
  PostgreSQL/MySQL/Trino backend found no change in eligibility.
- The primary Native-stream observer is shared by transport evidence and failure
  injection. Local adapter imports are consolidated. The small-output and larger
  Population phases remain together to produce one receipt with separate counts.
- Dispatch rejection again asserts the exact method/backend/row-shape payload.
  Registration and no-retained-import assertions belong to the parameterized
  scalar admission test, rather than the PostgreSQL-only test.
- Section 5 explicitly defers timestamp conversion. The live engine timezone
  metadata probe now has direct assertions; it is not timestamp conversion proof.
  The UInt64 evidence wording distinguishes a live synthetic conversion from
  an unsupported unsigned source-input journey.
- Physical schema results preserve actual nullability; only admission's logical
  type comparison normalizes it. A real mixed-nullability table verifies both.
- The unused reserve callback remains part of the existing common adapter
  binding interface. No new callback mechanism or backend capability is added.

Final follow-up checks: `make check-agent` **5,040 passed** with full
 typing/lint/import/API-documentation stages green; ClickHouse Runtime **39 passed**.
Independent re-review: **No findings**. No commit or push was performed.
