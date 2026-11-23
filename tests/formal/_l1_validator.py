"""Validate the executable selection slices emitted by L1(a) and L1(b)."""

from collections.abc import Callable
from functools import partial
from itertools import pairwise

from marivo._definition.query import CoordinateSlot, FilterValues, ValueExpression, ValueLiteral
from marivo.analysis.compiler.query_program import FrameProgram, ResultProgram
from marivo.analysis.compiler.query_stages import (
    FrameCompute,
    LocalCompute,
    QueryPlan,
    checked_output,
)
from marivo.analysis.core.frame_operations import FixFrameValues
from marivo.analysis.model.data_types import ScalarDataType
from tests.formal._smt import prove_unsat
from tests.formal.verify_query_rewrites import (
    _filters,
    _frame_operand,
    _kleene,
    _predicate,
    _symbolic_frame_row,
)


def frame_chain(plan: QueryPlan) -> tuple[FrameCompute, ...]:
    stages = {stage.stage_id: stage for stage in plan.stages}
    current = stages[plan.publish]
    steps = []
    while isinstance(current, FrameCompute):
        steps.append(current)
        (parent,) = current.inputs
        current = stages[parent]
    return tuple(reversed(steps))


def _projected_operand(
    expression: ValueExpression,
    *,
    encode: Callable[[ValueExpression], tuple[str, str]],
    projected: set[str],
    used: set[str],
) -> tuple[str, str]:
    name, missing = encode(expression)
    if not isinstance(expression, ValueLiteral):
        assert name in projected, f"Projected selection is missing operand {name}"
        used.add(name)
    return name, missing


def _selection_term(
    program: FrameProgram,
    operand: Callable[[ValueExpression], tuple[str, str]],
    floating: frozenset[str],
) -> str:
    operation = program.operation
    if isinstance(operation, FilterValues):
        return _kleene(operation.predicate, operand, floating=floating)
    assert isinstance(operation, FixFrameValues)
    (key,) = operation.key
    column, missing = operand(CoordinateSlot(operation.position))
    if key is None:
        return f"(ite {missing} 1 (- 1))"
    assert type(key) is int
    literal, _ = operand(ValueLiteral(key))
    return f"(ite (and (not {missing}) (= {column} {literal})) 1 (- 1))"


def validate_short_circuit(original: QueryPlan, rewritten: QueryPlan) -> None:
    before = frame_chain(original)
    after = frame_chain(rewritten)
    assert len(before) >= 2
    (stage,) = after
    fusion = stage.filter_fusion
    assert fusion is not None and before == fusion.fallback
    assert stage.inputs == before[0].inputs
    assert tuple(source.inputs for source in before[1:]) == tuple(
        (source.stage_id,) for source in before[:-1]
    )
    first = before[0].program
    assert isinstance(first, FrameProgram)
    row, bindings = _symbolic_frame_row(first.input_schema)
    original_terms: list[str] = []
    fused_terms: list[str] = []
    differences: list[str] = []

    for original_step, fused_step in zip(before, fusion.steps, strict=True):
        program = original_step.program
        assert isinstance(program, FrameProgram)
        assert program.input_schema == fused_step.program.input_schema
        encode = partial(_frame_operand, program.input_schema)
        projected_slots = set(fused_step.columns)
        assert len(projected_slots) == len(fused_step.columns), "Duplicate projection columns"
        used: set[str] = set()

        floating = frozenset(
            c.slot_id for c in program.input_schema.columns if c.dtype == ScalarDataType("float64")
        )
        expected = _selection_term(program, encode, floating)
        actual = _selection_term(
            fused_step.program,
            partial(_projected_operand, encode=encode, projected=projected_slots, used=used),
            floating,
        )
        assert used == projected_slots
        original_demand = f"(and true {' '.join(original_terms)})" if original_terms else "true"
        fused_demand = f"(and true {' '.join(fused_terms)})" if fused_terms else "true"
        differences.extend(
            (
                f"(distinct {original_demand} {fused_demand})",
                f"(and {original_demand} (distinct {expected} {actual}))",
            )
        )
        original_terms.append(f"(= {expected} 1)")
        fused_terms.append(f"(= {actual} 1)")
    prove_unsat(
        f"""(set-logic QF_LIRA)
{row}
(define-fun original_rows () Bool (and {" ".join(original_terms)}))
(define-fun fused_rows () Bool (and {" ".join(fused_terms)}))
(assert (or {" ".join(differences)} (distinct original_rows fused_rows)))""",
        claim=f"L1(a) emitted masks, demand and rows for {len(before)} steps",
        witness=(*bindings, "original_rows", "fused_rows"),
    )


def result_plan(program: FrameProgram, expressions: tuple[ValueExpression, ...]) -> QueryPlan:
    columns = program.input_schema.columns
    ids = ("input", *(f"selection_{index}" for index in range(len(expressions))))
    links = tuple(pairwise(ids))
    operations = tuple(
        LocalCompute(
            stage,
            (parent,),
            columns,
            program.keys,
            ResultProgram(
                FilterValues(expression),
                columns,
                program.keys,
                (),
                program.key_groups,
                program.key_groups,
            ),
        )
        for (parent, stage), expression in zip(links, expressions, strict=True)
    )
    return QueryPlan(
        (checked_output(ids[0], columns, program.keys), *operations),
        (*((parent, (stage,)) for parent, stage in links), (ids[-1], ())),
        ids[-1],
        program.key_groups,
    )


def validate_total_selection(
    program: FrameProgram, original: QueryPlan, rewritten: QueryPlan, reordered: QueryPlan
) -> None:
    before = _filters(original)
    (fused,) = _filters(rewritten)
    (reordered_fusion,) = _filters(reordered)
    assert len(before) >= 2
    assert any(
        group.law == "L1" and group.output == rewritten.publish for group in rewritten.fusion_groups
    )
    assert any(group.law == "L1" for group in reordered.fusion_groups)
    row, bindings = _symbolic_frame_row(program.input_schema)
    encode = partial(_frame_operand, program.input_schema)
    floating = frozenset(
        column.slot_id
        for column in program.input_schema.columns
        if column.dtype == ScalarDataType("float64")
    )
    sequence = " ".join(
        f"(= {_kleene(_predicate(stage), encode, floating=floating)} 1)" for stage in before
    )
    fused_term = _kleene(_predicate(fused), encode, floating=floating)
    reversed_term = _kleene(_predicate(reordered_fusion), encode, floating=floating)
    prove_unsat(
        f"""(set-logic QF_LIRA)
{row}
(define-fun original_rows () Bool (and {sequence}))
(define-fun fused_rows () Bool (= {fused_term} 1))
(define-fun reordered_rows () Bool (= {reversed_term} 1))
(assert (or (distinct original_rows fused_rows) (distinct fused_rows reordered_rows)))""",
        claim=f"L1(b) emitted nullable selection and commutativity for {len(before)} steps",
        witness=(*bindings, "original_rows", "fused_rows", "reordered_rows"),
    )
