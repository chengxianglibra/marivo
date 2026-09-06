marivo.datasource
=================

.. currentmodule:: marivo.datasource

.. automodule:: marivo.datasource
   :no-members:

Registration & lifecycle
------------------------

.. autosummary::
   :toctree: api/
   :nosignatures:

   connect
   DatasourceConnection
   register
   DatasourceSpec
   DuckDBSpec
   SQLiteSpec
   TrinoSpec
   MySQLSpec
   PostgresSpec
   ClickHouseSpec
   load
   list
   remove
   test

Connection ownership
--------------------

``connect()`` returns a caller-owned connection; prefer ``with md.connect(...)``.
Inspection, sampling, semantic preview, source health, and parity release their
operation-owned connections before returning. Analysis caches connections until
``Session.close()``. Connections retain validated environment-secret provenance;
host-injected secrets are never cached.

Internal handshakes have an independent 30-second deadline. ``raw_sql`` and
sampling/preview scopes retain their execution timeout, rather than an end-to-end
budget. ``test()`` retains its whole-roundtrip deadline. SQLite opens locally on
its caller's thread and cannot be forcibly interrupted during synchronous open.

Credential injection
--------------------

.. autosummary::
   :toctree: api/
   :nosignatures:

   credential_scope
   CredentialRequest
   CredentialResolver
   SecretValue
   DatasourceCredentialError
   DatasourceCredentialScopeError

Source constructors
-------------------

.. autosummary::
   :toctree: api/
   :nosignatures:

   csv
   source_param
   json
   parquet
   duckdb
   sqlite
   postgres
   mysql
   clickhouse
   trino
   table
   source_column

Inspection & snapshots
----------------------

.. autosummary::
   :toctree: api/
   :nosignatures:

   inspect
   SourceInspection
   DiscoverySnapshot
   PartitionInspection
   PhysicalExtent
   Partitioning
   ExecutionCapabilities
   raw_sql

``DiscoverySnapshot.contract().show()`` displays the query-free read contract
for a snapshot, including its selected columns, retained-row shape, and value
evidence state. When ``persist_values=True``, ``snapshot.retained_values`` is a
tuple of dictionaries keyed by the selected column names; with the default
``persist_values=False`` it remains unavailable and no follow-up query occurs.

Scope helpers
-------------

Datasource specs and explicit scopes expose bounded ``render()`` / ``show()``
cards for agent-relevant state. Specs show only their core connection target,
credential field names, and hidden-configuration count; scopes show guards and
a bounded predicate preview. Read ``.fields`` / ``.env_refs`` or ``.values``
for exact data, and ``.contract()`` for mechanical continuation.

.. autosummary::
   :toctree: api/
   :nosignatures:

   partition
   time_range
   unpruned

Description
-----------

``python -m marivo help`` is an environment bootstrap only. Use the sole public
coordinator, ``marivo.help("datasource.<target>")``, for bounded focused help
rendered from the datasource registry. The ``md`` namespace executes datasource
operations and intentionally has no ``md.help()`` alias. A
``SourceInspection`` card points to
``marivo.help("datasource.SourceInspection.sample")`` for the complete
single-process acquisition and query-free projection chain.

.. autosummary::
   :toctree: api/
   :nosignatures:

   describe

Catalog & refs
--------------

Datasource identities use ``marivo.semantic.ref.datasource(...)``. The
datasource module accepts that exact ref at inspection and raw-SQL boundaries,
but does not define a second ref type.

.. autosummary::
   :toctree: api/
   :nosignatures:

   DatasourceCatalog
   DatasourceList
   DatasourceSummary
   DatasourceDescription

Datasource vs source
--------------------

``md.duckdb(...)`` and ``md.sqlite(...)`` declare datasources. ``md.table(...)``
is the source descriptor for internal tables/views inside either datasource.
``md.parquet(...)`` and ``md.csv(...)`` remain DuckDB file sources.
``md.json(...)`` covers DuckDB JSON files and URLs, including a parameterized
JSON-body POST request. These sources are used with a datasource ref in inspection and
snapshot calls; they are not datasource declarations.

Metadata & sources
------------------

.. autosummary::
   :toctree: api/
   :nosignatures:

   TableSource
   TableColumnBindingIR
   PartitionScope
   UnprunedScope

Results
-------

``md.raw_sql(...)`` returns a bounded terminal ``RawSqlResult``. Its ``shape``
and ``row_count`` describe returned bounded rows:
``row_count == shape[0] == returned_row_count``. Read
``requested_limit`` and ``is_truncated`` alongside that count; it is not
full-source cardinality. Ordered ``columns`` and isolated ``to_pandas()`` are
available, but ``RawSqlResult`` has no ``contract()``, typed affordances, or
typed-analysis re-entry.

.. autosummary::
   :toctree: api/
   :nosignatures:

   DatasourceTestResult
   DatasourceFailure
