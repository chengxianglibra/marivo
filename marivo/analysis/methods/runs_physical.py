"""Exact physical declarations for complete-grid classification and run reads."""

from dataclasses import replace

from marivo.analysis.methods.deviation_physical import implementations as numeric_implementations
from marivo.analysis.methods.physical import (
    DecimalType,
    DurationType,
    Implementation,
    QualificationKey,
    Qualified,
    ScalarType,
    SourceShape,
    TimeShape,
    ValueType,
)
from marivo.analysis.methods.semantics import MethodKey


def output_type(field: str) -> ValueType:
    return (
        DurationType("us")
        if field == "duration"
        else ScalarType("timestamp")
        if field in ("start", "end")
        else ScalarType("int64")
    )


def implementations(method: MethodKey) -> tuple[Implementation, ...]:
    declarations = tuple(
        replace(
            item,
            key=replace(item.key, method=method),
            precision="exact",
            qualification=Qualified(
                "r8-" + method.name.replace(".", "-") + "-" + item.key.route + "-v1",
                "analysis.materialization.runs_execution",
                "tests/test_analysis_runs_r83.py",
            ),
        )
        for item in numeric_implementations(MethodKey("deviation.read"))
        if item.key.input_domains != ("journey",)
        if not isinstance(item.key.shape, SourceShape) or item.key.shape.backend == "duckdb"
    )
    sqlite = tuple(
        replace(
            item,
            key=replace(item.key, shape=replace(item.key.shape, backend="sqlite")),
            qualification=Qualified(
                "r93-sqlite-" + method.name + "-entity-int64-us-utc-v1",
                "analysis.materialization.runs_execution",
                "tests/test_r93_method_consumers.py",
            ),
        )
        for item in declarations
        if isinstance(item.key.shape, SourceShape)
        and item.key.shape.form == "table"
        and item.key.shape.time == TimeShape("instant", "us", "UTC")
        and item.key.input_domains == ("group" if method.name == "time.runs_read" else "entity",)
        and item.key.input_types == (ScalarType("int64"),)
    )
    trino = tuple(
        replace(
            item,
            key=replace(item.key, shape=replace(item.key.shape, backend="trino")),
            qualification=Qualified(
                "r93-trino-" + method.name + "-int64-us-utc-v1",
                "analysis.materialization.runs_execution",
                "tests/test_r93_method_consumers.py",
            ),
        )
        for item in sqlite
        if isinstance(item.key.shape, SourceShape)
    )
    postgres = tuple(
        replace(
            item,
            key=replace(item.key, shape=replace(item.key.shape, backend="postgres")),
            qualification=Qualified(
                "r93-postgres-" + method.name + "-int64-us-utc-v1",
                "analysis.materialization.runs_execution",
                "tests/test_r93_method_consumers.py",
            ),
        )
        for item in sqlite
        if isinstance(item.key.shape, SourceShape)
    )
    mysql = tuple(
        replace(
            item,
            key=replace(item.key, shape=replace(item.key.shape, backend="mysql")),
            qualification=Qualified(
                "r93-mysql-" + method.name + "-int64-us-utc-v1",
                "analysis.materialization.runs_execution",
                "tests/test_r93_method_consumers.py",
            ),
        )
        for item in sqlite
        if isinstance(item.key.shape, SourceShape)
    )
    clickhouse = tuple(
        replace(
            item,
            key=replace(item.key, shape=replace(item.key.shape, backend="clickhouse")),
            qualification=Qualified(
                "r93-clickhouse-" + method.name + "-int64-us-utc-v1",
                "analysis.materialization.runs_execution",
                "tests/test_r93_method_consumers.py",
            ),
        )
        for item in sqlite
        if isinstance(item.key.shape, SourceShape)
    )
    grouped = tuple(
        replace(
            item,
            key=replace(item.key, input_domains=("group",)),
            qualification=Qualified(
                "r94-" + item.key.shape.backend + "-runs-group-int64-us-utc-v1",
                "analysis.materialization.runs_execution",
                "tests/test_r94_statistical_recovery.py",
            ),
        )
        for item in (*sqlite, *trino, *postgres, *mysql, *clickhouse)
        if method.name == "time.runs"
        and isinstance(item.key.shape, SourceShape)
        and item.key.input_domains == ("entity",)
        and item.key.input_types == (ScalarType("int64"),)
        and item.key.shape.time == TimeShape("instant", "us", "UTC")
        and item.key.route == "ibis_python"
    )
    return (*declarations, *sqlite, *trino, *postgres, *mysql, *clickhouse, *grouped)


def specialize(implementation: Implementation, key: QualificationKey) -> Implementation:
    if (
        isinstance(key.shape, SourceShape)
        and key.shape.backend != "duckdb"
        and key.input_types != (ScalarType("int64"),)
    ):
        return implementation
    if (
        implementation.key.shape != key.shape
        or implementation.key.route != key.route
        or implementation.key.input_domains[0] != key.input_domains[0]
    ):
        return implementation
    allowed = (
        ScalarType("int64"),
        ScalarType("float64"),
        ScalarType("timestamp"),
        ScalarType("date"),
        ScalarType("boolean"),
        ScalarType("string"),
    )
    if any(
        t not in allowed and not isinstance(t, (DecimalType, DurationType)) for t in key.input_types
    ) or any(d != key.input_domains[0] for d in key.input_domains):
        return implementation
    return replace(implementation, key=key)
