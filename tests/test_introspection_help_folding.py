"""Pin the top-level help fold partition for each surface.

Any symbol that moves between enumerated and folded, or changes family, must be
a deliberate edit here. The top-level index enumerates only ``callable`` /
``module`` / ``topic`` kinds plus a small per-surface ``pinned_entries`` set of
core result types; every other public symbol folds into a family by suffix.

See docs/superpowers/specs/2026-06-13-agent-result-surface-design.md
("help top-level folding").
"""

from __future__ import annotations

from typing import Any, cast

from marivo.introspection.surface import Surface, render

_FOLD_LEAK_SUFFIXES = ("Ref", "Details", "Brief", "Frame")


def _families(surface: Surface) -> dict[str, list[str]]:
    data = cast("dict[str, Any]", render(surface, None, "json"))
    return {fam["label"]: fam["members"] for fam in data.get("families", [])}


def _enumerated(surface: Surface) -> set[str]:
    data = cast("dict[str, Any]", render(surface, None, "json"))
    return {entry["name"] for entry in data["entries"]}


def _assert_no_value_family_leaks(enumerated: set[str]) -> None:
    leaked = sorted(n for n in enumerated if n.endswith(_FOLD_LEAK_SUFFIXES))
    assert not leaked, f"value/identifier families leaked into enumerated index: {leaked}"


def test_semantic_fold_partition() -> None:
    from marivo.semantic.help import _surface

    surface = _surface()
    fams = _families(surface)
    assert fams["Detail shapes"] == [
        "DatasourceDetails",
        "DerivedMetricDetails",
        "DimensionDetails",
        "DomainDetails",
        "EntityDetails",
        "MeasureDetails",
        "MetricDetails",
        "RelationshipDetails",
        "SimpleMetricDetails",
        "TimeDimensionDetails",
    ]
    assert "Briefs" not in fams
    assert fams["References"] == [
        "DimensionRef",
        "DomainRef",
        "EntityRef",
        "MeasureRef",
        "MetricRef",
        "RelationshipRef",
        "SemanticRef",
        "TimeDimensionRef",
    ]
    assert "Type aliases" not in fams
    assert "Internal IR types" not in fams
    assert fams["Reports"] == ["ReadinessReport", "RichnessReport"]
    assert fams["Results"] == ["ParityResult", "VerifyResult"]
    assert fams["Catalog objects"] == [
        "CatalogCollection",
        "CatalogObject",
        "Datasource",
        "Dimension",
        "Domain",
        "Entity",
        "Measure",
        "Metric",
        "Relationship",
        "TimeDimension",
    ]
    assert set(fams["Other types"]) == {
        "AiContextValue",
        "AuthoringQuestion",
        "JoinKey",
        "ReadinessInputSummary",
        "ReadinessIssue",
        "SemanticKind",
        "SqlProvenance",
    }
    enumerated = _enumerated(surface)
    assert "SemanticCatalog" in enumerated
    assert "SemanticObject" not in enumerated
    assert "SemanticObjectList" not in enumerated
    _assert_no_value_family_leaks(enumerated)


_MINIMAL_DOMAIN_PY = (
    "import marivo.datasource as md\n"
    "import marivo.semantic as ms\n"
    'ms.domain(name="sales", owner="Mina Zhang", default=True)\n'
)
_DATASETS_PY = (
    "import marivo.datasource as md\n"
    "import marivo.semantic as ms\n"
    'orders = ms.entity(name="orders", datasource=md.ref("datasource.warehouse"), '
    'source=md.table("orders"))\n'
    "\n"
    "@ms.metric(entities=[orders], additivity='additive', )\n"
    "def revenue(table):\n"
    "    return table.amount.sum()\n"
)


def _make_catalog(semantic_project_factory):
    from marivo.semantic.catalog import SemanticCatalog

    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": _DATASETS_PY,
        }
    )
    return SemanticCatalog(project)


def test_semantic_catalog_has_no_legacy_list_method(semantic_project_factory) -> None:
    catalog = _make_catalog(semantic_project_factory)

    assert not hasattr(catalog, "list")


def test_datasource_fold_partition() -> None:
    from marivo.datasource.help import _surface

    surface = _surface()
    fams = _families(surface)
    # Convenience functions (duckdb, trino, etc.) are top-level callables, not folded.
    assert "Datasource specs" not in fams
    assert fams["References"] == ["DatasourceRef"]
    assert "Internal IR types" not in fams
    assert fams["Results"] == ["DatasourceTestResult"]
    assert "Metadata types" not in fams
    assert set(fams["Other types"]) == {
        "ClickHouseSpec",
        "DatasourceConnection",
        "DatasourceDescription",
        "DatasourceList",
        "DatasourceSpec",
        "DatasourceSummary",
        "DuckDBSpec",
        "ExecutionCapabilities",
        "MySQLSpec",
        "PartitionInspection",
        "PartitionScope",
        "Partitioning",
        "PhysicalExtent",
        "PostgresSpec",
        "TrinoSpec",
        "UnprunedScope",
    }
    enumerated = _enumerated(surface)
    # Entry-point and input types are pinned as top-level entries, not folded.
    assert {
        "DatasourceCatalog",
        "DiscoverySnapshot",
        "SourceInspection",
        "TableSource",
    } <= enumerated
    _assert_no_value_family_leaks(enumerated)


def test_analysis_fold_partition() -> None:
    from marivo.analysis.help import _surface

    surface = _surface()
    fams = _families(surface)
    assert fams["References"] == ["ArtifactRef", "CalendarRef", "SemanticRef"]
    assert fams["Frames"] == [
        "AttributionFrame",
        "DeltaFrame",
        "ForecastFrame",
        "MetricFrame",
    ]
    assert "Type aliases" not in fams
    assert fams["Other types"] == [
        "AbsoluteWindow",
        "AlignmentPolicy",
        "AssociationResult",
        "CandidateSet",
        "CatalogObject",
        "HypothesisTestResult",
        "QualityReport",
        "TimeScope",
    ]
    enumerated = _enumerated(surface)
    assert "Session" in enumerated
    _assert_no_value_family_leaks(enumerated)
