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
   TrinoSpec
   MySQLSpec
   PostgresSpec
   ClickHouseSpec
   load
   list
   remove
   ref
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

Help & description
------------------

.. autosummary::
   :toctree: api/
   :nosignatures:

   help
   help_text
   describe

Catalog & refs
--------------

.. autosummary::
   :toctree: api/
   :nosignatures:

   DatasourceCatalog
   DatasourceRef
   DatasourceList
   DatasourceSummary
   DatasourceDescription

Datasource vs source
--------------------

``md.duckdb(...)`` declares a datasource. ``md.table(...)`` is the source
descriptor for internal tables/views inside that datasource. ``md.parquet(...)``,
``md.csv(...)``, and ``md.json(...)`` are DuckDB file sources used with a
datasource ref in inspection and snapshot calls; they are not datasource
declarations.

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

.. autosummary::
   :toctree: api/
   :nosignatures:

   DatasourceTestResult
