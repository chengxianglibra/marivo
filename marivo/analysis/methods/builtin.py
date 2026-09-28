"""Precisely bounded R3.4 consumers; declarations are not invocation evidence."""

from __future__ import annotations

from typing import Literal

from marivo.analysis.core.model import CheckId, DomainKind, PartRole
from marivo.analysis.core.rules import (
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


def implementations(method: MethodKey) -> tuple[Implementation, ...]:
    if method.name not in (
        "bind_project",
        "parts_transport",
        "map_correspond",
        "row.count",
        "row.count_defined",
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
                    CHECKS,
                    PARTS,
                    "checked_int64" if method.name.startswith("row.") else "exact",
                    ResourceRequirements("stream", "producer", None),
                    Qualified(
                        f"r34.ibis.{method}",
                        "analysis.compiler.graph_lowering",
                        "tests/test_analysis_lowering_r34.py",
                    ),
                )
            )
    if method.name == "row.count":
        declarations.append(
            Implementation(
                QualificationKey(
                    method,
                    (ScalarType("int64"),),
                    ("entity",),
                    FixedShape(NoTime()),
                    "artifact_python",
                ),
                (),
                ("row_state",),
                "checked_int64",
                ResourceRequirements("complete", "caller", 100_000),
                Qualified(
                    "r34.local.row.count@v1",
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
    elif not isinstance(params, RowState) or params.method not in ("count", "count_defined"):
        reject("a connected count state consumer", repr(params), "Use the registered exact method.")
