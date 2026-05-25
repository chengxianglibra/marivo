# Python Analysis Library (analysis_py) v1.2 Design

Status: design draft (2026-05-25)

## Background

`marivo.analysis_py` v1 introduced the Python-native analysis runtime with
project-local sessions, persisted `MetricFrame` / `DeltaFrame` outputs, and
two atomic intents (`observe`, `compare`). v1.1 added `AttributionFrame`,
the `decompose` / `correlate` / `detect` intents, and structured slice
predicates.

v1.2 adds the time-handling layer the previous versions deferred:

- **Relative window expressions** in `observe(window=...)` — `"last 7 days"`,
  `"mtd"`, etc. — alongside the existing absolute window form.
- **Timezone alignment** — sessions hold an IANA timezone, with per-window
  override. Tz-aware encoding fixes a latent host-tz dependency in v1's
  `epoch_seconds` path.
- **Calendar-aware compare** — `mv.compare(..., align="calendar", ...)`
  aligns two `MetricFrame` time axes by day-of-week, working-day position,
  holiday identity, or a combination. Holiday data is project-local under
  `.marivo/calendar/<name>.json`.

The design preserves v1's dual-track isolation. `analysis_py` still consumes
only `marivo.semantic_py` and must not import the OSI/MCP runtime, evidence
layer, AOI generated contracts, or adapter stack. The OSI calendar service
(`marivo/runtime/semantic/calendar_data_service.py`) is **not** reused; the
two tracks maintain calendar assets independently.

## Scope

In scope:

- Tagged-union `WindowSpec` (`AbsoluteWindow` | `RelativeWindow`) under a
  new `marivo.analysis_py.windows/` subpackage.
- A small relative-expression parser covering the minimum representative
  set listed in §Relative Window DSL.
- Session-level `tz` (`zoneinfo.ZoneInfo`, default `"UTC"`) with per-window
  override; persisted in `meta.json`.
- Tz-aware `_encode_window_bound` for the two formats where tz semantically
  matters (`("timestamp", None)`, `("integer", "epoch_seconds")`).
- Windowed `observe` can produce a real `time_series` `MetricFrame` when the
  caller supplies `grain`; the frame includes a persisted time axis under
  `MetricFrameMeta.axes`.
- A new `marivo.analysis_py.calendar/` subpackage with project-local JSON
  loader, `Calendar` / `CalendarPolicy` Pydantic models, and pandas-only
  alignment helpers used by `compare`.
- Extension of `mv.compare` with `align="calendar"`, `calendar`, and
  `calendar_policy` parameters.
- `DeltaFrameMeta.calendar_info` lands its concrete shape (the v1 spec
  reserved the field name only).
- Job records persist both the user-supplied window (`original`) and the
  resolved absolute window (`resolved`) so future replay APIs have
  deterministic inputs.

Out of scope (deferred):

- `observe` accepting a calendar parameter for workday-filtered windows.
- Multi-level calendar nesting beyond the four documented modes.
- `decompose` / `correlate` / `detect` calendar awareness.
- `Calendar.timezone != Session.tz` cross-timezone alignment.
- Adding a `timezone` field to `TimeFieldMeta` in `semantic_py`.
- Hour-grain relative windows (`"last 3 hours"`).
- Algebraic relative expressions (`"last 2 weeks - 1 day"`).
- Slice predicates accepting relative expressions.
- `mv.replay(job_id)` API. Job records prepare for it; the API itself
  arrives later.
- Sunday-start weeks; custom fiscal year start.
- Adding `pytz` as a dependency.
- Calendar JSON `schema_version` field.
- Calendar cache invalidation / hot reload.
- Cross-track calendar reuse with the OSI metadata SQLite calendar.

## Design Principles

1. **Tagged union for window shape.** A discriminated `WindowSpec`
   (`absolute` | `relative`) matches marivo's existing `kind: Literal[...]`
   style. The string shortcut (`window="last 7 days"`) and dict shortcuts
   route to the same Pydantic models.
2. **Relative resolves to absolute before persistence.** The job record
   carries both the original (intent-revealing) and resolved (deterministic)
   forms. `MetricFrameMeta.window` only ever stores the absolute resolved
   form, keeping frames "already materialized" and free from time-walking
   surprises in downstream consumers.
3. **`window` filters; `grain` changes shape.** A window without `grain`
   remains a scalar filtered metric. A window with `grain` produces a
   time-series metric frame with an explicit time axis. Calendar compare
   depends on that time-series shape.
4. **Tz lives on the Session, callers can override per window.** Session
   holds a single `ZoneInfo`. `Window.tz` overrides it for one call. No
   IR-level tz changes in v1.2.
5. **Calendar files are project-local and immutable.**
   `.marivo/calendar/<name>.json` is the only source. Per-Session
   `CalendarCache` loads on first use, never invalidates. To change
   calendar data: edit the file and start a new session or attach in a
   new process.
6. **`compare_type` stays a declarative tag.** `calendar_policy` is the
   single source of truth for any calendar computation. The two parameters
   are orthogonal by design; no validation cross-checks them.
7. **No `marivo.runtime` / `marivo.core` reach.** The OSI calendar service,
   compare extractor, and metadata SQLite calendar table are off-limits.
   v1.2's calendar code is independent and lives only under
   `marivo/analysis_py/calendar/`.
8. **`pandas`-only for `compare`.** Calendar alignment is a frame-local
   transform; it never touches a backend. Read-only sessions (no
   `backend_factory`) can therefore run `compare(..., align="calendar")`
   end-to-end.

## Module Layout

```text
marivo/analysis_py/
├── windows/                     # new
│   ├── __init__.py
│   ├── spec.py                  # AbsoluteWindow / RelativeWindow / WindowSpec union
│   ├── relative.py              # RelativeKind + parse_relative_expr()
│   └── resolver.py              # resolve_to_absolute(window, *, as_of, tz)
├── calendar/                    # new
│   ├── __init__.py
│   ├── model.py                 # Calendar, CalendarEntry, CalendarPolicy
│   ├── loader.py                # CalendarCache (per-Session, lazy)
│   └── align.py                 # pandas-only alignment for compare()
├── executor/
│   └── runner.py                # tz-aware bounds; time-series bucketing
├── intents/
│   ├── observe.py               # accepts str / dict / WindowSpec; persists original+resolved
│   └── compare.py               # adds align="calendar" branch
├── session/
│   ├── core.py                  # Session: tz, default_calendar, calendars, known_calendars
│   └── persistence.py           # SessionInfo serializes new fields
└── errors.py                    # WindowRelativeParseError, CalendarNotFoundError,
                                 # CalendarPolicyError, TimezoneInvalidError
```

Boundary guardrails:

- `marivo/analysis_py/calendar/` does **not** import
  `marivo.runtime.semantic.calendar_data_service` or anything else under
  `marivo.runtime.*` / `marivo.core.*`. The two tracks maintain calendar
  assets independently.
- `windows/relative.py` is pure functions + dataclasses / Pydantic; no
  ibis import. ibis appears only in `executor/`.
- `calendar/align.py` is pandas-only — it consumes already-materialized
  frame data, matching the v1.1 frame-local rule used by
  `decompose` / `correlate` / `detect`.
- New subpackages do not break either `.importlinter` contract.

## Data Model

### WindowSpec union

```python
# marivo/analysis_py/windows/spec.py
from typing import Annotated, Literal
from pydantic import BaseModel, Field

class AbsoluteWindow(BaseModel):
    model_config = {"extra": "forbid"}
    kind: Literal["absolute"] = "absolute"
    start: str                            # ISO-8601 date or datetime, naive
    end: str                              # inclusive upper bound, same shape as start
    grain: Literal["hour", "day", "week", "month", "quarter", "year"] | None = None
    tz: str | None = None                 # IANA tz id; overrides Session.tz
    time_field: str | None = None         # disambiguator for multi-time-field datasets

class RelativeWindow(BaseModel):
    model_config = {"extra": "forbid"}
    kind: Literal["relative"] = "relative"
    expr: str                             # canonical lower-case form
    as_of: str | None = None              # ISO-8601 date or datetime; default = process now
    grain: Literal["hour", "day", "week", "month", "quarter", "year"] | None = None
    tz: str | None = None
    time_field: str | None = None

WindowSpec = Annotated[
    AbsoluteWindow | RelativeWindow,
    Field(discriminator="kind"),
]
```

### `observe(window=...)` input forms

`observe` calls `_normalize_window_input` which routes:

| Input shape | Result |
|---|---|
| `None` | `None` |
| `isinstance(x, (AbsoluteWindow, RelativeWindow))` | passed through |
| `str` | `RelativeWindow(expr=<str>)` |
| `dict` containing `"expr"` and neither `"start"` nor `"end"` | `RelativeWindow.model_validate(...)` |
| `dict` containing `"start"` or `"end"` but not `"expr"` | `AbsoluteWindow.model_validate(...)` |
| `dict` containing both `"start"`/`"end"` and `"expr"` | `WindowInvalidError(kind="MixedWindowForm")` |
| Any other type | `WindowInvalidError(kind="WindowTypeInvalid")` |

`WindowSpec` itself is `Annotated[AbsoluteWindow | RelativeWindow, ...]`,
which is a type alias, not a runtime class. Membership is checked against
the two concrete classes, not the alias.

`model_config = {"extra": "forbid"}` on both models catches keyword typos
(e.g. `{"strat": "..."}`) with Pydantic's standard error path.

### RelativeKind and the relative DSL

`RelativeKind` is module-internal to `windows/relative.py`; the observe
flow never touches it directly. The public-facing types are
`RelativeWindow` (input) and `AbsoluteWindow` (output of resolution).

```python
# marivo/analysis_py/windows/relative.py
from dataclasses import dataclass
from typing import Literal

RelativeUnit = Literal["day", "week", "month", "quarter", "year"]

@dataclass(frozen=True)
class RelativeKind:
    op: Literal["last_n", "this", "to_date", "today", "yesterday"]
    unit: RelativeUnit | None = None      # "today"/"yesterday" → None
    n: int | None = None                  # only for op="last_n"

def parse_relative_expr(expr: str) -> RelativeKind: ...
```

Accepted canonical forms (parser pre-processes with `expr.strip().lower()`
and collapses runs of whitespace):

| Input string | RelativeKind |
|---|---|
| `"last N day(s)"`, `"last N week(s)"`, `"last N month(s)"`, `"last N quarter(s)"`, `"last N year(s)"` | `op="last_n", unit=<u>, n=N` |
| `"this week"`, `"this month"`, `"this quarter"`, `"this year"` | `op="this", unit=<u>` |
| `"wtd"`, `"mtd"`, `"qtd"`, `"ytd"` | `op="to_date", unit=<u>` |
| `"today"` | `op="today"` |
| `"yesterday"` | `op="yesterday"` |

Anything else (including `"last 7d"`, `"this hour"`, algebraic expressions
like `"last 2 weeks - 1 day"`, and the empty string) raises
`WindowRelativeParseError` with a `hint` listing the supported forms.

v1.2 deliberately omits hour-grained relative windows; the v1
`_SUPPORTED_FORMATS` set also lacks hour-only formats, so adding hour
relatives without hour absolutes would create a partial story.

### Relative resolution semantics

```python
# marivo/analysis_py/windows/resolver.py
def resolve_to_absolute(
    window: RelativeWindow,
    *,
    as_of: datetime,                      # already tz-aware
    tz: ZoneInfo,
) -> AbsoluteWindow:
    """Parse window.expr internally and return the resolved AbsoluteWindow.

    Pseudocode:
        kind = parse_relative_expr(window.expr)
        start_date, end_date = _bounds_for(kind, as_of=as_of, tz=tz)
        return AbsoluteWindow(
            kind="absolute",
            start=start_date.isoformat(),
            end=end_date.isoformat(),
            grain=window.grain,
            tz=window.tz,
            time_field=window.time_field,
        )
    """
```

The observe flow only ever calls `resolve_to_absolute(window=..., as_of=..., tz=...)`
with a `RelativeWindow` and never sees `RelativeKind`. `parse_relative_expr`
is a `windows/relative.py` internal.

Rules (all dates evaluated in local `tz`):

- `to_date` and `this` windows start at the **local** 00:00:00 of the
  period boundary; the upper bound is `as_of` (inclusive).
- `last N day` → `[as_of.date() - (N - 1) days, as_of.date()]` —
  **N natural days including `as_of`**. `last 7 days` at
  `as_of=2026-05-24` resolves to `[2026-05-18, 2026-05-24]` exactly seven
  rows. `last N week / month / quarter / year` step back by `(N - 1)`
  calendar periods of the named unit from the period containing `as_of`,
  with end = `as_of`. **Not** N×7 days etc.
- `today` → `[as_of.date(), as_of.date()]`; `yesterday` →
  `[as_of.date() - 1, as_of.date() - 1]`.
- Week starts Monday (ISO weekday). Sunday-start is deferred.
- The returned `AbsoluteWindow.start` / `end` are always
  **date-only ISO strings** of the form `"YYYY-MM-DD"`. v1.2 does not
  support hour-grain relative windows, so the output never carries a
  time component. This keeps `_encode_window_bound`'s existing
  branches (string slice / int slice / `ibis.date` / `ibis.timestamp`)
  structurally unchanged; the timestamp / epoch branches gain date-only
  upper-bound handling so relative windows cover the entire local end day.
- Bounds are inclusive on both ends, consistent with v1's
  `field_expr >= lower, field_expr <= upper` filter for date / string /
  `yyyymmdd` integer fields. For `data_type="timestamp"` and
  `("integer", "epoch_seconds")`, a date-only end bound is interpreted
  as **full local day inclusive** by compiling the upper predicate as
  `< next_local_midnight_utc` instead of `<= local_midnight_utc`. An
  explicit datetime end bound remains an exact closed instant
  (`<= end`). This avoids the common `mtd` / `last 7 days` bug where the
  final day would otherwise include only rows at exactly midnight.
- `as_of_resolved` (written to the job record) **always preserves the
  resolved timezone offset** (e.g. `"2026-05-24T13:42:11+08:00"`); it
  is never normalized to UTC. The offset is part of the audit trail.
  Cross-machine reproducibility requires the caller to pass an explicit
  `as_of`; the default `datetime.now(tz=tz)` is intentionally
  machine-clock-dependent.

### Calendar models

```python
# marivo/analysis_py/calendar/model.py
class CalendarEntry(BaseModel):
    date: str                             # "YYYY-MM-DD"
    name: str | None = None
    group_id: str | None = None           # disambiguator when multiple holidays land on one day;
                                          # holiday_aligned key is group_id if set, else name

class Calendar(BaseModel):
    name: str                             # "cn_holidays"
    timezone: str                         # IANA tz id; interprets the date keys
    holidays: list[CalendarEntry]
    adjusted_workdays: list[CalendarEntry] = []

CalendarMode = Literal[
    "workday_aligned",            # skip weekend ∪ holidays; keep adjusted_workdays
    "dow_aligned",                # align by day-of-week across periods (Mon↔Mon, Tue↔Tue, ...)
    "holiday_aligned",            # align by holiday identity (group_id, fallback to name)
    "holiday_and_dow_aligned",    # holidays match by holiday id; non-holidays match by DoW
]

class CalendarPolicy(BaseModel):
    model_config = {"extra": "forbid"}
    mode: CalendarMode
    align_period: Literal["day", "week", "month", "quarter", "year"]
    fallback: Literal["drop", "nearest_prior_workday"] = "drop"
```

OSI's calendar service uses `weekday_aligned` /
`holiday_and_weekday_aligned`; the Python track uses the more precise
`dow_aligned` / `holiday_and_dow_aligned` names. Behaviour is the same,
the vocabulary is the only difference. Old OSI names are rejected by the
Pydantic `Literal`.

### Calendar JSON file format

`<project_root>/.marivo/calendar/<name>.json`:

```json
{
  "name": "cn_holidays",
  "timezone": "Asia/Shanghai",
  "holidays": [
    {"date": "2026-01-01", "name": "元旦"},
    {"date": "2026-02-17", "name": "春节", "group_id": "spring-festival"}
  ],
  "adjusted_workdays": [
    {"date": "2026-02-08", "name": "春节调休"}
  ]
}
```

`Calendar.model_validate_json(path.read_text())` is the only loader.
Parse failure → `CalendarPolicyError(kind="CalendarFileInvalid",
details={"path": str(path), "error": str(exc)})`. No `schema_version`
field; if a future change is incompatible the loader will gain a version
gate then.

## Session Changes

### Class field additions

```python
class Session:
    ...
    tz: ZoneInfo                          # default ZoneInfo("UTC")
    default_calendar: str | None          # e.g. "cn_holidays"; None when unset
    calendars: CalendarCache              # per-Session, lazy
    known_calendars: set[str]             # names ever loaded; persisted in meta.json
```

`tz` uses `zoneinfo.ZoneInfo` (stdlib). `pytz` is not introduced.
`default_calendar` is a name string; the actual `Calendar` object is
materialized lazily through `CalendarCache`.

### Public API additions

```python
mv.session.create(
    name: str,
    question: str | None = None,
    set_active: bool = True,
    *,
    backends: dict[str, Callable[[], ibis.BaseBackend]] | None = None,
    backend_factory: Callable[[str], ibis.BaseBackend] | None = None,
    tz: str = "UTC",                      # new
    default_calendar: str | None = None,  # new
) -> Session

mv.session.attach(name, *, backends=..., backend_factory=...,
                  tz: str | None = None,                # None = use meta.json value
                  default_calendar: str | None = None,  # None = use meta.json value
                  ) -> Session

mv.session.switch(...)                    # accepts tz / default_calendar
mv.session.active_or_create(name_hint, ..., tz="UTC", default_calendar=None)
```

`attach` resolution order for tz / default_calendar:

1. Explicit kwarg — use it and write back to meta.json.
2. Value in meta.json — use it.
3. Neither — `tz="UTC"`, `default_calendar=None`.

`create` does not accept `tz=None`. "Session has no tz" is unrepresentable
in v1.2 so `apply_window_to_dataset` can always read `session.tz`.

### CalendarCache

```python
# marivo/analysis_py/calendar/loader.py
class CalendarCache:
    def __init__(self, project_root: Path):
        self._root = project_root / ".marivo" / "calendar"
        self._cache: dict[str, Calendar] = {}

    def get(self, name: str) -> Calendar:
        if name not in self._cache:
            path = self._root / f"{name}.json"
            if not path.is_file():
                raise CalendarNotFoundError(
                    message=f"calendar '{name}' not found at {path}",
                    hint="put a JSON file under .marivo/calendar/<name>.json",
                )
            self._cache[name] = Calendar.model_validate_json(path.read_text())
        return self._cache[name]

    def list_available(self) -> list[str]:
        if not self._root.is_dir():
            return []
        return sorted(p.stem for p in self._root.glob("*.json"))
```

Per-Session, mirroring `BackendCache`. No `invalidate()` — calendar files
are treated as immutable; reload via a new session or new-process attach.

### Persistence

`meta.json` adds:

```json
{
  ...
  "tz": "Asia/Shanghai",
  "default_calendar": "cn_holidays",
  "known_calendars": ["cn_holidays"]
}
```

`index.db` schema is **unchanged**. Tz / default_calendar live only in
`meta.json` — one source of truth per fact. Legacy `meta.json` written by
v1 / v1.1 is upgraded in place on attach: missing `tz` → `"UTC"`; missing
`default_calendar` → `None`; missing `known_calendars` → `[]`. No version
gate.

### `is_read_only` unchanged

Read-only attach (no `backend_factory`):

- `observe` still raises `NoBackendFactoryError`.
- `compare(a, b, align="calendar", calendar=..., calendar_policy=...)`
  works — it is pandas-only and only reads filesystem calendar JSON.

### Agent script header update

```python
s = mv.session.active_or_create(
    name_hint="q3-revenue",
    backends={"warehouse_main": warehouse_main},
    tz="Asia/Shanghai",
    default_calendar="cn_holidays",
)
```

The `marivo-skill/marivo-py-analysis/` example header must be updated in
the same PR. `make examples-check` is the gate.

## Observe Flow Changes

### Step diff over v1 flow

The v1 observe flow keeps the same session / backend / persistence shape,
but v1.2 changes three points:

1. After "resolve session" and before "ensure_loaded", call
   `_normalize_window_input` then `_resolve_window` to obtain
   `(absolute, original_or_none)`.
2. `apply_window_to_dataset` takes `AbsoluteWindow` and `session_tz`
   instead of a raw mapping.
3. When the resolved window carries `grain`, `observe` materializes a
   grouped time series instead of a scalar filtered aggregate.

```python
def _resolve_window(
    window_in: AbsoluteWindow | RelativeWindow | None,
    *,
    session: Session,
) -> tuple[AbsoluteWindow | None, RelativeWindow | None]:
    """Return (resolved_absolute, original_relative_or_none).

    The second element is the RelativeWindow only — it is None when the
    caller already passed absolute. The job record uses it as the
    `original` field; the lineage-affecting downstream value is always
    the AbsoluteWindow.
    """
    if window_in is None:
        return None, None
    if isinstance(window_in, AbsoluteWindow):
        return window_in, None
    # RelativeWindow
    effective_tz = ZoneInfo(window_in.tz) if window_in.tz else session.tz
    as_of_dt = _coerce_as_of(window_in.as_of, tz=effective_tz)
    absolute = resolve_to_absolute(window_in, as_of=as_of_dt, tz=effective_tz)
    return absolute, window_in
```

`resolve_to_absolute` already preserves `window_in.grain` / `tz` /
`time_field` (see its pseudocode above), so the caller does not patch
those fields again.

`_coerce_as_of`:

- `None` → `datetime.now(tz=tz)`.
- ISO-8601 string → `datetime.fromisoformat`; if naive, localize using
  `tz`; if it carries an offset, convert to `tz`.
- Parse failure → `WindowInvalidError(kind="AsOfInvalid")`.

### Windowed output shape

`window` is a filter. `grain` is the shape switch:

| Input | Output shape |
|---|---|
| `window=None` | scalar `MetricFrame` (v1 behavior) |
| `window={start, end}` or relative window with no `grain` | scalar `MetricFrame` filtered to the resolved window |
| `window={..., grain="day"}` or `RelativeWindow(..., grain="day")` | `time_series` `MetricFrame` grouped by the resolved time axis |

The time-series path uses the same `_resolve_time_field` decision as window
filtering. The effective grain is `AbsoluteWindow.grain`; if it is absent,
the output stays scalar. v1.2 deliberately makes `grain` explicit instead
of inferring a time-series shape from the mere presence of a window.

Time-series materialization is intentionally narrow in v1.2:

- The metric must reference exactly one dataset. Multi-dataset grouped
  metrics are deferred until the plan DSL can express joins and grouping
  explicitly.
- The resolved time field must have `time_meta`; unsupported time formats
  keep using `WindowInvalidError`.
- The executor derives `bucket_start` from the time field at the requested
  grain, runs the metric expression as a grouped aggregate, orders by
  `bucket_start`, and returns columns `bucket_start` plus the metric value.
- `MetricFrameMeta.axes` is populated as
  `{"time": {"role": "time", "column": "bucket_start", "grain": <grain>,
  "time_field": <field_name>}}`.
- Unsupported grouped metric shapes raise
  `MetricShapeUnsupportedError(kind="WindowedTimeSeriesUnsupported")`
  before any frame or job record is persisted.

Calendar compare's ordinary observe-driven path is therefore:

```python
cur = mv.observe("sales.revenue", window={"expr": "mtd", "grain": "day"}, session=s)
base = mv.observe("sales.revenue", window={"expr": "last 1 month", "grain": "day"}, session=s)
delta = mv.compare(
    cur,
    base,
    align="calendar",
    calendar="cn_holidays",
    calendar_policy={"mode": "dow_aligned", "align_period": "month"},
    session=s,
)
```

### `_encode_window_bound` tz-aware behaviour

| `data_type` | v1 behaviour | v1.2 change |
|---|---|---|
| `"date"` | `ibis.date(iso)` | unchanged: date has no tz; the local-day boundary is the input's `iso[:10]`. |
| `"timestamp"` | `ibis.timestamp(iso)` | If `iso` is naive, localize with `effective_tz` then convert to UTC ISO before passing to `ibis.timestamp`. If the upper bound is date-only, compile the predicate as `< next_local_midnight_utc`; explicit datetime upper bounds remain `<= end`. v1.2 assumes backend timestamp columns are stored as UTC (the common case). Column-level tz metadata is a v1.3 topic. |
| `"string"` (`yyyy-mm-dd` / `yyyymmdd`) | string slice | unchanged. Date-string columns have no tz; `effective_tz` only controls how `iso` is interpreted into `iso[:10]`. |
| `"integer"` (`yyyymmdd`) | int slice | unchanged. Same as above. |
| `"integer"` (`epoch_seconds`) | `int(datetime.fromisoformat(iso).timestamp())` | **Fixed**: if `iso` is naive, localize with `effective_tz` before computing the timestamp; if it carries an offset, convert to UTC. Date-only upper bounds use `< next_local_midnight_epoch_seconds`. v1's implementation depended on the host timezone for naive ISO inputs — that latent bug is gone. |

The `_SUPPORTED_FORMATS` set itself is unchanged; only the encoding
semantics gain tz handling.

### Slice does not accept relative

v1.1 slice predicates accept literals only. v1.2 does **not** extend
them to relative expressions; time windowing routes through `window=`.
This keeps the slice surface a single language and avoids overlapping
semantics with the new `RelativeWindow`.

### Job record: original + resolved

`sessions/<sid>/jobs/<job_id>.json` `params.window` becomes structured:

```json
"window": {
  "original": {"kind": "relative", "expr": "mtd", "as_of": null, "tz": null,
               "grain": null, "time_field": null},
  "resolved": {"kind": "absolute", "start": "2026-05-01", "end": "2026-05-24",
               "tz": "Asia/Shanghai", "time_field": null, "grain": null},
  "as_of_resolved": "2026-05-24T13:42:11+08:00",
  "session_tz": "Asia/Shanghai"
}
```

Rules:

- Caller passes absolute → `original=null`, `resolved=<AbsoluteWindow>`,
  `as_of_resolved=null`, `session_tz=<...>`.
- Caller passes relative → all four populated; `resolved` is the actual
  filter window used.
- Future replay APIs should prefer `resolved`; `original` is intent /
  audit only.
- Caller passes no window → `params.window=null` (unchanged from v1).

### `MetricFrameMeta.window`

`MetricFrameMeta.window` is typed as `AbsoluteWindow | None` and stores
only the resolved form. The original relative form lives only in the job
record. Downstream frame-local operations (`compare`, `decompose`, etc.)
see concrete dates only.

### External entry: `MetricFrame.from_dataframe(window=...)`

`from_dataframe` is the only external entry into the frame system. v1
accepts `window: WindowSpec | None` where `WindowSpec` was a plain
Pydantic class. v1.2 makes `WindowSpec` a tagged union, so the entry
contract is updated:

- `window` accepts the same four input forms as `observe(window=...)`:
  `None`, an `AbsoluteWindow` / `RelativeWindow` instance, a `str`
  (relative shortcut), or a `dict` (absolute or relative).
- Routing through `_normalize_window_input` is reused.
- If the result is a `RelativeWindow`, it is resolved immediately using
  `session.tz` and `as_of=datetime.now(tz=session.tz)`; there is no job
  record for `from_dataframe`, so the unresolved form is **discarded**.
  Callers that want the unresolved expression preserved should record
  it externally (it does not belong in frame meta).
- The resulting `MetricFrameMeta.window` is always `AbsoluteWindow | None`.
- Frame lineage marks the entry as `external` (unchanged from v1).

### Read-back: `mv.load_frame` legacy compatibility

`load_frame` must restore frames written by v1 / v1.1. Those `meta.json`
files carry `window` either as `null` or as a plain dict with `start` /
`end` and no `kind` discriminator. The loader handles legacy meta:

- `window` field absent or `null` → `None`.
- `window` is a dict with a `kind` field → routed through Pydantic
  discriminator (`AbsoluteWindow` / `RelativeWindow`); v1.2 frames only
  ever persist `kind="absolute"`, but the discriminator path is the
  canonical one and stays open for future expansion.
- `window` is a dict **without** `kind` but with `start` and `end` →
  treated as a legacy `AbsoluteWindow`; `kind="absolute"` is injected
  before validation. Extra unknown keys are stripped before validation
  to tolerate forward-compatible fields v1 may have written.
- `window` is any other shape → `FrameMetaInvalidError(kind="LegacyWindowShapeInvalid",
  details={"raw": ..., "frame_ref": ...})`.

`FrameMetaInvalidError` is added under `AnalysisError` in the error model
below.

### Error model additions

```
AnalysisError
├── WindowInvalidError
│   └── (new kinds) "MixedWindowForm" / "WindowTypeInvalid" / "AsOfInvalid"
├── (new) WindowRelativeParseError
├── (new) TimezoneInvalidError
├── (new) MetricShapeUnsupportedError
│   └── (new kind) "WindowedTimeSeriesUnsupported"
└── (new) FrameMetaInvalidError
    └── (new kinds) "LegacyWindowShapeInvalid"
```

`TimezoneInvalidError(kind="TimezoneNotFound")` wraps
`zoneinfo.ZoneInfoNotFoundError` in `session.create` / `attach` /
window resolve. `FrameMetaInvalidError` is raised by `mv.load_frame`
when legacy meta cannot be coerced to a v1.2 shape.

## Compare: `align="calendar"`

### Signature

```python
def compare(
    a: MetricFrame,
    b: MetricFrame,
    *,
    align: Literal["bucket", "sample", "segment_key", "calendar"] = "bucket",
    compare_type: Literal["yoy", "qoq", "mom", "wow", "custom"] = "custom",
    calendar: str | None = None,                # required when align="calendar"
                                                # (or falls back to session.default_calendar)
    calendar_policy: CalendarPolicy | dict | None = None,   # required when align="calendar"
    session: Session | None = None,
) -> DeltaFrame
```

### Pre-alignment validation

Steps 1–6 run before any calendar IO; step 7 runs immediately after the
loader returns:

1. v1 checks (session resolution, cross-session ownership, `metric_id`
   equality, `semantic_kind` compatibility).
2. `align="calendar"` and `a.meta.semantic_kind != "time_series"` →
   `SemanticKindMismatchError(kind="CalendarAlignRequiresTimeSeries")`.
   v1.2 supports `time_series` only. Panel calendar alignment needs
   per-series key matching across frames and is deferred to v1.3.
3. `align != "calendar"` and any of `calendar` / `calendar_policy` is
   non-None → `CalendarPolicyError(kind="CalendarUnusedWithAlign")`.
4. `align="calendar"` and both `calendar` and `session.default_calendar`
   are None → `CalendarPolicyError(kind="CalendarNotSpecified")`.
5. `align="calendar"` and `calendar_policy is None` →
   `CalendarPolicyError(kind="CalendarPolicyNotSpecified")`. There is no
   default policy because `workday_aligned`, `dow_aligned`, and
   holiday-aware modes answer different business questions.
6. `calendar_policy` is a dict → `CalendarPolicy.model_validate(...)`;
   failure → `CalendarPolicyError(kind="CalendarPolicyInvalid")`.
7. After `session.calendars.get(calendar_name)` returns (may raise
   `CalendarNotFoundError` or `CalendarPolicyError(kind="CalendarFileInvalid")`),
   `Calendar.timezone != Session.tz` →
   `CalendarPolicyError(kind="CalendarTimezoneMismatch")`.

`compare_type` and `calendar_policy` are orthogonal; the runtime does no
cross-validation. `compare_type="yoy"` + `align_period="month"` is a
legitimate combination.

### Time axis derivation

`align="calendar"` requires a determinable time-axis column on each
input frame:

- `semantic_kind="time_series"`: use `meta.axes` with `role="time"`. If
  none → `AlignmentFailedError(kind="NoTimeAxis")`.
- `semantic_kind in {"scalar", "segmented", "panel"}`: rejected at
  pre-alignment step 2 above.

The time column must be pandas `datetime64[ns]`, `datetime64[ns, tz]`,
Python `date`, or parseable by `pandas.to_datetime` using the format
inferred from `MetricFrameMeta.window`. Otherwise →
`AlignmentFailedError(kind="TimeAxisNotDatelike")`.

The two derived Series are converted to **local dates under `session.tz`**:

- `datetime64[ns, tz]` → `.dt.tz_convert(session.tz).dt.date`.
- naive `datetime64[ns]` → treat as UTC because v1.2 assumes backend
  timestamp columns are stored in UTC, then
  `.dt.tz_localize("UTC").dt.tz_convert(session.tz).dt.date`.
- Python `date` / date-only strings → use the date value directly after
  parsing; no timezone conversion is applied.

All calendar key computation runs on these local dates.

### Single-period frame requirement

To prevent many-to-many joins when one frame spans multiple
`align_period` units (e.g. a 6-month daily frame with
`align_period="month"` would otherwise have `(weekday=1, week_offset=1)`
appear once per month, causing a 6×6 explosion against `b`), v1.2
enforces:

- Each input frame's local-date Series must span at most **one** period
  of `align_period`. The check uses the **min/max local date** of the
  Series and computes whether they fall in the same `align_period` bin
  (year / quarter / month / week / day).
- Violation → `AlignmentFailedError(kind="CalendarAlignFrameSpansMultiplePeriods",
  details={"side": "a"|"b", "align_period": "<unit>", "detected_periods": [...]})`.
- The caller's remedy is to call `observe` separately for each period
  and then `compare` pairwise. This matches the natural calendar
  workflow ("compare Q3 vs Q2", "compare Sept vs Aug").

`align_period="day"` is rejected at pre-alignment with
`CalendarPolicyError(kind="CalendarPolicyInvalid",
details={"reason": "align_period=day yields single-row frames; calendar
alignment is meaningless"})` regardless of `mode`. Combined with the
single-period span rule above, `align_period="day"` would force each
frame to a single date, which collapses every alignment mode to "same
exact date".

### Calendar key per mode

| mode | align_key | `align_period` used? |
|---|---|---|
| `dow_aligned` | `(weekday, week_offset_in_period)` | yes — defines the period in which `week_offset` is counted |
| `workday_aligned` | `nth_workday_in_period` | yes — defines the period |
| `holiday_aligned` | `holiday.group_id` if set, else `holiday.name` | only when `fallback="nearest_prior_workday"` |
| `holiday_and_dow_aligned` | holiday rows → holiday id; non-holiday rows → `(weekday, week_offset_in_period)` | yes — for both the DoW path and any fallback |

`week_offset_in_period` is the count of ISO weeks between the local
date's ISO week and the period's **anchor week**, where the anchor week
is defined as **the ISO week that contains the period's first calendar
day** (1 Jan for year, 1st-of-month for month, quarter-start for
quarter). This is deliberately simpler than ISO 8601 week-1 semantics
(which uses "the week containing the year's first Thursday") because
the alignment use case cares about contiguous within-period offsets,
not calendar-year week numbering.

Examples:
- `align_period="year"`, date `2026-01-05` (Mon): anchor week contains
  `2026-01-01` (Thu) → date is in the same ISO week as anchor →
  `week_offset = 0`.
- `align_period="month"`, date `2026-05-12` (Tue): anchor week
  contains `2026-05-01` (Fri) → date is 1 ISO week later →
  `week_offset = 1`.
- `align_period="week"` → always 0 (DoW alone discriminates rows).
- `align_period="day"` → rejected for all modes (see §Single-period
  frame requirement).

Weekday and weekend are derived from `date.isoweekday()` (Mon=1..Sun=7;
Python's `date.weekday()` returns Mon=0..Sun=6 and is **not** used).
The calendar JSON only annotates holidays and adjusted workdays —
matching the OSI design choice in
`docs/superpowers/specs/2026-05-16-calendar-data-simplification-design.md`.

### Fallback

| `CalendarPolicy.fallback` | meaning |
|---|---|
| `"drop"` (default) | inner join on `align_key`; unmatched rows are dropped. |
| `"nearest_prior_workday"` | when one side has no match, replace with the nearest prior workday **within the same `align_period`**. If none exists, the row is still dropped. |

Replaced rows are marked in the output DataFrame with
`align_quality="fallback"`; exact matches are `"exact"`. Dropped rows
do not appear at all, so the column has only two visible values.

### DeltaFrame meta

`DeltaFrameMeta.calendar_info` (v1 placeholder) gets its concrete shape:

```python
class CalendarInfo(BaseModel):
    calendar_name: str
    calendar_timezone: str                # from Calendar.timezone
    session_timezone: str                 # from Session.tz
    mode: CalendarMode
    align_period: Literal["day", "week", "month", "quarter", "year"]
    fallback: Literal["drop", "nearest_prior_workday"]
    matched_rows: int
    fallback_rows: int                    # > 0 only when fallback="nearest_prior_workday"
    dropped_rows_a: int
    dropped_rows_b: int
```

`calendar_info` stays `None` for non-calendar align modes (unchanged
from v1).

### DeltaFrame data columns

Standard v1 columns: `current`, `baseline`, `delta`, `pct_change`.
Additional v1.2 columns for `align="calendar"`:

- `align_key` — `tuple` or `str` at runtime; serialized to a JSON string
  in parquet so it stays stably ordered and readable across processes.
- `align_quality` — `"exact" | "fallback"`.
- Time axis values from both inputs preserved as `bucket_start_a` and
  `bucket_start_b`. For `align="bucket"` the existing single `bucket_start`
  column is retained; the `_a` / `_b` suffix only appears when
  `align="calendar"`.

### Execution flow

```text
1. v1 checks (session / cross-session / metric_id / semantic_kind) — unchanged
2. align="calendar": run pre-alignment validation (§Pre-alignment validation)
3. align="calendar": calendar = session.calendars.get(calendar_name)
4. align="calendar": derive local-date Series; enforce single-period
   span on each side (§Single-period frame requirement); derive
   `align_key` per frame
5. align="calendar": inner join on align_key; if either side has
   duplicate keys, raise `AlignmentFailedError(kind="CalendarAlignKeyNotUnique",
   details={"side": "a"|"b", "duplicate_keys": [...]})` before merging;
   apply fallback; build columns
6. Compute current / baseline / delta / pct_change (unchanged from v1)
7. Compose lineage = a.lineage.steps + b.lineage.steps + [compare_step].
   compare_step.params_digest includes calendar_name / mode / align_period /
   fallback so calendar runs hash differently from plain compare.
8. Build DeltaFrameMeta; populate calendar_info when align="calendar".
9. Persist (unchanged from v1).
```

Calendar loading is filesystem-only; no ibis backend touched. Read-only
sessions can run `compare(..., align="calendar", ...)` end-to-end.

### Error model additions

```
AnalysisError
├── AlignmentFailedError
│   └── (new kinds) "NoTimeAxis" / "TimeAxisNotDatelike"
│                  / "CalendarAlignKeyNotUnique"
│                  / "CalendarAlignFrameSpansMultiplePeriods"
├── SemanticKindMismatchError
│   └── (new kind) "CalendarAlignRequiresTimeSeries"
├── (new) CalendarNotFoundError
└── (new) CalendarPolicyError
    └── (new kinds) "CalendarNotSpecified" / "CalendarUnusedWithAlign"
                   / "CalendarPolicyNotSpecified" / "CalendarPolicyInvalid"
                   / "CalendarFileInvalid" / "CalendarTimezoneMismatch"
```

`AmbiguousTimeAxis` is no longer needed — panel is rejected at the
pre-alignment step, so the "multiple time axes" case can't arise from
the supported shapes.

## v1.2 Boundaries

| Item | Rationale |
|---|---|
| `align="calendar"` on `semantic_kind="panel"` frames | v1.3. Needs a defined per-series join key (currently `panel`'s series-axis convention is loose); incidental v1.2 support would silently cross-join series. v1.2 rejects with `SemanticKindMismatchError(kind="CalendarAlignRequiresTimeSeries")`. |
| Frames spanning more than one `align_period` | Rejected with `AlignmentFailedError(kind="CalendarAlignFrameSpansMultiplePeriods")`. Caller observes one period per frame, then compares. |
| Time-series observe for multi-dataset metrics | v1.3 / Plan DSL. v1.2 supports grouped time-series output only for single-dataset metrics; multi-dataset grouped semantics require explicit join and grain ownership. |
| `observe` accepts a calendar parameter for workday-filtered windows | v1.3. Would create a second time-filter entry alongside v1.1 slice predicates. |
| Multi-level calendar nesting beyond the four modes | v1.3. The four modes are a closed set for v1.2. |
| `decompose` / `correlate` / `detect` calendar awareness | v1.3 |
| `Calendar.timezone != Session.tz` cross-timezone alignment | Rejected with `CalendarPolicyError(kind="CalendarTimezoneMismatch")`. |
| `TimeFieldMeta` adds a `timezone` field | v1.3+. v1.2 keeps `semantic_py` IR unchanged; tz lives on Session and Window only. |
| Hour-grain relative windows (`"last 3 hours"`) | v1.3. Matches the v1 `_SUPPORTED_FORMATS` set which also lacks hour-only formats. |
| Algebraic relative expressions (`"last 2 weeks - 1 day"`) | v2 Plan DSL territory — avoids responsibility overlap. |
| Slice predicates accept relative expressions | Not done; v1.1 slice is the structured-predicate surface, time windowing routes through `window=`. |
| `mv.replay(job_id)` / historical-window replay API | Not in v1.2; the `original + resolved` job record prepares for it. |
| Sunday-start week / custom fiscal year start | v1.3. v1.2 fixes ISO weekday (Mon start) and calendar year. |
| `pytz` dependency | Not introduced; stdlib `zoneinfo` only. |
| Calendar JSON `schema_version` field | Not in v1.2; add when a non-compatible change forces it. |
| Calendar `invalidate()` / hot reload | Not done; calendars are treated as immutable per session. |
| Cross-track calendar reuse with OSI metadata SQLite | Not done; the two tracks maintain calendar assets independently. |

## v1.2 Deliverables

v1.2 is complete when:

1. New modules in place: `analysis_py/windows/{spec,relative,resolver}.py`,
   `analysis_py/calendar/{model,loader,align}.py`. `analysis_py/errors.py`
   gains `WindowRelativeParseError`, `CalendarNotFoundError`,
   `CalendarPolicyError`, `TimezoneInvalidError`,
   `MetricShapeUnsupportedError`, and `FrameMetaInvalidError`.
   `lint-imports` passes with the two existing `.importlinter` contracts
   intact.
2. Changed modules:
   - `session/core.py` — `tz` / `default_calendar` / `calendars` /
     `known_calendars`; `create` / `attach` / `switch` /
     `active_or_create` accept new kwargs.
   - `session/persistence.py` — `meta.json` serializes / deserializes
     new fields; legacy meta is upgraded in place on attach.
   - `executor/runner.py` — `apply_window_to_dataset` accepts
     `AbsoluteWindow` and `session_tz`; `_encode_window_bound` gains
     tz-aware branches for `("timestamp", None)` and
     `("integer", "epoch_seconds")`; date-only timestamp / epoch upper
     bounds use `< next_local_midnight`; grouped time-series observe
     builds `bucket_start` at `window.grain`.
   - `intents/observe.py` — input normalization; relative → absolute
     resolution; job record writes `original` + `resolved` +
     `as_of_resolved` + `session_tz`; `MetricFrameMeta.window` stores
     resolved only; `window.grain` produces a `time_series` frame with
     `axes.time.role="time"` and `bucket_start`.
   - `intents/compare.py` — `align="calendar"` branch;
     `DeltaFrameMeta.calendar_info` populated; single-period span check
     and key-uniqueness check before merge; panel rejection at
     pre-alignment.
   - `frames/metric.py` — `MetricFrame.from_dataframe(window=...)` uses
     `_normalize_window_input`; relative inputs resolved at entry.
   - `frames/base.py` (or wherever `mv.load_frame` lives) — legacy v1
     `window` dict shape handled (`kind` injected when absent; extra
     keys stripped; unparseable shapes → `FrameMetaInvalidError`).
3. Public API additions: new session kwargs; new `compare` parameters;
   `window` accepts `str` / `RelativeWindow` / `AbsoluteWindow` / dict
   in both `observe` and `MetricFrame.from_dataframe`.
4. Skill sync (**`make examples-check` gate**):
   - `marivo-skill/marivo-py-analysis/references/examples/` header
     updated to pass `tz` and `default_calendar`.
   - At least three new examples: `window_relative.py` (mtd / last 7
     days, including the scalar no-grain shape and the time-series
     `grain="day"` shape), `session_timezone.py` (override vs default),
     `compare_calendar.py` (uses `observe(..., window={..., "grain":
     "day"})` and covers at least two of the four modes).
5. Docs:
   - `marivo-skill/marivo-py-analysis/SKILL.md` gains a "Relative
     windows & timezone" section and a "Calendar-aware compare"
     section; total length stays under the 600-line cap.
   - This spec lives at
     `docs/superpowers/specs/2026-05-25-python-analysis-v1.2-design.md`.
6. Verification: `make test`, `make typecheck`, `make lint`, and
   `make examples-check` all pass.

## Testing

New tests follow the repo's flat `tests/test_*` convention:

- `tests/test_analysis_py_windows_relative.py`
  - `parse_relative_expr` covers `last N {unit}` across all five units
    with N ∈ {1, 7, 12}; `this {week|month|quarter|year}`;
    `{wtd|mtd|qtd|ytd}`; `today`; `yesterday`.
  - Edge cases: singular vs plural (`day` vs `days`); case insensitivity;
    leading / trailing / interior whitespace; empty string; unsupported
    forms (`"last 7d"`, `"last 2 weeks - 1 day"`, `"this hour"`) →
    `WindowRelativeParseError`.
  - `resolve_to_absolute` per `RelativeKind`: fixed
    `as_of="2026-05-24T13:42:11+08:00"`, `tz=Asia/Shanghai`; assert
    `(start, end)` matches a hand-computed expectation.
  - **Off-by-one regression**: `last 7 days @ as_of=2026-05-24` resolves
    to `start=2026-05-18`, `end=2026-05-24` (exactly 7 distinct local
    dates); `last 1 day` resolves to `start=end=2026-05-24` (one day,
    not two).
  - **Date-only output**: every `resolve_to_absolute` call's output
    `start` / `end` match the regex `^\d{4}-\d{2}-\d{2}$` — no time
    component ever appears in v1.2 relative-resolved outputs.
  - At least one DST-spanning case: `tz=America/New_York` with an
    `as_of` straddling the spring-forward boundary.

- `tests/test_analysis_py_windows_spec.py`
  - `_normalize_window_input` exercises all five input paths;
    mixed `{"start", "expr"}` → `WindowInvalidError(kind="MixedWindowForm")`;
    invalid type → `WindowTypeInvalid`.
  - Direct instance pass-through works for both `AbsoluteWindow(...)`
    and `RelativeWindow(...)` constructions (validates
    `isinstance(x, (AbsoluteWindow, RelativeWindow))` path, not the
    `WindowSpec` alias).
  - Pydantic `extra="forbid"` rejects typos like `{"strat": "..."}`.

- `tests/test_analysis_py_observe_relative.py`
  - DuckDB seeded fixture (per
    [`.agents/skills/marivo-test-fixtures/SKILL.md`](.agents/skills/marivo-test-fixtures/SKILL.md))
    running `observe("sales.revenue", window="mtd")`. Assert the job
    record's `params.window` carries both `original` and `resolved`;
    `MetricFrameMeta.window` carries only `resolved`; output remains
    `semantic_kind="scalar"` because no `grain` was requested.
  - `observe("sales.revenue", window={"expr": "mtd", "grain": "day"})`
    returns a `time_series` frame with columns `bucket_start` and the
    metric value; `meta.axes["time"]` has `role="time"`,
    `column="bucket_start"`, `grain="day"`, and the selected
    `time_field`.
  - Multi-dataset metric + `window.grain` fails before persistence with
    `MetricShapeUnsupportedError(kind="WindowedTimeSeriesUnsupported")`.
  - `observe(window={"expr": "last 7 days", "as_of": "2026-05-24"})`
    is reproducible regardless of host timezone.
  - `observe(window={"expr": "last 7 days", "tz": "America/New_York"})`
    overrides session tz; `resolved.tz` and `session_tz` are persisted
    correctly.
  - **as_of offset preservation**: `observe(window={"expr": "mtd",
    "as_of": "2026-05-24T13:42:11+08:00"})` writes `as_of_resolved`
    as `"2026-05-24T13:42:11+08:00"` (offset preserved, not normalized
    to UTC).

- `tests/test_analysis_py_session_timezone.py`
  - `create(tz="Asia/Shanghai")` round-trips through `meta.json`.
  - `create(tz="Mars/Olympus")` → `TimezoneInvalidError(kind="TimezoneNotFound")`.
  - Legacy `meta.json` (no `tz` field) attaches with `tz="UTC"`;
    explicit kwarg writes back.

- `tests/test_analysis_py_executor_window_tz.py`
  - `_encode_window_bound` with `("timestamp", None)`: naive ISO under
    `tz=Asia/Shanghai` vs `tz=UTC` produces different upper / lower
    expressions.
  - Date-only upper bound on `("timestamp", None)` compiles as
    `< next_local_midnight_utc`, so rows throughout `2026-05-24` match
    `end="2026-05-24"` while `2026-05-25T00:00:00` does not.
  - `("integer", "epoch_seconds")` with naive ISO + fixed `tz` produces
    the same int regardless of host timezone (regression for the v1
    latent bug).
  - Date-only upper bound on `("integer", "epoch_seconds")` uses the
    epoch value for next local midnight as an exclusive upper bound.
  - `("string", "yyyymmdd")` / `("date", None)` are tz-insensitive.

- `tests/test_analysis_py_calendar_loader.py`
  - `CalendarCache.get("cn_holidays")` loads the fixture JSON; missing
    file → `CalendarNotFoundError`; invalid JSON →
    `CalendarPolicyError(kind="CalendarFileInvalid")`.
  - Repeated `get` returns the same instance (cache hit).

- `tests/test_analysis_py_compare_calendar.py`
  - Happy path per mode (fixture: two `MetricFrame`s, each spans
    exactly one `align_period`).
  - `mode="workday_aligned"` × `fallback="nearest_prior_workday"`:
    baseline has one fewer holiday row than current; assert the
    affected output row has `align_quality="fallback"` and
    `calendar_info.fallback_rows == 1`.
  - `mode="dow_aligned"` × `align_period="month"`: month-over-month
    pair, `(weekday, week_offset_in_period)` key behaves correctly;
    `isoweekday()` (Mon=1..Sun=7) is the source.
  - **Multi-period fail-closed**: a frame whose local-date span covers
    two months passed with `align_period="month"` →
    `AlignmentFailedError(kind="CalendarAlignFrameSpansMultiplePeriods",
    details={"side": ..., "detected_periods": [...]})`.
  - **Duplicate-key fail-closed**: construct a frame whose
    `align_key` repeats (e.g. two rows on the same local date with
    `mode="dow_aligned"`) → `AlignmentFailedError(kind="CalendarAlignKeyNotUnique")`.
  - **Fallback exhausted**: `fallback="nearest_prior_workday"` but
    neither side has any prior workday in the period → those rows are
    dropped (not silently joined), and `calendar_info.dropped_rows_a` /
    `dropped_rows_b` reflect the drops.
  - Panel rejection: `semantic_kind="panel"` + `align="calendar"` →
    `SemanticKindMismatchError(kind="CalendarAlignRequiresTimeSeries")`.
  - Cross-session frame → `CrossSessionFrameError` (calendar path must
    not bypass v1 cross-session safety).
  - `align="calendar"` + missing `calendar` + missing
    `session.default_calendar` → `CalendarPolicyError(kind="CalendarNotSpecified")`.
  - `align="calendar"` + missing `calendar_policy` →
    `CalendarPolicyError(kind="CalendarPolicyNotSpecified")`.
  - `align="bucket"` + `calendar` or `calendar_policy` passed →
    `CalendarPolicyError(kind="CalendarUnusedWithAlign")`.
  - `Calendar.timezone != Session.tz` →
    `CalendarPolicyError(kind="CalendarTimezoneMismatch")`.
  - `semantic_kind="scalar"` + `align="calendar"` →
    `SemanticKindMismatchError(kind="CalendarAlignRequiresTimeSeries")`.
  - `align_period="day"` with any mode →
    `CalendarPolicyError(kind="CalendarPolicyInvalid")`.
  - Naive `datetime64[ns]` time-axis columns are interpreted as UTC before
    conversion to `session.tz`; timezone-aware columns use
    `tz_convert(session.tz)` directly; date-only columns do not get
    timezone-converted.
  - Read-only session (no `backend_factory`) successfully runs
    `compare(..., align="calendar", ...)` and returns a `DeltaFrame`.

- `tests/test_analysis_py_load_frame_v1_2.py`
  - Persist a `DeltaFrame` carrying `calendar_info`; `mv.load_frame`
    round-trips both `calendar_info` and the `align_quality` /
    `align_key` columns; JSON-serialized `align_key` keeps stable
    ordering.
  - **Legacy v1 meta compatibility**: write a `meta.json` with
    `window: {"start": "2026-07-01", "end": "2026-09-30"}` (no `kind`
    field); `mv.load_frame` returns a `MetricFrame` with
    `meta.window` populated as an `AbsoluteWindow`.
  - **Legacy with extra keys**: `window: {"start": ..., "end": ...,
    "rogue_key": 1}` — extra keys stripped before validation; load
    succeeds.
  - **Legacy unparseable**: `window: {"foo": "bar"}` →
    `FrameMetaInvalidError(kind="LegacyWindowShapeInvalid")`.

- `tests/test_analysis_py_from_dataframe_window.py`
  - `MetricFrame.from_dataframe(window="mtd", session=s)`: relative
    expression resolved at entry using `session.tz` and process now;
    `meta.window` is `AbsoluteWindow`.
  - `from_dataframe(window={"start": ..., "end": ...})`: routes to
    `AbsoluteWindow`.
  - `from_dataframe(window=RelativeWindow(expr="ytd"))` instance path
    works (validates instance pass-through at entry).

- `tests/test_analysis_py_examples_v1_2.py`
  - Import-and-run `references/examples/window_relative.py`,
    `session_timezone.py`, `compare_calendar.py` end-to-end without
    exception; the executable-SDK contract from `agent-guide.md`.

Verification commands:

```bash
.venv/bin/pytest \
  tests/test_analysis_py_windows_relative.py \
  tests/test_analysis_py_windows_spec.py \
  tests/test_analysis_py_observe_relative.py \
  tests/test_analysis_py_session_timezone.py \
  tests/test_analysis_py_executor_window_tz.py \
  tests/test_analysis_py_calendar_loader.py \
  tests/test_analysis_py_compare_calendar.py \
  tests/test_analysis_py_load_frame_v1_2.py \
  tests/test_analysis_py_from_dataframe_window.py \
  tests/test_analysis_py_examples_v1_2.py -v
make typecheck
make lint
make examples-check
```

## Roadmap (out of v1.2 scope)

- **v1.3**: panel calendar alignment (per-series key matching);
  multi-period frame support for calendar compare;
  `observe` calendar parameter; `decompose` / `correlate` / `detect`
  calendar awareness; hour-grain relative windows; Sunday-start /
  fiscal year start; `TimeFieldMeta.timezone`; column-level tz metadata
  for backend timestamp columns.
- **v1.4**: Evidence layer (finding / proposition / assessment /
  action_proposal); outcome envelope shape.
- **v1.5**: MCP / HTTP transport adapters.
- **v2**: Plan DSL (multi-step DAG submission); lazy frame mode; algebraic
  relative window expressions.

## Open Questions

None blocking. The items consciously deferred are listed in
§v1.2 Boundaries and §Roadmap.
