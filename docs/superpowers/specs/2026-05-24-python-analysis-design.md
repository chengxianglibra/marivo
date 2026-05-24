# Python Analysis Library (analysis_py) Architecture Design

Status: design draft (2026-05-24)

## Background

Marivo today exposes its analysis runtime through `marivo/runtime/intents/` plus
the evidence layer in `marivo/runtime/evidence/`, all of which run against the
OSI/SQLite semantic track and are reached via MCP / HTTP transports. A parallel
Python-native semantic layer (`marivo.semantic_py`) has shipped: it lets agents
declare datasources / datasets / fields / metrics in Python files under
`.marivo/semantic/<model>/` and exposes a reader API that materializes metrics
into `ibis.Expr` against an ibis backend supplied by the registered
`@ms.datasource` function.

This design defines a parallel **analysis runtime** that consumes the
`marivo.semantic_py` layer through the same agent-first Python library
approach: agents call `mv.observe(...)` / `mv.compare(...)` etc. directly
from Python scripts; results come back as typed Python frames; sessions
persist on disk under the project root.

The new path coexists with the existing OSI/MCP analysis runtime; nothing
in this spec removes or modifies the existing path.

## Scope

In scope (v1 = the minimum runnable skeleton):

- A new package `marivo/analysis_py/` independent of `marivo/runtime/*`.
- Session management with **named persistent sessions + CWD-scoped active
  pointer + explicit attach** as the resolution chain.
- Two atomic intents: `observe` and `compare`. Other intents
  (`decompose`, `correlate`, `detect`, `forecast`, `test`, `sample_summary`,
  `diagnose`, `validate`, `attribute`) are deferred to follow-on specs.
- Two frame families: `MetricFrame` and `DeltaFrame`. Each is a thin Python
  wrapper around a pandas DataFrame + Pydantic meta + lineage. Frame entry
  and exit go through explicit boundary calls (`to_pandas()` /
  `MetricFrame.from_dataframe(...)`).
- Ibis as the execution layer. Backends come from
  `@ms.datasource`-registered Python functions; the analysis runtime never
  builds connections itself.
- Full persistence of session meta + job history + frame contents under
  `<project_root>/.marivo/analysis/sessions/`.

Out of scope (deferred to later specs):

- All other intents and the AttributionFrame / SampleFrame / ForecastFrame
  families.
- The evidence layer (finding / proposition / assessment / action_proposal).
- MCP / HTTP transport adapters for analysis_py.
- Cross-session frame references.
- Async / streaming / cancellation.
- Backend connection pools.
- Automatic frame garbage collection / disk quotas.
- Cross-backend federation / joins.
- Slice predicates beyond `==` (no `!=` / `in` / range yet).
- Relative-window expressions (`"last 7 days"`).
- OSI ↔ analysis_py conversion (analysis_py is semantic_py only).
- Plan DSL (multi-step DAG submission).

## Design Principles

1. **Single-step function calls.** Each `mv.<intent>(...)` is exactly one job;
   no DAG plan submission in v1.
2. **Frame = thin wrapper + explicit in/out boundary.** Data is a pandas
   DataFrame inside a typed Python class; mutation is blocked; `to_pandas()`
   and `from_dataframe()` are the only crossings.
3. **Eager materialization.** Each intent immediately calls `backend.execute()`
   and returns a fully-materialized frame. No lazy ibis Expr surface in v1.
4. **Project-local sessions.** State lives under the nearest ancestor
   containing `.marivo/`. Switching project = switching session set; no
   global session registry.
5. **semantic_py is the only allowed cross-package dependency.** Everything
   else in `marivo.runtime.*` / `marivo.adapters.*` / `marivo.contracts.aoi*`
   is off-limits to enforce dual-track isolation.
6. **Backends are caller-built.** `@ms.datasource` functions return live
   `ibis.BaseBackend` instances; the analysis runtime caches them per-Session
   and never constructs connections itself.
7. **Persistence is the default.** Every successful intent call writes both
   the frame data and a job record to disk; sessions and frames are
   reproducible across processes.

## Module Layout

```text
marivo/analysis_py/
├── __init__.py                # public re-exports (mv.observe / mv.compare / mv.session / frames / load_frame)
├── frames/
│   ├── __init__.py
│   ├── base.py                # BaseFrame, frame storage helpers
│   ├── metric.py              # MetricFrame + MetricFrameMeta
│   └── delta.py               # DeltaFrame + DeltaFrameMeta
├── intents/
│   ├── __init__.py
│   ├── observe.py             # observe()
│   └── compare.py             # compare()
├── session/
│   ├── __init__.py            # exposes mv.session namespace
│   ├── core.py                # Session class + lifecycle
│   ├── active.py              # CWD active pointer resolver, .marivo/analysis/active reader/writer
│   ├── persistence.py         # disk layout: write/read session meta, job records, frame files
│   └── attach.py              # create / attach / list / switch / archive / active / active_or_create
├── executor/
│   ├── __init__.py
│   ├── backend.py             # BackendCache (per-Session) + resolve via semantic_py
│   └── runner.py              # execute(ibis.Expr) -> ExecutionResult; apply_window / apply_slice helpers
├── errors.py                  # AnalysisError hierarchy
└── lineage.py                 # Lineage / LineageStep dataclasses
```

Public surface available as `import marivo.analysis_py as mv`.

## Public API

```python
import marivo.analysis_py as mv

# Session management
mv.session.create(name: str, question: str | None = None, set_active: bool = True) -> Session
mv.session.attach(name: str) -> Session
mv.session.switch(name: str) -> Session
mv.session.active() -> Session
mv.session.active_or_create(name_hint: str, question: str | None = None) -> Session
mv.session.list(include_archived: bool = False) -> list[SessionSummary]
mv.session.archive(name: str) -> None
mv.session.delete(name: str) -> None

# Intents
mv.observe(
    metric: str,                      # "<model>.<metric_name>"
    *,
    window: dict | WindowSpec | None = None,
    slice: dict | None = None,
    session: Session | None = None,
) -> MetricFrame

mv.compare(
    a: MetricFrame,
    b: MetricFrame,
    *,
    align: Literal["bucket", "sample", "segment_key"] = "bucket",
    compare_type: Literal["yoy", "qoq", "mom", "wow", "custom"] = "custom",
    session: Session | None = None,
) -> DeltaFrame

# Frame entry / exit
MetricFrame.from_dataframe(
    df: pd.DataFrame,
    *,
    metric_id: str,
    axes: dict[str, AxisSpec],
    measure: MeasureSpec,
    semantic_kind: Literal["scalar", "time_series", "segmented", "panel"],
    semantic_model: str,
    window: WindowSpec | None = None,
    slice: dict[str, Any] | None = None,
    session: Session,
) -> MetricFrame

frame.to_pandas() -> pd.DataFrame
frame.ref -> str                      # frame_<8-hex>
frame.lineage -> Lineage
mv.load_frame(ref: str, *, session: Session) -> BaseFrame
```

## Session Model

### Concepts

```text
Session  = one continuous analysis (one business question, one Session)
Job      = one intent call inside a Session (observe / compare)
Frame    = the typed output of one Job
```

### Session identity

- `session_id`: marivo-generated `sess_<8-hex>`, immutable, the on-disk
  directory name.
- `name`: human-readable string, **unique within the project root**.
  Duplicate `create()` raises `DuplicateSessionNameError` and points at
  `attach()`.

### Lifecycle states

```text
created ──submit job──► active ──archive()──► archived
                          │
                          └── delete()──► deleted (purge from disk)
```

- `created`: just made, identical to `active`.
- `active`: accepts new jobs.
- `archived`: read-only; can `attach()` and read frames, cannot submit jobs.
- `deleted`: hard-removed from disk and index.

### Active session resolution chain

```text
1. explicit session=<obj> kwarg on the API call    → use it
2. process-level current set by attach() / switch() → use it
3. <cwd>/.marivo/analysis/active (walk up to project root) → resolve name → attach
4. raise NoActiveSessionError with a hint to call mv.session.create / active_or_create
```

`MARIVO_ANALYSIS_SESSION` environment variable fallback is **not** supported in
v1; only the chain above.

### Project root resolution

The project root is the nearest ancestor directory of `cwd` that contains a
`.marivo/` directory. If none is found, the CWD itself is treated as the
project root and `.marivo/` is created lazily on first write.

### Session class

```python
class Session:
    id: str                       # sess_a3b21c89
    name: str                     # "q3-revenue"
    question: str | None
    cwd: Path                     # cwd at creation time
    state: Literal["active", "archived"]
    created_at: datetime
    updated_at: datetime
    backend_cache: BackendCache   # per-Session, in-process

    def jobs(self) -> list[JobSummary]: ...
    def job(self, job_id: str) -> JobRecord: ...
    def frames(self) -> list[FrameRef]: ...
    def archive(self) -> None: ...
    def info(self) -> SessionInfo: ...
    def close(self) -> None: ...           # closes backend_cache
```

### Agent script header (recommended template, shipped in skill)

```python
import marivo.analysis_py as mv

s = mv.session.active_or_create(name_hint="<short-description>")
print(f"[marivo] session={s.name} id={s.id} jobs={len(s.jobs())} created={s.created_at}")
# All subsequent observe / compare calls default to s via the active chain.
```

`active_or_create` rule: if an active session already exists, return it and
**ignore `name_hint`**. The hint only fires when there is no active session
(first call in a project). This blocks LLM-naming-drift from spawning a new
session every script.

### Concurrency

- Two parallel processes submitting jobs to the same session: SQLite's
  WAL mode plus per-session fcntl lock on `index.db` writes. Job IDs are
  `job_<8-hex>` random, no collision risk. Frame files are
  `frames/<ref>/` and never collide.
- `mv.session.create()` uses the SQLite `UNIQUE(name)` constraint plus a
  short fcntl on `index.db` to prevent race-create with the same name.

## Persistence Layout

### Disk structure

```text
<project_root>/.marivo/analysis/
├── active                       # text file: current active session name
├── index.db                     # SQLite: session index + UNIQUE(name)
└── sessions/
    └── sess_a3b21c89/
        ├── meta.json            # SessionInfo
        ├── jobs/
        │   ├── job_001_e7c4f.json
        │   └── ...
        └── frames/
            └── frame_4c2a8b1d/
                ├── data.parquet
                └── meta.json
```

### `index.db` schema

```sql
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL,
    cwd TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX sessions_state ON sessions(state);
```

The SQLite database holds only the session index. Job records and frame data
live as plain files on disk under each `sessions/<sid>/`.

### Job record (`sessions/<sid>/jobs/<job_id>.json`)

```json
{
  "id": "job_001_e7c4f",
  "seq": 1,
  "session_id": "sess_a3b21c89",
  "intent": "observe",
  "params": {
    "metric": "sales.revenue",
    "window": {"start": "2026-07-01", "end": "2026-09-30"},
    "slice": {"region": "north"}
  },
  "input_frame_refs": [],
  "output_frame_ref": "frame_4c2a8b1d",
  "started_at": "2026-05-24T10:23:11Z",
  "finished_at": "2026-05-24T10:23:14Z",
  "duration_ms": 3214,
  "status": "succeeded",
  "error": null,
  "semantic_project_root": "/Users/foo/proj",
  "semantic_model": "sales"
}
```

### Frame layout (`sessions/<sid>/frames/<ref>/`)

- `data.parquet`: pandas DataFrame written via pyarrow + snappy.
- `meta.json`: Pydantic-serialized frame metadata plus full lineage. The
  meta `kind` field discriminates `MetricFrame` vs `DeltaFrame`.

Frame `ref` is `frame_<8-hex>` for every frame family. The Python class is
chosen from the meta `kind` field at load time.

### Write atomicity

```text
1. Compute ibis expr; backend.execute(); receive pandas DataFrame.
2. Write data.parquet and meta.json into a temp dir, fsync, rename into
   sessions/<sid>/frames/<ref>/.
3. Write jobs/<job_id>.json (referencing the now-visible frame_ref); fsync.
4. UPDATE sessions SET updated_at = NOW() in index.db.
```

A failed intent writes only the job record with `status="failed"` and a
populated `error` field; no frame files.

### Read-back

```python
mv.load_frame("frame_4c2a8b1d", session=s) -> BaseFrame
```

Resolution:

1. `<project_root>/.marivo/analysis/sessions/<s.id>/frames/<ref>/meta.json`
2. Read `meta.kind`; instantiate `MetricFrame` or `DeltaFrame`.
3. Load `data.parquet` via pandas + pyarrow.
4. Validate the meta payload via Pydantic.
5. Return the frame.

Cross-session frame references are not supported in v1: the meta record's
`produced_by_job` must belong to the session passed to `load_frame`. Loading
a frame from another session requires explicit `mv.session.attach(name=...)`
followed by `load_frame`.

### Cleanup

- `mv.session.archive(name)`: SQL updates `state = 'archived'`; no file
  deletion.
- `mv.session.delete(name)`: removes the SQLite row and recursively deletes
  `sessions/<sid>/`.

No automatic garbage collection in v1.

## Frame Design

### Class hierarchy

```text
BaseFrame
├── MetricFrame   (v1)
└── DeltaFrame    (v1)
```

`AttributionFrame`, `SampleFrame`, `ForecastFrame`, etc. arrive with their
respective intents in follow-on specs.

### `BaseFrame` shape

```python
class BaseFrame:
    _df: pd.DataFrame
    meta: <FrameTypeMeta>          # subclass-specific Pydantic model
    lineage: Lineage

    # Exit boundary
    def to_pandas(self) -> pd.DataFrame: ...   # returns .copy()
    def to_polars(self) -> "pl.DataFrame": ...

    # References
    @property
    def ref(self) -> str: ...                  # frame_<hex>
    @classmethod
    def from_persisted(cls, ref: str, *, session: Session) -> Self: ...

    # Read-through delegation (transparent for read-only pandas operations)
    def __getitem__(self, key): return self._df[key]
    def head(self, n=10): return self._df.head(n)
    def describe(self): return self._df.describe()
    @property
    def shape(self): return self._df.shape
    @property
    def columns(self): return list(self._df.columns)
    def plot(self, *a, **kw): return self._df.plot(*a, **kw)
    def __len__(self): return len(self._df)
    def __iter__(self): return iter(self._df)

    # Blocked operations (force to_pandas())
    def __add__(self, other): raise FrameMutationError(...)
    def __setitem__(self, key, value): raise FrameMutationError(...)

    def __repr__(self) -> str: ...
    def _repr_html_(self) -> str: ...
```

### MetricFrame

```python
class MetricFrameMeta(BaseModel):
    kind: Literal["metric_frame"] = "metric_frame"
    metric_id: str                            # "sales.revenue"
    axes: dict[str, AxisSpec]
    measure: MeasureSpec
    window: WindowSpec | None
    slice: dict[str, Any]
    semantic_kind: Literal["scalar", "time_series", "segmented", "panel"]
    semantic_model: str
    row_count: int
    byte_size: int

class MetricFrame(BaseFrame):
    meta: MetricFrameMeta

    @classmethod
    def from_dataframe(
        cls,
        df: pd.DataFrame,
        *,
        metric_id: str,
        axes: dict[str, AxisSpec],
        measure: MeasureSpec,
        semantic_kind: Literal["scalar", "time_series", "segmented", "panel"],
        semantic_model: str,
        window: WindowSpec | None = None,
        slice: dict[str, Any] | None = None,
        session: Session,
    ) -> "MetricFrame": ...
```

`from_dataframe` is the **entry boundary**. The resulting frame is marked
in lineage as `source: "external"` (`Lineage.external_inputs`) so downstream
intents and audit consumers know the data came from outside the marivo
materialization path.

### DeltaFrame

```python
class DeltaFrameMeta(BaseModel):
    kind: Literal["delta_frame"] = "delta_frame"
    metric_id: str
    source_a_ref: str
    source_b_ref: str
    compare_type: Literal["yoy", "qoq", "mom", "wow", "custom"]
    align: Literal["bucket", "sample", "segment_key"]
    calendar_info: CalendarInfo | None
    semantic_kind: Literal["scalar", "time_series", "segmented", "panel"]
    semantic_model: str
    row_count: int
    byte_size: int

class DeltaFrame(BaseFrame):
    meta: DeltaFrameMeta
    # No from_dataframe in v1 (compare only).
```

DataFrame columns: axis key columns (e.g., `bucket_start`, `region`) plus
`current`, `baseline`, `delta`, `pct_change`.

### Lineage

```python
class LineageStep(BaseModel):
    intent: str                       # "observe" | "compare" | "from_dataframe"
    job_ref: str | None               # job_<hex>; None when from_dataframe external
    inputs: list[str]                 # input frame refs
    params_digest: str                # sha256 of normalized params

class Lineage(BaseModel):
    steps: list[LineageStep]
    external_inputs: list[str] = []   # frame refs that came in via from_dataframe
```

`compare` produces a Lineage whose `steps` is `a.lineage.steps + b.lineage.steps
+ [compare_step]`. `external_inputs` propagates from `a` and `b`.

### Read-through vs blocked operations

| Operation | Allowed | Goes to |
|---|---|---|
| Read / slice / stats / plot | yes (transparent) | `frame.head() / frame[col] / frame.shape / frame.plot()` |
| Arithmetic / merge / groupby / schema mutation | no | `frame.to_pandas()` first |
| Column write / value mutation | no | `frame.to_pandas()` first |
| Type conversion | yes (new view) | `frame.to_pandas() / frame.to_polars()` |

Blocked operations raise `FrameMutationError("frame is immutable; call
.to_pandas() to operate on a copy")`.

## Intent Contracts

### `mv.observe(...)`

```python
def observe(
    metric: str,
    *,
    window: dict | WindowSpec | None = None,
    slice: dict | None = None,
    session: Session | None = None,
) -> MetricFrame
```

Parameter semantics:

- `metric`: two-segment dotted string `"<model>.<metric_name>"`. The runtime
  splits on the first `.`, treats the left segment as the semantic_py model
  name and the right segment as the metric name.
- `window`: dict like `{"start": "2026-07-01", "end": "2026-09-30", "grain": "day"}`
  or a `WindowSpec` Pydantic object. v1 accepts only absolute date / datetime
  windows; relative expressions like `"last 7 days"` are out of scope.
- `slice`: dict like `{"region": "north", "tenant_id": 42}`. Field names must
  resolve to declared `@ms.field` / `@ms.time_field` entries on the metric's
  dataset, or fall back to physical column names. v1 supports `==` semantics
  only; `!=` / `in` / range predicates are deferred.
- `session`: defaults to `mv.session.active()` resolution.

Execution flow:

```text
1. Parse metric → (model_name, metric_name).
2. Resolve session via the active chain.
3. reader.get_metric(model_name, metric_name) → MetricIR
4. reader.materialize_metric(...) → ibis.Expr
5. apply_window(expr, window, dataset_ir, metric_ir) → ibis.Expr
6. apply_slice(expr, slice, dataset_ir) → ibis.Expr
7. executor.runner.execute(expr, datasource_name=ds.datasource_name, session=...) → pd.DataFrame
8. Build MetricFrameMeta (metric_id, axes, measure, semantic_kind, window, slice, ...).
9. Persist: write frame_<ref>/data.parquet + meta.json, write job record.
10. Return MetricFrame.
```

### `mv.compare(...)`

```python
def compare(
    a: MetricFrame,
    b: MetricFrame,
    *,
    align: Literal["bucket", "sample", "segment_key"] = "bucket",
    compare_type: Literal["yoy", "qoq", "mom", "wow", "custom"] = "custom",
    session: Session | None = None,
) -> DeltaFrame
```

Parameter semantics:

- `a`, `b`: both `MetricFrame`. Must share `metric_id`; v1 rejects
  cross-metric compare.
- `align`:
  - `"bucket"`: align on time-axis key (time_series); default.
  - `"sample"`: align by row order (`reset_index` then concat).
  - `"segment_key"`: align on segment key (segmented frame).
- `compare_type`: declarative tag stored on the DeltaFrame meta; does not
  alter computation. Defaults to `"custom"`.
- `session`: defaults to active.

Execution flow:

```text
1. Validate metric_id equality.
2. Validate semantic_kind compatibility (both time_series, both segmented, etc.).
3. Apply alignment:
   - bucket: inner-join on the time axis (NaN-fill misses become explicit nulls)
   - segment_key: inner-join on the segment axis
   - sample: align by row index
4. Compute current / baseline / delta / pct_change columns.
5. Compose lineage = a.lineage.steps + b.lineage.steps + [compare_step].
6. Persist frame + job record.
7. Return DeltaFrame.
```

Calendar / timezone alignment beyond simple bucket join is deferred to v1.2.

### Error model

```text
AnalysisError
├── MetricNotFoundError
├── WindowInvalidError
├── SliceInvalidError
├── SemanticKindMismatchError
├── AlignmentFailedError
├── FrameMutationError
├── FrameRefNotFound
├── BackendError
├── DuplicateSessionNameError
├── NoActiveSessionError
└── SessionStateError                 # write to archived session, etc.
```

Every error carries:

- `kind: str` (stable identifier)
- `message: str` (human-readable)
- `hint: str | None` (optional fix suggestion)
- `details: dict[str, Any]` (structured fields: metric_id, available_metrics,
  generated SQL when debug mode is on, etc.)

### Coupling with semantic_py

The intent layer talks to semantic_py only through the public reader:

```python
from marivo.semantic_py import reader

reader.list_models() -> list[str]
reader.get_model(name) -> ModelIR
reader.get_metric(model, metric_name) -> MetricIR
reader.get_dataset(model, dataset_name) -> DatasetIR
reader.get_field(model, dataset, field_name) -> FieldIR
reader.materialize_metric(model, metric_name) -> ibis.Expr
```

No imports of `marivo.semantic_py.registry` / `marivo.semantic_py.decorators`
etc. from `analysis_py`.

### Session ↔ semantic project

`mv.session.create()` records the active semantic_py project root in
`SessionInfo.semantic_project_root`. v1 supports a single semantic project
per session (one set of semantic models, one set of datasources). A future
spec may relax this when cross-model analysis is supported.

## Executor + Backend

### Component layering

```text
intent (observe / compare)
   ↓
executor.runner.execute(ibis_expr, *, datasource_name, session)
   ↓
session.backend_cache.get_or_create(datasource_name)
   ↓
marivo.semantic_py.reader.get_dataset(...) → DatasetIR → datasource_name → DatasourceIR.fn()
```

### `BackendCache`

Per-Session, in-process:

```python
class BackendCache:
    _by_datasource: dict[str, ibis.BaseBackend]

    def get_or_create(self, datasource_name: str) -> ibis.BaseBackend: ...
    def close_all(self) -> None: ...        # best-effort disconnect on Session.close()
```

When a datasource is requested for the first time the cache walks the loaded
models, finds the matching `DatasourceIR`, calls its registered `fn()`, and
stores the returned backend. Subsequent requests return the cached instance.

The cache lives on the Session (not the global registry) so that:

- Two sessions in the same process can use isolated backend instances
  (useful for tests).
- Session-scoped cleanup is easy (`session.close()` closes everything).
- No accidental cross-session sharing of authenticated connections.

### `runner.execute`

The single ibis-execution entry point:

```python
@dataclass
class ExecutionResult:
    df: pd.DataFrame
    duration_ms: int
    row_count: int

def execute(
    expr: ibis.Expr,
    *,
    datasource_name: str,
    session: Session,
) -> ExecutionResult:
    backend = session.backend_cache.get_or_create(datasource_name)
    t0 = monotonic()
    try:
        df = backend.execute(expr)
    except Exception as exc:
        raise BackendError(
            kind="ExecutionFailed",
            message=str(exc),
            details=_debug_details(expr, datasource_name),
        ) from exc
    return ExecutionResult(df=df, duration_ms=int((monotonic() - t0) * 1000),
                           row_count=len(df))


def _debug_details(expr, datasource_name) -> dict:
    """Add generated SQL to details only when MARIVO_ANALYSIS_DEBUG=1."""
    out = {"datasource": datasource_name}
    if os.environ.get("MARIVO_ANALYSIS_DEBUG") == "1":
        try:
            out["expr_sql"] = ibis.to_sql(expr)
        except Exception:
            pass
    return out
```

`MARIVO_ANALYSIS_DEBUG=1` gates exposing generated SQL in error details. The
default-off mode protects against accidental schema leakage in production.

### Window / slice translation

```python
# executor/runner.py
def apply_window(expr: ibis.Expr, window: dict | WindowSpec | None,
                 *, dataset_ir, metric_ir) -> ibis.Expr:
    """v1 requires the metric's dataset to have exactly one time_field.

    If the dataset has zero time_fields, the metric is not windowable and
    we raise WindowInvalidError. If the dataset has more than one
    time_field, the caller must extend window with an explicit
    'time_field' key naming which one to apply against (v1.1 will lift
    this constraint and accept multiple primary time fields).
    """
    if window is None:
        return expr
    time_field_expr = _resolve_time_field(dataset_ir, window=window)
    return expr.filter(time_field_expr >= window["start"],
                       time_field_expr <= window["end"])


def apply_slice(expr: ibis.Expr, slice: dict | None,
                *, dataset_ir) -> ibis.Expr:
    if not slice:
        return expr
    for field_name, value in slice.items():
        field_expr = _resolve_slice_field(dataset_ir, field_name)
        expr = expr.filter(field_expr == value)            # v1: == only
    return expr
```

`_resolve_slice_field` first looks up `dataset_ir.fields[field_name]`; if
missing, falls back to the physical column name; if neither exists, raises
`SliceInvalidError`.

### Concurrency and thread safety

- `semantic_py` uses `ContextVar` for its registry / model stacks; thread-local
  by default, no contention from analysis_py callers.
- Per-Session `BackendCache` removes cross-Session sharing concerns.
- Parallel processes against the same session disk state coordinate via the
  SQLite WAL mode and per-file rename atomicity for frame writes.
- Async / streaming / cancellation: out of scope for v1.

## Dual-track Isolation

The new `marivo/analysis_py/` track and the existing
`marivo/runtime/intents/` / `marivo/runtime/evidence/` /
`marivo/runtime/workflows/` track operate independently.

### Allowed and forbidden imports

`marivo.analysis_py` may only import:

- The Python standard library.
- Third-party packages declared in `pyproject.toml` (`ibis-framework`,
  `pandas`, `pyarrow`, `pydantic`, `sqlite3` via stdlib).
- `marivo.semantic_py` (the only marivo sibling allowed).

`marivo.analysis_py` is forbidden from importing:

- `marivo.runtime.*`
- `marivo.adapters.*`
- `marivo.contracts.aoi_runtime` / `marivo.contracts.generated.aoi`
- `marivo.core.evidence.*`
- `marivo.core.session.*`
- `marivo.core.intent.*`

The inverse is also forbidden: existing `marivo.runtime.*` etc. may not
import `marivo.analysis_py.*`.

These rules are enforced via the existing `.importlinter` INI configuration
at the repository root. Two new contracts are appended:

```ini
[importlinter:contract:analysis_py-independence]
name = analysis_py must not import marivo internals beyond semantic_py
type = forbidden
source_modules =
    marivo.analysis_py
forbidden_modules =
    marivo.runtime
    marivo.adapters
    marivo.contracts.aoi_runtime
    marivo.contracts.generated.aoi
    marivo.core.evidence
    marivo.core.session
    marivo.core.intent

[importlinter:contract:runtime-does-not-depend-on-analysis_py]
name = marivo.runtime and adapters must not import analysis_py
type = forbidden
source_modules =
    marivo.runtime
    marivo.adapters
forbidden_modules =
    marivo.analysis_py
```

### Disk namespace separation

The new track uses `<project_root>/.marivo/analysis/`. The existing track's
storage (if any) under `.marivo/` uses different subpaths
(`.marivo/sessions/`, `.marivo/state/`, etc.). The `analysis/` segment is the
filesystem-level separator.

### Transport surface

v1 does not expose `analysis_py` through MCP or HTTP transports. Agents reach
the new track exclusively via Python imports
(`import marivo.analysis_py as mv`). MCP / HTTP adapters arrive in a later
spec to avoid duplicate intent surfaces confusing agents.

### Test directory separation

```text
tests/analysis_py/              # new
tests/runtime/                  # existing, untouched
```

`conftest.py` fixtures are not shared between the two.

### Documentation separation

This spec is the single source of truth for `analysis_py`. The existing
analysis documentation under `docs/specs/analysis/` is untouched.

## v1 Boundaries

| Item | Rationale |
|---|---|
| Other intents (`decompose` / `correlate` / `detect` / `forecast` / `test` / `sample_summary` / `diagnose` / `validate` / `attribute`) | Deferred to v1.1 specs per intent |
| Evidence layer (finding / proposition / assessment / action_proposal) | Deferred to v1.3 |
| MCP / HTTP transport adapters | Deferred to v1.4 |
| AttributionFrame / SampleFrame / ForecastFrame families | Land with their producing intents |
| Cross-session frame references | Must explicitly attach to the source session first |
| Async / streaming / cancellation | v1 is synchronous and blocking |
| Backend connection pool | Per-Session single instance suffices |
| Automatic frame GC / disk quota management | Users / agents call `session.delete()` |
| Cross-backend joins / federation | semantic_py keeps one metric per datasource |
| Slice predicates beyond `==` | v1 == only; expand in v1.1 |
| Relative window expressions (`"last 7 days"`) | v1 absolute only; expand in v1.2 |
| OSI ↔ analysis_py conversion | analysis_py supports only semantic_py |
| Plan DSL (multi-step DAG submission) | v1 is single-step per intent |
| `next` / recommended-action hints in returns | Belongs to the deferred evidence layer |
| `DeltaFrame.from_dataframe` entry boundary | v1 only `MetricFrame.from_dataframe`; revisit when an evidence consumer needs it |

## v1 Deliverables

The minimum runnable skeleton is complete when:

1. Twelve modules under `marivo/analysis_py/` exist and import cleanly.
2. `tests/analysis_py/` covers:
   - `BaseFrame` / `MetricFrame` / `DeltaFrame` construction, mutation
     blocking, `to_pandas` / `from_dataframe` boundaries, lineage.
   - Session lifecycle: create / attach / switch / active / archive /
     delete; active resolution chain; duplicate-name rejection;
     `active_or_create` ignoring `name_hint` when active exists.
   - Persistence: job record write + frame file write + read-back via
     `mv.load_frame`.
   - `mv.observe` end-to-end against a seeded DuckDB
     `@ms.datasource` exposing a `sales` model.
   - `mv.compare` between two MetricFrames with `align="bucket"`.
   - Error cases: `MetricNotFoundError`, `SliceInvalidError`,
     `SemanticKindMismatchError`, `NoActiveSessionError`,
     `DuplicateSessionNameError`, `FrameMutationError`.
3. One end-to-end example:

   ```python
   import marivo.analysis_py as mv
   s = mv.session.create(name="demo", question="Q3 revenue diff?")
   q3 = mv.observe("sales.revenue", window={"start": "2026-07-01", "end": "2026-09-30"})
   q2 = mv.observe("sales.revenue", window={"start": "2026-04-01", "end": "2026-06-30"})
   d = mv.compare(q3, q2, align="bucket", compare_type="qoq")
   assert d.meta.kind == "delta_frame"
   assert "delta" in d.columns
   ```

4. `lint-imports` passes with the new contract in place.
5. `make typecheck` and `make lint` pass.

## Roadmap (out of v1 scope)

- **v1.1**: `decompose` / `correlate` / `detect` intents; `AttributionFrame`
  family; slice predicates beyond `==`.
- **v1.2**: Calendar / timezone alignment; relative window expressions.
- **v1.3**: Evidence layer (finding / proposition / assessment /
  action_proposal); outcome envelope shape.
- **v1.4**: MCP / HTTP transport adapters.
- **v2**: Plan DSL (multi-step DAG submission); lazy frame mode.

## Open Questions

None blocking. Items consciously deferred are listed in v1 Boundaries and
the Roadmap.
