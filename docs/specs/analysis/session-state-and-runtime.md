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
consumed by operators, the report timezone, and the persistence layout;
every operator is a method on it. Sessions are created and resumed through the
narrow `mv.session` module facade, never constructed directly.

Read-only identity properties are `session.id` (a `sess_<hex>` id) and
`session.name`. Other read-only public properties include `session.question`
(the current guiding question), `session.cwd`, `session.project_root`, `session.catalog`,
`session.created_at`, `session.updated_at`, `session.tz` / `session.report_tz`
(plus `report_tz_name` / `report_tz_resolution` / `report_tz_warning`),
and `session.is_read_only`.

`repr(session)` is a bounded one-line identity that points to `session.show()`.
`session.show()` prints, and `session.render()` returns, the same bounded state
card with the question, read/write status, report timezone, timestamps, and
catalog/job/frame inspection entries.

`session.is_read_only` is `True` when no datasource resolution path is configured:
such a session can read persisted artifacts and evidence but cannot run analysis
that touches a datasource. Operators that need a backend raise
`NoBackendFactoryError` on a read-only session.

### Lifecycle

The public session surface is intentionally small (`mv.session.__all__` is exactly
`current`, `delete`, `get_or_create`, `inspect`, `recent`, `resume`; the removed
names `archive`, `attach`, `create`, `switch`, `active` are gone):

- `mv.session.get_or_create(name, question=None, *, report_timezone=None, backends=None, backend_factory=None, use_datasources=True) -> Session`
  — the default entry. The first call with a name creates the session; later calls
  attach to the same immutable session id. An explicit string becomes the current
  guiding question, while omitting `question` preserves the persisted value.
  Either way the named session becomes current.
- `mv.session.resume(session_id, *, backends=None, backend_factory=None, use_datasources=True) -> Session`
  — explicitly resume one current-project session by its immutable `sess_...` id.
  It never changes the persisted name, question, or report timezone.
- `mv.session.current() -> Session | None` — a safe probe for the current session
  (process-current, else the persisted `current_session_id`, else `None`).
- `mv.session.recent(*, limit=20, cursor=None) -> SessionSummaryPage` — a bounded,
  newest-updated-first keyset page for selective historical reference. This is
  the discovery path for historical sessions; each summary supports bounded
  `.show()`, and its immutable `id` can be passed to `resume` to obtain a live
  `Session`.
- `mv.session.inspect(name, *, frame_limit=10, job_limit=5) -> SessionInspection`
  — a bounded metadata snapshot containing the exact session summary, recent
  frame summaries, and recent jobs. It does not resume the session, move the
  current pointer, touch timestamps, load semantic/datasource state, or expose
  execution methods.
- `mv.session.delete(name) -> None` — permanently remove a session and its
  on-disk data; a no-op for unknown names.

`name` is the stable API lookup key and `session.id` remains the immutable
persistence identity. Updating the current question never rewrites existing jobs,
Artifacts, Evidence, lineage, or their `analysis_purpose`. `report_timezone` is
persisted on first create; reopening with a conflicting value raises
`SessionTimezoneConflict` (see
[`timezone-and-calendar-design.md`](timezone-and-calendar-design.md)). `backends`
and `backend_factory` are mutually exclusive; supplying both raises
`SessionStateError`.

```python
import marivo.analysis as mv

session = mv.session.get_or_create("q4-revenue", question="Why did Q4 drop?")
frame = session.observe(
    metrics=session.catalog.require(ms.ref.metric("analytics.dau")).ref,
    time_scope={"start": "2026-06-18", "end": "2026-06-25"},
    grain="day",
)
```

### Parameterized physical sources

`session.source_bindings({...})` supplies non-secret runtime values declared by
`md.source_param(...)` on JSON Entity sources. It is a context manager rather
than an `observe` keyword: one request scope can consistently cover planning,
materialization, and any nested analysis calls without changing the stable
semantic project.

```python
with session.source_bindings(
    {
        ms.ref.entity("monitoring.samples"): {
            "start": "now-3600",
            "end": "now",
        },
    }
):
    frame = session.observe(ms.ref.metric("monitoring.pending_containers"))
```

Bindings are validated against the current catalog before execution, nest with
normal context-manager semantics, and are isolated with `ContextVar`. Each scope
is keyed by its owning Session connection runtime, so another Session in the
same task cannot consume its values. The exact non-secret values enter persisted
observe params and scope identity. Credentials remain datasource-owned `*_env`
references and are never accepted here.

## Project-local persistence layout

All analysis state lives project-locally under `<project_root>/.marivo/analysis/`.
Nothing is written to user-global state (datasource secrets are the sole exception
and live outside analysis). The layout, owned by `PersistenceLayout`:

```text
<project_root>/.marivo/analysis/
  session_store.db                 # SQLite (WAL): the authoritative session index
  sessions/<sess_id>/
    meta.json                      # report timezone and known datasources
    jobs/<job_id>.json             # full job records (intent, params, status, timing, output ref)
    frames/<ref>/data.parquet      # frame data (snappy parquet via pyarrow)
    frames/<ref>/meta.json         # BaseFrameMeta sidecar, content-hashed
    scripts/                       # session-local script storage
    judgment.db                    # evidence ledger (see evidence-access-surface.md)
```

Writes are atomic (temp file + `os.replace`) so an interrupted turn never leaves a
partial `meta.json` or parquet. Evidence-backed Artifacts publish in one order:
data/auxiliary files and `meta.json`, then the one-transaction evidence projection,
then the Session Store index. A committed schema-v4 evidence Artifact row is the
recovery marker for interruption before the final index write; exact recovery
validates its session, canonical path, schemas, content hashes, evidence status,
and digest before restoring the missing index row. Files without either an index
row or that committed marker remain unreachable orphans. Paths recorded in the
Session Store are **project-relative** (via `PersistenceLayout.relative_path`), so
the `.marivo/` tree stays valid if the project directory is moved.

If a non-lock projection transaction fails but `judgment.db` can still accept a
fresh transaction, Marivo commits an `evidence_status="unavailable"` Artifact row
and the exact `evidence_store_unavailable` issue without findings or a digest. That
row is a truthful recovery marker, but it is not an immutable reuse hit when a
later invocation requests evidence: the same deterministic Artifact ref retries
the complete projection and atomically replaces the unavailable marker. If even
the fallback transaction cannot be written for a first publication, the unavailable
sidecar remains the only durable failure record. A failed retry of an existing
unavailable marker instead restores its prior sidecar and Session Store registration
before raising, preserving sidecar/ledger/index agreement.

### The session store schema

`session_store.db` is a single WAL-mode SQLite database — the ordinary authoritative
index for sessions, the current-session pointer, artifacts, and jobs. Its Artifact
row may be reconstructed only from the exact committed evidence marker described
above; arbitrary frame directories never populate it:

| Table | Columns | Role |
| --- | --- | --- |
| `sessions` | `id` PK, `name` UNIQUE, `question`, `cwd`, `created_at`, `updated_at` | Session index |
| `runtime_state` | `key` PK, `value` | Small runtime pointers (e.g. `current_session_id`) |
| `artifacts` | (`session_id`,`artifact_id`) PK, `kind`, `path`, `meta_path`, `content_hash`, `created_at`, `produced_by_job` | Frame index, FK→`sessions` `ON DELETE CASCADE` |
| `jobs` | (`session_id`,`job_id`) PK, `intent`, `status`, `started_at`, `finished_at`, `output_artifact_id`, `record_path` | Job index, FK→`sessions` `ON DELETE CASCADE` |

The store holds the index; the on-disk `frames/<ref>/` directory holds the data and
the `BaseFrameMeta` sidecar. `frames/<ref>/meta.json` is the source of truth for a
frame's kind, schema, semantic shape, lineage, quality, typed issues, evidence
status, and bounded digest.

## Content-addressed artifact identity

Every persisted frame carries a `content_hash` computed from its `BaseFrameMeta`
plus the parquet bytes (`compute_frame_content_hash`). After `observe()` /
`compare()` return, `frame.ref` equals the deterministic artifact id, so a frame
produced in one script can be reloaded in the next with
`session.artifact(prev_frame.ref)`.

`ref` is the single Artifact identity vocabulary: Artifacts expose
`artifact.ref`, Run outputs expose `output_artifact_ref`, and typed
`ArtifactRef` carries the same field. There is no `id` alias — one name end to end avoids agent
selection and serialization burden.

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

Loop turn N+1 may lose every in-memory object. Recovery reads the current
runtime schema without querying a datasource:

- `mv.session.recent(...)` then `mv.session.inspect(name, run_limit=5)` provides
  a bounded historical `RunPage` without resuming or mutating the Session;
- `session.runs(...)` and `session.get_run(run_id)` expose the closed Run
  lifecycle variants and exact Artifact inputs/output;
- `session.artifact(ref)` reconstructs the exact committed Artifact;
- `session.graph(...)` projects factual Run/Artifact adjacency, heads, failed
  and incomplete Runs, with focused ancestor or descendant traversal;
- `artifact.findings()` and `artifact.finding(id)` audit exact Findings;
- `session.revalidate(ref)` checks persisted identity, current scoped semantic
  authority, and evidence integrity.

Session reads and the graph do not check semantic authority or datasource
freshness. Revalidation does not query datasource health or prove freshness.
No public API exposes frames, jobs, a Session Evidence namespace, digest pages,
derivation traces, or Finding-selection compatibility.

`Session.show()` reports exact Artifact and Run counts, bounded head and
attention previews, Evidence status counts, and the canonical Run, Artifact,
Graph, and revalidation continuations. Incompatible Store, Run, or Artifact
schema fails before projection and does not modify the old directory.

## Cross-session frame ownership

Frame ownership across sessions is enforced, not advisory. Each `BaseFrameMeta`
records its owning `session_id` and `project_root`; `session.artifact(ref)` raises
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
- the Run record with its lifecycle, retrievable via `runs()` / `get_run(run_id)`.

SQLite `locked`/`busy` timeouts in either the Session Store or evidence ledger raise
`SessionLockedByAnotherProcessError`. They are not silently retried, overwritten,
or downgraded to `evidence_status="unavailable"`. A failed final index write leaves
the already committed evidence marker recoverable on the next exact read or retry.
Projection integrity failures are distinct from environment or permission failures:
their typed repair directs a projection retry with the current Marivo build.

There is no non-raising batch API on the default surface; a future advanced
`StepOutcome` / `try_*` path, if added, would not change the terminal artifact
family.

## The session DAG and factual navigation

An analysis is a multi-Artifact DAG, not a single object — no one value "is the
analysis." Cross-turn state is reconstructed from session-level facts that already
exist, which is why there is no public `AnalysisSnapshot` artifact:

- `session.runs()` — bounded typed execution history and exact Artifact refs;
- `session.graph()` — factual producer, consumer, reuse, head, and attention state;
- per-artifact bounded reads — `show()`/`render()`, `contract()`, `state`,
  `lineage`, `evidence_status`, and `evidence_digest`;
- Artifact-owned bounded audit pages — `artifact.findings(...)`.

The graph is a factual projection, not synthesis or a planner.
Cross-artifact judgment and the decision to execute another operator belong to
the agent. If the evidence store cannot be read, audit methods raise
`EvidenceStoreUnavailableError`; an empty page means a healthy store matched no
records.

## Re-run and replay discipline

Because operators are pure computations over content-addressed inputs, re-running an
accumulated script is safe: identical resolved params + definitions + datasource
freshness reproduce the same `content_hash`, so repeated execution does not create
semantic drift, and unchanged upstream steps can be served from persisted frames
instead of re-querying. The persisted `jobs`/`artifacts` records let a script
reconcile its intended step chain against what already materialized before deciding
what to recompute.
