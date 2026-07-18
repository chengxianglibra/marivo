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

    assert mv.window_bucket().kind == "window_bucket"
    assert callable(mv.dow_aligned)
    assert callable(mv.holiday_aligned)
    assert callable(mv.holiday_and_dow_aligned)
    assert mv.SamplingPolicy().pairing == "window_bucket"
    assert HypothesisTestResultMeta.model_fields["kind"].default == "hypothesis_test_result"
    assert ForecastFrameMeta.model_fields["kind"].default == "forecast_frame"
    assert QualityReportMeta.model_fields["kind"].default == "quality_report"


def test_session_does_not_expose_report_methods() -> None:
    import marivo.analysis as mv

    assert not hasattr(mv.Session, "save_report")
    assert not hasattr(mv.Session, "validate_report")
    assert not hasattr(mv.Session, "publish_report")


def test_analysis_publish_submodule_removed() -> None:
    import marivo.analysis as mv

    assert not hasattr(mv, "publish")


def test_session_class_exposes_execution_surface():
    import marivo.analysis as mv

    assert callable(mv.Session.observe)
    assert callable(mv.Session.compare)
    assert callable(mv.Session.attribute)
    assert callable(mv.Session.correlate)
    assert callable(mv.Session.forecast)
    assert callable(mv.Session.assess_quality)
    assert callable(mv.Session.hypothesis_test)
    assert isinstance(mv.Session.discover, property)
    assert not hasattr(mv.Session, "transform")
    assert not hasattr(mv.Session, "from_pandas")
    assert not hasattr(mv.Session, "explore_ibis")
    assert not hasattr(mv.Session, "promote_metric_frame")
    assert not hasattr(mv.Session, "promote_delta_frame")
    assert not hasattr(mv.Session, "promote_attribution_frame")
    assert not hasattr(mv.MetricFrame, "from_dataframe")


def test_analysis_exports_no_promotion_types():
    import marivo.analysis as mv

    assert mv.ArtifactRef("frame_1").id == "frame_1"
    assert not hasattr(mv, "PromotionPolicy")
    assert not hasattr(mv.errors, "PromotionFailedError")


def test_analysis_exports_public_surface_by_layer() -> None:
    import marivo.analysis as mv

    # Public surface exports listed in __all__.
    default_exports = {
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
        "SemanticRef",
        "CatalogObject",
        "ArtifactRef",
        "CalendarRef",
        "TimeScope",
        "AbsoluteWindow",
    }
    for name in default_exports:
        assert name in mv.__all__, name
        assert hasattr(mv, name), name

    # Types importable via explicit attribute access but not listed in the
    # top-level __all__ help index.
    advanced_internal = {
        "BaseFrame",
        "BaseFrameMeta",
        "JobSummary",
        "SessionSummary",
        "Lineage",
        "LineageStep",
        "SamplingPolicy",
        "errors",
        "evidence",
        "frames",
    }
    for name in advanced_internal:
        assert name not in mv.__all__, name
        assert hasattr(mv, name), name


def test_analysis_keeps_subdomain_dtos_out_of_top_level() -> None:
    import marivo.analysis as mv
    import marivo.datasource as md
    from marivo.datasource.metadata import TableMetadata
    from marivo.preview import PreviewResult

    assert mv.evidence.Subject is not None
    assert TableMetadata is not None
    assert PreviewResult is not None
    assert not hasattr(md, "TableMetadata")
    assert not hasattr(md, "PreviewResult")
    assert not hasattr(mv.errors, "PromotionFailedError")
    assert mv.errors.DiscoverInsufficientDataError is not None


def test_analysis_keeps_report_types_out_of_public_surface() -> None:
    import marivo.analysis as mv

    for name in [
        "ReportRegistration",
        "MarivoReportArtifact",
        "ReportManifest",
        "ReportSpec",
        "PublishReportResult",
    ]:
        assert name not in mv.__all__
        assert not hasattr(mv, name)


def test_analysis_exports_no_derive_symbols() -> None:
    import marivo.analysis as mv

    for name in (
        "ibis_query",
        "metric_columns",
        "time_column",
        "dimension_column",
        "DeriveContext",
        "IbisQuerySpec",
        "MetricColumnBinding",
        "MetricColumns",
    ):
        assert not hasattr(mv, name), f"mv.{name} should be removed"


def test_analysis_derive_module_is_deleted() -> None:
    import importlib

    with __import__("pytest").raises(ModuleNotFoundError):
        importlib.import_module("marivo.analysis.derive")


def test_analysis_escape_hatch_module_is_deleted() -> None:
    import importlib

    with __import__("pytest").raises(ModuleNotFoundError):
        importlib.import_module("marivo.analysis.escape_hatch")
