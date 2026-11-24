"""Verify L7 group restriction and compare its coverage with independent evaluation."""

import duckdb
import pytest

import marivo.analysis as mv
from marivo.analysis.model.optimization_policy import OptimizationPolicy
from tests.analysis.materialization.test_key_predicate_pushdown import _decisions
from tests.analysis.materialization.test_query_optimization import (
    optimization_session as optimization_session,
)
from tests.formal._smt import prove_unsat
from tests.formal.reference.equivalence import assert_equivalent, assert_policies_agree, run_frame
from tests.formal.reference.evaluator import fix, observe
from tests.formal.reference.model import Bag, Branch, Key, Occurrence, Read, Reducer, Scope
from tests.shared_fixtures import catalog_value


@pytest.mark.parametrize("operation", ("where_eq", "where_null", "fix"))
def test_ordinary_key_pushdown_preserves_support_validity_and_group_sum(operation: str) -> None:
    """Prove grouped outcomes for four arbitrary present or absent contributions."""

    def local_keeps(key: str) -> str:
        if operation == "where_eq":
            return f"(and (distinct {key} 2) (distinct selected 2) (= {key} selected))"
        if operation == "where_null":
            return f"(= {key} 2)"
        # One NULL token models the null-safe equality used by local Fix.
        return f"(= {key} selected)"

    def native_truth(key: str) -> str:
        if operation == "where_eq":
            # SQL UNKNOWN (-1) and false (0) are both discarded by WHERE.
            return f"(ite (or (= {key} 2) (= selected 2)) -1 (ite (= {key} selected) 1 0))"
        if operation == "where_null":
            return f"(ite (= {key} 2) 1 0)"
        return f"(ite (= {key} 2) (ite (= selected 2) 1 0) (ite (= {key} selected) 1 0))"

    clauses = [
        "(declare-const group_key Int)",
        "(declare-const category Int)",
        "(declare-const selected Int)",
        "(assert (and (<= 0 group_key) (<= group_key 2)))",
        "(assert (and (<= 0 selected) (<= selected 2)))",
        "(assert (and (<= 0 category) (<= category 1)))",
    ]
    baseline: dict[str, list[str]] = {name: [] for name in ("support", "valid", "sum")}
    pushed: dict[str, list[str]] = {name: [] for name in baseline}
    inputs: list[str] = []
    for position in range(4):
        key, tag, present, valid, amount = (
            f"{stem}_{position}" for stem in ("key", "tag", "present", "valid", "amount")
        )
        inputs.extend((key, tag, present, valid, amount))
        clauses.extend(
            (
                f"(declare-const {key} Int)",
                f"(declare-const {tag} Int)",
                f"(declare-const {present} Bool)",
                f"(declare-const {valid} Bool)",
                f"(declare-const {amount} Int)",
                f"(assert (and (<= 0 {key}) (<= {key} 2)))",
                f"(assert (and (<= 0 {tag}) (<= {tag} 1)))",
                f"(assert (and (<= -2 {amount}) (<= {amount} 2)))",
            )
        )
        belongs = f"(and {present} (= {key} group_key) (= {tag} category))"
        baseline["support"].append(f"(ite {belongs} 1 0)")
        baseline["valid"].append(f"(ite (and {belongs} {valid}) 1 0)")
        baseline["sum"].append(f"(ite (and {belongs} {valid}) {amount} 0)")
        retained = f"(and (= {native_truth(key)} 1) {belongs})"
        pushed["support"].append(f"(ite {retained} 1 0)")
        pushed["valid"].append(f"(ite (and {retained} {valid}) 1 0)")
        pushed["sum"].append(f"(ite (and {retained} {valid}) {amount} 0)")

    differences: list[str] = []
    for name, terms in baseline.items():
        serial = f"(ite {local_keeps('group_key')} (+ {' '.join(terms)}) 0)"
        native = f"(+ {' '.join(pushed[name])})"
        clauses.append(f"(define-fun serial_{name} () Int {serial})")
        clauses.append(f"(define-fun native_{name} () Int {native})")
        differences.append(f"(distinct serial_{name} native_{name})")
    clauses.append(f"(assert (or {' '.join(differences)}))")
    prove_unsat(
        "\n".join(clauses),
        claim="L7 preserves support, all-NA groups and exact bounded sums",
        witness=("group_key", "category", "selected", *inputs),
    )


@pytest.mark.parametrize("covered", (True, False), ids=("output-only", "excluded-witness"))
def test_cropped_literal_publication_retains_full_domain_coverage(
    optimization_session: mv.Session, covered: bool
) -> None:
    facts: dict[Key, Bag] = {
        ("A", "x"): (Occurrence((1,), 10),),
        ("A", "y"): (Occurrence((2,), 30),),
        ("B", "x"): (Occurrence((3,), 20),),
        (None, "z"): (Occurrence((4,), None),),
    }
    supplied: tuple[tuple[str | None, str], ...] = (
        ("A", "empty"),
        ("A", "x"),
        ("A", "y"),
        (None, "z"),
    )
    if covered:
        supplied = (*supplied, ("B", "x"))
    expected = fix(
        observe(
            {"minimum": Branch(Reducer("min"), facts)},
            {"value": Read("minimum")},
            Scope("explicit", supplied),
        ),
        0,
        "A",
    )
    session = optimization_session
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="measure")
    region = catalog_value(session.catalog, "sales.orders.region", kind="dimension")
    detail = catalog_value(session.catalog, "sales.orders.detail", kind="dimension")
    domain = mv.coordinates(by=(region, detail), keys=supplied)
    frame = session.observe({"value": amount.min()}, by=domain)
    query = frame.fix(frame.axes[0], "A")
    assert any(decision.status == "adopted" for decision in _decisions(query))

    default = run_frame(query, OptimizationPolicy.default())
    disabled = run_frame(query, OptimizationPolicy.disabled())
    assert_equivalent(expected, default)
    assert_equivalent(expected, disabled)
    assert_policies_agree(disabled, default)


def test_cropped_source_population_retains_unselected_coverage_witness(
    optimization_session: mv.Session,
) -> None:
    session = optimization_session
    with duckdb.connect(str(session.project_root / "optimization.duckdb")) as connection:  # noqa: TID251
        connection.execute("ALTER TABLE orders ALTER COLUMN id SET NOT NULL")
    facts: dict[Key, Bag] = {
        (1,): (Occurrence((1,), 10),),
        (2,): (Occurrence((2,), 30),),
        (3,): (Occurrence((3,), 20),),
        (4,): (Occurrence((4,), None),),
    }
    expected = fix(
        observe(
            {"minimum": Branch(Reducer("min"), facts)},
            {"value": Read("minimum")},
            Scope("explicit", ((1,), (2,))),
        ),
        0,
        1,
    )
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="measure")
    entity = catalog_value(session.catalog, "sales.orders", kind="entity")
    members = session.members(entity, where=amount != 20)
    frame = session.observe({"value": amount.min()}, by=members)
    query = frame.fix(frame.axes[0], 1)
    assert any(decision.status == "adopted" for decision in _decisions(query))

    default = run_frame(query, OptimizationPolicy.default())
    disabled = run_frame(query, OptimizationPolicy.disabled())
    assert_equivalent(expected, default)
    assert_equivalent(expected, disabled)
    assert_policies_agree(disabled, default)


def test_ordinary_window_selection_restricts_independent_support_as_well() -> None:
    source = """
    (declare-const dated Bool)
    (declare-const independent Bool)
    (declare-const kept Bool)
    (define-fun original () Bool (and kept (or dated independent)))
    (define-fun reduced () Bool (or (and kept dated) (and kept independent)))
    (assert (distinct original reduced))
    """
    prove_unsat(
        source,
        claim="Ordinary selection distributes over clocked and independent support",
        witness=("dated", "independent", "kept"),
    )
