"""Pin the public ``__all__`` of each marivo surface module.

Any added or removed public symbol must be a deliberate edit here.
See docs/superpowers/specs/2026-06-13-agent-result-surface-design.md.
"""

from __future__ import annotations

import marivo.analysis as ma
import marivo.datasource as md
import marivo.semantic as ms

SEMANTIC_PUBLIC = {
    "AggregateFoldInput",
    "AggregateFoldValue",
    "AiContextValue",
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
    "JoinKey",
    "MeasureEntry",
    "MeasureDetails",
    "MetricEntry",
    "MetricDetails",
    "ParityResult",
    "Participant",
    "ParticipantRoleHandle",
    "PreviewBatchResult",
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
    "SqlProvenance",
    "TimeDimensionEntry",
    "TimeDimensionDetails",
    "VerifyResult",
    "aggregate",
    "ai_context",
    "all_rows",
    "bind",
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
    "help",
    "help_text",
    "hour_prefix",
    "join_on",
    "linear",
    "load",
    "measure",
    "measure_column",
    "metric",
    "parity_check",
    "participant",
    "participant_role",
    "ratio",
    "ref",
    "semi_additive",
    "relationship",
    "richness",
    "snapshot",
    "strptime",
    "time_dimension",
    "time_dimension_column",
    "timestamp",
    "trailing",
    "typing",
    "validity",
    "weighted_mean",
    "where",
}

ANALYSIS_PUBLIC = {
    "AnalysisScope",
    "AnomalyCandidate",
    "ArtifactDigest",
    "ArtifactDigestPage",
    "ArtifactIssue",
    "AssociationFact",
    "CandidateSelection",
    "ChangeFact",
    "ComparabilityIssue",
    "CompletenessDeclaration",
    "ContributionFact",
    "CrossSectionalOutlierSelection",
    "DataQualityIssue",
    "DriverAxisSelection",
    "EvidenceAvailabilityIssue",
    "EvidenceDerivationTrace",
    "EventFrame",
    "EventPattern",
    "EventWatermarkReceipt",
    "EventWatermarkRequest",
    "EveryStart",
    "Finding",
    "FindingPage",
    "FirstPerSubject",
    "ForecastOutput",
    "FrameSummaryEntry",
    "FrameSummaryPage",
    "ObservationFact",
    "PatternStep",
    "PeriodShiftSelection",
    "PointAnomalySelection",
    "QualityCheckResult",
    "SliceSelection",
    "TestDecision",
    "WindowSelection",
    "help",
    "help_text",
    "session",
    "declared_complete_through",
    "every_start",
    "first_per_subject",
    "Session",
    "MetricFrame",
    "DeltaFrame",
    "AttributionFrame",
    "CandidateSet",
    "AssociationResult",
    "HypothesisTestResult",
    "ForecastFrame",
    "QualityReport",
    "window_bucket",
    "dow_aligned",
    "holiday_aligned",
    "holiday_and_dow_aligned",
    "AlignmentPolicy",
    "runtime_metric",
    "sequence",
    "step",
    "ArtifactRef",
    "CalendarRef",
    "TimeScope",
    "AbsoluteWindow",
}

DATASOURCE_PUBLIC = {
    "ClickHouseSpec",
    "DatasourceCatalog",
    "DatasourceConnection",
    "DatasourceDescription",
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
    "TrinoSpec",
    "UnprunedScope",
    "clickhouse",
    "connect",
    "csv",
    "describe",
    "duckdb",
    "help",
    "help_text",
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
    "sqlite",
    "table",
    "test",
    "trino",
    "unpruned",
}


def test_semantic_all_is_pinned() -> None:
    assert set(ms.__all__) == SEMANTIC_PUBLIC


def test_analysis_all_is_pinned() -> None:
    assert set(ma.__all__) == ANALYSIS_PUBLIC


def test_phase2_datasource_all_is_pinned_to_the_baseline() -> None:
    assert set(md.__all__) == DATASOURCE_PUBLIC


def test_followup_action_is_not_public_analysis_api() -> None:
    assert "FollowupAction" not in ma.__all__
    assert not hasattr(ma, "FollowupAction")


def test_analysis_public_surface_keeps_session_summaries_not_frame_summaries() -> None:
    assert not hasattr(ma, "FrameSummary")
    assert not hasattr(ma, "FramePreview")
    assert not hasattr(ma, "AssociationResultSummary")
    assert not hasattr(ma, "QualityReportSummary")
    assert hasattr(ma, "FrameSummaryEntry")
    assert hasattr(ma, "JobSummary")
