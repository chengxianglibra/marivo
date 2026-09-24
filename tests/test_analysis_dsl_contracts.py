"""Counterexamples for the inactive first-round Analysis DSL contract seam."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

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
)
from marivo.analysis.observation.contracts import ContractEvidence, derive_metric_components
from marivo.analysis.operators.registry import (
    MethodContract,
    MethodImplementation,
    MethodRegistration,
)
from marivo.analysis.session._lazy_sources import make_lazy_sources
from marivo.refs import ref
from marivo.semantic.metric_graph_lowering import normalize_target_metric
from tests.lazy_dataset_fixtures import make_logical_dataset, make_materialized_dataset
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
    with pytest.raises(DatasetRegistrationError):
        replace(contract, **{"cell_policy": "unknown"})
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
    run_bindings = {run_a: {source._root: object()}, run_b: {source._root: object()}}
    assert run_bindings[run_a][source._root] is not run_bindings[run_b][source._root]
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
