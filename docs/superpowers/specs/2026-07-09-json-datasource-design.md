# JSON file datasource support via DuckDB

Add JSON as a first-class member of the existing DuckDB file-source family so
agents can build semantic layers over fixed-schema JSON — local files (single
or many via glob) and `http(s)://` endpoints reachable by DuckDB's httpfs —
using the same `discover -> author -> analyze` loop as parquet and CSV.

## Problem

Marivo already models physical file sources for two formats. `md.parquet(path)`
and `md.csv(path)` build `ParquetSourceIR` / `CsvSourceIR`, which are accepted
everywhere a physical source is expected (`entity(source=...)`, every
`md.discover_*`, schema inspection, materialization) and map to
`backend.read_parquet` / `backend.read_csv`.

There is no JSON equivalent. Users with fixed-schema JSON must first convert it
to parquet/csv or hand-author a DuckDB view. Two concrete shapes are common and
unserved:

- Local JSON, often as many files that share a schema but vary in content, e.g.
  `data/events/*.json`.
- JSON served over HTTP from a static-ish endpoint (object storage, CDN, a
  published data URL) that DuckDB's httpfs can read directly.

DuckDB reads all three targets — local path, glob, and `http(s)://` URL —
through a single `read_json` call, so the gap is purely a missing Marivo
surface, not a missing engine capability. (Verified: `read_json` over an HTTPS
URL returns rows in a fresh Marivo-built connection with httpfs auto-loaded.)

## Goals

- Add `JsonSourceIR` plus `md.json(...)` and `ms.json(...)` builders, mirroring
  the parquet/csv family in shape, validation, serialization, and help.
- Support local paths, glob patterns (multiple fixed-schema files), and
  `http(s)://` URLs, all through `backend.read_json`.
- Auto-enable DuckDB's connection-level `force_download` when a JSON source path
  is `http(s)://`, so dynamic/chunked/gzip endpoints work without manual config.
- Inherit discovery, semantic authoring, materialization, analysis, and
  round-trip persistence with no changes on the analysis side.
- Keep the surface minimal: only fields that change read behavior earn a place.

## Non-goals

- No schema-lock. `read_json`'s `columns` argument is a name->type map, not a
  projection list; exposing it is the "schema-locked" option that was
  considered and rejected. Column projection stays at the entity/dimension
  layer, as it already does for every source.
- No `union_by_name` field. Verified to be a no-op for JSON (see Design 3):
  JSON is inherently key-based, so DuckDB reconciles columns by name across
  files whether or not the flag is set. It earns its keep for positional
  parquet/csv, not for JSON.
- No `hive_partitioning` field. Partition-column-from-path extraction is a
  hive-layout concern; that data belongs behind the Trino backend, not a JSON
  file source. Multiple fixed-schema files are already covered by a glob path.
- No auth, pagination, POST bodies, request templating, or nested-array
  unwrapping (`{"data": [...]}`). Those "business API" shapes are handled
  upstream by a pull script that lands NDJSON/parquet, which this same
  `md.json` / `md.parquet` path then consumes. Explicitly out of scope.
- No new backend and no new datasource kind. JSON is a *source*, read through an
  existing DuckDB *datasource*.

## Design

### 1. `JsonSourceIR` — new closed-union member

Add to `marivo/datasource/ir.py`, mirroring `ParquetSourceIR`:

```python
@dataclass(frozen=True)
class JsonSourceIR:
    """Physical JSON source for an entity."""

    path: str
    format: Literal["auto", "newline_delimited", "array"] = "auto"
    kind: Literal["json"] = "json"

    def __post_init__(self) -> None:
        _require_non_empty_str(self.path, "JsonSourceIR.path")
        _require_json_format(self.format, "JsonSourceIR.format")
        _require_kind(self.kind, field_name="JsonSourceIR.kind", expected="json")

    def to_dict(self) -> dict[str, object]:
        return {"kind": self.kind, "path": self.path, "format": self.format}

    def to_ir(self) -> "JsonSourceIR":
        return self
```

Field rationale:

- `path` — local path, glob, or `http(s)://` URL. Same semantics as
  `ParquetSourceIR.path`.
- `format` — the one JSON-specific override that changes parsing. `"auto"` is
  DuckDB's default and detects newline-delimited vs. array; an explicit value
  resolves ambiguous cases (e.g. a single-line array that should be many rows).
  Maps to `read_json(format=...)`. `_require_json_format` validates the literal.
- `kind` — serialization discriminator (see Design 5). Always `"json"` for this
  class; not a user-facing parameter, kept out of `help`.

Extend the union: `EntitySourceIR = TableSourceIR | ParquetSourceIR |
CsvSourceIR | JsonSourceIR`, add `"JsonSourceIR"` to the module `__all__`, and
mirror the same one-line addition to `scan.py`'s `TableSource` alias and
`semantic/dtos.py`'s `FileSource` alias.

### 2. Builders `md.json` / `ms.json`

Datasource builder in `marivo/datasource/scan.py`, mirroring `parquet`:

```python
def json(
    path: str,
    /,
    *,
    format: Literal["auto", "newline_delimited", "array"] = "auto",
) -> JsonSourceIR:
    """Build a structured JSON source reference.

    Args:
        path: File path, glob pattern, or http(s):// URL.
        format: JSON layout; "auto" detects newline-delimited vs array.

    Returns:
        A ``JsonSourceIR`` suitable for ``ms.entity(source=...)`` or
        ``md.entity(source=...)``.
    """
    return JsonSourceIR(path=path, format=format)
```

Semantic builder in `marivo/semantic/authoring.py` mirrors `csv`/`parquet`
(delegates to the datasource builder), and `entity(source=...)` validation adds
`JsonSourceIR` to its accepted-source `isinstance` tuple and its docstring /
error message ("accepts ms.table(...), ms.parquet(...), ms.csv(...), or
ms.json(...)").

### 3. `read_json` mapping and verified kwarg facts

The reader dispatch (`isinstance(source, ...) -> backend.read_X(...)`) is
duplicated today across four read sites (Design 4). Each grows a JSON branch:

```python
elif isinstance(source, JsonSourceIR):
    json_kwargs: dict[str, object] = {}
    if source.format != "auto":
        json_kwargs["format"] = source.format
    expr = backend.read_json(source.path, **json_kwargs)
```

Only non-default kwargs are passed, matching the existing parquet/csv pattern.
Facts confirmed empirically against DuckDB 1.5.3 / ibis to keep this grounded:

- `read_json(path, format="newline_delimited")` — `format` passes through ibis
  to DuckDB. OK.
- `read_json(glob, columns=["id", "amount"])` — FAILS with `'list' object has
  no attribute 'items'`; `columns` is a name->type map, confirming the Non-goal.
- `read_json(glob)` vs `read_json(glob, union_by_name=True)` over two files with
  different column sets/order — identical output (`(a, b, c)` with NULLs for
  absent keys), confirming the `union_by_name` Non-goal for JSON.

Add `read_json(self, path, /, **options) -> ibis.Table` to the `IbisBackend`
protocol in `marivo/semantic/typing.py`, alongside `read_parquet`/`read_csv`.

### 4. HTTP auto-enable via a read-time helper

httpfs auto-loads, so static endpoints already work with no config. The only
gap is DuckDB's connection-level `force_download`, required for dynamic /
chunked / gzip responses. It is a session setting (`SET force_download=true`),
not a `read_json` argument, and the DuckDB connection is built generically
without knowing the source — so the enable happens at read time on the
already-built backend.

Verified safe and sufficient: on a generic backend, `raw_sql("SET
force_download=true")` before the read lets a static endpoint still return rows
(cars.json -> 406) and fixes a dynamic one that otherwise errors
(jsonplaceholder todos -> 200). The flag only affects httpfs reads, so it is
harmless when the same connection later reads local files; no reset needed.

Introduce one shared helper, e.g. in `marivo/datasource/backends.py`:

```python
def apply_json_http_settings(backend: Any, source: object) -> None:
    """Enable force_download for http(s) JSON sources; no-op otherwise."""
    if isinstance(source, JsonSourceIR) and source.path.lower().startswith("http"):
        backend.raw_sql("SET force_download=true")
```

Call it in each of the four read sites immediately before the JSON read:

- `marivo/datasource/manage.py` — `_execute_scoped_sample` (bounded discovery
  sample).
- `marivo/datasource/metadata.py` — schema-inference reader path.
- `marivo/semantic/scope.py` — semantic-side source expression build.
- `marivo/semantic/materializer.py` — materialization reader path.

The helper is the only new cross-cutting logic; the existing per-site dispatch
duplication is left as-is (surgical change, not a refactor).

### 5. Serialization round-trip (the `kind` discriminator)

Sources persist to project-local `.marivo/` state as plain dicts (`to_dict`
writes `"kind"`) and are rebuilt by `source_from_dict` in
`marivo/semantic/ir.py`, which dispatches on the `kind` string. In memory the
type is known via `isinstance`; once serialized, only `kind` distinguishes a
JSON dict from a parquet dict. Add the branch before the final `raise`:

```python
if kind == "json":
    return JsonSourceIR(
        path=str(data["path"]),
        format=str(data.get("format", "auto")),
    )
```

Add `"JsonSourceIR"` to `semantic/ir.py`'s `__all__`, and extend
`source_label(...)` / any other `isinstance`-over-the-union helper in that file
to name JSON sources.

### 6. Discovery and analysis parity

`discover_entity`, `discover_dimensions`, `discover_time_dimensions`, and
`discover_measures` already accept `source: TableSource` (the file-source
union). Once `JsonSourceIR` joins the union and the reader branches exist, the
full loop works unchanged:

```python
events = md.json("data/events/*.json")
md.discover_entity(ds, events).show()
md.discover_measures(ds, events, columns=("amount",)).show()
orders = ms.entity(name="events", datasource=ds, source=events)
ms.verify_object(orders)
```

Semantic authoring, materialization, and the analysis track inherit JSON with no
analysis-side code changes. Update every `discover_*` docstring that enumerates
"md.table(), md.parquet(), or md.csv()" to include `md.json()`.

### 7. Public surface and snapshot

- Export `json` from `marivo/datasource/__init__.py` and
  `marivo/semantic/__init__.py` `__all__`.
- Update the pinned public-surface snapshot in `tests/test_public_surface.py`
  and the import tests `tests/test_datasource_imports.py`,
  `tests/test_semantic_imports.py`.
- `md.json` joins the existing file-source family in `md.help` / `ms.help`; the
  help text at `marivo/semantic/help.py` that lists "TableSourceIR |
  ParquetSourceIR | CsvSourceIR" gains `JsonSourceIR`.

## Error handling

- `JsonSourceIR.__post_init__` raises `TypeError` for an empty path and for a
  `format` outside the allowed literal, matching the existing per-field
  validators (`_require_non_empty_str`, a new `_require_json_format`).
- HTTP failures that survive auto-enabled `force_download` (a genuine 404, TLS
  failure, or auth wall) surface through the existing datasource metadata error
  path. Per the repo's errors-teach principle, the message names the URL and
  states the scope boundary: only httpfs-reachable endpoints are supported;
  endpoints requiring auth, pagination, or POST belong in a pull script that
  lands NDJSON/parquet. No silent fallback.
- `ms.entity(source=...)` with a non-source object continues to raise the
  existing `INVALID_REF` semantic error, now listing `ms.json(...)` among the
  accepted builders.

## Testing

- Unit: `JsonSourceIR` construction, field validation (empty path, bad
  `format`), `to_dict` shape, and `source_from_dict` round-trip — mirror
  `tests/test_semantic_dtos.py` and
  `tests/test_semantic_authoring_surface_phase1.py`.
- Builder: `md.json(...)` / `ms.json(...)` return a `JsonSourceIR` and reject a
  bad `format` — mirror `tests/test_datasource_scan.py` and
  `tests/test_semantic_authoring.py`.
- Union: assert `JsonSourceIR` is in the datasource / semantic source unions —
  extend `tests/test_datasource_discovery_evidence.py`.
- Local integration: write a temp directory of multiple fixed-schema NDJSON
  files, register an in-memory DuckDB datasource, and run
  `discover_entity` / `discover_measures` over `md.json("<dir>/*.json")`,
  asserting reconciled columns and row counts.
- HTTP: gate any network-touching test behind the same opt-in marker used by
  existing live-integration tests so the default `make test` stays offline; do
  not add network flakiness to the default suite. `log`/document that the http
  path is exercised only under that marker.
- Surface: snapshot and import tests updated for the new `json` export.

## Documentation

- `docs/specs/semantic/python-semantic-layer.md`: add JSON to the file-source
  section.
- `md.help` / `ms.help` authoring contract and the semantic help index list the
  new builder.
- The `marivo-semantic` skill's datasource reference gains `md.json(...)` in the
  discovery examples (workflow only; no parameter-table duplication, per the
  guide's layering rules).
- `site/` docs: add the JSON source example to the versioned pages under
  `site/src/content/docs/*/latest/`, keeping the English and Chinese editions in
  sync (per CLAUDE.md).

## Success criteria

- `md.json("data/events/*.json")` and `md.json("https://host/data.json")` both
  drive `discover_* -> ms.entity -> ms.verify_object` end to end.
- A dynamic HTTP JSON endpoint that errors without `force_download` succeeds via
  the auto-enable helper; a static one keeps working; both need zero manual
  datasource config.
- A JSON-backed entity round-trips through `.marivo/` project state (persist and
  reload) with an equal `JsonSourceIR`.
- `make test`, `make typecheck`, and `make lint` pass; the public-surface
  snapshot includes `json` and no other surface drift.

## Decisions log

- Surface richness: **JSON-aware**, not minimal-parity or schema-locked.
  `format` kept; `columns` type-map rejected.
- HTTP handling: **auto-enable `force_download` on `http(s)` paths** at read
  time, over the datasource-level `extra` + teaching-error alternative.
- `union_by_name`: **dropped** — verified no observable effect for JSON.
- `hive_partitioning`: **dropped** — hive-layout data belongs behind Trino.
- `format`: **kept** — the one override that materially changes parsing.
- `kind`: retained as the serialization discriminator; pinned to `"json"`,
  non-user-facing.
