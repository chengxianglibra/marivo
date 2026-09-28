"""Precisely bounded R3.4 consumers; declarations are not invocation evidence."""

from __future__ import annotations

from typing import Literal

from marivo.analysis.core.model import CheckId, DomainKind, PartRole
from marivo.analysis.core.rules import (
    AssociationScore,
    BindProject,
    MapCorrespond,
    PartsTransport,
    RowState,
    RuleParameters,
)
from marivo.analysis.methods.errors import reject
from marivo.analysis.methods.physical import (
    FixedShape,
    Implementation,
    NoTime,
    QualificationKey,
    Qualified,
    ResourceRequirements,
    ScalarType,
    SourceShape,
)
from marivo.analysis.methods.semantics import MethodKey

PARTS: tuple[PartRole, ...] = ("subject", "original_state", "row_state", "coverage")
CHECKS: tuple[CheckId, ...] = (
    "source.unique_key@v1",
    "source.exact_pairing@v1",
    "source.cell_policy@v1",
)
NUMERIC_CHECKS: tuple[CheckId, ...] = (*CHECKS, "source.finite_numeric@v1")


def implementations(method: MethodKey) -> tuple[Implementation, ...]:
    if method.name == "association.spearman":
        pair_checks: tuple[CheckId, ...] = (*NUMERIC_CHECKS,)
        pair_parts: tuple[PartRole, ...] = (*PARTS, "pair_counts")
        pair_declarations: list[Implementation] = []
        pair_shapes: tuple[tuple[Literal["table", "parquet"], str], ...] = (
            ("table", "native"),
            ("parquet", "parquet"),
        )
        scalar_names: tuple[Literal["int64", "float64"], ...] = ("int64", "float64")
        for form, table_kind in pair_shapes:
            shape = SourceShape("duckdb", form, table_kind, NoTime())
            for left in scalar_names:
                for right in scalar_names:
                    for route in ("ibis", "ibis_python"):
                        pair_declarations.append(
                            Implementation(
                                QualificationKey(
                                    method,
                                    (ScalarType(left), ScalarType(right)),
                                    ("entity", "entity"),
                                    shape,
                                    route,
                                ),
                                pair_checks,
                                pair_parts,
                                "finite_float64",
                                ResourceRequirements(
                                    "complete" if route == "ibis_python" else "stream",
                                    "producer",
                                    None,
                                ),
                                Qualified(
                                    f"r43.spearman.{route}.{left}.{right}@v1",
                                    "analysis.compiler.graph_lowering",
                                    "tests/test_analysis_lowering_r34.py",
                                ),
                            )
                        )
        for left in scalar_names:
            for right in scalar_names:
                pair_declarations.append(
                    Implementation(
                        QualificationKey(
                            method,
                            (ScalarType(left), ScalarType(right)),
                            ("entity", "entity"),
                            FixedShape(NoTime()),
                            "artifact_python",
                        ),
                        ("source.exact_pairing@v1", "source.finite_numeric@v1"),
                        pair_parts,
                        "finite_float64",
                        ResourceRequirements("complete", "caller", None),
                        Qualified(
                            f"r43.spearman.fixed.{left}.{right}@v1",
                            "analysis.methods.local",
                            "tests/test_analysis_lowering_r34.py",
                        ),
                    )
                )
        return tuple(pair_declarations)
    if method.name not in (
        "bind_project",
        "parts_transport",
        "map_correspond",
        "row.count",
        "row.count_defined",
        "row.sum",
        "row.mean",
    ):
        return ()
    declarations: list[Implementation] = []
    shapes: tuple[tuple[Literal["table", "parquet"], str], ...] = (
        ("table", "native"),
        ("parquet", "parquet"),
    )
    domain: DomainKind = "entity"
    for form, table_kind in shapes:
        shape = SourceShape("duckdb", form, table_kind, NoTime())
        for arity in (1, 2) if method.name == "map_correspond" else (1,):
            declarations.append(
                Implementation(
                    QualificationKey(
                        method, (ScalarType("int64"),) * arity, (domain,) * arity, shape, "ibis"
                    ),
                    NUMERIC_CHECKS if method.name in ("row.sum", "row.mean") else CHECKS,
                    PARTS,
                    "finite_float64"
                    if method.name == "row.mean"
                    else "checked_int64"
                    if method.name.startswith("row.")
                    else "exact",
                    ResourceRequirements("stream", "producer", None),
                    Qualified(
                        f"r34.ibis.{method}",
                        "analysis.compiler.graph_lowering",
                        "tests/test_analysis_lowering_r34.py",
                    ),
                )
            )
    if method.name in ("row.count", "row.count_defined", "row.sum", "row.mean"):
        declarations.append(
            Implementation(
                QualificationKey(
                    method,
                    (ScalarType("int64"),),
                    ("entity",),
                    FixedShape(NoTime()),
                    "artifact_python",
                ),
                ("source.cell_policy@v1",)
                if method.name == "row.count_defined"
                else NUMERIC_CHECKS
                if method.name not in ("row.count",)
                else (),
                ("row_state",),
                "finite_float64" if method.name == "row.mean" else "checked_int64",
                ResourceRequirements(
                    "complete", "caller", 100_000 if method.name == "row.count" else None
                ),
                Qualified(
                    "r34.local.row.count@v1"
                    if method.name == "row.count"
                    else f"r43.local.{method}@v1",
                    "analysis.methods.local",
                    "tests/test_analysis_lowering_r34.py",
                ),
            )
        )
    return tuple(declarations)


def admit(implementation: Implementation, params: RuleParameters) -> None:
    """Resolve a real consumer and reject parameter variants outside its evidence."""
    if implementation not in implementations(implementation.key.method):
        reject(
            "an implemented R3.4 consumer",
            repr(implementation.key),
            "Qualify a real lowerer and all required checkers for this exact key.",
        )
    if isinstance(params, BindProject):
        if (
            params.path
            or params.metric_contract is not None
            or params.field_contract is None
            or params.field_contract.parse is not None
            or params.field_contract.logical_type != "int64"
        ):
            reject(
                "direct int64 field binding without parsing",
                repr(params.ref),
                "Qualify the relationship, Metric or temporal lowering separately.",
            )
    elif isinstance(params, MapCorrespond):
        if params.mode not in ("exact_keys", "one_to_one", "union_keys", "subjects"):
            reject(
                "qualified complete-key correspondence",
                params.mode,
                "Qualify explicit grouping and its target domain separately.",
            )
    elif isinstance(params, PartsTransport):
        if params.mode not in ("where", "projection", "view") or (
            params.mode == "where" and not params.predicates
        ):
            reject(
                "projection, view, or where with explicit predicates",
                params.mode,
                "Bind the selection in the definition graph; materialization belongs to R4.",
            )
    elif isinstance(params, AssociationScore):
        if (
            params.pairing_check_id != "source.exact_pairing@v1"
            or params.numeric_check_id != "source.finite_numeric@v1"
        ):
            reject(
                "registered pairing and numeric checks",
                repr(params),
                "Use the exact Association checks.",
            )
    elif not isinstance(params, RowState) or params.method not in (
        "count",
        "count_defined",
        "sum",
        "mean",
    ):
        reject("a connected row state consumer", repr(params), "Use the registered exact method.")
