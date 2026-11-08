# Persisted result service

Marivo explicitly saves AnalysisFrames and pandas DataFrames. The project-bound
HTTP service reads their exact saved identities. The plugin owns report layout,
text, data bindings, date policies, analysis programs and refresh execution.
There are no script endpoints, current-result aliases or Catalog HTTP operations.

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

The DataFrame reference contains `schema: "marivo.dataframe_ref/v1"` and
`result_id`. The explicit root is the exact existing directory; omission uses
normal project discovery. Saving needs no Session, Analysis Store, Catalog,
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

## Start and connect

Install `marivo[api]`. Generate a token once with `secrets.token_urlsafe(32)`,
store it in deployment credentials, and set `MARIVO_API_TOKEN` in the service
environment. The host owns connection management and user authorization.

```sh
marivo serve --project-root /srv/sales-project --port 0
```

The default listener is `127.0.0.1:8765`. Port 0 selects an available port.
The foreground process emits one JSON readiness line with host, port, protocol,
pid, marivo_version and api_version 2. Logs go to stderr. SIGTERM/SIGINT stop it;
fixed files remain available after restart. Token rotation uses configuration
and restart, without changing fixed references.

For remote HTTPS browser access, configure a trusted certificate and matching key,
or use a protected HTTPS proxy. Configure exact browser Origins; repeat the flag
for multiple Origins. No wildcard Origin is accepted.

```sh
marivo serve --project-root /srv/sales-project --host 0.0.0.0 --port 8765 \
  --ssl-certfile /etc/tls/fullchain.pem --ssl-keyfile /etc/tls/privkey.pem \
  --allow-origin https://analysis.example.com
```

All business requests require `Authorization: Bearer <token>`, including info and
OpenAPI. Valid CORS preflights require no token, allow GET/OPTIONS and the
Authorization/Content-Type headers. Actual requests are authenticated. Allowed
Origins receive CORS headers on errors too. Server-to-server requests need no Origin.

## HTTP v2

| Method and path | Result |
| --- | --- |
| `GET /v2/info` | API/Marivo versions and current Store identity or null |
| `GET /v2/openapi.json` | OpenAPI 3.1 request, response and error schemas |
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

## Ownership, retention and failures

Reads operate independently of the producer process, source availability and
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

Use `marivo help analysis.persist_dataframe`,
`marivo help analysis.analysis_frame.ref` and
`marivo help analysis.results.service.http` for focused native contracts.
