"""Smoke tests that the analysis package and its subpackages import cleanly."""


def test_package_imports():
    import marivo.analysis

    assert marivo.analysis is not None


def test_namespace_alias_works():
    import marivo.analysis as mv

    assert mv.__name__ == "marivo.analysis"


def test_analysis_keeps_frame_and_policy_exports():
    import marivo.analysis as mv

    assert mv.SamplingPolicy().pairing == "window_bucket"
    assert mv.HypothesisTestResultMeta.model_fields["kind"].default == "hypothesis_test_result"
    assert mv.ForecastFrameMeta.model_fields["kind"].default == "forecast_frame"
    assert mv.QualityReportMeta.model_fields["kind"].default == "quality_report"


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
    assert mv.ExplorationResultMeta.model_fields["kind"].default == "exploration_result"


def test_analysis_exports_non_execution_escape_hatch_types():
    import marivo.analysis as mv

    assert mv.ArtifactRef("frame_1").id == "frame_1"
    assert mv.PromotionPolicy().on_missing == "fail_closed"
    assert mv.ExplorationResultMeta.model_fields["kind"].default == "exploration_result"
    assert hasattr(mv.errors, "PromotionFailedError")


def test_analysis_exports_metadata_dtos() -> None:
    import marivo.analysis as mv

    assert mv.TableMetadata is not None
    assert mv.ColumnMetadata is not None
    assert mv.PartitionMetadata is not None
    assert mv.MetadataWarning is not None
