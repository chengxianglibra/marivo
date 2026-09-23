# C10 installed-package acceptance plan

Date: 2026-09-23. Baseline: `d3aa5b94e4e387e0cb0f225b0a4801252d0f984b` on `lazy-dataset`, with a clean worktree. This plan implements the C10 boundary in the [capability completion design](2026-09-16-multi-datasource-capability-completion-design-and-plan.md) and [C0 checklist](2026-09-16-multisource-capability-c0-implementation-plan.md). It does not activate a new capability, change the public API or publish a package.

## Scope and owners

Use the current C1–C9 acceptance records as the authority for admitted cells. C10 exercises representative installed-wheel journeys in each admitted family; those journeys do not replace the complete source-tree method matrices. In particular, C5's later approved table-form scope supersedes the original narrower table list, and C9 remains partial. MySQL's previously admitted methods remain in scope; its C9 cells remain excluded.

The existing `tests/test_installed_multisource.py` owns wheel creation, isolated installation, subprocess orchestration and receipts. `tests/installed_multisource_probe.py` owns the public Session journey. Reuse the existing backend fixture helpers and selected C-stage runtime tests from a copy of `tests/` outside the checkout, running against the installed wheel. Keep source-tree tests and wheel tests separately identified in the acceptance record. The old installed probe's pre-Run distinct rejection is obsolete after C7 and must become a positive exact-result and retained-state check. No packaged skill changes are planned.

| Family | Installed-wheel representative | Negative neighbor / limitation |
| --- | --- | --- |
| C1–C2 | required-column projection, ordinary scalar and exact Boolean/UInt or timestamp transfer on a backend that owns the physical type | missing/invalid schema, overflow or invalid value |
| C3a/C3b | timestamp hour/day and qualified parsed/multiunit axis | DST or invalid parser/hour; Trino C3b stays rejected |
| C4 | computed measure, Linear and supported exact Decimal | unsupported Decimal operations keep pre-I/O rejection |
| C5 | views, Trino memory catalog, real two-shard ClickHouse Distributed | internal table, duplicate cross-shard identity and read-only boundary |
| C6 | calendar, cumulative, validity and qualified status fold | unqualified fold or calendar authority |
| C7 | exact distinct and linear distribution with private retained parts | forbidden transfer/invalid membership |
| C8 | Entity correlation and admitted hidden-axis attribution | candidate/driver and unsupported Top-K remain rejected |
| C9 | PostgreSQL Event/Lifecycle and derived method, Trino Event/Lifecycle, ClickHouse Event/Lifecycle | SQLite/MySQL pre-I/O rejection and each backend's remaining derived-cell limits |

Numerical expectations come from the hand-counted rows in the owning tests and are stored with the C10 receipt, never recalculated from Marivo output. Each backend retains the original public aggregate/relationship/date journey, restricted-reader privilege probe, failed publication, fixture removal and separate-process retained rollup. A selected submission journey additionally compares the native driver or server-log SQL stream with Runtime's submitted SQL and roles; record coverage limits for driver-internal work rather than inferring full capture.

## Execution and evidence

Record service status first. PostgreSQL/MySQL and local SQLite can run with the single-node ClickHouse group. Run the two-shard ClickHouse group separately; then start Trino, rerun `trino_analysis.setup()` and `setup_non_iceberg()` because its memory catalog is volatile. Restore the initial service profile after acceptance. Administrators create only UUID fixture tables; the Dataset uses `analysis_reader`. All fixtures are removed in `finally`, including failed cases. Trino and ClickHouse groups never run concurrently.

Run targeted lint and typing for changed tests, then `make check-agent`, `make pypi-build pypi-check`, and the opt-in `make installed-multisource-test` once for each service group with `MARIVO_MULTISOURCE_EVIDENCE_DIR` under `/tmp/marivo-capability-c10`. The latter must install the exact final wheel in an isolated venv outside the checkout, assert `direct_url.json` SHA-256 and every Marivo module origin, and preserve per-command exits, distinct PIDs, safe SQL/role/transfer statistics and independently expected values. A backend skip is not a pass. Do not run `release-check` or start MinIO.

Write `2026-09-23-multisource-capability-c10-acceptance.md` and the matching `-execution-receipts.json` from actual outputs, linking C0–C9. Distinguish C10 wheel-passed, prior specialty evidence only, unrun/blocked, and still-rejected cells. If a real regression appears, repair only the owning boundary and rerun its affected checks. Do not commit, push or publish.
