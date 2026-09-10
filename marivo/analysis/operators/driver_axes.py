"""Pure driver-axis admission, common screening scope and Candidate contracts."""

from __future__ import annotations

import re

from marivo.analysis.datasets import descriptors as d
from marivo.analysis.datasets.actions import construct_operator
from marivo.analysis.datasets.base import Dataset, MaterializedDataset
from marivo.analysis.observation.contracts import (
    IDENTITY_FIELD_ID,
    DimensionInput,
    owner_of,
    producer_contract,
)
from marivo.analysis.observation.fold_contracts import decode_fold_authority
from marivo.analysis.operators.attribute import _axis_ref, attribute_method
from marivo.analysis.operators.attribution_contracts import delta_part_authorities
from marivo.analysis.operators.candidate_contracts import CandidateSemantics
from marivo.analysis.operators.candidate_dataset import LogicalCandidateDataset
from marivo.analysis.operators.contracts import DeltaSemantics
from marivo.analysis.operators.driver_contracts import (
    DriverCandidateDefinition,
    DriverCandidatePayload,
    DriverCandidateSpecV1,
)
from marivo.analysis.operators.errors import driver_error as discovery_error
from marivo.semantic._quantile import decode_approximation

DRIVER_FIELDS = (
    ("axis_ref", "string"),
    ("axis_cardinality", "int64"),
    ("concentration_member_count", "int64"),
    ("concentration_share", "float64"),
)


def validate_driver_definition(definition: DriverCandidateDefinition) -> None:
    """Validate the closed original search authority without semantic lookup."""
    if type(definition) is not DriverCandidateDefinition or (
        definition.objective,
        definition.method_id,
    ) != ("driver_axes", "axis_concentration@v1"):
        raise discovery_error("closed driver-axis definition", "changed objective or method")
    if type(definition.limit) is not int or not 1 <= definition.limit <= 1000:
        raise discovery_error("integer discovery limit in [1, 1000]", "invalid limit")
    pattern = (
        r"ds_[a-f0-9]{64}"
        if definition.input_state_kind == "logical"
        else r"artifact_[a-f0-9]{32}"
        if definition.input_state_kind == "materialized"
        else None
    )
    if pattern is None or re.fullmatch(pattern, definition.input_authority) is None:
        raise discovery_error("exact input fingerprint or Artifact ref", "invalid input authority")
    if (
        type(definition.search_space) is not tuple
        or not definition.search_space
        or any(type(axis) is not str or not axis for axis in definition.search_space)
        or len(set(definition.search_space)) != len(definition.search_space)
    ):
        raise discovery_error(
            "ordered nonempty unique governed search axes", "invalid search space"
        )
    decode_approximation(definition.approximation)
    current, baseline = (
        decode_fold_authority(v)
        for v in (definition.fold_authority, definition.baseline_fold_authority)
    )
    if (
        len(current.metrics) != 1
        or len(baseline.metrics) != 1
        or current.metrics != baseline.metrics
        or "metric:" + current.metrics[0].metric_ref != definition.metric_key
        or current.time_grain() != baseline.time_grain()
        or current.temporal_snapshot() != baseline.temporal_snapshot()
        or (definition.metric_unit is not None and type(definition.metric_unit) is not str)
    ):
        raise discovery_error(
            "one compatible additive Metric and paired fold authority", "invalid driver binding"
        )
    for authority in (current.metrics[0], baseline.metrics[0]):
        retained = tuple(
            axis for axis in definition.search_space if axis in dict(authority.axis_partitions)
        )
        if attribute_method(authority, retained) != "additive_difference@v1":
            raise discovery_error(
                "exact additive Delta partitions", "nonadditive or component-mix Metric"
            )
    scope = definition.scope_fields
    times = definition.paired_time_fields
    if (
        type(scope) is not tuple
        or type(times) is not tuple
        or len({f.field_id for f in (*scope, *times)}) != len((*scope, *times))
        or any(
            f.role_id not in ("dimension", "entity_identity", "comparison_coordinate")
            for f in scope
        )
        or any(f.role_id != "comparison_time" for f in times)
        or any(
            isinstance(f.identity, d._CatalogFieldIdentity)
            and f.identity.identity_id.split(":", 1)[1] in definition.search_space
            for f in scope
        )
    ):
        raise discovery_error("one exact common screening scope", "invalid driver scope authority")


def driver_axes(
    dataset: Dataset,
    *,
    search_space: tuple[DimensionInput, ...] | list[DimensionInput],
    limit: int = 50,
) -> LogicalCandidateDataset:
    """Construct complete additive driver screening without source execution."""
    if dataset.kind != "delta" or not isinstance(
        dataset.row_contract.family_semantics, DeltaSemantics
    ):
        raise discovery_error("one additive Metric Delta", "unsupported receiver")
    if not isinstance(search_space, (tuple, list)) or not search_space:
        raise discovery_error("nonempty ordered governed search space", "invalid search space")
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise discovery_error("integer discovery limit in [1, 1000]", "invalid limit")
    axes = tuple(search_space)
    refs = tuple(_axis_ref(axis) for axis in axes)
    if len({axis.path for axis in refs}) != len(refs):
        raise discovery_error("unique governed search axes", "duplicate search axes")
    original = dataset.row_contract.family_semantics
    known = {
        f.identity.identity_id.split(":", 1)[1]: f
        for f in dataset.schema.columns
        if f.role_id == "dimension" and isinstance(f.identity, d._CatalogFieldIdentity)
    }
    expanded_compare = None
    original_input_row = None
    inputs: tuple[Dataset, ...] = (dataset,)
    operator_id = "discover.driver_axes"
    input_row, input_rows = dataset.row_contract, dataset.row_set_contract
    if any(axis.path not in known for axis in refs):
        if isinstance(dataset, MaterializedDataset):
            raise discovery_error(
                "all requested axes in retained Delta", "materialized missing-axis barrier"
            )
        from marivo.analysis.operators.attribute_expansion import expand_attribute_inputs

        current, baseline, expanded_compare = expand_attribute_inputs(dataset, axes)
        inputs = (dataset, current, baseline)
        operator_id = "discover.driver_axes_expanded"
        original_input_row = dataset.row_contract
        input_row, input_rows = expanded_compare.output_row, expanded_compare.output_rows
        known = {
            f.identity.identity_id.split(":", 1)[1]: f
            for f in input_row.schema.columns
            if f.role_id == "dimension" and isinstance(f.identity, d._CatalogFieldIdentity)
        }
    incoming = input_row.family_semantics
    if not isinstance(incoming, DeltaSemantics):
        raise discovery_error("exact expanded Delta semantics", "invalid expansion")
    paths = tuple(axis.path for axis in refs)
    for _, authority in delta_part_authorities(input_row):
        if attribute_method(authority, paths) != "additive_difference@v1":
            raise discovery_error(
                "exact additive Delta partitions", "nonadditive or component-mix Metric"
            )
    axis_fields = tuple(known[axis.path] for axis in refs)
    axis_ids = tuple(f.field_id for f in axis_fields)
    scope = tuple(
        f
        for f in input_row.schema.columns
        if f.field_id in input_row.coordinate_field_ids and f.field_id not in axis_ids
    )
    times = tuple(f for f in input_row.schema.columns if f.role_id == "comparison_time")
    definition = DriverCandidateDefinition(
        "materialized" if isinstance(dataset, MaterializedDataset) else "logical",
        dataset.state.artifact_ref.ref
        if isinstance(dataset, MaterializedDataset)
        else dataset.definition_fingerprint,
        limit,
        original.approximation_class,
        original.current_fold_authority,
        original.baseline_fold_authority,
        "metric:" + original.metric_ref,
        original.metric_unit,
        paths,
        scope,
        times,
    )
    validate_driver_definition(definition)
    from marivo.analysis.operators.discovery import COMMON_FIELDS, _generated

    ids = dataset._registration.ids
    common = tuple(_generated("driver_axes", name, kind, ids) for name, kind in COMMON_FIELDS)
    values = tuple(_generated("driver_axes", name, kind, ids) for name, kind in DRIVER_FIELDS)
    columns = (*common, *scope, *times, *values)
    if len({f.name for f in columns}) != len(columns):
        raise discovery_error("unambiguous scope and generated names", "Candidate field collision")
    semantics = CandidateSemantics(
        _token=d._CORE_TOKEN,
        objective="driver_axes",
        method_id="axis_concentration@v1",
        approximation=definition.approximation,
        item_id_field_id=common[0].field_id,
        score_field_id=common[1].field_id,
        reason_codes_field_id=common[2].field_id,
    )
    keys = (*(f.field_id for f in scope), values[0].field_id)
    row = d._make_row_contract(
        schema_version=1,
        shape_id=d._make_shape_id("candidate", "driver-axis", 1, ids=ids),
        schema=d._make_schema(columns),
        coordinate_field_ids=keys,
        key_field_ids=keys,
        family_semantics=semantics,
    )
    rows = d._make_row_set_contract(
        schema_version=1,
        cardinality=d._keyed_cardinality(d._static_row_bound(limit)),
        ordering=d._ordered_ordering(
            tuple(
                d._make_order_term(
                    key,
                    direction="descending" if key == semantics.score_field_id else "ascending",
                    nulls="last",
                    value_order_contract_id="observation.identity_tuple@v1"
                    if key == IDENTITY_FIELD_ID
                    else "observation.scalar_order@v1",
                    ids=ids,
                )
                for key in (semantics.score_field_id, *keys, semantics.item_id_field_id)
            )
        ),
    )
    spec = DriverCandidateSpecV1(
        input_row,
        input_rows,
        row,
        rows,
        definition,
        axis_fields,
        scope,
        incoming.current_fold_authority,
        incoming.baseline_fold_authority,
        expanded_compare,
        original_input_row,
        dataset.row_set_contract if original_input_row is not None else None,
    )
    result = construct_operator(
        owner=owner_of(dataset),
        registry=dataset._registry,
        operator_id=operator_id,
        contract_versions=producer_contract(operator_id).versions,
        inputs=inputs,
        row_contract=row,
        row_set_contract=rows,
        payload=DriverCandidatePayload(_token=d._CORE_TOKEN, spec=spec),
    )
    if not isinstance(result, LogicalCandidateDataset):
        raise discovery_error("paired Logical Candidate", "invalid family registration")
    return result


def retained_driver_field(field: d.DatasetField) -> bool:
    """Validate retained Delta time fields independently of their presentation name."""
    return (
        field.name in ("comparison_ordinal", "current_time", "baseline_time")
        and field.field_id.value == f"generated.compare.{field.name}@v1"
        and (
            field.derivation_identity == "compare.comparison_ordinal@v1"
            if field.name == "comparison_ordinal"
            else re.fullmatch(
                rf"compare\.{field.name}@v1:coordinate\.[a-f0-9]{{64}}", field.derivation_identity
            )
            is not None
        )
        and isinstance(field.identity, d._GeneratedFieldIdentity)
        and field.identity.producer_field_id == field.field_id
        and (
            (
                field.role_id == "comparison_coordinate"
                and field.name == "comparison_ordinal"
                and field.logical_type_id == "int64"
                and not field.nullable
            )
            or (
                field.role_id == "comparison_time"
                and field.name != "comparison_ordinal"
                and field.logical_type_id in ("date", "timestamp")
                and field.nullable
            )
        )
    )


def validate_driver_candidate(row: d.DatasetRowContract, rows: d.DatasetRowSetContract) -> None:
    """Keep driver rows closed while retaining their exact Delta screening coordinates."""
    from marivo.analysis.operators.discovery import (
        COMMON_FIELDS,
        _generated_field,
        candidate_filterable_field,
    )

    s = row.family_semantics
    if (
        not isinstance(s, CandidateSemantics)
        or s.objective != "driver_axes"
        or s.method_id != "axis_concentration@v1"
        or row.shape_id.local_shape_id != "driver-axis"
    ):
        raise discovery_error("exact driver Candidate semantics", "invalid driver semantics")
    decode_approximation(s.approximation)
    fields = {f.name: f for f in row.schema.columns}
    for name, kind in (*COMMON_FIELDS, *DRIVER_FIELDS):
        f = fields.get(name)
        if f is None or not _generated_field(f, "driver_axes", name, kind):
            raise discovery_error("exact generated driver field contracts", "invalid driver field")
    scope = tuple(
        f
        for f in row.schema.columns
        if f.role_id in ("entity_identity", "dimension", "comparison_coordinate")
    )
    identities = tuple(f for f in scope if f.role_id == "entity_identity")
    dimensions = tuple(f for f in scope if f.role_id == "dimension")
    ordinals = tuple(f for f in scope if f.role_id == "comparison_coordinate")
    times = tuple(f for f in row.schema.columns if f.role_id == "comparison_time")
    if (
        len(identities) > 1
        or len(ordinals) > 1
        or scope != (*identities, *dimensions, *ordinals)
        or any(not isinstance(f.identity, d._CatalogFieldIdentity) for f in dimensions)
        or any(
            f.field_id != IDENTITY_FIELD_ID
            or f.name != "entity_identity"
            or f.nullable
            or f.logical_type_id != "identity_tuple"
            or not isinstance(f.identity, d._EntityFieldIdentity)
            or not f.identity.identity_signature
            or f.derivation_identity != "identity.entity_identity@v1"
            for f in identities
        )
        or any(not retained_driver_field(f) for f in (*ordinals, *times))
        or tuple(f.name for f in times) != (("current_time", "baseline_time") if ordinals else ())
        or (times and times[0].logical_type_id != times[1].logical_type_id)
    ):
        raise discovery_error(
            "exact retained Delta screening scope and paired times", "invalid driver coordinates"
        )
    expected = (
        *(name for name, _ in COMMON_FIELDS),
        *(f.name for f in scope),
        *(f.name for f in times),
        *(name for name, _ in DRIVER_FIELDS),
    )
    if "rank" in fields:
        if not candidate_filterable_field(fields["rank"]):
            raise discovery_error("registered nullable rank", "invalid driver rank")
        expected = (*expected, "rank")
    keys = (*(f.field_id for f in scope), fields["axis_ref"].field_id)
    if (
        tuple(fields) != expected
        or row.coordinate_field_ids != keys
        or row.key_field_ids != keys
        or rows.cardinality.kind != "keyed"
        or not isinstance(rows.ordering, d._OrderedOrdering)
        or (s.item_id_field_id, s.score_field_id, s.reason_codes_field_id)
        != tuple(fields[name].field_id for name, _ in COMMON_FIELDS)
    ):
        raise discovery_error("exact scoped driver schema and key", "invalid driver row contract")
    if "rank" not in fields and (
        tuple(t.field_id for t in rows.ordering.terms)
        != (s.score_field_id, *keys, s.item_id_field_id)
        or any(
            (t.direction, t.nulls, t.value_order_contract_id)
            != (
                "descending" if i == 0 else "ascending",
                "last",
                "observation.identity_tuple@v1"
                if t.field_id == IDENTITY_FIELD_ID
                else "observation.scalar_order@v1",
            )
            for i, t in enumerate(rows.ordering.terms)
        )
    ):
        raise discovery_error(
            "score descending and exact typed key ordering", "invalid driver ordering"
        )
