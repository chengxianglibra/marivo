"""Validate the executable selection slices emitted by L1(a) and L1(b)."""

from functools import partial
from itertools import pairwise

from marivo._definition.query import FilterValues, ValueBinary, ValueExpression, ValueLiteral
from marivo.analysis.compiler.query_program import FrameProgram, ResultProgram
from marivo.analysis.compiler.query_stages import (
    FrameCompute,
    LocalCompute,
    QueryPlan,
    checked_output,
)
from tests.formal._smt import prove_unsat
from tests.formal.verify_query_rewrites import (
    _filters,
    _frame_filter,
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


def validate_short_circuit(original: QueryPlan, rewritten: QueryPlan) -> None:
    before = frame_chain(original)
    after = frame_chain(rewritten)
    assert len(before) in (2, 3)
    (stage,) = (stage for stage in after if stage.filter_fusion is not None)
    fusion = stage.filter_fusion
    assert fusion is not None
    first, second = fusion.fallback
    expanded = tuple(
        step for output in after for step in ((output,) if output is not stage else fusion.fallback)
    )
    assert expanded == before
    assert stage.inputs == first.inputs and second.inputs == (first.stage_id,)
    program, emitted = _frame_filter(stage)
    assert isinstance(emitted, ValueBinary) and emitted.operation == "and"
    row, bindings = _symbolic_frame_row(program.input_schema)
    encode = partial(_frame_operand, program.input_schema)

    projected_slots = set(fusion.second_columns)
    assert len(projected_slots) == len(fusion.second_columns), "Duplicate right projection columns"
    right_slots: set[str] = set()

    def projected_operand(expression: ValueExpression) -> tuple[str, str]:
        name, missing = encode(expression)
        if not isinstance(expression, ValueLiteral):
            assert name in projected_slots, f"Projected selection is missing right operand {name}"
            right_slots.add(name)
        return name, missing

    preceding = []
    for step in before:
        if step.stage_id == first.stage_id:
            break
        preceding.append(f"(= {_kleene(_frame_filter(step)[1], encode)} 1)")
    prefix = f"(and true {' '.join(preceding)})" if preceding else "true"
    first_term = _kleene(_frame_filter(first)[1], encode)
    second_term = _kleene(_frame_filter(second)[1], encode)
    after_left = _kleene(emitted.left, encode)
    after_right = _kleene(emitted.right, projected_operand)
    assert right_slots == projected_slots
    before_terms = " ".join(f"(= {_kleene(_frame_filter(item)[1], encode)} 1)" for item in before)
    after_terms = " ".join(f"(= {_kleene(_frame_filter(item)[1], encode)} 1)" for item in after)
    prove_unsat(
        f"""(set-logic QF_LIA)
{row}
(define-fun original_demand () Bool (and {prefix} (= {first_term} 1)))
(define-fun fused_demand () Bool (and {prefix} (= {after_left} 1)))
(define-fun original_rows () Bool (and {before_terms}))
(define-fun fused_rows () Bool (and {after_terms}))
(assert (or (distinct original_demand fused_demand)
            (and original_demand (distinct {second_term} {after_right}))
            (distinct original_rows fused_rows)))""",
        claim=f"L1(a) emitted masks, demand and rows for {len(before)} steps",
        witness=(*bindings, "original_demand", "fused_demand", "original_rows", "fused_rows"),
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
    assert len(before) in (2, 3)
    assert any(
        group.law == "L1" and group.output == rewritten.publish for group in rewritten.fusion_groups
    )
    assert any(group.law == "L1" for group in reordered.fusion_groups)
    row, bindings = _symbolic_frame_row(program.input_schema)
    encode = partial(_frame_operand, program.input_schema)
    sequence = " ".join(f"(= {_kleene(_predicate(stage), encode)} 1)" for stage in before)
    fused_term = _kleene(_predicate(fused), encode)
    reversed_term = _kleene(_predicate(reordered_fusion), encode)
    prove_unsat(
        f"""(set-logic QF_LIA)
{row}
(define-fun original_rows () Bool (and {sequence}))
(define-fun fused_rows () Bool (= {fused_term} 1))
(define-fun reordered_rows () Bool (= {reversed_term} 1))
(assert (or (distinct original_rows fused_rows) (distinct fused_rows reordered_rows)))""",
        claim=f"L1(b) emitted nullable selection and commutativity for {len(before)} steps",
        witness=(*bindings, "original_rows", "fused_rows", "reordered_rows"),
    )
