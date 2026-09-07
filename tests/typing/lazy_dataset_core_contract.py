"""Static acceptance of private paired states and selector-only field values.

Expected-error ignores are deliberate negative assertions: strict mypy reports
an unused ignore if a forbidden action or implicit type conversion becomes valid.
"""

from __future__ import annotations

from typing import Literal

from typing_extensions import assert_type

from marivo.analysis.datasets.base import Dataset, LogicalDataset, MaterializedDataset
from marivo.analysis.datasets.contract import DatasetContract
from marivo.analysis.datasets.descriptors import (
    DatasetByteCount,
    DatasetCardinality,
    DatasetFamilyRowSemantics,
    DatasetFieldId,
    DatasetFieldIdentity,
    DatasetOrdering,
    DatasetPhysicalTypeState,
    DatasetRowBound,
    DatasetRowContract,
    DatasetRowSetContract,
    DatasetSchema,
)
from marivo.analysis.datasets.fields import DatasetFieldRef, DatasetFields
from marivo.analysis.datasets.state import LogicalDatasetState, MaterializedDatasetState
from marivo.refs import ref as ref_factory
from tests.lazy_dataset_fixtures import LogicalTestDataset, MaterializedTestDataset


def _closed_descriptor_discriminators(
    identity: DatasetFieldIdentity,
    physical_type: DatasetPhysicalTypeState,
    row_bound: DatasetRowBound,
    cardinality: DatasetCardinality,
    ordering: DatasetOrdering,
    byte_count: DatasetByteCount,
    family_semantics: DatasetFamilyRowSemantics,
) -> None:
    assert_type(identity.kind, Literal["catalog_ref", "runtime_metric", "generated"])
    assert_type(physical_type.kind, Literal["resolved", "deferred"])
    assert_type(row_bound.kind, Literal["unknown", "static", "runtime_policy"])
    assert_type(cardinality.kind, Literal["singleton", "keyed"])
    assert_type(ordering.kind, Literal["unordered", "ordered"])
    assert_type(byte_count.kind, Literal["exact", "unavailable"])
    assert_type(family_semantics.kind, str)


def _common_values(dataset: Dataset) -> None:
    assert_type(dataset.row_contract, DatasetRowContract)
    assert_type(dataset.row_set_contract, DatasetRowSetContract)
    assert_type(dataset.schema, DatasetSchema)
    assert_type(dataset.fields, DatasetFields)
    assert_type(dataset.contract(), DatasetContract)
    assert_type(dataset.contract().render(), str)
    assert_type(dataset.contract().show(), None)
    assert_type(dataset.fields.get("value"), DatasetFieldRef)
    assert_type(dataset.fields.metric(ref_factory.metric("sales.revenue")), DatasetFieldRef)
    assert_type(
        dataset.fields.dimension(ref_factory.dimension("sales.orders.region")), DatasetFieldRef
    )
    assert_type(
        dataset.fields.dimension(ref_factory.time_dimension("sales.orders.time")), DatasetFieldRef
    )
    dataset.render()  # type: ignore[attr-defined]
    dataset["value"]  # type: ignore[index]
    len(dataset)  # type: ignore[arg-type]


def _paired_states(logical: LogicalDataset, materialized: MaterializedDataset) -> None:
    assert_type(logical.state, LogicalDatasetState)
    assert_type(materialized.state, MaterializedDatasetState)
    assert_type(logical.execute(), MaterializedDataset)
    logical.show()  # type: ignore[attr-defined]
    logical.to_pandas()  # type: ignore[attr-defined]
    logical.findings()  # type: ignore[attr-defined]
    materialized.execute()  # type: ignore[attr-defined]


def _exact_family(logical: LogicalTestDataset, materialized: MaterializedTestDataset) -> None:
    assert_type(logical.execute(), MaterializedTestDataset)
    assert_type(logical.step(), LogicalTestDataset)
    assert_type(materialized.step(), LogicalTestDataset)
    assert_type(logical.combine(materialized), LogicalTestDataset)
    assert_type(materialized.combine(logical), LogicalTestDataset)


def _requires_test_family(dataset: LogicalTestDataset) -> None:
    pass


def _nominal_admission(generic: Dataset) -> None:
    _requires_test_family(generic)  # type: ignore[arg-type]


def _selector_is_not_an_expression(selector: DatasetFieldRef) -> None:
    assert_type(selector.field_id, DatasetFieldId)
    assert_type(selector.owning_session_id, str)
    assert_type(selector.field_binding_fingerprint, str)
    selector + 1  # type: ignore[operator]
    _ = selector > 1  # type: ignore[operator]
    selector[0]  # type: ignore[index]
    selector.show()  # type: ignore[attr-defined]
    selector.to_pandas()  # type: ignore[attr-defined]
    DatasetFieldRef()  # type: ignore[call-arg]
