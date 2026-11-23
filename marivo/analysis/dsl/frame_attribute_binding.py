"""Rewrite captured source declarations over a selected complete parent domain."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal, Protocol, TypeVar

from ibis.common.deferred import Deferred
from typing_extensions import Self, override

from marivo._definition.correspondence import ComparisonAxis
from marivo._definition.expressions import RowField, SourceAggregate
from marivo._definition.graph import computation_fingerprint, direct_refs
from marivo._definition.grouping import _MembershipGrouping, grouping_binding
from marivo._definition.population_types import population_definition, population_identity_keys
from marivo._definition.relations import (
    MembershipBinding,
    ObserveQuery,
)
from marivo._definition.types import (
    AxisBinding,
    CalendarCoordinate,
    IntervalCoordinate,
    field_axis,
)
from marivo._kernel.grouping import Grouping
from marivo._kernel.input_context import captured_context
from marivo._kernel.validation import is_instance
from marivo.analysis.binding import observe
from marivo.analysis.binding.attribute_game import (
    AttributeGame,
    build_game,
    combine_games,
    require_games_match,
    state_unit,
)
from marivo.analysis.binding.attribute_rules import (
    AttributeRule,
    AttributeRuleError,
    AttributeSourceRequest,
    build_attribute_rule,
    component_requests,
    require_rules_match,
)
from marivo.analysis.binding.graph import BoundGraph
from marivo.analysis.binding.signature import BoundQuantity, BoundSignature
from marivo.analysis.core.attribute import pending_contribution_schema
from marivo.analysis.core.attribute_game import game_schema
from marivo.analysis.core.frame_attribute_inputs import AttributeInputRequest
from marivo.analysis.core.frame_data import (
    DerivedOutput,
    ExpressionFrameBacking,
    FrameExpressionRoot,
    FrameNode,
    OperationFrameBinding,
    SourceFrameBinding,
    SourceMetric,
)
from marivo.analysis.core.frame_operations import (
    AttributeContributionValues,
    AttributeFrameValues,
    AttributeGameValues,
    FrameAttributeTarget,
)
from marivo.analysis.core.frame_value_analysis import select_attribute_source_schema
from marivo.analysis.core.source_frame_schema import pending_schema
from marivo.analysis.dsl._frame_inputs import population_frame_inputs
from marivo.analysis.dsl.frame_attribute_projection import project_attribute_parent
from marivo.analysis.dsl.frame_attribute_sources import (
    SourceGroupKey,
    SourceMetricGroup,
    collect_source_groups,
    source_group_key,
)
from marivo.analysis.dsl.frame_attribute_target import (
    target_comparison as _target_comparison,
)
from marivo.analysis.dsl.population import Population, _axes, _owner, product
from marivo.analysis.materialization.frame_services import LocalFrameServices
from marivo.analysis.model.attribute_game_errors import AttributeGameError
from marivo.analysis.model.data_schema import (
    CalendarDataAxis,
    IntervalDataAxis,
    PairedDataAxis,
)
from marivo.analysis.model.errors import reject
from marivo.analysis.model.frame_contracts import (
    frame_backing,
    frame_derivation_of,
    frame_local,
    frame_logical_schema,
    merge_frame_key_inputs,
)
from marivo.analysis.model.frame_schema import LogicalSchema
from marivo.semantic import RowExpression
from marivo.semantic.errors import SemanticError
from marivo.semantic.spi import (
    FieldExpression,
    bind_field_in_state,
    expression_role,
    validate_context,
)


class AttributeFrameNode(FrameNode, Protocol):
    """Construct further immutable nodes under the receiver's local authority."""

    @property
    @override
    def _local(self) -> LocalFrameServices: ...

    @classmethod
    def _from_expression(
        cls, local: LocalFrameServices, schema: LogicalSchema, root: FrameExpressionRoot
    ) -> Self: ...


AttributeFrameT = TypeVar("AttributeFrameT", bound=AttributeFrameNode)


def derived_attribute_frame(
    frame: AttributeFrameT, schema: LogicalSchema, root: FrameExpressionRoot
) -> AttributeFrameT:
    """Create an Attribute node under the receiver's exact local authority."""
    return frame._from_expression(frame._local, schema, root)  # noqa: SLF001 - Attribute construction protocol.


def _validate_classification_scope(
    source: SourceFrameBinding,
    target: DerivedOutput,
    side: Literal["left", "right"],
    bound_by: tuple[Deferred | Grouping, ...] | Population,
    fine_axes: tuple[ComparisonAxis, ...],
    fine_memberships: tuple[MembershipBinding, ...],
) -> None:
    required = (
        set(direct_refs(population_definition(bound_by)))
        if isinstance(bound_by, Population)
        else set()
    )
    for axis in fine_axes:
        if isinstance(axis, AxisBinding):
            required.update((axis.entity, *axis.path))
            if axis.field is not None:
                required.add(axis.field)
    for membership in fine_memberships:
        for path in membership.paths:
            for relationship in path.relationships:
                required.update(
                    (relationship.ref, relationship.from_entity, relationship.to_entity)
                )
    missing = tuple(
        sorted(
            reference.ref.key
            for reference in required
            if reference.ref not in source.state.definitions
        )
    )
    if missing:
        reject(
            "classification dependencies in each retained source scope",
            f"{side} endpoint for {'.'.join(target.path)}: {', '.join(missing)}",
            "Choose classifications available in this endpoint's captured scope.",
            "frame.attribute",
            canonical_id="analysis_frame.attribute",
        )


def _bind_classification(
    parent: FrameNode,
    source: SourceFrameBinding,
    target: DerivedOutput,
    side: Literal["left", "right"],
    by: tuple[FieldExpression | Grouping, ...] | Population,
) -> tuple[
    tuple[Deferred | Grouping, ...] | Population,
    tuple[MembershipBinding, ...],
    tuple[ComparisonAxis, ...],
]:
    try:
        if isinstance(by, Population):
            validate_context(
                captured_context(by),
                project_root=source.connection.project_root,
                owner=frame_local(parent).owner,
            )
            for identity, captured_keys in population_identity_keys(by):
                entity = source.state.registry.entities.get(identity.removeprefix("entity:"))
                if entity is None or tuple(entity.primary_key) != captured_keys:
                    reject(
                        "the captured complete identity in this endpoint scope",
                        f"{side}: {identity}",
                        "Rebuild the Population with compatible complete keys.",
                        "frame.attribute",
                    )
            bound_by: tuple[Deferred | Grouping, ...] | Population = by
        else:
            bound_items: list[Deferred | Grouping] = []
            for index, item in enumerate(by):
                if isinstance(item, RowExpression) and expression_role(item) in (
                    "dimension",
                    "time",
                    "numeric",
                ):
                    bound_items.append(
                        bind_field_in_state(
                            item,
                            source.state,
                            project_root=source.connection.project_root,
                            owner=frame_local(parent).owner,
                        )
                    )
                elif isinstance(item, Grouping):
                    validate_context(
                        captured_context(item),
                        project_root=source.connection.project_root,
                        owner=frame_local(parent).owner,
                    )
                    bound_items.append(item)
                else:
                    reject(
                        "a semantic classification field or Grouping",
                        type(item).__name__,
                        "Pass loaded classification fields or a complete Population.",
                        f"frame.attribute.by[{index}]",
                    )
            bound_by = tuple(bound_items)
    except SemanticError as error:
        reject(
            "classification dependencies in each retained source scope",
            f"{side} endpoint for {'.'.join(target.path)}: {error}",
            "Choose classifications available in this endpoint's captured scope.",
            "frame.attribute",
            canonical_id="analysis_frame.attribute",
        )
    fine_memberships = (
        tuple(grouping_binding(item) for item in bound_by if isinstance(item, _MembershipGrouping))
        if isinstance(bound_by, tuple)
        else ()
    )
    fine_axes = (
        population_definition(bound_by).axes
        if isinstance(bound_by, Population)
        else tuple(
            axis
            for item in bound_by
            for axis in (
                tuple(field_axis(read) for read in grouping_binding(item).fields)
                if isinstance(item, _MembershipGrouping)
                else _axes((item,))
            )
        )
    )
    if not fine_axes or any(not isinstance(axis, AxisBinding) for axis in fine_axes):
        reject(
            "nonempty business classifications",
            repr(fine_axes),
            "Pass new semantic fields or one complete Population.",
            "frame.attribute",
            canonical_id="analysis_frame.attribute",
        )
    _validate_classification_scope(source, target, side, bound_by, fine_axes, fine_memberships)
    return bound_by, fine_memberships, fine_axes


def _attribute_quantities(
    original: BoundGraph,
    target: DerivedOutput,
    metric: SourceMetric,
    side: Literal["left", "right"],
    fine_axes: tuple[ComparisonAxis, ...],
    groups: dict[SourceGroupKey, SourceMetricGroup] | None,
    *,
    game: bool,
) -> tuple[tuple[BoundQuantity, ...], int, AttributeGame | None]:
    group = None if groups is None else groups[source_group_key(metric, side, target.target)]
    quantities: tuple[BoundQuantity, ...]
    if group is None:
        quantity = next(q for q in original.signature.quantities if q.name == ".".join(metric.path))
        assert isinstance(quantity, BoundQuantity)
        quantities = (replace(quantity, name="target"),)
        source_index = 0
    else:
        quantities = group.quantities
        source_index = group.index(metric.path)
    captured_game = None
    if game:
        built = tuple(
            build_game(
                quantity.definition,
                dict(original.signature.declared_metrics),
                metric=quantity.name,
                by=fine_axes,
            )
            for quantity in quantities
        )
        combined = combine_games(built, tuple(quantity.name for quantity in quantities))
        captured_game = combined[source_index]
        selected_quantity = quantities[source_index]
        original_quantities = {quantity.name: quantity for quantity in quantities}

        quantities = tuple(
            replace(
                selected_quantity,
                name=name,
                definition=node,
                unit=original_quantities[name].unit
                if name in original_quantities
                else state_unit(node),
            )
            for name, node in captured_game.quantities
        )
    return quantities, source_index, captured_game


@dataclass(frozen=True, slots=True)
class _AttributeEndpoint:
    frame: FrameNode
    parent_positions: tuple[int, ...]
    projection_id: str
    rule: AttributeRule | None
    source_rules: tuple[tuple[str, AttributeRule], ...]
    source_index: int
    source_side: int
    game: AttributeGame | None


def _capture_attribute_endpoint(
    parent: AttributeFrameNode,
    target: DerivedOutput,
    metric: SourceMetric,
    side: Literal["left", "right"],
    by: tuple[FieldExpression | Grouping, ...] | Population,
    cache: dict[SourceGroupKey, FrameNode] | None,
    groups: dict[SourceGroupKey, SourceMetricGroup] | None,
    *,
    game: bool,
) -> _AttributeEndpoint:
    source = metric.binding
    bound_by, fine_memberships, fine_axes = _bind_classification(parent, source, target, side, by)
    projection = project_attribute_parent(parent, target, metric, side, fine_axes)
    original = source.graph
    observation = original.signature.observation
    assert observation is not None
    if game and observation.coordinates.memberships:
        reject(
            "unique fact-to-player input without membership allocation",
            repr(observation.coordinates.memberships),
            "Use a uniquely classified source Metric.",
            "frame.attribute",
            canonical_id="analysis_frame.attribute",
        )
    fine_by = product(projection.population, bound_by) if isinstance(bound_by, Population) else None
    assert fine_by is None or isinstance(fine_by, Population)
    axes = (*projection.source_axes, *fine_axes)
    reads = tuple(
        axis if isinstance(axis, (CalendarCoordinate, IntervalCoordinate)) else axis.as_read()
        for axis in axes
    )
    quantities, source_index, captured_game = _attribute_quantities(
        original, target, metric, side, fine_axes, groups, game=game
    )
    bound = observe.bind_observation(
        tuple((quantity.name, quantity.definition) for quantity in quantities),
        dict(original.signature.declared_metrics),
        by=reads,
        where=projection.condition,
        timezone=observation.period.timezone if observation.period is not None else "UTC",
        state=source.state,
        memberships=(*observation.coordinates.memberships, *fine_memberships),
        population=population_definition(fine_by) if fine_by is not None else None,
        report_period=observation.period,
    )
    bound = replace(
        bound,
        coordinates=replace(
            bound.coordinates,
            parent_scope=population_definition(projection.population),
        ),
    )
    graph = BoundGraph(
        ObserveQuery(
            tuple((quantity.name, quantity.definition) for quantity in quantities),
            axes,
            frame_local(parent).owner,
            population=bound.coordinates.population,
            count_populations=tuple(domain for _, domain in bound.count_populations),
        ),
        BoundSignature(
            frame_local(parent).owner,
            quantities,
            axes,
            original.signature.declared_metrics,
            observation=bound,
        ),
    )
    fine: FrameNode = derived_attribute_frame(
        parent,
        pending_schema(graph, source.state),
        SourceFrameBinding(
            graph,
            source.state,
            source.connection,
            merge_frame_key_inputs(
                source.frame_key_inputs,
                population_frame_inputs(projection.population),
                population_frame_inputs(by) if isinstance(by, Population) else (),
                owner=frame_local(parent).owner,
            ),
        ),
    )
    if cache is not None:
        fine = cache.setdefault(source_group_key(metric, side, target.target), fine)
    fine_backing = frame_backing(fine)
    assert isinstance(fine_backing, ExpressionFrameBacking) and isinstance(
        fine_backing.root, SourceFrameBinding
    )
    source_rules = (
        ()
        if game
        else tuple(
            (
                quantity.name,
                build_attribute_rule(
                    quantity.definition,
                    dict(graph.signature.declared_metrics),
                    bound.contributions,
                    occurrence=(index,),
                    by=fine_axes,
                    metric=".".join(target.path) if index == source_index else quantity.name,
                ),
            )
            for index, quantity in enumerate(quantities)
        )
    )
    return _AttributeEndpoint(
        fine,
        projection.positions,
        computation_fingerprint(population_definition(projection.population)),
        None if game else source_rules[source_index][1],
        source_rules,
        source_index,
        0 if projection.side == "left" else 1,
        captured_game,
    )


def source_inputs(
    parent: AttributeFrameNode,
    target: DerivedOutput,
    by: tuple[FieldExpression | Grouping, ...] | Population,
    cache: dict[SourceGroupKey, FrameNode] | None = None,
    groups: dict[SourceGroupKey, SourceMetricGroup] | None = None,
    *,
    game: bool = False,
) -> AttributeInputRequest:
    """Bind from captured state/config; no Catalog or executable endpoint reconstruction."""
    if game and (
        isinstance(by, tuple) and any(isinstance(item, _MembershipGrouping) for item in by)
    ):
        reject(
            "unique fact-to-player classifications for replacement attribution",
            repr(by),
            "Use single-valued classification fields or a complete Population with unique grouping.",
            "frame.attribute",
            canonical_id="analysis_frame.attribute",
        )
    left = _capture_attribute_endpoint(
        parent, target, target.target.current, "left", by, cache, groups, game=game
    )
    baseline = target.target.baseline
    right = left
    if target.target.mode != "level":
        assert baseline is not None
        right = _capture_attribute_endpoint(
            parent, target, baseline, "right", by, cache, groups, game=game
        )
    games = None
    if game:
        assert left.game is not None and right.game is not None
        require_games_match(left.game, right.game, metric=".".join(target.path))
        games = left.game, right.game
    else:
        assert left.rule is not None and right.rule is not None
        require_rules_match(left.rule, right.rule, metric=".".join(target.path))
    operation = AttributeFrameValues(
        left.parent_positions,
        right.parent_positions,
        "outer",
        left.projection_id,
        right.projection_id,
    )
    return AttributeInputRequest(
        parent,
        (left.frame, right.frame),
        operation,
        (
            () if left.rule is None else (("target", left.rule),),
            () if right.rule is None else (("target", right.rule),),
        ),
        (left.source_rules, right.source_rules) if groups is not None else None,
        (left.source_index, right.source_index) if groups is not None else None,
        (left.source_side, right.source_side) if groups is not None else None,
        games,
    )


def _single(
    parent: AttributeFrameT,
    target: DerivedOutput,
    by: tuple[FieldExpression | Grouping, ...] | Population,
    cache: dict[SourceGroupKey, FrameNode],
    groups: dict[SourceGroupKey, SourceMetricGroup],
    permutations: int,
    seed: int,
    game_cache: dict[SourceGroupKey, FrameNode],
    game_selected: bool,
) -> AttributeFrameT:
    """Private graph-backed contribution entry used by the Frame API."""

    if game_selected and isinstance(by, Population):
        raise AttributeGameError(
            reason="unsupported_state",
            metric=".".join(target.path),
            received="Population classifications have no qualified replacement-game coverage receipt; uncovered source facts cannot be silently excluded",
        )
    if game_selected:
        if target.target.mode == "level":
            reject(
                "a paired absolute or relative change for replacement attribution",
                repr(target.path),
                "First compare the Metric and select an absolute or relative change target.",
                "frame.attribute",
                canonical_id="analysis_frame.attribute",
            )
        request = source_inputs(parent, target, by, game_cache, groups, game=True)
    else:
        request = source_inputs(parent, target, by, cache, groups)
    if request.games is not None:
        game_operation = AttributeGameValues(
            request.operation,
            request.games,
            FrameAttributeTarget(
                target.path,
                target.target.mode,
                frame_logical_schema(parent).value_paths.index(target.path),
                _target_comparison(parent, target),
            ),
            permutations,
            seed,
        )
        schema = game_schema(
            frame_logical_schema(request.endpoints[0]),
            frame_logical_schema(request.endpoints[1]),
            frame_logical_schema(parent),
            game_operation,
        )
        return derived_attribute_frame(
            parent,
            schema,
            OperationFrameBinding(game_operation, (parent, *request.endpoints)),
        )
    operation = AttributeContributionValues(
        request.operation,
        request.rules,
        FrameAttributeTarget(
            target.path,
            target.target.mode,
            frame_logical_schema(parent).value_paths.index(target.path),
            _target_comparison(parent, target),
        ),
        target.target.mode == "level",
        request.source_rules,
        request.source_indices,
        request.source_sides,
    )

    left_schema, right_schema = (
        frame_logical_schema(request.endpoints[0]),
        frame_logical_schema(request.endpoints[1]),
    )
    if request.source_indices is not None:
        left_schema = select_attribute_source_schema(left_schema, request.source_indices[0])
        right_schema = select_attribute_source_schema(right_schema, request.source_indices[1])
    schema = pending_contribution_schema(
        left_schema, right_schema, frame_logical_schema(parent), operation
    )
    return derived_attribute_frame(
        parent,
        schema,
        OperationFrameBinding(
            operation,
            (
                parent,
                *(
                    request.endpoints[:1]
                    if operation.target is not None and operation.target.mode == "level"
                    else request.endpoints
                ),
            ),
        ),
    )


def _requires_replacement_game(
    parent: AttributeFrameNode,
    target: DerivedOutput,
    by: tuple[FieldExpression | Grouping, ...] | Population,
) -> bool:
    try:
        selected = source_inputs(parent, target, by)
        fine_fields = (
            population_definition(by).axes
            if isinstance(by, Population)
            else tuple(
                axis
                for item in by
                if is_instance(item, (Deferred, RowExpression, Grouping))
                for axis in (
                    tuple(field_axis(read) for read in grouping_binding(item).fields)
                    if isinstance(item, _MembershipGrouping)
                    else _axes((item,))
                )
            )
        )

        def unproven_distinct(
            item: AttributeSourceRequest, fine_fields: tuple[ComparisonAxis, ...] = fine_fields
        ) -> bool:
            node = item.binding.definition
            if not isinstance(node, SourceAggregate) or node.reducer != "count_distinct":
                return False
            row = node.operand
            return not isinstance(row, RowField) or not any(
                isinstance(axis, AxisBinding)
                and axis.field is not None
                and axis.field.ref == row.ref.ref
                and axis.entity.ref == row.root.ref
                and tuple(ref.ref for ref in axis.path)
                == tuple(ref.ref for ref in (row.read.path if row.read is not None else ()))
                for axis in fine_fields
            )

        return target.target.mode != "level" and any(
            unproven_distinct(item) for item in component_requests(selected.rules[0][0][1])
        )
    except AttributeRuleError as error:
        if target.target.mode == "level":
            raise
        if error.reason != "unsupported_expression" or error.mismatch is not None:
            raise
        return True


def bind_attribute_reports(
    parent: AttributeFrameT,
    *,
    by: tuple[FieldExpression | Grouping, ...] | Population,
    permutations: int = 1024,
    seed: int = 0,
) -> tuple[AttributeFrameT, ...]:
    """Build the ordered closed targets retained by this exact receiver."""
    if type(permutations) is not int or permutations < 2 or type(seed) is not int or seed < 0:
        reject(
            "permutations >= 2 and seed >= 0, both exact integers excluding bool",
            repr((permutations, seed)),
            "Pass permutations=1024 and seed=0, or other integers in these ranges.",
            "frame.attribute",
            canonical_id="analysis_frame.attribute",
        )
    if not is_instance(by, (tuple, Population)) or (isinstance(by, tuple) and not by):
        reject(
            "a nonempty classification tuple or complete Population",
            repr(by),
            "Pass by=(field,) or one complete Population.",
            "frame.attribute",
            canonical_id="analysis_frame.attribute",
        )
    if isinstance(by, Population) and _owner(population_definition(by)) not in (
        None,
        frame_local(parent).owner,
    ):
        reject(
            "a Population from this Session",
            repr(_owner(population_definition(by))),
            "Build the fine Population in the receiver's owning Session.",
            "frame.attribute",
            canonical_id="analysis_frame.attribute",
        )
    declaration = frame_derivation_of(parent)
    if declaration is None:
        ordinary_axes = tuple(
            (axis.token, axis.name)
            for axis in frame_logical_schema(parent).axes
            if not isinstance(axis, (CalendarDataAxis, IntervalDataAxis, PairedDataAxis))
        )
        reject(
            "a retained source declaration on this Frame instance",
            repr((frame_logical_schema(parent).value_paths, ordinary_axes)),
            "Use the same-process compute/persist return value, or supply the original Metric, where, membership rules, time scopes and comparison. Bridge the saved complete ordinary keys "
            + repr(ordinary_axes)
            + " with session.population_from(saved_frame, on=...) and in_population before observe/compare. Reconstruct temporal periods and their original correspondence explicitly from saved coordinate facts.",
            "frame.attribute",
            canonical_id="analysis_frame.attribute",
        )
    failed = tuple(
        (path, dict(declaration.rejections).get(path, "unsupported source recipe"))
        for path in frame_logical_schema(parent).value_paths
        if path not in {item.path for item in declaration.outputs}
    )
    if failed:
        reject(
            "direct captured Metric or comparison outputs",
            repr(failed),
            "Select direct supported source outputs, or investigate these targets explicitly with population_from, in_population and a finer observe/compare.",
            "frame.attribute",
            canonical_id="analysis_frame.attribute",
        )

    selected_games = tuple(
        _requires_replacement_game(parent, target, by) for target in declaration.outputs
    )
    exact_groups = collect_source_groups(
        replace(
            declaration,
            outputs=tuple(
                target
                for target, game in zip(declaration.outputs, selected_games, strict=True)
                if not game
            ),
        )
    )
    game_groups = collect_source_groups(
        replace(
            declaration,
            outputs=tuple(
                target
                for target, game in zip(declaration.outputs, selected_games, strict=True)
                if game
            ),
        )
    )
    cache: dict[SourceGroupKey, FrameNode] = {}
    game_cache: dict[SourceGroupKey, FrameNode] = {}
    return tuple(
        _single(
            parent,
            target,
            by,
            cache,
            game_groups if game else exact_groups,
            permutations,
            seed,
            game_cache,
            game,
        )
        for target, game in zip(declaration.outputs, selected_games, strict=True)
    )
