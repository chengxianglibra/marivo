# Project read service

Install `marivo[api]` to expose the current semantic Catalog, saved reports and
explicitly refreshed script endpoints for one fixed project. AnalysisFrames use
the current Store generation; DataFrame snapshots use independent Parquet files.
The service keeps one current semantic snapshot in memory.

For agent-directed service work, `marivo-service` routes publication and
consumption decisions. Start with `marivo help analysis.results.service`, then
follow its script and HTTP references for the installed Runtime's contracts.
`marivo init` installs the skill alongside the analysis and authoring skills;
the host owns skill selection and the bound service connection.

## Start and connect

Generate a token once with a secure random source such as
`secrets.token_urlsafe(32)`. Save it in deployment credential configuration
and pass it to the service as `MARIVO_API_TOKEN`. The token grants reads,
Catalog reload and execution of administrator-installed endpoint scripts.
Restarting with the same configuration preserves access.
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
It describes the generic named endpoint route, not the names of scripts in a
project. Obtain a concrete endpoint name from the project or its host.

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
| `GET /v1/data/{endpoint}` | Current saved report without executing its script |
| `POST /v1/data/{endpoint}/refresh` | Execute the script, publish and return a complete report. Send an empty body |
| `GET /v1/dataframes/{result_id}` | Complete saved DataFrame report for the exact reference |

Directories accept `limit` from 1 to 100 (default 20) and `cursor`. Continue
with the same selection and limit using `next_cursor`. The first semantic
request loads the Catalog in a fresh process. Reload replaces the snapshot
atomically on success and invalidates its previous directory and relation
cursors. Failed reloads keep the last successful snapshot. Semantic detail
includes direct references in the definition. Source locations are
project-relative when available. The projection excludes connection options,
source URLs and paths, and raw entity declaration source.

An explicit reload starts a new load. If any Catalog load is already active,
reload returns `409 catalog_load_in_progress` without queuing or accepting the
older load as its result. Wait for that load to finish before requesting reload
again. Ordinary reads share an initial load or read the last successful snapshot
while reloading. The service owns Catalog loads: a client disconnect or request
timeout does not cancel a shared load. Its own read deadline and service shutdown
still stop it.

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
  "kind": "analysis_frame",
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

Named AnalysisFrame endpoints use this same report projection, including
`kind: "analysis_frame"`. The exact Frame route remains the canonical fixed
version read; it does not accept `refresh`.

## Script endpoints

Place one script per endpoint under the project's `endpoints/` directory.
`endpoints/sales/revenue.py` maps to `/v1/data/sales/revenue`. Path segments use
lowercase snake_case. Private files and directories beginning with `_` are
ignored. Startup discovers paths and validates syntax and the entry signature
without importing scripts. Restart to discover a new path. Refresh of an existing
path loads its latest script and helper source in a fresh Python process.
The `endpoints` package resolves inside the bound project, including when an
installed distribution provides the same package name.

```python
# endpoints/sales/revenue.py
import marivo.analysis as mv


def main(session: mv.Session) -> mv.AnalysisFrame:
    revenue = session.catalog.get("metric:sales.revenue")
    assert revenue.kind == "metric"
    return session.observe({"revenue": revenue})
```

Each script declares one synchronous `main(session)` with a Session annotation
and exactly one concrete return annotation: `mv.AnalysisFrame` or `pd.DataFrame`.
Defaults, extra arguments, generators, async functions and union return types
are rejected. The service supplies a fresh Session bound to the project, checks
the actual output, and persists it. The injected Session keeps the service's
project binding even if script imports configure a different environment.
An AnalysisFrame must belong to that Session
and be unpersisted. A computed memory Frame is allowed. The script owns explicit
time scope selection; the service does not advance dates or reconstruct a producer
from lineage. Scripts and imports are trusted project code, not sandboxed code.

```sh
curl --fail-with-body -X POST -H "Authorization: Bearer ${MARIVO_API_TOKEN}" \
  'http://127.0.0.1:8765/v1/data/sales/revenue/refresh'
curl --fail-with-body -H "Authorization: Bearer ${MARIVO_API_TOKEN}" \
  'http://127.0.0.1:8765/v1/data/sales/revenue'
```

The first call executes, saves and returns a complete report synchronously. The
second reads the saved current reference. These saved GET reads
never import scripts, recover Sessions or query sources, including on the first
read: no saved result returns `result_not_found`. Both routes reject all query
parameters, including the former `refresh` parameter. Removed or
broken scripts cannot invalidate a saved result. Catalog reload remains separate.
Do not automatically retry POST refresh. A timeout or lost response does not
prove publication failed; read current state before deciding to refresh again.

## DataFrame reports and fixed report versions

DataFrame scripts receive the same Session and can continue a Marivo computation:

```python
# endpoints/sales/revenue_table.py
import pandas as pd
import marivo.analysis as mv


def main(session: mv.Session) -> pd.DataFrame:
    revenue = session.catalog.get("metric:sales.revenue")
    assert revenue.kind == "metric"
    table = session.observe({"revenue": revenue}).compute().to_pandas()
    return table.assign(double_revenue=table["revenue"] * 2)
```

Return a default unnamed RangeIndex and unique nonempty string column names.
Move business indexes into explicit columns. Supported Arrow scalar columns are
null, boolean, string, integer, float32/64, decimal, date, timestamp and duration.
Nested objects and unsupported types fail with a field location. `attrs` is not
persisted or interpreted as semantic metadata. Rows and columns retain their
order, typed empty tables retain schema, and zero-column tables retain their
logical row count. Exact scalar encoding follows the Frame rules above.

```json
{
  "kind": "dataframe",
  "ref": {"schema":"marivo.dataframe_ref/v1","result_id":"df_0123456789abcdef0123456789abcdef"},
  "session_id": "session_example",
  "committed_at": "2026-11-07T00:00:00+00:00",
  "schema": {"columns":[{"name":"count","dtype":{"kind":"int64"}}]},
  "data": {"layout":["count"],"rows":[["9007199254740993"]],"row_count":1}
}
```

`schema.columns` declares ordered physical types; `data.layout` gives each row's
column order. The captured `session_id` describes the production context. Reading
`GET /v1/dataframes/{result_id}` needs neither that Session nor the Analysis Store,
model or script. DataFrames carry no inferred units, lineage, axes or periods.

Save the service connection identity together with the returned `kind` and `ref`
in a UI report. For AnalysisFrames, retain the connection's Store identity from
`/v1/info` as well. Reopen via the exact Frame or DataFrame route, not the named
endpoint. Refresh changes the endpoint's current result, never an existing fixed
reference. Replace the report's saved ref explicitly to adopt a newer version.
`committed_at` is publication time, not source coverage or freshness. The host owns
report components, saving, offline export and user authorization.

## Persistence and refresh lifecycle

Current references live in `.marivo/server/endpoints/{endpoint}.json`. An
AnalysisFrame pointer contains `kind`, `store_id` and its `marivo.frame_ref/v1`
ref. A DataFrame pointer contains `kind` and its `marivo.dataframe_ref/v1` ref.
Every successful refresh publishes a new fixed identity. DataFrames are stored as
`.marivo/server/dataframes/{result_id}.parquet`, outside the Analysis Store. Their
service-owned Parquet metadata records format, result and Session identity, UTC
commit time and logical row count; nonzero-column reads verify the physical count.

The worker validates and saves the result, then encodes the complete report. Only
after successful worker exit does the parent atomically replace the current JSON
reference. Readers see the previous or the complete new result. Pre-publication
failures preserve the previous pointer; a saved but unpublished snapshot may remain
orphaned. Fixed snapshots are retained; there is no automatic deletion or replay.

A filesystem lock excludes simultaneous refreshes of the same endpoint across
service processes. Conflicts return `refresh_in_progress`. Each service also has
a refresh capacity limit; excess requests return `service_unavailable` without
queuing. Ordinary saved reads remain independent. Cancellation and timeout kill
and reap the worker before releasing its slot and lock. Subprocesses inheriting
the refresh's process group share its lifetime and are stopped on completion,
cancellation or parent loss. Script stdout and stderr go directly to service
stderr, so inherited logging descriptors cannot block worker cleanup. The worker watches a
dedicated parent channel and stops its process group on parent loss; the inherited lock excludes a
new refresh until it exits. Result IPC is separate from script stdout and stderr.
If the response is lost after the pointer switch, read the current result before
retrying: a failed connection does not prove publication failed.

## Limits and errors

`--max-response-mib` defaults to 128 and counts the entire uncompressed UTF-8
JSON response. `--read-timeout-seconds` defaults to 300 for the complete read
and encoding operation. Both accept positive integers. The Catalog loader uses
the same limits. A complete report has no separate row or period count cap.
Reads stop cooperatively at batch and receipt-check boundaries after a
disconnect or deadline. An in-progress native file or database call may need
to return before its worker slot becomes available.

`--refresh-timeout-seconds` defaults to 300 and covers script loading, execution,
publication and report encoding. `--max-refreshes` defaults to 1 per service
process. Both accept positive integers. Refresh uses the same response-size
budget, with its own deadline, and returns `refresh_timeout` on expiry.

A failure returns `{"error":{...}}` with `code`, `message`, `location`,
`repair` and nullable `target`. Use the stable code to handle the error.

| Status | Code | Action |
| --- | --- | --- |
| 400 | `invalid_parameter` | Correct the named request parameter |
| 401 | `unauthorized` | Supply the administrator-configured token |
| 403 | `origin_not_allowed` | Check the configured Origin and preflight |
| 404 | `not_found` | Verify the project and exact identity |
| 404 | `result_not_found` | Check the fixed ref or explicitly create the endpoint's first result |
| 409 | `invalid_cursor` | Start the directory listing again |
| 409 | `refresh_in_progress` | Wait for the same endpoint's refresh or read the saved result |
| 409 | `catalog_load_in_progress` | Wait for the active load to finish, then request reload again |
| 413 | `response_too_large` | Increase the response budget and retry |
| 422 | `catalog_load_failed` | Repair the model and reload |
| 422 | `unsupported_store_format` | Use a Runtime compatible with the saved Store |
| 422 | `corrupt_artifact` | Restore the exact committed metadata or files |
| 422 | `unencodable_value` | Inspect the named field and its carrier |
| 422 | `script_contract_error` | Correct the script signature, output type or Frame ownership |
| 422 | `refresh_failed` | Inspect the script's error in service logs and repair its inputs |
| 422 | `invalid_result_reference` | Restore the saved pointer or explicitly refresh |
| 422 | `corrupt_dataframe` | Restore the original Parquet result |
| 503 | `read_timeout` | Increase the configured deadline and retry |
| 503 | `refresh_timeout` | Check current state and the refresh deadline before retrying |
| 503 | `service_unavailable` | Retry when a read or refresh slot is available |
| 500 | `result_publication_failed` | Check storage capacity and permissions, then read current state |
| 500 | `internal_error` | Keep the reference and check service logs |

A report either returns complete data or fails. If deployment capacity cannot
accommodate a larger budget, explicitly persist a smaller result.

Unexpected request failures log their operation and traceback to service stderr.
Catalog worker stderr is retained there as well; failed worker exits include the
exit status. HTTP errors do not expose tracebacks. For `catalog_load_failed`,
inspect these logs and run `marivo doctor --semantic` in the bound project.


Per-column contribution time is captured in `frame.schema.value_time_semantics`.
Observed facts retain each source occurrence's clock or report independence,
report range/bucket effect, attached window and sample role. Fixed-data arithmetic
and statistics retain derived input descriptions, not executable Metric authority.
The same frozen facts survive persistence and cold reopening and are projected in
HTTP Frame reports. They complement `value_contexts` and `source_times`; a common
calendar key does not establish a shared source cohort or temporal additivity.
