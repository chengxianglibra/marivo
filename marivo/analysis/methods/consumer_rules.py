"""Closed physical rules shared by declaration and execution consumers."""

from collections.abc import Mapping
from types import MappingProxyType

from marivo.analysis.methods.physical import (
    Backend,
    FixedShape,
    Implementation,
    NoTime,
    Qualified,
    ScalarType,
    SourceShape,
    TimeShape,
)

NATIVE_DISTRIBUTION_BACKENDS: Mapping[str, tuple[Backend, ...]] = MappingProxyType(
    {
        "metric.distinct": ("sqlite", "postgres", "mysql", "trino"),
        "metric.approx_distinct": ("sqlite", "postgres", "mysql", "trino", "clickhouse"),
        "metric.quantile": ("postgres",),
        "metric.approx_quantile": ("postgres", "trino", "clickhouse"),
    }
)


def prepared_numeric(implementation: Implementation) -> bool:
    """Recognize local consumers of the governed prepared observation envelope."""
    key = implementation.key
    shape = key.shape
    status = implementation.qualification
    if (
        not isinstance(status, Qualified)
        or key.method.version != 1
        or key.route != "ibis_python"
        or not isinstance(shape, SourceShape)
        or shape.form != "table"
        or shape.table_kind != "native"
        or shape.time != TimeShape("instant", "us", "UTC")
        or "subject" not in implementation.parts
    ):
        return False
    if shape.backend == "duckdb" and key.method.name != "state_rollup.sum_zero":
        return False
    if key.method.name in ("state_rollup.sum_zero", "state_rollup.mean"):
        return (
            status.consumer_id == "analysis.materialization.graph_local_execution"
            and key.input_domains in (("entity",), ("group",))
            and key.input_types
            == (ScalarType("int64" if key.method.name == "state_rollup.sum_zero" else "float64"),)
            and {"original_state", "coverage"}.issubset(implementation.parts)
        )
    if key.method.name == "cell.difference":
        return (
            status.consumer_id == "analysis.materialization.graph_local_execution"
            and key.input_domains == ("singleton",) * 2
            and key.input_types in ((ScalarType("int64"),) * 2, (ScalarType("float64"),) * 2)
            and {"current_endpoint", "baseline_endpoint", "correspondence"}.issubset(
                implementation.parts
            )
        )
    return (
        key.method.name in ("attribution.additive_difference", "attribution.component_mix")
        and status.consumer_id == "analysis.materialization.graph_attribution"
        and key.input_domains == ("singleton",) * 2
        and key.input_types
        == (
            ScalarType(
                "int64" if key.method.name == "attribution.additive_difference" else "float64"
            ),
        )
        * 2
        and {"basis", "allocation", "reconciliation"}.issubset(implementation.parts)
    )


def subject_image(implementation: Implementation) -> bool:
    """Recognize the real total Subject projection, independently of provenance IDs."""
    key = implementation.key
    status = implementation.qualification
    shape = key.shape
    time = shape.time
    supported_shape = (
        isinstance(shape, FixedShape)
        and (key.input_domains == ("group",) or not isinstance(time, NoTime))
    ) or (
        isinstance(shape, SourceShape)
        and shape.backend == "duckdb"
        and (shape.form, shape.table_kind) in (("table", "native"), ("parquet", "parquet"))
        and (shape.form == "parquet" or isinstance(time, NoTime) or time.unit == "us")
    )
    return (
        isinstance(status, Qualified)
        and status.consumer_id == "analysis.materialization.graph_local_execution"
        and key.method.name == "map_correspond"
        and key.method.version == 1
        and key.input_domains in (("entity",), ("group",))
        and key.route in ("ibis_python", "artifact_python")
        and implementation.parts == ("subject",)
        and supported_shape
        and (
            isinstance(time, NoTime)
            or (
                time.kind == "instant"
                and time.unit in ("s", "ms", "us", "ns")
                and time.timezone in ("UTC", "America/New_York")
            )
        )
    )


def native_distribution(implementation: Implementation) -> bool:
    """Recognize bounded direct-column distributions on the admitted native backends."""
    key = implementation.key
    shape = key.shape
    status = implementation.qualification
    return (
        isinstance(status, Qualified)
        and key.method.version == 1
        and status.consumer_id == "analysis.compiler.graph_lowering"
        and isinstance(shape, SourceShape)
        and shape.backend in NATIVE_DISTRIBUTION_BACKENDS.get(key.method.name, ())
        and shape
        == SourceShape(shape.backend, "table", "native", TimeShape("instant", "us", "UTC"))
        and key.route == "ibis"
        and key.input_types == (ScalarType("string"),)
        and key.input_domains == ("entity",)
        and {"original_state", "coverage"}.issubset(implementation.parts)
    )
