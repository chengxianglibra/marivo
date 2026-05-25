"""Structured string template for analysis_py errors."""

from marivo.analysis_py.errors import (
    AnalysisError,
    MetricNotFoundError,
    SemanticKindMismatchError,
    WindowInvalidError,
)


def test_analysis_error_renders_structured_sections_from_details_and_hint():
    err = AnalysisError(
        message="something happened",
        hint="try fixing X",
        details={
            "location": "mv.compare call",
            "cause": "param a was invalid",
            "fix_snippet": "delta = mv.compare(cur, base)",
            "doc": "marivo-skill/marivo-py-analysis/references/pitfalls.md",
        },
    )

    rendered = str(err)

    assert rendered.startswith("AnalysisError: something happened")
    assert "发生位置: mv.compare call" in rendered
    assert "原因: param a was invalid" in rendered
    assert "建议: try fixing X" in rendered
    assert "正确写法:" in rendered
    assert "  delta = mv.compare(cur, base)" in rendered
    assert "相关文档: marivo-skill/marivo-py-analysis/references/pitfalls.md" in rendered


def test_base_template_omits_missing_optional_sections():
    err = AnalysisError(message="something happened")

    rendered = str(err)

    assert rendered == "AnalysisError: something happened"
    assert "发生位置:" not in rendered
    assert "原因:" not in rendered
    assert "建议:" not in rendered
    assert "正确写法:" not in rendered
    assert "相关文档:" not in rendered


def test_metric_not_found_uses_class_name_head():
    err = MetricNotFoundError(message="metric 'revenue' is unknown")

    assert str(err).startswith("MetricNotFoundError: metric 'revenue' is unknown")


def test_subclass_template_defaults_are_used_when_details_are_missing():
    class CustomAnalysisError(AnalysisError):
        def _template_fields(self) -> dict[str, str]:
            return {
                "location": "custom call",
                "cause": "custom cause",
                "fix_snippet": "custom_fix()",
                "doc": "custom-doc.md",
            }

    err = CustomAnalysisError(message="custom failed", hint="custom hint")

    rendered = str(err)

    assert rendered.startswith("CustomAnalysisError: custom failed")
    assert "发生位置: custom call" in rendered
    assert "原因: custom cause" in rendered
    assert "建议: custom hint" in rendered
    assert "正确写法:" in rendered
    assert "  custom_fix()" in rendered
    assert "相关文档: custom-doc.md" in rendered


def test_semantic_kind_mismatch_has_compare_fix_template():
    err = SemanticKindMismatchError(
        message="wrong frame kind",
        details={"got_kind": "delta_frame", "expected_kind": "metric_frame"},
    )

    rendered = str(err)

    assert "发生位置:" in rendered
    assert "delta_frame" in rendered
    assert "metric_frame" in rendered
    assert "正确写法:" in rendered
    assert "  delta = mv.compare(cur, base)" in rendered


def test_semantic_kind_mismatch_without_kind_details_is_not_compare_specific():
    err = SemanticKindMismatchError(message="decompose requires a DeltaFrame input")

    rendered = str(err)

    assert "mv.compare call" not in rendered
    assert "delta = mv.compare(cur, base)" not in rendered


def test_semantic_kind_mismatch_for_delta_expected_is_not_compare_specific():
    err = SemanticKindMismatchError(
        message="decompose requires a DeltaFrame input",
        details={"got_kind": "metric_frame", "expected_kind": "delta_frame"},
    )

    rendered = str(err)

    assert "metric_frame" in rendered
    assert "delta_frame" in rendered
    assert "mv.compare call" not in rendered
    assert "delta = mv.compare(cur, base)" not in rendered


def test_window_invalid_has_window_fix_template():
    err = WindowInvalidError(
        message="window is invalid",
        details={"window": "last quarter"},
    )

    rendered = str(err)

    assert "last quarter" in rendered
    assert "正确写法:" in rendered
    assert '  mv.observe(mv.MetricRef("sales.revenue"), window="2026Q3")' in rendered


def test_metric_not_found_has_list_metrics_fix_template():
    err = MetricNotFoundError(
        message="metric not found",
        details={"metric_id": "revenu"},
    )

    rendered = str(err)

    assert "metric_id=revenu" in rendered
    assert "正确写法:" in rendered
    assert "  ms.list_metrics()  # confirm the exact id" in rendered


def test_metric_not_found_uses_model_and_metric_details_in_cause():
    err = MetricNotFoundError(
        message="metric not found",
        details={"model": "sales", "metric": "revenu"},
    )

    rendered = str(err)

    assert "sales.revenu" in rendered
    assert "正确写法:" in rendered
    assert "  ms.list_metrics()  # confirm the exact id" in rendered
    assert "<metric_id>" not in rendered


def test_metric_not_found_without_details_does_not_show_wrong_id_remediation():
    err = MetricNotFoundError(message="metric 'sales.revenue' references no datasets")

    rendered = str(err)

    assert "正确写法:" not in rendered
    assert "ms.list_metrics()" not in rendered
    assert "registered_metric_id" not in rendered
    assert "Requested metric is not registered" not in rendered
    assert "metric_id=<metric_id>" not in rendered
    assert "<metric_id>" not in rendered


def test_no_backend_factory_default_template_fields_populated() -> None:
    from marivo.analysis_py.errors import NoBackendFactoryError

    err = NoBackendFactoryError(
        message="@ms.datasource 'tiny_orders' did not return an ibis backend.",
        details={"datasource": "tiny_orders"},
    )
    rendered = str(err)
    assert "正确写法:" in rendered
    assert "datasource='tiny_orders' returned None or a non-ibis object" in rendered
    assert "ibis.duckdb.connect" in rendered
    assert "@ms.datasource" in rendered
    assert "相关文档: marivo-skill/marivo-py-semantic/references/pitfalls.md" in rendered


def test_no_backend_factory_without_details_uses_session_backend_template() -> None:
    from marivo.analysis_py.errors import NoBackendFactoryError

    err = NoBackendFactoryError(
        message="session has no backend_factory; data-materializing intents need one",
        hint="Pass backends={...} or backend_factory=... when creating or attaching.",
    )

    rendered = str(err)

    assert "datasource=None" not in rendered
    assert "@ms.datasource" not in rendered
    assert "returned None or a non-ibis object" not in rendered
    assert "session has no backend factory configured" in rendered
    assert "正确写法:" in rendered
    assert "mv.attach" in rendered
    assert "backends=" in rendered
    assert "backend_factory=" in rendered
    assert "相关文档: marivo-skill/marivo-py-analysis/references/pitfalls.md" in rendered
