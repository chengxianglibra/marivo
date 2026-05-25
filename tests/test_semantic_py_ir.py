from __future__ import annotations

from marivo.semantic_py.errors import SemanticError
from marivo.semantic_py.ir import (
    DatasourceIR,
    DecompositionIR,
    MetricIR,
    MetricReferences,
    SourceLocation,
    SourceProvenance,
)


def test_package_imports() -> None:
    import marivo.semantic_py as ms

    assert {"ratio", "ref", "sum", "weighted_average"}.issubset(ms.__all__)


def test_ir_preserves_source_sql_provenance() -> None:
    location = SourceLocation(file="/tmp/semantic/sales/metrics.py", line=12)
    metric = MetricIR(
        name="revenue",
        model_name="sales",
        fn=lambda orders: orders.amount.sum(),
        decomposition=DecompositionIR(kind="sum"),
        description="Total paid revenue",
        ai_context={"synonyms": ["gmv"]},
        references=MetricReferences(datasets=["orders"], metrics=[], fields=[]),
        source_location=location,
        source=SourceProvenance(
            sql="sum(case when pay_status = 1 then pay_amount else 0 end)",
            dialect="trino",
            document="kb://revenue",
            notes="Official finance metric definition.",
        ),
    )

    assert metric.source is not None
    assert metric.source.dialect == "trino"
    assert metric.source.sql is not None
    assert "pay_status" in metric.source.sql


def test_datasource_ir_is_pure_identity() -> None:
    datasource = DatasourceIR(
        name="warehouse_main",
        backend_type="trino",
        description="Primary warehouse",
        ai_context=None,
        source_location=SourceLocation(file="/tmp/datasources.py", line=3),
    )

    assert datasource.name == "warehouse_main"
    assert datasource.backend_type == "trino"


def test_semantic_error_keyword_construction_initializes_exception_args() -> None:
    error = SemanticError(
        phase="decorator",
        kind="invalid_metric",
        location=SourceLocation(file="/tmp/semantic/sales/metrics.py", line=18),
        function="revenue",
        message="Metric expression is not callable.",
        hint="Use a function or lambda.",
        refs=["metric:revenue"],
    )

    rendered = str(error)

    assert error.short_form() == (
        "decorator:invalid_metric at /tmp/semantic/sales/metrics.py:18: "
        "Metric expression is not callable."
    )
    assert rendered.startswith("SemanticError: Metric expression is not callable.")
    assert "原因: decorator:invalid_metric" in rendered
    assert error.args == (rendered,)
