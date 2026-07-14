"""Structured string template for analysis errors."""

from marivo.analysis._capabilities.model import LiveHelpTarget
from marivo.analysis.errors import (
    AnalysisError,
    AnalysisRepair,
    AxisNotInPanelDimensionsError,
    DatasourceEnvVarMissingError,
    DatasourceSecretStorePermissionsError,
    MetricNotFoundError,
    SemanticKindMismatchError,
    WindowInvalidError,
)


def test_analysis_error_renders_stable_fields_and_repair() -> None:
    repair = AnalysisRepair(
        kind="retry",
        action="Pass a parseable time_scope.",
        help_target=LiveHelpTarget(surface="analysis", canonical_id="observe"),
        snippet='session.observe(metric, time_scope={"start": "2026-07-01", "end": "2026-10-01"})',
    )
    err = AnalysisError(
        message="something happened",
        expected="a parseable absolute time_scope",
        received="param a was invalid",
        location="session.compare call",
        repair=repair,
        hint="try fixing X",
    )

    rendered = str(err)

    assert rendered.startswith("AnalysisError: something happened")
    assert "Location: session.compare call" in rendered
    assert "Expected: a parseable absolute time_scope" in rendered
    assert "Received: param a was invalid" in rendered
    assert "Hint: try fixing X" in rendered
    assert "Repair:" in rendered
    assert "  Pass a parseable time_scope." in rendered
    assert (
        '  session.observe(metric, time_scope={"start": "2026-07-01", "end": "2026-10-01"})'
        in rendered
    )
    assert "Help: mv.help('observe')" in rendered


def test_base_template_omits_missing_optional_sections() -> None:
    err = AnalysisError(message="something happened")

    rendered = str(err)

    assert rendered == "AnalysisError: something happened"
    assert "Location:" not in rendered
    assert "Expected:" not in rendered
    assert "Received:" not in rendered
    assert "Hint:" not in rendered
    assert "Repair:" not in rendered
    assert "Help:" not in rendered


def test_datasource_env_var_missing_mentions_cache_and_validation() -> None:
    err = DatasourceEnvVarMissingError(
        message="secret missing",
        details={"datasource": "wh", "field": "password", "env_var": "TRINO_PASSWORD"},
    )

    rendered = str(err)

    assert "TRINO_PASSWORD" in rendered
    assert "not set in os.environ and is not present in ~/.marivo/secrets.toml" in rendered
    assert 'md.test("wh")' in rendered


def test_secret_store_permissions_error_has_chmod_fix() -> None:
    err = DatasourceSecretStorePermissionsError(
        message="secret store is too open",
        details={"path": "/Users/alice/.marivo/secrets.toml", "mode": 0o644},
    )

    rendered = str(err)

    assert "Location: /Users/alice/.marivo/secrets.toml" in rendered
    assert "0o644" in rendered
    assert "chmod 600 ~/.marivo/secrets.toml" in rendered


def test_metric_not_found_uses_class_name_head() -> None:
    err = MetricNotFoundError(message="metric 'revenue' is unknown")

    assert str(err).startswith("MetricNotFoundError: metric 'revenue' is unknown")


def test_metric_not_found_renders_repair_with_candidates() -> None:
    repair = AnalysisRepair(
        kind="retry",
        action="Use a registered metric id from the catalog.",
        help_target=LiveHelpTarget(surface="analysis", canonical_id="observe"),
        snippet=(
            "import marivo.semantic as ms\n"
            "catalog = ms.load()\n"
            "catalog.metrics.show()\n"
            'session.observe(catalog.get("metric.<registered_metric_id>"), '
            'time_scope={"start": "2026-07-01", "end": "2026-10-01"})'
        ),
        candidates=("sales.revenue", "sales.orders"),
    )
    err = MetricNotFoundError(
        message="metric not found",
        expected="registered metric semantic object",
        received="revenu",
        location="session.observe call",
        repair=repair,
    )

    rendered = str(err)

    assert "Received: revenu" in rendered
    assert "Repair:" in rendered
    assert "  Use a registered metric id from the catalog." in rendered
    assert "  catalog.metrics.show()" in rendered
    assert "sales.revenue, sales.orders" in rendered
    assert "Help: mv.help('observe')" in rendered


def test_semantic_kind_mismatch_has_compare_fix_repair() -> None:
    repair = AnalysisRepair(
        kind="retry",
        action="Pass an observe result (MetricFrame) instead of a compare result (DeltaFrame).",
        help_target=LiveHelpTarget(surface="analysis", canonical_id="compare"),
        snippet=(
            'revenue = session.catalog.get("metric.sales.revenue")\n'
            'cur  = session.observe(revenue, time_scope={"start": "2026-07-01", "end": "2026-10-01"})\n'
            'base = session.observe(revenue, time_scope={"start": "2025-07-01", "end": "2025-10-01"})\n'
            "delta = session.compare(cur, base, alignment=mv.window_bucket())"
        ),
    )
    err = SemanticKindMismatchError(
        message="wrong frame kind",
        expected="metric_frame",
        received="delta_frame",
        location="session.compare call",
        repair=repair,
    )

    rendered = str(err)

    assert "Expected: metric_frame" in rendered
    assert "Received: delta_frame" in rendered
    assert "Repair:" in rendered
    assert 'revenue = session.catalog.get("metric.sales.revenue")' in rendered
    assert "  delta = session.compare(cur, base, alignment=mv.window_bucket())" in rendered
    assert "Help: mv.help('compare')" in rendered


def test_semantic_kind_mismatch_without_repair_is_bare() -> None:
    err = SemanticKindMismatchError(message="decompose requires a DeltaFrame input")

    rendered = str(err)

    assert "Repair:" not in rendered
    assert "Help:" not in rendered


def test_window_invalid_has_repair_snippet() -> None:
    repair = AnalysisRepair(
        kind="retry",
        action="Pass a parseable absolute time_scope.",
        help_target=LiveHelpTarget(surface="analysis", canonical_id="observe"),
        snippet=(
            'session.observe(session.catalog.get("metric.sales.revenue"), '
            'time_scope={"start": "2026-07-01", "end": "2026-10-01"})'
        ),
    )
    err = WindowInvalidError(
        message="window is invalid",
        received="last quarter",
        location="session.observe time_scope",
        repair=repair,
    )

    rendered = str(err)

    assert "Received: last quarter" in rendered
    assert "Repair:" in rendered
    assert (
        '  session.observe(session.catalog.get("metric.sales.revenue"), '
        'time_scope={"start": "2026-07-01", "end": "2026-10-01"})'
    ) in rendered


def test_axis_not_in_panel_dimensions_renders_paste_ready_fix_snippet() -> None:
    err = AxisNotInPanelDimensionsError(
        message="axis not in panel",
        context={"axis": "channel", "available_dimensions": ["region", "country"]},
    )

    rendered = str(err)

    assert "panel dimension column 'region'" in rendered
    assert 'axis = session.catalog.get("dimension.<domain.entity.dimension>").ref' in rendered
    assert "session.attribute(delta, axes=[axis])" in rendered
    assert "region, country" in rendered


def test_segment_dimension_mismatch_renders_cause_and_fix() -> None:
    from marivo.analysis.errors import SegmentDimensionMismatchError

    err = SegmentDimensionMismatchError(
        message="compare requires matching segment dimension columns",
        context={
            "current_dimensions": ["country"],
            "baseline_dimensions": ["region"],
        },
    )

    rendered = str(err)

    assert rendered.startswith("SegmentDimensionMismatchError:")
    assert "Extra in current: country" in rendered
    assert "Extra in baseline: region" in rendered
    assert "Repair:" in rendered


def test_segment_dimension_mismatch_shows_set_differences() -> None:
    from marivo.analysis.errors import SegmentDimensionMismatchError

    err = SegmentDimensionMismatchError(
        message="compare requires matching segment dimension columns",
        context={
            "current_dimensions": ["country", "channel"],
            "baseline_dimensions": ["country", "region"],
        },
    )

    rendered = str(err)

    assert "Extra in current: channel" in rendered
    assert "Extra in baseline: region" in rendered


def test_segment_dimension_mismatch_without_context_is_bare() -> None:
    from marivo.analysis.errors import SegmentDimensionMismatchError

    err = SegmentDimensionMismatchError(
        message="compare requires matching segment dimension columns",
    )

    rendered = str(err)

    assert rendered.startswith("SegmentDimensionMismatchError:")
    assert "Repair:" not in rendered


def test_no_backend_factory_renders_repair_snippet() -> None:
    from marivo.analysis.errors import NoBackendFactoryError

    err = NoBackendFactoryError(
        message="datasource 'tiny_orders' did not resolve to an ibis backend.",
        context={"datasource": "tiny_orders"},
    )
    rendered = str(err)
    assert "Repair:" in rendered
    assert "datasource='tiny_orders' resolved to None or a non-ibis object" in rendered
    assert "md.register" in rendered
    assert "@ms.datasource" not in rendered
    assert "Help: mv.help('datasources')" in rendered


def test_no_backend_factory_without_context_uses_session_backend_template() -> None:
    from marivo.analysis.errors import NoBackendFactoryError

    err = NoBackendFactoryError(
        message="session has no backend_factory; data-materializing intents need one",
        hint=(
            "Register a project datasource and call mv.session.get_or_create(name=...), "
            "or pass backends={...}/backend_factory=... only for explicit overrides."
        ),
    )

    rendered = str(err)

    assert "datasource=None" not in rendered
    assert "@ms.datasource" not in rendered
    assert "returned None or a non-ibis object" not in rendered
    assert "Session has no backend factory configured" in rendered
    assert "Repair:" in rendered
    assert "mv.session.get_or_create" in rendered
    assert "md.register" in rendered
    assert "backend_factory=" in rendered
    assert "Help: mv.help('datasources')" in rendered
