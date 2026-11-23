"""Public directed paths and exact version selections for node occurrences."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import NoReturn, final, overload

from ibis.common.deferred import Deferred
from typing_extensions import override

from marivo._definition.deferred import _PathValue
from marivo._definition.population_parameters import PathRecipe, parameter
from marivo._definition.types import (
    DefinitionGraphError,
    InputContext,
    PathParameter,
    RelationshipStepParameter,
    merge_input_contexts,
)
from marivo._kernel.input_context import captured_context
from marivo._kernel.symbolic import PathSymbol, RelationshipSymbol
from marivo._kernel.temporal import InstantAnchor
from marivo.introspection.live.model import LiveHelpTarget
from marivo.semantic.catalog_model.objects import (
    Entity,
    Relationship,
    RelationshipStep,
    _step,
    relationship_step_parameter,
)
from marivo.semantic.errors import ErrorKind, SemanticExpressionError
from marivo.telemetry import traced

_PATH_HELP = LiveHelpTarget(surface="analysis", canonical_id="path.define")


@final
@dataclass(frozen=True, slots=True, init=False, repr=False)
class Path(_PathValue):
    """A directed chain of Relationships and optional node version instants."""

    steps: tuple[RelationshipStep, ...]
    source_at: InstantAnchor | None
    _parameter: PathParameter
    _context: InputContext = field(compare=False, hash=False)

    def __init__(self) -> None:
        raise TypeError("Use mv.path(...).")

    @override
    def __init_subclass__(cls) -> NoReturn:
        raise TypeError("Path is a closed value type.")

    @property
    def source(self) -> Entity:
        """Return the first Relationship's source Entity."""
        return self.steps[0].relationship.from_entity

    @property
    def target(self) -> Entity:
        """Return the final Relationship's target Entity."""
        return self.steps[-1].relationship.to_entity

    @override
    def __repr__(self) -> str:
        return f"<Path {self.source.id[:90]} -> {self.target.id[:90]}; use .show()>"

    def show(self) -> None:
        """Print bounded source, target, and step facts without reading data."""
        print(repr(self))
        print(f"source_at={self.source_at}")
        for step in self.steps[:8]:
            print(f"{step.relationship.id[:150]} target_at={step.target_at}")
        if len(self.steps) > 8:
            print(f"... {len(self.steps) - 8} more steps")


@overload
def path(
    *steps: Relationship | RelationshipStep, source_at: InstantAnchor | None = None
) -> Path: ...


@overload
def path(
    *steps: Relationship | RelationshipStep | RelationshipSymbol | Deferred,
    source_at: InstantAnchor | None = None,
) -> Path | PathSymbol: ...


@traced(surface="analysis", operation="path.define", capture="short")
def path(
    *steps: Relationship | RelationshipStep | Deferred,
    source_at: InstantAnchor | None = None,
) -> Path | PathSymbol:
    """Capture a nonempty connected directed Path without reading a source.

    Args:
        steps: Ordered Relationships or steps from relationship.at(anchor).
        source_at: Optional exact version instant of the first source Entity.

    Returns:
        An immutable Path for Catalog input, or a native symbolic Path parameter.

    Example: ``mv.path(order_customer.at(customer_time), customer_region)``.
    Constraints: Anchor each versioned node required by the consuming operation.
    """
    captured: list[RelationshipStep] = []
    recipes: list[RelationshipStepParameter | Deferred] = []
    try:
        for step in steps:
            if isinstance(step, Deferred):
                recipes.append(step)
                continue
            if isinstance(step, Relationship):
                step = _step(step, None)
            if not isinstance(step, RelationshipStep):
                raise SemanticExpressionError(
                    kind=ErrorKind.INVALID_TIME_PATH,
                    message="Path step has an invalid type.",
                    expected="directed Relationship or RelationshipStep values",
                    received=type(step).__name__,
                    action="Pass ordered Catalog Relationships, Relationship symbols, or relationship.at(anchor).",
                    help_target=_PATH_HELP,
                )
            captured.append(step)
            recipes.append(relationship_step_parameter(step))
        context = merge_input_contexts(*(captured_context(step) for step in captured))
        if len(recipes) != len(captured):
            return PathSymbol(parameter(PathRecipe(tuple(recipes), source_at, _context=context)))
        facts = PathParameter(
            tuple(relationship_step_parameter(step) for step in captured),
            source_at,
            _context=context,
        )
    except DefinitionGraphError as error:
        raise SemanticExpressionError(
            kind=ErrorKind.INVALID_TIME_PATH,
            message="Path construction failed.",
            expected="a connected directed Path with exact node anchors",
            received=str(error),
            action="Order the directed Relationships by endpoint and use session.instant(...) for versioned nodes.",
            help_target=_PATH_HELP,
        ) from error
    value = object.__new__(Path)
    object.__setattr__(value, "steps", tuple(captured))
    object.__setattr__(value, "source_at", source_at)
    object.__setattr__(value, "_parameter", facts)
    object.__setattr__(value, "_context", context)
    return value
