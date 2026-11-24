"""Exact applicability and invocation-local trace of Population rewrites."""

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field

from marivo._definition.relations import PopulationDependency, PopulationProduct, PopulationProgram
from marivo._definition.types import AxisBinding, EntityIdentityRead, FieldRead
from marivo.analysis.binding.programs import PopulationInput
from marivo.analysis.compiler.predicate import population_key
from marivo.analysis.compiler.query_stages import QueryPlan, RuleDecision
from marivo.analysis.model.optimization_policy import OptimizationPolicy


@dataclass(slots=True)
class PopulationOptimizations:
    """Record preparation decisions for the invocation's exact policy."""

    policy: OptimizationPolicy
    decisions: list[RuleDecision] = field(default_factory=list)
    stored_projection: Callable[[str], bool] | None = None

    def factorized(self, definition: PopulationDependency) -> bool:
        if not isinstance(definition, PopulationProgram) or not isinstance(
            definition.root, PopulationProduct
        ):
            return False
        identifier = population_key(definition)
        statuses = {
            decision.status
            for decision in self.decisions
            if decision.rule == "population_product_factorization"
            and identifier in decision.members
        }
        return statuses == {"adopted"}


def _fanout_prefix(read: FieldRead | EntityIdentityRead) -> tuple[str, ...]:
    for index, step in enumerate(read.path_facts):
        if step.cardinality in ("one_to_many", "many_to_many"):
            # Ignoring anchor differences here declines some otherwise
            # independent groups. An anchored occurrence is never split.
            return tuple(step.ref.ref.path for step in read.path_facts[: index + 1])
    return ()


def _product_tree(definition: PopulationDependency) -> Iterator[PopulationDependency]:
    yield definition
    if isinstance(definition, PopulationProgram) and isinstance(definition.root, PopulationProduct):
        for factor in definition.root.factors:
            yield from _product_tree(factor)


def product_members(definition: PopulationDependency) -> tuple[str, ...]:
    """Identify the plain nested product receipts of one factored condition."""
    return tuple(
        population_key(item)
        for item in _product_tree(definition)
        if isinstance(item, PopulationProgram) and isinstance(item.root, PopulationProduct)
    )


def independent_product_groups(
    definition: PopulationDependency, reads: Mapping[AxisBinding, FieldRead | EntityIdentityRead]
) -> tuple[PopulationDependency, ...]:
    """Group factors connected by any shared multivalued path occurrence."""
    if not isinstance(definition, PopulationProgram) or not isinstance(
        definition.root, PopulationProduct
    ):
        return (definition,)
    factors = tuple(
        item
        for item in _product_tree(definition)
        if not isinstance(item, PopulationProgram) or not isinstance(item.root, PopulationProduct)
    )
    components: list[tuple[set[int], set[tuple[str, ...]]]] = []
    for index, factor in enumerate(factors):
        prefixes = {
            prefix
            for axis in factor.axes
            if isinstance(axis, AxisBinding) and (prefix := _fanout_prefix(reads[axis]))
        }
        members = {index}
        retained = []
        for indices, prior in components:
            if prior & prefixes:
                members.update(indices)
                prefixes.update(prior)
            else:
                retained.append((indices, prior))
        components = [*retained, (members, prefixes)]
    if len(components) <= 1:
        return (definition,)
    groups = tuple(
        tuple(factors[index] for index in sorted(members))
        for members, _ in sorted(components, key=lambda component: min(component[0]))
    )
    return tuple(
        group[0]
        if len(group) == 1
        else PopulationProgram(
            PopulationProduct(group),
            definition.owner,
            tuple(axis for factor in group for axis in factor.axes),
        )
        for group in groups
    )


def unused_factored_products(
    plan: QueryPlan,
    consumer: str,
    bindings: tuple[PopulationInput, ...],
    decisions: tuple[RuleDecision, ...],
) -> frozenset[str]:
    """Find unused product stages while keeping their independent check inputs."""
    adopted = {
        member
        for decision in decisions
        if decision.rule == "population_product_factorization" and decision.status == "adopted"
        for member in decision.members
    }
    refused = {
        member
        for decision in decisions
        if decision.rule == "population_product_factorization" and decision.status == "refused"
        for member in decision.members
    }
    candidates = {
        binding.stage_id
        for binding in bindings
        if isinstance(binding.definition, PopulationProgram)
        and isinstance(binding.definition.root, PopulationProduct)
        and population_key(binding.definition) in adopted - refused
    }
    removable: set[str] = set()
    for stage in reversed(plan.stages):
        identifier = stage.stage_id
        if identifier in candidates and set(plan.consumers_of(identifier)) <= {
            consumer,
            *removable,
        }:
            removable.add(identifier)
    return frozenset(removable)
