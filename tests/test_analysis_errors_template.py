"""Structured string template for analysis errors."""

from marivo.analysis.errors import (
    AnalysisError,
    AxisNotInPanelDimensionsError,
    DatasourceEnvVarMissingError,
    DatasourceSecretStorePermissionsError,
    MetricNotFoundError,
    SemanticKindMismatchError,
    WindowInvalidError,
)


def test_analysis_error_renders_structured_sections_from_details_and_hint():
    err = AnalysisError(
        message="something happened",
        hint="try fixing X",
        details={
            "location": "session.compare call",
            "cause": "param a was invalid",
            "fix_snippet": 'delta = session.compare(cur, base, alignment=mv.AlignmentPolicy(kind="window_bucket"))',
            "doc": "marivo-skills/marivo-analysis/references/pitfalls.md",
        },
    )

    rendered = str(err)

    assert rendered.startswith("AnalysisError: something happened")
    assert "Location: session.compare call" in rendered
    assert "Cause: param a was invalid" in rendered
    assert "Hint: try fixing X" in rendered
    assert "Fix:" in rendered
    assert (
        '  delta = session.compare(cur, base, alignment=mv.AlignmentPolicy(kind="window_bucket"))'
        in rendered
    )
    assert "Docs: marivo-skills/marivo-analysis/references/pitfalls.md" in rendered


def test_base_template_omits_missing_optional_sections():
    err = AnalysisError(message="something happened")

    rendered = str(err)

    assert rendered == "AnalysisError: something happened"
    assert "Location:" not in rendered
    assert "Cause:" not in rendered
    assert "Hint:" not in rendered
    assert "Fix:" not in rendered
    assert "Docs:" not in rendered


def test_datasource_env_var_missing_mentions_cache_and_validation() -> None:
    err = DatasourceEnvVarMissingError(
        message="secret missing",
        details={"datasource": "wh", "field": "password", "env_var": "TRINO_PASSWORD"},
    )

    rendered = str(err)

    assert "TRINO_PASSWORD" in rendered
    assert "not set in os.environ and is not present in ~/.marivo/secrets.toml" in rendered
    assert 'mv.datasources.test("wh")' in rendered


def test_secret_store_permissions_error_has_chmod_fix() -> None:
    err = DatasourceSecretStorePermissionsError(
        message="secret store is too open",
        details={"path": "/Users/alice/.marivo/secrets.toml", "mode": 0o644},
    )

    rendered = str(err)

    assert "Location: /Users/alice/.marivo/secrets.toml" in rendered
    assert "0o644" in rendered
    assert "chmod 600 ~/.marivo/secrets.toml" in rendered


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
    assert "Location: custom call" in rendered
    assert "Cause: custom cause" in rendered
    assert "Hint: custom hint" in rendered
    assert "Fix:" in rendered
    assert "  custom_fix()" in rendered
    assert "Docs: custom-doc.md" in rendered


def test_semantic_kind_mismatch_has_compare_fix_template():
    err = SemanticKindMismatchError(
        message="wrong frame kind",
        details={"got_kind": "delta_frame", "expected_kind": "metric_frame"},
    )

    rendered = str(err)

    assert "Location:" in rendered
    assert "delta_frame" in rendered
    assert "metric_frame" in rendered
    assert "Fix:" in rendered
    assert (
        'cur  = session.observe(mv.MetricRef("sales.revenue"), '
        'timescope={"start": "2026-07-01", "end": "2026-09-30"})'
    ) in rendered
    assert (
        'base = session.observe(mv.MetricRef("sales.revenue"), '
        'timescope={"start": "2025-07-01", "end": "2025-09-30"})'
    ) in rendered
    assert (
        '  delta = session.compare(cur, base, alignment=mv.AlignmentPolicy(kind="window_bucket"))'
        in rendered
    )
    assert 'session.observe("revenue"' not in rendered


def test_semantic_kind_mismatch_without_kind_details_is_not_compare_specific():
    err = SemanticKindMismatchError(message="decompose requires a DeltaFrame input")

    rendered = str(err)

    assert "session.compare call" not in rendered
    assert (
        'delta = session.compare(cur, base, alignment=mv.AlignmentPolicy(kind="window_bucket"))'
        not in rendered
    )


def test_semantic_kind_mismatch_for_delta_expected_is_not_compare_specific():
    err = SemanticKindMismatchError(
        message="decompose requires a DeltaFrame input",
        details={"got_kind": "metric_frame", "expected_kind": "delta_frame"},
    )

    rendered = str(err)

    assert "metric_frame" in rendered
    assert "delta_frame" in rendered
    assert "session.compare call" not in rendered
    assert (
        'delta = session.compare(cur, base, alignment=mv.AlignmentPolicy(kind="window_bucket"))'
        not in rendered
    )


def test_window_invalid_has_window_fix_template():
    err = WindowInvalidError(
        message="window is invalid",
        details={"window": "last quarter"},
    )

    rendered = str(err)

    assert "last quarter" in rendered
    assert "Fix:" in rendered
    assert (
        '  session.observe(mv.MetricRef("sales.revenue"), '
        'timescope={"start": "2026-07-01", "end": "2026-09-30"})'
    ) in rendered
    assert 'session.observe("revenue", window=' not in rendered


def test_metric_not_found_has_list_metrics_fix_template():
    err = MetricNotFoundError(
        message="metric not found",
        details={"metric_id": "revenu"},
    )

    rendered = str(err)

    assert "metric_id=revenu" in rendered
    assert "Fix:" in rendered
    assert "  project.list_metrics()  # confirm the exact id" in rendered
    assert (
        'session.observe(mv.MetricRef("<registered_metric_id>"), '
        'timescope={"start": "2026-07-01", "end": "2026-09-30"})'
    ) in rendered
    assert 'session.observe("<registered_metric_id>", window=' not in rendered


def test_metric_not_found_uses_model_and_metric_details_in_cause():
    err = MetricNotFoundError(
        message="metric not found",
        details={"model": "sales", "metric": "revenu"},
    )

    rendered = str(err)

    assert "sales.revenu" in rendered
    assert "Fix:" in rendered
    assert "  project.list_metrics()  # confirm the exact id" in rendered
    assert "<metric_id>" not in rendered


def test_metric_not_found_without_details_does_not_show_wrong_id_remediation():
    err = MetricNotFoundError(message="metric 'sales.revenue' references no datasets")

    rendered = str(err)

    assert "Fix:" not in rendered
    assert "ms.list_metrics()" not in rendered
    assert "registered_metric_id" not in rendered
    assert "Requested metric is not registered" not in rendered
    assert "metric_id=<metric_id>" not in rendered
    assert "<metric_id>" not in rendered


def test_metric_not_found_renders_available_ids_preview():
    err = MetricNotFoundError(
        message="metric not found",
        details={
            "metric_id": "revenu",
            "available_ids": ["sales.revenue", "sales.orders"],
        },
    )

    rendered = str(err)

    assert "Available metrics: sales.revenue, sales.orders" in rendered


def test_metric_not_found_truncates_long_available_ids():
    available = [f"m.metric_{i}" for i in range(15)]
    err = MetricNotFoundError(
        message="metric not found",
        details={"metric_id": "absent", "available_ids": available},
    )

    rendered = str(err)

    assert "m.metric_0, m.metric_1," in rendered
    assert "m.metric_9" in rendered
    assert "(+5 more)" in rendered


def test_dimension_not_found_renders_available_ids_preview():
    from marivo.analysis.errors import DimensionFieldNotFoundError

    err = DimensionFieldNotFoundError(
        message="dimension 'regn' not found",
        details={
            "dimension_id": "regn",
            "searched_datasets": ["orders"],
            "available_ids": ["region", "country"],
        },
    )

    rendered = str(err)

    assert "Available dimensions: region, country" in rendered


def test_axis_not_in_panel_dimensions_renders_paste_ready_fix_snippet():
    err = AxisNotInPanelDimensionsError(
        message="axis not in panel",
        details={"axis": "channel", "available_dimensions": ["region", "country"]},
    )

    rendered = str(err)

    assert 'session.decompose(delta, axis=mv.DimensionRef("region"))' in rendered
    assert "region, country" in rendered


def test_select_attribute_mismatch_lists_valid_attributes_for_shape():
    err = SemanticKindMismatchError(
        message="select attribute 'axis' is not available for shape 'point_anomaly'",
        details={
            "shape": "point_anomaly",
            "attribute": "axis",
            "valid_fields": ["direction", "item_id", "recommended_followups", "score", "window"],
        },
    )

    rendered = str(err)

    assert "Valid attributes for shape 'point_anomaly':" in rendered
    assert "direction, item_id, recommended_followups, score, window" in rendered
    assert 'cands.select(rank=1, attribute="direction")' in rendered


def test_no_backend_factory_default_template_fields_populated() -> None:
    from marivo.analysis.errors import NoBackendFactoryError

    err = NoBackendFactoryError(
        message="datasource 'tiny_orders' did not resolve to an ibis backend.",
        details={"datasource": "tiny_orders"},
    )
    rendered = str(err)
    assert "Fix:" in rendered
    assert "datasource='tiny_orders' resolved to None or a non-ibis object" in rendered
    assert "mv.datasources.register" in rendered
    assert "@ms.datasource" not in rendered
    assert "Docs: marivo-skills/marivo-semantic/references/datasource.md" in rendered


def test_no_backend_factory_without_details_uses_session_backend_template() -> None:
    from marivo.analysis.errors import NoBackendFactoryError

    err = NoBackendFactoryError(
        message="session has no backend_factory; data-materializing intents need one",
        hint="Pass backends={...} or backend_factory=... when creating or attaching.",
    )

    rendered = str(err)

    assert "datasource=None" not in rendered
    assert "@ms.datasource" not in rendered
    assert "returned None or a non-ibis object" not in rendered
    assert "session has no backend factory configured" in rendered
    assert "Fix:" in rendered
    assert "mv.session.attach" in rendered
    assert "mv.datasources.register" in rendered
    assert "backend_factory=" in rendered
    assert "Docs: marivo-skills/marivo-semantic/references/datasource.md" in rendered
