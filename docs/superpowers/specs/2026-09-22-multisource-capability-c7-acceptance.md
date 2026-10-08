# C7 implementation evidence (2026-09-22)

Status: the five-backend C7 capability matrix is implemented and accepted. The SQLite/MySQL Entity identity gap is closed by typed scalar SQL identity fields with the unchanged private Arrow struct. Workspace changes are not committed.

## Live execution matrix

| Backend | Direct-measure exact distinct | Entity-identity exact distinct | Exact linear distribution | Full methods/runtime suite |
| --- | --- | --- | --- | --- |
| SQLite | passed | passed | passed | 59 passed |
| PostgreSQL | passed | passed | passed | 54 passed |
| MySQL | passed | passed | passed | 62 passed |
| Trino | passed | passed | passed | 76 passed |
| ClickHouse | passed | passed | passed | 81 passed |

Every passed cell ran against a real declared read-only datasource, with separate administrator fixture setup. Trino used the Iceberg `analysis` catalog. Trino and ClickHouse were not run simultaneously. The direct-measure journeys used an independent hand-counted key set; distribution journeys checked independently calculated endpoints. SQLite's whole-scope distinct tenant set has 4 members; EU/US/NULL regions have 3/2/1 members. Trino and ClickHouse now run the complete shared distinct row set, including NULL tenant and channel values and `shared` in January and February; both focused journeys passed after this coverage change. SQLite checked q=0.25/0.5/0.9 interpolation for `[5,9]` as 6/7/8.6. PostgreSQL, Trino and ClickHouse Entity journeys used native struct identity keys and proved retained rollup after credentials or the source table were removed. SQLite distribution additionally executed a cold comparison of January and February retained results after source removal, yielding deltas -3 and 4.

The original SQLite Entity probe failed on `STRUCT(...)` SQL, and the MySQL probe failed on Ibis `StructColumn` compilation. Neither engine needs a native struct for exact identity equality. The new compiler path filters each typed identity field, deduplicates the coordinate and identity columns, and counts that member relation for the endpoint. The private part still uses the existing typed Arrow struct and retained contract. Real SQLite/MySQL Entity journeys passed endpoint, private part and source-offline cold rollup checks; real composite-key journeys distinguished `A`, `a` and `a ` under the governed backend text comparisons. MySQL's compiler also compiled primary, part and membership integrity queries for single and composite Entity identities without `STRUCT(...)` SQL.

This is a recorded deviation from the implementation plan's §2/§3.5 assumption that source SQL must carry a native struct. The user approved the typed scalar SQL approach after reviewing the first-principles identity requirement. The persisted part shape, identity signature and exact equality contract remain unchanged; only the private execution representation differs.

ClickHouse distribution initially failed because its expression-widening pass placed a `Cast` inside `WindowFunction.func`, which Ibis requires to be an analytic or reduction. The adapter now moves the cast outside the window function. The real ClickHouse distribution journey and all 81 methods/runtime cases passed after that change.

## Private-state and negative boundaries

- Remote membership integrity runs the existing Ibis `membership_validations` as scalar violation checks; DuckDB retains its original native SQL check. A SQLite runtime mutation case uses a duplicate coordinate/member pair with a matching endpoint: validation rejects it, and suppressing only `.pair_unique` makes the same relation pass. This proves the pair-uniqueness check detects the intended corruption.
- The remote linear-interpolation primary endpoint replays the value-frequency relation. DuckDB retains its native quantile lowering. Every backend writes an independent private Parquet part. A journey guard verifies private key/value columns cross only in `part.*` batch streams; direct-measure journeys also scan public rows, descriptors and statement inventories for raw key literals.
- A SQLite distribution journey uses raw values `913001.125` and `913007.375`, verifies the public median `913004.25`, and scans the public rows, descriptor (including Evidence) and statement inventory for both source values. The shared part-stream guard still permits those values only in independent private part batches.
- Generic retained-part transfer rejects real membership and distribution parts. Percentile status-time folds remain rejected; remote `duckdb_tdigest@v1` remains rejected for all five backends. Direct-measure spatial rollup remains blocked by its contract.
- Entity order IDs are source-unique, so the tested channel partitions are disjoint and their spatial rollup is intentionally additive under the registered `spatial_merge=("blocked", "sum")` contract. A union-versus-sum test with one order ID in multiple source rows would violate that Entity's source identity uniqueness; overlapping direct-measure keys are covered in source observations while direct-measure spatial rollup remains blocked.

## Checks

- The final unmodified `make check-agent`, rerun after review follow-ups, passed 5,680 tests with 16 skipped, plus full lint, typecheck and API docs. The user-directed removal of the obsolete test that asserted absence of the old `intents` directory allows the default gate to run without moving the pre-existing ignored cache directory.
- Site content verification and build passed after the C7 disclosure edits.
- Focused real backend methods/runtime suites passed as shown in the matrix. Updated admission, status-time fold, statement-statistics and scalar-transport checks passed (181 tests). SQLite's additional mutation test and Trino's final raw-key inventory assertion were rerun after the previous broad gate. The current SQLite/MySQL Entity addition also passed the independent MySQL SQL compilation checks and 59 admission/scalar-transport tests.
- The review follow-up passed 64 backend-dispatch and state-admission tests, the full 59-case SQLite methods/runtime suite, and the revised Trino and ClickHouse distinct journeys (3 and 2 cases). `git diff --check` is clean.

## Review disposition

- The always-true dispatch branch and misleading admission test names were corrected. Empty-set membership and distribution rejection remain directly tested; aggregate-node tests now assert both the empty-set rejection and registered backend admission.
- The locked plan's §2/§3.5 decisions were restored verbatim. The user-approved SQLite/MySQL typed scalar SQL strategy is recorded above as an implementation deviation, rather than silently rewriting the plan.
- Backend admission sets and compiler flags serve different purposes: one decides whether a shape is allowed, the other selects its SQL lowering. DuckDB's native membership integrity query and remote Ibis scalar validations likewise remain distinct dialect paths. Their small branches stay explicit.
- The previously existing generic retained transfer rejection is cited as retained evidence, not as a newly added C7 test. Deleting the old `intents` directory-presence assertion was a direct user request.

The SQL representation change does not alter the persisted membership authority, private part schema or Store format. The temporary MySQL, Trino and ClickHouse services were stopped after their respective live checks, restoring their initial state.
