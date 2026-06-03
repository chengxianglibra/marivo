# Dynamic Time Grain

Date: 2026-06-03

Status: revised after spec review; pending re-review

## Problem

A time field's `granularity` attribute declares the *finest* resolution at which
the underlying column is meaningful. When running analysis, callers should be
able to bucket at any grain *coarser than or equal to* that base granularity —
including dynamic, equal-width sub-day buckets such as 5-minute or 10-minute.

Two limits block this today:

1. **Base granularity stops at `hour`.** `time_field(granularity=...)` is typed
   `Literal["year","quarter","month","week","day","hour"]`
   (`marivo/semantic/authoring.py:489`). Minute- and second-resolution columns
   cannot be declared.

2. **Analysis grain is a fixed enum.** `TimeGrain` is hardcoded
   `Literal["hour","day","week","month","quarter","year"]`
   (`marivo/analysis/windows/spec.py:9`) and lands directly on ibis
   `.truncate(grain)` (`marivo/analysis/executor/runner.py:893,904,940,960`).
   `truncate` only expresses a *single* calendar unit, so "5 minute" / "10
   minute" equal-width buckets are inexpressible.

There is also no validation that a requested analysis grain is coarser than the
time field's base granularity — base and analysis grain are currently
independent.

## Goals

- Extend base `granularity` down to `second` (add `minute`, `second`).
- Support dynamic analysis grain as `N x {second, minute, hour}` (e.g. 5 minute,
  10 minute, 30 second, 2 hour), in addition to the existing single calendar
  grains.
- Constrain dynamic sub-day widths to **divide a day evenly** so daily-reset
  buckets tile perfectly (no ragged trailing bucket).
- Enforce that a requested grain is a valid integer multiple of the time field's
  base granularity (coarser-or-equal, aligned to the base).
- Record the dynamic grain faithfully across every serialized surface (frame
  axes, window dumps, lineage, job records, escape-hatch promotion, evidence).
- Let `compare` consume dynamic-grain frames.

## Non-goals (scope decisions)

- **No calendar-unit multiples.** `day`/`week`/`month`/`quarter`/`year` remain
  `count == 1` only. `2 week`, `3 month`, `2 day` are out of scope (irregular
  width, anchor-dependent).
- **No string-shorthand parsing as the blessed public path.** The public API
  takes a structured value. String tokens remain accepted for backward
  compatibility and JSON round-trip.
- **No dynamic grain in forecast / calendar.** `forecast.horizon_unit` and
  `calendar.align_period` keep their calendar enums; a dynamic grain there raises
  a structured error.

## Decisions

| Axis | Decision |
|------|----------|
| Dynamic grain range | Sub-day equal-width multiples only (`N x {second,minute,hour}`); calendar grains stay `count == 1`. |
| Sub-day width invariant | `86400 % width_seconds == 0` — a Grain invariant (divides a day evenly). Rejects 7 minute, 5 hour, 25 minute. |
| Requested-vs-base rule | Sub-day request: base must be sub-day and `width_req % width_base == 0` (integer multiple). Calendar request: `unit_rank(req) >= unit_rank(base)`. |
| Public API surface | Structured `Grain(count=..., unit=...)` or `(count, unit)` tuple; legacy `"day"`-style strings still accepted for `count == 1`. |
| Canonical internal type | `Grain` frozen value object; serialized to a canonical token string at every persisted boundary. |
| Operator scope | `observe` bucketing + faithful evidence + **`compare` dynamic-bucket support**; `forecast`/`calendar` reject dynamic grain. |
| Sub-day bucket anchoring | Session-local midnight, reset daily (epoch-floor of seconds-since-local-midnight). |

## Design

### 1. Grain type model

New module `marivo/analysis/windows/grain.py` (a small, well-bounded unit;
`marivo/analysis/windows/spec.py` re-exports to limit import churn).

```python
GrainUnit = Literal["second","minute","hour","day","week","month","quarter","year"]

_UNIT_RANK     = {"second":0,"minute":1,"hour":2,"day":3,"week":4,"month":5,"quarter":6,"year":7}
_SUBDAY_UNITS  = frozenset({"second","minute","hour"})
_UNIT_SECONDS  = {"second":1, "minute":60, "hour":3600}
_TRUNCATE_CODE = {"second":"s","minute":"m","hour":"h","day":"D","week":"W","month":"M","quarter":"Q","year":"Y"}
_DAY_SECONDS   = 86_400

class Grain(BaseModel):                 # model_config: frozen=True, extra="forbid"
    count: int = 1
    unit: GrainUnit
    # validators:
    #   - count >= 1
    #   - calendar units (rank >= day) MUST have count == 1
    #   - sub-day units MUST satisfy _DAY_SECONDS % (count * _UNIT_SECONDS[unit]) == 0

    @property
    def is_subday(self) -> bool: ...     # unit in _SUBDAY_UNITS
    @property
    def is_day(self) -> bool: ...        # count == 1 and unit == "day"
    def width_seconds(self) -> int: ...  # sub-day only: count * _UNIT_SECONDS[unit]
    def to_token(self) -> str: ...       # "day" | "5minute" | "30second"

GrainInput = Grain | tuple[int, str] | str | None

def normalize_grain(value: GrainInput) -> Grain | None: ...
def parse_grain_token(text: str) -> Grain: ...  # "day" / "5minute" / "5 minute" / "5min" / "30s"
```

- **Construction.** `Grain` is a pydantic model, so it is constructed by keyword:
  `Grain(count=5, unit="minute")`. Positional `Grain(5, "minute")` is **not**
  valid; the positional ergonomic form is the tuple `(5, "minute")` accepted by
  `normalize_grain`. Examples and docs use `(5, "minute")` or
  `Grain(count=5, unit="minute")` — never bare positional.
- `count == 1` calendar units may still be written as the legacy string `"day"`;
  `normalize_grain("day") -> Grain(count=1, unit="day")`. Backward compatible.
- **Token format:** `count == 1` -> bare unit word; `count > 1` ->
  `"<count><unit>"` (e.g. `"5minute"`). Leading digits + alphabetic unit word is
  unambiguous; the `'m'`/`'M'` ibis ambiguity lives only inside `_TRUNCATE_CODE`.
- `parse_grain_token` exists for JSON token round-trip and alias tolerance; it is
  not the blessed public path.
- **Base granularity extension:** `time_field(granularity=...)` Literal becomes
  `Literal["year","quarter","month","week","day","hour","minute","second"]`.
  `FieldIR.granularity` stays `str | None`; only the accepted domain widens.

### 2. Validation rules

Three layers, each owning one concern:

1. **Grain invariants** (pydantic validators on `Grain`): `count >= 1`; calendar
   units forced to `count == 1`; sub-day units must divide a day evenly
   (`86400 % width_seconds == 0`). This rejects `Grain(7, "minute")`,
   `Grain(5, "hour")`, `Grain(25, "minute")` at construction.

2. **Requested grain vs base granularity** (`ensure_grain_supported(grain,
   base_granularity)`):

   ```python
   def ensure_grain_supported(grain: Grain, base: str) -> None:
       if grain.is_subday:
           if base not in _SUBDAY_UNITS:            # base is day-or-coarser
               raise AnalysisError(...)             # sub-day request finer than base
           if grain.width_seconds() % _UNIT_SECONDS[base] != 0:
               raise AnalysisError(...)             # not an integer multiple of base
       else:                                        # calendar request (count == 1, day+)
           if _UNIT_RANK[grain.unit] < _UNIT_RANK[base]:
               raise AnalysisError(...)             # finer than base
   ```

   Called in `observe` once the window grain and the resolved target time field
   are both known, before execution (fail-fast). Errors carry structured
   `requested`, `base`, `hint`. Examples: base `hour` + `(5, "minute")` rejected
   (sub-day request, base not sub-day); base `minute` + `(90, "second")` rejected
   (`90 % 60 != 0`); base `minute` + `(5, "minute")` accepted (`300 % 60 == 0`).

   > Consequence to confirm: `(90, "second")` is a *constructible* Grain (90
   > divides a day) and is permitted only when the base granularity is `second`
   > (`90 % 1 == 0`). It is rejected against any coarser base. This is the
   > faithful reading of "integer multiple of base".

3. **Base granularity vs `data_type`** (`marivo/semantic/validator.py`, authoring
   time): a sub-day base granularity (`hour`/`minute`/`second`) requires a
   data_type that carries time — `datetime`/`timestamp`, `integer` with an
   epoch/time-bearing format, or `string` with a time-bearing format.
   `granularity="second"` with `data_type="date"` is rejected.

### 3. Bucketing engine

A single helper replaces the scattered `truncate(grain)` calls
(`marivo/analysis/executor/runner.py:893,904,940,960`):

```python
def bucket_start_expr(local_ts, grain: Grain):   # local_ts already session-local
    if grain.count == 1:
        if grain.is_day:
            return local_ts.cast("date").name("bucket_start")        # unchanged
        return local_ts.truncate(_TRUNCATE_CODE[grain.unit]).name("bucket_start")
    # count > 1  =>  sub-day, equal width, anchored to session-local midnight
    W         = grain.width_seconds()
    day_start = local_ts.truncate("D")
    delta_s   = day_start.delta(local_ts, "second")      # seconds since local midnight
    offset_s  = (delta_s // W) * W
    return (day_start + offset_s.as_interval("s")).name("bucket_start")
```

- **Reuses existing timezone handling.** `_local_bucket_expr` already normalizes
  instant / naive / declared-tz columns to session-local `local_expr`; the
  truncate-vs-epoch-floor dispatch is added inside it. The string-format
  (strptime) path applies the same helper to the parsed timestamp.
- **Daily midnight reset is safe by construction.** The Grain invariant
  guarantees `86400 % W == 0`, so every sub-day bucket tiles a day perfectly —
  there is never a short trailing bucket before midnight.
- **`date` columns** never reach the sub-day branch (rejected by validation layer
  3), so the date path only sees day-or-coarser grain.
- **`count == 1` minute/second** route through `truncate` (which supports
  `s`/`m`/`h`), so existing `hour` behavior is unchanged; only `count > 1` uses
  epoch-floor.
- The exact ibis surface for `delta()` / `as_interval()` is validated during
  implementation (must push down on DuckDB and MySQL); the semantics are
  "seconds-since-local-midnight, floored to width `W`".

### 4. Threading and the serialization boundary

`Grain` is the in-memory type **inside the analysis boundary only**. Every
surface that persists, digests, or hands grain to a plain `dict` stores
`grain.to_token()` (a string). Verified sites:

- `AbsoluteWindow.grain: Grain | None` — a `field_validator(mode="before")`
  normalizes `GrainInput`/token -> `Grain`; a `field_serializer` emits the token.
  `dump_window` writes `{"grain": "5minute", ...}`. Round-trips losslessly.
- Frame axes: `marivo/analysis/intents/observe.py:509,550,716` change
  `"grain": resolved_window.grain` -> `resolved_window.grain.to_token()`.
- `marivo/analysis/intents/observe_planner.py:1238` (`"grain": resolved_window.grain`)
  -> token.
- Escape-hatch promotion: `marivo/analysis/escape_hatch.py:742-743`
  (`time_meta["grain"] = resolved_window.grain`) -> token; the reader at
  `escape_hatch.py:325` (`window.get("grain")`) stays string.
- Executor reconstruction: `marivo/analysis/executor/runner.py:841` builds
  `AbsoluteWindow(..., grain=window.get("grain"))` from a dict; the
  `AbsoluteWindow` validator parses the token back into `Grain`.
- Job record / params digest (`_params_digest`, `write_job_record`) and lineage
  params: grain enters these as a token, never as a `Grain` object, so
  `json.dumps(..., default=str)` digests are stable.

Day-equality string comparisons become `grain.is_day`:
`marivo/analysis/intents/observe.py:502,665`;
`marivo/analysis/executor/runner.py:902,934,957`. `is_time_series = grain is not
None` is unchanged.

### 5. Evidence faithfulness

- `Subject.grain`: `Literal["hour","day","week","month"] | None` becomes
  `str | None`, holding `grain.to_token()`. Fixes sub-day grains and the
  currently-dropped `quarter`/`year`.
- `observe`'s `_subject_grain` narrowing
  (`marivo/analysis/intents/observe.py:1110-1124`) is replaced by passing the
  token through; the `subject_grain` parameter
  (`marivo/analysis/intents/observe.py:1167,1185`) widens to `str | None`.

### 6. Out-of-scope guards (forecast, calendar)

- **forecast:** `horizon_unit` Literal is unchanged. When forecast derives grain
  from its input frame and that grain is dynamic / sub-day, it raises a
  structured `AnalysisError` ("forecast supports day..quarter calendar grains
  only"), located where the forecast intent reads the frame grain
  (`marivo/analysis/intents/forecast.py`).
- **calendar:** `align_period` is set by `CalendarPolicy` independently and is not
  derived from analysis grain; dynamic grain does not flow in. No change beyond
  confirming it is never fed a dynamic grain.

### 7. Compare dynamic-grain support

`compare` has two alignment paths. The **overlap join** (matching equal
`bucket_start` values) already works for any bucket values and needs no change.
The **window_bucket ordinal** path (used when buckets do not overlap) enumerates
buckets by grain and currently hard-rejects anything outside the calendar enum
(`marivo/analysis/intents/compare.py:851-864`). It is generalized:

- `_grain_from_axes` (`compare.py:1734`) widens from
  `Literal["hour","day","week","month"]` to the faithful grain token `str | None`
  (read from `axes["time"]["grain"]`, parseable by `parse_grain_token`).
- `_bucket_key` (`compare.py:825`): for a sub-day grain, floor the timestamp to
  its `W`-second bucket anchored to session-local midnight (matching observe) and
  format as an ISO timestamp `"%Y-%m-%dT%H:%M:%S"`. The existing `hour`
  special-case becomes the `W = 3600` instance of this rule.
- `_truncate_bucket_*` / `_advance_bucket_*` (`compare.py:790,808`): add sub-day
  variants operating on `datetime` — truncate to the `W`-second bucket, advance by
  `W` seconds. Calendar units keep their `date`-level logic.
- `_window_bucket_values` (`compare.py:835`): accepts sub-day grain and steps the
  start..end sequence by `W` seconds. **Safety cap:** if the generated sequence
  would exceed a bounded count (e.g. 100k buckets), raise a structured
  `AlignmentFailedError` ("window_bucket ordinal alignment would generate N
  buckets; reduce the window or coarsen the grain") rather than building an
  unbounded series.
- The fixed-enum guard at `compare.py:851-858` becomes "accept calendar grains OR
  a valid sub-day grain token".

### 8. Migration and compatibility

- `TimeGrain` (the old Literal) is kept as a deprecated alias of `GrainInput` to
  limit import churn; `Grain` / `GrainUnit` / `GrainInput` are the new names.
- **Public exports.** The structured path is `marivo.analysis.Grain`. Add `Grain`
  (and `GrainInput`) to `marivo/analysis/windows/__init__.py` and
  `marivo/analysis/__init__.py` (`__all__` + imports), and surface it anywhere
  `help` / runtime docs enumerate the grain type
  (`marivo/analysis/help.py`).
- Existing `grain="day"` calls and tests keep identical behavior.
- **Examples** (mandated by `agent-guide.md`): update
  `marivo-skills/marivo-*/references/examples/` entries that show `granularity=`
  / `grain=`, using keyword/tuple `Grain` construction, and add one dynamic-grain
  example; `make examples-check`.
- **Docs:** `time_field` and `observe` docstrings; three specs —
  `docs/specs/semantic/python-semantic-layer.md` (granularity adds
  minute/second), `docs/specs/analysis/python-analysis-operator-design.md`
  (grain = `Grain` / dynamic), `docs/specs/analysis/python-track-evidence-surface.md`
  (`Subject.grain` token).

### 9. Testing strategy

- `grain.py` units: parse/normalize (tuple/str/token/alias/invalid); validators
  (calendar `count > 1` rejected, `count < 1` rejected, sub-day non-divisor of a
  day rejected — 7 minute, 5 hour, 25 minute); ordering; `to_token` round-trip;
  keyword vs tuple construction; positional `Grain(5,"minute")` rejected.
- `ensure_grain_supported`: base `hour` + `5 minute` rejected; base `minute` +
  `90 second` rejected; base `minute` + `5 minute` accepted; base `second` +
  `90 second` accepted; calendar request rank checks.
- Base granularity extension: `granularity="minute"`/`"second"` accepted;
  data_type guard rejects `second` + `date`.
- Bucketing correctness (existing DuckDB test harness): expected `bucket_start`
  for 5/10/30 minute and 2 hour; midnight reset; timezone correctness; across the
  datetime / integer-epoch_seconds / string-strptime paths; `count == 1`
  minute/second equals `truncate`.
- Serialization: `AbsoluteWindow` JSON round-trip `Grain` <-> token;
  `dump_window` is a string; observe axes / observe_planner / escape-hatch
  promotion store tokens; `_params_digest` stable across a Grain-bearing observe.
- Evidence: `Subject.grain == "5minute"`.
- Compare: dynamic-grain frames align via overlap join; window_bucket ordinal
  path generates sub-day buckets and enforces the safety cap.
- Out-of-scope: forecast/calendar with dynamic grain raise structured errors.
- Backward compatibility: existing `grain="day"` suite stays green; `make test`.

## File-by-file change map

| File | Change |
|------|--------|
| `marivo/analysis/windows/grain.py` | **New.** `Grain`, `GrainUnit`, `GrainInput`, `normalize_grain`, `parse_grain_token`, rank/width/token helpers, `ensure_grain_supported`, `_DAY_SECONDS` invariant. |
| `marivo/analysis/windows/spec.py` | `AbsoluteWindow.grain: Grain | None` with validator/serializer; re-export grain types; `TimeGrain` -> deprecated alias; `make_absolute_window` normalizes. |
| `marivo/analysis/windows/__init__.py` | Export `Grain`, `GrainInput`. |
| `marivo/analysis/__init__.py` | Import + `__all__` `Grain`, `GrainInput`. |
| `marivo/analysis/help.py` | Surface `Grain` in runtime/grain docs. |
| `marivo/analysis/executor/runner.py` | Unified `bucket_start_expr`; epoch-floor sub-day branch; replace `grain == "day"`; token reconstruction at 841 via validator. |
| `marivo/analysis/intents/observe.py` | `grain: GrainInput`; `is_day` checks; `.to_token()` in axes (509/550/716); faithful `subject_grain` token. |
| `marivo/analysis/intents/observe_planner.py` | `.to_token()` at 1238. |
| `marivo/analysis/escape_hatch.py` | `.to_token()` at 742-743. |
| `marivo/analysis/intents/compare.py` | Dynamic-grain `_bucket_key`/`_truncate`/`_advance`/`_window_bucket_values`; widen `_grain_from_axes`; safety cap. |
| `marivo/analysis/evidence/types.py` | `Subject.grain: str | None`. |
| `marivo/analysis/intents/forecast.py` | Reject dynamic grain with structured error. |
| `marivo/semantic/authoring.py` | `time_field` granularity Literal adds `minute`, `second`; docstring. |
| `marivo/semantic/validator.py` | Base-granularity vs data_type guard. |
| `marivo-skills/marivo-*/references/examples/` | Update affected examples; add a dynamic-grain example. |
| `docs/specs/...` | semantic granularity, analysis grain, evidence surface. |
| `tests/...` | New grain tests + bucketing/observe/serialization/evidence/compare/guards coverage. |

## Open implementation-time validations

- Confirm the `(90, "second")` consequence: permitted only against a `second`
  base (integer-multiple rule), rejected against `minute`+ bases.
- Pick the compare window_bucket safety-cap value (proposed 100k buckets).
- ibis `delta()` / `as_interval()` (or epoch-arithmetic equivalent) pushes down
  correctly on DuckDB and MySQL.
