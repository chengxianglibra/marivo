"""Independent row meaning and row-set promises are validated as one pair."""

from __future__ import annotations

from dataclasses import dataclass, field, fields, replace
from typing import Literal

import pytest

from marivo.analysis.datasets.descriptors import (
    _CORE_TOKEN,
    DatasetFamilyRowSemantics,
    DatasetFieldId,
    _complete_from_schema,
    _deferred_type,
    _keyed_cardinality,
    _make_field_id,
    _make_order_term,
    _make_row_contract,
    _make_row_set_contract,
    _make_schema,
    _ordered_ordering,
    _resolved_type,
    _row_contract_fingerprint,
    _row_set_contract_fingerprint,
    _runtime_policy_row_bound,
    _singleton_cardinality,
    _static_row_bound,
    _unordered_ordering,
    _validate_registered_contract,
    _validate_row_contract_pair,
)
from marivo.analysis.datasets.errors import DatasetConstructionError
from tests.lazy_dataset_fixtures import TEST_IDS, make_row_contracts


def test_row_and_row_set_contracts_have_only_their_exact_owned_fields() -> None:
    row, row_set = make_row_contracts("entity")
    _validate_row_contract_pair(row, row_set)
    assert {item.name for item in fields(row)} == {
        "schema_version",
        "shape_id",
        "schema",
        "coordinate_field_ids",
        "key_field_ids",
        "family_semantics",
    }
    assert {item.name for item in fields(row_set)} == {"schema_version", "cardinality", "ordering"}
    assert row.schema.columns[0].field_id in row.coordinate_field_ids
    values = tuple(
        column for column in row.schema.columns if column.field_id not in row.coordinate_field_ids
    )
    assert len(values) == 1
    assert values[0] is row.schema.columns[1]
    assert not hasattr(row, "values")
    assert not hasattr(row, "cardinality")
    assert not hasattr(row_set, "schema")


def test_schema_rejects_duplicate_identity_and_public_name() -> None:
    row, _ = make_row_contracts()
    value = row.schema.columns[0]
    with pytest.raises(DatasetConstructionError, match="duplicate field ids"):
        _make_schema((value, replace(value, _token=_CORE_TOKEN, name="other")))
    with pytest.raises(DatasetConstructionError, match="duplicate names"):
        _make_schema((value, replace(value, _token=_CORE_TOKEN, field_id=_make_field_id("other"))))


def test_row_contract_rejects_duplicate_missing_and_reordered_key_ids() -> None:
    row, _ = make_row_contracts("entity")
    key = row.coordinate_field_ids[0]
    value = row.schema.columns[1].field_id
    absent = _make_field_id("absent")
    for coordinates, keys in (
        ((key, key), (key,)),
        ((absent,), (absent,)),
        ((key,), (value,)),
        ((key, value), (value, key)),
    ):
        with pytest.raises(DatasetConstructionError):
            _make_row_contract(
                schema_version=1,
                shape_id=row.shape_id,
                schema=row.schema,
                coordinate_field_ids=coordinates,
                key_field_ids=keys,
                family_semantics=_complete_from_schema(),
            )


def test_singleton_and_keyed_cardinality_cannot_pair_with_wrong_row_identity() -> None:
    scalar, scalar_set = make_row_contracts()
    entity, entity_set = make_row_contracts("entity")
    _validate_row_contract_pair(scalar, scalar_set)
    _validate_row_contract_pair(entity, entity_set)
    with pytest.raises(DatasetConstructionError, match="empty coordinates"):
        _validate_row_contract_pair(entity, scalar_set)
    with pytest.raises(DatasetConstructionError, match="non-empty key"):
        _validate_row_contract_pair(scalar, entity_set)
    corrupt = replace(entity, _token=_CORE_TOKEN, key_field_ids=())
    with pytest.raises(DatasetConstructionError, match="non-empty key"):
        _validate_row_contract_pair(corrupt, entity_set)


def test_total_order_admits_values_followed_by_key_and_registered_unique_tiebreaker() -> None:
    row, row_set = make_row_contracts("entity")
    key, value = (column.field_id for column in row.schema.columns)
    value_order = _make_order_term(
        value,
        direction="descending",
        nulls="last",
        value_order_contract_id="test.numeric",
        ids=TEST_IDS,
    )
    key_order = _make_order_term(
        key,
        direction="ascending",
        nulls="first",
        value_order_contract_id="test.string",
        ids=TEST_IDS,
    )
    ordered = replace(
        row_set, _token=_CORE_TOKEN, ordering=_ordered_ordering((value_order, key_order))
    )
    _validate_row_contract_pair(row, ordered)
    value_only = replace(row_set, _token=_CORE_TOKEN, ordering=_ordered_ordering((value_order,)))
    with pytest.raises(DatasetConstructionError, match="partial order"):
        _validate_row_contract_pair(row, value_only)
    _validate_row_contract_pair(row, value_only, unique_tie_breakers=((value,),))
    with pytest.raises(DatasetConstructionError, match="non-empty proven unique"):
        _validate_row_contract_pair(row, value_only, unique_tie_breakers=((),))


def test_ordering_rejects_empty_duplicate_stale_and_unregistered_terms() -> None:
    row, row_set = make_row_contracts("entity")
    key = row.key_field_ids[0]
    term = _make_order_term(
        key,
        direction="ascending",
        nulls="first",
        value_order_contract_id="test.string",
        ids=TEST_IDS,
    )
    with pytest.raises(DatasetConstructionError, match="non-empty"):
        _ordered_ordering(())
    with pytest.raises(DatasetConstructionError, match="duplicate"):
        _ordered_ordering((term, term))
    with pytest.raises(DatasetConstructionError, match="registered"):
        _make_order_term(
            key,
            direction="ascending",
            nulls="first",
            value_order_contract_id="natural",
            ids=TEST_IDS,
        )
    stale = replace(term, _token=_CORE_TOKEN, field_id=_make_field_id("absent"))
    with pytest.raises(DatasetConstructionError, match="missing or stale"):
        _validate_row_contract_pair(
            row, replace(row_set, _token=_CORE_TOKEN, ordering=_ordered_ordering((stale,)))
        )


def test_row_fingerprint_and_row_set_fingerprint_have_distinct_ownership() -> None:
    row, row_set = make_row_contracts("entity")
    same_row, same_set = make_row_contracts("entity")
    assert _row_contract_fingerprint(row) == _row_contract_fingerprint(same_row)
    assert _row_set_contract_fingerprint(row_set) == _row_set_contract_fingerprint(same_set)
    limited = _make_row_set_contract(
        schema_version=1,
        cardinality=_keyed_cardinality(_static_row_bound(10)),
        ordering=_unordered_ordering(),
    )
    _validate_row_contract_pair(row, limited)
    assert _row_set_contract_fingerprint(limited) != _row_set_contract_fingerprint(row_set)
    assert _row_contract_fingerprint(row) == _row_contract_fingerprint(same_row)
    reordered = replace(
        row, _token=_CORE_TOKEN, schema=_make_schema(tuple(reversed(row.schema.columns)))
    )
    assert _row_contract_fingerprint(reordered) != _row_contract_fingerprint(row)
    assert _row_set_contract_fingerprint(row_set) == _row_set_contract_fingerprint(same_set)
    assert _row_contract_fingerprint(
        replace(row, _token=_CORE_TOKEN, schema_version=2)
    ) != _row_contract_fingerprint(row)
    assert _row_set_contract_fingerprint(
        replace(row_set, _token=_CORE_TOKEN, schema_version=2)
    ) != _row_set_contract_fingerprint(row_set)


def test_row_fingerprint_covers_coordinate_and_key_order() -> None:
    row, _ = make_row_contracts("entity")
    key, value = (column.field_id for column in row.schema.columns)
    with_both = replace(
        row, _token=_CORE_TOKEN, coordinate_field_ids=(key, value), key_field_ids=(key, value)
    )
    reordered = replace(
        with_both, _token=_CORE_TOKEN, coordinate_field_ids=(value, key), key_field_ids=(value, key)
    )
    assert _row_contract_fingerprint(row) != _row_contract_fingerprint(with_both)
    assert _row_contract_fingerprint(with_both) != _row_contract_fingerprint(reordered)


@pytest.mark.parametrize("nested", [False, True])
def test_family_field_references_do_not_collide_with_literal_tuple_facts(nested: bool) -> None:
    @dataclass(frozen=True, slots=True, repr=False, kw_only=True)
    class TestPartSemantics(DatasetFamilyRowSemantics, _token=_CORE_TOKEN):
        part: DatasetFieldId | tuple[str, str] | tuple[DatasetFieldId] | tuple[tuple[str, str]]
        kind: Literal["test.part"] = field(default="test.part", init=False)

    row, row_set = make_row_contracts()
    field_id = row.schema.columns[0].field_id
    literal = ("field_id", field_id.value)
    reference_row = replace(
        row,
        _token=_CORE_TOKEN,
        family_semantics=TestPartSemantics(
            _token=_CORE_TOKEN, part=(field_id,) if nested else field_id
        ),
    )
    literal_row = replace(
        row,
        _token=_CORE_TOKEN,
        family_semantics=TestPartSemantics(
            _token=_CORE_TOKEN, part=(literal,) if nested else literal
        ),
    )
    _validate_registered_contract(reference_row, row_set, ids=TEST_IDS)
    _validate_registered_contract(literal_row, row_set, ids=TEST_IDS)
    assert reference_row != literal_row
    assert _row_contract_fingerprint(reference_row) != _row_contract_fingerprint(literal_row)


def test_family_semantics_extension_is_typed_immutable_and_field_id_only() -> None:
    @dataclass(frozen=True, slots=True, repr=False, kw_only=True)
    class TestPositionSemantics(DatasetFamilyRowSemantics, _token=_CORE_TOKEN):
        position: DatasetFieldId
        kind: Literal["test_position"] = field(default="test_position", init=False)

    row, _ = make_row_contracts("entity")
    own = TestPositionSemantics(_token=_CORE_TOKEN, position=row.key_field_ids[0])
    variant = _make_row_contract(
        schema_version=1,
        shape_id=row.shape_id,
        schema=row.schema,
        coordinate_field_ids=row.coordinate_field_ids,
        key_field_ids=row.key_field_ids,
        family_semantics=own,
    )
    assert _row_contract_fingerprint(variant) != _row_contract_fingerprint(row)
    assert not hasattr(own, "__dict__")
    assert len(repr(own)) < 200


def test_family_semantics_cannot_repeat_common_facts_or_embed_mutable_payloads() -> None:
    @dataclass(frozen=True, slots=True, repr=False, kw_only=True)
    class DuplicateSemantics(DatasetFamilyRowSemantics, _token=_CORE_TOKEN):
        family_id: str
        kind: Literal["duplicate"] = field(default="duplicate", init=False)

    @dataclass(frozen=True, slots=True, repr=False, kw_only=True)
    class MutableSemantics(DatasetFamilyRowSemantics, _token=_CORE_TOKEN):
        payload: list[str]
        kind: Literal["mutable"] = field(default="mutable", init=False)

    row, _ = make_row_contracts()
    for semantics in (
        DuplicateSemantics(_token=_CORE_TOKEN, family_id="test"),
        MutableSemantics(_token=_CORE_TOKEN, payload=["bad"]),
    ):
        with pytest.raises(DatasetConstructionError):
            _make_row_contract(
                schema_version=1,
                shape_id=row.shape_id,
                schema=row.schema,
                coordinate_field_ids=(),
                key_field_ids=(),
                family_semantics=semantics,
            )


@pytest.mark.parametrize("schema_version", [0, -1, True])
def test_common_schema_versions_are_positive_non_boolean(schema_version: int) -> None:
    with pytest.raises(DatasetConstructionError):
        _make_row_set_contract(
            schema_version=schema_version,
            cardinality=_singleton_cardinality(),
            ordering=_unordered_ordering(),
        )
    row, _ = make_row_contracts()
    with pytest.raises(DatasetConstructionError):
        _make_row_contract(
            schema_version=schema_version,
            shape_id=row.shape_id,
            schema=row.schema,
            coordinate_field_ids=(),
            key_field_ids=(),
            family_semantics=_complete_from_schema(),
        )


def test_owning_vocabulary_rejects_descriptors_built_with_foreign_ids() -> None:
    row, row_set = make_row_contracts("entity")
    foreign_ids = replace(
        TEST_IDS,
        roles=TEST_IDS.roles | {"foreign.role"},
        logical_types=TEST_IDS.logical_types | {"foreign.logical"},
        physical_types=TEST_IDS.physical_types | {"foreign.physical"},
        admitted_types=TEST_IDS.admitted_types | {"foreign.admitted"},
        value_orders=TEST_IDS.value_orders | {"foreign.order"},
        policies=TEST_IDS.policies | {"foreign.policy"},
    )
    original = row.schema.columns[1]
    foreign_fields = (
        replace(original, _token=_CORE_TOKEN, role_id="foreign.role"),
        replace(original, _token=_CORE_TOKEN, logical_type_id="foreign.logical"),
        replace(
            original,
            _token=_CORE_TOKEN,
            physical_type_state=_resolved_type("foreign.physical", ids=foreign_ids),
        ),
        replace(
            original,
            _token=_CORE_TOKEN,
            physical_type_state=_deferred_type("foreign.admitted", ids=foreign_ids),
        ),
    )
    _validate_registered_contract(row, row_set, ids=TEST_IDS)
    for foreign in foreign_fields:
        foreign_row = replace(
            row, _token=_CORE_TOKEN, schema=_make_schema((row.schema.columns[0], foreign))
        )
        _validate_registered_contract(foreign_row, row_set, ids=foreign_ids)
        with pytest.raises(DatasetConstructionError, match="registered"):
            _validate_registered_contract(foreign_row, row_set, ids=TEST_IDS)
    foreign_term = _make_order_term(
        row.key_field_ids[0],
        direction="ascending",
        nulls="first",
        value_order_contract_id="foreign.order",
        ids=foreign_ids,
    )
    foreign_sets = (
        replace(row_set, _token=_CORE_TOKEN, ordering=_ordered_ordering((foreign_term,))),
        replace(
            row_set,
            _token=_CORE_TOKEN,
            cardinality=_keyed_cardinality(
                _runtime_policy_row_bound("foreign.policy", ids=foreign_ids)
            ),
        ),
    )
    for foreign_set in foreign_sets:
        _validate_registered_contract(row, foreign_set, ids=foreign_ids)
        with pytest.raises(DatasetConstructionError, match="registered"):
            _validate_registered_contract(row, foreign_set, ids=TEST_IDS)


def test_owner_admission_rejects_corrupted_variant_discriminators() -> None:
    row, row_set = make_row_contracts()
    original = row.schema.columns[0]
    physical = _resolved_type("float64", ids=TEST_IDS)
    object.__setattr__(physical, "kind", "deferred")
    corrupted = replace(
        row,
        _token=_CORE_TOKEN,
        schema=_make_schema((replace(original, _token=_CORE_TOKEN, physical_type_state=physical),)),
    )
    with pytest.raises(DatasetConstructionError, match="corrupt variant kind"):
        _validate_registered_contract(corrupted, row_set, ids=TEST_IDS)
