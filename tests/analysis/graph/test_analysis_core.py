"""Independent fixed-input checks for the private algebra boundary."""

from __future__ import annotations

import sqlite3
from dataclasses import replace

import duckdb
import pytest

import marivo.semantic as ms
from marivo.analysis.core.model import (
    Binding,
    Coordinate,
    CoreRuleError,
    CorrespondencePart,
    CoveragePart,
    Defined,
    DomainSignature,
    EndpointPart,
    Evidence,
    Fact,
    FactInput,
    FixedReferencePart,
    Null,
    Obligation,
    ObservedQuantity,
    OriginalStatePart,
    RowStatePart,
    Signature,
    StatisticalWeightPart,
    SubjectPart,
    Undefined,
    Unknown,
    available_facts,
    require_part,
)
from marivo.analysis.core.rules import (
    BindProject,
    CellDerive,
    MapCorrespond,
    OriginalReduce,
    PartsTransport,
    RowState,
    TransportMode,
    derive,
    derive_numeric_cell,
    entity_members,
    pair_coordinates,
    subjects_image,
    union_full_tuples,
)
from marivo.refs import RefPayloadV1
from marivo.semantic.ir import TargetSnapshotSelection, TargetSnapshotVersion
from marivo.semantic.metric_graph_lowering import normalize_target_metric
from marivo.semantic.validator import (
    normalize_target_dimension,
    normalize_target_entity,
    normalize_target_relationship,
)
from tests.shared_fixtures import DslCaseFactory


def _binding(scope: str = "august") -> Binding:
    return Binding("session-r31", "sales-owner", "source-a", scope)


def _domain(binding: Binding | None = None) -> DomainSignature:
    bound = binding or _binding()
    entity = ms.ref.entity("sales.customer")
    key = (
        Coordinate(entity, "tenant_id", "identity"),
        Coordinate(entity, "customer_id", "identity"),
    )
    return DomainSignature(bound, "entity", key, key, "customers")


def _quantity(name: str = "revenue") -> ObservedQuantity:
    return ObservedQuantity(
        f"metric:{name}",
        ms.ref.metric(f"sales.{name}"),
        f"graph:{name}",
        "CNY",
        "august",
        "order_contribution",
        "strict",
        "sum@v1",
    )


def _evidence(kind: str, binding: Binding, subject: str) -> Evidence:
    return Evidence(Fact(kind, binding, subject, "v1"), "check", "fixed-input-oracle")


def _observed(*, state: bool = False, coverage: bool = False) -> Signature:
    domain = _domain()
    quantity = _quantity()
    parts = []
    if state:
        parts.append(
            OriginalStatePart(
                domain.binding,
                quantity.definition_id,
                quantity.method_version,
                quantity.contribution_id,
                ("sum", "non_null_count"),
                "v1",
            )
        )
    if coverage:
        parts.append(
            CoveragePart(domain.binding, quantity.definition_id, domain.binding.scope_id, "v1")
        )
    return Signature(domain, quantity, tuple(parts))


def test_members_use_complete_declared_key_and_exact_version_without_io(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = analysis_dsl_case_factory("j1")
    entity_ref = ms.ref.entity(f"{case.names.domain}.{case.names.customer}")
    original = normalize_target_entity(case.catalog._state.registry, entity_ref.path)
    version_ref = ms.ref.time_dimension(f"{entity_ref.path}.snapshot_day")
    version = TargetSnapshotVersion(
        RefPayloadV1.from_ref(version_ref),
        "snapshot_day",
        "date",
        "UTC",
        None,
    )
    contract = replace(
        original,
        primary_key=("tenant_id", "customer_id"),
        identity_signature=(("tenant_id", "string"), ("customer_id", "string")),
        version_row_key=("tenant_id", "customer_id", "snapshot_day"),
        columns=(("tenant_id", "string"), ("customer_id", "string"), ("snapshot_day", "date")),
        version=version,
    )
    selected = TargetSnapshotSelection(version.coordinate_ref, "2026-08-01", "instant")

    def forbidden_connect(*args: object, **kwargs: object) -> None:
        raise AssertionError("construction opened a business source")

    monkeypatch.setattr(duckdb, "connect", forbidden_connect)
    with pytest.raises(CoreRuleError, match="explicit version selection"):
        entity_members(contract, entity_ref, _binding())
    wrong = TargetSnapshotSelection(
        RefPayloadV1.from_ref(ms.ref.time_dimension(f"{entity_ref.path}.other_day")),
        "2026-08-01",
        "instant",
    )
    with pytest.raises(CoreRuleError, match="matching snapshot selection"):
        entity_members(contract, entity_ref, _binding(), version_selection=wrong)
    root = entity_members(contract, entity_ref, _binding(), version_selection=selected)
    assert tuple(item.field for item in root.domain.instance_key) == ("tenant_id", "customer_id")
    assert root.domain.version_selection == selected
    assert root.domain.target_key == root.domain.instance_key
    assert root.parts[0].injective is True
    assert root.evidence[0].basis == "declaration"
    assert root.evidence[0].fact.kind == "declared_key"
    assert root.obligations == ()
    assert root.evidence[1].basis == "declaration"
    assert root.evidence[1].fact.kind == "unique_key"
    assert root.evidence[1].fact in available_facts(root)
    with pytest.raises(CoreRuleError, match="complete normalized identity"):
        entity_members(
            replace(contract, identity_signature=(("customer_id", "string"),)),
            entity_ref,
            _binding(),
            version_selection=selected,
        )


def test_bind_project_checks_exact_ref_owner_path_and_bound_quantity(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    owner_ref = ms.ref.entity(f"{case.names.domain}.{case.names.customer}")
    source = Signature(_domain())
    entity = ms.ref.entity("sales.customer")
    region = ms.ref.dimension("sales.customer.region")
    field = normalize_target_dimension(case.catalog._state.registry, region.path)
    direct = BindProject(region, entity, field, None, (), ())
    result = derive((source,), direct)
    assert result.rule == "bind_project@v1"
    assert result.output.domain == source.domain
    assert result.output.quantity is None
    assert result.pre[0] in available_facts(result.output)
    assert result.output.evidence[0].basis == "builder"
    assert result.post[0] in available_facts(result.output)
    with pytest.raises(CoreRuleError, match="normalized owning definition"):
        derive(
            (source,),
            replace(
                direct,
                field_contract=replace(
                    field,
                    entity_ref=normalize_target_entity(
                        case.catalog._state.registry, f"{case.names.domain}.{case.names.order}"
                    ).ref,
                ),
            ),
        )
    with pytest.raises(CoreRuleError, match="field or Metric Ref"):
        derive((source,), replace(direct, ref=entity))
    buyer = ms.ref.relationship(f"{case.names.domain}.{case.names.buyer}")
    with pytest.raises(CoreRuleError, match="complete directed Relationship path"):
        derive((source,), replace(direct, path=(buyer,)))

    metric = ms.ref.metric("sales.revenue")
    metric_contract = normalize_target_metric(
        case.catalog._state.registry, metric.path, sidecar=case.catalog._state.sidecar
    )
    quantity = replace(_quantity(), graph_fingerprint=metric_contract.bound_graph_fingerprint)
    order_ref = ms.ref.entity(f"{case.names.domain}.{case.names.order}")
    order_key = (Coordinate(order_ref, case.names.order_id, "identity"),)
    order_domain = DomainSignature(_binding(), "entity", order_key, order_key, "orders")
    order_source = Signature(order_domain)
    buyer_contract = normalize_target_relationship(case.catalog._state.registry, buyer.path)
    via_buyer = derive(
        (order_source,),
        replace(direct, path=(buyer,), path_contracts=(buyer_contract,)),
    )
    assert via_buyer.obligations[0].fact.kind == "mapping_total"
    with pytest.raises(CoreRuleError, match="directed single-valued"):
        derive(
            (source,),
            replace(direct, path=(buyer,), path_contracts=(buyer_contract,)),
        )
    projected = derive(
        (order_source,),
        replace(
            direct,
            ref=metric,
            field_owner=order_ref,
            field_contract=None,
            metric_contract=metric_contract,
            quantity=quantity,
        ),
    )
    assert projected.output.quantity == quantity
    with pytest.raises(CoreRuleError, match="matching normalized Metric quantity"):
        derive(
            (order_source,),
            replace(
                direct,
                ref=metric,
                field_owner=order_ref,
                field_contract=None,
                metric_contract=metric_contract,
                quantity=_quantity("profit"),
            ),
        )
    with pytest.raises(CoreRuleError, match="matching normalized Metric quantity"):
        derive(
            (source,),
            replace(
                direct,
                ref=metric,
                field_contract=None,
                metric_contract=metric_contract,
                quantity=quantity,
            ),
        )


def test_mapping_subject_image_full_tuple_union_and_missing_side() -> None:
    bound = _binding()
    order = ms.ref.entity("sales.order")
    customer = ms.ref.entity("sales.customer")
    order_key = (Coordinate(order, "order_id", "identity"),)
    customer_key = (Coordinate(customer, "customer_id", "identity"),)
    orders = DomainSignature(bound, "journey", order_key, order_key, "orders")
    customers = DomainSignature(bound, "entity", customer_key, customer_key, "customers")
    subject = SubjectPart(bound, customer, order_key, customer_key, False, True, "v1")
    mapped = derive((Signature(orders, parts=(subject,)),), MapCorrespond("subjects", customers))
    assert mapped.output.domain.instance_key == customers.instance_key
    assert mapped.output.domain.correspondence.multiplicity == "set_image"
    assert mapped.eval_id == "map_correspond.subject_set_image@v1"
    assert mapped.output.quantity is None
    assert subjects_image(
        ((1,), (2,), (3,)), (((1,), ("A",)), ((2,), ("A",)), ((3,), ("B",)))
    ) == frozenset({("A",), ("B",)})
    assert subjects_image((), ()) == frozenset()
    with pytest.raises(CoreRuleError, match="duplicate identity"):
        subjects_image(((1,), (1,)), (((1,), ("A",)),))
    with pytest.raises(CoreRuleError, match="total Subject map"):
        subjects_image(((1,), (2,)), (((1,), ("A",)),))
    with pytest.raises(CoreRuleError, match="total Subject map"):
        derive(
            (Signature(orders, parts=(replace(subject, total=False),)),),
            MapCorrespond("subjects", customers),
        )

    assert union_full_tuples((("east", "aug"), ("west", "sep")), (("east", "sep"),)) == frozenset(
        {("east", "aug"), ("west", "sep"), ("east", "sep")}
    )
    with pytest.raises(CoreRuleError, match="one arity"):
        union_full_tuples((("east", "aug"),), (("west",),))
    with pytest.raises(CoreRuleError, match="typed coordinate tuples"):
        union_full_tuples((("east", True),))
    paired = pair_coordinates(((1,), (2,)), ((2,), (3,)), exact=False)
    assert paired.keys == frozenset({(2,)})
    assert {(item.side, item.key) for item in paired.missing} == {
        ("baseline", (1,)),
        ("current", (3,)),
    }
    with pytest.raises(CoreRuleError, match="equal complete endpoint"):
        pair_coordinates(((1,), (2,)), ((2,), (3,)), exact=True)


def test_exact_and_group_mapping_keep_runtime_obligations_bound() -> None:
    source = Signature(_domain())
    paired = derive(
        (source, source), MapCorrespond("one_to_one", source.domain, "source.exact_pairing@v1")
    )
    assert {fact.kind for fact in paired.pre} == {
        "key_set_equal",
        "single_value",
        "mapping_injective",
    }
    assert len(paired.obligations) == 3
    assert paired.output.domain.correspondence.multiplicity == "paired"
    assert all(
        item.fact.binding == source.domain.binding and item.before == "consume"
        for item in paired.obligations
    )
    with pytest.raises(CoreRuleError, match="no matching evidence"):
        derive((source, source), MapCorrespond("exact_keys", source.domain))
    group_coord = Coordinate(ms.ref.entity("sales.customer"), "region", "group")
    group = DomainSignature(
        source.domain.binding, "group", (group_coord,), (group_coord,), "regions"
    )
    grouped = derive((source,), MapCorrespond("group", group, "source.group_mapping@v1"))
    assert grouped.output.domain.instance_key == group.instance_key
    assert {item.fact.kind for item in grouped.obligations} == {"mapping_total", "single_value"}
    assert grouped.output.domain.correspondence.multiplicity == "group"


def test_cells_are_distinct_from_pairing_and_post_is_conditional() -> None:
    current = _observed()
    baseline = Signature(current.domain, _quantity("baseline"))
    result = derive(
        (current, baseline),
        CellDerive(
            "difference",
            "delta",
            "strict",
            "CNY",
            "august",
            "source.exact_pairing@v1",
            "source.finite_numeric@v1",
        ),
    )
    assert result.rule == "cell_derive@v1"
    assert len(result.obligations) == 2
    assert result.post[0] not in available_facts(result.output)
    assert {part.side for part in result.output.parts if isinstance(part, EndpointPart)} == {
        "current",
        "baseline",
    }
    assert tuple(part for part in result.output.parts if isinstance(part, CorrespondencePart)) == (
        CorrespondencePart(
            current.domain.binding, current.domain.instance_key, baseline.domain.instance_key, "v2"
        ),
    )
    assert derive_numeric_cell("difference", Defined(5), Defined(2)) == Defined(3)
    assert derive_numeric_cell("ratio", Defined(5), Defined(0)) == Undefined("zero_denominator")
    for cell in (Null("source_null"), Undefined("empty_mean"), Unknown("coverage")):
        with pytest.raises(CoreRuleError, match="Defined numeric Cells"):
            derive_numeric_cell("difference", Defined(1), cell)
    with pytest.raises(CoreRuleError, match="bound units"):
        derive(
            (current, baseline),
            CellDerive(
                "difference",
                "delta",
                "strict",
                "USD",
                "august",
                "source.exact_pairing@v1",
                "source.finite_numeric@v1",
            ),
        )
    with pytest.raises(CoreRuleError, match="registered check for this exact premise"):
        derive(
            (current, baseline),
            CellDerive(
                "difference",
                "delta",
                "strict",
                "CNY",
                "august",
                "source.finite_numeric@v1",
                "source.finite_numeric@v1",
            ),
        )


def test_current_row_state_is_not_original_rollup() -> None:
    source = _observed()
    singleton = DomainSignature(source.domain.binding, "singleton", (), (), "all_customers")
    count = derive((source,), RowState("count", singleton, "row_count", "count_all"))
    defined_count = derive(
        (source,),
        RowState(
            "count_defined",
            singleton,
            "defined_count",
            "defined_only",
            numeric_check_id="source.cell_policy@v1",
        ),
    )
    mean = derive(
        (source,),
        RowState(
            "mean",
            singleton,
            "mean_customer_revenue",
            "strict",
            numeric_check_id="source.finite_numeric@v1",
        ),
    )
    assert count.output.quantity.definition_id == "row_count"
    assert defined_count.output.quantity.definition_id == "defined_count"
    assert count.output.quantity.method_version != defined_count.output.quantity.method_version
    assert mean.output.quantity.kind == "row_statistic"
    assert isinstance(require_part(mean.output, "row_state"), RowStatePart)
    assert mean.output.quantity.input_domain_id == source.domain.definition_id
    with pytest.raises(CoreRuleError, match="original Metric quantity"):
        derive(
            (mean.output,),
            OriginalReduce(
                singleton, "source.contribution_partition@v1", "source.complete_coverage@v1"
            ),
        )
    with pytest.raises(CoreRuleError, match="statistical_weight"):
        derive(
            (source,),
            RowState("weighted_mean", singleton, "weighted", "strict", weighting="weight_role"),
        )
    weight = StatisticalWeightPart(source.domain.binding, "weight_role", "1", "v1")
    weighted = derive(
        (replace(source, parts=(weight,)),),
        RowState(
            "weighted_mean",
            singleton,
            "weighted",
            "strict",
            weighting="weight_role",
            numeric_check_id="source.finite_numeric@v1",
        ),
    )
    assert require_part(weighted.output, "row_state").components == ("weighted_sum", "weight_sum")
    assert weighted.output.quantity.weighting == "weight_role"


def test_original_reduce_requires_bound_components_and_coverage() -> None:
    source = _observed(state=True, coverage=True)
    singleton = DomainSignature(source.domain.binding, "singleton", (), (), "all_customers")
    rolled = derive(
        (source,),
        OriginalReduce(
            singleton, "source.contribution_partition@v1", "source.complete_coverage@v1"
        ),
    )
    assert rolled.output.quantity.kind == "original_rollup"
    assert rolled.eval_id == "original_reduce.merge_then_finish@v1"
    assert require_part(rolled.output, "original_state").components == ("sum", "non_null_count")
    assert require_part(rolled.output, "coverage").quantity_id == _quantity().definition_id
    assert {item.fact.kind for item in rolled.obligations} == {
        "complete_coverage",
        "contribution_partition",
    }
    with pytest.raises(CoreRuleError, match="retained coverage"):
        derive(
            (_observed(state=True),),
            OriginalReduce(
                singleton, "source.contribution_partition@v1", "source.complete_coverage@v1"
            ),
        )
    wrong_state = replace(require_part(source, "original_state"), quantity_id="metric:profit")
    with pytest.raises(CoreRuleError, match="part bound to this quantity"):
        derive(
            (replace(source, parts=(wrong_state, require_part(source, "coverage"))),),
            OriginalReduce(
                singleton, "source.contribution_partition@v1", "source.complete_coverage@v1"
            ),
        )


def test_parts_transport_drops_dependent_continuations_and_never_promotes_post() -> None:
    source = _observed(state=True, coverage=True)
    subject = SubjectPart(
        source.domain.binding,
        ms.ref.entity("sales.customer"),
        source.domain.instance_key,
        source.domain.instance_key,
        True,
        True,
        "v1",
    )
    reference = FixedReferencePart(
        source.domain.binding, "reference:full", source.domain.binding.scope_id, "v1"
    )
    enriched = replace(source, parts=(*source.parts, subject, reference))
    selected = derive(
        (enriched,),
        PartsTransport("projection", source.domain, ("subject", "fixed_reference"), False),
    )
    assert selected.part_transform.removed == ("original_state", "coverage")
    assert selected.output.quantity is None
    assert selected.post[0] in available_facts(selected.output)
    with pytest.raises(CoreRuleError, match="retained original_state"):
        require_part(selected.output, "original_state")
    with pytest.raises(CoreRuleError, match="quantity-bound parts"):
        derive((enriched,), PartsTransport("projection", source.domain, ("original_state",), False))
    with pytest.raises(CoreRuleError, match="retained baseline_endpoint"):
        derive((enriched,), PartsTransport("compare", source.domain, ("baseline_endpoint",), True))
    narrowed_domain = replace(source.domain, binding=_binding("august-selected"))
    with pytest.raises(CoreRuleError, match="coverage retained only"):
        derive((enriched,), PartsTransport("where", narrowed_domain, ("coverage",), True))


def test_evidence_scope_is_not_a_queued_check_or_parent_post() -> None:
    source = _observed()
    binding = source.domain.binding
    baseline = Signature(source.domain, _quantity("baseline"))
    declaration = Evidence(
        Fact(
            "key_set_equal",
            binding,
            "delta",
            "v1",
            (
                FactInput(source.domain, source.quantity),
                FactInput(baseline.domain, baseline.quantity),
            ),
        ),
        "check",
        "fixed-input-oracle",
    )
    current = replace(source, evidence=(declaration,))
    derived = derive(
        (current, baseline),
        CellDerive(
            "difference",
            "delta",
            "strict",
            "CNY",
            "august",
            numeric_check_id="source.finite_numeric@v1",
        ),
    )
    assert len(derived.obligations) == 1
    assert derived.transport == (declaration,)
    child = derive(
        (derived.output,), PartsTransport("view", source.domain, ("current_endpoint",), True)
    )
    assert child.obligations == derived.obligations
    assert derived.post[0] not in available_facts(child.output)
    stale = _evidence("key_set_equal", _binding("july"), "delta")
    with pytest.raises(CoreRuleError, match="no matching evidence"):
        derive(
            (replace(source, evidence=(stale,)), baseline),
            CellDerive(
                "difference",
                "delta",
                "strict",
                "CNY",
                "august",
                numeric_check_id="source.finite_numeric@v1",
            ),
        )
    key_fact = Fact("declared_key", binding, "customers", "v1")
    derived_fact = Fact("key_set_equal", binding, "delta", "v1")
    deduction = Evidence(derived_fact, "deduction", "map_correspond@v1", (key_fact,))
    assert derived_fact not in available_facts(replace(source, evidence=(deduction,)))
    declared_key = Evidence(key_fact, "declaration", "entity-key")
    assert derived_fact in available_facts(replace(source, evidence=(deduction, declared_key)))
    numeric_fact = Fact("finite_numeric", binding, "customers", "v1")
    numeric_check = Evidence(numeric_fact, "check", "fixed-input-oracle")
    pending = Obligation(numeric_fact, "source.finite_numeric@v1", "consume")
    assert numeric_fact not in available_facts(
        replace(source, evidence=(numeric_check,), obligations=(pending,))
    )
    with pytest.raises(CoreRuleError, match="declared definition fact"):
        Evidence(Fact("mapping_total", binding, "customers", "v1"), "declaration", "entity-key")
    with pytest.raises(CoreRuleError, match="builder-owned definition fact"):
        Evidence(Fact("single_value", binding, "customers", "v1"), "builder", "mapping-declaration")
    with pytest.raises(CoreRuleError, match="deduction with bound premises"):
        Evidence(derived_fact, "deduction", "map_correspond@v1")


def test_all_six_rules_construct_without_source_store_or_run_io(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marivo.analysis.materialization import graph_store
    from marivo.analysis.materialization.admission import DatasetRuntime
    from marivo.analysis.materialization.store import SessionStore
    from marivo.datasource.adapters import SourceSession

    case = analysis_dsl_case_factory("j1")
    region = ms.ref.dimension(f"{case.names.domain}.{case.names.customer}.{case.names.region}")
    field = normalize_target_dimension(case.catalog._state.registry, region.path)
    source = Signature(_domain())
    observed = _observed(state=True, coverage=True)
    singleton = DomainSignature(source.domain.binding, "singleton", (), (), "all_customers")

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("construction opened a source, Store, or Runtime")

    monkeypatch.setattr(duckdb, "connect", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr(SourceSession, "__init__", forbidden)
    monkeypatch.setattr(SessionStore, "__init__", forbidden)
    monkeypatch.setattr(SessionStore, "open_existing", forbidden)
    monkeypatch.setattr(graph_store, "run", forbidden)
    monkeypatch.setattr(graph_store, "artifact", forbidden)
    monkeypatch.setattr(DatasetRuntime, "__init__", forbidden)
    monkeypatch.setattr(DatasetRuntime, "get_run", forbidden)
    assert (
        derive(
            (source,), BindProject(region, ms.ref.entity("sales.customer"), field, None, (), ())
        ).rule
        == "bind_project@v1"
    )
    assert (
        derive((source, source), MapCorrespond("union_keys", source.domain)).rule
        == "map_correspond@v1"
    )
    assert (
        derive(
            (observed, observed),
            CellDerive(
                "difference",
                "delta",
                "strict",
                "CNY",
                "august",
                "source.exact_pairing@v1",
                "source.finite_numeric@v1",
            ),
        ).rule
        == "cell_derive@v1"
    )
    assert (
        derive((observed,), RowState("count", singleton, "rows", "count_all")).rule
        == "row_state@v1"
    )
    assert (
        derive(
            (observed,),
            OriginalReduce(
                singleton, "source.contribution_partition@v1", "source.complete_coverage@v1"
            ),
        ).rule
        == "original_reduce@v1"
    )
    assert (
        derive((observed,), PartsTransport("projection", observed.domain, (), True)).rule
        == "parts_transport@v1"
    )


@pytest.mark.parametrize("change", ("input", "scope", "quantity", "domain", "order"))
def test_pairing_evidence_cannot_authorize_different_endpoints(change: str) -> None:
    current = _observed()
    baseline = Signature(
        replace(current.domain, binding=replace(_binding(), input_id="baseline")),
        _quantity("baseline"),
    )
    params = CellDerive(
        "difference",
        "delta",
        "strict",
        "CNY",
        "august",
        "source.exact_pairing@v1",
        "source.finite_numeric@v1",
    )
    checked = derive((current, baseline), params)
    evidence = tuple(Evidence(item.fact, "check", "checked-pair") for item in checked.obligations)
    proven = replace(current, evidence=evidence)
    no_checks = replace(params, pairing_check_id=None, numeric_check_id=None)
    assert not derive((proven, baseline), no_checks).obligations
    changed = baseline
    if change == "input":
        changed = replace(
            baseline,
            domain=replace(
                baseline.domain, binding=replace(baseline.domain.binding, input_id="different")
            ),
        )
    elif change == "scope":
        changed = replace(
            baseline,
            domain=replace(
                baseline.domain, binding=replace(baseline.domain.binding, scope_id="july")
            ),
        )
    elif change == "quantity":
        changed = replace(baseline, quantity=_quantity("profit"))
    elif change == "domain":
        changed = replace(baseline, domain=replace(baseline.domain, definition_id="other-domain"))
    inputs = (baseline, proven) if change == "order" else (proven, changed)
    with pytest.raises(CoreRuleError, match="no matching evidence"):
        derive(inputs, no_checks)
    assert derive(inputs, params).obligations != checked.obligations
    endpoint = require_part(checked.output, "baseline_endpoint")
    assert endpoint.binding == baseline.domain.binding
    transported = derive(
        (checked.output,),
        PartsTransport("view", checked.output.domain, ("baseline_endpoint",), True),
    )
    assert require_part(transported.output, "baseline_endpoint") == endpoint


@pytest.mark.parametrize("mode", ("where", "projection", "compare", "view", "materialize"))
def test_transport_rejects_unproved_input_and_domain_changes(mode: TransportMode) -> None:
    source = _observed(state=True, coverage=True)
    targets = (
        replace(source.domain, binding=replace(_binding(), input_id="unrelated-source")),
        replace(source.domain, definition_id="unproved-selection"),
        replace(source.domain, target_key=()),
    )
    for target in targets:
        with pytest.raises(CoreRuleError, match="exact input domain"):
            derive((source,), PartsTransport(mode, target, ("original_state", "coverage"), True))
    same = derive(
        (source,), PartsTransport(mode, source.domain, ("original_state", "coverage"), True)
    )
    assert same.output.parts == source.parts


def test_reducers_reject_unmapped_groups_and_foreign_singletons() -> None:
    source = _observed(state=True, coverage=True)
    key = (Coordinate(ms.ref.entity("sales.customer"), "undeclared_group", "group"),)
    targets = (
        DomainSignature(_binding(), "group", key, key, "invented-groups"),
        DomainSignature(replace(_binding(), input_id="foreign"), "singleton", (), (), "all"),
        DomainSignature(_binding("july"), "singleton", (), (), "all"),
    )
    for target in targets:
        with pytest.raises(CoreRuleError, match="singleton over the exact input"):
            derive((source,), RowState("count", target, "group-count", "count_all"))
        with pytest.raises(CoreRuleError, match="singleton over the exact input"):
            derive(
                (source,),
                OriginalReduce(
                    target, "source.contribution_partition@v1", "source.complete_coverage@v1"
                ),
            )


@pytest.mark.parametrize(
    "components", (("unrelated_component",), ("sum",), ("sum", "non_null_count", "extra"))
)
def test_original_reduce_rejects_incomplete_or_unknown_components(
    components: tuple[str, ...],
) -> None:
    source = _observed(state=True, coverage=True)
    state = require_part(source, "original_state")
    assert isinstance(state, OriginalStatePart)
    bad = replace(
        source, parts=(replace(state, components=components), require_part(source, "coverage"))
    )
    target = DomainSignature(_binding(), "singleton", (), (), "all")
    with pytest.raises(CoreRuleError, match="complete state bound"):
        derive(
            (bad,),
            OriginalReduce(
                target, "source.contribution_partition@v1", "source.complete_coverage@v1"
            ),
        )


@pytest.mark.parametrize("method,version", (("mean@v1", "v1"), ("sum@v1", "v2")))
def test_original_reduce_rejects_unimplemented_state_contracts(method: str, version: str) -> None:
    source = _observed(state=True, coverage=True)
    state = require_part(source, "original_state")
    assert isinstance(state, OriginalStatePart)
    quantity = _quantity()
    bad = replace(
        source,
        quantity=replace(quantity, method_version=method),
        parts=(
            replace(state, method_version=method, version=version),
            require_part(source, "coverage"),
        ),
    )
    target = DomainSignature(_binding(), "singleton", (), (), "all")
    with pytest.raises(CoreRuleError, match="complete state bound"):
        derive(
            (bad,),
            OriginalReduce(
                target, "source.contribution_partition@v1", "source.complete_coverage@v1"
            ),
        )


@pytest.mark.parametrize("value", (float("inf"), float("-inf"), float("nan")))
@pytest.mark.parametrize("denominator", (0, 1))
def test_ratio_rejects_nonfinite_numerator_before_zero_policy(
    value: float, denominator: int
) -> None:
    with pytest.raises(CoreRuleError, match="finite numeric cells"):
        derive_numeric_cell("ratio", Defined(value), Defined(denominator))
    assert derive_numeric_cell("ratio", Defined(5), Defined(0)) == Undefined("zero_denominator")
