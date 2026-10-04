"""Exact tuple arity is preserved across private logical and physical masks."""

from dataclasses import replace

import pyarrow as pa
import pytest

from marivo.analysis.datasets import descriptors as d
from marivo.analysis.datasets.errors import DatasetConstructionError, DatasetRegistrationError
from marivo.analysis.datasets.registry import DatasetFamilyRegistry
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.scalar_sql_execution import _cell
from tests.lazy_dataset_fixtures import (
    TEST_IDS,
    make_logical_dataset,
    make_row_contracts,
    make_test_registration,
)


def test_internal_preparation_is_registered_without_disclosing_a_second_entrypoint() -> None:
    registration = make_test_registration()
    preparation = replace(registration.consumers[0], id="test.prepare", discoverable=False)
    registry = DatasetFamilyRegistry()
    registry.register(replace(registration, consumers=(*registration.consumers, preparation)))
    registry.freeze()
    dataset = make_logical_dataset(registry=registry)
    assert registry.consumer(dataset, "test.prepare") is preparation
    assert preparation not in registry.consumers_for(dataset)
    assert "test.prepare" not in dataset.contract().render()
    assert not hasattr(dataset, "prepare")
    invalid = replace(preparation)
    object.__setattr__(invalid, "discoverable", 1)
    with pytest.raises(DatasetRegistrationError):
        invalid.__post_init__()


def test_boolean_tuple_arity_refines_only_to_the_same_registered_physical_type() -> None:
    ids = replace(
        TEST_IDS,
        logical_types=TEST_IDS.logical_types | {"bool_tuple"},
        physical_types=TEST_IDS.physical_types | {"bool_tuple"},
        admitted_types=TEST_IDS.admitted_types | {"bool_tuple"},
        physical_type_classes=TEST_IDS.physical_type_classes | {("bool_tuple", "bool_tuple")},
    )
    row, _ = make_row_contracts()
    original = row.schema.columns[0]
    logical = replace(
        original,
        _token=d._CORE_TOKEN,
        logical_type_id="bool_tuple:2",
        physical_type_state=d._deferred_type("bool_tuple:2", ids=ids),
    )
    realized = replace(
        logical, _token=d._CORE_TOKEN, physical_type_state=d._resolved_type("bool_tuple:2", ids=ids)
    )
    d._validate_realized_schema(d._make_schema((logical,)), d._make_schema((realized,)), ids=ids)
    wrong = replace(
        logical, _token=d._CORE_TOKEN, physical_type_state=d._resolved_type("bool_tuple:3", ids=ids)
    )
    with pytest.raises(DatasetConstructionError):
        d._validate_realized_schema(d._make_schema((logical,)), d._make_schema((wrong,)), ids=ids)
    with pytest.raises(DatasetConstructionError):
        d._deferred_type("bool_tuple:2", ids=TEST_IDS)
    for invalid in ("bool_tuple:0", "bool_tuple:-1", "bool_tuple:02", "bool_tuple:two"):
        with pytest.raises(DatasetConstructionError):
            d._deferred_type(invalid, ids=ids)


def test_source_boolean_array_accepts_only_exact_driver_bits() -> None:
    dtype = pa.list_(pa.bool_())
    assert _cell([1, 0, True], dtype) == [True, False, True]
    with pytest.raises(MaterializationError, match="invalid Boolean representation"):
        _cell([2], dtype)
    with pytest.raises(MaterializationError, match="invalid array representation"):
        _cell("10", dtype)


def test_internal_operands_admit_only_the_shape_registered_for_their_role() -> None:
    registration = make_test_registration()
    scalar, entity = registration.shape_ids
    preparation = replace(
        registration.consumers[1],
        id="test.prepare",
        discoverable=False,
        accepted_shape_ids=(scalar,),
        operand_shape_ids=((scalar,), (entity,)),
    )
    registry = DatasetFamilyRegistry()
    registry.register(replace(registration, consumers=(preparation,)))
    registry.freeze()
    left = make_logical_dataset(registry=registry)
    right = make_logical_dataset(
        registry=registry, owner=left._owner, contracts=make_row_contracts("entity")
    )
    make_logical_dataset(
        registry=registry,
        owner=left._owner,
        operation_id="test.prepare",
        inputs=(left, right),
    )
    with pytest.raises(DatasetConstructionError, match="unsupported input shape"):
        make_logical_dataset(
            registry=registry,
            owner=left._owner,
            operation_id="test.prepare",
            inputs=(left, left),
        )
    for invalid in (((scalar,),), ((scalar,), ()), ((entity,), (scalar,))):
        with pytest.raises(DatasetRegistrationError):
            replace(preparation, operand_shape_ids=invalid)
