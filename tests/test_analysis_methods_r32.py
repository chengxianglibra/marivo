"""Independent registration and qualification oracles, not backend acceptance."""

from __future__ import annotations

import sqlite3
from dataclasses import FrozenInstanceError, replace

import duckdb
import pytest

import marivo.semantic as ms
from marivo.analysis.core.model import (
    Binding,
    Coordinate,
    CoreRuleError,
    CoveragePart,
    Defined,
    DomainSignature,
    Null,
    ObservedQuantity,
    OriginalStatePart,
    Signature,
    StatisticalWeightPart,
    Undefined,
    Unknown,
)
from marivo.analysis.core.rules import (
    CellDerive,
    OriginalReduce,
    PartsTransport,
    RowState,
    derive,
    derive_numeric_cell,
)
from marivo.analysis.methods.errors import MethodRegistrationError
from marivo.analysis.methods.physical import (
    DecimalType,
    FixedShape,
    Implementation,
    NoTime,
    QualificationKey,
    Qualified,
    ResourceRequirements,
    ScalarType,
    SourceShape,
    TimeShape,
    Unavailable,
)
from marivo.analysis.methods.registry import REGISTRY, MethodRegistry
from marivo.analysis.methods.semantics import MethodKey, MethodSemantics


def _input(*, original: bool = False) -> Signature:
    binding = Binding("r32", "sales", "orders", "september")
    key = (Coordinate(ms.ref.entity("sales.customer"), "customer_id", "identity"),)
    domain = DomainSignature(binding, "entity", key, key, "customers")
    quantity = ObservedQuantity(
        "revenue",
        ms.ref.metric("sales.revenue"),
        "revenue-graph",
        "CNY",
        "september",
        "order-contribution",
        "strict",
        "sum@v1",
    )
    if not original:
        return Signature(domain, quantity)
    return Signature(
        domain,
        quantity,
        (
            OriginalStatePart(
                binding, "revenue", "sum@v1", "order-contribution", ("sum", "non_null_count"), "v1"
            ),
            CoveragePart(binding, "revenue", "september", "v1"),
        ),
    )


def _params(source: Signature) -> RowState:
    target = DomainSignature(source.domain.binding, "singleton", (), (), "all-customers")
    return RowState(
        "mean", target, "current-row-mean", "strict", numeric_check_id="source.finite_numeric@v1"
    )


def _implementation() -> Implementation:
    return Implementation(
        QualificationKey(
            MethodKey("row.mean"),
            (ScalarType("int64"),),
            ("entity",),
            SourceShape("duckdb", "table", "native", NoTime()),
            "ibis",
        ),
        ("source.finite_numeric@v1",),
        ("row_state",),
        "finite_float64",
        ResourceRequirements("stream", "producer", 1000),
        Qualified("test.mean", "test.consumer", "test-only-static-declaration"),
    )


def _registry(*implementations: Implementation) -> MethodRegistry:
    return MethodRegistry(
        (replace(REGISTRY.lookup(MethodKey("row.mean")), implementations=implementations),)
    )


def test_connected_methods_have_one_owner_per_rule() -> None:
    expected = {
        "bind_project@v1": "bind_project@v1",
        "metric.observe@v1": "bind_project@v1",
        "metric.count@v1": "bind_project@v1",
        "metric.weighted_mean@v1": "bind_project@v1",
        "metric.mean@v1": "bind_project@v1",
        "metric.fold@v1": "bind_project@v1",
        "state_rollup.fold@v1": "original_reduce@v1",
        "metric.sum_zero@v1": "bind_project@v1",
        "metric.ratio@v1": "original_reduce@v1",
        "metric.linear@v1": "occurrence_combine@v1",
        "map_correspond@v1": "map_correspond@v1",
        "cell.difference@v1": "cell_derive@v1",
        "cell.ratio@v1": "cell_derive@v1",
        "cell.relative_change@v1": "cell_derive@v1",
        "row.sum@v1": "row_state@v1",
        "row.mean@v1": "row_state@v1",
        "row.min@v1": "row_state@v1",
        "row.max@v1": "row_state@v1",
        "group.attach@v1": "parts_transport@v1",
        "time.product@v1": "parts_transport@v1",
        "group.complete@v1": "parts_transport@v1",
        "row.count@v1": "row_state@v1",
        "row.count_defined@v1": "row_state@v1",
        "row.weighted_mean@v1": "row_state@v1",
        "state_rollup@v1": "original_reduce@v1",
        "state_rollup.count@v1": "original_reduce@v1",
        "metric.min@v1": "bind_project@v1",
        "metric.max@v1": "bind_project@v1",
        "metric.distinct@v1": "bind_project@v1",
        "metric.approx_distinct@v1": "bind_project@v1",
        "metric.quantile@v1": "bind_project@v1",
        "metric.approx_quantile@v1": "bind_project@v1",
        "state_rollup.min@v1": "original_reduce@v1",
        "state_rollup.max@v1": "original_reduce@v1",
        "state_rollup.sum_zero@v1": "original_reduce@v1",
        "state_rollup.ratio@v1": "original_reduce@v1",
        "state_rollup.weighted_mean@v1": "original_reduce@v1",
        "state_rollup.mean@v1": "original_reduce@v1",
        "state_rollup.linear@v1": "original_reduce@v1",
        "parts_transport@v1": "parts_transport@v1",
        "domain.cohort@v1": "domain.cohort@v1",
        "reference.share@v1": "reference@v1",
        "reference.penetration@v1": "reference@v1",
        "reference.standardize@v1": "reference@v1",
        "display.rank@v1": "display@v1",
        "display.table@v1": "display@v1",
        "attribution.additive_difference@v1": "attribution@v1",
        "attribution.component_mix@v1": "attribution@v1",
        "association.spearman@v1": "association_score@v1",
        "journey.match@v1": "journey_match@v1",
        "journey.duration@v1": "journey_view@v1",
        "journey.completed@v1": "journey_view@v1",
        "journey.read@v1": "journey_view@v1",
        "funnel.entry_axes@v1": "funnel@v1",
        "funnel.reduce@v1": "funnel@v1",
        "funnel.compare@v1": "funnel@v1",
        "funnel.read@v1": "funnel@v1",
        "funnel_ratio_mix@v1": "funnel@v1",
        "occurrence.prepare@v1": "occurrence_prepare@v1",
        "history.replay@v1": "history_replay@v1",
    }
    assert {
        str(item.semantics.key): item.semantics.rule for item in REGISTRY.registrations
    } == expected
    assert all(item.semantics.owner == "analysis.core.rules" for item in REGISTRY.registrations)
    assert {item.semantics.key.name for item in REGISTRY.registrations if item.implementations} == {
        "journey.match",
        "journey.duration",
        "journey.completed",
        "journey.read",
        "funnel.entry_axes",
        "funnel.reduce",
        "funnel.compare",
        "funnel.read",
        "funnel_ratio_mix",
        "occurrence.prepare",
        "history.replay",
        "time.product",
        "cell.ratio",
        "cell.relative_change",
        "group.attach",
        "group.complete",
        "metric.min",
        "metric.max",
        "metric.distinct",
        "metric.approx_distinct",
        "metric.quantile",
        "metric.approx_quantile",
        "state_rollup.min",
        "state_rollup.max",
        "metric.mean",
        "metric.fold",
        "state_rollup.fold",
        "state_rollup.mean",
        "row.min",
        "row.max",
        "metric.observe",
        "metric.count",
        "metric.weighted_mean",
        "metric.sum_zero",
        "metric.ratio",
        "metric.linear",
        "state_rollup",
        "state_rollup.count",
        "state_rollup.sum_zero",
        "state_rollup.ratio",
        "state_rollup.weighted_mean",
        "state_rollup.linear",
        "bind_project",
        "parts_transport",
        "domain.cohort",
        "reference.share",
        "reference.penetration",
        "reference.standardize",
        "display.rank",
        "display.table",
        "attribution.additive_difference",
        "attribution.component_mix",
        "map_correspond",
        "cell.difference",
        "row.count",
        "row.count_defined",
        "row.sum",
        "row.mean",
        "association.spearman",
    }
    assert all(item.missing.status == "blocked" for item in REGISTRY.registrations)
    with pytest.raises(FrozenInstanceError):
        REGISTRY.registrations = ()


def test_duplicate_owner_and_conflicting_implementation_fail_at_assembly() -> None:
    registration = REGISTRY.lookup(MethodKey("row.mean"))
    with pytest.raises(MethodRegistrationError, match="one semantic owner"):
        MethodRegistry((registration, registration))
    candidate = _implementation()
    with pytest.raises(MethodRegistrationError, match="one implementation per exact"):
        _registry(
            candidate,
            replace(candidate, qualification=Unavailable("unverified", "no evidence", "qualify")),
        )
    with pytest.raises(MethodRegistrationError, match="Attach the implementation"):
        replace(
            registration,
            implementations=(
                replace(candidate, key=replace(candidate.key, method=MethodKey("row.sum"))),
            ),
        )


@pytest.mark.parametrize(
    "name,version",
    [
        ("md.raw_sql", 1),
        ("raw_sql_terminal", 1),
        ("dsl.j1.observe_sum", 1),
        ("missing", 1),
        ("row.mean", 2),
        ("row.mean", True),
    ],
)
def test_unknown_and_terminal_methods_do_not_enter_analysis(name: str, version: int) -> None:
    with pytest.raises(MethodRegistrationError, match="connected Analysis method"):
        MethodKey(name, version)


def test_missing_owner_invalid_parameter_and_unknown_registry_entry_refuse() -> None:
    with pytest.raises(MethodRegistrationError, match="semantic owner"):
        MethodSemantics(MethodKey("row.mean"), "adapter")
    with pytest.raises(MethodRegistrationError, match="immutable complete"):
        MethodRegistry([])
    with pytest.raises(MethodRegistrationError, match="registered method version"):
        MethodRegistry(()).lookup(MethodKey("row.mean"))
    source = _input()
    with pytest.raises(MethodRegistrationError, match="Use this method's parameters"):
        REGISTRY.lookup(MethodKey("row.count")).semantics.derive((source,), _params(source))
    with pytest.raises(MethodRegistrationError, match="closed method parameters"):
        derive((source,), object())
    with pytest.raises(MethodRegistrationError, match="exact Signature"):
        derive((object(),), _params(source))


@pytest.mark.parametrize("field", ["checks", "parts"])
def test_incomplete_implementation_refuses_at_assembly(field: str) -> None:
    candidate = _implementation()
    broken = replace(candidate, checks=()) if field == "checks" else replace(candidate, parts=())
    with pytest.raises(MethodRegistrationError, match="every"):
        _registry(broken)


@pytest.mark.parametrize("status", ["unsupported", "unverified", "blocked"])
def test_unavailable_exact_cells_remain_distinct(status: str) -> None:
    candidate = replace(
        _implementation(),
        qualification=Unavailable(status, "missing oracle", "Run the exact independent oracle."),
    )
    source = _input()
    with pytest.raises(MethodRegistrationError, match=status) as caught:
        _registry(candidate).select(candidate.key, (source,), _params(source))
    assert caught.value.expected and caught.value.received
    assert caught.value.repair.action == "Run the exact independent oracle."


@pytest.mark.parametrize(
    "shape",
    [
        SourceShape("postgres", "table", "native", NoTime()),
        SourceShape("duckdb", "parquet", "native", NoTime()),
        SourceShape("duckdb", "table", "view", NoTime()),
        SourceShape("duckdb", "table", "native", TimeShape("calendar", "day", "America/New_York")),
        SourceShape("duckdb", "table", "native", TimeShape("elapsed", "s", "UTC")),
    ],
)
def test_physical_shapes_never_borrow_a_qualification(shape: SourceShape) -> None:
    candidate = _implementation()
    source = _input()
    with pytest.raises(MethodRegistrationError, match="qualified exact key"):
        _registry(candidate).select(replace(candidate.key, shape=shape), (source,), _params(source))


def test_decimal_precision_and_scale_are_part_of_the_key() -> None:
    candidate = _implementation()
    candidate = replace(
        candidate, key=replace(candidate.key, input_types=(DecimalType(18, 2),)), precision="exact"
    )
    source = _input()
    selected = _registry(candidate).select(candidate.key, (source,), _params(source))
    assert selected.implementation is candidate
    for decimal in (DecimalType(19, 2), DecimalType(18, 3)):
        with pytest.raises(MethodRegistrationError, match="qualified exact key"):
            _registry(candidate).select(
                replace(candidate.key, input_types=(decimal,)), (source,), _params(source)
            )
    with pytest.raises(MethodRegistrationError, match="exact Decimal"):
        replace(candidate, precision="finite_float64")


def test_wrong_type_route_resource_and_check_declarations_refuse() -> None:
    candidate = _implementation()
    with pytest.raises(MethodRegistrationError, match="scalar type"):
        ScalarType("unknown")
    with pytest.raises(MethodRegistrationError, match="Decimal precision"):
        DecimalType(4, 5)
    with pytest.raises(MethodRegistrationError, match="positive optional row limit"):
        ResourceRequirements("complete", "caller", 0)
    with pytest.raises(MethodRegistrationError, match="consumer and qualification evidence"):
        Qualified("example", "", "evidence")
    with pytest.raises(MethodRegistrationError, match="complete immutable implementation"):
        replace(candidate, checks=("source.not_a_checker@v1",))
    with pytest.raises(MethodRegistrationError, match="complete exact"):
        replace(candidate.key, shape=FixedShape(NoTime()))
    with pytest.raises(MethodRegistrationError, match="complete exact"):
        replace(candidate.key, route="raw_sql")
    with pytest.raises(MethodRegistrationError, match="producer-owned"):
        replace(candidate, resources=ResourceRequirements("stream", "caller", None))


def test_physical_input_positions_match_the_connected_rule() -> None:
    candidate = _implementation()
    with pytest.raises(MethodRegistrationError, match="complete exact"):
        replace(
            candidate.key,
            input_types=(ScalarType("int64"), ScalarType("float64")),
        )
    for method in ("bind_project",):
        key = replace(
            candidate.key,
            method=MethodKey(method),
            input_types=(ScalarType("int64"), ScalarType("int64")),
            input_domains=("entity", "entity"),
        )
        implementation = replace(candidate, key=key, checks=(), parts=())
        with pytest.raises(MethodRegistrationError, match="one ordered input"):
            replace(REGISTRY.lookup(MethodKey(method)), implementations=(implementation,))
    key = replace(
        candidate.key,
        method=MethodKey("map_correspond"),
        input_types=(ScalarType("int64"),) * 3,
        input_domains=("entity",) * 3,
    )
    with pytest.raises(MethodRegistrationError, match="one or two ordered"):
        replace(
            REGISTRY.lookup(MethodKey("map_correspond")),
            implementations=(replace(candidate, key=key, checks=(), parts=()),),
        )


def test_numeric_precision_matches_method_result_and_input_types() -> None:
    candidate = _implementation()
    for input_type, precision in (
        (ScalarType("int64"), "checked_int64"),
        (ScalarType("float64"), "checked_int64"),
    ):
        implementation = replace(
            candidate,
            key=replace(candidate.key, input_types=(input_type,)),
            precision=precision,
        )
        with pytest.raises(MethodRegistrationError, match="finite_float64 precision"):
            _registry(implementation)
    count_key = replace(candidate.key, method=MethodKey("row.count"))
    count = replace(candidate, key=count_key, checks=(), precision="checked_int64")
    registration = REGISTRY.lookup(MethodKey("row.count"))
    MethodRegistry((replace(registration, implementations=(count,)),))
    for precision in ("exact", "finite_float64"):
        with pytest.raises(MethodRegistrationError, match="checked_int64 precision"):
            MethodRegistry(
                (replace(registration, implementations=(replace(count, precision=precision),)),)
            )
    decimal_count = replace(count, key=replace(count_key, input_types=(DecimalType(18, 2),)))
    MethodRegistry((replace(registration, implementations=(decimal_count,)),))


def test_route_selection_preserves_semantics_and_never_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _input()
    params = _params(source)
    ibis = _implementation()
    python = replace(ibis, key=replace(ibis.key, route="ibis_python"))
    fixed = replace(
        ibis,
        key=replace(ibis.key, route="artifact_python", shape=FixedShape(NoTime())),
        resources=ResourceRequirements("complete", "caller", 1000),
    )
    registry = _registry(ibis, python, fixed)
    expected = derive((source,), params)
    for candidate in (ibis, python, fixed):
        chosen = registry.select(candidate.key, (source,), params)
        assert chosen.implementation is candidate
        assert chosen.derivation == expected
        assert chosen.derivation.output.quantity.method_version == "row.mean@v1"
        assert chosen.derivation.output.quantity.unit == "CNY"
        assert chosen.derivation.output.parts[0].components == ("sum", "count")
        assert chosen.derivation.obligations
    with pytest.raises(MethodRegistrationError, match="qualified exact key"):
        _registry(python).select(ibis.key, (source,), params)
    blocked = replace(
        ibis, qualification=Unavailable("blocked", "failed qualification", "Repair this route.")
    )
    with pytest.raises(MethodRegistrationError, match="failed qualification"):
        _registry(blocked, python).select(ibis.key, (source,), params)
    from marivo.analysis.core import rules

    calls = []

    def fail(*args: object) -> None:
        calls.append(args)
        raise RuntimeError("semantic consumer failed")

    monkeypatch.setattr(rules, "_row_state", fail)
    with pytest.raises(RuntimeError, match="semantic consumer failed"):
        registry.select(ibis.key, (source,), params)
    assert len(calls) == 1


def test_cell_semantics_are_not_adapter_options() -> None:
    assert derive_numeric_cell("difference", Defined(5), Defined(2)) == Defined(3)
    assert derive_numeric_cell("ratio", Defined(5), Defined(0)) == Undefined("zero_denominator")
    for cell in (Null("empty_contribution"), Undefined("zero_denominator"), Unknown("incomplete")):
        with pytest.raises(CoreRuleError, match="two Defined"):
            derive_numeric_cell("difference", cell, Defined(2))
    source = _input()
    parameters = CellDerive(
        "ratio",
        "ratio",
        "strict",
        "1",
        "september",
        "source.exact_pairing@v1",
        "source.finite_numeric@v1",
    )
    derived = derive((source, source), parameters)
    assert derived.output.quantity.unit == "1"
    assert derived.output.quantity.method_version == "cell.ratio@v1"
    assert len(derived.obligations) == 2


def test_conditional_k_loses_original_rollup_when_parts_are_removed() -> None:
    source = _input(original=True)
    names = lambda value: {str(item.method) for item in REGISTRY.continuations(value)}
    assert "state_rollup@v1" in names(source)
    transported = derive((source,), PartsTransport("projection", source.domain, (), True))
    assert "state_rollup@v1" not in names(transported.output)
    row = derive((source,), _params(source))
    assert "state_rollup@v1" not in names(row.output)
    with pytest.raises(CoreRuleError, match="original Metric quantity"):
        derive((row.output,), OriginalReduce(row.output.domain))
    assert "row.count@v1" in names(row.output)
    assert "row.weighted_mean@v1" not in names(source)


def test_semantic_policy_and_numeric_types_cannot_be_overridden_by_qualification() -> None:
    candidate = _implementation()
    source = _input()
    for scalar in ("string", "boolean", "timestamp"):
        with pytest.raises(MethodRegistrationError, match="numeric inputs admitted"):
            _registry(
                replace(candidate, key=replace(candidate.key, input_types=(ScalarType(scalar),)))
            )
    with pytest.raises(MethodRegistrationError, match="complete exact"):
        _registry(
            replace(
                candidate,
                key=replace(
                    candidate.key, input_types=(ScalarType("int64"), ScalarType("float64"))
                ),
            )
        )
    with pytest.raises(MethodRegistrationError, match="Cell policy"):
        _registry(candidate).select(
            candidate.key, (source,), replace(_params(source), value_policy="drop_unknown")
        )
    with pytest.raises(MethodRegistrationError, match="ordered input domains"):
        _registry(replace(candidate, key=replace(candidate.key, input_domains=("group",)))).select(
            replace(candidate.key, input_domains=("group",)), (source,), _params(source)
        )


def test_conditional_k_requires_exact_state_and_coverage_bindings() -> None:
    source = _input(original=True)
    state, coverage = source.parts
    for parts in (
        (replace(state, contribution_id="another-contribution"), coverage),
        (replace(state, method_version="mean@v1"), coverage),
        (state, replace(coverage, scope_id="august")),
    ):
        methods = {item.method for item in REGISTRY.continuations(replace(source, parts=parts))}
        assert MethodKey("state_rollup") not in methods
    assert MethodRegistry(()).continuations(source) == ()


def test_invocation_specific_parts_must_be_supported() -> None:
    source = _input(original=True)
    params = PartsTransport("projection", source.domain, ("original_state", "coverage"), True)
    candidate = _implementation()
    candidate = replace(
        candidate,
        key=replace(candidate.key, method=MethodKey("parts_transport")),
        checks=(),
        parts=(),
    )
    registration = replace(
        REGISTRY.lookup(MethodKey("parts_transport")), implementations=(candidate,)
    )
    with pytest.raises(MethodRegistrationError, match="all bound checks and required/output parts"):
        MethodRegistry((registration,)).select(candidate.key, (source,), params)


def test_stale_statistical_weights_cannot_enable_a_method() -> None:
    source = _input()
    weight = StatisticalWeightPart(
        replace(source.domain.binding, scope_id="august"), "weight", "1", "v1"
    )
    source = replace(source, parts=(weight,))
    assert MethodKey("row.weighted_mean") not in {
        item.method for item in REGISTRY.continuations(source)
    }
    params = replace(_params(source), method="weighted_mean", weighting="weight")
    with pytest.raises(MethodRegistrationError, match="weights bound to the exact input"):
        derive((source,), params)


def test_registry_and_core_consumer_use_no_io_or_legacy_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marivo.analysis.datasets.registry import DatasetFamilyRegistration
    from marivo.analysis.materialization.admission import DatasetRuntime
    from marivo.analysis.materialization.store import SessionStore
    from marivo.analysis.operators import registry
    from marivo.datasource.adapters import SourceSession

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("registration touched I/O or a legacy method registry")

    for owner, name in (
        (duckdb, "connect"),
        (sqlite3, "connect"),
        (SourceSession, "__init__"),
        (SessionStore, "__init__"),
        (SessionStore, "open_existing"),
        (SessionStore, "run"),
        (SessionStore, "artifact"),
        (DatasetRuntime, "__init__"),
        (DatasetFamilyRegistration, "__post_init__"),
        (registry, "implementation"),
    ):
        monkeypatch.setattr(owner, name, forbidden)
    source = _input()
    candidate = _implementation()
    chosen = _registry(candidate).select(candidate.key, (source,), _params(source))
    assert chosen.derivation == derive((source,), _params(source))
    assert (
        REGISTRY.select(candidate.key, (source,), _params(source)).derivation == chosen.derivation
    )
    # Prove the existing core consumer consults the new owner, with no local fallback.
    import marivo.analysis.methods.registry as method_registry

    monkeypatch.setattr(method_registry, "REGISTRY", MethodRegistry(()))
    with pytest.raises(MethodRegistrationError, match="registered method version"):
        derive((source,), _params(source))


@pytest.mark.parametrize("name", ["parts_transport", "group.attach", "group.complete"])
@pytest.mark.parametrize("arity", [1, 2, 3])
def test_transport_and_group_methods_enforce_ordered_arity(name: str, arity: int) -> None:
    registration = REGISTRY.lookup(MethodKey(name))
    candidate = registration.implementations[0]
    key = replace(
        candidate.key,
        input_types=(ScalarType("int64"),) * arity,
        input_domains=("entity",) * arity,
    )
    implementation = replace(candidate, key=key)
    if arity == 2 or (name == "parts_transport" and arity == 1):
        accepted = replace(registration, implementations=(implementation,))
        assert accepted.implementations == (implementation,)
    else:
        with pytest.raises(MethodRegistrationError, match="ordered"):
            replace(registration, implementations=(implementation,))
