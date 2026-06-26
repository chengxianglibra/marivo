"""Pin the public ``__all__`` of each marivo surface module.

Any added or removed public symbol must be a deliberate edit here.
See docs/superpowers/specs/2026-06-13-agent-result-surface-design.md.
"""

from __future__ import annotations

import importlib

import pytest

import marivo.analysis as ma
import marivo.datasource as md
import marivo.semantic as ms
from marivo.introspection.surface import render

SEMANTIC_PUBLIC = {
    "AiContextValue",
    "AuthoringQuestion",
    "BriefStatus",
    "CrossEntityMetricBrief",
    "DatasourceDetails",
    "DecisionRecord",
    "DerivedMetricBrief",
    "DerivedMetricDetails",
    "DimensionBrief",
    "DimensionDetails",
    "DimensionRef",
    "DomainBrief",
    "DomainDetails",
    "DomainRef",
    "EntityBrief",
    "EntityDetails",
    "EntityRef",
    "JoinKey",
    "LadderOrderError",
    "MeasureBrief",
    "MeasureDetails",
    "MeasureRef",
    "MetricBrief",
    "MetricDetails",
    "MetricRef",
    "ParityResult",
    "ReadinessInputSummary",
    "ReadinessIssue",
    "ReadinessReport",
    "RegisteredMatch",
    "RichnessReport",
    "RelationshipBrief",
    "RelationshipDetails",
    "RelationshipRef",
    "SemanticCatalog",
    "SemanticKind",
    "SemanticKindInput",
    "SemanticObject",
    "SemanticObjectDetails",
    "SemanticObjectList",
    "SemanticRef",
    "SemanticRefInput",
    "SimpleMetricDetails",
    "SqlProvenance",
    "TimeDimensionBrief",
    "TimeDimensionDetails",
    "TimeDimensionRef",
    "VerifyResult",
    "aggregate",
    "ai_context",
    "count",
    "csv",
    "datetime",
    "dimension",
    "dimension_column",
    "domain",
    "entity",
    "errors",
    "from_sql",
    "help",
    "help_text",
    "hour_prefix",
    "join_on",
    "linear",
    "load",
    "measure",
    "measure_column",
    "metric",
    "parquet",
    "parity_check",
    "prepare_cross_entity_metric",
    "prepare_derived_metric",
    "prepare_dimension",
    "prepare_domain",
    "prepare_entity",
    "prepare_measure",
    "prepare_metric",
    "prepare_relationship",
    "prepare_time_dimension",
    "ratio",
    "readiness",
    "semi_additive",
    "record_decision",
    "ref",
    "relationship",
    "richness",
    "snapshot",
    "strptime",
    "table",
    "time_dimension",
    "time_dimension_column",
    "timestamp",
    "typing",
    "validity",
    "verify_object",
    "weighted_average",
}

ANALYSIS_PUBLIC = {
    "AbsoluteWindow",
    "AlignmentKind",
    "AlignmentPolicy",
    "ArtifactAffordance",
    "ArtifactColumn",
    "ArtifactContract",
    "ArtifactParamTemplate",
    "ArtifactPrecondition",
    "ArtifactRef",
    "ArtifactSchema",
    "ArtifactState",
    "AssociationResult",
    "AttributionFrame",
    "BaseFrame",
    "BaseFrameMeta",
    "BlockingIssue",
    "CalendarPolicy",
    "CalendarRef",
    "CandidateObjective",
    "CandidateSet",
    "ComponentFrame",
    "CoverageFrame",
    "ConfidenceScope",
    "DeltaFrame",
    "DiscoverSensitivity",
    "ExplorationResult",
    "ForecastFrame",
    "FramePreview",
    "FrameSummary",
    "FrameSummaryEntry",
    "HypothesisTestResult",
    "JobSummary",
    "Lineage",
    "LineageStep",
    "dow_aligned",
    "holiday_aligned",
    "holiday_and_dow_aligned",
    "MetricFrame",
    "PromotionPolicy",
    "PromotionSemanticAnchors",
    "QualityReport",
    "ReportRegistration",
    "SamplingPolicy",
    "SemanticObject",
    "SemanticRef",
    "Session",
    "SessionSummary",
    "SlicePredicate",
    "SlicePredicateOp",
    "SliceScalar",
    "SliceValue",
    "TimeScope",
    "TimeScopeInput",
    "window_bucket",
    "errors",
    "evidence",
    "frames",
    "help",
    "help_text",
    "publish",
    "session",
}

DATASOURCE_PUBLIC = {
    "ColumnDiscovery",
    "DatasourceCatalog",
    "DatasourceDescription",
    "DatasourceList",
    "DatasourceRef",
    "DatasourceSummary",
    "DatasourceTestResult",
    "DimensionDiscoveryResult",
    "DimensionValueDiscoveryResult",
    "DimensionValueFact",
    "DiscoveryEvidenceEntry",
    "DiscoveryIssue",
    "DiscoverySignal",
    "EntityDiscoveryResult",
    "FormatCandidate",
    "JoinSide",
    "MeasureDiscoveryResult",
    "PrimaryKeyCandidate",
    "PreviewResult",
    "RawSqlResult",
    "RelationshipDiscoveryResult",
    "ScanScope",
    "TableMetadata",
    "TableSource",
    "TimeColumnDiscovery",
    "TimeDimensionDiscoveryResult",
    "TimeValueRange",
    "clickhouse",
    "connect",
    "csv",
    "describe",
    "discover_dimension_values",
    "discover_dimensions",
    "discover_entity",
    "discover_measures",
    "discover_relationship",
    "discover_time_dimensions",
    "duckdb",
    "help",
    "help_text",
    "latest_partition",
    "list",
    "load",
    "mysql",
    "partition",
    "parquet",
    "postgres",
    "preview",
    "raw_sql",
    "ref",
    "register",
    "remove",
    "table",
    "test",
    "trino",
    "unpruned",
}


def test_semantic_all_is_pinned() -> None:
    assert set(ms.__all__) == SEMANTIC_PUBLIC


def test_analysis_all_is_pinned() -> None:
    assert set(ma.__all__) == ANALYSIS_PUBLIC


def test_datasource_all_is_pinned() -> None:
    assert set(md.__all__) == DATASOURCE_PUBLIC


def _top_level_entries(surface):
    return render(surface, None, "json")["entries"]


@pytest.mark.parametrize(
    "surface_factory",
    [
        "marivo.semantic.help._surface",
        "marivo.datasource.help._surface",
        "marivo.analysis.help._surface",
    ],
)
def test_help_index_has_no_blank_summary(surface_factory: str) -> None:
    module_path, attr = surface_factory.rsplit(".", 1)
    surface = getattr(importlib.import_module(module_path), attr)()
    blank = [e["name"] for e in _top_level_entries(surface) if not e["summary"].strip()]
    assert blank == [], f"{surface_factory} has blank help summaries: {blank}"


def test_semantic_input_aliases_hidden_from_index() -> None:
    from marivo.semantic.help import _surface

    data = render(_surface(), None, "json")
    visible_names = {e["name"] for e in data["entries"]}
    visible_names |= {name for f in data["families"] for name in f["members"]}
    assert "SemanticKindInput" not in visible_names
    assert "SemanticRefInput" not in visible_names


def test_no_internal_ir_family_and_small_other_bucket() -> None:
    from marivo.datasource.help import _surface as d_surface
    from marivo.semantic.help import _surface as s_surface

    for surface in (s_surface(), d_surface()):
        data = render(surface, None, "json")
        labels = {f["label"] for f in data["families"]}
        assert "Internal IR types" not in labels
        other = next((f for f in data["families"] if f["label"] == "Other types"), None)
        assert other is None or len(other["members"]) <= 10, other


def test_followup_action_is_not_public_analysis_api() -> None:
    assert "FollowupAction" not in ma.__all__
    assert not hasattr(ma, "FollowupAction")
