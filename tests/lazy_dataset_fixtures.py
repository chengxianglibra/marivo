"""Pure test-family builders for private Dataset Core acceptance."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from marivo._compat import Never
from marivo.analysis.datasets.actions import construct_operator
from marivo.analysis.datasets.base import (
    Dataset,
    DatasetOwner,
    LogicalDataset,
    MaterializedDataset,
    _dataset_repr,
    _make_logical_dataset,
    _make_materialized_dataset,
)
from marivo.analysis.datasets.descriptors import (
    _CORE_TOKEN,
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
    _validate_row_contract_pair,
)
from marivo.analysis.datasets.handles import (
    CanonicalValue,
    RealizationHandle,
    RealizationRequirement,
    _LogicalNodePayload,
)
from marivo.analysis.datasets.registry import (
    ConsumerRegistration,
    DatasetFamilyRegistration,
    DatasetFamilyRegistry,
)
from marivo.analysis.datasets.state import MaterializedDatasetState, _materialized_state
from marivo.analysis.refs import ArtifactRef

if TYPE_CHECKING:
    import pandas as pd

    from marivo.semantic.runtime_metric import RuntimeMetricExpr


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

TestOwner = DatasetOwner


class LogicalTestDataset(LogicalDataset, _token=_CORE_TOKEN, family_id="test"):
    """Test-only family with no execution implementation."""

    __slots__ = ()

    def step(self) -> LogicalTestDataset:
        return make_logical_dataset(
            owner=self._owner,
            registry=self._registry,
            operation_id="test.step",
            inputs=(self,),
            contracts=(self.row_contract, self.row_set_contract),
        )

    def combine(self, other: Dataset) -> LogicalTestDataset:
        return make_logical_dataset(
            owner=self._owner,
            registry=self._registry,
            operation_id="test.combine",
            inputs=(self, other),
            contracts=(self.row_contract, self.row_set_contract),
        )

    def execute(self) -> MaterializedTestDataset:
        raise AssertionError("The private test family must never execute.")


class MaterializedTestDataset(MaterializedDataset, _token=_CORE_TOKEN, family_id="test"):
    """Trusted test value; no Artifact publication or row backing is claimed."""

    __slots__ = ()

    def step(self) -> LogicalTestDataset:
        return make_logical_dataset(
            owner=self._owner,
            registry=self._registry,
            operation_id="test.step",
            inputs=(self,),
            contracts=(self.row_contract, self.row_set_contract),
        )

    def combine(self, other: Dataset) -> LogicalTestDataset:
        return make_logical_dataset(
            owner=self._owner,
            registry=self._registry,
            operation_id="test.combine",
            inputs=(self, other),
            contracts=(self.row_contract, self.row_set_contract),
        )

    def show(self, *, max_output_bytes: int | None = None) -> None:
        raise AssertionError("The private test family has no row reader.")

    def to_pandas(self) -> pd.DataFrame:
        raise AssertionError("The private test family has no row collector.")

    @property
    def evidence_digest(self) -> Never:
        raise AssertionError("The private test family has no Evidence.")

    def findings(self, *args: object, **kwargs: object) -> Never:
        raise AssertionError("The private test family has no Findings.")

    def finding(self, *args: object, **kwargs: object) -> Never:
        raise AssertionError("The private test family has no Finding.")


def make_owner(
    *,
    session_id: str = "session-test",
    store_id: str = "store-test",
    catalog_identity: object | None = None,
    runtime_metric_bindings: tuple[tuple[RuntimeMetricExpr, str], ...] = (),
) -> DatasetOwner:
    return DatasetOwner(
        session_id=session_id,
        store_id=store_id,
        catalog_identity=catalog_identity,
        runtime_metric_bindings=runtime_metric_bindings,
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


def _test_row_validator(row: DatasetRowContract, row_set: DatasetRowSetContract) -> None:
    _validate_row_contract_pair(row, row_set)


def _test_state_decoder(state: MaterializedDatasetState) -> MaterializedDatasetState:
    return state


def make_test_registration(
    *,
    consumers: tuple[ConsumerRegistration, ...] | None = None,
) -> DatasetFamilyRegistration:
    shapes = tuple(_make_shape_id("test", shape, 1, ids=TEST_IDS) for shape in ("scalar", "entity"))
    if consumers is None:
        consumers = (
            ConsumerRegistration(
                id="test.step",
                input_roles=("input",),
                output_family="test",
                accepted_shape_ids=shapes,
            ),
            ConsumerRegistration(
                id="test.combine",
                input_roles=("left", "right"),
                output_family="test",
                accepted_shape_ids=shapes,
            ),
        )
    return DatasetFamilyRegistration(
        family_id="test",
        ids=TEST_IDS,
        logical_type=LogicalTestDataset,
        materialized_type=MaterializedTestDataset,
        shape_ids=shapes,
        owner_id="tests.lazy_dataset_fixtures",
        row_validator=_test_row_validator,
        consumers=consumers,
        repr_renderer=_dataset_repr,
        materialized_state_decoder=_test_state_decoder,
    )


def make_test_registry(
    *,
    consumers: tuple[ConsumerRegistration, ...] | None = None,
    frozen: bool = True,
) -> DatasetFamilyRegistry:
    registry = DatasetFamilyRegistry()
    registry.register(make_test_registration(consumers=consumers))
    if frozen:
        registry.freeze()
    return registry


def make_logical_dataset(
    *,
    owner: DatasetOwner | None = None,
    registry: DatasetFamilyRegistry | None = None,
    contracts: tuple[DatasetRowContract, DatasetRowSetContract] | None = None,
    operation_id: str = "test.source",
    inputs: tuple[Dataset, ...] = (),
    significant: bool = False,
    realization_handle: RealizationHandle | None = None,
    parameters: CanonicalValue = (),
    requirements: tuple[str, ...] = (),
    dependency_facts: tuple[str, ...] = (),
    contract_versions: tuple[tuple[str, str], ...] = (),
    payload: _LogicalNodePayload | None = None,
) -> LogicalTestDataset:
    row, row_set = contracts if contracts is not None else make_row_contracts()
    owner = make_owner() if owner is None else owner
    registry = make_test_registry() if registry is None else registry
    realizations = (
        (RealizationRequirement("test.sample", realization_handle or RealizationHandle()),)
        if significant
        else ()
    )
    if inputs:
        result = construct_operator(
            owner=owner,
            registry=registry,
            row_contract=row,
            row_set_contract=row_set,
            operator_id=operation_id,
            inputs=inputs,
            parameters=parameters,
            realizations=realizations,
            dependency_facts=dependency_facts,
            contract_versions=contract_versions,
            payload=payload,
        )
    else:
        result = _make_logical_dataset(
            owner=owner,
            registry=registry,
            family_id="test",
            row_contract=row,
            row_set_contract=row_set,
            operator_id=operation_id,
            inputs=inputs,
            parameters=parameters,
            realizations=realizations,
            requirements=requirements,
            dependency_facts=dependency_facts,
            contract_versions=contract_versions,
            payload=payload,
        )
    assert isinstance(result, LogicalTestDataset)
    return result


def make_materialized_dataset(
    *,
    owner: DatasetOwner | None = None,
    registry: DatasetFamilyRegistry | None = None,
    contracts: tuple[DatasetRowContract, DatasetRowSetContract] | None = None,
    artifact_ref: str = "art_test",
    origin: LogicalDataset | None = None,
    state: MaterializedDatasetState | None = None,
) -> MaterializedTestDataset:
    row, row_set = contracts if contracts is not None else make_row_contracts()
    owner = make_owner() if owner is None else owner
    registry = make_test_registry() if registry is None else registry
    if state is None:
        state = _materialized_state(
            artifact_ref=ArtifactRef(ref=artifact_ref),
            artifact_session_ref=owner.session_id,
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
    result = _make_materialized_dataset(
        owner=owner,
        registry=registry,
        family_id="test",
        row_contract=row,
        row_set_contract=row_set,
        state=state,
        definition_fingerprint=origin.definition_fingerprint
        if origin is not None
        else "ds_" + "0" * 64,
    )
    assert isinstance(result, MaterializedTestDataset)
    return result
