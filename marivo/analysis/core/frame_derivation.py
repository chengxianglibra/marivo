"""Compact process-local declarations, independent of completed Frame backings."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal

from marivo._definition.query import FilterValues, SelectValues, TopValues, ValueBinary, ValueSlot
from marivo._definition.relations import ObserveQuery
from marivo.analysis.core.data_schema import DataSchema
from marivo.analysis.core.frame_data import OperationFrameBinding, SourceFrameBinding
from marivo.analysis.core.frame_expression import (
    AlignFrameValues,
    CompareFrameValues,
    NamedFrameComparison,
)

if TYPE_CHECKING:
    from marivo.analysis.core.frame_audit import FrameResultMetadata
    from marivo.analysis.core.frame_data import CompletedFrameData, FrameExpressionRoot
    from marivo.analysis.core.frame_structure import PendingDataSchema


@dataclass(frozen=True, slots=True)
class SourceMetric:
    binding: SourceFrameBinding
    path: tuple[str, ...]
    axis_positions: tuple[tuple[int, int], ...]


@dataclass(frozen=True, slots=True)
class DerivationTarget:
    mode: Literal["level", "absolute", "relative"]
    current: SourceMetric
    baseline: SourceMetric | None = None
    pairing: NamedFrameComparison | AlignFrameValues | None = None
    align_side: Literal["left", "right"] | None = None


@dataclass(frozen=True, slots=True)
class DerivedOutput:
    path: tuple[str, ...]
    target: DerivationTarget


@dataclass(frozen=True, slots=True)
class FrameDerivation:
    outputs: tuple[DerivedOutput, ...]


def _remap(metric: SourceMetric, positions: tuple[tuple[int, int], ...]) -> SourceMetric:
    mapping = dict(positions)
    return replace(metric, axis_positions=tuple((a, mapping[b]) for a, b in metric.axis_positions))


def derive_declaration(
    schema: DataSchema | PendingDataSchema, root: FrameExpressionRoot
) -> FrameDerivation | None:
    """Transport only explicitly supported source output recipes."""
    if isinstance(root, SourceFrameBinding):
        if not isinstance(root.graph.root, ObserveQuery):
            return None
        positions = tuple((i, i) for i in range(len(schema.axes)))
        return FrameDerivation(
            tuple(
                DerivedOutput(path, DerivationTarget("level", SourceMetric(root, path, positions)))
                for path in schema.value_paths
            )
        )
    if not isinstance(root, OperationFrameBinding):
        return None
    operation = root.operation
    inputs = root.inputs
    declarations = tuple(frame._derivation for frame in inputs)
    first = declarations[0]
    if isinstance(operation, (FilterValues, TopValues)):
        return first
    if isinstance(operation, SelectValues):
        if first is None:
            return None
        sources = {output.path: output.target for output in first.outputs}
        paths = inputs[0]._schema.value_paths

        def direct(slot: ValueSlot) -> DerivationTarget | None:
            return sources.get(paths[slot.index])

        selected: list[DerivedOutput] = []
        for path, (_, expression) in zip(schema.value_paths, operation.values, strict=True):
            target = direct(expression) if isinstance(expression, ValueSlot) else None
            if (
                isinstance(expression, ValueBinary)
                and expression.operation == "sub"
                and isinstance(expression.left, ValueSlot)
                and isinstance(expression.right, ValueSlot)
            ):
                left, right = direct(expression.left), direct(expression.right)
                if (
                    left is not None
                    and right is not None
                    and left.align_side == "left"
                    and right.align_side == "right"
                    and left.pairing == right.pairing
                    and left.baseline == right.current
                    and right.baseline == left.current
                ):
                    target = replace(left, mode="absolute", align_side=None)
            if target is not None:
                selected.append(DerivedOutput(path, target))
        return FrameDerivation(tuple(selected))
    if isinstance(operation, CompareFrameValues):
        outputs = (
            []
            if first is None
            else [
                DerivedOutput(item.path, item.target)
                for item in first.outputs
                if item.target.mode == "level"
                and item.target.align_side is None
                and item.target.baseline is None
            ]
        )
        current = {item.path: item.target for item in outputs}
        for pairing, declaration in zip(operation.baselines, declarations[1:], strict=True):
            if declaration is None:
                continue
            for item in declaration.outputs:
                left, right = current.get(item.path), item.target
                if (
                    left is None
                    or right.mode != "level"
                    or right.baseline is not None
                    or right.align_side is not None
                ):
                    continue
                baseline = _remap(right.current, tuple((j, i) for i, j in pairing.pairs))
                outputs.append(
                    DerivedOutput(
                        (pairing.alias, "baseline", *item.path),
                        DerivationTarget("level", baseline, left.current, pairing),
                    )
                )
                for role in ("absolute", "relative"):
                    outputs.append(
                        DerivedOutput(
                            (pairing.alias, role, *item.path),
                            DerivationTarget(role, left.current, baseline, pairing),
                        )
                    )
        return FrameDerivation(tuple(outputs))
    if isinstance(operation, AlignFrameValues):
        left_declaration, right_declaration = declarations
        if left_declaration is None or right_declaration is None:
            return None
        output = []
        for declaration, companion, side in (
            (left_declaration, right_declaration, "left"),
            (right_declaration, left_declaration, "right"),
        ):
            opposite = {item.path: item.target for item in companion.outputs}
            for item in declaration.outputs:
                target = item.target
                peer = opposite.get(item.path)
                if (
                    target.mode != "level"
                    or target.baseline is not None
                    or target.align_side is not None
                ):
                    continue
                a = target.current
                b = (
                    peer.current
                    if peer is not None
                    and peer.mode == "level"
                    and peer.baseline is None
                    and peer.align_side is None
                    else None
                )
                inverse = tuple((j, i) for i, j in operation.pairs)
                if side == "right":
                    a = _remap(a, inverse)
                elif b is not None:
                    b = _remap(b, inverse)
                output.append(
                    DerivedOutput(
                        (side, *item.path),
                        DerivationTarget(
                            "level", a, b, operation, "left" if side == "left" else "right"
                        ),
                    )
                )
        return FrameDerivation(tuple(output))
    return None


def key_checkpoint(data: CompletedFrameData) -> CompletedFrameData:
    """Copy only complete coordinates and their required frozen directories."""
    from marivo.analysis.core.frame_data import CompletedFrameData, required_attachments

    schema = DataSchema(data.schema.axes, ())
    names = set(required_attachments(schema))
    return CompletedFrameData(
        schema,
        data.copy_rows().loc[:, list(schema.key_fields)],
        tuple(a for a in data.copy_attachments() if a.name in names),
    )


def complete_declaration(
    declaration: FrameDerivation | None,
    keys: dict[str, CompletedFrameData],
    metadata: FrameResultMetadata,
) -> FrameDerivation | None:
    """Replace source key dependencies with detached coordinate-only carriers."""
    if declaration is None:
        return None
    from marivo.analysis.core.frame_data import CompletedFrameResult, MemoryFrameBacking
    from marivo.analysis.dsl._frame_inputs import FrameKeyInput
    from marivo.analysis.dsl.frame import AnalysisFrame

    cache: dict[int, SourceMetric] = {}

    def complete(metric: SourceMetric) -> SourceMetric:
        previous = cache.get(id(metric))
        if previous is not None:
            return previous
        frozen = []
        for dependency in metric.binding.frame_key_inputs:
            original = dependency.frame
            data = keys.get(dependency.input_id)
            if data is None:
                backing = original._backing
                if isinstance(backing, MemoryFrameBacking):
                    data = key_checkpoint(backing.data)
                else:
                    raise AssertionError("Evaluated source key dependency has no checkpoint")
            # The carrier is deliberately source-free and contains no value history.
            carrier = AnalysisFrame._from_memory(
                original._local, CompletedFrameResult(data, metadata)
            )
            carrier = AnalysisFrame._bind(
                carrier._local, carrier._schema, carrier._backing, dependency.input_id
            )
            frozen.append(FrameKeyInput(dependency.input_id, carrier))
        result = replace(metric, binding=replace(metric.binding, frame_key_inputs=tuple(frozen)))
        cache[id(metric)] = result
        return result

    return FrameDerivation(
        tuple(
            replace(
                output,
                target=replace(
                    output.target,
                    current=complete(output.target.current),
                    baseline=None
                    if output.target.baseline is None
                    else complete(output.target.baseline),
                ),
            )
            for output in declaration.outputs
        )
    )
