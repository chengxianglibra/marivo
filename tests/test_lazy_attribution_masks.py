"""Exact tuple arity is preserved across private logical and physical masks."""

from dataclasses import replace

import pytest

from marivo.analysis.datasets import descriptors as d
from marivo.analysis.datasets.errors import DatasetConstructionError, DatasetRegistrationError
from marivo.analysis.datasets.registry import DatasetFamilyRegistry
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


def test_local_mask_validation_checks_complete_arrow_keys_and_exact_values() -> None:
    import pandas as pd

    from marivo.analysis.materialization.errors import MaterializationError
    from marivo.analysis.materialization.local import validate_frame
    from marivo.analysis.operators.attribute_values import execute_attribute
    from tests.lazy_attribute_fixtures import inputs

    primary, spec, parts = inputs(
        "order_count",
        [("a", "x"), ("a", "y")],
        [(4, 1), (2, 1)],
        [(1, 1), (1, 1)],
        mode="hierarchy",
    )
    result = execute_attribute(primary, spec, parts)
    validate_frame(result, spec.output_row, spec.output_rows)
    duplicate = pd.concat([result, result.iloc[:1]], ignore_index=True)
    with pytest.raises(MaterializationError, match="duplicate local key"):
        validate_frame(duplicate, spec.output_row, spec.output_rows)
    for invalid in ((True,), (True, 0), None):
        malformed = result.copy()
        malformed["other_mask"] = pd.Series([invalid] * len(result), dtype=object)
        with pytest.raises(MaterializationError, match="invalid local mask"):
            validate_frame(malformed, spec.output_row, spec.output_rows)
