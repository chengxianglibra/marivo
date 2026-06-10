# Semantic Time Field Format — Canonical strptime (Subtractive Fix)

Status: Approved design (ready for plan)

## Problem

`session.observe(..., time_dimension=<string/integer column>)` against Trino or
Presto backends fails with `INVALID_FUNCTION_ARGUMENT` (`Invalid format:
"yyyyMMdd"` or similar). The failure has nothing to do with format strings
leaking through uncompiled — it is caused by Marivo itself rewriting correct
SQL into a format the backend rejects.

### Root cause evidence

Trino documentation (https://trino.io/docs/current/functions/datetime.html)
and Presto documentation (https://prestodb.io/docs/current/functions/datetime.html)
both split date functions into two distinct sections:

- **MySQL date functions**: `date_format()`, `date_parse()`. Format is
  "compatible with the MySQL `date_parse` and `str_to_date` functions."
  Specifiers: `%Y %m %d %H %i %S %y %j ...`. Documented example:
  `date_parse('2022/10/20/05', '%Y/%m/%d/%H')`.
- **Java date functions**: `format_datetime()`, `parse_datetime()`. Format
  is "compatible with JodaTime's `DateTimeFormat` pattern format."

Trino and Presto's `date_parse` therefore accepts **MySQL/strptime style**,
not Joda. Joda format is only accepted by the separate `parse_datetime`
function.

ibis 12 emits the correct format directly:

```
as_date("%Y%m%d")                  -> CAST(DATE_PARSE(col, '%Y%m%d') AS DATE)
as_timestamp("%Y-%m-%d %H:%M:%S")  -> DATE_PARSE(col, '%Y-%m-%d %H:%M:%s')
```

Marivo's post-compilation regex `_fix_trino_date_parse`
(`marivo/analysis/executor/runner.py:1491-1517`) rewrites these to Joda
(`yyyyMMdd`, `yyyy-MM-dd HH:mm:ss`), which Trino/Presto's `date_parse`
rejects. The accompanying comment (runner.py:1500-1504) incorrectly claims
"Trino's `date_parse()` expects Joda Time format strings" — this is
factually wrong, and is the source of the bug.

The translation layer exists for no current reason. It was likely written
against an older ibis that emitted `parse_datetime` (which does want
Joda); ibis now emits `date_parse` (MySQL), and the regex is stale.

## Approach

Two orthogonal changes bundled as one breaking-change release:

1. **§1 — Delete the Joda translation layer.** Pure subtractive fix.
   ibis emits correct SQL; Marivo stops breaking it.
2. **§2 — Normalize the author surface.** `date_format` accepts only
   Python strptime; shorthand aliases, `epoch_seconds`, and `date_format`
   on already-temporal types are removed.

The two are bundled because both touch the same author-facing surface
and the same set of tests. Splitting them would force two migrations on
`.marivo/semantic/<model>/_domain.py` authors.

## §1 — Delete the Joda translation layer

### Deletions

| Location | Removed |
|---|---|
| `runner.py:75-93` | `_STRPTIME_TO_JODA` directive map |
| `runner.py:95` | `_NO_JODA_EQUIVALENT` set |
| `runner.py:98-122` | `StrptimeToJodaError`, `_strptime_to_joda` |
| `runner.py:1491-1517` | `_DATE_PARSE_FMT_RE`, `_fix_trino_date_parse` |
| `runner.py:1551, 1570` | `if dialect == "trino": sql = _fix_trino_date_parse(sql)` branches in `execute()` |
| `tests/test_analysis_window_encoding.py:10-15` | imports of `StrptimeToJodaError`, `_strptime_to_joda` |
| `tests/test_analysis_window_encoding.py:688-751` | all `test_strptime_to_joda_*` unit tests and the surrounding section header |

### What stays

- ibis's `as_date(fmt)` / `as_timestamp(fmt)` calls in `_parse_string_column`
  (runner.py:419-420). Unchanged. ibis emits `DATE_PARSE(col, fmt)` with
  the author's strptime format string verbatim.
- `_classify_strptime_format` (runner.py:125-146). Still needed by the
  bucket logic to decide whether to truncate or pass through.
- The ClickHouse dateTrunc fixer (`_fix_clickhouse_datetrunc`). Orthogonal.

### What this does not change

- Dialect is **not** threaded into `apply_time_series_bucket`,
  `apply_window_to_dataset`, or any window/bucket function. The format
  string flows ibis → backend SQL unchanged.
- No format translator. No `translate_for_dialect`. The
  `marivo/semantic/time_format.py` module introduced in §2 contains only
  `normalize_strptime` (a syntax validator), not a per-backend translator.

## §2 — Canonical strptime author surface

`@ms.time_dimension(date_format=...)` accepts **only** Python strptime strings
(`%Y-%m-%d`, `%Y%m%d`, `%Y%m%d%H`, `%Y-%m-%d-%H`, ...).

Removed:
- Shorthand aliases: `yyyymmdd`, `yyyymmddhh`, `yyyy-mm-dd`,
  `yyyy-mm-dd-hh`, `yyyymmdd-hh`, `yyyymmddthh`, `hh`, `h`, `int`.
- `epoch_seconds` support (deferred to a separate proposal).
- `date_format` on `data_type` ∈ {`"date"`, `"datetime"`, `"timestamp"`}
  (these columns are already temporal and need no parsing format).
- `date_format` on hour-only fields that rely on `required_prefix`.

Hour-only fields (separate hour column referenced via `required_prefix`)
declare only `data_type` (`"string"` or `"integer"`), `granularity="hour"`,
and `required_prefix`. The runtime unconditionally `lpad(2, "0")`-normalizes
the string-converted hour before concatenation and sorting; this is
idempotent on already-zero-padded values, so source columns holding either
`"01"` or `"1"` are handled uniformly.

### Deletions (supporting code)

| Location | Removed |
|---|---|
| `runner.py:52` | `("integer", "epoch_seconds")` constant |
| `runner.py:54-55` | `_HOUR_PRECISION_FORMATS`, `_HOUR_ONLY_FORMATS` |
| `runner.py:59-66` | `_SHORTHAND_TO_STRPTIME` |
| `runner.py:149-167` | `_resolve_strptime_format` (collapses to identity; IR stores canonical strptime) |
| `runner.py:170-195` | `_normalize_time_format` |
| `runner.py:209, 676, 1054, 1269` | epoch_seconds code paths |
| `validator.py:68-75` | `_normalized_time_format` (merged into a single `normalize_strptime` living in `marivo/semantic/`) |

### Merge: single normalizer

`marivo/semantic/time_format.py` exposes:

```python
def normalize_strptime(value: str) -> str:
    """Strip whitespace and validate Python strptime syntax."""
```

Input must be `%`-prefixed. Non-`%` input raises `ValueError`. Both
`validator.py` and `runner.py` import this single function.

### IR invariant

`DimensionIR.format` is constrained by:

- `data_type` ∈ {`"string"`, `"integer"`} and no `required_prefix` →
  must be a valid strptime string.
- `data_type` ∈ {`"date"`, `"datetime"`, `"timestamp"`} → must be `None`.
- `required_prefix is not None` → must be `None` (hour-only fields carry
  no format).

`@ms.time_dimension` enforces this at decoration time. The IR loader trusts
the invariant.

### Validator simplification

`_requires_required_prefix` (`validator.py:107-126`) no longer inspects
the `format` string. The new rule:

- `granularity == "hour"` and `data_type` ∈ {`"string"`, `"integer"`} and
  `required_prefix is None` → raise (hour-only fields must declare a
  prefix).

### Migration table

| Old form | New form |
|---|---|
| `date_format="yyyymmdd"` | `date_format="%Y%m%d"` |
| `date_format="yyyymmddhh"` | `date_format="%Y%m%d%H"` |
| `date_format="yyyy-mm-dd-hh"` | `date_format="%Y-%m-%d-%H"` |
| `date_format="hh"` / `"h"` / `"int"` | omit `date_format`; declare `required_prefix` |
| `data_type="integer", date_format="epoch_seconds"` | not supported (remove) |

## §3 — Format semantics caveat: `%M` vs `%i`

Python strptime and MySQL/Trino/Presto `date_parse` agree on most common
specifiers (`%Y %m %d %H %S %y %j`), but disagree on minutes:

- Python strptime: `%M` = minutes (00..59)
- MySQL/Trino/Presto: `%M` = month name (January..December); minutes is `%i`

Marivo performs **no translation**. The format string flows ibis → SQL
verbatim. Authors of minute-granularity string fields must write
`date_format="%Y-%m-%d %H:%i"` (MySQL `%i`), not `"%H:%M"` (Python would
read `%M` as minutes; the backend reads it as month name).

This is documented as a known semantic divergence. Rationale: introducing
a `%M` → `%i` translator would re-establish exactly the kind of format
translation layer this design deletes. The contract is "the format string
reaches the backend's `date_parse` unchanged; author with target-backend
semantics in mind."

Affected specifiers (documented in author guide):

- `%M` — Python minutes vs MySQL month name. Use `%i` for minutes on
  Trino/Presto backends.
- `%c` — Python locale-dependent datetime vs MySQL month numeric (1..12).
  Avoid; use `%m` or `%-m` patterns instead.
- Trino-unsupported per Trino docs: `%D`, `%U`, `%u`, `%V`, `%w`, `%X`.
  Marivo does not pre-validate against backend support; queries using
  these will fail at the backend with the backend's native error.

## §4 — Validation and testing

### Validation timing

| Check | When |
|---|---|
| strptime syntax (`time.strptime` accepts it) | `@ms.time_dimension` decorator |
| granularity consistent with strptime tokens (sub-hour tokens cannot pair with `granularity="day"`) | decorator |

No backend-compatibility check at decorator time. A field is valid for
any backend whose `date_parse` accepts its specifiers; mismatches surface
at query time with the backend's native error.

### Test changes

**Delete:**
- All `test_strptime_to_joda_*` tests (`tests/test_analysis_window_encoding.py:688-751`).
- Tests for `_fix_trino_date_parse` regex behavior, shorthand alias
  resolution, `_HOUR_ONLY_FORMATS` / `_HOUR_PRECISION_FORMATS` /
  `_SHORTHAND_TO_STRPTIME` internals, and `epoch_seconds`.

**Add:**

| Test | Covers |
|---|---|
| `@ms.time_dimension(date_format="yyyymmdd", ...)` raises | shorthand rejected (regression) |
| `@ms.time_dimension(date_format="%Q", ...)` raises | invalid strptime |
| `@ms.time_dimension(data_type="date", date_format="%Y-%m-%d", ...)` raises | temporal type rejects format |
| hour-only field with `date_format` raises | hour-only rejects format |
| end-to-end Trino strptime observe: assert emitted SQL contains `DATE_PARSE(col, '%Y%m%d')` verbatim, no Joda | regression for the actual bug |
| end-to-end Presto-as-Trino observe: same assertion | regression for the original reported failure |
| end-to-end DuckDB strptime observe | non-trino path unchanged |

### Reproduction-first implementation

The first commit of the implementation **must** reproduce the original
`INVALID_FUNCTION_ARGUMENT` against the current codebase, then pass after
the refactor. Acceptable forms:

- User-supplied actual failing SQL + Trino error message as fixture.
- Or: minimal end-to-end test that compiles an `observe()` plan over a
  `data_type="string"`, `date_format="%Y%m%d"` field through the Trino
  dialect, asserts the compiled SQL contains `DATE_PARSE(..., 'yyyyMMdd')`
  on `main` (the bug), then asserts `DATE_PARSE(..., '%Y%m%d')` after
  refactor.

This gate must pass before the PR is considered complete.

## Acceptance criteria

- [ ] `make test` green.
- [ ] `make typecheck` green.
- [ ] `make lint` green.
- [ ] Reproduction test from §4 committed first, fails on `main`, passes
      after refactor.
- [ ] Manual run: real Trino `session.observe(time_dimension=<partition column>,
      ...)` no longer raises `INVALID_FUNCTION_ARGUMENT`.
- [ ] At least one `.marivo/semantic/` model file migrated from shorthand
      to strptime as dogfooding evidence.

## Breaking change callout (for PR description and changelog)

- **Bug fix**: Trino/Presto `date_parse` queries no longer have their
  format string rewritten to Joda. ibis emits MySQL-compatible format
  directly, which is what `date_parse` actually accepts.
- Removed shorthand aliases: `yyyymmdd`, `yyyymmddhh`, `yyyy-mm-dd`,
  `yyyy-mm-dd-hh`, `yyyymmdd-hh`, `yyyymmddthh`, `hh`, `h`, `int`.
- Removed `epoch_seconds` support (deferred).
- Hour-only fields (`required_prefix` mode) no longer accept `date_format`.
- `data_type` ∈ {`"date"`, `"datetime"`, `"timestamp"`} no longer accepts
  `date_format`.
- Removed public-by-convention helpers `_strptime_to_joda`,
  `_fix_trino_date_parse`, `StrptimeToJodaError`. (All were private with
  leading underscore but imported by tests.)
- Documented Python-vs-MySQL specifier divergence: `%M` means minutes in
  Python but month name in Trino/Presto `date_parse`. Use `%i` for
  minutes on string-parsed time fields.

Authors updating existing `.marivo/semantic/<model>/_domain.py` files:
rewrite `date_format="yyyymmdd"` → `date_format="%Y%m%d"` (and similarly
for the other shorthands per the migration table in §2); drop
`date_format` from hour-only and from temporal-typed fields; for any
minute-granularity string field, switch `%M` to `%i`.
