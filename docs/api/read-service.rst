Marivo project read service
===========================

Save an AnalysisFrame with :meth:`marivo.analysis.AnalysisFrame.persist` and export
its fixed identity through ``saved.ref.to_dict()``. Save a plain pandas table with
:func:`marivo.analysis.persist_dataframe`; no Analysis Session or HTTP service is
required for table persistence. Keep the service connection separately.

Read a saved table locally with :func:`marivo.analysis.read_dataframe`, passing
only the reference object or its original ``to_dict()`` dictionary. The v2
reference captures the absolute ``project_root`` and ``result_id``; working
directory and project discovery do not affect reads. The captured path must be
accessible locally; there is no location override or relocation fallback.
:class:`marivo.analysis.DataFrameResult` contains the exact reference,
UTC save time, logical row count and ordered Arrow schema. Its ``to_pandas()``
returns an independent complete table with Arrow-backed types, empty attrs and a
default RangeIndex. No Session, Catalog, sources, HTTP dependencies or state
creation are involved. Files close before return. Memory grows with the complete
result; reads never recompute missing or corrupt snapshots.

Install ``marivo[api]`` for the HTTP and vector search runtimes and configure
``MARIVO_API_TOKEN``.

.. code-block:: sh

   marivo serve --project-root /path/to/project --port 0

Marivo prepares the model files before the listener starts. The JSON readiness
receipt reports ``api_version: 2``. All business requests use
Bearer authentication. ``GET /v2/openapi.json`` owns complete protocol schemas.
Read Frames with ``/v2/stores/{store_id}/sessions/{session_id}/frames/{artifact_id}``
and tables with ``/v2/dataframes/{result_id}``. The Store identity must match.
``GET /v2/info``, ``GET /v2/sessions`` and
``GET /v2/sessions/{session_id}/frames`` provide status and saved-result discovery.

Fixed-result reads return complete data or an error, preserve exact numeric/time/null
values and never execute producers, load the Catalog or query sources. Tables
retain physical schema and data; Frames additionally retain semantic metadata.
The plugin owns reports, text and refresh execution.

Browse current semantic summaries with ``GET /v2/semantic/objects``. Add an optional
URL-encoded UTF-8 ``query`` for vector search over IDs, paths, names, business
definitions and authored aliases. Use at most 512 decoded printable characters with
at least one non-whitespace character. The optional ``kind`` and ``domain`` filters
apply before ranking. Search pages cover up to 30 vector candidates with cosine
similarity at or above 0.80, plus any exact ID or path matches outside that window.
Exact matches come first. Browse and search use the same summary response.
Pass ``next_cursor`` as ``cursor`` with the same query, filters and ``limit``
(1 through 100, default 20). A cursor from another selection or snapshot returns
``409 invalid_cursor``.

By default, startup verifies the pinned Granite 97M multilingual R2 INT8 ONNX
model and tokenizer against their SHA-256 checksums and downloads missing or
invalid files. Projects and restarts share the model cache. The root defaults to
``~/.cache/marivo/models``, or ``$XDG_CACHE_HOME/marivo/models`` when set. Override
it with ``MARIVO_VECTOR_MODEL_CACHE_DIR``.

For offline provisioning, use ``--experimental-granite-model PATH`` with a local
Granite INT8 ``.onnx`` or ``.ort`` export and a sibling ``tokenizer.json``.
Marivo exits before listener readiness if runtime dependencies are missing or
it cannot prepare the managed files. The index prewarms asynchronously.
``GET /v2/info`` exposes its state and build time in ``catalog.search``.

``POST /v2/semantic/reload`` loads a fresh Catalog. It matches canonical IDs,
reuses vectors with identical search text, encodes new or changed text, and drops
removed objects. A successful reload publishes the Catalog and index together.

``--experimental-granite-cache-dir`` persists row fingerprints and vectors. On
restart with a compatible archive, Marivo reuses matching rows and encodes only
new or changed text. Removed rows are omitted from the new archive. If every
current row is reusable, the first text search initializes the encoder.

Read details with ``GET /v2/semantic/objects/{id}`` and navigate links with
``GET /v2/semantic/objects/{id}/related?relation=dependencies`` or ``dependents``.
Use canonical ``kind:path`` identities, URL-encoded in paths. Details retain business
context, formulas, units, time bindings and project-relative provenance while
omitting connection fields and file/HTTP source locations.
For an exact ``datasource:<name>`` identity,
``GET /v2/semantic/objects/{id}/datasource-properties`` exposes approved literal
settings and declared credential references to callers with the project token.
``env_refs`` maps credential fields to their exact ``md.env(...)`` lookup names
without resolving credential values. It is ``{}`` when the datasource declares
no references.

The service loads the Catalog in a fresh interpreter without querying business
sources. ``GET /v2/info`` reports its load status. Send an empty body to
``POST /v2/semantic/reload`` to load edited models and atomically replace the snapshot.
Failure retains the previous snapshot; an active load returns
``409 catalog_load_in_progress``. Reload invalidates old Catalog cursors and never
changes saved results. No script endpoints, current-result aliases or HTTP v1
compatibility routes are provided.

TLS, exact-origin CORS, response budgets and read deadlines are configured through
``marivo serve --help``. Native Help is ``analysis.results.service.http``.
The `result service guide <https://marivo.io/docs/latest/reference/read-service/>`_
contains deployment, retention and value-encoding details.
