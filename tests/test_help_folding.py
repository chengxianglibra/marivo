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
        "SemanticObjectDetails",
        "SimpleMetricDetails",
        "TimeDimensionDetails",
    ]
    assert fams["Briefs"] == [
        "CrossEntityMetricBrief",
        "DerivedMetricBrief",
        "DimensionBrief",
        "DomainBrief",
        "EntityBrief",
        "MeasureBrief",
        "MetricBrief",
        "RelationshipBrief",
        "TimeDimensionBrief",
    ]
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
    assert set(fams["Other types"]) == {
        "AiContextValue",
        "AuthoringQuestion",
        "DecisionRecord",
        "JoinKey",
        "LadderOrderError",
        "ReadinessInputSummary",
        "ReadinessIssue",
        "RegisteredMatch",
        "SemanticKind",
        "SqlProvenance",
    }
    enumerated = _enumerated(surface)
    assert {"SemanticCatalog", "SemanticObject", "SemanticObjectList"} <= enumerated
    _assert_no_value_family_leaks(enumerated)


def test_datasource_fold_partition() -> None:
    from marivo.datasource.help import _surface

    surface = _surface()
    fams = _families(surface)
    # Convenience functions (duckdb, trino, etc.) are top-level callables, not folded.
    assert "Datasource specs" not in fams
    assert "DatasourceSpec" not in {name for members in fams.values() for name in members}
    assert fams["References"] == ["DatasourceRef"]
    assert "Internal IR types" not in fams
    assert set(fams["Results"]) == {
        "DatasourceTestResult",
        "DimensionDiscoveryResult",
        "DimensionValueDiscoveryResult",
        "EntityDiscoveryResult",
        "MeasureDiscoveryResult",
        "PreviewResult",
        "RawSqlResult",
        "RelationshipDiscoveryResult",
        "TimeDimensionDiscoveryResult",
    }
    assert fams["Metadata types"] == ["TableMetadata"]
    assert set(fams["Other types"]) == {
        "DatasourceDescription",
        "DatasourceList",
        "DatasourceSummary",
        "DimensionValueFact",
        "DiscoveryEvidenceEntry",
        "DiscoveryIssue",
        "DiscoverySignal",
        "TimeValueRange",
    }
    enumerated = _enumerated(surface)
    # Entry-point and input types are pinned as top-level entries, not folded.
    assert {
        "ColumnDiscovery",
        "DatasourceCatalog",
        "FormatCandidate",
        "JoinSide",
        "PrimaryKeyCandidate",
        "ScanScope",
        "TableSource",
        "TimeColumnDiscovery",
    } <= enumerated
    _assert_no_value_family_leaks(enumerated)


def test_analysis_fold_partition() -> None:
    from marivo.analysis.help import _surface

    surface = _surface()
    fams = _families(surface)
    assert fams["References"] == ["ArtifactRef", "CalendarRef", "SemanticRef"]
    assert fams["Frames"] == [
        "AttributionFrame",
        "BaseFrame",
        "ComponentFrame",
        "CoverageFrame",
        "DeltaFrame",
        "ForecastFrame",
        "MetricFrame",
    ]
    assert fams["Type aliases"] == ["TimeScopeInput"]
    assert set(fams["Other types"]) == {
        "AbsoluteWindow",
        "AlignmentKind",
        "AlignmentPolicy",
        "ArtifactAffordance",
        "ArtifactColumn",
        "ArtifactContract",
        "ArtifactParamTemplate",
        "ArtifactPrecondition",
        "ArtifactSchema",
        "ArtifactState",
        "AssociationResult",
        "BaseFrameMeta",
        "BlockingIssue",
        "CalendarPolicy",
        "CandidateObjective",
        "CandidateSet",
        "ConfidenceScope",
        "DeriveContext",
        "DiscoverSensitivity",
        "FramePreview",
        "FrameSummary",
        "FrameSummaryEntry",
        "HypothesisTestResult",
        "IbisQuerySpec",
        "JobSummary",
        "Lineage",
        "LineageStep",
        "MetricColumnBinding",
        "MetricColumns",
        "QualityReport",
        "ReportRegistration",
        "SamplingPolicy",
        "SemanticObject",
        "SessionSummary",
        "SlicePredicate",
        "SlicePredicateOp",
        "SliceScalar",
        "SliceValue",
        "TimeScope",
    }
    enumerated = _enumerated(surface)
    assert "Session" in enumerated
    _assert_no_value_family_leaks(enumerated)


def test_publish_fold_partition() -> None:
    from marivo.analysis.publish.help import _surface

    surface = _surface()
    fams = _families(surface)
    assert fams["Metadata types"] == ["DatasetMetadata", "McpAdapterMetadata"]
    assert set(fams["Other types"]) == {
        "DataPolicy",
        "Dataset",
        "Flow",
        "FlowStep",
        "GroundedClaim",
        "Grounding",
        "LocalFilesystemTarget",
        "MarivoReportArtifact",
        "PublishConfig",
        "PublishReportResult",
        "PublishTarget",
        "ReplayCheckIssue",
        "ReplayCheckResult",
        "ReportBlock",
        "ReportChartSpec",
        "ReportColumn",
        "ReportManifest",
        "ReportMetric",
        "ReportPackageValidationIssue",
        "ReportPackageValidationResult",
        "ReportSection",
        "ReportSpec",
        "SecretScanIssue",
        "SourceProvenance",
    }
    _assert_no_value_family_leaks(_enumerated(surface))
