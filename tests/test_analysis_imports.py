"""Smoke tests that the analysis package and its subpackages import cleanly."""


def test_package_imports():
    import marivo.analysis

    assert marivo.analysis is not None


def test_namespace_alias_works():
    import marivo.analysis as mv

    assert mv.__name__ == "marivo.analysis"


def test_analysis_keeps_frame_and_policy_exports():
    import marivo.analysis as mv
    from marivo.analysis.frames.forecast import ForecastFrameMeta
    from marivo.analysis.frames.hypothesis import HypothesisTestResultMeta
    from marivo.analysis.frames.quality import QualityReportMeta

    assert mv.SamplingPolicy().pairing == "window_bucket"
    assert HypothesisTestResultMeta.model_fields["kind"].default == "hypothesis_test_result"
    assert ForecastFrameMeta.model_fields["kind"].default == "forecast_frame"
    assert QualityReportMeta.model_fields["kind"].default == "quality_report"


def test_analysis_does_not_export_execution_operators():
    import marivo.analysis as mv

    removed = [
        "observe",
        "compare",
        "decompose",
        "correlate",
        "forecast",
        "assess_quality",
        "hypothesis_test",
        "discover",
        "transform",
        "select",
        "from_pandas",
        "explore_ibis",
        "promote_metric_frame",
        "promote_delta_frame",
        "promote_attribution_frame",
    ]
    for name in removed:
        assert name not in mv.__all__
        assert not hasattr(mv, name)


def test_session_class_exposes_execution_surface():
    import marivo.analysis as mv

    assert callable(mv.Session.observe)
    assert callable(mv.Session.compare)
    assert callable(mv.Session.decompose)
    assert callable(mv.Session.correlate)
    assert callable(mv.Session.forecast)
    assert callable(mv.Session.assess_quality)
    assert callable(mv.Session.hypothesis_test)
    assert isinstance(mv.Session.discover, property)
    assert isinstance(mv.Session.transform, property)
    assert callable(mv.Session.from_pandas)
    assert callable(mv.Session.explore_ibis)
    assert callable(mv.Session.promote_metric_frame)
    assert callable(mv.Session.promote_delta_frame)
    assert callable(mv.Session.promote_attribution_frame)
    assert mv.ExplorationResult is not None
    assert not hasattr(mv.MetricFrame, "from_dataframe")


def test_analysis_exports_non_execution_escape_hatch_types():
    import marivo.analysis as mv

    assert mv.ArtifactRef("frame_1").id == "frame_1"
    assert mv.PromotionPolicy().on_missing == "fail_closed"
    assert hasattr(mv.errors, "PromotionFailedError")


def test_analysis_exports_public_surface_by_layer() -> None:
    import marivo.analysis as mv

    construction_types = {
        "MetricRef",
        "DimensionRef",
        "CalendarRef",
        "ArtifactRef",
        "TimeScope",
        "AlignmentPolicy",
        "PromotionPolicy",
        "SamplingPolicy",
    }
    core_runtime_result_types = {
        "Session",
        "SessionSummary",
        "JobSummary",
        "BaseFrame",
        "BaseFrameMeta",
        "FrameSummary",
        "FramePreview",
        "FrameSummaryEntry",
        "Lineage",
        "LineageStep",
    }
    namespaces = {"session", "evidence", "frames", "errors", "publish"}

    for name in construction_types | core_runtime_result_types | namespaces:
        assert name in mv.__all__, name
        assert hasattr(mv, name), name


def test_analysis_keeps_subdomain_dtos_out_of_top_level() -> None:
    import marivo.analysis as mv
    import marivo.datasource as md

    subdomain_only = {
        # Evidence DTOs live under mv.evidence.
        "Assessment",
        "AssociationSummary",
        "AttributedDriver",
        "BlockedFollowup",
        "ChangeFact",
        "EvidenceTrace",
        "Finding",
        "ForecastSummary",
        "OpenAnomaly",
        "OpenQuestion",
        "Proposition",
        "QualitySummary",
        "SessionKnowledge",
        "Subject",
        "TestedHypothesis",
        "TimeWindow",
        "TriggeredByFollowup",
        # Datasource metadata DTOs live under md.
        "ColumnMetadata",
        "MetadataWarning",
        "PartitionMetadata",
        "TableMetadata",
        "PreviewResult",
        "PreviewSamplePolicy",
        "PreviewWarning",
        # Error classes live under mv.errors.
        "DiscoverInsufficientDataError",
        "PromotionFailedError",
        # The old persisted-frame listing name conflicts with constructor refs.
        "FrameRef",
        "FrameRecord",
        # Removed compatibility or implementation-detail analysis names.
        "Grain",
        "GrainInput",
        "GrainUnit",
        "LagPolicy",
        "TimeGrain",
        "ensure_grain_supported",
        "load_frame",
    }

    for name in subdomain_only:
        assert name not in mv.__all__, f"{name} should not be in mv.__all__"
        assert not hasattr(mv, name), f"{name} should not be a mv attribute"

    assert mv.evidence.Subject is not None
    assert md.TableMetadata is not None
    assert md.PreviewResult is not None
    assert mv.errors.PromotionFailedError is not None
    assert mv.errors.DiscoverInsufficientDataError is not None


def test_analysis_exports_report_artifact_surface() -> None:
    from marivo.analysis.publish import (
        MarivoReportArtifact,
        ReportManifest,
        load_report_artifact,
        validate_report_artifact,
    )

    assert MarivoReportArtifact.__name__ == "MarivoReportArtifact"
    assert ReportManifest.__name__ == "ReportManifest"
    assert callable(validate_report_artifact)
    assert callable(load_report_artifact)


def test_analysis_exports_report_mcp_adapter_surface() -> None:
    from marivo.analysis.publish import (
        ReportChartSpec,
        ReportColumn,
        ReportMetric,
        to_mcp_artifact_payload,
    )

    assert ReportChartSpec.__name__ == "ReportChartSpec"
    assert ReportColumn.__name__ == "ReportColumn"
    assert ReportMetric.__name__ == "ReportMetric"
    assert callable(to_mcp_artifact_payload)


def test_analysis_exports_report_html_adapter_surface() -> None:
    from marivo.analysis.publish import (
        render_report_html,
        to_html_report_payload,
    )

    assert callable(to_html_report_payload)
    assert callable(render_report_html)


def test_analysis_does_not_export_publish_or_meta_types() -> None:
    import marivo.analysis as mv

    removed = [
        # Publish types
        "DataPolicy",
        "Dataset",
        "DatasetMetadata",
        "Flow",
        "FlowStep",
        "GroundedClaim",
        "Grounding",
        "LocalFilesystemTarget",
        "MarivoReportArtifact",
        "McpAdapterMetadata",
        "PublishConfig",
        "PublishReportResult",
        "PublishTarget",
        "ReportBlock",
        "ReportChartSpec",
        "ReportColumn",
        "ReportManifest",
        "ReportMetric",
        "ReportPackageValidationIssue",
        "ReportPackageValidationResult",
        "ReportSection",
        "ReportSpec",
        "SourceProvenance",
        # Publish functions (directory-based APIs replaced by session methods)
        "export_report_json_schema",
        "load_report_artifact",
        "materialize_html_adapter",
        "materialize_mcp_adapter",
        "publish_report_package",
        "render_report_html",
        "to_html_report_payload",
        "to_mcp_artifact_payload",
        "validate_report_artifact",
        "write_report_artifact",
        "MetricFrameMeta",
        "DeltaFrameMeta",
        "AttributionFrameMeta",
        "ComponentFrameMeta",
        "ForecastFrameMeta",
        "AssociationResultMeta",
        "ExplorationResultMeta",
        "HypothesisTestResultMeta",
        "CandidateSetMeta",
        "QualityReportMeta",
        # Internal-only types
        "CandidateShape",
        "ValidationIssue",
    ]
    for name in removed:
        assert name not in mv.__all__, f"{name} should not be in mv.__all__"
        assert not hasattr(mv, name), f"{name} should not be a mv attribute"


def test_analysis_publish_does_not_export_directory_materialization_helpers() -> None:
    """Directory-based report helpers are internal; use session methods instead."""
    from marivo.analysis import publish

    removed = [
        "write_report_artifact",
        "materialize_html_adapter",
        "materialize_mcp_adapter",
        "publish_report_package",
    ]
    for name in removed:
        assert name not in publish.__all__, f"{name} should not be in publish.__all__"
        assert not hasattr(publish, name), f"{name} should not be a publish attribute"


def test_session_exposes_report_methods() -> None:
    import marivo.analysis as mv

    assert callable(mv.Session.save_report)
    assert callable(mv.Session.validate_report)
    assert callable(mv.Session.publish_report)


def test_analysis_publish_submodule_accessible() -> None:
    import marivo.analysis as mv

    assert hasattr(mv, "publish")
    assert callable(mv.publish.help)
    assert mv.publish.MarivoReportArtifact is not None
