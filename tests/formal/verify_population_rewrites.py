"""Prove exact product, relationship round-trip and scalar graph premises."""

from itertools import product
from typing import Literal

import pytest
import z3  # type: ignore[import-not-found, import-untyped, unused-ignore]  # Optional formal solver.

from marivo._definition.correspondence import ComparisonAxis
from marivo._definition.relations import (
    LiteralPopulation,
    PopulationProduct,
    PopulationProgram,
)
from marivo._definition.types import AxisBinding, RelationshipParameter
from marivo._kernel.refs import ExactRef, ref
from marivo.analysis.compiler.population_factorization import independent_product_groups
from tests.formal._summary_monoids import result


def _relationship(
    name: str,
    start: ExactRef,
    target: ExactRef,
    cardinality: Literal["many_to_one", "one_to_many", "many_to_many"],
) -> RelationshipParameter:
    return RelationshipParameter(
        ExactRef(ref.relationship(f"proof.{name}"), "proof"),
        start,
        target,
        cardinality,
    )


def _identity_factor(*path: RelationshipParameter) -> LiteralPopulation:
    axis = AxisBinding(
        path[-1].to_entity,
        None,
        tuple(step.ref for step in path),
        "identity",
        path_root=path[0].from_entity,
        path_facts=path,
    )
    return LiteralPopulation((axis,), (((1,),),), "proof")


@pytest.mark.parametrize("topology", ("independent", "shared_fanout", "shared_scalar"))
def test_independent_witness_fibers_distribute_over_product(topology: str) -> None:
    fact, first, second, other = (
        ExactRef(ref.entity(f"proof.{name}"), "proof")
        for name in ("fact", "first", "second", "other")
    )
    lead = _relationship(
        "lead", fact, first, "many_to_one" if topology == "shared_scalar" else "one_to_many"
    )
    tail = _relationship("tail", first, second, "many_to_many")
    branch = _relationship(
        "branch", first if topology != "independent" else fact, other, "one_to_many"
    )
    left_path = (lead, tail)
    right_path = (branch,) if topology == "independent" else (lead, branch)
    left, right = _identity_factor(*left_path), _identity_factor(*right_path)
    axes = (*left.axes, *right.axes)
    definition = PopulationProgram(PopulationProduct((left, right)), None, axes)
    groups = independent_product_groups(definition, {axis: axis.as_read() for axis in axes})

    # A shared multivalued prefix uses one occurrence index for both leaves.
    # A shared scalar prefix has one row, with independent choices at its two
    # outgoing multivalued edges. Independent paths have two occurrence indices.
    shared = topology == "shared_fanout"
    selections = (
        z3.Bools("left0 left1"),
        z3.Bools("right0 right1"),
    )
    assignments = ((0, 0), (1, 1)) if shared else tuple(product(range(2), repeat=2))
    original = z3.Or(
        *(
            z3.And(*(predicates[i] for predicates, i in zip(selections, indices, strict=True)))
            for indices in assignments
        )
    )
    declared: dict[ComparisonAxis, int] = {axis: index for index, axis in enumerate(axes)}
    groups_required = tuple(tuple(declared[axis] for axis in group.axes) for group in groups)
    rewritten = z3.And(
        *(
            z3.Or(
                *(
                    z3.And(*(selections[index][row[index]] for index in group))
                    for row in assignments
                )
            )
            for group in groups_required
        )
    )
    result(
        "The compiler partition preserves joint membership over the declared path occurrences",
        original == rewritten,
        logic="QF_UF",
    )
    assert groups_required == (((0, 1),) if shared else ((0,), (1,)))
    if shared:
        isolated = z3.And(*(z3.Or(*predicates) for predicates in selections))
        result(
            "A shared fanout can place the two selected fields on different occurrence rows",
            original == isolated,
            selections[0][0],
            z3.Not(selections[0][1]),
            z3.Not(selections[1][0]),
            selections[1][1],
            logic="QF_UF",
            counterexample=True,
        )


def test_preimage_of_same_image_requires_full_left_support() -> None:
    selected = z3.Bools("selected0 selected1")
    cells = (z3.Bools("edge00 edge01"), z3.Bools("edge10 edge11"))
    image = tuple(z3.Or(*(z3.And(selected[i], cells[i][j]) for i in range(2))) for j in range(2))
    round_trip = tuple(
        z3.And(selected[i], z3.Or(*(z3.And(cells[i][j], image[j]) for j in range(2))))
        for i in range(2)
    )
    parity = z3.And(*(a == b for a, b in zip(round_trip, selected, strict=True)))
    total = z3.And(
        *(z3.Implies(member, z3.Or(*edges)) for member, edges in zip(selected, cells, strict=True))
    )
    result("Exact left-total condition for H ∩ preimage(image(H))", parity == total, logic="QF_UF")
    result(
        "A surviving row with a nullable or unbound foreign key is a counterexample",
        parity,
        selected[0],
        z3.Not(cells[0][0]),
        z3.Not(cells[0][1]),
        logic="QF_UF",
        counterexample=True,
    )


def test_scalar_graph_extension_commutes_but_identity_check_can_fail_on_dropped_row() -> None:
    keep, found, identity = z3.Bools("keep found identity")
    original = z3.And(identity, z3.Not(found))
    delayed = z3.And(keep, identity, z3.Not(found))
    # An unchanged predicate selects the same old coordinate and its fixed
    # nullable attribute. Only the identity lookup can add a failure here.
    payload = z3.Int("scalar_payload")
    cell_before = z3.If(keep, z3.If(found, payload, -1), -2)
    cell_after = z3.If(keep & found, payload, z3.If(keep, -1, -2))
    result(
        "Direct scalar graph extension preserves row support, value, and success",
        z3.And(original == delayed, cell_before == cell_after),
        z3.Not(identity),
        payload >= 0,
        logic="QF_LIA",
    )
    result(
        "A missing identity lookup cannot be hidden by filtering away its source key",
        original == delayed,
        identity,
        z3.Not(keep),
        z3.Not(found),
        logic="QF_UF",
        counterexample=True,
    )


def test_empty_product_does_not_remove_an_independent_check() -> None:
    first, second, check = z3.Bools("first_present second_present check_failed")
    retained = z3.Or(check, z3.And(first, second))
    shortcut = z3.And(first, second)
    result(
        "An empty second factor annihilates rows while retaining the first factor's check",
        retained == shortcut,
        z3.Not(second),
        check,
        logic="QF_UF",
        counterexample=True,
    )
