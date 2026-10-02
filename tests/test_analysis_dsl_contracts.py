"""Counterexamples for the inactive first-round Analysis DSL contract seam."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from marivo._temporal import time_scope
from marivo.analysis.compiler.normalize import classify_inputs, require_unmixed_inputs
from marivo.analysis.datasets.descriptors import (
    _entity_domain,
    _group_domain,
    _make_field_id,
    _observed_quantity,
    _row_statistic_quantity,
    _singleton_domain,
)
from marivo.analysis.datasets.errors import DatasetConstructionError, DatasetRegistrationError
from marivo.analysis.datasets.handles import (
    DefinitionInput,
    LogicalInputToken,
    LogicalRootHandle,
    MaterializedInputToken,
    MaterializedScanLeafHandle,
    _make_logical_root,
    _RunNodeBindings,
)
from marivo.analysis.domains.contracts import EventPayload
from marivo.analysis.event import first_per_subject, sequence, step
from marivo.analysis.observation.contracts import ContractEvidence, derive_metric_components
from marivo.analysis.operators.registry import (
    MethodContract,
    MethodImplementation,
    MethodRegistration,
)
from marivo.analysis.session._lazy_sources import make_lazy_sources
from marivo.refs import ref
from marivo.semantic.event import participant_role
from marivo.semantic.metric_graph_lowering import normalize_target_metric
from tests.lazy_dataset_fixtures import make_logical_dataset, make_materialized_dataset
from tests.lazy_event_fixtures import make_event_sources
from tests.lazy_execution_fixtures import make_execution_registry
from tests.lazy_observation_fixtures import NoIoActionPort


def _source_population(database: Path):
    registry, sidecar = make_execution_registry(database)
    sources = make_lazy_sources(
        semantic_registry=registry,
        sidecar=sidecar,
        action_port=NoIoActionPort(),
        session_id="dsl-contract",
        store_id="dsl-contract",
    )
    return sources.population(ref.entity("sales.customers"))


def _compose(*roots: LogicalRootHandle | MaterializedScanLeafHandle) -> LogicalRootHandle:
    sample = make_logical_dataset()._root
    assert isinstance(sample, LogicalRootHandle)
    inputs = tuple(
        DefinitionInput(
            role=f"input_{index}",
            token=(
                LogicalInputToken(root.definition_fingerprint)
                if isinstance(root, LogicalRootHandle)
                else MaterializedInputToken(root.artifact_ref)
            ),
            root=root,
        )
        for index, root in enumerate(roots)
    )
    return _make_logical_root(
        session_id="dsl-contract",
        store_id="dsl-contract",
        shape_id=sample.shape_id,
        row_contract_fingerprint=sample.row_contract_fingerprint,
        row_set_contract_fingerprint=sample.row_set_contract_fingerprint,
        operator_id="test.combine",
        inputs=inputs,
    )


def test_domain_member_identity_and_contribution_coordinates_are_distinct() -> None:
    member = _make_field_id("member")
    region = _make_field_id("region")
    contribution = _make_field_id("order")
    entity = _entity_domain(ref.entity("sales.customers"), member)
    group = _group_domain(entity, (region,))
    singleton = _singleton_domain(group)
    observed = _observed_quantity(entity, "metric:sales.revenue", (contribution,), ("revenue.sum",))
    statistic = _row_statistic_quantity(entity, group, "ds_" + "0" * 64, "mean", ("sum", "count"))
    assert entity.kind == "entity" and group.kind == "group" and singleton.kind == "singleton"
    assert observed.kind == "observed"
    assert observed.contribution_coordinates == (contribution,)
    assert statistic.kind == "row_statistic"
    assert statistic.input_domain is entity and statistic.output_domain is group
    assert statistic.current_row_unit == "one_per_row"
    with pytest.raises(DatasetConstructionError):
        _group_domain(entity, (region, region))
    with pytest.raises(DatasetConstructionError):
        _row_statistic_quantity(entity, group, "bad", "mean", ("sum", "count"))
    with pytest.raises(DatasetConstructionError):
        _row_statistic_quantity(entity, group, "ds_" + "0" * 64, "mean", ("count",))


def test_real_metric_graph_derives_components_but_not_missing_authority() -> None:
    registry, sidecar = make_execution_registry(Path("must-not-open"))
    revenue = derive_metric_components(
        normalize_target_metric(registry, "sales.revenue", sidecar=sidecar)
    )
    count = derive_metric_components(
        normalize_target_metric(registry, "sales.order_count", sidecar=sidecar)
    )
    ratio = derive_metric_components(
        normalize_target_metric(registry, "sales.conversion_rate", sidecar=sidecar)
    )
    assert revenue.method == "sum" and count.method == "count"
    assert ratio.method == "ratio"
    assert len(ratio.component_roles) == 2
    assert ratio.required_parts
    assert ratio.null_rule == "null_component_or_zero_denominator"
    assert ratio.evidence.completed_checks == ()
    assert "contribution_partition" in ratio.evidence.pending_checks
    with pytest.raises(DatasetConstructionError):
        ratio.require_original_state_rollup()
    with pytest.raises(DatasetConstructionError):
        derive_metric_components(
            replace(
                normalize_target_metric(registry, "sales.conversion_rate", sidecar=sidecar),
                required_state=(),
            )
        )
    malformed = normalize_target_metric(registry, "sales.conversion_rate", sidecar=sidecar)
    missing_component = malformed.components[-1].node_id
    with pytest.raises(DatasetConstructionError, match="missing component graph node"):
        derive_metric_components(
            replace(
                malformed,
                graph=replace(
                    malformed.graph,
                    nodes=tuple(
                        record
                        for record in malformed.graph.nodes
                        if record.node_id != missing_component
                    ),
                ),
            )
        )


def test_declared_and_pending_facts_do_not_become_completed_checks() -> None:
    evidence = ContractEvidence(
        declarations=("entity_identity",),
        deductions=("component_graph",),
        completed_checks=(),
        pending_checks=("contribution_partition",),
    )
    with pytest.raises(DatasetConstructionError):
        evidence.require_completed("contribution_partition")
    with pytest.raises(DatasetConstructionError):
        evidence.require_completed("entity_identity")
    with pytest.raises(DatasetConstructionError):
        replace(evidence, completed_checks=("contribution_partition",))
    # The same named premise may need both an authored assertion and a completed check.
    both = replace(
        evidence,
        declarations=("contribution_partition",),
        pending_checks=(),
        completed_checks=("contribution_partition",),
    )
    both.require_declared("contribution_partition")
    both.require_completed("contribution_partition")


def test_method_semantics_are_single_owner_for_qualified_implementations() -> None:
    contract = MethodContract(
        method_id="dsl.observe_sum",
        version=1,
        input_kinds=("observed",),
        input_domains=("entity",),
        output_kind="observed",
        domain_policy="same",
        unit_policy="preserve",
        cell_policy="strict",
        numeric_policy="int64_checked",
        capabilities=("original_state_reduction", "part_transport"),
        part_effect="merge_original",
        required_parts=("sum",),
        required_checks=("coverage",),
        continuations=("rollup",),
    )
    source = MethodImplementation(
        method_id="dsl.observe_sum",
        version=1,
        route="source",
        backend="duckdb",
        input_domains=("entity",),
        logical_types=("int64", "float64"),
        supported_parts=("sum",),
        supported_checks=("coverage",),
        batch_mode="stream",
        resource_owner="producer",
    )
    local = MethodImplementation(
        method_id="dsl.observe_sum",
        version=1,
        route="local",
        backend="pandas",
        input_domains=("entity",),
        logical_types=("int64", "float64"),
        supported_parts=("sum",),
        supported_checks=("coverage",),
        batch_mode="complete",
        resource_owner="caller",
    )
    isolated = MethodRegistration(contract, (source, local))
    assert isolated.require_route("source", "duckdb", "entity", "int64") is source
    assert isolated.require_route("local", "pandas", "entity", "float64") is local
    with pytest.raises(DatasetRegistrationError):
        isolated.require_route("source", "postgres", "entity", "int64")
    with pytest.raises(DatasetRegistrationError):
        isolated.require_route("local", "pandas", "group", "float64")
    with pytest.raises(DatasetRegistrationError):
        MethodRegistration(contract, (source, source))
    with pytest.raises(DatasetRegistrationError):
        MethodRegistration(contract, (replace(local, supported_parts=()),))
    with pytest.raises(DatasetRegistrationError):
        MethodRegistration(
            contract,
            (
                MethodImplementation(
                    method_id="dsl.observe_sum",
                    version=1,
                    route="local",
                    backend="pandas",
                    input_domains=("entity",),
                    logical_types=("int64",),
                    supported_parts=("sum",),
                    supported_checks=(),
                    batch_mode="complete",
                    resource_owner="caller",
                ),
            ),
        )

    with pytest.raises(DatasetRegistrationError):
        replace(contract, **{"capabilities": ("not_a_capability",)})
    with pytest.raises(DatasetRegistrationError):
        replace(contract, input_kinds=("row_statistic",))
    assert replace(
        contract,
        input_kinds=("observed", "observed"),
        capabilities=("part_transport",),
        part_effect="preserve",
    ).input_kinds == ("observed", "observed")
    with pytest.raises(DatasetRegistrationError):
        replace(contract, **{"cell_policy": "unknown"})
    with pytest.raises(DatasetRegistrationError):
        replace(contract, cell_policy="spearman_pairs", numeric_policy="none")
    with pytest.raises(DatasetRegistrationError):
        replace(contract, capabilities=("cell_calculation",), input_kinds=("domain",))
    with pytest.raises(DatasetRegistrationError):
        replace(contract, capabilities=("domain_correspondence",), output_kind="observed")
    with pytest.raises(DatasetRegistrationError):
        replace(contract, capabilities=("bind_project",), input_kinds=("observed",))
    conservative_transport = replace(
        contract, capabilities=("part_transport",), part_effect="preserve", continuations=()
    )
    assert conservative_transport.continuations == ()
    row_statistic = replace(
        contract,
        method_id="dsl.summarize_mean",
        output_kind="row_statistic",
        unit_policy="mean",
        numeric_policy="float64_finite",
        capabilities=("current_row_state",),
        part_effect="build_current",
        required_parts=("sum", "count"),
    )
    with pytest.raises(DatasetRegistrationError):
        MethodRegistration(row_statistic, ()).require_route("local", "pandas", "entity", "float64")
    with pytest.raises(DatasetRegistrationError):
        MethodRegistration(
            contract,
            (
                source,
                MethodImplementation(
                    method_id="dsl.observe_sum",
                    version=2,
                    route="local",
                    backend="pandas",
                    input_domains=("entity",),
                    logical_types=("int64",),
                    supported_parts=("sum",),
                    supported_checks=("coverage",),
                    batch_mode="complete",
                    resource_owner="caller",
                ),
            ),
        )


def test_input_classification_stops_at_artifact_and_keeps_explicit_node_identity(
    tmp_path: Path,
) -> None:
    database = tmp_path / "must-not-open.duckdb"
    source = _source_population(database)
    source_again = _source_population(database)
    assert source.definition_fingerprint == source_again.definition_fingerprint
    assert source._root is not source_again._root
    run_a, run_b = uuid4().hex, uuid4().hex
    assert run_a != run_b
    run_bindings = {
        run_a: _RunNodeBindings[object]("dsl-contract"),
        run_b: _RunNodeBindings[object]("dsl-contract"),
    }
    first_implementation = object()
    assert run_bindings[run_a].bind(source._root, first_implementation) is first_implementation
    assert run_bindings[run_a].bind(source._root, first_implementation) is first_implementation
    assert run_bindings[run_b].bind(source._root, object()) is not first_implementation
    run_bindings[run_a].bind(source_again._root, object())
    with pytest.raises(DatasetConstructionError):
        run_bindings[run_a].bind(source._root, object())
    with pytest.raises(DatasetConstructionError):
        _RunNodeBindings[object]("foreign-session").bind(source._root, object())
    assert source.definition_fingerprint == source_again.definition_fingerprint
    artifact = make_materialized_dataset(origin=source)
    source_result = classify_inputs(_compose(source._root, source._root))
    independent = classify_inputs(_compose(source._root, source_again._root))
    fixed = classify_inputs(_compose(artifact._root))
    mixed = classify_inputs(_compose(source._root, artifact._root))
    assert source_result.kind == "source" and len(source_result.source_nodes) == 1
    assert independent.kind == "source" and len(independent.source_nodes) == 2
    assert fixed.kind == "artifact" and fixed.source_nodes == ()
    assert mixed.kind == "mixed" and len(mixed.artifact_leaves) == 1
    with pytest.raises(DatasetConstructionError, match="live source and explicit Artifact"):
        require_unmixed_inputs(_compose(source._root, artifact._root))
    assert source.definition_fingerprint == source_again.definition_fingerprint
    assert not database.exists()


def test_unbound_root_rejects_without_business_io() -> None:
    root = make_logical_dataset()._root
    assert isinstance(root, LogicalRootHandle)
    with pytest.raises(DatasetConstructionError):
        classify_inputs(root)


def test_event_source_fact_prevents_artifact_only_false_negative(tmp_path: Path) -> None:
    database = tmp_path / "must-not-open.duckdb"
    sources = make_event_sources(
        database=database, session_id="dsl-contract", store_id="dsl-contract"
    )
    pattern = sequence(
        step(
            participant=participant_role(event=ref.event("sales.started"), name="buyer"),
            key="start",
        ),
        step(
            participant=participant_role(event=ref.event("sales.finished"), name="buyer"),
            key="finish",
        ),
    )
    event = sources.events.match(
        pattern,
        cohort_window=time_scope(
            start="2026-02-01T00:00:00+00:00", end="2026-03-01T00:00:00+00:00"
        ),
        completion_through=datetime(2026, 3, 2, tzinfo=timezone.utc),
        matching=first_per_subject(),
    )
    assert isinstance(event._root, LogicalRootHandle)
    assert isinstance(event._root.payload, EventPayload)
    assert event._root.payload.live_source_dependencies
    funnel = event.funnel(axes=(ref.dimension("sales.customers.region"),))
    assert isinstance(funnel._root, LogicalRootHandle)
    assert funnel._root.payload is not None
    assert funnel._root.payload.live_source_dependencies
    assert funnel._root in classify_inputs(funnel._root).source_nodes
    artifact = make_materialized_dataset(origin=event)
    classification = classify_inputs(_compose(event._root, artifact._root))
    assert classification.kind == "mixed"
    assert event._root in classification.source_nodes
    fixed_input = DefinitionInput(
        role="fixed",
        token=MaterializedInputToken(artifact._root.artifact_ref),
        root=artifact._root,
    )
    event_with_fixed_members = _make_logical_root(
        session_id="dsl-contract",
        store_id="dsl-contract",
        shape_id=event._root.shape_id,
        row_contract_fingerprint=event._root.row_contract_fingerprint,
        row_set_contract_fingerprint=event._root.row_set_contract_fingerprint,
        operator_id="test.event_with_fixed_members",
        inputs=(fixed_input,),
        payload=event._root.payload,
    )
    assert classify_inputs(event_with_fixed_members).kind == "mixed"
    assert not database.exists()
