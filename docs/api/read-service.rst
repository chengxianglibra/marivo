Marivo persisted result service
===============================

Save an AnalysisFrame with :meth:`marivo.analysis.AnalysisFrame.persist` and export
its fixed identity through ``saved.ref.to_dict()``. Save a plain pandas table with
:func:`marivo.analysis.persist_dataframe`; no Analysis Session or HTTP service is
required for table persistence. Keep the service connection separately.

Install ``marivo[api]``, configure ``MARIVO_API_TOKEN``, then run:

.. code-block:: sh

   marivo serve --project-root /path/to/project --port 0

The JSON readiness receipt reports ``api_version: 2``. All business requests use
Bearer authentication. ``GET /v2/openapi.json`` owns complete protocol schemas.
Read Frames with ``/v2/stores/{store_id}/sessions/{session_id}/frames/{artifact_id}``
and tables with ``/v2/dataframes/{result_id}``. The Store identity must match.
``GET /v2/info``, ``GET /v2/sessions`` and
``GET /v2/sessions/{session_id}/frames`` provide status and saved-result discovery.

Reads return complete fixed data or an error, preserve exact numeric/time/null
values and never execute producers, load the Catalog or query sources. Tables
retain physical schema and data; Frames additionally retain semantic metadata.
There are no script endpoints, current aliases, refresh operations, Catalog HTTP
or HTTP v1 compatibility routes. The plugin owns reports, text and refresh execution.

TLS, exact-origin CORS, response budgets and read deadlines are configured through
``marivo serve --help``. Native Help is ``analysis.results.service.http``.
The `result service guide <https://marivo.io/docs/latest/reference/read-service/>`_
contains deployment, retention and value-encoding details.
