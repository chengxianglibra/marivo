# W2 implementation record: period coverage

Date: 2026-10-11

Owner: [compare / ratio design](2026-10-10-analysis-compare-api-design.md), sections 5.2 and 10.
Baseline: clean `main@ba5f9da2bf3869898d99c8f3b53ad3b6c8bc4ad4`.
Status: W2 implemented and verified. ClickHouse Runtime remains unverified because its service was unavailable. No commit or push.

## Delivered contract

- Both complete original grids map independently to ordinal keys **1..N**.
  Output keeps the existing key order and public column names, with an `int64`
  time position of role `aligned` and no output `time_grid`.
- `CorrespondencePart` v3 owns immutable `TimeKeys.current` and `.baseline`.
  Endpoint grids, calendars, timezones, observation authority and original key
  types remain frozen. Missing sides have null complete original-key projections.
- Exact and time-mapped one_to_one ratios require equal aligned-key sets and
  checked composite coverage. Outer keep unions complete composite keys for
  either longer window and independently returns missing_side in both Cells.
  Empty finish rejects unobserved time keys at construction, while ordinary
  missing non-time groups retain Metric empty rules.
- `ComparisonPart` v2 retains the W1 atomic root publication and independent
  absolute/relative arithmetic with W0 non-Defined propagation. Exact absolute
  attribution retains both reindexed endpoint states.
- Graph snapshots and shared value tables use `graph_dag/v9` and
  `graph-dag-v9:`. Store generation stays 9. Old graph versions require source
  re-execution; no migration or dual read was introduced.
- Aligned outputs reject time grouping/rollup, runs, forecast and explicit lag
  correlation. Original endpoints must perform these operations first.
  Selection and row statistics preserve this restriction while aligned keys
  survive. Ordinary selection/statistics, valid Subject continuation, common-domain
  correlation without lag and independently admitted attribution keep their owners.
  No public side-selecting time projection was added.

## Audited anchor consumers

The inventory below covers readers of coordinate role `anchor` reachable from
period comparison views and time-mapped one_to_one ratios. Occurrence, Journey,
History and Anchor-domain methods have independent input-kind admission and do
not accept these numeric outputs.

| Consumer | Files | W2 handling |
| --- | --- | --- |
| Original observation, classification and time binding | `core/model.py`, `core/rules.py`, `public_dsl.py`, `compiler/graph_lowering.py`, `materialization/graph_preparation.py` | Original endpoints keep anchor grids; derived aligned output never acquires observation/time binding authority. |
| Grouping and time rollup | `public_dsl.py`, `core/rules.py`, `materialization/graph_relation.py`, `compiler/graph_lowering.py`, `materialization/graph_local_execution.py` | TimeGrid/Grain grouping rejects aligned coordinates with original-endpoint repair; original reduce remains limited to admitted Metric state. Retained-row statistics cannot recover a time axis. |
| Runs | `core/runs_rules.py`, `materialization/graph_relation.py`, `materialization/runs_execution.py` | Reject before constructing a Run method; aligned integers are never interpreted as bucket identities. |
| Forecast | `core/statistical_rules.py`, `materialization/graph_relation.py`, `materialization/statistical_execution.py` | Reject aligned input before future-grid construction; no current-grid inheritance. |
| Explicit lag correlation | `core/statistical_rules.py`, `materialization/graph_relation.py`, `materialization/statistical_execution.py` | Reject every aligned input when lag_range is explicit, including range(1); no-lag matching follows common-domain admission. |
| Original state, contribution coordinates and attribution | `compiler/graph_attribution.py`, `materialization/graph_attribution.py`, `compiler/graph_lowering.py`, `materialization/graph_local_execution.py`, `materialization/graph_exchange.py` | Both endpoint state lookup keys use original buckets; both contribution-coordinate tables reindex to aligned keys. Original-rollup time branches remain behind original-state/grid admission; exact absolute attribution remains admitted. |
| Statistical retained tables | `materialization/deviation_execution.py`, `materialization/statistical_execution.py`, `materialization/graph_dataset.py` | No grid_cells table or calendar interpretation is synthesized for aligned input; actual domain keys remain int64. Row-state retention does not grant real-time continuation. |
| Business time coverage | `materialization/business_coverage.py` | Business completeness remains owned by original observations and their grids. Comparison endpoint state freezes those facts; output does not claim a new observed time window. |
| Subject and original correspondence | `core/rules.py`, `compiler/graph_lowering.py`, `materialization/graph_local_execution.py`, `materialization/graph_exchange.py` | Subject maps cover the paired union, including baseline-only rows. Shared rows must agree. Inverse maps restore each side's actual original key and validate complete null projection on absence. |
| Dynamic guidance and display | `public_dsl.py`, `materialization/graph_dataset.py`, `session/_public_disclosure.py`, `operators/_disclosure.py` | Contract cards disclose aligned keys and original-endpoint repair; real-time actions are absent. Show includes aligned/current_time/baseline_time per row. Native Help independently resolves constraints from the owning callable. |
| Other anchor-kind execution | `materialization/anchor_execution.py`, `materialization/retention_execution.py`, occurrence/Journey/History preparation | Separate kind/Part admission; no numeric aligned output enters these methods. |

## Verification repairs

The first broad gate exposed two obsolete v8 snapshot-byte pins. They now pin
v9 bytes while independently preserving the original definition and plan hashes.
It also exposed cross-test deadline contamination from existing fault-injection
tests: resetting an inner ContextVar token after the execution budget had already
closed resurrected that closed 600-second budget. A same-process Anchor-fault plus
native-interrupt sequence reproduced the failures; interrupt tests alone passed.
The four affected fault-injection tests now assert that execution restored its
entry deadline instead of resetting stale tokens. Product deadline code is unchanged. The repaired fault-injection/native-interrupt
sequence passed all 8 cases in one process; the v9 graph pin scope passed 35 tests.

## Evidence

The local source path acquires original endpoints through native Ibis/DuckDB or
Parquet. Dual comparison finishes both Cells in the local stage. A separate
single-quantity `CellDerive` carrier exercises native SQL CASE mapping, complete
grid checks, full outer pairing, projections and Subject mapping with the same
frozen correspondence; this is recorded separately from the dual local finish.

| Boundary | Check | Result |
| --- | --- | --- |
| Source and fixed | Unequal grids in both directions, key/type/value/Cell/presence/original-key/Part equality; exact and empty-finish rejection; count/zero/null empty groups; exact attribution; ratio and selected/statistic time guards | 24 focused tests passed in 29.08s; the existing source/fixed one_to_one cases also passed. |
| Integrity | Forged frozen mappings, original keys, presence, projection type and both Cells; old v8 schema/prefix rejection | Passed focused corruption/graph checks and the full default gate. |
| Cold | Separate fixed and cold workers after source/model removal; source/Catalog/native opens forbidden; cached cold execute forbidden from recomputing fixed finish | Related explicit slow gate: 4 passed in 37.26s, including the independent W2 fixed/cold workers. |
| External Runtime | PostgreSQL, MySQL, Trino native carrier, including single SQL versus dual result equality | 3 passed in 16.82s; ClickHouse 1 skipped (service port closed, explicit readiness not enabled). No ClickHouse qualification. |
| Typing | Standard repository typecheck; additional new test modules with silent dependency following | 349 source files passed; all 4 new test files passed. Initial direct test typing exposed existing untyped optional backend fixture dependencies; touched test errors were repaired. |
| Site | Content verification | 385 required files verified; npm run build passed (Astro: 0 errors/warnings; 308 original routes, 48 latest pages and 351 internal targets verified). |
| Broad gate | make check-agent and git diff --check | Passed: lint/import contracts, 349-file typecheck, 4810 passed / 1 skipped / 1 warning in 524.36s, API docs built; whitespace check passed. |

No W3 weekday/holiday alignment, old-data migration, packaging/release acceptance,
commit, push, AGENTS.md change or packaged-skill change is included. The unrelated
`.pre-commit-config.yaml` worktree edit is preserved.

The default-suite warning is the existing fixture use of deprecated DuckDB
`fetch_arrow_table()` in `tests/analysis/statistics/deviation_fixture.py`; it does
not represent additional Runtime or release qualification.
