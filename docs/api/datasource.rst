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

Source constructors
-------------------

.. autosummary::
   :toctree: api/
   :nosignatures:

   csv
   json
   parquet
   duckdb
   sqlite
   postgres
   mysql
   clickhouse
   trino
   table

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

Scope helpers
-------------

.. autosummary::
   :toctree: api/
   :nosignatures:

   partition
   unpruned

Description
-----------

Use ``marivo.help("datasource.<target>")`` for bounded focused help.

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
``md.parquet(...)``, ``md.csv(...)``, and ``md.json(...)`` remain DuckDB file
sources used with a datasource ref in inspection and snapshot calls; they are
not datasource declarations.

Metadata & sources
------------------

.. autosummary::
   :toctree: api/
   :nosignatures:

   TableSource
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
