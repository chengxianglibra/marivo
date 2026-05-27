"""Smoke tests that the analysis_py package and its subpackages import cleanly."""


def test_package_imports():
    import marivo.analysis_py

    assert marivo.analysis_py is not None


def test_namespace_alias_works():
    import marivo.analysis_py as mv

    assert mv.__name__ == "marivo.analysis_py"


def test_analysis_py_exports_test_operator():
    import marivo.analysis_py as mv

    assert callable(mv.test)
    assert mv.SamplingPolicy().pairing == "calendar_bucket"
    assert mv.HypothesisTestResultMeta.model_fields["kind"].default == "hypothesis_test_result"


def test_analysis_py_exports_forecast_operator():
    import marivo.analysis_py as mv

    assert callable(mv.forecast)
    assert mv.ForecastFrameMeta.model_fields["kind"].default == "forecast_frame"


def test_analysis_py_exports_assess_quality_operator():
    import marivo.analysis_py as mv

    assert callable(mv.assess_quality)
    assert mv.QualityReportMeta.model_fields["kind"].default == "quality_report"


def test_analysis_py_exports_escape_hatch_symbols():
    import marivo.analysis_py as mv

    assert callable(mv.from_pandas)
    assert callable(mv.promote_attribution_frame)
    assert mv.PromotionFailedError is mv.errors.PromotionFailedError
    assert mv.ExplorationResult is not None
    assert mv.ExplorationResultMeta.model_fields["kind"].default == "exploration_result"


def test_analysis_py_exports_escape_hatch_api():
    import marivo.analysis_py as mv

    assert callable(mv.from_pandas)
    assert callable(mv.explore_ibis)
    assert callable(mv.promote_metric_frame)
    assert callable(mv.promote_delta_frame)
    assert callable(mv.promote_attribution_frame)
    assert mv.ArtifactRef("frame_1").id == "frame_1"
    assert mv.PromotionPolicy().on_missing == "fail_closed"
    assert mv.ExplorationResultMeta.model_fields["kind"].default == "exploration_result"
    assert hasattr(mv.errors, "PromotionFailedError")
