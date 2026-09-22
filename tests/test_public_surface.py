"""Pin the public ``__all__`` of each marivo surface module.

Any added or removed public symbol must be a deliberate edit here.
"""

from __future__ import annotations

import hashlib
import json
import pydoc
import subprocess
import sys

import marivo
import marivo.analysis as ma
import marivo.datasource as md
import marivo.semantic as ms


def test_entity_sampling_is_absent_from_analysis_surface() -> None:
    assert not hasattr(ma, "engine_sample")
    assert not hasattr(ma, "EntitySamplingPolicy")
    assert not hasattr(ma.LogicalPopulationDataset, "sample")
    assert not hasattr(ma.MaterializedPopulationDataset, "sample")


SEMANTIC_PUBLIC = {
    "AggregateFoldInput",
    "AggregateFoldValue",
    "AiContextValue",
    "CalendarLevelDetails",
    "CalendarPeriodPage",
    "CatalogCollection",
    "CatalogEntry",
    "DatasourceEntry",
    "DatasourceDetails",
    "DerivedMetricDetails",
    "DimensionEntry",
    "DimensionDetails",
    "DomainEntry",
    "DomainDetails",
    "EntityEntry",
    "EntityDetails",
    "EventDetails",
    "EventEntry",
    "GrainToDate",
    "Inception",
    "JoinKey",
    "LifecycleState",
    "MeasureEntry",
    "MeasureDetails",
    "MetricEntry",
    "MetricDetails",
    "ModelStateHandle",
    "ParityResult",
    "Participant",
    "ParticipantRoleHandle",
    "PeriodCalendarDetails",
    "PeriodCalendarEntry",
    "PeriodCalendarKind",
    "TemporalSetKind",
    "PeriodCorrespondence",
    "PreviewBatchResult",
    "QuantileMetricInput",
    "ReadinessInputSummary",
    "ReadinessIssue",
    "ReadinessReport",
    "Ref",
    "RelationshipEntry",
    "RelationshipDetails",
    "RichnessReport",
    "SemanticCatalog",
    "SemanticKind",
    "SimpleMetricDetails",
    "SourceCheck",
    "SourceHealthCheckResult",
    "SourceHealthReport",
    "StateModelDetails",
    "StateModelEntry",
    "StateTransition",
    "TemporalOccurrencePage",
    "TemporalSetDetails",
    "TemporalSetEntry",
    "WorkScheduleDetails",
    "WorkScheduleEntry",
    "WorkScheduleKind",
    "SqlProvenance",
    "TimeDimensionEntry",
    "TimeDimensionDetails",
    "aggregate",
    "ai_context",
    "all_rows",
    "bind",
    "calendar_grain",
    "count",
    "cumulative",
    "datetime",
    "dimension",
    "dimension_column",
    "domain",
    "entity",
    "errors",
    "event",
    "from_sql",
    "grain_to_date",
    "hour_prefix",
    "inception",
    "join_on",
    "linear",
    "lifecycle_state",
    "load",
    "measure",
    "measure_column",
    "metric",
    "model_state",
    "parity_check",
    "participant",
    "participant_role",
    "period_calendar",
    "period_correspondence",
    "temporal_set",
    "quantile_metric",
    "ratio",
    "ref",
    "semi_additive",
    "relationship",
    "richness",
    "snapshot",
    "source_check",
    "state_model",
    "strptime",
    "time_dimension",
    "time_dimension_column",
    "timestamp",
    "trailing",
    "transition",
    "typing",
    "validity",
    "weighted_mean",
    "where",
    "work_schedule",
}

ANALYSIS_PUBLIC = {
    "Dataset",
    "LogicalDataset",
    "MaterializedDataset",
    "DatasetShapeId",
    "DatasetFieldId",
    "DatasetFieldIdentity",
    "DatasetPhysicalTypeState",
    "DatasetField",
    "DatasetRowBound",
    "DatasetCardinality",
    "DatasetOrderTerm",
    "DatasetOrdering",
    "DatasetByteCount",
    "DatasetFamilyRowSemantics",
    "DatasetRowContract",
    "DatasetRowSetContract",
    "DatasetSchema",
    "LogicalDatasetState",
    "MaterializedDatasetState",
    "DatasetContract",
    "DatasetFields",
    "DatasetFieldRef",
    "LogicalPopulationDataset",
    "MaterializedPopulationDataset",
    "LogicalMetricDataset",
    "MaterializedMetricDataset",
    "LogicalDeltaDataset",
    "MaterializedDeltaDataset",
    "LogicalAttributionDataset",
    "MaterializedAttributionDataset",
    "LogicalAssociationDataset",
    "MaterializedAssociationDataset",
    "LogicalForecastDataset",
    "MaterializedForecastDataset",
    "LogicalCandidateDataset",
    "MaterializedCandidateDataset",
    "LogicalEventDataset",
    "MaterializedEventDataset",
    "LogicalLifecycleDataset",
    "MaterializedLifecycleDataset",
    "AnalysisPredicate",
    "ForecastHorizon",
    "ForecastModel",
    "WindowBucketAlignment",
    "BoundedCompletenessDeclarationV1",
    "SourceOriginCompletenessDeclarationV1",
    "DroppedBefore",
    "EventPattern",
    "EveryStart",
    "FirstPerSubject",
    "FromInception",
    "FunnelLossRate",
    "Grain",
    "InState",
    "PatternStep",
    "TimeScope",
    "ArtifactDigest",
    "ArtifactRef",
    "ArtifactRevalidation",
    "ArtifactSummary",
    "EvidenceIntegrityError",
    "FailedRun",
    "Finding",
    "FindingPage",
    "IncompleteRun",
    "RunPage",
    "SessionGraph",
    "SucceededRun",
    "Session",
    "eq",
    "not_eq",
    "lt",
    "lte",
    "gt",
    "gte",
    "is_in",
    "is_null",
    "is_not_null",
    "all_of",
    "any_of",
    "not_",
    "grain",
    "time_scope",
    "window_bucket",
    "step",
    "sequence",
    "first_per_subject",
    "every_start",
    "dropped_before",
    "in_state",
    "funnel_loss_rate",
    "from_inception",
    "periods",
    "naive",
    "drift",
    "seasonal_naive",
    "runtime_metric",
    "session",
}

ANALYSIS_PUBLIC_ORDER_SHA256 = "bd9135b8f33fc450f706499b3fc6bda94947ccdb29cc0bf328a53107aa16c9e3"

DATASOURCE_PUBLIC = {
    "ClickHouseSpec",
    "DatasourceCatalog",
    "DatasourceConnection",
    "DatasourceDescription",
    "DatasourceFailure",
    "DatasourceList",
    "DatasourceSpec",
    "DatasourceSummary",
    "DatasourceTestResult",
    "DiscoverySnapshot",
    "DuckDBSpec",
    "ExecutionCapabilities",
    "MySQLSpec",
    "PartitionInspection",
    "PartitionScope",
    "Partitioning",
    "PhysicalExtent",
    "PostgresSpec",
    "SourceInspection",
    "SQLiteSpec",
    "TableSource",
    "TableColumnBindingIR",
    "TrinoSpec",
    "UnprunedScope",
    "clickhouse",
    "connect",
    "csv",
    "describe",
    "duckdb",
    "inspect",
    "json",
    "list",
    "load",
    "mysql",
    "partition",
    "parquet",
    "postgres",
    "raw_sql",
    "register",
    "remove",
    "source_column",
    "source_param",
    "sqlite",
    "table",
    "test",
    "time_range",
    "trino",
    "unpruned",
}


def test_top_level_help_teaches_supported_surface_imports_and_cli_routes() -> None:
    rendered = pydoc.render_doc(marivo, renderer=pydoc.plaintext)

    assert "import marivo.datasource as md" in rendered
    assert "import marivo.semantic as ms" in rendered
    assert "import marivo.analysis as mv" in rendered
    assert "python -m marivo help" in rendered
    assert "marivo.help(...)" in rendered
    assert "for all focused help" in rendered
    assert "python -m marivo help datasource" not in rendered


def test_time_range_does_not_expand_partition_scope_constructor_surface() -> None:
    import inspect

    import marivo.datasource as md

    assert str(inspect.signature(md.PartitionScope)) == (
        "(values: 'tuple[tuple[str, str], ...]', max_rows: 'int', timeout_seconds: 'int') -> None"
    )
    assert "_time_range" not in md.__all__
    assert "advisories" not in ms.ReadinessReport.__dataclass_fields__
    assert "TimeRangeScope" not in md.__all__
    assert "ColumnBindingCandidate" not in md.__all__


def test_top_level_package_does_not_add_public_convenience_exports() -> None:
    script = (
        "import json, marivo; "
        "print(json.dumps(sorted(name for name in dir(marivo) "
        "if name == '__version__' or not name.startswith('_'))))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == ["__version__", "help"]


def test_semantic_all_is_pinned() -> None:
    assert set(ms.__all__) == SEMANTIC_PUBLIC


def test_analysis_all_is_pinned() -> None:
    assert set(ma.__all__) == ANALYSIS_PUBLIC


def test_analysis_all_order_is_pinned() -> None:
    payload = json.dumps(ma.__all__, ensure_ascii=True, separators=(",", ":"))

    assert hashlib.sha256(payload.encode("utf-8")).hexdigest() == ANALYSIS_PUBLIC_ORDER_SHA256


def test_ontology_metric_candidate_has_no_legacy_alias() -> None:
    assert not hasattr(ma, "OntologyMetricCandidate")
    assert not hasattr(ma, "SemanticMetricCandidate")


def test_phase2_datasource_all_is_pinned_to_the_baseline() -> None:
    assert set(md.__all__) == DATASOURCE_PUBLIC


def test_followup_action_is_not_public_analysis_api() -> None:
    assert "FollowupAction" not in ma.__all__
    assert not hasattr(ma, "FollowupAction")


def test_run_query_stays_nested_under_terminal_runs() -> None:
    assert "RunQuery" not in ma.__all__
    assert not hasattr(ma, "RunQuery")


def test_analysis_public_surface_keeps_session_summaries_not_frame_summaries() -> None:
    assert not hasattr(ma, "FrameSummary")
    assert not hasattr(ma, "FramePreview")
    assert not hasattr(ma, "AssociationResultSummary")
    assert not hasattr(ma, "QualityReportSummary")
    assert not hasattr(ma, "FrameSummaryEntry")
    assert not hasattr(ma, "JobSummary")
