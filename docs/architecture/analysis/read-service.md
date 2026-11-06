# Project read service

Install `marivo[api]` to expose the current semantic Catalog and committed
AnalysisFrame reports for one fixed project. The service reads the current Store
generation and keeps one current semantic snapshot in memory.

## Start and connect

Generate a token once with a secure random source such as
`secrets.token_urlsafe(32)`. Save it in deployment credential configuration
and pass it to the service as `MARIVO_API_TOKEN`. The token grants all reads
and Catalog reload. Restarting with the same configuration preserves access.
To rotate it, change the deployment configuration and restart. Frame references
stay the same.

```sh
marivo serve --project-root /path/to/project --port 0
```

The foreground process listens on `127.0.0.1` and port `8765` by default.
Port `0` lets the OS choose an available port. Once listening, the process
writes a single JSON line to stdout. Logs go to stderr.

```json
{"host":"127.0.0.1","port":49152,"protocol":"http","pid":12345,"marivo_version":"0.6.3.dev0","api_version":1}
```

The receipt contains the bind address. An HTTPS entry point or proxy may have
a different client-facing address. SIGTERM and SIGINT close the service.
Saved reports remain available to a later process.

To expose the API to a remote HTTPS page, supply the PEM certificate chain
and private key together. The certificate must cover the API hostname and
be trusted by the browser. An existing HTTPS entry point can terminate TLS
and forward to a protected internal HTTP listener.

```sh
marivo serve --project-root /path/to/project --host 0.0.0.0 --port 8765 \
  --ssl-certfile /etc/tls/fullchain.pem --ssl-keyfile /etc/tls/privkey.pem \
  --allow-origin https://analysis.example.com
```

Repeat `--allow-origin` for exact allowed browser Origins, including scheme,
hostname and port. Valid preflights allow GET, POST, OPTIONS and the
Authorization and Content-Type headers without a token. Actual requests use
Bearer authentication, including server-to-server calls with no Origin.
Allowed Origins receive CORS headers on success and errors. The host or proxy
owns user authentication and project selection.

## Protocol

All business requests require `Authorization: Bearer <token>`.
`GET /v1/openapi.json` returns the OpenAPI 3.1 contract.

| Method and path | Result |
| --- | --- |
| `GET /v1/info` | API and Marivo versions, Store identity or null, Catalog load status |
| `GET /v1/openapi.json` | Typed request, response and error schemas |
| `GET /v1/semantic/objects` | Current summaries, optional `kind` and `domain` filters, available kinds and domains |
| `GET /v1/semantic/objects/{id}` | Current definition and safe provenance for a URL-encoded canonical `kind:path` |
| `GET /v1/semantic/objects/{id}/related` | Related summaries and outside-scope identities for `relation=dependencies` or `dependents` |
| `POST /v1/semantic/reload` | Loaded time and object count. Send an empty body |
| `GET /v1/sessions` | Existing Sessions, newest updated first |
| `GET /v1/sessions/{session_id}/frames` | Committed Frame summaries, newest committed first |
| `GET /v1/sessions/{session_id}/frames/{artifact_id}` | Complete saved report for the exact reference |

Directories accept `limit` from 1 to 100 (default 20) and `cursor`. Continue
with the same selection and limit using `next_cursor`. The first semantic
request loads the Catalog in a fresh process. Reload replaces the snapshot
atomically on success and invalidates its previous directory and relation
cursors. Failed reloads keep the last successful snapshot. Semantic detail
includes direct references in the definition. Source locations are
project-relative when available. The projection excludes connection options,
source URLs and paths, and raw entity declaration source.

## Complete Frame reports

Keep the identities returned by publication.

```python
saved = frame.persist(description="Recorded revenue")
ref = {
    "schema": "marivo.frame_ref/v1",
    "session_id": saved.session_id,
    "artifact_id": saved.artifact_id,
}
```

A client with the exact reference can request the report directly. In this
example the client has the configured token and the saved IDs.

```sh
curl --fail-with-body \
  -H "Authorization: Bearer ${MARIVO_API_TOKEN}" \
  "http://127.0.0.1:8765/v1/sessions/${session_id}/frames/${artifact_id}"
```

Use `data.layout` for row order and actual column names. Fields resolve by
slot ID, and coordinate columns by axis token, endpoint and part. Coordinate
name collisions get deterministic `__2`, `__3` and subsequent suffixes.
`schema.layout` keeps the original default names. `data.coordinates` contains
all original key values by slot ID, with one value per result row. Display
pagination can slice the table already returned to the client.

Business integers and Decimal values use exact strings. Dates and timestamps
use ISO strings. Durations use integer tick strings with units from the dtype.
Finite floats use JSON numbers, and nonfinite floats use `{"float":"nan"}`,
`{"float":"inf"}` or `{"float":"-inf"}`. Null values and Boolean presence are
independent. Period keys and labels use `{dtype,value}`, as do standalone
calendar labels in rows. Periods can exist without current rows. Comparison
periods retain the saved endpoints and missing reasons. Zero rows use
`rows: []`. A zero-column single row uses `rows: [[]]`.

A complete scalar report:

```json
{
  "ref": {"schema":"marivo.frame_ref/v1","session_id":"session_example","artifact_id":"artifact_example"},
  "description": "Recorded revenue",
  "committed_at": "2026-11-06T00:00:00+00:00",
  "schema": {
    "input_id": "stored_example",
    "values": {"revenue":{"slot_id":"amount","name":"revenue","dtype":{"kind":"decimal","precision":18,"scale":2},"nullable":false,"unit":"CNY"}},
    "presence": {},
    "axes": [],
    "layout": [{"kind":"field","name":"revenue","slot_id":"amount"}],
    "fixed_axes": [],
    "value_contexts": {},
    "source_times": [],
    "alignments": [],
    "comparisons": [],
    "decompositions": []
  },
  "lineage": {
    "input_id": "stored_example",
    "values": {"revenue":{"value_origins":[],"context_origins":[]}},
    "axes": {},
    "presence": {}
  },
  "source_reads": [],
  "data": {
    "layout": [{"kind":"field","name":"revenue","slot_id":"amount"}],
    "coordinates": {},
    "rows": [["123.45"]],
    "row_count": 1
  },
  "periods": []
}
```

Frame reads check exact ownership, committed descriptors and receipts. They
operate independently of the producer and the current Catalog, and skip source
execution, Session recovery and complete method payloads. Internal batches can
hash or scan complete consumed files. Batch size does not bound physical I/O.

DataFrameView remains a host resource whose data travels with its report or
message. The host owns report components, offline export and user authorization.

## Limits and errors

`--max-response-mib` defaults to 128 and counts the entire uncompressed UTF-8
JSON response. `--read-timeout-seconds` defaults to 300 for the complete read
and encoding operation. Both accept positive integers. The Catalog loader uses
the same limits. A complete report has no separate row or period count cap.
Reads stop cooperatively at batch and receipt-check boundaries after a
disconnect or deadline. An in-progress native file or database call may need
to return before its worker slot becomes available.

A failure returns `{"error":{...}}` with `code`, `message`, `location`,
`repair` and nullable `target`. Use the stable code to handle the error.

| Status | Code | Action |
| --- | --- | --- |
| 400 | `invalid_parameter` | Correct the named request parameter |
| 401 | `unauthorized` | Supply the administrator-configured token |
| 403 | `origin_not_allowed` | Check the configured Origin and preflight |
| 404 | `not_found` | Verify the project and exact identity |
| 409 | `invalid_cursor` | Start the directory listing again |
| 413 | `response_too_large` | Increase the response budget and retry |
| 422 | `catalog_load_failed` | Repair the model and reload |
| 422 | `unsupported_store_format` | Use a Runtime compatible with the saved Store |
| 422 | `corrupt_artifact` | Restore the exact committed metadata or files |
| 422 | `unencodable_value` | Inspect the named field and its carrier |
| 503 | `read_timeout` | Increase the configured deadline and retry |
| 503 | `service_unavailable` | Retry when a read slot is available |
| 500 | `internal_error` | Keep the reference and check service logs |

A report either returns complete data or fails. If deployment capacity cannot
accommodate a larger budget, explicitly persist a smaller result.
