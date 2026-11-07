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
``GET /v1/data/sales/revenue?refresh=true`` executes it, saves the result and
atomically updates the endpoint's current reference. Without ``refresh=true``,
the endpoint only reads saved data; an absent result returns 404.
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
previous result. A lost response after publication can still mean success;
read the endpoint's current reference before retrying.

For startup options, use ``marivo serve --help``. See the
`project read service guide <https://marivo.io/docs/latest/reference/read-service/>`_.
