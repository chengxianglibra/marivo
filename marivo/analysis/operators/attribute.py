"""Private additive Attribution construction and family registration."""

from __future__ import annotations

from dataclasses import replace

from marivo.analysis.datasets.actions import construct_operator
from marivo.analysis.datasets.base import Dataset, MaterializedDataset, _dataset_repr
from marivo.analysis.datasets.descriptors import (
    _CORE_TOKEN,
    DatasetField,
    DatasetRowContract,
    DatasetRowSetContract,
    _CatalogFieldIdentity,
    _deferred_type,
    _generated_identity,
    _GeneratedFieldIdentity,
    _keyed_cardinality,
    _make_field,
    _make_field_id,
    _make_row_contract,
    _make_row_set_contract,
    _make_schema,
    _make_shape_id,
    _StableIdRegistry,
    _unknown_row_bound,
    _unordered_ordering,
)
from marivo.analysis.datasets.registry import (
    ConsumerRegistration,
    DatasetFamilyRegistration,
    DatasetFamilyRegistry,
)
from marivo.analysis.datasets.state import MaterializedDatasetState, _validate_materialized_state
from marivo.analysis.observation.contracts import (
    DimensionInput,
    RetainedRowsPayload,
    owner_of,
    producer_contract,
)
from marivo.analysis.observation.fold_contracts import MetricFoldAuthorityV1
from marivo.analysis.operators.attribution import (
    LogicalAttributionDataset,
    MaterializedAttributionDataset,
)
from marivo.analysis.operators.attribution_contracts import (
    INDEPENDENT_RESOLUTION_METHODS,
    AttributePayload,
    AttributeSpecV1,
    AttributionMethod,
    AttributionMode,
    AttributionSemantics,
    delta_part_authorities,
)
from marivo.analysis.operators.contracts import DeltaSemantics
from marivo.analysis.operators.errors import attribution_error
from marivo.refs import Ref, SemanticKind, SemanticKindTag

GENERATED = (
    ("active_axis_mask", "attribution_partition_identity", "mask", False),
    ("other_mask", "attribution_partition_identity", "mask", False),
    ("current_value", "comparison_value", "numeric", False),
    ("baseline_value", "comparison_value", "numeric", False),
    ("overall_delta", "comparison_value", "numeric", False),
    ("contribution", "effect_value", "numeric", False),
    ("share_of_total_delta", "effect_value", "float64", True),
    ("share_of_positive_pool", "effect_value", "float64", True),
    ("share_of_negative_pool", "effect_value", "float64", True),
    ("contribution_rank", "rank", "int64", False),
    ("status", "status", "string", False),
)


def attribute_method(authority: MetricFoldAuthorityV1, axes: tuple[str, ...]) -> AttributionMethod:
    """Select the sole admitted arithmetic from closed component and partition proof."""
    if authority.membership is not None:
        from marivo.analysis.observation.distinct_contracts import (
            membership_authority_reason,
            validate_membership_authority,
        )

        invalid_reason = None
        try:
            validate_membership_authority(authority)
        except ValueError as error:
            invalid_reason = membership_authority_reason(error)
        if invalid_reason is not None:
            raise attribution_error(
                "exact reproducible root distinct membership",
                invalid_reason,
                repair="Re-observe the count_distinct Metric and rebuild current.compare(baseline).attribute(axes=...). Use a compatible engine target for membership checkpoints.",
            ) from None
        return "distinct_membership@v1"
    partitions = dict(authority.axis_partitions)
    if any(partitions.get(axis) not in ("functional", "disjoint") for axis in axes):
        raise attribution_error(
            "complete disjoint requested-axis partitions", "missing or overlapping partition proof"
        )
    if authority.distribution is not None:
        from marivo.analysis.observation.distribution_contracts import (
            validate_distribution_authority,
        )

        try:
            validate_distribution_authority(authority)
        except ValueError:
            raise attribution_error(
                "exact registered distribution authority", "invalid distribution authority"
            ) from None
        return "distribution_shapley@v1"
    nodes = {node.node_id: node for node in authority.nodes}
    components = {component.node_id: component for component in authority.components}
    if any(
        component.spatial_merge != "sum" or not component.state_columns
        for component in authority.components
    ):
        raise attribution_error(
            "exact additive spatial component state", "nonadditive component or missing fold state"
        )

    def unwrap(node_id: str) -> str:
        while nodes[node_id].kind == "identity":
            node_id = nodes[node_id].children[0]
        return node_id

    def additive(node_id: str, *, component_mix: bool = False) -> bool:
        node = nodes[unwrap(node_id)]
        if node.kind == "component":
            component = components[node.node_id]
            return component.kind in ("sum", "count") and (
                not component_mix or not component.cumulative
            )
        return node.kind == "linear" and all(
            additive(child, component_mix=component_mix) for child in node.children
        )

    root = nodes[unwrap(authority.root_id)]
    if additive(root.node_id):
        return "additive_difference@v1"
    if root.kind == "ratio" and all(additive(child, component_mix=True) for child in root.children):
        return "component_mix@v1"
    if (
        root.kind == "component"
        and components[root.node_id].kind in ("mean", "weighted_mean")
        and not components[root.node_id].cumulative
    ):
        return "component_mix@v1"
    raise attribution_error(
        "registered additive or component-mix Metric", "unsupported aggregation contract"
    )


def _axis_ref(axis: DimensionInput) -> Ref[SemanticKindTag]:
    candidate = axis if isinstance(axis, Ref) else getattr(axis, "ref", None)
    if not isinstance(candidate, Ref) or candidate.kind is not SemanticKind.DIMENSION:
        raise attribution_error(
            "governed non-time Dimension axes",
            "invalid or time Dimension",
            repair="Use governed Dimensions as axes; retain comparison_ordinal for time scope.",
        )
    return candidate


def _generated(
    name: str, role: str, kind: str, nullable: bool, ids: _StableIdRegistry
) -> DatasetField:
    field_id = _make_field_id(f"generated.attribute.{name}@v1")
    return _make_field(
        field_id=field_id,
        name=name,
        role_id=role,
        identity=_generated_identity(field_id),
        derivation_identity=f"attribute.{name}@v1",
        logical_type_id=kind,
        physical_type_state=_deferred_type(kind, ids=ids),
        nullable=nullable,
        ids=ids,
    )


def attribute(
    dataset: Dataset,
    *,
    axes: tuple[DimensionInput, ...] | list[DimensionInput],
    mode: AttributionMode = "joint",
    top_k: int | None = None,
) -> LogicalAttributionDataset:
    """Construct one exact private Attribution without reading source data."""
    if dataset.kind != "delta" or not isinstance(
        dataset.row_contract.family_semantics, DeltaSemantics
    ):
        raise attribution_error("one Metric Delta", "unsupported receiver")
    if not isinstance(axes, (tuple, list)) or not axes or mode not in ("joint", "hierarchy"):
        raise attribution_error(
            "nonempty ordered axes and joint or hierarchy mode", "invalid attribution arguments"
        )
    axes = tuple(axes)
    refs = tuple(_axis_ref(axis) for axis in axes)
    if len({axis.path for axis in refs}) != len(refs) or (mode == "hierarchy" and len(axes) < 2):
        raise attribution_error(
            "unique axes and at least two for hierarchy", "duplicate axes or meaningless hierarchy"
        )
    if top_k is not None and (type(top_k) is not int or not 1 <= top_k <= 1000):
        raise attribution_error("top_k integer in [1, 1000]", "invalid Top-K bound")
    known = {
        field.identity.identity_id.split(":", 1)[1]: field
        for field in dataset.schema.columns
        if field.role_id == "dimension" and isinstance(field.identity, _CatalogFieldIdentity)
    }
    expanded_compare = None
    original_input_row = None
    inputs: tuple[Dataset, ...] = (dataset,)
    operator_id = "delta.attribute"
    input_row, input_rows = dataset.row_contract, dataset.row_set_contract
    if any(axis.path not in known for axis in refs):
        if isinstance(dataset, MaterializedDataset):
            raise attribution_error(
                "all requested axes in retained Delta",
                "materialized missing-axis barrier",
                repair="Reconstruct logical current and baseline with .with_dimensions(*axes) before .aggregate().compare(...).attribute(axes=axes).",
            )
        from marivo.analysis.operators.attribute_expansion import expand_attribute_inputs

        current, baseline, expanded_compare = expand_attribute_inputs(dataset, axes)
        inputs = (dataset, current, baseline)
        operator_id = "delta.attribute_expanded"
        original_input_row = dataset.row_contract
        input_row, input_rows = expanded_compare.output_row, expanded_compare.output_rows
        known = {
            field.identity.identity_id.split(":", 1)[1]: field
            for field in input_row.schema.columns
            if field.role_id == "dimension" and isinstance(field.identity, _CatalogFieldIdentity)
        }
    semantics = input_row.family_semantics
    if not isinstance(semantics, DeltaSemantics):
        raise attribution_error("exact expanded Delta semantics", "invalid expansion")
    axis_fields = tuple(known[axis.path] for axis in refs)
    authority_pairs = delta_part_authorities(input_row)
    methods = tuple(
        attribute_method(authority, tuple(axis.path for axis in refs))
        for _, authority in authority_pairs
    )
    if len(set(methods)) != 1:
        raise attribution_error("same method on both comparison sides", "incompatible side folds")
    method = methods[0]
    if method == "distribution_shapley@v1":
        if authority_pairs[0][1].distribution != authority_pairs[1][1].distribution:
            raise attribution_error(
                "identical percentile methods and parameters", "incompatible distribution sides"
            )
        if any(field.role_id == "entity_identity" for field in input_row.schema.columns):
            raise attribution_error(
                "non-identity distribution Attribution", "source-required Entity scope"
            )
    axis_ids = tuple(field.field_id for field in axis_fields)
    scope = tuple(
        field
        for field in input_row.schema.columns
        if field.field_id in input_row.coordinate_field_ids and field.field_id not in axis_ids
    )
    ids = dataset._registration.ids
    numeric_type = semantics.numeric_type if method == "additive_difference@v1" else "float64"
    output_axes = tuple(replace(field, _token=_CORE_TOKEN, nullable=True) for field in axis_fields)
    times = tuple(field for field in input_row.schema.columns if field.role_id == "comparison_time")
    generated = tuple(
        _generated(
            name,
            role,
            f"bool_tuple:{len(axes)}"
            if kind == "mask"
            else numeric_type
            if kind == "numeric"
            else kind,
            nullable,
            ids,
        )
        for name, role, kind, nullable in GENERATED
    )
    columns = (*scope, *output_axes, *times, *generated)
    if len({field.name for field in columns}) != len(columns):
        raise attribution_error(
            "unambiguous scope, axes and generated field names", "attribution field collision"
        )
    active_id, other_id = generated[0].field_id, generated[1].field_id
    scope_ids = tuple(field.field_id for field in scope)
    output_semantics = AttributionSemantics(
        _token=_CORE_TOKEN,
        metric_ref=semantics.metric_ref,
        metric_unit=semantics.metric_unit,
        numeric_type=numeric_type,
        scope_field_ids=scope_ids,
        axis_field_ids=axis_ids,
        resolution_prefixes=(axis_ids,)
        if mode == "joint"
        else tuple(axis_ids[:index] for index in range(1, len(axes) + 1)),
        current_time_field_name=semantics.current_time_field_name,
        baseline_time_field_name=semantics.baseline_time_field_name,
        method=method,
        approximation_class=semantics.approximation_class,
        resolution_semantics="independent"
        if method in INDEPENDENT_RESOLUTION_METHODS
        else "rollup",
        rollup_safe=method not in INDEPENDENT_RESOLUTION_METHODS,
    )
    row = _make_row_contract(
        schema_version=1,
        shape_id=_make_shape_id("attribution", mode, 1, ids=ids),
        schema=_make_schema(columns),
        coordinate_field_ids=(*scope_ids, active_id, *axis_ids, other_id),
        key_field_ids=(
            *scope_ids,
            *((active_id,) if mode == "hierarchy" else ()),
            *axis_ids,
            other_id,
        ),
        family_semantics=output_semantics,
    )
    rows = _make_row_set_contract(
        schema_version=1,
        cardinality=_keyed_cardinality(_unknown_row_bound()),
        ordering=_unordered_ordering(),
    )
    spec = AttributeSpecV1(
        input_row,
        input_rows,
        row,
        rows,
        axis_fields,
        scope,
        method,
        mode,
        top_k,
        semantics.current_fold_authority,
        semantics.baseline_fold_authority,
        expanded_compare,
        original_input_row,
    )
    result = construct_operator(
        owner=owner_of(dataset),
        registry=dataset._registry,
        operator_id=operator_id,
        contract_versions=producer_contract(operator_id).versions,
        inputs=inputs,
        row_contract=row,
        row_set_contract=rows,
        payload=AttributePayload(_token=_CORE_TOKEN, spec=spec),
    )
    if not isinstance(result, LogicalAttributionDataset):
        raise attribution_error("paired Logical Attribution", "invalid family registration")
    return result


def validate_attribution(row: DatasetRowContract, rows: DatasetRowSetContract) -> None:
    semantics = row.family_semantics
    if not isinstance(semantics, AttributionSemantics) or row.shape_id.local_shape_id not in (
        "joint",
        "hierarchy",
    ):
        raise attribution_error("exact Attribution row semantics", "invalid family contract")
    if (
        semantics.numeric_type not in ("int64", "float64", "decimal")
        or (
            semantics.method
            in ("component_mix@v1", "distinct_membership@v1", "distribution_shapley@v1")
            and semantics.numeric_type != "float64"
        )
        or semantics.approximation_class
        not in ("exact", "sampled_population", "semantic_percentile", "sampled_semantic_percentile")
    ):
        raise attribution_error(
            "exact registered numeric and approximation meaning",
            "invalid attribution value semantics",
        )
    axes, scope = semantics.axis_field_ids, semantics.scope_field_ids
    if not axes or len({*scope, *axes}) != len((*scope, *axes)) or rows.cardinality.kind != "keyed":
        raise attribution_error(
            "nonempty unique axes and keyed Attribution", "invalid coordinate authority"
        )
    active, other = (
        _make_field_id(f"generated.attribute.{name}@v1")
        for name in ("active_axis_mask", "other_mask")
    )
    hierarchy = row.shape_id.local_shape_id == "hierarchy"
    expected_prefixes = (
        tuple(axes[:index] for index in range(1, len(axes) + 1)) if hierarchy else (axes,)
    )
    if (
        semantics.resolution_prefixes != expected_prefixes
        or (hierarchy and len(axes) < 2)
        or semantics.method
        not in (
            "additive_difference@v1",
            "component_mix@v1",
            "distinct_membership@v1",
            "distribution_shapley@v1",
        )
        or semantics.resolution_semantics
        != ("independent" if semantics.method in INDEPENDENT_RESOLUTION_METHODS else "rollup")
        or semantics.rollup_safe is not (semantics.method not in INDEPENDENT_RESOLUTION_METHODS)
    ):
        raise attribution_error(
            "closed method-specific resolution authority", "invalid resolution semantics"
        )
    if row.coordinate_field_ids != (*scope, active, *axes, other) or row.key_field_ids != (
        *scope,
        *((active,) if hierarchy else ()),
        *axes,
        other,
    ):
        raise attribution_error(
            "complete scoped mask-discriminated keys", "invalid Attribution key"
        )
    fields = {field.name: field for field in row.schema.columns}
    for name, role, kind, nullable in GENERATED:
        field = fields.get(name)
        expected_type = (
            f"bool_tuple:{len(axes)}"
            if kind == "mask"
            else semantics.numeric_type
            if kind == "numeric"
            else kind
        )
        if (
            field is None
            or field.field_id.value != f"generated.attribute.{name}@v1"
            or field.role_id != role
            or field.logical_type_id != expected_type
            or field.nullable != nullable
            or not isinstance(field.identity, _GeneratedFieldIdentity)
            or field.identity.producer_field_id != field.field_id
            or field.derivation_identity != f"attribute.{name}@v1"
        ):
            raise attribution_error(
                "exact generated Attribution field contracts", "invalid generated field"
            )
    by_id = {field.field_id: field for field in row.schema.columns}
    if any(
        by_id[axis].role_id != "dimension"
        or not by_id[axis].nullable
        or not isinstance(by_id[axis].identity, _CatalogFieldIdentity)
        for axis in axes
    ):
        raise attribution_error("nullable governed Dimension axis cells", "invalid axis field")
    paired = (semantics.current_time_field_name, semantics.baseline_time_field_name)
    if (paired[0] is None) != (paired[1] is None):
        raise attribution_error("paired time bindings", "incomplete paired time")
    expected_names = (
        *(by_id[key].name for key in scope),
        *(by_id[key].name for key in axes),
        *(("current_time", "baseline_time") if paired[0] is not None else ()),
        *(name for name, _, _, _ in GENERATED),
    )
    if "rank" in fields:
        rank = fields["rank"]
        if (
            rank.field_id.value != "generated.rank@v1"
            or rank.role_id != "rank"
            or rank.logical_type_id != "int64"
            or not rank.nullable
            or not isinstance(rank.identity, _GeneratedFieldIdentity)
            or rank.identity.producer_field_id != rank.field_id
        ):
            raise attribution_error("exact nullable generated rank", "invalid Attribution rank")
        expected_names = (*expected_names, "rank")
    if tuple(fields) != expected_names:
        raise attribution_error("ordered scoped Attribution fields", "invalid schema order")


def register_attribution(registry: DatasetFamilyRegistry, ids: _StableIdRegistry) -> None:
    def decode(state: MaterializedDatasetState) -> MaterializedDatasetState:
        _validate_materialized_state(state, ids=ids)
        return state

    shapes = tuple(
        _make_shape_id("attribution", shape, 1, ids=ids) for shape in ("joint", "hierarchy")
    )
    registry.register(
        DatasetFamilyRegistration(
            family_id="attribution",
            logical_type=LogicalAttributionDataset,
            materialized_type=MaterializedAttributionDataset,
            shape_ids=shapes,
            owner_id="operators.attribute",
            ids=ids,
            row_validator=validate_attribution,
            consumers=tuple(
                ConsumerRegistration(
                    f"attribution.{method}",
                    ("input",),
                    "attribution",
                    shapes,
                    ("attribution.current_rows@v1",),
                )
                for method in ("where", "rank", "limit")
            ),
            repr_renderer=_dataset_repr,
            materialized_state_decoder=decode,
            node_payload_types=(AttributePayload, RetainedRowsPayload),
            consumer_admission=lambda dataset, method: (
                not any(field.name == "rank" for field in dataset.schema.columns)
                if method == "attribution.rank"
                else dataset.row_set_contract.ordering.kind == "ordered"
                if method == "attribution.limit"
                else True
            ),
        )
    )
