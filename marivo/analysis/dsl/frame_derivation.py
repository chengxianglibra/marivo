"""Compact process-local declarations, independent of completed Frame backings."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING

from marivo._definition.query import FilterValues, SelectValues, TopValues, ValueBinary, ValueSlot
from marivo._definition.relations import ObserveQuery
from marivo.analysis.core.frame_data import (
    CompletedFrameResult,
    DerivationTarget,
    DerivedOutput,
    FrameDerivation,
    FrameKeyDependency,
    FrameNode,
    MemoryFrameBacking,
    OperationFrameBinding,
    SourceFrameBinding,
    SourceMetric,
    key_checkpoint,
)
from marivo.analysis.core.frame_operations import (
    AlignFrameValues,
    CompareFrameValues,
    FixFrameValues,
)
from marivo.analysis.materialization.frame_services import LocalFrameServices
from marivo.analysis.model.data_schema import DataSchema
from marivo.analysis.model.frame_contracts import FrameKeyInput
from marivo.analysis.model.lineage import directory

if TYPE_CHECKING:
    from marivo.analysis.core.frame_data import CompletedFrameData, FrameExpressionRoot
    from marivo.analysis.model.frame_metadata import FrameResultMetadata
    from marivo.analysis.model.frame_structure import PendingDataSchema


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
    if isinstance(operation, FixFrameValues):
        return _fix(first, operation)
    if isinstance(operation, (FilterValues, TopValues)):
        return first
    if isinstance(operation, SelectValues):
        return _select(first, inputs[0]._schema.value_paths, schema, operation)
    if isinstance(operation, CompareFrameValues):
        return _compare(first, declarations, operation)
    if isinstance(operation, AlignFrameValues):
        return _align(declarations, operation)
    return None


def _fix(first: FrameDerivation | None, operation: FixFrameValues) -> FrameDerivation | None:
    if first is None:
        return None

    def fixed(metric: SourceMetric) -> SourceMetric:
        return replace(
            metric,
            axis_positions=tuple(
                (a, b - (b > operation.position))
                for a, b in metric.axis_positions
                if b != operation.position
            ),
            fixed_axes=tuple(
                sorted(
                    (
                        *metric.fixed_axes,
                        *(
                            (a, operation.key)
                            for a, b in metric.axis_positions
                            if b == operation.position
                        ),
                    )
                )
            ),
        )

    def fix_target(value: DerivationTarget) -> DerivationTarget:
        pairing = value.pairing
        if pairing is not None:
            # The receiver already uses current/left coordinates; source positions
            # are transported separately for each endpoint.
            removed = next(j for i, j in pairing.pairs if i == operation.position)
            pairing = replace(
                pairing,
                pairs=tuple(
                    (a - (a > operation.position), b - (b > removed))
                    for a, b in pairing.pairs
                    if a != operation.position
                ),
            )
        return replace(
            value,
            current=fixed(value.current),
            baseline=None if value.baseline is None else fixed(value.baseline),
            pairing=pairing,
        )

    return replace(
        first,
        outputs=tuple(replace(item, target=fix_target(item.target)) for item in first.outputs),
    )


def _select(
    first: FrameDerivation | None,
    paths: tuple[tuple[str, ...], ...],
    schema: DataSchema | PendingDataSchema,
    operation: SelectValues,
) -> FrameDerivation | None:
    if first is None:
        return None
    sources = {output.path: output.target for output in first.outputs}

    def direct(slot: ValueSlot) -> DerivationTarget | None:
        return sources.get(paths[slot.index])

    selected: list[DerivedOutput] = []
    rejections: list[tuple[tuple[str, ...], str]] = []
    prior_rejections = dict(first.rejections)
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
        else:
            label = (
                prior_rejections.get(
                    paths[expression.index], "ValueSlot without a direct source Metric"
                )
                if isinstance(expression, ValueSlot)
                else type(expression).__name__
            )
            rejections.append((path, label))
    return FrameDerivation(tuple(selected), tuple(rejections))


def _compare(
    first: FrameDerivation | None,
    declarations: tuple[FrameDerivation | None, ...],
    operation: CompareFrameValues,
) -> FrameDerivation | None:
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


def _align(
    declarations: tuple[FrameDerivation | None, ...], operation: AlignFrameValues
) -> FrameDerivation | None:
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


def uses_frame_keys(declaration: FrameDerivation | None) -> bool:
    """Identify retained key inputs before evaluating a source declaration."""
    return declaration is not None and any(
        output.target.current.binding.frame_key_inputs
        or (output.target.baseline is not None and output.target.baseline.binding.frame_key_inputs)
        for output in declaration.outputs
    )


def complete_declaration(
    declaration: FrameDerivation | None,
    keys: dict[str, CompletedFrameData],
    metadata: FrameResultMetadata,
    bind_key: Callable[[LocalFrameServices, CompletedFrameResult, str], FrameNode],
) -> FrameDerivation | None:
    """Replace source key dependencies with detached coordinate-only carriers."""
    if declaration is None:
        return None
    cache: dict[int, SourceMetric] = {}
    frozen_inputs: dict[str, FrameKeyDependency] = {}

    def complete(metric: SourceMetric) -> SourceMetric:
        previous = cache.get(id(metric))
        if previous is not None:
            return previous
        frozen = []
        for dependency in metric.binding.frame_key_inputs:
            prior = frozen_inputs.get(dependency.input_id)
            if prior is not None:
                frozen.append(prior)
                continue
            original = dependency.frame
            assert isinstance(original._local, LocalFrameServices)
            data = keys.get(dependency.input_id)
            if data is None:
                backing = original._backing
                if isinstance(backing, MemoryFrameBacking):
                    data = key_checkpoint(backing.data)
                else:
                    raise AssertionError("Evaluated source key dependency has no checkpoint")
            # The carrier is deliberately source-free and contains no value history.
            lineage = directory(
                data.schema,
                (),
                tuple(original.lineage.axes.values()),
                domain=original.lineage._domain,
            )
            carrier = bind_key(
                original._local,
                CompletedFrameResult(data, replace(metadata, lineage=lineage)),
                dependency.input_id,
            )
            captured = FrameKeyInput(dependency.input_id, carrier)
            frozen_inputs[dependency.input_id] = captured
            frozen.append(captured)
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
        ),
        declaration.rejections,
    )
