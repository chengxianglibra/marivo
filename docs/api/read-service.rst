Marivo read service
===================

Install the optional ``marivo[api]`` extra and supply an
administrator-configured ``MARIVO_API_TOKEN`` in the service environment.
Agents can use the packaged ``marivo-service`` workflow and resolve
``marivo help analysis.results.service`` for the installed script and HTTP
contracts.

.. code-block:: sh

   marivo serve --project-root /path/to/project --port 0

Use the foreground process's JSON readiness line to locate the listener.
With ``Authorization: Bearer <token>``, fetch ``GET /v1/openapi.json`` for
the route, request, response and error schemas. The ``/v1`` contract supports
the current semantic Catalog, committed AnalysisFrame reports and script endpoints.

Publish a result with :meth:`marivo.analysis.AnalysisFrame.persist` and retain
its ``session_id`` and ``artifact_id``. Request
``GET /v1/sessions/{session_id}/frames/{artifact_id}`` to read the complete
saved report, including its schema, lineage, source reads, rows and periods.
Directory routes use cursor pagination. Python callers can continue to use
:meth:`marivo.analysis.AnalysisFrame.read_page` and
:meth:`marivo.analysis.AnalysisFrame.read_periods`.

Place a script at ``endpoints/sales/revenue.py`` with one synchronous
``main(session: mv.Session) -> mv.AnalysisFrame`` or
``main(session: mv.Session) -> pd.DataFrame``. Both receive a new project Session.
``POST /v1/data/sales/revenue/refresh`` with an empty body executes it, saves
the result and atomically updates the endpoint's current reference.
``GET /v1/data/sales/revenue`` only reads saved data; an absent result returns 404.
Neither route accepts query parameters, including ``refresh``.
Restart the service to discover new script paths.

AnalysisFrames use the existing Store and fixed Frame route. DataFrames use
independent Parquet snapshots under ``.marivo/server/dataframes/`` and
``GET /v1/dataframes/{result_id}``. Responses include ``kind``, a fixed ``ref``
and ``committed_at``; DataFrames also include the producing ``session_id``.
Save the service identity and fixed ref in reports to retain the same version
across refreshes. Current references live under ``.marivo/server/endpoints/``.
DataFrame reads need no Session or Analysis Store.

Refreshes use ``--refresh-timeout-seconds`` (default 300) and
``--max-refreshes`` (default 1). Same-endpoint refreshes are mutually exclusive
across service processes. Failures before reference publication preserve the
previous result. Do not automatically retry refresh: a timeout or lost response
can still mean publication succeeded. Read the endpoint's current reference
before deciding whether another refresh is needed.

``POST /v1/semantic/reload`` with an empty body starts a new Catalog load.
An active load returns ``409 catalog_load_in_progress``; wait for it to finish
before requesting reload again. Ordinary reads share the initial load or use
the last successful snapshot during reload. Failed loads retain that snapshot.
Catalog loads continue after a client disconnect or request timeout, bounded
by their own read deadline and service shutdown.

Unexpected request failures log the operation and traceback to service stderr,
which also receives Catalog worker stderr. HTTP errors do not expose tracebacks.
For ``catalog_load_failed``, inspect the logs and run ``marivo doctor --semantic``
in the bound project.

For startup options, use ``marivo serve --help``. See the
`project read service guide <https://marivo.io/docs/latest/reference/read-service/>`_.
