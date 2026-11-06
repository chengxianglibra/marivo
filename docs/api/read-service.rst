Marivo read service
===================

Install the optional ``marivo[api]`` extra and supply an
administrator-configured ``MARIVO_API_TOKEN`` in the service environment.

.. code-block:: sh

   marivo serve --project-root /path/to/project --port 0

Use the foreground process's JSON readiness line to locate the listener.
With ``Authorization: Bearer <token>``, fetch ``GET /v1/openapi.json`` for
the route, request, response and error schemas. The ``/v1`` contract supports
the current semantic Catalog and committed AnalysisFrame reports.

Publish a result with :meth:`marivo.analysis.AnalysisFrame.persist` and retain
its ``session_id`` and ``artifact_id``. Request
``GET /v1/sessions/{session_id}/frames/{artifact_id}`` to read the complete
saved report, including its schema, lineage, source reads, rows and periods.
Directory routes use cursor pagination. Python callers can continue to use
:meth:`marivo.analysis.AnalysisFrame.read_page` and
:meth:`marivo.analysis.AnalysisFrame.read_periods`.

For startup options, use ``marivo serve --help``. See the
`project read service guide <https://marivo.io/docs/latest/reference/read-service/>`_.
