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
- No `hive_partitioning` field. `ParquetSourceIR` does expose it on the DuckDB
  backend, so this is a deliberate scope cut, not a backend limitation: the
  fixed-schema multi-file case here is served by a glob path, and
  partition-column-from-path extraction adds surface the current use case does
  not need. It can be added later by the same one-field pattern if a partitioned
  JSON layout appears.
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
mirror the same one-line addition to `scan.py`'s `TableSource` alias,
`semantic/dtos.py`'s `FileSource` union, and its
`FileFormat = Literal["parquet", "csv"]` (add `"json"`). All of these aliases
and every `isinstance` guard are enumerated in Design 4's closed-union site
list — none may be missed.

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
duplicated today across the reader-dispatch sites Design 4 enumerates (which
also lists the `isinstance` *guard* sites that must learn JSON). Each
reader-dispatch site grows a JSON branch:

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

Introduce one shared helper in `marivo/datasource/backends.py`. It matches a
strict `http://` / `https://` scheme (so a local path like
`http_exports/events.json` stays local), and guards on backend capability so a
non-DuckDB / fake backend gets a teaching error instead of an `AttributeError`:

```python
_HTTP_SCHEME = re.compile(r"^https?://", re.IGNORECASE)

def apply_json_http_settings(backend: object, source: object) -> None:
    """Enable force_download for http(s) JSON sources; no-op for local paths."""
    if not isinstance(source, JsonSourceIR):
        return
    if not _HTTP_SCHEME.match(source.path):
        return
    raw_sql = getattr(backend, "raw_sql", None)
    if raw_sql is None:
        raise DatasourceMetadataError(
            message=(
                f"json source {source.path!r} is http(s), but this datasource "
                "backend cannot read remote JSON. md.json(...) is a DuckDB "
                "file-source (local path, glob, or httpfs URL), not a generic "
                "HTTP API reader."
            ),
            details={"path": source.path, "reason": "backend_lacks_httpfs"},
        )
    raw_sql("SET force_download=true")
```

Because `read_json` / `read_parquet` / `read_csv` are DuckDB-only anyway, this
capability guard is the honest failure point for "http JSON on a non-DuckDB or
fake backend": capability is checked, then the teaching error names both the
cause and the scope boundary.

**Closed-union `isinstance` sites — all must learn `JsonSourceIR`.** Adding a
member to a closed union means every site switching on it must be updated. Some
are *reader dispatch* (add a branch); some are *guards* that silently downgrade
or raise on an unknown type — those are the dangerous ones.

Reader-dispatch sites (add a `read_json` branch; call the helper first):

- `marivo/datasource/manage.py` — `_execute_scoped_sample` (bounded discovery sample).
- `marivo/datasource/metadata.py` — schema-inference reader path.
- `marivo/semantic/scope.py` — semantic-side source expression build.
- `marivo/semantic/materializer.py` — materialization reader path.

Guard sites (must admit `JsonSourceIR` or they misbehave):

- `marivo/datasource/manage.py` `ColumnInspection` construction — currently
  `source if isinstance(source, (TableSourceIR, ParquetSourceIR, CsvSourceIR))
  else TableSourceIR(table=str(source))`. Omitting JSON silently **downgrades** a
  JSON discovery result to `TableSourceIR(table="JsonSourceIR(...)")`, corrupting
  fidelity without an error. Add `JsonSourceIR` to the tuple.
- `marivo/datasource/metadata.py` inspection guard — currently raises
  "unsupported datasource source kind" for anything but Parquet/CSV; must admit JSON.
- `marivo/semantic/authoring.py` `entity(source=...)` validation tuple — raises
  `INVALID_REF` for unknown sources; must admit JSON.
- `marivo/semantic/ir.py` `source_from_dict` (kind dispatch) and `source_label`.
- Type aliases: `datasource/ir.py` `EntitySourceIR`, `scan.py` `TableSource`,
  `semantic/dtos.py` `FileSource` and `FileFormat`.

The helper is the only new cross-cutting logic; the existing per-site dispatch
duplication is left as-is (surgical change, not a refactor).

### 5. Serialization round-trip (the `kind` discriminator)

`source_from_dict` in `marivo/semantic/ir.py` is the dict/DTO round-trip path:
`to_dict` writes `"kind"`, and `source_from_dict` dispatches on that string to
rebuild the dataclass. In memory the type is known via `isinstance`; once
reduced to a dict, only `kind` distinguishes a JSON dict from a parquet dict.
(Project semantic authoring itself is executed from `models/semantic/*.py` by
the loader — it is not reconstructed from these dicts. The DTO round-trip is a
separate, serialization-only path, which is why both are covered independently
in Success criteria.) Add the branch before the final `raise`:

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
md.help("json").show()                        # static authoring contract
events = md.json("data/events/*.json")         # glob over many fixed-schema files
md.test(ds).show()                             # datasource round-trip
md.inspect_table(ds, events).show()            # schema; works on file sources
md.discover_entity(ds, events).show()
md.discover_measures(ds, events, columns=("amount",)).show()
orders = ms.entity(name="events", datasource=ds, source=events)
ms.verify_object(orders).show()                # readiness for analysis handoff
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
- An `http(s)` JSON source on a backend lacking DuckDB/httpfs capability raises
  a teaching `DatasourceMetadataError` from `apply_json_http_settings` (Design 4)
  stating that `md.json(...)` is a DuckDB file-source, not a generic HTTP API
  reader — instead of an opaque `AttributeError` on a missing `raw_sql`.
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
  asserting reconciled columns and row counts. Assert the discovery result's
  `source` survives as a `JsonSourceIR` (`source.kind == "json"`), guarding the
  `ColumnInspection` fidelity branch against the `TableSourceIR` downgrade.
- Schema-drift semantics (verified against DuckDB 1.5.3, so the tests assert
  real behavior, not an aspiration):
  - Missing column across files → the absent key reads as `NULL`. Acceptable;
    test that the column is present and null-filled.
  - Same-name column with conflicting types across files → DuckDB does **not**
    fail the read; it widens the column to the `json` type. Test that discovery
    surfaces that widened/non-scalar type so the drift is *visible*. The
    fail-loud point is the semantic layer: authoring a measure or dimension that
    declares a concrete type over a `json`-typed column is where verification
    rejects it — not the read.
- HTTP scheme discrimination: a local path beginning with `http` (e.g.
  `http_exports/events.json`) is a no-op in `apply_json_http_settings` (no
  `SET force_download`); only `http(s)://` triggers it. Add a unit test for both.
- HTTP: gate any network-touching test behind the same opt-in marker used by
  existing live-integration tests so the default `make test` stays offline; do
  not add network flakiness to the default suite. `log`/document that the http
  path is exercised only under that marker.
- Surface: update the pinned snapshot/import tests (`test_public_surface.py`,
  `test_datasource_imports.py`, `test_semantic_imports.py`) and the help-contract
  tests that assert the authoring surface (`test_semantic_help_contract.py`,
  `test_datasource_help.py`, `test_introspection_help_folding.py`).

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
- An unauthenticated GET endpoint serving one complete JSON/NDJSON document
  works over http: one that errors without `force_download` succeeds via the
  auto-enable helper, and a static one keeps working — both with zero manual
  datasource config. Auth, pagination, POST, and `{"data":[...]}` wrappers stay
  explicitly unsupported; a capability gap raises the teaching error, not a
  silent failure.
- DTO round-trip: `source_from_dict(JsonSourceIR(...).to_dict())` reconstructs an
  equal `JsonSourceIR`.
- Project round-trip: a real `models/semantic/*.py` file declaring
  `source=ms.json(...)` loads via `ms.load()` and passes `ms.verify_object(...)`.
- `make test`, `make typecheck`, and `make lint` pass; the public-surface
  snapshot includes `json` and no other surface drift.

## Decisions log

- Surface richness: **JSON-aware**, not minimal-parity or schema-locked.
  `format` kept; `columns` type-map rejected.
- HTTP handling: **auto-enable `force_download` on `http(s)` paths** at read
  time, over the datasource-level `extra` + teaching-error alternative.
- `union_by_name`: **dropped** — verified no observable effect for JSON.
- `hive_partitioning`: **dropped** — deliberate scope cut (parquet exposes it;
  JSON's fixed-schema multi-file case is covered by globs). Addable later.
- `format`: **kept** — the one override that materially changes parsing.
- `kind`: retained as the serialization discriminator; pinned to `"json"`,
  non-user-facing.
