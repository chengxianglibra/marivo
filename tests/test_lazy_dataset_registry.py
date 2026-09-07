"""Closed family registration and private state/root corruption tests."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from marivo.analysis.datasets.base import Dataset, LogicalDataset, MaterializedDataset
from marivo.analysis.datasets.descriptors import (
    _CORE_TOKEN,
    DatasetRowContract,
    DatasetRowSetContract,
    _keyed_cardinality,
    _make_field,
    _make_schema,
    _make_shape_id,
    _static_row_bound,
)
from marivo.analysis.datasets.errors import DatasetConstructionError, DatasetRegistrationError
from marivo.analysis.datasets.handles import (
    DefinitionInput,
    LogicalRootHandle,
    MaterializedScanLeafHandle,
    _make_logical_root,
)
from marivo.analysis.datasets.registry import (
    REGISTRY,
    DatasetFamilyRegistration,
    DatasetFamilyRegistry,
)
from marivo.analysis.datasets.state import LogicalDatasetState, MaterializedDatasetState
from tests.lazy_dataset_fixtures import (
    TEST_IDS,
    LogicalTestDataset,
    MaterializedTestDataset,
    make_logical_dataset,
    make_materialized_dataset,
    make_owner,
    make_row_contracts,
    make_test_registration,
    make_test_registry,
)


def test_production_registry_is_empty_and_fresh_instances_have_no_shared_assembly() -> None:
    assert REGISTRY.registrations == ()
    empty = DatasetFamilyRegistry()
    populated = make_test_registry()
    assert empty.registrations == ()
    assert len(populated.registrations) == 1
    with pytest.raises(DatasetRegistrationError) as exc_info:
        empty.get("test")
    assert exc_info.value.expected
    assert exc_info.value.received == "unknown family"
    assert exc_info.value.location == "dataset.registry"
    assert exc_info.value.hint


def test_complete_pair_registers_once_and_is_immutable() -> None:
    registry = make_test_registry(frozen=False)
    registration = registry.get("test")
    assert registration.logical_type is LogicalTestDataset
    assert registration.materialized_type is MaterializedTestDataset
    assert tuple(str(shape) for shape in registration.shape_ids) == (
        "test/scalar@v1",
        "test/entity@v1",
    )
    with pytest.raises(DatasetRegistrationError):
        registry.register(registration)
    with pytest.raises(FrozenInstanceError):
        registration.family_id = "changed"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("logical_type", None),
        ("materialized_type", None),
        ("logical_type", MaterializedTestDataset),
        ("materialized_type", LogicalTestDataset),
        ("logical_type", LogicalDataset),
        ("materialized_type", MaterializedDataset),
        ("logical_type", object),
        ("shape_ids", ()),
        ("row_validator", None),
        ("repr_renderer", None),
        ("materialized_state_decoder", None),
    ],
)
def test_incomplete_or_invalid_family_registration_fails(name: str, value: object) -> None:
    with pytest.raises(DatasetRegistrationError):
        replace(make_test_registration(), **{name: value})


def test_duplicate_shape_consumers_and_unavailable_methods_are_rejected() -> None:
    registration = make_test_registration()
    with pytest.raises(DatasetRegistrationError):
        replace(registration, shape_ids=(registration.shape_ids[0],) * 2)
    with pytest.raises(DatasetRegistrationError):
        replace(registration, consumers=(registration.consumers[0],) * 2)
    with pytest.raises(DatasetRegistrationError):
        replace(
            registration,
            consumers=(replace(registration.consumers[0], id="test.absent"),),
        )


def test_exact_registered_semantic_shape_version_is_required() -> None:
    registration = make_test_registration()
    ids = replace(TEST_IDS, shapes=TEST_IDS.shapes | {("test", "scalar", 2)})
    other_version = _make_shape_id("test", "scalar", 2, ids=ids)
    with pytest.raises(DatasetRegistrationError):
        replace(
            registration,
            consumers=(replace(registration.consumers[0], accepted_shape_ids=(other_version,)),),
        )
    dataset = make_logical_dataset()
    wrong_row = replace(dataset.row_contract, _token=_CORE_TOKEN, shape_id=other_version)
    with pytest.raises(DatasetRegistrationError):
        make_logical_dataset(contracts=(wrong_row, dataset.row_set_contract))


def test_consumer_output_family_must_exist_before_registry_freezes() -> None:
    registration = make_test_registration()
    registry = DatasetFamilyRegistry()
    registry.register(
        replace(
            registration,
            consumers=(replace(registration.consumers[0], output_family="missing"),),
        )
    )
    before = registry.registrations
    with pytest.raises(DatasetRegistrationError):
        registry.freeze()
    assert registry.registrations == before
    assert registry._frozen is False
    registry.register(_alternate_registration())
    assert len(registry.registrations) == 2


def test_explicitly_frozen_registration_has_common_methods_on_both_states() -> None:
    registry = make_test_registry()
    logical = make_logical_dataset(registry=registry)
    materialized = make_materialized_dataset(registry=registry)
    with pytest.raises(DatasetRegistrationError):
        registry.register(make_test_registration())
    assert isinstance(logical.step(), LogicalTestDataset)
    assert isinstance(materialized.step(), LogicalTestDataset)
    assert isinstance(logical.combine(materialized), LogicalTestDataset)
    assert isinstance(materialized.combine(logical), LogicalTestDataset)
    assert registry.consumers_for(logical) == registry.consumers_for(materialized)


@pytest.mark.parametrize("materialized", [False, True])
@pytest.mark.parametrize("invalid", [False, True])
def test_unfrozen_construction_rejects_without_mutating_or_finishing_assembly(
    materialized: bool,
    invalid: bool,
) -> None:
    validation_calls = 0

    def validate(row: DatasetRowContract, row_set: DatasetRowSetContract) -> None:
        nonlocal validation_calls
        validation_calls += 1

    registry = DatasetFamilyRegistry()
    registry.register(replace(make_test_registration(), row_validator=validate))
    before = registry.registrations
    storage = registry._registrations
    with pytest.raises(DatasetRegistrationError) as exc_info:
        if materialized:
            state = make_materialized_dataset().state
            if invalid:
                state = replace(state, _token=_CORE_TOKEN, realized_row_count=0)
            make_materialized_dataset(registry=registry, state=state)
        else:
            make_logical_dataset(registry=registry, parameters=(float("nan"),) if invalid else ())
    assert exc_info.value.received == "unfrozen registry"
    assert validation_calls == 0
    assert registry._frozen is False
    assert registry._registrations is storage
    assert registry.registrations == before
    registry.register(_alternate_registration())
    assert len(registry.registrations) == 2


def test_successful_and_failed_construction_only_read_the_finalized_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = make_test_registry()
    state = make_materialized_dataset(registry=registry).state
    before = registry.registrations
    storage = registry._registrations

    def reject_freeze(self: DatasetFamilyRegistry) -> None:
        raise AssertionError("Dataset construction must not finish registry assembly.")

    monkeypatch.setattr(DatasetFamilyRegistry, "freeze", reject_freeze)
    logical = make_logical_dataset(registry=registry)
    materialized = make_materialized_dataset(registry=registry, state=state)
    assert isinstance(logical.step(), LogicalTestDataset)
    assert isinstance(materialized.step(), LogicalTestDataset)
    with pytest.raises(DatasetConstructionError):
        make_logical_dataset(registry=registry, parameters=(float("nan"),))
    invalid_state = replace(state, _token=_CORE_TOKEN, realized_row_count=0)
    with pytest.raises(DatasetConstructionError):
        make_materialized_dataset(registry=registry, state=invalid_state)
    assert registry._frozen is True
    assert registry._registrations is storage
    assert registry.registrations == before


def _alternate_registration() -> DatasetFamilyRegistration:
    class LogicalAlternate(LogicalTestDataset, _token=_CORE_TOKEN, family_id="alternate"):
        __slots__ = ()

    class MaterializedAlternate(MaterializedTestDataset, _token=_CORE_TOKEN, family_id="alternate"):
        __slots__ = ()

    ids = replace(
        TEST_IDS, families=frozenset({"alternate"}), shapes=frozenset({("alternate", "scalar", 1)})
    )
    shape = _make_shape_id("alternate", "scalar", 1, ids=ids)
    return replace(
        make_test_registration(),
        family_id="alternate",
        owner_id="tests.alternate",
        logical_type=LogicalAlternate,
        materialized_type=MaterializedAlternate,
        ids=ids,
        shape_ids=(shape,),
        consumers=(),
    )


def test_registration_order_does_not_change_lookup_projection_or_fingerprints() -> None:
    first = DatasetFamilyRegistry()
    second = DatasetFamilyRegistry()
    registration = make_test_registration()
    alternate = _alternate_registration()
    first.register(registration)
    first.register(alternate)
    second.register(alternate)
    second.register(registration)
    first.freeze()
    second.freeze()
    assert first.registrations == second.registrations
    assert tuple(item.family_id for item in first.registrations) == ("alternate", "test")
    assert (
        make_logical_dataset(registry=first).definition_fingerprint
        == make_logical_dataset(registry=second).definition_fingerprint
    )


def test_duplicate_owner_is_rejected_independently_of_distinct_family_and_shapes() -> None:
    registry = make_test_registry(frozen=False)
    with pytest.raises(DatasetRegistrationError):
        registry.register(
            replace(_alternate_registration(), owner_id="tests.lazy_dataset_fixtures")
        )


def _reconstruct(
    dataset: Dataset,
    *,
    state: LogicalDatasetState | MaterializedDatasetState,
    root: LogicalRootHandle | MaterializedScanLeafHandle,
    registration: DatasetFamilyRegistration | None = None,
    registry: DatasetFamilyRegistry | None = None,
    definition_fingerprint: str | None = None,
) -> Dataset:
    return type(dataset)(
        _token=_CORE_TOKEN,
        owner=dataset._owner,
        registration=dataset._registration if registration is None else registration,
        registry=dataset._registry if registry is None else registry,
        row_contract=dataset.row_contract,
        row_set_contract=dataset.row_set_contract,
        state=state,
        root=root,
        definition_fingerprint=(
            dataset.definition_fingerprint
            if definition_fingerprint is None
            else definition_fingerprint
        ),
        lineage=dataset._lineage,
        inputs=dataset._inputs,
    )


def test_crossed_state_and_root_pairings_fail_without_coercion() -> None:
    logical = make_logical_dataset()
    materialized = make_materialized_dataset()
    for dataset, state, root in (
        (logical, logical.state, materialized._root),
        (logical, materialized.state, logical._root),
        (materialized, materialized.state, logical._root),
        (materialized, logical.state, materialized._root),
    ):
        with pytest.raises(DatasetConstructionError):
            _reconstruct(dataset, state=state, root=root)


@pytest.mark.parametrize(
    "name", ["session_id", "store_id", "row_contract_fingerprint", "row_set_contract_fingerprint"]
)
def test_corrupt_root_ownership_or_contract_fingerprints_fail(name: str) -> None:
    logical = make_logical_dataset()
    root = replace(logical._root, _token=_CORE_TOKEN, **{name: "corrupt"})
    with pytest.raises(DatasetConstructionError):
        _reconstruct(logical, state=logical.state, root=root)


def test_materialized_state_must_match_exact_scan_authority() -> None:
    materialized = make_materialized_dataset()
    for name in ("artifact_session_ref", "content_authority_digest"):
        bad_state = replace(materialized.state, _token=_CORE_TOKEN, **{name: "different"})
        with pytest.raises(DatasetConstructionError):
            _reconstruct(materialized, state=bad_state, root=materialized._root)


def test_private_root_construction_and_serialization_are_closed() -> None:
    logical = make_logical_dataset()
    with pytest.raises(DatasetConstructionError):
        replace(logical._root, _token=None)
    with pytest.raises(DatasetConstructionError):
        logical._root.__reduce_ex__(5)


def test_trusted_reconstruction_requires_the_exact_registry_owned_registration() -> None:
    dataset = make_logical_dataset()
    another_registration = make_test_registration()
    assert another_registration == dataset._registration
    assert another_registration is not dataset._registration
    with pytest.raises(DatasetConstructionError) as exc_info:
        _reconstruct(
            dataset, state=dataset.state, root=dataset._root, registration=another_registration
        )
    assert exc_info.value.received == "foreign registration"


@pytest.mark.parametrize("materialized", [False, True])
def test_raw_trusted_construction_also_requires_explicit_registry_finalization(
    materialized: bool,
) -> None:
    dataset = make_materialized_dataset() if materialized else make_logical_dataset()
    registry = DatasetFamilyRegistry()
    registry.register(dataset._registration)
    before = registry.registrations
    with pytest.raises(DatasetRegistrationError) as exc_info:
        _reconstruct(dataset, state=dataset.state, root=dataset._root, registry=registry)
    assert exc_info.value.received == "unfrozen registry"
    assert registry.registrations == before
    assert registry._frozen is False


@pytest.mark.parametrize(
    "fingerprint", ["ds_" + "g" * 64, "ds_" + "A" * 64, "ds_" + "0" * 63, "xx_" + "0" * 64]
)
def test_materialized_definition_fingerprint_is_canonical_lowercase_hex(fingerprint: str) -> None:
    dataset = make_materialized_dataset()
    with pytest.raises(DatasetConstructionError) as exc_info:
        _reconstruct(
            dataset, state=dataset.state, root=dataset._root, definition_fingerprint=fingerprint
        )
    assert exc_info.value.expected == "canonical definition fingerprint"


@pytest.mark.parametrize("row_count", [0, 2])
def test_materialized_singleton_requires_exactly_one_realized_row(row_count: int) -> None:
    dataset = make_materialized_dataset()
    state = replace(dataset.state, _token=_CORE_TOKEN, realized_row_count=row_count)
    with pytest.raises(DatasetConstructionError) as exc_info:
        make_materialized_dataset(state=state)
    assert exc_info.value.expected == "exactly one realized row for a singleton"


def test_materialized_keyed_static_bound_admits_boundary_and_rejects_overflow() -> None:
    row, row_set = make_row_contracts("entity")
    row_set = replace(
        row_set, _token=_CORE_TOKEN, cardinality=_keyed_cardinality(_static_row_bound(2))
    )
    dataset = make_materialized_dataset(contracts=(row, row_set))
    for count in (0, 2):
        state = replace(dataset.state, _token=_CORE_TOKEN, realized_row_count=count)
        assert (
            make_materialized_dataset(
                contracts=(row, row_set), state=state
            ).state.realized_row_count
            == count
        )
    overflow = replace(dataset.state, _token=_CORE_TOKEN, realized_row_count=3)
    with pytest.raises(DatasetConstructionError) as exc_info:
        make_materialized_dataset(contracts=(row, row_set), state=overflow)
    assert exc_info.value.expected == "realized rows within the static row bound"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("operator_id", "test.changed"),
        ("parameters", ("changed",)),
        ("dependency_facts", ("metric:changed",)),
        ("contract_versions", (("test.operator", "v2"),)),
        ("realizations", ()),
        ("has_realizations", False),
    ],
)
def test_changed_logical_definition_cannot_reuse_its_old_fingerprint(
    name: str,
    value: object,
) -> None:
    dataset = make_logical_dataset(
        parameters=("original",),
        dependency_facts=("metric:original",),
        contract_versions=(("test.operator", "v1"),),
        significant=True,
    )
    changed = replace(dataset._root, _token=_CORE_TOKEN, **{name: value})
    assert changed.definition_fingerprint == dataset.definition_fingerprint
    with pytest.raises(DatasetConstructionError):
        _reconstruct(dataset, state=dataset.state, root=changed)
    assert dataset._root.operator_id == "test.source"
    assert dataset._root.has_realizations is True


def _changed_pure_child_binding(parent: LogicalTestDataset) -> DefinitionInput:
    binding = parent._root.inputs[0]
    assert isinstance(binding.root, LogicalRootHandle)
    assert binding.root.has_realizations is False
    changed_child = replace(binding.root, _token=_CORE_TOKEN, parameters=("changed-child",))
    assert changed_child.definition_fingerprint == binding.root.definition_fingerprint
    return DefinitionInput(role=binding.role, token=binding.token, root=changed_child)


def test_nested_rebuilt_input_cannot_smuggle_an_old_parent_fingerprint() -> None:
    parent = make_logical_dataset().step()
    binding = _changed_pure_child_binding(parent)
    changed_parent = replace(parent._root, _token=_CORE_TOKEN, inputs=(binding,))
    assert changed_parent.definition_fingerprint == parent.definition_fingerprint
    with pytest.raises(DatasetConstructionError) as exc_info:
        _reconstruct(parent, state=parent.state, root=changed_parent)
    assert exc_info.value.received == "corrupt logical root"
    valid = _reconstruct(parent, state=parent.state, root=parent._root)
    assert valid.definition_fingerprint == parent.definition_fingerprint


def test_parent_factory_rejects_a_replaced_pure_child_before_issuing_a_root() -> None:
    parent = make_logical_dataset().step()
    binding = _changed_pure_child_binding(parent)
    root = parent._root
    assert isinstance(root, LogicalRootHandle)
    with pytest.raises(DatasetConstructionError) as exc_info:
        _make_logical_root(
            session_id=root.session_id,
            store_id=root.store_id,
            shape_id=root.shape_id,
            row_contract_fingerprint=root.row_contract_fingerprint,
            row_set_contract_fingerprint=root.row_set_contract_fingerprint,
            operator_id=root.operator_id,
            inputs=(binding,),
            parameters=root.parameters,
            realizations=root.realizations,
            requirements=root.requirements,
            dependency_facts=root.dependency_facts,
            contract_versions=root.contract_versions,
        )
    assert exc_info.value.received == "corrupt logical root"


@pytest.mark.parametrize("name", ["input_roles", "accepted_shape_ids", "requirements"])
def test_consumer_registration_rejects_mutable_fact_containers(name: str) -> None:
    consumer = make_test_registration().consumers[0]
    values = list(getattr(consumer, name))
    with pytest.raises(DatasetRegistrationError):
        replace(consumer, **{name: values})
    assert type(getattr(consumer, name)) is tuple


@pytest.mark.parametrize("name", ["shape_ids", "consumers", "unique_tie_breakers"])
def test_family_registration_rejects_mutable_outer_fact_containers(name: str) -> None:
    registration = make_test_registration()
    values = list(getattr(registration, name))
    with pytest.raises(DatasetRegistrationError):
        replace(registration, **{name: values})
    assert type(getattr(registration, name)) is tuple


def test_family_registration_rejects_mutable_nested_tie_breaker() -> None:
    row, _ = make_row_contracts("entity")
    with pytest.raises(DatasetRegistrationError):
        replace(make_test_registration(), unique_tie_breakers=(list(row.key_field_ids),))


@pytest.mark.parametrize("bindings", [[(object(), "value")], ([object(), "value"],)])
def test_dataset_owner_rejects_mutable_runtime_binding_containers(bindings: object) -> None:
    owner = make_owner()
    with pytest.raises(DatasetConstructionError):
        replace(owner, runtime_metric_bindings=bindings)
    assert owner.runtime_metric_bindings == ()


def test_family_admission_rejects_field_roles_validated_against_foreign_ids() -> None:
    row, row_set = make_row_contracts()
    original = row.schema.columns[0]
    foreign_ids = replace(TEST_IDS, roles=TEST_IDS.roles | {"foreign.metric"})
    foreign_field = _make_field(
        field_id=original.field_id,
        name=original.name,
        role_id="foreign.metric",
        identity=original.identity,
        derivation_identity=original.derivation_identity,
        logical_type_id=original.logical_type_id,
        physical_type_state=original.physical_type_state,
        nullable=original.nullable,
        ids=foreign_ids,
    )
    foreign_row = replace(row, _token=_CORE_TOKEN, schema=_make_schema((foreign_field,)))
    assert foreign_field.role_id in foreign_ids.roles
    assert foreign_field.role_id not in TEST_IDS.roles
    with pytest.raises(DatasetConstructionError):
        make_logical_dataset(contracts=(foreign_row, row_set))


def _invalid_family_method(*args: object, **kwargs: object) -> None:
    pass


@pytest.mark.parametrize(
    ("base", "method"),
    [
        (LogicalTestDataset, "show"),
        (MaterializedTestDataset, "execute"),
        (LogicalTestDataset, "__eq__"),
        (MaterializedTestDataset, "__eq__"),
        (LogicalTestDataset, "__hash__"),
        (MaterializedTestDataset, "__hash__"),
        (LogicalTestDataset, "__getitem__"),
        (MaterializedTestDataset, "__getitem__"),
        (LogicalTestDataset, "__setattr__"),
        (MaterializedTestDataset, "__setattr__"),
    ],
)
def test_family_registration_rejects_wrong_state_or_mutable_protocols(
    base: type[LogicalTestDataset] | type[MaterializedTestDataset],
    method: str,
) -> None:
    family_type = type(
        "InvalidTestDataset",
        (base,),
        {"__slots__": (), method: _invalid_family_method},
        _token=_CORE_TOKEN,
        family_id="test",
    )
    field = "logical_type" if issubclass(base, LogicalDataset) else "materialized_type"
    with pytest.raises(DatasetRegistrationError):
        replace(make_test_registration(), **{field: family_type})
