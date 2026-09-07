"""Exact retained field selection and Session-first consumer admission."""

from __future__ import annotations

import pickle
from dataclasses import replace

import pytest

from marivo.analysis.datasets import fields as field_module
from marivo.analysis.datasets.descriptors import (
    _CORE_TOKEN,
    DatasetField,
    DatasetRowContract,
    DatasetRowSetContract,
    _catalog_identity,
    _make_field_id,
    _make_schema,
    _resolved_type,
    _runtime_metric_identity,
)
from marivo.analysis.datasets.errors import DatasetFieldSelectionError, DatasetOwnershipError
from marivo.analysis.datasets.fields import DatasetFieldRef, DatasetFields, validate_field_ref
from marivo.refs import ref
from marivo.semantic.catalog import DimensionEntry, MetricEntry, TimeDimensionEntry
from marivo.semantic.runtime_metric import RuntimeRatioExpr
from tests.lazy_dataset_fixtures import (
    TEST_IDS,
    make_logical_dataset,
    make_owner,
    make_row_contracts,
)


def _contracts(*columns: DatasetField) -> tuple[DatasetRowContract, DatasetRowSetContract]:
    row, row_set = make_row_contracts()
    return replace(row, _token=_CORE_TOKEN, schema=_make_schema(columns)), row_set


def _field(
    name: str,
    *,
    semantic_key: str,
    role: str,
) -> DatasetField:
    base = make_row_contracts()[0].schema.columns[0]
    return replace(
        base,
        _token=_CORE_TOKEN,
        field_id=_make_field_id(name),
        name=name,
        role_id=role,
        identity=_catalog_identity(semantic_key),
    )


def test_get_selects_exact_id_or_name_without_parsing_semantic_keys() -> None:
    field = _field("revenue", semantic_key="metric:sales.revenue", role="metric")
    dataset = make_logical_dataset(contracts=_contracts(field))
    assert dataset.fields.get("revenue").field_id == field.field_id
    assert dataset.fields.get(field.field_id).field_id == field.field_id
    assert dataset.fields.metric(ref.metric("sales.revenue")).field_id == field.field_id
    for missing in ("sales.revenue", "metric:sales.revenue", "Revenue", " revenue"):
        with pytest.raises(DatasetFieldSelectionError) as caught:
            dataset.fields.get(missing)
        assert "revenue" in str(caught.value)
        assert "dataset.schema.columns" in str(caught.value)


def test_dimension_and_time_dimension_select_exact_retained_refs() -> None:
    region = _field("region", semantic_key="dimension:sales.order.region", role="dimension")
    occurred = _field(
        "occurred", semantic_key="time_dimension:sales.order.occurred", role="time_dimension"
    )
    dataset = make_logical_dataset(contracts=_contracts(region, occurred))
    assert dataset.fields.dimension(ref.dimension("sales.order.region")).field_id == region.field_id
    assert (
        dataset.fields.dimension(ref.time_dimension("sales.order.occurred")).field_id
        == occurred.field_id
    )


@pytest.mark.parametrize("entry_type", [MetricEntry, DimensionEntry, TimeDimensionEntry])
def test_catalog_entries_require_owner_identity_before_ref_access(entry_type: type) -> None:
    catalog_identity = object()
    owner = make_owner(catalog_identity=catalog_identity)
    if entry_type is MetricEntry:
        semantic_ref = ref.metric("sales.revenue")
        role = "metric"
    elif entry_type is DimensionEntry:
        semantic_ref = ref.dimension("sales.order.region")
        role = "dimension"
    else:
        semantic_ref = ref.time_dimension("sales.order.occurred")
        role = "time_dimension"
    column = _field("selected", semantic_key=semantic_ref.key, role=role)
    dataset = make_logical_dataset(owner=owner, contracts=_contracts(column))
    # These trusted entry descriptors have no catalog implementation or details;
    # lookup must only check owner identity and then the retained ref.
    entry = object.__new__(entry_type)
    object.__setattr__(entry, "_catalog", catalog_identity)
    object.__setattr__(entry, "ref", semantic_ref)
    method = dataset.fields.metric if entry_type is MetricEntry else dataset.fields.dimension
    assert method(entry).field_id == column.field_id
    foreign = object.__new__(entry_type)
    object.__setattr__(foreign, "_catalog", object())
    with pytest.raises(DatasetOwnershipError, match="different catalog"):
        method(foreign)


def test_runtime_metric_requires_same_expression_object_and_retained_id_reacquires() -> None:
    expression = RuntimeRatioExpr(
        kind="ratio",
        numerator=ref.metric("sales.revenue"),
        denominator=ref.metric("sales.orders"),
        zero_division="null",
        label="revenue_per_order",
    )
    equivalent = replace(expression)
    base = make_row_contracts()[0].schema.columns[0]
    field = replace(base, _token=_CORE_TOKEN, identity=_runtime_metric_identity("runtime_ratio_v1"))
    owner = make_owner(runtime_metric_bindings=((expression, field.field_id.value),))
    dataset = make_logical_dataset(owner=owner, contracts=_contracts(field))
    assert dataset.fields.metric(expression).field_id == field.field_id
    with pytest.raises(DatasetFieldSelectionError, match="0 matches"):
        dataset.fields.metric(equivalent)
    recovered = make_logical_dataset(contracts=_contracts(field))
    assert recovered.fields.get(field.field_id).field_id == field.field_id
    with pytest.raises(DatasetFieldSelectionError, match="0 matches"):
        recovered.fields.metric(expression)
    inconsistent_retention = make_logical_dataset(owner=owner)
    with pytest.raises(DatasetFieldSelectionError, match="0 matches"):
        inconsistent_retention.fields.metric(expression)


def test_semantic_lookup_rejects_ambiguous_and_wrong_role_bindings() -> None:
    field = _field("first", semantic_key="metric:sales.revenue", role="metric")
    duplicate_identity = replace(
        field, _token=_CORE_TOKEN, name="second", field_id=_make_field_id("second")
    )
    ambiguous = make_logical_dataset(contracts=_contracts(field, duplicate_identity))
    with pytest.raises(DatasetFieldSelectionError, match="2 matches"):
        ambiguous.fields.metric(ref.metric("sales.revenue"))
    wrong_role = replace(field, _token=_CORE_TOKEN, role_id="dimension")
    dataset = make_logical_dataset(contracts=_contracts(wrong_role))
    with pytest.raises(DatasetFieldSelectionError, match="field role"):
        dataset.fields.metric(ref.metric("sales.revenue"))
    assert dataset.fields.get("first").field_id == wrong_role.field_id


def test_consumer_checks_corresponding_input_session_before_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = make_logical_dataset(owner=make_owner(session_id="source_session"))
    other = make_logical_dataset(owner=make_owner(session_id="other_session"))
    selector = source.fields.get("value")

    def unexpected_binding_read(field: DatasetField) -> str:
        raise AssertionError("binding admission ran before Session ownership")

    monkeypatch.setattr(field_module, "_field_binding_fingerprint", unexpected_binding_read)
    with pytest.raises(DatasetOwnershipError, match="source_session"):
        validate_field_ref(other, selector, allowed_roles=("dimension",))


def test_selector_survives_only_exact_current_binding_in_same_session() -> None:
    source = make_logical_dataset()
    selector = source.fields.get("value")
    equivalent = make_logical_dataset(
        owner=source._owner, contracts=(source.row_contract, source.row_set_contract)
    )
    assert validate_field_ref(equivalent, selector) is source.schema.columns[0]
    with pytest.raises(DatasetFieldSelectionError, match="field role"):
        validate_field_ref(equivalent, selector, allowed_roles=("dimension",))


@pytest.mark.parametrize(
    "attribute",
    [
        "field_id",
        "name",
        "role_id",
        "identity",
        "derivation_identity",
        "logical_type_id",
        "physical_type_state",
        "nullable",
    ],
)
def test_binding_guard_covers_every_field_fact(attribute: str) -> None:
    source = make_logical_dataset()
    field = source.schema.columns[0]
    selector = source.fields.get(field.field_id)
    replacements: dict[str, object] = {
        "field_id": _make_field_id("new_value"),
        "name": "renamed_value",
        "role_id": "dimension",
        "identity": _catalog_identity("metric:sales.revenue"),
        "derivation_identity": "new_derivation",
        "logical_type_id": "string",
        "physical_type_state": _resolved_type("int64", ids=TEST_IDS),
        "nullable": not field.nullable,
    }
    changed = replace(field, _token=_CORE_TOKEN, **{attribute: replacements[attribute]})
    target = make_logical_dataset(owner=source._owner, contracts=_contracts(changed))
    with pytest.raises(DatasetFieldSelectionError):
        validate_field_ref(target, selector)


def test_fields_and_selectors_are_closed_immutable_and_not_column_protocols() -> None:
    dataset = make_logical_dataset()
    fields = dataset.fields
    selector = fields.get("value")
    assert DatasetFieldRef.__slots__ == (
        "field_binding_fingerprint",
        "field_id",
        "owning_session_id",
    )
    assert "0x" not in repr(fields) + repr(selector)
    assert "\n" not in repr(fields) + repr(selector)
    assert len(repr(fields)) <= 200
    assert len(repr(selector)) <= 200
    for value in (fields, selector):
        for member in (
            "items",
            "refs",
            "render",
            "show",
            "to_pandas",
            "__iter__",
            "__len__",
            "__getitem__",
        ):
            assert not hasattr(value, member)
        with pytest.raises(AttributeError, match="immutable"):
            value.field_id = _make_field_id("other")
        with pytest.raises(TypeError, match="cannot be pickled"):
            pickle.dumps(value)
        with pytest.raises(TypeError):
            iter(value)
    for constructor in (DatasetFields, DatasetFieldRef):
        with pytest.raises(TypeError, match="no public constructor"):
            constructor()
    with pytest.raises(TypeError, match="selector-only"):
        assert selector == fields.get("value")
    with pytest.raises(TypeError, match="truth value"):
        bool(selector)
    with pytest.raises(TypeError):
        hash(selector)
