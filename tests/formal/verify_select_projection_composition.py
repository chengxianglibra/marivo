"""Bounded L5 witness for required failures in two and three public projections."""

from __future__ import annotations

import pytest

import marivo.analysis as mv
from marivo.analysis.compiler.frame_select_fusion import fuse_frame_selects
from marivo.analysis.compiler.query_stages import FrameCompute
from tests.formal._smt import prove_unsat
from tests.formal.verify_query_rewrites import _compile_fixed_frame
from tests.formal.verify_query_rewrites import filter_frame as filter_frame


# A distinct witness ID records the logical stage, evaluation phase and
# output position of a possible failure. An exact alias has neither failure.
def _first_failure(events: tuple[tuple[str, int], ...]) -> str:
    result = "0"
    for flag, witness in reversed(events):
        result = f"(ite {flag} {witness} {result})"
    return result


def _first_event_constraint(events: tuple[tuple[str, int], ...]) -> str:
    """Characterize the first present event without the serial stage fold."""
    prefix: list[str] = []
    cases: list[str] = []
    for flag, witness in events:
        cases.append(f"(and (= fused {witness}) {flag} {' '.join(prefix)})")
        prefix.append(f"(not {flag})")
    cases.append(f"(and (= fused 0) {' '.join(prefix)})")
    return "(assert (or " + " ".join(cases) + "))"


@pytest.mark.parametrize("length", (2, 3))
def test_l5_keeps_first_expression_or_carrier_failure_in_compiled_projection_chain(
    filter_frame: mv.AnalysisFrame, length: int
) -> None:
    """Prove preservation for arbitrary per-column errors on all non-alias outputs."""
    frame = filter_frame
    first = frame.select(
        {"keep": frame["x"] + 1, "risky": frame["y"] + 1, "ignored_alias": frame["y"]}
    )
    total_aliases: tuple[tuple[bool, ...], ...]
    if length == 2:
        query = first.select({"output": first["keep"] * 2})
        total_aliases = ((False, False, True), (False,))
    else:
        middle = first.select(
            {
                "keep": first["keep"],
                "risky": first["risky"] + 1,
                "ignored_alias": first["keep"],
            }
        )
        query = middle.select({"output": middle["keep"] * 2})
        total_aliases = ((False, False, True), (True, False, True), (False,))

    compiled = fuse_frame_selects(_compile_fixed_frame(query), protected_inputs=frozenset())
    (stage,) = (item for item in compiled.lowered.plan.stages if isinstance(item, FrameCompute))
    fusion = stage.select_fusion
    assert fusion is not None and len(fusion.steps) == length - 1
    positions = [
        frozenset(index for index, _ in (*step.live_values, *step.retained_check_expressions))
        for step in fusion.steps
    ]
    # The approved transform must elide the unused exact aliases. In the
    # three-step chain, the first risky value feeds only the middle check.
    assert positions == [frozenset((0, 1))] * (length - 1)
    if length == 3:
        assert tuple(index for index, _ in fusion.steps[0].live_values) == (0, 1)
        assert tuple(index for index, _ in fusion.steps[1].retained_check_expressions) == (1,)
    positions.append(frozenset((0,)))

    clauses: list[str] = ["(declare-const fused Int)"]
    symbols: list[str] = []
    baseline: list[str] = []
    fused_events: list[tuple[str, int]] = []
    for step, alias_totality in enumerate(total_aliases):
        events: list[tuple[str, int]] = []
        # Ordinary Select evaluates the entire output directory, then applies
        # its checked carrier normalizer in the same physical slot order.
        for phase in ("evaluate", "normalize"):
            for index, total in enumerate(alias_totality):
                symbol = f"{phase}_{step}_{index}"
                symbols.append(symbol)
                clauses.append(f"(declare-const {symbol} Bool)")
                if total:
                    clauses.append(f"(assert (not {symbol}))")
                event = (symbol, len(symbols))
                events.append(event)
                if index in positions[step]:
                    fused_events.append(event)
        identifier = f"stage_{step}"
        clauses.append(f"(define-fun {identifier} () Int {_first_failure(tuple(events))})")
        baseline.append(f"(not (= {identifier} 0))")
    serial = _first_failure(tuple(zip(baseline, range(1, length + 1), strict=True)))
    independent = "0"
    for index in reversed(range(length)):
        independent = f"(ite (= {serial} {index + 1}) stage_{index} {independent})"
    clauses.append(f"(define-fun baseline () Int {independent})")
    clauses.append(_first_event_constraint(tuple(fused_events)))
    clauses.append("(assert (distinct baseline fused))")
    prove_unsat(
        "\n".join(clauses),
        claim="L5 retains the first original stage, phase and output position of any failure",
        witness=(*symbols, "baseline", "fused"),
    )
