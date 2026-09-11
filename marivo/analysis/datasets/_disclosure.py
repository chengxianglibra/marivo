"""Dataset Core owns its native type, selector and action disclosure inputs."""

from __future__ import annotations

from typing import Literal

from marivo.analysis._capabilities.dataset_model import (
    CONSTRUCTION_FAILURES,
    CallableInput,
    Descriptor,
    DisclosureProvider,
    ExampleInput,
    ExportInput,
    bind,
    operation,
    value_type,
    with_sealed_variants,
)
from marivo.analysis._capabilities.dataset_model import (
    ParameterInput as P,
)
from marivo.analysis.datasets import descriptors as d
from marivo.analysis.datasets.base import Dataset, LogicalDataset, MaterializedDataset
from marivo.analysis.datasets.contract import DatasetContract
from marivo.analysis.datasets.fields import DatasetFieldRef, DatasetFields
from marivo.analysis.datasets.registry import DatasetFamilyRegistry
from marivo.analysis.datasets.state import LogicalDatasetState, MaterializedDatasetState

CORE_TYPES: tuple[tuple[type[object], str, str], ...] = (
    (Dataset, "dataset", "Common sealed Dataset identity and row contracts."),
    (LogicalDataset, "logical", "Immutable definition; execute is the explicit action."),
    (
        MaterializedDataset,
        "materialized",
        "Committed Artifact snapshot with bounded retained reads.",
    ),
    (d.DatasetShapeId, "shape_id", "Qualified family, local shape and semantic version."),
    (d.DatasetFieldId, "field_id", "Stable field identity within a registered schema."),
    (
        d.DatasetFieldIdentity,
        "field_identity",
        "Closed Entity, catalog, runtime Metric or generated identity.",
    ),
    (
        d.DatasetPhysicalTypeState,
        "physical_type_state",
        "Resolved physical type or deferred admitted type class.",
    ),
    (d.DatasetField, "field", "One schema binding with role, identity, type and nullability."),
    (d.DatasetRowBound, "row_bound", "Unknown, static maximum or Runtime policy row bound."),
    (d.DatasetCardinality, "cardinality", "Singleton or keyed row-set cardinality."),
    (
        d.DatasetOrderTerm,
        "order_term",
        "Exact field, direction, null placement and value ordering.",
    ),
    (d.DatasetOrdering, "ordering", "Unordered rows or an exact total sequence of order terms."),
    (d.DatasetByteCount, "byte_count", "Exact byte count or typed unavailable reason."),
    (
        d.DatasetFamilyRowSemantics,
        "family_row_semantics",
        "Closed family-owned row meanings; inspect the exact family page.",
    ),
    (
        d.DatasetRowContract,
        "row_contract",
        "Shape, schema, coordinates, keys and exact family semantics.",
    ),
    (d.DatasetRowSetContract, "row_set_contract", "Cardinality and ordering of the current rows."),
    (d.DatasetSchema, "schema", "Ordered immutable field bindings."),
    (
        LogicalDatasetState,
        "logical_state",
        "Logical definition state without a published Artifact.",
    ),
    (
        MaterializedDatasetState,
        "materialized_state",
        "Committed Artifact state, counts and execution identity.",
    ),
    (
        DatasetContract,
        "contract",
        "Bounded current requirements and mechanically admitted continuations.",
    ),
    (DatasetFields, "fields", "Selector namespace; never an expression or row reader."),
    (DatasetFieldRef, "field_ref", "Sealed selector tied to the exact Session, schema and field."),
)


def provider(registry: DatasetFamilyRegistry) -> DisclosureProvider:
    descriptors: list[Descriptor] = []
    exports: list[ExportInput] = []
    acquisitions = {
        "dataset": (
            "Use a registered source or Dataset operator; this abstract base is not constructible.",
            ("observe", "population.create"),
            ("datasets.dataset.contract",),
        ),
        "logical": (
            "Use a source or Dataset operator; execute() returns the paired Materialized state.",
            ("observe", "population.create"),
            ("actions.execute",),
        ),
        "materialized": (
            "Use logical.execute() or session.artifact(ref).",
            ("actions.execute", "session.artifact"),
            ("actions.show", "actions.to_pandas"),
        ),
        "shape_id": (
            "Read dataset.row_contract.shape_id.",
            ("datasets.row_contract",),
            ("datasets.dataset.contract",),
        ),
        "field_id": (
            "Read dataset.schema.columns[i].field_id.",
            ("datasets.field",),
            ("datasets.fields.get",),
        ),
        "field_identity": (
            "Read dataset.schema.columns[i].identity; never author raw Entity identities.",
            ("datasets.field",),
            ("datasets.dataset.contract",),
        ),
        "physical_type_state": (
            "Read dataset.schema.columns[i].physical_type_state.",
            ("datasets.field",),
            ("datasets.dataset.contract",),
        ),
        "field": (
            "Read a binding from dataset.schema.columns.",
            ("datasets.schema",),
            ("datasets.fields",),
        ),
        "row_bound": (
            "For keyed cardinality, read dataset.row_set_contract.cardinality.row_bound.",
            ("datasets.cardinality",),
            ("datasets.dataset.contract",),
        ),
        "cardinality": (
            "Read dataset.row_set_contract.cardinality and dispatch on its kind.",
            ("datasets.row_set_contract",),
            ("datasets.dataset.contract",),
        ),
        "order_term": (
            "For ordered rows, read dataset.row_set_contract.ordering.terms.",
            ("datasets.ordering",),
            ("datasets.limit",),
        ),
        "ordering": (
            "Read dataset.row_set_contract.ordering and dispatch on its kind.",
            ("datasets.row_set_contract",),
            ("datasets.limit",),
        ),
        "byte_count": (
            "Read materialized.state.realized_byte_count; unavailable is distinct from zero.",
            ("datasets.materialized_state",),
            ("actions.show",),
        ),
        "family_row_semantics": (
            "Read dataset.row_contract.family_semantics; its complete variant belongs to the exact family page.",
            ("datasets.row_contract",),
            ("datasets.dataset.contract",),
        ),
        "row_contract": (
            "Read dataset.row_contract.",
            ("datasets.dataset",),
            ("datasets.dataset.contract",),
        ),
        "row_set_contract": (
            "Read dataset.row_set_contract.",
            ("datasets.dataset",),
            ("datasets.dataset.contract",),
        ),
        "schema": (
            "Read dataset.schema; column order is part of the contract.",
            ("datasets.dataset",),
            ("datasets.fields",),
        ),
        "logical_state": (
            "Read logical.state; this carries no committed Artifact authority.",
            ("datasets.logical",),
            ("actions.execute",),
        ),
        "materialized_state": (
            "Read materialized.state after execute() or Artifact recovery.",
            ("datasets.materialized",),
            ("session.artifact",),
        ),
        "contract": (
            "Call dataset.contract().",
            ("datasets.dataset.contract",),
            ("datasets.contract.render", "datasets.contract.show"),
        ),
        "fields": (
            "Read dataset.fields to select by semantic ref, stable field id or generated name.",
            ("datasets.dataset",),
            ("datasets.fields.metric", "datasets.fields.dimension", "datasets.fields.get"),
        ),
        "field_ref": (
            "Resolve a selector through this Dataset's fields namespace.",
            ("datasets.fields.metric", "datasets.fields.dimension", "datasets.fields.get"),
            ("datasets.rank", "eq"),
        ),
    }
    for value, leaf, summary in CORE_TYPES:
        target = "datasets." + leaf
        descriptors.append(
            value_type(
                target,
                value,
                summary=summary,
                acquisition=acquisitions[leaf][0],
                producers=acquisitions[leaf][1],
                consumers=acquisitions[leaf][2],
            )
        )
        exports.append(ExportInput(value.__name__, value, target))

    def common(
        target: str,
        method: str,
        state: Literal["both", "logical", "materialized"],
        *,
        summary: str,
        parameters: tuple[P, ...],
        output: str,
        code: str,
        requires: tuple[str, ...],
        effects: str,
        runtime: bool = False,
    ) -> CallableInput:
        bindings = tuple(
            bind(getattr(t, method), t)
            for f in registry.registrations
            for t in (
                (f.logical_type, f.materialized_type)
                if state == "both"
                else (f.logical_type,)
                if state == "logical"
                else (f.materialized_type,)
            )
        )
        return operation(
            target,
            "dataset." + method,
            bindings[0].implementation,
            bindings=bindings,
            summary=summary,
            parameters=parameters,
            output=output,
            constraints=("The receiver must have the declared state and belong to this Session.",),
            effects=effects,
            failures=(
                *CONSTRUCTION_FAILURES,
                "MaterializationError: inspect the structured phase and repair; an incomplete execution is not a committed result.",
            ),
            example=ExampleInput(code, requires, "result", output, runtime),
        )

    descriptors.extend(
        (
            common(
                "actions.execute",
                "execute",
                "logical",
                summary="Execute or recover the exact same-Session committed snapshot.",
                parameters=(),
                output="Paired Materialized Dataset",
                code="result = metric.execute()",
                requires=("metric",),
                effects="May query sources and publish one atomic Artifact; an execution-key hit recovers the existing snapshot, not fresh source rows.",
                runtime=True,
            ),
            common(
                "actions.show",
                "show",
                "materialized",
                summary="Print bounded committed rows.",
                parameters=(
                    P(
                        "max_output_bytes",
                        "Optional tighter byte budget; None uses the Runtime default.",
                    ),
                ),
                output="None",
                code="result = materialized.show()",
                requires=("materialized",),
                effects="Bounded retained read; no origin replay or new Run.",
                runtime=True,
            ),
            common(
                "actions.to_pandas",
                "to_pandas",
                "materialized",
                summary="Collect an isolated complete DataFrame at the terminal boundary.",
                parameters=(),
                output="pandas.DataFrame; cannot re-enter typed analysis",
                code="result = materialized.to_pandas()",
                requires=("materialized",),
                effects="Explicit terminal collection under Runtime row limits; no origin replay.",
                runtime=True,
            ),
            common(
                "datasets.dataset.contract",
                "contract",
                "both",
                summary="Inspect exact current requirements and legal continuations.",
                parameters=(),
                output="DatasetContract",
                code="result = metric.contract()",
                requires=("metric",),
                effects="Pure metadata and consumer-admission read; no query or Run.",
            ),
        )
    )
    # The common contract implementation is inherited unchanged by both states.
    for name, parameter, code in (
        (
            "metric",
            P("metric", "Select one Metric ref from the current schema or semantic catalog."),
            "result = metric.fields.metric(revenue)",
        ),
        (
            "dimension",
            P("dimension", "Select a Dimension or TimeDimension present in the current schema."),
            "result = dimensioned.fields.dimension(region)",
        ),
        (
            "get",
            P(
                "key",
                "Read a stable DatasetFieldId or exact generated field name from dataset.schema.",
            ),
            "result = metric.fields.get(metric.schema.columns[0].field_id)",
        ),
    ):
        descriptors.append(
            operation(
                "datasets.fields." + name,
                "dataset.fields." + name,
                getattr(DatasetFields, name),
                summary="Resolve one exact schema-bound selector.",
                parameters=(parameter,),
                output="DatasetFieldRef",
                constraints=(
                    "Not an expression; selectors cannot cross Session or schema bindings.",
                ),
                effects="Pure schema read.",
                failures=(
                    "DatasetFieldSelectionError: choose a field from the receiving Dataset's schema.",
                ),
                example=ExampleInput(
                    code,
                    ("metric", "revenue", "dimensioned", "region"),
                    "result",
                    "DatasetFieldRef",
                ),
            )
        )
    for name, output in (("render", "str"), ("show", "None")):
        descriptors.append(
            operation(
                "datasets.contract." + name,
                "dataset.contract()." + name,
                getattr(DatasetContract, name),
                summary="Render the bounded current Dataset contract.",
                parameters=(
                    P("max_output_bytes", "Optional tighter byte budget; None uses the default."),
                ),
                output=output,
                constraints=(
                    "Continuations come from the current consumer admission, not static Help.",
                ),
                effects="Pure metadata rendering.",
                failures=CONSTRUCTION_FAILURES,
                example=ExampleInput(
                    f"result = metric.contract().{name}()", ("metric",), "result", output
                ),
            )
        )
    sealed_variants = {
        "datasets.field_identity": (
            d._EntityFieldIdentity,
            d._CatalogFieldIdentity,
            d._RuntimeMetricFieldIdentity,
            d._GeneratedFieldIdentity,
        ),
        "datasets.physical_type_state": (d._ResolvedPhysicalType, d._DeferredPhysicalType),
        "datasets.row_bound": (d._UnknownRowBound, d._StaticRowBound, d._RuntimePolicyRowBound),
        "datasets.cardinality": (d._SingletonCardinality, d._KeyedCardinality),
        "datasets.ordering": (d._UnorderedOrdering, d._OrderedOrdering),
        "datasets.byte_count": (d._ExactByteCount, d._UnavailableByteCount),
    }
    return DisclosureProvider(
        "core", with_sealed_variants(descriptors, sealed_variants), tuple(exports)
    )
