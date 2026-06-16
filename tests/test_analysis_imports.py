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
        "SemanticRef",
        "SemanticObject",
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
