"""Independent acceptance of immutable Dataset values and definition identity."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import operator
import pickle
import subprocess
import sys
from dataclasses import FrozenInstanceError, dataclass, fields, replace
from pathlib import Path

import pytest

from marivo.analysis.datasets.base import Dataset, LogicalDataset, MaterializedDataset
from marivo.analysis.datasets.descriptors import (
    _CORE_TOKEN,
    _canonical_digest,
    _make_field_id,
    _make_order_term,
    _ordered_ordering,
    _row_contract_fingerprint,
    _row_set_contract_fingerprint,
)
from marivo.analysis.datasets.errors import DatasetConstructionError, DatasetDefinitionError
from marivo.analysis.datasets.handles import (
    CanonicalValue,
    DefinitionInput,
    LogicalInputToken,
    MaterializedInputToken,
    RealizationHandle,
    _check_label,
    _digest,
    _LogicalNodePayload,
    _sharing_occurrences,
)
from marivo.analysis.datasets.registry import DatasetFamilyRegistry
from marivo.render import AgentResult
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


@dataclass(frozen=True, slots=True, repr=False)
class _SourcePayload(_LogicalNodePayload, _token=_CORE_TOKEN):
    captured: str

    @property
    def identity_payload(self) -> CanonicalValue:
        return ("test.source/v1", _canonical_digest((self.captured,)))


def _payload_registry() -> DatasetFamilyRegistry:
    registry = DatasetFamilyRegistry()
    registry.register(replace(make_test_registration(), node_payload_types=(_SourcePayload,)))
    registry.freeze()
    return registry


def test_owner_payload_is_immutable_private_and_uses_one_safe_definition_projection() -> None:
    payload = _SourcePayload(_token=_CORE_TOKEN, captured="private-source-parameter-7294")
    registry = _payload_registry()
    source = make_logical_dataset(registry=registry, payload=payload)
    assert source._root.payload is payload
    assert source._root.parameters == payload.identity_payload
    assert (
        source.definition_fingerprint
        == make_logical_dataset(
            registry=registry, payload=replace(payload, _token=_CORE_TOKEN)
        ).definition_fingerprint
    )
    changed = replace(payload, _token=_CORE_TOKEN, captured="another-private-value")
    assert (
        source.definition_fingerprint
        != make_logical_dataset(registry=registry, payload=changed).definition_fingerprint
    )
    for display in (
        repr(source),
        repr(payload),
        repr(source._root),
        source.contract().render(max_output_bytes=None),
        repr(source._lineage),
        repr(source._root.parameters),
    ):
        assert payload.captured not in display
    with pytest.raises(FrozenInstanceError):
        payload.captured = "mutated"
    with pytest.raises(DatasetDefinitionError, match="serialization"):
        pickle.dumps(payload)
    with pytest.raises(DatasetDefinitionError, match="duplicate parameters"):
        make_logical_dataset(registry=registry, payload=payload, parameters=("second projection",))
    with pytest.raises(DatasetConstructionError, match="unregistered payload"):
        make_logical_dataset(payload=payload)
    with pytest.raises(DatasetDefinitionError, match="direct root construction"):
        _SourcePayload(_token=object(), captured="private")


def test_payload_authority_stops_at_the_materialized_leaf() -> None:
    payload = _SourcePayload(_token=_CORE_TOKEN, captured="private-captured-value")
    source = make_logical_dataset(registry=_payload_registry(), payload=payload)
    downstream = source.step()
    assert downstream._root.payload is None
    assert downstream._root.inputs[0].root is source._root
    materialized = make_materialized_dataset(origin=downstream, registry=source._registry)
    leaf = materialized._root
    assert not hasattr(leaf, "payload")
    assert not hasattr(leaf, "inputs")
    assert materialized.step()._root.inputs[0].root is leaf


def test_payload_projection_is_rechecked_on_downstream_admission() -> None:
    payload = _SourcePayload(_token=_CORE_TOKEN, captured="private-value")
    source = make_logical_dataset(registry=_payload_registry(), payload=payload)
    object.__setattr__(payload, "captured", "corrupt-value")
    with pytest.raises(DatasetDefinitionError, match="changed payload"):
        source.step()


def test_payload_subclasses_are_sealed_and_cannot_expose_default_repr() -> None:
    with pytest.raises(DatasetDefinitionError, match="untrusted subclass"):

        class UntrustedPayload(_LogicalNodePayload):
            pass

    @dataclass(frozen=True, slots=True)
    class ExposedPayload(_LogicalNodePayload, _token=_CORE_TOKEN):
        @property
        def identity_payload(self) -> CanonicalValue:
            return ()

    with pytest.raises(DatasetDefinitionError, match="invalid payload class"):
        replace(make_test_registration(), node_payload_types=(ExposedPayload,))


def test_definition_identity_is_explicit_and_datasets_are_unhashable() -> None:
    first = make_logical_dataset()
    second = make_logical_dataset()
    assert first is not second
    assert first != second
    assert first == first
    assert first.definition_fingerprint == second.definition_fingerprint
    assert len(first.definition_fingerprint) == 67
    with pytest.raises(TypeError):
        hash(first)
    assert make_materialized_dataset() != make_materialized_dataset()


@pytest.mark.parametrize("materialized", [False, True])
def test_values_are_immutable_and_have_no_dataframe_or_recovery_protocol(
    materialized: bool,
) -> None:
    value = make_materialized_dataset() if materialized else make_logical_dataset()
    assert value.schema is value.row_contract.schema
    assert not isinstance(value, AgentResult)
    assert not hasattr(value, "render")
    assert not hasattr(value, "columns")
    assert not hasattr(value, "iloc")
    assert not hasattr(value, "__dataframe__")
    assert not hasattr(value, "__array__")
    for name in ("kind", "row_contract", "schema", "state", "definition_fingerprint"):
        with pytest.raises((AttributeError, TypeError, DatasetConstructionError)):
            setattr(value, name, None)
        with pytest.raises((AttributeError, TypeError, DatasetConstructionError)):
            delattr(value, name)
    for action in (len, iter, lambda item: operator.getitem(item, "value")):
        with pytest.raises(TypeError):
            action(value)
    assert copy.copy(value) is value
    assert copy.deepcopy(value) is value
    with pytest.raises((TypeError, DatasetConstructionError)):
        pickle.dumps(value)


def test_paired_states_have_distinct_action_method_sets() -> None:
    logical = make_logical_dataset()
    materialized = make_materialized_dataset()
    assert isinstance(logical, LogicalTestDataset)
    assert isinstance(materialized, MaterializedTestDataset)
    assert callable(logical.execute)
    assert not hasattr(logical, "show")
    assert not hasattr(logical, "to_pandas")
    assert not hasattr(logical, "findings")
    assert not hasattr(materialized, "execute")
    assert callable(materialized.show)
    assert callable(materialized.to_pandas)
    assert callable(materialized.findings)


@pytest.mark.parametrize("base", [Dataset, LogicalDataset, MaterializedDataset])
def test_public_base_construction_and_external_subclassing_fail(base: type[Dataset]) -> None:
    with pytest.raises((TypeError, DatasetConstructionError)):
        base()
    with pytest.raises((TypeError, DatasetConstructionError)):
        type("ExternalDataset", (base,), {})


def test_generic_downstream_returns_new_logical_values_from_either_state() -> None:
    owner = make_owner()
    registry = make_test_registry()
    for source in (
        make_logical_dataset(owner=owner, registry=registry),
        make_materialized_dataset(owner=owner, registry=registry),
    ):
        output = make_logical_dataset(
            owner=owner, registry=registry, operation_id="test.step", inputs=(source,)
        )
        assert isinstance(output, LogicalTestDataset)
        assert output is not source
        assert output.schema is output.row_contract.schema
        assert output.row_contract == source.row_contract
        assert output.row_set_contract == source.row_set_contract
        assert output.state.kind == "logical"


def test_logical_ownership_and_materialized_store_admission_are_distinct() -> None:
    first = make_owner(session_id="first")
    second = make_owner(session_id="second")
    registry = make_test_registry()
    logical = make_logical_dataset(owner=first, registry=registry)
    with pytest.raises(DatasetConstructionError):
        make_logical_dataset(
            owner=second, registry=registry, operation_id="test.step", inputs=(logical,)
        )
    materialized = make_materialized_dataset(owner=first, registry=registry)
    result = make_logical_dataset(
        owner=second, registry=registry, operation_id="test.step", inputs=(materialized,)
    )
    assert result.state.kind == "logical"
    with pytest.raises(DatasetConstructionError):
        make_logical_dataset(
            owner=make_owner(store_id="other-store"),
            registry=registry,
            operation_id="test.step",
            inputs=(materialized,),
        )


def _combine(*sources: Dataset) -> LogicalTestDataset:
    return make_logical_dataset(operation_id="test.combine", inputs=sources)


def test_significant_occurrences_distinguish_shared_and_independent_producers() -> None:
    shared = make_logical_dataset(significant=True)
    independent = make_logical_dataset(significant=True)
    assert shared.definition_fingerprint == independent.definition_fingerprint
    shared_uses = _combine(shared, shared)
    independent_uses = _combine(shared, independent)
    assert shared_uses.definition_fingerprint != independent_uses.definition_fingerprint
    assert [ordinal for _, ordinal in _sharing_occurrences(shared_uses._root.inputs, ())] == [0, 0]
    assert [ordinal for _, ordinal in _sharing_occurrences(independent_uses._root.inputs, ())] == [
        0,
        1,
    ]
    reconstructed = make_logical_dataset(significant=True)
    assert (
        shared_uses.definition_fingerprint
        == _combine(reconstructed, reconstructed).definition_fingerprint
    )


def test_sharing_through_nested_roles_and_pure_node_sharing_are_normalized() -> None:
    shared = make_logical_dataset(significant=True)
    repeated = make_logical_dataset(operation_id="test.step", inputs=(shared,))
    reconstructed = make_logical_dataset(operation_id="test.step", inputs=(shared,))
    assert (
        _combine(repeated, repeated).definition_fingerprint
        == _combine(repeated, reconstructed).definition_fingerprint
    )
    first = make_logical_dataset()
    second = make_logical_dataset()
    assert (
        _combine(first, first).definition_fingerprint
        == _combine(first, second).definition_fingerprint
    )


def test_graph_local_realization_handles_never_enter_canonical_identity() -> None:
    left_handle = RealizationHandle()
    right_handle = RealizationHandle()
    assert left_handle is not right_handle
    left = make_logical_dataset(significant=True, realization_handle=left_handle)
    right = make_logical_dataset(significant=True, realization_handle=right_handle)
    assert left.definition_fingerprint == right.definition_fingerprint
    assert repr(left_handle) == repr(right_handle)
    assert "0x" not in repr(left_handle)


def test_materialized_scan_leaf_cuts_off_origin_and_uses_only_exact_artifact_ref() -> None:
    origin_a = _combine(make_logical_dataset(significant=True), make_logical_dataset())
    origin_b = make_logical_dataset()
    first = make_materialized_dataset(origin=origin_a)
    second = make_materialized_dataset(origin=origin_b)
    assert first.definition_fingerprint != second.definition_fingerprint
    left = make_logical_dataset(operation_id="test.step", inputs=(first,))
    right = make_logical_dataset(operation_id="test.step", inputs=(second,))
    assert left.definition_fingerprint == right.definition_fingerprint
    assert _sharing_occurrences(left._root.inputs, ()) == ()
    assert not hasattr(first._root, "inputs")
    assert not hasattr(first._root, "origin")
    different_ref = make_materialized_dataset(artifact_ref="art_other", origin=origin_a)
    assert (
        left.definition_fingerprint
        != make_logical_dataset(
            operation_id="test.step", inputs=(different_ref,)
        ).definition_fingerprint
    )


def test_authority_tokens_have_exact_closed_field_sets_and_reject_cross_pairing() -> None:
    assert tuple(field.name for field in fields(LogicalInputToken)) == ("definition_fingerprint",)
    assert tuple(field.name for field in fields(MaterializedInputToken)) == ("artifact_ref",)
    logical = make_logical_dataset()
    materialized = make_materialized_dataset()
    with pytest.raises(DatasetDefinitionError):
        DefinitionInput(
            "input", LogicalInputToken(logical.definition_fingerprint), materialized._root
        )
    with pytest.raises(DatasetDefinitionError):
        DefinitionInput(
            "input", MaterializedInputToken(materialized.state.artifact_ref), logical._root
        )
    with pytest.raises(DatasetDefinitionError):
        replace(
            DefinitionInput(
                "input", LogicalInputToken(logical.definition_fingerprint), logical._root
            ),
            token=LogicalInputToken("ds_" + "0" * 64),
        )


@pytest.mark.parametrize("bad", [math.inf, -math.inf, math.nan])
def test_non_finite_definition_literals_fail_before_output(bad: float) -> None:
    with pytest.raises(DatasetDefinitionError) as exc_info:
        make_logical_dataset(parameters=(bad,))
    assert exc_info.value.expected
    assert exc_info.value.received
    assert exc_info.value.location
    assert "0x" not in str(exc_info.value)


def test_literal_types_and_order_are_part_of_definition_identity() -> None:
    values = (True, 1, 1.0, "1", (1, 2), (2, 1))
    fingerprints = {
        make_logical_dataset(parameters=(value,)).definition_fingerprint for value in values
    }
    assert len(fingerprints) == len(values)


def test_descriptor_and_definition_encoders_share_an_independent_canonical_vector() -> None:
    value = (None, True, 1, -0.0, "\u00e9", (False,))
    encoded = (
        b'["tuple",[["null"],["bool",true],["int","1"],'
        b'["float","-0x0.0p+0"],["str","\\u00e9"],["tuple",[["bool",false]]]]]'
    )
    expected = hashlib.sha256(encoded).hexdigest()
    assert _canonical_digest(value) == expected
    assert _digest(value) == expected
    values = (True, 1, 1.0, "1", 0.0, -0.0, (), (1, 2), (2, 1))
    assert len({_canonical_digest(item) for item in values}) == len(values)


def test_canonical_encoder_rejects_tuple_subclasses_with_owner_specific_errors() -> None:
    class TupleSubclass(tuple[int, ...]):
        pass

    value = TupleSubclass((1, 2))
    with pytest.raises(DatasetConstructionError) as descriptor_error:
        _canonical_digest(value)
    assert type(descriptor_error.value) is DatasetConstructionError
    with pytest.raises(DatasetDefinitionError) as definition_error:
        _digest(value)
    assert definition_error.value.received == "TupleSubclass"
    assert definition_error.value.location == "dataset.definition"


@pytest.mark.parametrize("value", ["a", "a" * 160, "test/object.v1:role_1@v2+-"])
def test_descriptor_and_definition_ids_accept_the_same_canonical_vocabulary(value: str) -> None:
    assert _make_field_id(value).value == value
    _check_label(value)


@pytest.mark.parametrize("value", ["", "a" * 161, "/bad", "a*b", "\u00e9", "a\x7f", "a\tb"])
def test_descriptor_and_definition_ids_reject_the_same_invalid_vocabulary(value: str) -> None:
    with pytest.raises(DatasetConstructionError):
        _make_field_id(value)
    with pytest.raises(DatasetDefinitionError):
        _check_label(value)


def test_root_retains_immutable_normalized_definition_inputs_for_later_consumers() -> None:
    parameters = ("test.window", (1, True, None, 2.5))
    dependencies = ("metric:sales.revenue", "entity:sales.orders")
    versions = (("test.operator", "v1"),)
    value = make_logical_dataset(
        parameters=parameters, dependency_facts=dependencies, contract_versions=versions
    )
    assert value._root.parameters == parameters
    assert value._root.dependency_facts == dependencies
    assert value._root.contract_versions == versions
    with pytest.raises(FrozenInstanceError):
        value._root.parameters = ("changed",)
    assert (
        value.definition_fingerprint
        != make_logical_dataset(
            parameters=("test.window", (2, True, None, 2.5)),
            dependency_facts=dependencies,
            contract_versions=versions,
        ).definition_fingerprint
    )
    assert (
        value.definition_fingerprint
        != make_logical_dataset(
            parameters=parameters,
            dependency_facts=dependencies,
            contract_versions=(("test.operator", "v2"),),
        ).definition_fingerprint
    )
    assert (
        value.definition_fingerprint
        != make_logical_dataset(
            parameters=parameters,
            dependency_facts=tuple(reversed(dependencies)),
            contract_versions=versions,
        ).definition_fingerprint
    )
    assert "test.window" not in repr(value._root)


def test_equivalent_sharing_reconstructs_across_fresh_processes() -> None:
    script = """
import json
from tests.lazy_dataset_fixtures import make_logical_dataset
shared = make_logical_dataset(significant=True)
independent = make_logical_dataset(significant=True)
def combine(a, b):
    return make_logical_dataset(operation_id='test.combine', inputs=(a, b)).definition_fingerprint
print(json.dumps([combine(shared, shared), combine(shared, independent)]))
"""
    fingerprints: list[list[str]] = []
    for _ in range(2):
        result = subprocess.run(
            [sys.executable, "-B", "-c", script],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        fingerprints.append(json.loads(result.stdout))
    assert fingerprints[0] == fingerprints[1]
    assert fingerprints[0][0] != fingerprints[0][1]


def test_lineage_retains_contract_transitions_and_stops_at_materialized_artifacts() -> None:
    row, row_set = make_row_contracts("entity")
    source = make_logical_dataset(contracts=(row, row_set))
    ordered_row_set = replace(
        row_set,
        _token=_CORE_TOKEN,
        ordering=_ordered_ordering(
            (
                _make_order_term(
                    row.key_field_ids[0],
                    direction="ascending",
                    nulls="last",
                    value_order_contract_id="test.string",
                    ids=TEST_IDS,
                ),
            )
        ),
    )
    ordered = make_logical_dataset(
        operation_id="test.step", inputs=(source,), contracts=(row, ordered_row_set)
    )
    scalar = make_logical_dataset(
        operation_id="test.step", inputs=(ordered,), contracts=make_row_contracts("scalar")
    )
    assert source.row_contract == ordered.row_contract
    assert source.row_set_contract != ordered.row_set_contract
    assert scalar.row_contract.shape_id != ordered.row_contract.shape_id
    for dataset in (source, ordered, scalar):
        current = dataset._lineage.facts[0]
        assert f"definition={dataset.definition_fingerprint}" in current
        assert f"row={_row_contract_fingerprint(dataset.row_contract)}" in current
        assert f"row_set={_row_set_contract_fingerprint(dataset.row_set_contract)}" in current
        assert len(current) <= 512
        assert dataset._lineage.omitted_count == 0
    assert ordered._lineage.facts[1:] == source._lineage.facts
    assert scalar._lineage.facts[1:] == ordered._lineage.facts

    materialized = make_materialized_dataset(origin=scalar)
    assert materialized._lineage.facts == ("artifact:art_test",)
    assert materialized._lineage.omitted_count == 0
    downstream = materialized.step()
    assert downstream._lineage.facts[1:] == materialized._lineage.facts
    retained = " ".join(downstream._lineage.facts)
    for origin in (source, ordered, scalar):
        assert origin.definition_fingerprint not in retained
