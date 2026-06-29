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
   parquet
   duckdb
   postgres
   mysql
   clickhouse
   trino
   table

Preview
-------

.. autosummary::
   :toctree: api/
   :nosignatures:

   preview

Discovery
---------

.. autosummary::
   :toctree: api/
   :nosignatures:

   discover_entity
   discover_dimensions
   discover_time_dimensions
   discover_measures
   discover_relationship
   discover_dimension_values
   inspect_table
   inspect_partitions
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

Metadata & sources
------------------

.. autosummary::
   :toctree: api/
   :nosignatures:

   TableMetadata
   ScanScope
   TableSource

Results
-------

.. autosummary::
   :toctree: api/
   :nosignatures:

   DatasourceTestResult
   PreviewResult
   DatasourceResult
   JoinSide
