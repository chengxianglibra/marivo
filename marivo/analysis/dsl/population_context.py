"""Capture project ownership and complete key declarations for Population inputs."""

from collections.abc import Callable
from typing import TypeVar

from ibis.common.deferred import Deferred

from marivo._definition.deferred import inspect_capture
from marivo._definition.population_types import PopulationParameter
from marivo._definition.types import DefinitionGraphError
from marivo._kernel.grouping import Grouping, grouping_identity_keys
from marivo._kernel.input_context import InputContext, captured_context, merge_input_contexts
from marivo.analysis.model.errors import reject
from marivo.semantic import Entity, MetricExpression, RowExpression
from marivo.semantic.spi import Path


def population_context(*values: object) -> InputContext:
    contexts: list[InputContext] = []
    for value in values:
        if isinstance(
            value,
            (Entity, Path, RowExpression, MetricExpression, Grouping, PopulationParameter),
        ):
            contexts.append(captured_context(value))
        elif isinstance(value, Deferred):
            contexts.append(inspect_capture(value).context)
        elif isinstance(value, (tuple, list)):
            contexts.append(population_context(*value))
    return validate_population_contract(
        lambda: merge_input_contexts(*contexts), "population.context"
    )


def population_identity_contracts(*values: object) -> tuple[tuple[str, tuple[str, ...]], ...]:
    result: dict[str, tuple[str, ...]] = {}
    for value in values:
        items = (
            grouping_identity_keys(value)
            if isinstance(value, (Grouping, PopulationParameter))
            else ((value.id, value.primary_key),)
            if isinstance(value, Entity)
            else population_identity_contracts(*value)
            if isinstance(value, (tuple, list))
            else ()
        )
        for identity, keys in items:
            if identity in result and result[identity] != keys:
                reject(
                    "one captured complete identity",
                    identity,
                    "Rebuild the coordinate domain with consistent primary keys.",
                    "population",
                )
            result[identity] = keys
    return tuple(result.items())


_T = TypeVar("_T")


def validate_population_contract(action: Callable[[], _T], location: str) -> _T:
    try:
        return action()
    except DefinitionGraphError as error:
        reject(
            "a valid complete coordinate domain",
            str(error),
            "Use compatible complete axes, connected paths and one Session owner.",
            location,
            canonical_id=location
            if location
            in {
                "population.classify",
                "population.identities",
                "population.project",
                "population.where",
                "population.image",
                "population.preimage",
                "population.union",
                "population.intersection",
                "population.difference",
                "session.read",
            }
            else None,
        )
