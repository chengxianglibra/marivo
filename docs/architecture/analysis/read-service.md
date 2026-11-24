# Project read service

Marivo explicitly saves AnalysisFrames and pandas DataFrames. Python reads plain
tables by exact saved reference; the project-bound HTTP service reads both kinds.
The plugin owns report layout,
text, data bindings, date policies, analysis programs and refresh execution.
The HTTP service also reads current semantic definitions through a separate Catalog snapshot.

## Save and deliver results

AnalysisFrames retain the existing persistence contract:

```python
saved = frame.persist(description="Monthly revenue")
reference = saved.ref.to_dict()
```

Only stored Frames have a `ref`; reading it never computes or saves a result.
The reference contains `schema: "marivo.frame_ref/v2"`, `store_id`, `session_id`
and `artifact_id`, all captured from the saved Frame. Pending or memory Frames
require explicit `persist()`. An already computed Frame is saved without rereading
sources; an already stored Frame retains its existing commit.

Plain tables have an independent public entry point:

```python
from pathlib import Path
import pandas as pd
import marivo.analysis as mv

reference = mv.persist_dataframe(
    pd.DataFrame({"region": ["east", "south"], "orders": [12, 7]}),
    project_root=Path("/srv/sales-project"),
)
print(reference.to_dict())
```

The DataFrame reference contains `schema: "marivo.dataframe_ref/v2"`, the resolved
absolute `project_root` and `result_id`. Together the location and identity locate
one fixed local snapshot. The explicit save root is the exact existing directory;
omission uses normal project discovery. Saving needs no Session, Analysis Store, Catalog,
source connection, running HTTP service or optional API dependencies. Every call
creates a new fixed identity. Later edits to the original table do not affect it.
Neither reference includes URLs, credentials, a report identity or producer code.
Keep the service connection separately in the consuming application.

Tables require an unnamed default RangeIndex, unique nonempty string column
names and supported scalar columns: null, boolean, string, integer, float32/64,
Decimal, date, timestamp or duration. Move business indexes into columns.
Unsupported nested/object values fail rather than becoming strings. Row/column
order, zero-column row counts, empty typed columns and supported exact values
are retained. `attrs` is not saved as semantic metadata. Plain tables do not gain
inferred units, lineage, axes, periods or source freshness.

## Native Python table reads

`mv.read_dataframe(reference)` reads one complete saved table from its captured location.
Pass the `DataFrameReference` returned by `persist_dataframe`, or its original
three-field `to_dict()` dictionary, including one recovered from JSON:

```python
import json

wire = json.loads(json.dumps(reference.to_dict()))
result = mv.read_dataframe(wire)
result.show()
table = result.to_pandas()
```

`DataFrameResult` is immutable and acquired only from this reader. It exposes:

| Field or method | Captured information |
| --- | --- |
| `kind` | `"dataframe"` |
| `ref` | Exact saved `DataFrameReference` |
| `committed_at` | Original UTC save time as an aware `datetime` |
| `row_count` | Complete logical row count, including zero-column rows |
| `schema` | Ordered `pyarrow.Schema` without internal metadata |
| `to_pandas()` | Independent complete DataFrame with Arrow-backed columns |

The schema retains saved integer widths, Decimal precision/scale and timestamp
or duration units and timezone. `schema.names` gives ordered names; `len(schema)`
gives the column count. Field nullability describes allowed values, not an audit
of actual missingness. Exports retain saved null/NaN distinctions, exact numbers,
row/column order, empty typed columns and zero-column row counts. Each export has
empty attrs and an unnamed default RangeIndex. Original pandas dtype containers
and attrs are not reconstructed. Convert explicitly for arithmetic when needed.

The reader needs no Session, Catalog, Analysis Store, source or HTTP dependencies.
It creates no state and closes the file before returning; subsequent exports
work even when that file becomes unavailable. `reference.project_root` is an
immutable `Path`; `to_dict()` serializes it as an absolute path string. The reader
uses only that captured location and accepts no location override. Current working
directory and `MARIVO_PROJECT_ROOT` do not affect reads. The captured directory
must be accessible on this filesystem; moving it does not trigger discovery or
relocation. Bare IDs, paths, JSON strings, v1 references, extra dictionary fields
and AnalysisFrame references are rejected. HTTP responses contain the service's
project path; remote HTTP clients still use their separately configured connection.

Reads return the complete result or an `AnalysisError` with expected, received,
location and repair. Missing results use `result_not_found`, invalid references
use `invalid_result_reference`, corrupt files use `corrupt_dataframe`, and
non-format I/O failures use `result_read_failed`. A malformed captured location
uses `invalid_result_reference`; an absent project uses `result_not_found`.
Repairs point to `analysis.read_dataframe`. Missing or corrupt
snapshots never trigger recalculation. Memory grows with the full result; Python
reads have no pagination or HTTP JSON limits. Commit time does not prove freshness.

## Start and connect

Install `marivo[api]` for the service, including `onnxruntime` and `tokenizers`.
Generate a token once with `secrets.token_urlsafe(32)`, store it in deployment
credentials, and set `MARIVO_API_TOKEN` in the service environment. The host owns
connection management and user authorization.

```sh
marivo serve --project-root /srv/sales-project --port 0
```

Marivo prepares the Granite model files before starting the listener. The
default listener is `127.0.0.1:8765`. Port 0 selects an available port. The
foreground process emits one JSON readiness line with host, port, protocol, pid,
marivo_version and api_version 2. Logs go to stderr. SIGTERM/SIGINT stop it;
fixed files remain available after restart. Token rotation uses configuration and
restart, without changing fixed references.

For remote HTTPS browser access, configure a trusted certificate and matching key,
or use a protected HTTPS proxy. Configure exact browser Origins; repeat the flag
for multiple Origins. No wildcard Origin is accepted.

```sh
marivo serve --project-root /srv/sales-project --host 0.0.0.0 --port 8765 \
  --ssl-certfile /etc/tls/fullchain.pem --ssl-keyfile /etc/tls/privkey.pem \
  --allow-origin https://analysis.example.com
```

All business requests require `Authorization: Bearer <token>`, including info and
OpenAPI. Valid CORS preflights require no token, allow GET/POST/OPTIONS and the
Authorization/Content-Type headers. Actual requests are authenticated. Allowed
Origins receive CORS headers on errors too. Server-to-server requests need no Origin.

## HTTP v2

| Method and path | Result |
| --- | --- |
| `GET /v2/info` | API/Marivo versions, current Store identity or null, Catalog load status |
| `GET /v2/openapi.json` | OpenAPI 3.1 request, response and error schemas |
| `GET /v2/semantic/objects` | Browse or search current semantic summaries with optional kind/domain filters |
| `GET /v2/semantic/objects/{id}` | Captured semantic definition and safe provenance |
| `GET /v2/semantic/objects/{id}/related` | Dependencies or dependents; required relation parameter |
| `GET /v2/semantic/objects/{id}/datasource-properties` | Approved datasource configuration from the current Catalog snapshot |
| `POST /v2/semantic/reload` | Atomically reload current models; empty body |
| `GET /v2/sessions` | Saved Sessions, newest updated first |
| `GET /v2/sessions/{session_id}/frames` | Committed Frames, newest committed first |
| `GET /v2/stores/{store_id}/sessions/{session_id}/frames/{artifact_id}` | Exact AnalysisFrame result |
| `GET /v2/dataframes/{result_id}` | Exact plain-table result |

Directories accept `limit` 1..100 (default 20) and `cursor`. Continue with the
same selection/limit. Fixed reads accept no query parameters and return the
complete saved result or an error. Other methods on these resources return 405;
unknown and retired routes return 404. There are no HTTP v1 aliases or redirects.
A Frame's Store must match the path identity or the read returns
`409 invalid_result_reference`. A project without an Analysis Store can still
serve saved DataFrames and has an empty Session directory.

Both result kinds contain `kind`, `ref`, `committed_at`, `schema` and `data`.
AnalysisFrames additionally retain description, lineage, source_reads and saved
periods. Their data layout preserves actual column names, coordinate slot IDs and
deterministic collision suffixes. Comparison periods retain saved endpoint mapping
and missing reasons. Plain tables expose physical columns and rows without a
synthetic Session. Full schemas are in OpenAPI, not inferred from display labels.

Integers and Decimal values use exact strings; dates/timestamps use ISO strings;
durations use tick strings with dtype units. Finite floats are JSON numbers;
nonfinite values use `{"float":"nan"}`, `{"float":"inf"}` or `{"float":"-inf"}`.
Null and Boolean presence remain independent. Empty rows use `rows: []`;
a zero-column single row uses `rows: [[]]`.

## Read current semantic definitions

The service loads the bound project's semantic definitions in a fresh interpreter
without connecting to business sources. It prewarms the Catalog snapshot and
vector index asynchronously at startup. `GET /v2/info` reports `catalog.loaded`
and `catalog.loaded_at` without triggering a load.

Omit `query` to browse in canonical ID order. To search, send a URL-encoded UTF-8
`query` containing at most 512 decoded printable characters and at least one
non-whitespace character. Leading and trailing spaces are trimmed. Browsing and
search accept optional `kind` and `domain` filters. Search applies them to the whole
current snapshot before vector ranking. Granite encodes captured IDs, paths,
names, business definitions and authored aliases without reading datasources.
Search returns up to 30 vector candidates at or above the cosine cutoff. The default
is 0.80. With `query`, set `min_similarity` to a number from 0 through 1 to use a
different cutoff. Exact canonical ID or path matches within the filters come first
and can fall outside the vector candidate window.

By default, `marivo serve` verifies the portable INT8 ONNX model and
`tokenizer.json` against pinned SHA-256 checksums at each startup. It downloads
missing or invalid files from IBM's Granite Embedding 97M Multilingual R2
repository. Projects and restarts share the same model cache.
[Pinned IBM model revision](https://huggingface.co/ibm-granite/granite-embedding-97m-multilingual-r2/tree/835ad14087e140460703cf0fae09f97d469d65c2)

The cache root defaults to `~/.cache/marivo/models`, or
`$XDG_CACHE_HOME/marivo/models` when that variable is set. Set
`MARIVO_VECTOR_MODEL_CACHE_DIR` to override it.

For offline provisioning, use `--experimental-granite-model /path/to/model.onnx`
to supply a local Granite INT8 export with a sibling `tokenizer.json`. The local
option accepts `.onnx` and `.ort` files. Marivo exits before listener readiness
if runtime dependencies are missing or it cannot prepare the managed files.

The service prewarms a 384-dimensional in-memory vector index and uses at most
256 tokens per model input. `GET /v2/info` includes `catalog.search` with
`mode: "vector"`, `state` (`building`, `ready`, or `failed`), and `index_build_ms`
(index preparation after document projection, including any model loading and
encoding, or null before index readiness).
Catalog requests share the initial build. `POST /v2/semantic/reload` loads a fresh
Catalog and compares search text with the previous ready snapshot using SHA-256.
The exact encoder input includes the canonical `kind:path` ID, path, name,
business definition and aliases. Reload encodes new or changed search text,
reuses vectors for unchanged text, and drops vectors for removed IDs. Each input
is encoded independently so its vector is stable when other documents change.
Reload publishes objects and the updated index together. An initial build failure
can be retried by reload, and a failed reload retains the previous ready snapshot.

To reuse Granite vectors on restart, pass `--experimental-granite-cache-dir /path/to/cache`.
This cache is separate from the model file cache. Use a trusted local directory and
a self-contained model export. Each archive is scoped to the absolute project root,
both model files and the encoding recipe. It stores row fingerprints and float32 vectors.
Startup reuses rows with matching fingerprints, encodes new or changed text, and drops
rows for removed IDs. A successful update atomically replaces the archive. A missing,
incompatible or invalid archive requires a full build. If all current rows can be reused,
the service can defer native model initialization to the first text search.
Restart the service when replacing model files.

Browse and search return the same object summaries and pagination fields. Pass
`next_cursor` as `cursor` with the same query, similarity cutoff, filters and limit.
A cursor from a different selection or snapshot returns `409 invalid_cursor`.
Start a new request without a cursor to use the current selection and snapshot.

```sh
curl -H "Authorization: Bearer $MARIVO_API_TOKEN" \
  'http://127.0.0.1:8765/v2/semantic/objects?kind=metric&domain=sales&limit=20'
curl -G -H "Authorization: Bearer $MARIVO_API_TOKEN" \
  --data-urlencode 'query=net revenue' --data 'kind=metric' \
  'http://127.0.0.1:8765/v2/semantic/objects'
curl -H "Authorization: Bearer $MARIVO_API_TOKEN" \
  'http://127.0.0.1:8765/v2/semantic/objects/metric%3Asales.revenue'
curl -H "Authorization: Bearer $MARIVO_API_TOKEN" \
  'http://127.0.0.1:8765/v2/semantic/objects/metric%3Asales.revenue/related?relation=dependencies'
```

Object IDs are canonical `kind:path` identities, URL-encoded in paths. Directories
return summaries plus available kinds/domains. Details retain business context,
formulas, units, contribution-time bindings and project-relative provenance.
Connection fields and environment references, file/HTTP source locations and
external absolute model paths are omitted. Related navigation accepts
`dependencies` or `dependents`, uses the full loaded scope and reports known
unloaded dependencies separately as `outside_scope`.

The dedicated datasource-properties endpoint accepts an exact `datasource:<name>`
identity and returns `backend_type`, `wall_clock_timezone`, `properties`,
`env_refs` and `has_omitted_fields`. It returns 404 for absent or non-datasource
identities. `properties` contains only allowlisted literal fields with approved
value shapes for the backend. `env_refs` maps credential fields to the exact
lookup names supplied to `md.env(...)`. DuckDB S3 and HTTP field keys retain
their scopes. The map is `{}` for datasources without references. The service
reads these names from the definition without resolving credentials.
`has_omitted_fields` indicates whether `properties` omits any literal fields.

This endpoint exposes approved connection settings, DuckDB paths and credential
references to **every holder of the project's API token**. Hosts must enforce
their own user/project authorization. Ordinary semantic details omit these
settings and references. Marivo merges `extra` into literal fields before
capturing the snapshot. Keep secret values outside datasource declarations and
use `md.env(...)` to reference them.

Properties are captured in the same isolated, bounded Catalog load as semantic
objects. Warm requests read the immutable snapshot without starting a new process or
connecting to the datasource. The existing first-load, atomic reload, failed-reload
retention and response/worker budgets apply equally to this endpoint.

After model edits, send an empty body to `POST /v2/semantic/reload`. The isolated
load atomically replaces the snapshot only on success, returning `loaded_at` and
`object_count`. Failure returns `catalog_load_failed` and retains the previous
snapshot; an active load returns `409 catalog_load_in_progress`. Existing readers
may continue using the old snapshot during reload. Successful reload invalidates
old Catalog cursors. Loads continue after a client disconnect until the load
deadline; shutdown cancels an active worker. Response size and time limits also
bound Catalog loading. Reload never changes saved Frames or DataFrames.

## Ownership, retention and failures

Fixed-result reads operate independently of the producer process, source availability and
current models. They open existing state and validate committed ownership and
consumed files. They never replay producers, execute scripts or query sources.
Read validation can scan/hash complete files; internal batches do not bound total I/O.

AnalysisFrames use the existing Analysis Store. Plain tables use immutable
`.marivo/server/dataframes/{result_id}.parquet` files, written atomically. Their
metadata records result identity, UTC commit time and logical row count. The same
reader consumes retained snapshots; an older background Session field is ignored.
There is no automatic persistence, cache, deletion or garbage collection.

Commit time and source-read completion do not prove business freshness or complete
coverage. The plugin records the requested range and generates supported conclusions
from saved facts. It publishes a complete report version only after all referenced
data and text are ready. Marivo does not manage that cross-result transaction.

Saving is independent of HTTP response limits. `--max-response-mib` defaults to 128
and bounds the complete uncompressed JSON; `--read-timeout-seconds` defaults to 300.
Both require positive integers. Oversized results return `response_too_large`,
without truncation. Reads cancel cooperatively; an active native/file call may need
to return before releasing its slot. Errors contain `code`, `message`, `location`,
`repair` and nullable `target`; use the stable code and exact saved reference.
Missing/corrupt data never triggers recalculation. The server logs unexpected
failures without exposing tracebacks to HTTP clients.

Use `marivo help analysis.persist_dataframe`, `marivo help analysis.read_dataframe`,
`marivo help analysis.dataframe_result`,
`marivo help analysis.analysis_frame.ref` and
`marivo help analysis.results.service.http` for focused native contracts.
