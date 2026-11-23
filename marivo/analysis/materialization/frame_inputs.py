"""Capture exact Frame operands and sidecars for one evaluation invocation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from marivo._definition.query import FilterValues, SelectValues, TopValues
from marivo.analysis.core.frame_data import (
    ExpressionFrameBacking,
    FrameKeyDependency,
    OperationFrameBinding,
    SourceFrameBinding,
)
from marivo.analysis.core.frame_operations import (
    AlignFrameValues,
    AttributeContributionValues,
    AttributeFrameValues,
    AttributeGameValues,
    AttributeInputsValues,
    CompareFrameValues,
    FixedFrameInput,
    FixFrameValues,
    FrameExpression,
    FrameOperation,
    FrameOperationKind,
    SourceFrameInput,
)
from marivo.analysis.model.data_schema import DataSchema
from marivo.analysis.model.errors import reject
from marivo.analysis.model.frame_attribute_merge import AttributeMergeValues
from marivo.analysis.model.frame_contracts import (
    FrameKeyInput,
    frame_backing,
    frame_input_id,
    frame_local,
    frame_logical_schema,
    merge_frame_key_inputs,
)
from marivo.analysis.model.frame_metadata import (
    FrameOperationName,
)

if TYPE_CHECKING:
    from marivo.analysis.binding.attribute_rules import AttributeRule
    from marivo.analysis.core.frame_attribute_inputs import AttributeInputRequest
    from marivo.analysis.core.frame_data import FrameNode


@dataclass(frozen=True, slots=True)
class CapturedFrameInputs:
    """One invocation's exact inputs, expressions and ordered fact associations."""

    root: FrameExpression
    source_frames: dict[str, FrameNode]
    fixed_frames: dict[str, FrameNode]
    expressions: dict[str, FrameExpression]
    collected: dict[str, FrameKeyDependency]
    dependencies: dict[str, tuple[str, ...]]
    requested: dict[str, tuple[int, tuple[tuple[str, AttributeRule], ...], AttributeFrameValues]]
    operations: tuple[FrameOperationName, ...]
    games: tuple[str, ...] = ()


def _operation_name(operation: FrameOperationKind) -> FrameOperationName:
    return (
        "attribute"
        if isinstance(operation, (AttributeMergeValues, AttributeGameValues))
        else "align"
        if isinstance(operation, AlignFrameValues)
        else "compare"
        if isinstance(operation, CompareFrameValues)
        else "select"
        if isinstance(operation, SelectValues)
        else "where"
        if isinstance(operation, FilterValues)
        else "top"
        if isinstance(operation, TopValues)
        else "fix"
        if isinstance(operation, FixFrameValues)
        else "reduce"
    )


def capture_frame_inputs(
    frame: FrameNode, request: AttributeInputRequest | None = None
) -> CapturedFrameInputs:
    """Capture explicit operands and sidecars without opening or reading sources."""
    source_frames: dict[str, FrameNode] = {}
    fixed_frames: dict[str, FrameNode] = {}
    expressions: dict[str, FrameExpression] = {}
    collected: dict[str, FrameKeyDependency] = {}
    dependencies: dict[str, tuple[str, ...]] = {}
    operations: list[FrameOperationName] = []
    game_inputs: list[str] = []
    contribution_requests: dict[
        str, tuple[int, tuple[tuple[str, AttributeRule], ...], AttributeFrameValues]
    ] = {}

    def collect(current: FrameNode) -> FrameExpression:
        if frame_local(current).owner != frame_local(frame).owner:
            reject(
                "explicit inputs from one Store and Session",
                frame_input_id(current),
                "Use inputs owned by this Frame's Session.",
                "frame.compute",
            )
        previous = collected.get(frame_input_id(current))
        carrier = FrameKeyInput(frame_input_id(current), current)
        merge_frame_key_inputs(
            () if previous is None else (previous,), (carrier,), owner=frame_local(frame).owner
        )
        collected.setdefault(frame_input_id(current), carrier)
        existing = expressions.get(frame_input_id(current))
        if existing is not None:
            return existing
        backing = frame_backing(current)
        if isinstance(backing, ExpressionFrameBacking):
            root = backing.root
            if isinstance(root, OperationFrameBinding):
                dependencies[frame_input_id(current)] = tuple(
                    frame_input_id(child) for child in root.inputs
                )
                inputs = tuple(collect(child) for child in root.inputs)
                expression: FrameExpression = FrameOperation(
                    inputs, root.operation, frame_logical_schema(current)
                )
                operation = _operation_name(root.operation)
                if isinstance(root.operation, AttributeGameValues):
                    game_inputs.extend(frame_input_id(endpoint) for endpoint in root.inputs[1:])
                if isinstance(root.operation, AttributeContributionValues):
                    operation = "attribute"
                    for side, endpoint_frame in enumerate(root.inputs[1:]):
                        contribution_requests[frame_input_id(endpoint_frame)] = (
                            side
                            if root.operation.source_sides is None
                            else root.operation.source_sides[side],
                            (root.operation.source_rules or root.operation.rules)[side],
                            root.operation.mapping,
                        )
                operations.append(operation)
            else:
                dependencies[frame_input_id(current)] = (
                    tuple(item.input_id for item in root.frame_key_inputs)
                    if isinstance(root, SourceFrameBinding)
                    else ()
                )
                if isinstance(root, SourceFrameBinding):
                    for dependency in root.frame_key_inputs:
                        collect(dependency.frame)
                source_frames[frame_input_id(current)] = current
                expression = SourceFrameInput(frame_input_id(current), root.graph)
        else:
            schema = frame_logical_schema(current)
            assert isinstance(schema, DataSchema)
            dependencies[frame_input_id(current)] = ()
            fixed_frames[frame_input_id(current)] = current
            expression = FixedFrameInput(frame_input_id(current), schema)
        expressions[frame_input_id(current)] = expression
        return expression

    if request is None:
        root_expression = collect(frame)
    else:
        root_expression = FrameOperation(
            tuple(collect(item) for item in (request.parent, *request.endpoints)),
            AttributeInputsValues(request.operation),
        )
    requested = (
        {}
        if request is None
        else {
            frame_input_id(endpoint): (side, rules, request.operation)
            for side, (endpoint, rules) in enumerate(
                zip(request.endpoints, request.rules, strict=True)
            )
        }
    )
    for input_id, (side, rules, mapping) in contribution_requests.items():
        requested[input_id] = (side, rules, request.operation if request is not None else mapping)
    return CapturedFrameInputs(
        root_expression,
        source_frames,
        fixed_frames,
        expressions,
        collected,
        dependencies,
        requested,
        tuple(operations),
        tuple(dict.fromkeys(game_inputs)),
    )
