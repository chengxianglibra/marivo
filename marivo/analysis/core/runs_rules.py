"""Bound complete-grid runs and owned interval projections."""

from dataclasses import replace
from hashlib import sha256

from pydantic import TypeAdapter

from marivo.analysis.core.model import (
    ConditionCellsPart,
    Coordinate,
    DerivedQuantity,
    DomainSignature,
    FindingPolicyPart,
    GridCellsPart,
    Part,
    RunCellsPart,
    Signature,
    SubjectPart,
    require_part,
)
from marivo.analysis.core.predicates import ValuePredicate
from marivo.analysis.core.rules import RuleDerivation, TimeRunRead, TimeRuns, _binding, _result
from marivo.analysis.methods.errors import reject


def derive(inputs: tuple[Signature, ...], params: TimeRuns | TimeRunRead) -> RuleDerivation:
    _binding(inputs, "analysis.runs")
    source = inputs[0]
    parts: tuple[Part, ...]
    if isinstance(params, TimeRunRead):
        retained = require_part(source, "run_cells")
        assert isinstance(retained, RunCellsPart)
        parts = tuple(
            replace(p, view=params.field) if isinstance(p, RunCellsPart) else p
            for p in source.parts
        )
        return _result(
            "time_runs@v1",
            inputs,
            source.domain,
            DerivedQuantity(
                retained.run_id + ":" + params.field,
                "time.runs_read@v1",
                (retained.run_id,),
                None,
                source.quantity.time_scope if source.quantity else "runs",
                "input_owned",
            ),
            parts,
            pre=(),
            required=("condition_cells", "run_cells"),
            created=(),
            post=(),
            obligations=(),
            eval_id="time.runs_read@v1",
        )
    grid = source.domain.time_grid
    if grid is None or not source.domain.instance_key or any(cell.partial for cell in grid.cells):
        reject(
            "original complete, non-partial time grid",
            repr(source.domain),
            "Construct runs on the original complete time observation.",
        )
    if any(s.domain != source.domain for s in inputs[1:]):
        reject(
            "exactly corresponding predicate domains",
            repr(inputs),
            "Bind every condition field on the receiver domain.",
        )
    if any(p.role == "anchor" for p in source.domain.instance_key) is False:
        reject(
            "original grid cell coordinate",
            repr(source.domain),
            "Use the original time observation.",
        )
    owner = source.domain.instance_key[0].entity_ref
    key = (Coordinate(owner, "run:" + params.run_id, "instance"),)
    domain = DomainSignature(source.domain.binding, "group", key, key, params.run_id)
    parts = (
        ConditionCellsPart(
            source.domain.binding,
            source.domain,
            params.run_id,
            sha256(TypeAdapter(ValuePredicate).dump_json(params.predicate)).hexdigest(),
        ),
        RunCellsPart(source.domain.binding, params.run_id),
        GridCellsPart(source.domain.binding, source.domain, params.run_id),
        FindingPolicyPart(
            source.domain.binding, "time.runs", "graph.no_findings@v1", "zero_findings@v1"
        ),
    )
    subject = next((p for p in source.parts if isinstance(p, SubjectPart)), None)
    if subject is not None:
        parts += (replace(subject, source_key=key, injective=False),)
    return _result(
        "time_runs@v1",
        inputs,
        domain,
        DerivedQuantity(
            params.run_id + ":count",
            "time.runs@v1",
            (source.quantity.definition_id,) if source.quantity else (),
            None,
            source.quantity.time_scope if source.quantity else "runs",
            "input_owned",
        ),
        parts,
        pre=(),
        required=(),
        created=(
            "condition_cells",
            "run_cells",
            "grid_cells",
            "finding_policy",
            *(("subject",) if subject is not None else ()),
        ),
        post=(),
        obligations=(),
        eval_id="time.runs@v1",
    )
