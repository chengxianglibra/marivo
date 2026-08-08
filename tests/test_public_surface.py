"""Pin the public ``__all__`` of each marivo surface module.

Any added or removed public symbol must be a deliberate edit here.
See docs/superpowers/specs/2026-06-13-agent-result-surface-design.md.
"""

from __future__ import annotations

import json
import pydoc
import subprocess
import sys

import marivo
import marivo.analysis as ma
import marivo.datasource as md
import marivo.semantic as ms

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
    "PeriodCorrespondence",
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
    "StateModelDetails",
    "StateModelEntry",
    "StateTransition",
    "SqlProvenance",
    "TimeDimensionEntry",
    "TimeDimensionDetails",
    "VerifyResult",
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
    "ratio",
    "ref",
    "semi_additive",
    "relationship",
    "richness",
    "snapshot",
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
}

ANALYSIS_PUBLIC = {
    "AnalysisScope",
    "AnomalyCandidate",
    "ArtifactDigest",
    "ArtifactDigestPage",
    "ArtifactIssue",
    "AssociationFact",
    "CandidateOrigin",
    "CandidateResolutionIssue",
    "CandidateSelection",
    "ChangeFact",
    "ComparabilityIssue",
    "CompletenessDeclaration",
    "ContributionFact",
    "CrossSectionalOutlierSelection",
    "DataQualityIssue",
    "DriverAxisSelection",
    "DroppedBefore",
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
    "FunnelLossRate",
    "FromInception",
    "InState",
    "ObservationFact",
    "PatternStep",
    "PeriodShiftSelection",
    "PointAnomalySelection",
    "QualityCheckResult",
    "SliceSelection",
    "TestDecision",
    "WindowSelection",
    "session",
    "declared_complete_through",
    "dropped_before",
    "every_start",
    "first_per_subject",
    "funnel_loss_rate",
    "from_inception",
    "in_state",
    "Session",
    "OntologyMetricCandidate",
    "SubjectSet",
    "LifecycleFrame",
    "MetricFrame",
    "DeltaFrame",
    "AttributionFrame",
    "CandidateSet",
    "AssociationResult",
    "HypothesisTestResult",
    "ForecastFrame",
    "QualityReport",
    "window_bucket",
    "day_of_week",
    "period_progress",
    "period_correspondence",
    "AlignmentPolicy",
    "runtime_metric",
    "sequence",
    "step",
    "ArtifactRef",
    "TimeScope",
    "AbsoluteWindow",
    "Grain",
    "grain",
}

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
    "sqlite",
    "table",
    "test",
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


def test_ontology_metric_candidate_has_no_legacy_alias() -> None:
    assert hasattr(ma, "OntologyMetricCandidate")
    assert not hasattr(ma, "SemanticMetricCandidate")


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
