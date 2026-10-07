"""Exact funnel placements in the existing governed preparation/local pipeline."""

from dataclasses import replace

from marivo.analysis.core.model import DomainKind
from marivo.analysis.core.rules import FunnelAxesPrepare, FunnelReduce, RuleParameters
from marivo.analysis.methods.domain_preparation import implementations as preparations
from marivo.analysis.methods.domain_preparation import (
    remote_implementations,
    sqlite_implementations,
)
from marivo.analysis.methods.errors import reject
from marivo.analysis.methods.physical import (
    FixedShape,
    Implementation,
    NoTime,
    Qualified,
    ScalarName,
    ScalarType,
    SourceShape,
)
from marivo.analysis.methods.semantics import MethodKey
from marivo.semantic.ir import TargetEntityContract, TargetSnapshotVersion, TargetValidityVersion


def _date_version(entity: TargetEntityContract) -> bool:
    version = entity.version
    if version is None:
        return True
    columns = dict(entity.columns)
    if isinstance(version, TargetSnapshotVersion):
        return columns.get(version.source_column) == "date32[day]" and version.timezone == "UTC"
    return (
        isinstance(version, TargetValidityVersion)
        and version.interval == "closed_open"
        and version.open_end == (None,)
        and version.timezone == "UTC"
        and all(
            columns.get(column) == "date32[day]"
            for column in (version.valid_from_column, version.valid_to_column)
        )
    )


def implementations(method: MethodKey) -> tuple[Implementation, ...]:
    bases = tuple(
        i
        for i in preparations(MethodKey("occurrence.prepare"))
        + (
            sqlite_implementations(MethodKey("occurrence.prepare"))
            if method.name in ("funnel.entry_axes", "funnel.reduce")
            else ()
        )
        + (
            remote_implementations(MethodKey("occurrence.prepare"))
            if method.name in ("funnel.entry_axes", "funnel.reduce")
            else ()
        )
        if i.key.input_types == (ScalarType("int64"),)
    )
    fixed = next(i for i in bases if isinstance(i.key.shape, FixedShape))
    bases = (*bases, replace(fixed, key=replace(fixed.key, shape=FixedShape(NoTime()))))
    contracts: dict[str, tuple[tuple[tuple[ScalarName, ...], tuple[DomainKind, ...]], ...]] = {
        "funnel.entry_axes": ((("int64",), ("occurrence",)),),
        "funnel.reduce": (
            (("int64",), ("journey",)),
            (("int64", "int64"), ("journey", "occurrence")),
        ),
        "funnel.compare": ((("int64", "int64"), ("group", "group")),),
        "funnel.read": ((("int64",), ("group",)), (("float64",), ("group",))),
        "funnel_ratio_mix": ((("float64", "float64"), ("group", "group")),),
    }
    return tuple(
        replace(
            base,
            key=replace(
                base.key,
                method=method,
                input_types=tuple(ScalarType(t) for t in types),
                input_domains=domains,
                route="artifact_python"
                if isinstance(base.key.shape, FixedShape)
                else "ibis"
                if method.name == "funnel.entry_axes"
                else "ibis_python",
            ),
            parts=("entry_axes",)
            if method.name == "funnel.entry_axes"
            else ("funnel_state", "finding_policy"),
            numeric_specialization="consumer"
            if isinstance(base.key.shape, SourceShape)
            and base.key.shape.backend in ("postgres", "mysql", "trino", "clickhouse")
            else "exact"
            if isinstance(base.key.shape, SourceShape) and base.key.shape.backend == "sqlite"
            else "consumer",
            qualification=Qualified(
                f"r94.{base.key.shape.backend}.{method.name}.int64_us_utc@v1"
                if isinstance(base.key.shape, SourceShape)
                and base.key.shape.backend in ("postgres", "mysql", "trino", "clickhouse")
                else f"r93.c12.sqlite.{method.name}.int64_us_utc@v1"
                if isinstance(base.key.shape, SourceShape) and base.key.shape.backend == "sqlite"
                else f"r7.{method.name}.{'artifact_python' if isinstance(base.key.shape, FixedShape) else 'ibis' if method.name == 'funnel.entry_axes' else 'ibis_python'}@v1",
                "analysis.materialization.funnel_execution",
                "tests/test_r94_native_funnel_recovery.py"
                if isinstance(base.key.shape, SourceShape)
                and base.key.shape.backend in ("postgres", "mysql", "trino", "clickhouse")
                else "tests/test_r93_journey_consumers.py"
                if isinstance(base.key.shape, SourceShape) and base.key.shape.backend == "sqlite"
                else "tests/test_analysis_funnel_r74.py",
            ),
        )
        for base in bases
        for types, domains in contracts[method.name]
        if method.name != "funnel.entry_axes" or not isinstance(base.key.shape, FixedShape)
        if not (
            isinstance(base.key.shape, SourceShape)
            and base.key.shape.backend != "duckdb"
            and method.name == "funnel.reduce"
            and len(types) != 2
        )
    )


def admit_axes(implementation: Implementation, params: RuleParameters) -> None:
    """Admit only the consumer's supported SQLite parameter variants."""
    if (
        isinstance(implementation.key.shape, SourceShape)
        and implementation.key.shape.backend == "sqlite"
        and implementation.key.method.name in ("funnel.entry_axes", "funnel.reduce")
        and isinstance(implementation.qualification, Qualified)
        and implementation.qualification.consumer_id == "analysis.materialization.funnel_execution"
        and isinstance(params, (FunnelAxesPrepare, FunnelReduce))
        and (
            not params.axes
            or any(
                axis.subject.version is not None
                or axis.dimension.logical_type not in ("int64", "string")
                or any(not _date_version(entity) for entity in axis.entities)
                for axis in params.axes
            )
        )
    ):
        reject(
            "nonempty string/int64 axes from an unversioned Subject through to-one UTC DATE history paths",
            repr(params),
            "Use unique ordered direct or historical string/int64 axes. Keep every versioned path Entity on UTC DATE snapshots or closed-open validity with NULL open end, and the Subject unversioned.",
        )
