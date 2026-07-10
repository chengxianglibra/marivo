# Session, State, and Runtime

Status: design. This document specifies how `marivo.analysis` holds state across
the agent write-run-read loop: the `Session` object, the project-local on-disk
layout, content-addressed artifact identity, cold-start rehydration, cross-session
ownership, and failure recovery. It is the runtime companion to
[`python-analysis-design.md`](python-analysis-design.md) (overview) and
[`operators-and-frames.md`](operators-and-frames.md) (the operator algebra). The
evidence ledger that shares this session directory is specified in
[`evidence-access-surface.md`](evidence-access-surface.md).

The analysis alias is `mv` (`import marivo.analysis as mv`).

## The Session object

A `Session` is the one stateful handle in analysis. It owns the semantic catalog
consumed by operators, the report timezone/calendars, and the persistence layout;
every operator is a method on it. Sessions are created and resumed through the
narrow `mv.session` module facade, never constructed directly.

Read-only identity properties: `session.id` (a `sess_<hex>` id), `session.name`,
`session.question`, `session.cwd`, `session.project_root`, `session.catalog`,
`session.created_at`, `session.updated_at`, `session.tz` / `session.report_tz`
(plus `report_tz_name` / `report_tz_resolution` / `report_tz_warning`),
`session.default_calendar`, and `session.is_read_only`.

`session.is_read_only` is `True` when no datasource resolution path is configured:
such a session can read persisted artifacts and evidence but cannot run analysis
that touches a datasource. Operators that need a backend raise
`NoBackendFactoryError` on a read-only session.

### Lifecycle

The public session surface is intentionally small (`mv.session.__all__` is exactly
`current`, `delete`, `get_or_create`, `list`; the removed names `archive`,
`attach`, `create`, `switch`, `active` are gone):

- `mv.session.get_or_create(name, question=None, *, default_calendar=None, report_timezone=None, backends=None, backend_factory=None, use_datasources=True) -> Session`
  — the default entry. Idempotent: the first call with a name creates the session,
  later calls attach to it, and either way it becomes the current session. This is
  what makes a script safe to re-run across loop turns.
- `mv.session.current() -> Session | None` — a safe probe for the current session
  (process-current, else the persisted `current_session_id`, else `None`).
- `mv.session.list() -> list[SessionSummary]` — lightweight rows (name, counts,
  timestamps), not live sessions.
- `mv.session.delete(name) -> None` — permanently remove a session and its
  on-disk data; a no-op for unknown names.

`report_timezone` is persisted on first create; reopening with a conflicting value
raises `SessionTimezoneConflict` (see
[`timezone-and-calendar-design.md`](timezone-and-calendar-design.md)). `backends`
and `backend_factory` are mutually exclusive; supplying both raises
`SessionStateError`.

```python
import marivo.analysis as mv

session = mv.session.get_or_create("q4-revenue", question="Why did Q4 drop?")
frame = session.observe(
    metric=session.catalog.get("metric.analytics.dau"),
    time_scope={"start": "2026-06-18", "end": "2026-06-25"},
    grain="day",
)
```

## Project-local persistence layout

All analysis state lives project-locally under `<project_root>/.marivo/analysis/`.
Nothing is written to user-global state (datasource secrets are the sole exception
and live outside analysis). The layout, owned by `PersistenceLayout`:

```text
<project_root>/.marivo/analysis/
  session_store.db                 # SQLite (WAL): the authoritative session index
  sessions/<sess_id>/
    meta.json                      # report timezone, default/known calendars, known datasources
    jobs/<job_id>.json             # full job records (intent, params, status, timing, output ref)
    frames/<ref>/data.parquet      # frame data (snappy parquet via pyarrow)
    frames/<ref>/meta.json         # BaseFrameMeta sidecar, content-hashed
    scripts/                       # session-local script storage
    judgment.db                    # evidence ledger (see evidence-access-surface.md)
```

Writes are atomic (temp file + `os.replace`) so an interrupted turn never leaves a
partial `meta.json` or parquet. Paths recorded in the store are **project-relative**
(via `PersistenceLayout.relative_path`), so the `.marivo/` tree stays valid if the
project directory is moved.

### The session store schema

`session_store.db` is a single WAL-mode SQLite database — the authoritative index
for sessions, the current-session pointer, artifacts, and jobs:

| Table | Columns | Role |
| --- | --- | --- |
| `sessions` | `id` PK, `name` UNIQUE, `question`, `cwd`, `default_calendar`, `created_at`, `updated_at` | Session index |
| `runtime_state` | `key` PK, `value` | Small runtime pointers (e.g. `current_session_id`) |
| `artifacts` | (`session_id`,`artifact_id`) PK, `kind`, `path`, `meta_path`, `content_hash`, `created_at`, `produced_by_job` | Frame index, FK→`sessions` `ON DELETE CASCADE` |
| `jobs` | (`session_id`,`job_id`) PK, `intent`, `status`, `started_at`, `finished_at`, `output_artifact_id`, `record_path` | Job index, FK→`sessions` `ON DELETE CASCADE` |

The store holds the index; the on-disk `frames/<ref>/` directory holds the data and
the `BaseFrameMeta` sidecar. `frames/<ref>/meta.json` is the source of truth for a
frame's kind, schema, semantic shape, lineage, quality, and blocking issues.

## Content-addressed artifact identity

Every persisted frame carries a `content_hash` computed from its `BaseFrameMeta`
plus the parquet bytes (`compute_frame_content_hash`). After `observe()` /
`compare()` return, `frame.ref` equals the deterministic artifact id, so a frame
produced in one script can be reloaded in the next with
`session.get_frame(prev_frame.ref)`.

`frame.state` (an `ArtifactState`) carries only the baseline runtime facts:
`materialization` (`materialized` | `recomputed` | `partial`) and `content_hash`.
Cache, freshness, and superseded relationships are intentionally not baseline
artifact fields — they are future extensions, and failure state belongs to
job/recovery metadata, not the terminal artifact family. A content hash lets the
runtime skip re-querying a backend for a deterministic computation that already
materialized, but cache-hit correctness depends on the datasource snapshot and
freshness, so identity is derived from resolved params + definition version +
datasource freshness, never operator+params alone.

## Cold-start rehydration

Loop turn N+1 may lose all in-memory objects (a new script, or context that was
compacted). Recovery never re-queries the datasource unless the agent explicitly
asks to refresh/recompute; it reads persisted state:

- `session.get_frame(ref) -> BaseFrame` — reconstruct a fully functional frame
  from `data.parquet` + `meta.json`; the result can be passed to any operator.
  Raises `FrameRefNotFound`, `CrossSessionFrameError`, or
  `FrameCacheCorruptedError`.
- `session.frame_summaries() -> list[FrameSummaryEntry]` — rich per-frame metadata
  (`ref`, `kind`, `metric_id`, `semantic_kind`, `semantic_model`, `created_at`,
  `row_count`, `content_hash`, `analysis_purpose`) for semantic lookup across
  script boundaries. `session.frames()` returns the lightweight `(ref, kind)`
  pairs when only identity is needed.
- `session.jobs()` / `session.recent_jobs(limit=5) -> list[JobSummary]` and
  `session.job(job_id) -> dict` — the step history and full per-job records
  (raises `JobNotFoundError` for an unknown id).
- `session.knowledge() -> SessionKnowledge` — a bounded, read-only synthesis
  snapshot projected from `judgment.db` (observations, established facts, open
  items). See [`evidence-access-surface.md`](evidence-access-surface.md).

`analysis_purpose`, accepted by every operator, is persisted on the frame and
surfaced in `frame_summaries()` so a later turn can tell why a frame was produced.

## Cross-session frame ownership

Frame ownership across sessions is enforced, not advisory. Each `BaseFrameMeta`
records its owning `session_id` and `project_root`; `session.get_frame(ref)` raises
`CrossSessionFrameError` when the ref belongs to a different session. A helper that
consumes a frame therefore cannot silently mix artifacts from two sessions — the
consuming session must own the frame it is handed.

## Failure recovery

Default operators fail loud: if `compare()` cannot produce a `DeltaFrame`, it
raises a structured error rather than returning a widened `DeltaFrame | FailedStep`.
When a multi-step script fails at step *k* with steps `1..k-1` already
materialized, the session/job layer keeps the recoverable context so the next turn
can reuse upstream work:

- successfully materialized upstream artifact refs (in `artifacts` + on disk);
- the failed step's operator, expected/received, and repair hints (structured
  error);
- the job record with its `status`, retrievable via `recent_jobs()` / `job(id)`.

There is no non-raising batch API on the default surface; a future advanced
`StepOutcome` / `try_*` path, if added, would not change the terminal artifact
family.

## The session DAG and factual navigation

An analysis is a multi-frame DAG, not a single object — no one value "is the
analysis." Cross-turn state is reconstructed from session-level facts that already
exist, which is why there is no public `AnalysisSnapshot` artifact:

- `frame_summaries()` — refs, kind, semantic shape, metric, row count, created_at;
- `recent_jobs()` — steps/jobs/status/output refs;
- per-artifact bounded reads — `summary` via `show()`/`render()`, `contract()`,
  `state`, `lineage`.

`session.knowledge()` adds a bounded factual projection for synthesis at decision
points and closeout; it is read-only, does not create a step or enter lineage, and
is not a `recap()` — it never generates conclusions, headlines, or next-step
recommendations. Check `knowledge.evidence_completeness` before consuming its
lists: `"unavailable"` means unknown, not empty.

## Re-run and replay discipline

Because operators are pure computations over content-addressed inputs, re-running an
accumulated script is safe: identical resolved params + definitions + datasource
freshness reproduce the same `content_hash`, so repeated execution does not create
semantic drift, and unchanged upstream steps can be served from persisted frames
instead of re-querying. The persisted `jobs`/`artifacts` records let a script
reconcile its intended step chain against what already materialized before deciding
what to recompute.
