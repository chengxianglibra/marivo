"""Exact local Lifecycle replay qualification after governed input preparation."""

from dataclasses import replace

from marivo.analysis.methods.domain_preparation import implementations as preparations
from marivo.analysis.methods.domain_preparation import (
    remote_implementations,
    sqlite_implementations,
)
from marivo.analysis.methods.physical import (
    FixedShape,
    Implementation,
    Qualified,
    ScalarType,
    SourceShape,
)
from marivo.analysis.methods.semantics import MethodKey


def implementations(method: MethodKey) -> tuple[Implementation, ...]:
    return tuple(
        replace(
            item,
            key=replace(
                item.key,
                method=method,
                input_types=(subject_type, ScalarType("int64")),
                input_domains=("entity", "occurrence"),
                route="artifact_python"
                if isinstance(item.key.shape, FixedShape)
                else "ibis_python",
            ),
            parts=("history", "subject", "occurrences"),
            numeric_specialization="exact"
            if isinstance(item.key.shape, SourceShape) and item.key.shape.backend == "sqlite"
            else "consumer",
            qualification=Qualified(
                "r93.c13.sqlite.history_replay_int64_us_utc@v1"
                if isinstance(item.key.shape, SourceShape) and item.key.shape.backend == "sqlite"
                else f"r7.history.replay.{item.key.route if isinstance(item.key.shape, FixedShape) else 'ibis_python'}@v1",
                "analysis.materialization.history_execution",
                "tests/test_r93_lifecycle_consumers.py"
                if isinstance(item.key.shape, SourceShape) and item.key.shape.backend == "sqlite"
                else "tests/test_analysis_lifecycle_r75.py",
            ),
        )
        for item in preparations(MethodKey("occurrence.prepare"))
        + sqlite_implementations(MethodKey("occurrence.prepare"))
        + remote_implementations(MethodKey("occurrence.prepare"))
        if item.key.input_types == (ScalarType("int64"),)
        for subject_type in (
            (ScalarType("int64"),)
            if isinstance(item.key.shape, SourceShape) and item.key.shape.backend != "duckdb"
            else (ScalarType("int64"), ScalarType("string"))
        )
    )
