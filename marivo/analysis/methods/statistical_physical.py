"""Exact ordered shape declarations for the connected statistical consumers."""

from dataclasses import replace

from marivo.analysis.methods.deviation_physical import implementations as numeric_implementations
from marivo.analysis.methods.physical import (
    DecimalType,
    Implementation,
    QualificationKey,
    Qualified,
    ScalarType,
    SourceShape,
    TimeShape,
)
from marivo.analysis.methods.semantics import MethodKey


def implementations(method: MethodKey) -> tuple[Implementation, ...]:
    association = method.name.startswith("association.") and method.name != "association.read"
    declarations = tuple(
        replace(
            item,
            key=replace(
                item.key,
                method=method,
                input_types=(item.key.input_types[0],) * (2 if association else 1),
                input_domains=(item.key.input_domains[0],) * (2 if association else 1),
            ),
            precision="exact" if method.name.endswith(".read") else "certified_statistical",
            qualification=Qualified(
                "r8-" + method.name.replace(".", "-") + "-" + item.key.route + "-v1",
                "analysis.materialization.statistical_execution",
                "tests/test_r93_duration_ratio_unknown.py"
                if item.key.input_domains[0] == "journey"
                else "tests/test_analysis_statistics_r84.py",
            ),
        )
        for item in numeric_implementations(MethodKey("deviation.read"))
        if item.key.input_domains[0] != "singleton"
        and (item.key.input_domains[0] != "journey" or association)
        and (not isinstance(item.key.shape, SourceShape) or item.key.shape.backend == "duckdb")
    )
    if method.name not in (
        "association.pearson",
        "association.spearman",
        "association.kendall",
        "association.read",
        "forecast.naive",
        "forecast.drift",
        "forecast.seasonal_naive",
        "forecast.read",
    ):
        return declarations
    sqlite = tuple(
        replace(
            item,
            key=replace(item.key, shape=replace(item.key.shape, backend="sqlite")),
            qualification=Qualified(
                "r93-sqlite-" + method.name + "-entity-int64-us-utc-v1",
                "analysis.materialization.statistical_execution",
                "tests/test_r93_method_consumers.py",
            ),
        )
        for item in declarations
        if isinstance(item.key.shape, SourceShape)
        and item.key.shape.backend == "duckdb"
        and item.key.shape.form == "table"
        and item.key.shape.time == TimeShape("instant", "us", "UTC")
        and item.key.input_domains[0]
        == (
            "group"
            if method.name == "association.read" or method.name.startswith("forecast.")
            else "entity"
        )
        and item.key.input_types[0] == ScalarType("int64")
    )
    postgres = tuple(
        replace(
            item,
            key=replace(item.key, shape=replace(item.key.shape, backend="postgres")),
            qualification=Qualified(
                "r93-postgres-" + method.name + "-v1",
                "analysis.materialization.statistical_execution",
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
                "r93-mysql-" + method.name + "-v1",
                "analysis.materialization.statistical_execution",
                "tests/test_r93_method_consumers.py",
            ),
        )
        for item in postgres
        if isinstance(item.key.shape, SourceShape)
    )
    trino = tuple(
        replace(
            item,
            key=replace(item.key, shape=replace(item.key.shape, backend="trino")),
            qualification=Qualified(
                "r93-trino-" + method.name + "-v1",
                "analysis.materialization.statistical_execution",
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
                "r93-clickhouse-" + method.name + "-v1",
                "analysis.materialization.statistical_execution",
                "tests/test_r93_method_consumers.py",
            ),
        )
        for item in trino
        if isinstance(item.key.shape, SourceShape)
    )
    return (*declarations, *sqlite, *postgres, *mysql, *trino, *clickhouse)


def specialize(implementation: Implementation, key: QualificationKey) -> Implementation:
    if isinstance(key.shape, SourceShape) and key.shape.backend != "duckdb":
        allowed = (
            ((ScalarType("float64"),), (ScalarType("boolean"),))
            if key.method.name == "association.read"
            else ((ScalarType("float64"),),)
            if key.method.name == "forecast.read"
            else ((ScalarType("int64"),),)
            if key.method.name.startswith("forecast.")
            else ((ScalarType("int64"),) * 2,)
        )
        if key.input_types not in allowed:
            return implementation
    if (
        implementation.key.shape != key.shape
        or implementation.key.route != key.route
        or implementation.key.method != key.method
        or key.input_domains[0] != implementation.key.input_domains[0]
        or any(d != key.input_domains[0] for d in key.input_domains)
    ):
        return implementation
    if any(
        t not in (ScalarType("int64"), ScalarType("float64"), ScalarType("boolean"))
        and not isinstance(t, DecimalType)
        for t in key.input_types
    ):
        return implementation
    if (
        key.method.name.startswith("association.")
        and key.method.name != "association.read"
        and not 2 <= len(key.input_types) <= 16
    ):
        return implementation
    if (key.method.name.startswith("forecast.") or key.method.name == "association.read") and len(
        key.input_types
    ) != 1:
        return implementation
    return replace(implementation, key=key)
