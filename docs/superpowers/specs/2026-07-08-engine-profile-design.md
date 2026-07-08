# Engine Profile Design

Date: 2026-07-08
Status: approved (design review complete; implementation not started)

## Summary

Consolidate per-engine backend behavior — today scattered across ten touch
points in three layers — into one `EngineProfile` value object per engine,
registered in a closed registry under `marivo/datasource/engines/`. Adding a
new engine becomes: one engine module, one registry entry, and the public
authoring spec/function that is already per-engine by design.

The motivating complaint was "`runner.py` is too large because engine logic
is mixed in". The size diagnosis and the engine diagnosis are separate
problems: of `marivo/analysis/executor/runner.py` (2070 lines, 80
functions), only five touch points are engine-specific; ~90% is
engine-agnostic ibis expression construction (window bound coercion,
timezone resolution, bucketing, partition encoding). This design therefore
does two orthogonal things in one effort: (a) an engine profile abstraction
across the whole repository, and (b) a responsibility-based split of
`runner.py`.

## Settled Decisions

1. **Profile, not interface.** ibis is already the cross-engine execution
   abstraction. Per-engine differences in Marivo are quirk patches on ibis
   gaps (strptime specifier translation, ClickHouse `dateTrunc` rewrite,
   datetime decode semantics), not divergent execution flows.
   `EngineProfile` is a frozen dataclass of data plus precisely typed
   callables with shared identity defaults — not an ABC hierarchy, where
   four of five engines would implement most methods as no-ops.
2. **Full-repository scope, one effort.** All ten touch points migrate:
   datasource (connect dispatch, read-only kwargs, timezone probe SQL,
   metadata introspection, identifier quoting, authoring-function map,
   supported-list error guidance, doctor module map), analysis (executor
   quirks, quantile capabilities), semantic (materializer percentile
   branch).
3. **Behavior-preserving.** This is a refactor; no engine's emitted SQL or
   results change. In particular the materializer keeps its trino-only
   approximate-percentile branch as an explicit profile flag. Reconciling
   ClickHouse quantile semantics between the materializer and
   `sampled_fold` is visible after consolidation but explicitly out of
   scope.
4. **Public API zero change.** `md.duckdb(...)` and friends stay where they
   are; profiles never enter any `__all__`; the public-surface snapshot
   test must not change.
5. **`GENERIC_PROFILE` replaces the `"unknown"` dialect string.** Expression
   builders that today default `dialect: str = "unknown"` take an
   `EngineProfile` defaulting to a generic profile with identity behaviors.
   `QueryExecution.dialect` stays a plain string (`profile.name`) — that is
   query provenance metadata, not dispatch.
6. **No transition shims.** Moved symbols are not re-exported from their
   old homes; all importers (including the seven test files that import
   `runner.py` privates) are updated in the same commit as each move.

## Touch Point Inventory

| Layer | File | Engine-specific logic today |
|-------|------|------------------------------|
| datasource | `backends.py` | connect builder if/elif chain; read-only kwargs shaping (duckdb `read_only=True` vs clickhouse `settings.access_mode`) |
| datasource | `timezone.py` | per-engine timezone probe SQL dict (incl. a `"postgresql"` alias key) |
| datasource | `metadata.py` | cursor row decode (DB-API vs clickhouse `QueryResult`); duckdb view predicate; trino column decode and `SHOW CREATE` partition parsing |
| datasource | `manage.py:616` | identifier quoting (backtick for mysql/clickhouse, double quote otherwise) |
| datasource | `store.py:55`, `constraints.py` | backend-to-authoring-function map; hardcoded supported-backend tuples in error guidance |
| top-level | `doctor.py:109` | per-engine required ibis module map |
| analysis | `executor/runner.py` | strptime translation for trino/mysql; clickhouse datetime decode policy; `_fix_clickhouse_datetrunc` SQL rewrite; two `if dialect == "clickhouse"` compile-hook branches; `dialect: str = "unknown"` threaded through ~10 signatures |
| analysis | `intents/sampled_fold.py:24` | `_QUANTILE_CAPABILITIES` per-engine table |
| semantic | `materializer.py:444` | trino percentile uses `approx_quantile` |

Excluded: `semantic/parity.py` — its dialect handling is provenance metadata
on authored SQL, not engine behavior dispatch.

## Package Layout

```
marivo/datasource/engines/
    base.py        # EngineProfile + sub-objects + GENERIC_PROFILE
    duckdb.py      # each engine module builds and exports one PROFILE
    trino.py
    mysql.py
    postgres.py
    clickhouse.py
    __init__.py    # ENGINE_PROFILES registry + lookup helpers
```

The package lives inside `marivo/datasource/` rather than as a new
top-level package because the dependency direction already holds: semantic
and analysis import datasource. Engine modules need authoring/spec types
from datasource siblings; a top-level `marivo/engines/` would create a
package-level cycle (datasource dispatch imports engines, engines import
datasource types).

## EngineProfile Fields

Every field has a real consumer today; no speculative fields. Engines with
no quirk share module-level identity defaults rather than writing no-op
methods.

| Field | Type (sketch) | Today's consumer |
|-------|---------------|------------------|
| `name` | `str` | registry key; `QueryExecution.dialect` provenance |
| `aliases` | `tuple[str, ...]` | ibis backend-name lookup (postgres profile carries `"postgresql"`) |
| `authoring_func` | `str` | `store.py` convenience-function map |
| `required_modules` | `tuple[str, ...]` | `doctor.py` dependency checks |
| `connect` | `Callable[[str, Mapping[str, object]], BaseBackend]` | `backends.py` builders |
| `apply_read_only_kwargs` | `Callable[[dict[str, object]], dict[str, object]]` | `backends.py` read-only shaping (identity default; duckdb/clickhouse override) |
| `timezone_probe_sql` | `str` | `timezone.py` |
| `identifier_quote` | `str` | `manage.py` quoting (`"` default; mysql/clickhouse backtick) |
| `metadata` | `EngineMetadataIntrospection` | `metadata.py` (row decode, view predicate, partition discovery; trino's `SHOW CREATE` parsing moves wholesale into `engines/trino.py`) |
| `translate_strptime_format` | `Callable[[str], str]` | executor string-column parsing (identity default; mysql/trino use `python_to_mysql_strptime`) |
| `postprocess_sql` | `Callable[[str], str]` | executor compile hook (identity default; clickhouse `dateTrunc` rewrite) |
| `datetime_decode_policy` | `BackendDatetimeDecodePolicy` | executor decode policy (`"local_naive_label"` default; clickhouse `"utc_naive_instant"`) |
| `quantile` | `QuantileCapability` | `sampled_fold` capability table |
| `percentile_uses_approx_quantile` | `bool` | materializer percentile branch (trino only; kept separate from `quantile` to preserve behavior) |

Typing: no `Any`-valued fields; callables carry concrete signatures. Built
backends are typed as `ibis.backends.BaseBackend`, tightening the informal
`Any` returns the current builders use.

`GENERIC_PROFILE` carries the identity defaults and
`"local_naive_label"`. It is not in the registry (it cannot be authored);
it exists only as the default for expression builders invoked without a
datasource, matching today's `dialect="unknown"` behavior exactly.

## Registry, Lookup, and Errors

- `ENGINE_PROFILES: Mapping[str, EngineProfile]` is an explicit literal
  dict in `engines/__init__.py`. Closed registration; no entry points or
  import-time side-effect discovery.
- `SUPPORTED_BACKEND_TYPES`, the `constraints.py` supported tuples, and
  unsupported-backend error suggestions all derive from registry keys —
  errors teach from real state, never hardcoded lists.
- Lookup helpers: by `backend_type` (authoring path) and by ibis
  `backend.name` (execution path, alias-aware, falling back to
  `GENERIC_PROFILE` with the same semantics as today's `"unknown"`).
- `runner.py`'s `datasource_backend_dialect` / `_backend_dialect` become a
  single profile resolver in the engines package.

## Call-Site Changes

- `backends.py`: if/elif chain becomes `profile.connect(...)` after
  `profile.apply_read_only_kwargs(...)`; builder bodies move to engine
  modules.
- `timezone.py`: probe SQL dict deleted; reads `profile.timezone_probe_sql`.
- `metadata.py`: per-engine decode/view/partition functions move to engine
  modules behind `profile.metadata`; the orchestration stays.
- `manage.py`: `_quote_backend_identifier` reads `profile.identifier_quote`.
- `store.py` / `constraints.py` / `doctor.py`: read
  `authoring_func` / registry keys / `required_modules`.
- `executor/runner.py`: `_parse_string_column` calls
  `profile.translate_strptime_format`; compile hooks call
  `profile.postprocess_sql` unconditionally (identity for most);
  `backend_datetime_decode_policy(dialect)` becomes
  `profile.datetime_decode_policy`; all `dialect: str` threading becomes
  `profile: EngineProfile = GENERIC_PROFILE`.
- `sampled_fold.py`: `_QUANTILE_CAPABILITIES` deleted; reads
  `profile.quantile`.
- `materializer.py`: `backend_type == "trino"` becomes
  `profile.percentile_uses_approx_quantile`.

## runner.py Responsibility Split

Same effort, separate commits, after the quirk migration lands. Target
shape under `marivo/analysis/executor/`:

- `windowing.py` — window bound coercion/encoding, timezone resolution,
  partition bound encoding.
- `bucketing.py` — bucket start expressions, bucket output kinds,
  sub-day bucketing.
- `string_time.py` — strptime classification and string/integer temporal
  column parsing.
- `runner.py` — `execute`, `ExecutionResult`, compile capture, query
  record glue only.

Exact function-to-module assignment is an implementation-plan detail with
one constraint: no module in the package exceeds ~500 lines, and nothing
engine-specific remains outside `engines/`. The seven test files importing
`runner.py` internals (`_parse_string_column`, `_fix_clickhouse_datetrunc`,
`bucket_start_expr`, `_column_timezone`, ...) update imports in the same
commit as each move; production importers (`observe.py`,
`observe_planner.py`, `observe_multi.py`, `derive.py`, `escape_hatch.py`,
`sampled_fold.py`) likewise.

## Testing

- New registry completeness test: registry keys equal
  `SUPPORTED_BACKEND_TYPES`; every profile populates required fields;
  every authoring spec's `backend_type` resolves to a profile. A new
  engine that skips registration fails loudly here.
- Existing per-engine behavior tests re-point at engine modules (e.g. the
  clickhouse `dateTrunc` compat test targets
  `engines/clickhouse.py`); where practical, per-engine assertions
  parametrize over the registry instead of naming engines ad hoc.
- Every commit lands green: `make test`, `make typecheck`, `make lint`.

## Sequencing

1. Add `engines/` package: `EngineProfile`, `GENERIC_PROFILE`, registry;
   migrate connect builders, read-only kwargs, `doctor.py`, `store.py`,
   `constraints.py` derivations.
2. Migrate remaining datasource touch points: timezone probe SQL,
   identifier quoting, metadata introspection strategies.
3. Migrate executor quirks; replace dialect string threading with profile
   parameters; retarget affected tests.
4. Consolidate quantile capabilities (`sampled_fold`, materializer flag).
5. Split `runner.py` by responsibility.

Each phase is one or more commits that each pass the full gates, so
in-flight worktree branches can rebase at commit granularity.

## Risks

- **In-flight branches.** A dozen worktrees hold divergent `runner.py`
  copies (1097–2070 lines); phases 3 and 5 will conflict with them. The
  per-commit-green sequencing above is the mitigation; coordinate landing
  order with active work.
- **Private test imports.** Seven test files import executor internals;
  missing one breaks the pre-commit pytest gate. The move commits update
  imports atomically.
- **Alias drift.** `"postgresql"` appears as an alias key today; the
  profile `aliases` field plus the registry completeness test make alias
  handling explicit instead of incidental.

## Acceptance Criteria

- Adding a new engine requires: `engines/<name>.py`, one registry entry,
  the public authoring spec/function, and docs — nothing else.
- Outside `marivo/datasource/engines/`, no `backend_type ==` /
  `dialect ==` / dialect-set membership conditionals remain in `marivo/`
  (provenance metadata fields and docstrings excepted).
- Public `__all__` snapshots unchanged; no site/docs updates required
  (no behavior change).
- `make test`, `make typecheck`, `make lint` green at every commit.
