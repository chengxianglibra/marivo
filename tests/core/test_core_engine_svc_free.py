from __future__ import annotations

from marivo.core.engine import CoreEngine


def test_core_engine_no_svc_required() -> None:
    """After 4b-1, CoreEngine takes no svc argument."""
    engine = CoreEngine()
    assert engine is not None


def test_core_engine_pure_methods_work() -> None:
    """Pure computation methods work without svc."""
    engine = CoreEngine()
    assert engine.normalize_intent_metric_ref("test") == "metric.test"
    assert engine.metric_name_from_ref("metric.test") == "test"


def test_core_engine_has_no_io_methods() -> None:
    """After 4b-1, CoreEngine has no I/O proxy methods."""
    engine = CoreEngine()
    io_methods = [
        "resolve_metric_execution_context",
        "resolve_metric",
        "resolve_metric_table",
        "resolve_metric_dimensions",
        "resolve_metric_sql_for_execution",
        "resolve_metric_value_sql_for_execution",
        "resolve_scope_constraint_column",
        "compile_step",
        "resolve_windowed_query_time_axis",
        "build_scoped_query",
        "commit_artifact_with_extraction",
        "insert_step",
        "resolve_artifact_for_ref",
        "resolve_artifact_id_for_step",
        "resolve_artifact_with_id",
        "insert_artifact",
        "resolve_engine_for_session",
        "resolve_engine",
    ]
    for method_name in io_methods:
        assert not hasattr(engine, method_name), (
            f"CoreEngine should not have I/O method {method_name!r} after 4b-1"
        )
