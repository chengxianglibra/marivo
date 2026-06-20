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
   register
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

Inspection & preview
--------------------

.. autosummary::
   :toctree: api/
   :nosignatures:

   preview
   inspect_source
   inspect_table
   inspect_columns
   probe_join_keys

Discovery
---------

.. autosummary::
   :toctree: api/
   :nosignatures:

   help
   help_text
   describe

Source IR
---------

.. autosummary::
   :toctree: api/
   :nosignatures:

   DatasourceIR
   CsvSourceIR
   ParquetSourceIR
   AiContextIR
   DatasourceAiContextIR

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
   DatasourceSourceLocation
   DatasourceConnectionService

Metadata
--------

.. autosummary::
   :toctree: api/
   :nosignatures:

   TableMetadata
   ColumnMetadata
   ColumnProfile
   PartitionMetadata
   ScanScope

Results & reports
-----------------

.. autosummary::
   :toctree: api/
   :nosignatures:

   DatasourceTestResult
   PreviewResult
   PreviewSamplePolicy
   ScanReport
   ColumnInspection
   JoinKeyProbe
   JoinSide

Warnings
--------

.. autosummary::
   :toctree: api/
   :nosignatures:

   MetadataWarning
   PreviewWarning
