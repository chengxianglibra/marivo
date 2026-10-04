"""The sole deviation derivation and retained fit declaration owner."""

from __future__ import annotations

from dataclasses import replace
from typing import Literal

from marivo.analysis.core.model import (
    DerivedQuantity,
    FindingPolicyPart,
    FitInputsPart,
    FitStatePart,
    GridCellsPart,
    PairInputsPart,
    Part,
    Quantity,
    Signature,
    SubjectMapPart,
    SubjectPart,
    TableFitsPart,
    TrainingInputsPart,
    require_part,
)
from marivo.analysis.core.rules import (
    DeviationFit,
    DeviationRead,
    RuleDerivation,
    _binding,
    _result,
)
from marivo.analysis.methods.errors import reject


def owned_view(
    signature: Signature,
) -> Literal["observed", "reference", "deviation", "score"] | None:
    fit = next((p for p in signature.parts if isinstance(p, FitInputsPart)), None)
    if fit is None or signature.quantity is None:
        return None
    field = "score" if fit.view == "result" else fit.view
    if field == "observed":
        return field if signature.quantity == fit.input_quantity else None
    return (
        field
        if signature.quantity.definition_id == fit.fit_id + ":" + field
        and signature.quantity.method_version
        in ("deviation.read@v1", "deviation.zscore@v1", "deviation.mad@v1")
        else None
    )


def validate(
    part: FitInputsPart | FitStatePart | GridCellsPart | SubjectMapPart | TableFitsPart,
) -> None:
    if isinstance(part, TableFitsPart):
        indices = tuple(column.index for column in part.columns)
        if (
            part.version != "v1"
            or not indices
            or indices != tuple(sorted(set(indices)))
            or any(
                column.index < 0
                or column.signature.domain.binding != part.binding
                or not any(
                    isinstance(p, (FitInputsPart, PairInputsPart, TrainingInputsPart))
                    for p in column.signature.parts
                )
                for column in part.columns
            )
        ):
            reject(
                "ordered fitted table columns with their original bound signatures",
                repr(part),
                "Reconstruct the table from its original typed columns.",
            )
        return
    if not part.fit_id or part.version != "v1":
        reject(
            "an exact versioned fit identity",
            repr(part),
            "Reconstruct the original deviation node.",
        )
    if isinstance(part, FitInputsPart) and (
        part.method not in ("zscore", "mad")
        or part.view not in ("result", "observed", "reference", "deviation", "score")
        or part.binding != part.input_domain.binding
    ):
        reject(
            "the original bound fit scope and owned field",
            repr(part),
            "Use the original fit input.",
        )


def derive(inputs: tuple[Signature, ...], params: DeviationFit | DeviationRead) -> RuleDerivation:
    _binding(inputs, "analysis.deviation")
    source = inputs[0]
    if source.quantity is None:
        reject("one numeric quantity", "a domain", "Observe a numeric quantity before deviation.")
    if isinstance(params, DeviationFit):
        if any(item.domain != source.domain for item in inputs[1:]):
            reject(
                "exactly corresponding categorical partitions",
                "different domains",
                "Read categories over the receiver's exact domain.",
            )
        if any(item.quantity is not None for item in inputs[1:]):
            reject(
                "categorical partitions",
                "numeric quantities",
                "Use corresponding CategoryRelation inputs.",
            )
        original = FitInputsPart(
            source.domain.binding,
            source.domain,
            source.quantity,
            params.input_type,
            params.method,
            params.fit_id,
            tuple(item.domain.definition_id for item in inputs[1:]),
            original_parts=source.parts,
        )
        state = FitStatePart(source.domain.binding, params.fit_id)
        quantity: Quantity = DerivedQuantity(
            params.fit_id + ":score",
            "deviation." + params.method + "@v1",
            (source.quantity.definition_id,),
            None,
            source.quantity.time_scope,
            "input_owned",
        )
        parts: tuple[Part, ...] = (
            *tuple(p for p in source.parts if isinstance(p, SubjectPart)),
            original,
            state,
            FindingPolicyPart(
                source.domain.binding,
                "deviation.zscore" if params.method == "zscore" else "deviation.mad",
                "graph.no_findings@v1",
                "zero_findings@v1",
            ),
        )
        if source.domain.time_grid is not None:
            parts = (*parts, GridCellsPart(source.domain.binding, source.domain, params.fit_id))
        parts = (
            *parts,
            *(
                SubjectMapPart(source.domain.binding, p, params.fit_id)
                for p in source.parts
                if isinstance(p, SubjectPart)
            ),
        )
        return _result(
            "deviation@v1",
            inputs,
            source.domain,
            quantity,
            parts,
            pre=(),
            required=(),
            created=("fit_inputs", "fit_state", "finding_policy"),
            post=(),
            obligations=(),
            eval_id="deviation." + params.method + "@v1",
        )
    retained = require_part(source, "fit_inputs")
    assert isinstance(retained, FitInputsPart)
    original = retained
    quantity = (
        original.input_quantity
        if params.field == "observed"
        else DerivedQuantity(
            original.fit_id + ":" + params.field,
            "deviation.read@v1",
            (original.input_quantity.definition_id,),
            None if params.field == "score" else original.input_quantity.unit,
            original.input_quantity.time_scope,
            "input_owned",
        )
    )
    parts = tuple(
        p
        for p in source.parts
        if isinstance(
            p, (FitInputsPart, FitStatePart, FindingPolicyPart, GridCellsPart, SubjectMapPart)
        )
    )
    parts = tuple(
        replace(p, view=params.field) if isinstance(p, FitInputsPart) else p for p in parts
    )
    if params.field == "observed":
        parts = (
            tuple(
                p
                for p in original.original_parts
                if not isinstance(
                    p,
                    (FitInputsPart, FitStatePart, FindingPolicyPart, GridCellsPart, SubjectMapPart),
                )
            )
            + parts
        )
    return _result(
        "deviation@v1",
        inputs,
        source.domain,
        quantity,
        parts,
        pre=(),
        required=("fit_inputs", "fit_state"),
        created=(),
        post=(),
        obligations=(),
        eval_id="deviation.read@v1",
    )
