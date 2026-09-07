"""Bounded terminal audit snapshots derive solely from current Core authority."""

from __future__ import annotations

import pickle
from dataclasses import replace

import pytest

from marivo.analysis.datasets.base import Dataset, LogicalDataset
from marivo.analysis.datasets.contract import DatasetContract
from marivo.analysis.datasets.descriptors import (
    _CORE_TOKEN,
    DatasetRowContract,
    DatasetRowSetContract,
    _make_field_id,
    _make_schema,
)
from marivo.analysis.datasets.errors import DatasetRegistrationError
from marivo.analysis.datasets.registry import ConsumerRegistration, DatasetFamilyRegistry
from marivo.render import AgentResult
from tests.lazy_dataset_fixtures import (
    make_logical_dataset,
    make_materialized_dataset,
    make_row_contracts,
    make_test_registration,
    make_test_registry,
)


def test_only_contract_satisfies_terminal_protocol_and_show_matches_render(
    capsys: pytest.CaptureFixture[str],
) -> None:
    for dataset in (make_logical_dataset(), make_materialized_dataset()):
        assert not isinstance(dataset, AgentResult)
        contract = dataset.contract()
        assert isinstance(contract, AgentResult)
        rendered = contract.render()
        assert contract.show() is None
        assert capsys.readouterr().out == rendered + "\n"
        assert len(rendered.encode("utf-8")) <= 8192
        assert len(repr(contract)) <= 200
        assert "\n" not in repr(contract)
        assert "0x" not in repr(contract)
        assert ".show()" in repr(contract)


def test_contract_projects_logical_contract_schema_and_action_requirements() -> None:
    dataset = make_logical_dataset(
        contracts=make_row_contracts("entity"),
        requirements=("test.minimum_rows",),
        dependency_facts=("metric:sales.revenue",),
    )
    rendered = dataset.contract().render(max_output_bytes=None)
    for expected in (
        "test/entity@v1",
        "session-test",
        "row_contract: v1 fp=",
        "row_set_contract: v1 fp=",
        "coordinates: entity_id",
        "row_key: entity_id",
        "keyed; row_bound=unknown",
        "ordering: unordered",
        "entity_key",
        "resolved:float64",
        "test.minimum_rows",
        "metric:sales.revenue",
        "execute() produces the paired MaterializedDataset",
    ):
        assert expected in rendered
    assert "evidence_authority:" not in rendered
    assert "artifact_session:" not in rendered


def test_materialized_contract_uses_committed_projection_not_execution_session() -> None:
    dataset = make_materialized_dataset(artifact_ref="art_contract")
    state = dataset.state
    rendered = dataset.contract().render(max_output_bytes=None)
    for expected in (
        "state=materialized",
        "art_contract",
        state.artifact_session_ref,
        state.content_authority_digest,
        state.quality_authority_digest,
        state.evidence_authority_digest,
        f"rows: {state.realized_row_count}",
        "storage_kind: test_parquet",
        "no logical-origin replay",
    ):
        assert expected in rendered


def test_continuations_use_registered_shape_admission_roles_and_requirements() -> None:
    scalar = make_row_contracts()[0].shape_id
    entity = make_row_contracts("entity")[0].shape_id
    consumers = (
        ConsumerRegistration(
            id="test.step",
            input_roles=("source",),
            output_family="test",
            accepted_shape_ids=(entity,),
            requirements=("test.require_entity",),
        ),
        ConsumerRegistration(
            id="test.combine",
            input_roles=("baseline", "candidate"),
            output_family="test",
            accepted_shape_ids=(scalar, entity),
            requirements=("test.match_rows",),
        ),
    )
    registry = make_test_registry(consumers=consumers)
    scalar_dataset = make_logical_dataset(registry=registry)
    scalar_card = scalar_dataset.contract().render(max_output_bytes=None)
    assert "test.step:" not in scalar_card
    assert (
        "test.combine: input_roles=(baseline, candidate) -> test; requirements=(test.match_rows)"
        in scalar_card
    )
    entity_dataset = make_logical_dataset(registry=registry, contracts=make_row_contracts("entity"))
    entity_card = entity_dataset.contract().render(max_output_bytes=None)
    assert (
        "test.step: input_roles=(source) -> test; requirements=(test.require_entity)" in entity_card
    )
    assert entity_card.index("test.combine:") < entity_card.index("test.step:")


def test_owner_admission_is_shared_by_operator_construction_and_contract_disclosure() -> None:
    def admits(dataset: Dataset, consumer_id: str) -> bool:
        return consumer_id != "test.step" or isinstance(dataset, LogicalDataset)

    def facts(dataset: Dataset) -> tuple[tuple[str, str], ...]:
        return (("test.shape", dataset.row_contract.shape_id.local_shape_id),)

    registry = DatasetFamilyRegistry()
    registry.register(
        replace(make_test_registration(), consumer_admission=admits, contract_facts=facts)
    )
    registry.freeze()
    source = make_logical_dataset(registry=registry)
    assert "test.step:" in source.contract().render(max_output_bytes=None)
    assert "test.shape: scalar" in source.contract().render(max_output_bytes=None)
    assert source.step().kind == "test"
    materialized = make_materialized_dataset(registry=registry)
    assert "test.step:" not in materialized.contract().render(max_output_bytes=None)
    with pytest.raises(DatasetRegistrationError, match="unavailable operator"):
        materialized.step()


def test_contract_and_render_do_not_repeat_family_validation_or_registry_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validation_calls = 0

    def validate(row: DatasetRowContract, row_set: DatasetRowSetContract) -> None:
        nonlocal validation_calls
        validation_calls += 1

    registry = DatasetFamilyRegistry()
    registry.register(replace(make_test_registration(), row_validator=validate))
    registry.freeze()
    dataset = make_logical_dataset(registry=registry)
    admitted_calls = validation_calls
    contract = dataset.contract()
    assert validation_calls == admitted_calls
    expected = contract.render()

    def unexpected_read(*args: object, **kwargs: object) -> None:
        raise AssertionError("terminal snapshot consulted live registration")

    monkeypatch.setattr(DatasetFamilyRegistry, "consumers_for", unexpected_read)
    monkeypatch.setattr(DatasetFamilyRegistry, "get", unexpected_read)
    assert contract.render() == expected
    assert validation_calls == admitted_calls


def test_contract_snapshot_stays_structurally_bounded_even_with_no_byte_bound() -> None:
    row, row_set = make_row_contracts()
    columns = tuple(
        replace(
            row.schema.columns[0],
            _token=_CORE_TOKEN,
            field_id=_make_field_id(f"value_{i}"),
            name=f"value_{i}",
        )
        for i in range(100)
    )
    wide_row = replace(row, _token=_CORE_TOKEN, schema=_make_schema(columns))
    consumers = tuple(
        ConsumerRegistration(
            id=f"test.consumer_{i:02d}.step",
            input_roles=("input",),
            output_family="test",
            accepted_shape_ids=(row.shape_id,),
        )
        for i in range(40)
    )
    dataset = make_logical_dataset(
        registry=make_test_registry(consumers=consumers),
        contracts=(wide_row, row_set),
        requirements=tuple(f"test.requirement_{i}" for i in range(40)),
        dependency_facts=tuple(f"test.dependency_{i}" for i in range(40)),
    )
    contract = dataset.contract()
    assert len(contract.render().encode("utf-8")) <= 8192
    full = contract.render(max_output_bytes=None)
    assert "88" in full and "omitted" in full
    assert "28 additional operators omitted" in full
    assert "28 additional facts omitted" in full
    assert len(full.encode("utf-8")) < 16000
    assert len(contract.render(max_output_bytes=2048).encode("utf-8")) <= 2048
    with pytest.raises(ValueError, match="too small"):
        contract.render(max_output_bytes=1)


def test_contract_is_sealed_immutable_and_has_no_arbitrary_public_payload() -> None:
    contract = make_logical_dataset().contract()
    with pytest.raises(TypeError, match="no public constructor"):
        DatasetContract()
    with pytest.raises(AttributeError, match="immutable"):
        contract._identity = "changed"
    with pytest.raises(TypeError, match="cannot be pickled"):
        pickle.dumps(contract)
    assert not hasattr(contract, "payload")
    assert not hasattr(contract, "_dataset")
    assert not hasattr(contract, "_root")
    assert not hasattr(contract, "__dict__")
