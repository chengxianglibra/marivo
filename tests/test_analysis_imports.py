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
    assert mv.DiscoverInsufficientDataError is mv.errors.DiscoverInsufficientDataError


def test_session_class_exposes_execution_surface():
    import marivo.analysis as mv
    from marivo.analysis.session.core import Session

    assert callable(Session.observe)
    assert callable(Session.compare)
    assert callable(Session.decompose)
    assert callable(Session.correlate)
    assert callable(Session.forecast)
    assert callable(Session.assess_quality)
    assert callable(Session.hypothesis_test)
    assert isinstance(Session.discover, property)
    assert isinstance(Session.transform, property)
    assert callable(Session.from_pandas)
    assert callable(Session.explore_ibis)
    assert callable(Session.promote_metric_frame)
    assert callable(Session.promote_delta_frame)
    assert callable(Session.promote_attribution_frame)
    assert mv.PromotionFailedError is mv.errors.PromotionFailedError
    assert mv.ExplorationResult is not None


def test_analysis_exports_non_execution_escape_hatch_types():
    import marivo.analysis as mv

    assert mv.ArtifactRef("frame_1").id == "frame_1"
    assert mv.PromotionPolicy().on_missing == "fail_closed"
    assert hasattr(mv.errors, "PromotionFailedError")


def test_analysis_exports_metadata_dtos() -> None:
    import marivo.analysis as mv

    assert mv.TableMetadata is not None
    assert mv.ColumnMetadata is not None
    assert mv.PartitionMetadata is not None
    assert mv.MetadataWarning is not None


def test_analysis_exports_report_artifact_surface() -> None:
    from marivo.analysis.publish import (
        MarivoReportArtifact,
        ReportManifest,
        load_report_artifact,
        validate_report_artifact,
        write_report_artifact,
    )

    assert MarivoReportArtifact.__name__ == "MarivoReportArtifact"
    assert ReportManifest.__name__ == "ReportManifest"
    assert callable(validate_report_artifact)
    assert callable(load_report_artifact)
    assert callable(write_report_artifact)


def test_analysis_exports_report_mcp_adapter_surface() -> None:
    from marivo.analysis.publish import (
        ReportChartSpec,
        ReportColumn,
        ReportMetric,
        materialize_mcp_adapter,
        to_mcp_artifact_payload,
    )

    assert ReportChartSpec.__name__ == "ReportChartSpec"
    assert ReportColumn.__name__ == "ReportColumn"
    assert ReportMetric.__name__ == "ReportMetric"
    assert callable(to_mcp_artifact_payload)
    assert callable(materialize_mcp_adapter)


def test_analysis_exports_report_html_adapter_surface() -> None:
    from marivo.analysis.publish import (
        materialize_html_adapter,
        render_report_html,
        to_html_report_payload,
    )

    assert callable(to_html_report_payload)
    assert callable(render_report_html)
    assert callable(materialize_html_adapter)


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
        # Publish functions
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
        # Frame Meta types
        "BaseFrameMeta",
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
        "FramePreview",
        "ValidationIssue",
    ]
    for name in removed:
        assert name not in mv.__all__, f"{name} should not be in mv.__all__"
        assert not hasattr(mv, name), f"{name} should not be a mv attribute"


def test_analysis_publish_submodule_accessible() -> None:
    import marivo.analysis as mv

    assert hasattr(mv, "publish")
    assert callable(mv.publish.help)
    assert mv.publish.MarivoReportArtifact is not None
