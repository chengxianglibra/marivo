"""Pure test-family builders for private Dataset Core acceptance."""

from __future__ import annotations

from typing import Literal

from marivo.analysis.datasets.descriptors import (
    DatasetField,
    DatasetFieldId,
    DatasetRowContract,
    DatasetRowSetContract,
    _complete_from_schema,
    _exact_byte_count,
    _generated_identity,
    _keyed_cardinality,
    _make_field,
    _make_field_id,
    _make_row_contract,
    _make_row_set_contract,
    _make_schema,
    _make_shape_id,
    _resolved_type,
    _singleton_cardinality,
    _StableIdRegistry,
    _unknown_row_bound,
    _unordered_ordering,
)
from marivo.analysis.datasets.state import MaterializedDatasetState, _materialized_state
from marivo.analysis.refs import ArtifactRef

TEST_IDS = _StableIdRegistry(
    families=frozenset({"test"}),
    shapes=frozenset({("test", "scalar", 1), ("test", "entity", 1)}),
    roles=frozenset(
        {"metric", "dimension", "time_dimension", "entity_key", "generated", "value", "coordinate"}
    ),
    logical_types=frozenset({"numeric", "string"}),
    physical_types=frozenset({"float64", "int64", "string"}),
    physical_type_classes=frozenset(
        {("float64", "numeric"), ("int64", "numeric"), ("string", "string")}
    ),
    admitted_types=frozenset({"numeric", "string"}),
    policies=frozenset({"test.collection"}),
    value_orders=frozenset({"test.numeric", "test.string"}),
    byte_unavailable_reasons=frozenset({"test.unavailable"}),
    storage_kinds=frozenset({"test_parquet"}),
)


def make_row_contracts(
    shape: Literal["scalar", "entity"] = "scalar",
) -> tuple[DatasetRowContract, DatasetRowSetContract]:
    value_id = _make_field_id("value")
    value = _make_field(
        field_id=value_id,
        name="value",
        role_id="metric",
        identity=_generated_identity(value_id),
        derivation_identity="test.value/v1",
        logical_type_id="numeric",
        physical_type_state=_resolved_type("float64", ids=TEST_IDS),
        nullable=False,
        ids=TEST_IDS,
    )
    columns: tuple[DatasetField, ...]
    coordinates: tuple[DatasetFieldId, ...]
    if shape == "entity":
        entity_id = _make_field_id("entity_id")
        entity = _make_field(
            field_id=entity_id,
            name="entity_id",
            role_id="entity_key",
            identity=_generated_identity(entity_id),
            derivation_identity="test.entity/v1",
            logical_type_id="string",
            physical_type_state=_resolved_type("string", ids=TEST_IDS),
            nullable=False,
            ids=TEST_IDS,
        )
        columns = (entity, value)
        coordinates = (entity_id,)
        cardinality = _keyed_cardinality(_unknown_row_bound())
    else:
        columns = (value,)
        coordinates = ()
        cardinality = _singleton_cardinality()
    row = _make_row_contract(
        schema_version=1,
        shape_id=_make_shape_id("test", shape, 1, ids=TEST_IDS),
        schema=_make_schema(columns),
        coordinate_field_ids=coordinates,
        key_field_ids=coordinates,
        family_semantics=_complete_from_schema(),
    )
    row_set = _make_row_set_contract(
        schema_version=1, cardinality=cardinality, ordering=_unordered_ordering()
    )
    return row, row_set


def make_materialized_state() -> MaterializedDatasetState:
    row, _ = make_row_contracts()
    return _materialized_state(
        artifact_ref=ArtifactRef(ref="art_test"),
        artifact_session_ref="test_session",
        content_authority_digest="content-test-v1",
        storage_kind_id="test_parquet",
        realized_schema=row.schema,
        realized_row_count=1,
        realized_byte_count=_exact_byte_count(64),
        producing_run_ref="run_test",
        quality_authority_digest="quality-test-v1",
        evidence_authority_digest="evidence-test-v1",
        ids=TEST_IDS,
    )
