"""Pin the public ``__all__`` of each marivo surface module.

Any added or removed public symbol must be a deliberate edit here.
See docs/superpowers/specs/2026-06-13-agent-result-surface-design.md.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

import marivo.analysis as ma
import marivo.datasource as md
import marivo.semantic as ms
from marivo.introspection.surface import render

SEMANTIC_PUBLIC = {
    "AiContextValue",
    "AuthoringQuestion",
    "CatalogCollection",
    "CatalogObject",
    "Datasource",
    "DatasourceDetails",
    "DerivedMetricDetails",
    "Dimension",
    "DimensionDetails",
    "DimensionRef",
    "Domain",
    "DomainDetails",
    "DomainRef",
    "Entity",
    "EntityDetails",
    "EntityRef",
    "JoinKey",
    "Measure",
    "MeasureDetails",
    "MeasureRef",
    "Metric",
    "MetricDetails",
    "MetricRef",
    "ParityResult",
    "ReadinessInputSummary",
    "ReadinessIssue",
    "ReadinessReport",
    "Relationship",
    "RelationshipDetails",
    "RelationshipRef",
    "RichnessReport",
    "SemanticCatalog",
    "SemanticKind",
    "SemanticRef",
    "SimpleMetricDetails",
    "SqlProvenance",
    "TimeDimension",
    "TimeDimensionDetails",
    "TimeDimensionRef",
    "VerifyResult",
    "aggregate",
    "ai_context",
    "count",
    "cumulative",
    "datetime",
    "dimension",
    "dimension_column",
    "domain",
    "entity",
    "errors",
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
    "ratio",
    "readiness",
    "semi_additive",
    "ref",
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
    "verify_object",
    "weighted_average",
}

ANALYSIS_PUBLIC = {
    "help",
    "help_text",
    "session",
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
    "ibis_query",
    "metric_columns",
    "time_column",
    "dimension_column",
    "SemanticRef",
    "CatalogObject",
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
    "DatasourceRef",
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


def test_semantic_input_aliases_removed_from_public_surface() -> None:
    from marivo.semantic.help import _surface

    assert "SemanticKindInput" not in ms.__all__
    assert "SemanticRefInput" not in ms.__all__

    data = render(_surface(), None, "json")
    visible_names = {e["name"] for e in data["entries"]}
    visible_names |= {name for f in data["families"] for name in f["members"]}
    assert "SemanticKindInput" not in visible_names
    assert "SemanticRefInput" not in visible_names


def test_semantic_api_docs_do_not_list_internal_input_aliases() -> None:
    docs = Path("docs/api/semantic.rst").read_text(encoding="utf-8")

    assert "SemanticKindInput" not in docs
    assert "SemanticRefInput" not in docs


def test_no_internal_ir_family_and_small_other_bucket() -> None:
    from marivo.datasource.help import _surface as d_surface
    from marivo.semantic.help import _surface as s_surface

    for surface in (s_surface(), d_surface()):
        data = render(surface, None, "json")
        labels = {f["label"] for f in data["families"]}
        assert "Internal IR types" not in labels
        other = next((f for f in data["families"] if f["label"] == "Other types"), None)
        assert other is None or len(other["members"]) <= 20, other


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
