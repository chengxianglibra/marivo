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
    "AnalysisScope",
    "AnomalyCandidate",
    "ArtifactDigest",
    "ArtifactSummary",
    "ArtifactIssue",
    "ArtifactRevalidation",
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
    "EvidenceIntegrityError",
    "EvidenceRuleIssue",
    "EventOccurrenceBounds",
    "EventFrame",
    "EventPattern",
    "EventWatermarkReceipt",
    "EventWatermarkRequest",
    "EveryStart",
    "Finding",
    "FindingPage",
    "IncompleteRun",
    "FirstPerSubject",
    "ForecastOutput",
    "FailedRun",
    "FunnelLossRate",
    "FromInception",
    "InState",
    "ObservationFact",
    "PatternStep",
    "PeriodShiftSelection",
    "PointAnomalySelection",
    "QualityCheckResult",
    "RunPage",
    "SessionGraph",
    "SliceSelection",
    "SucceededRun",
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
    "window_bucket",
    "day_of_week",
    "period_progress",
    "period_correspondence",
    "occurrence_progress",
    "working_day_progress",
    "AlignmentPolicy",
    "runtime_metric",
    "sequence",
    "step",
    "ArtifactRef",
    "TimeScope",
    "AbsoluteWindow",
    "Grain",
    "grain",
    "time_scope",
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


def test_ontology_metric_candidate_has_no_legacy_alias() -> None:
    assert hasattr(ma, "OntologyMetricCandidate")
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
