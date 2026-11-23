"""Read captured receipts for the fixed-state Attribute program."""

from collections.abc import Callable
from typing import Literal, NoReturn, TypeAlias

from marivo.analysis.core.frame_shapley_facts import FrameShapleyCapture
from marivo.analysis.methods.carriers.stages import StageOutput

FixedStateRoute: TypeAlias = Literal[
    "typed_sufficient_state/v1", "typed_value_frequency/v1", "typed_sufficient_frequency/v1"
]


def capture_facts(
    left: StageOutput, right: StageOutput, fail: Callable[[str], NoReturn]
) -> tuple[tuple[FrameShapleyCapture, ...], FixedStateRoute]:
    capture_directory = {
        capture.stage_id: capture
        for source in (left, right)
        for capture in source.attribute_state_captures
    }
    current_ids = {capture.stage_id for capture in left.attribute_state_captures}
    baseline_ids = {capture.stage_id for capture in right.attribute_state_captures}
    captured_facts = []
    for capture_id, receipt in sorted(capture_directory.items()):
        endpoint: Literal["current", "baseline", "both"] = (
            "both"
            if capture_id in current_ids and capture_id in baseline_ids
            else "current"
            if capture_id in current_ids
            else "baseline"
        )
        route: Literal["typed_sufficient_state/v1", "typed_value_frequency/v1"] = (
            "typed_value_frequency/v1"
            if receipt.state_route in ("typed_frequencies", "typed_membership")
            else "typed_sufficient_state/v1"
        )
        captured_facts.append(
            FrameShapleyCapture(
                capture_id,
                endpoint,
                receipt.provider,
                None,
                route,
                receipt.state_entries,
                receipt.state_bytes,
                receipt.returned_rows,
                receipt.decoded_arrow_bytes,
                1,
                0,
                0,
                None,
                None,
                None,
            )
        )
    if not captured_facts:
        fail("no actual fixed-state capture receipts")
    routes = {capture.state_route for capture in captured_facts}
    combined_route: FixedStateRoute = (
        next(iter(routes)) if len(routes) == 1 else "typed_sufficient_frequency/v1"
    )
    return tuple(captured_facts), combined_route
