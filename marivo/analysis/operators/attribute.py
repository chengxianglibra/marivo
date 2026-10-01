"""Frozen partition method and schema facts consumed by R7/R8 helpers."""

from __future__ import annotations

from marivo.analysis.datasets.descriptors import (
    DatasetField,
    DatasetRowContract,
    DatasetRowSetContract,
    _CatalogFieldIdentity,
    _deferred_type,
    _generated_identity,
    _GeneratedFieldIdentity,
    _make_field,
    _make_field_id,
    _StableIdRegistry,
)
from marivo.analysis.observation.contracts import (
    DimensionInput,
)
from marivo.analysis.observation.fold_contracts import MetricFoldAuthorityV1
from marivo.analysis.operators.attribution_contracts import (
    INDEPENDENT_RESOLUTION_METHODS,
    AttributionMethod,
    AttributionSemantics,
)
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


def validate_attribution(row: DatasetRowContract, rows: DatasetRowSetContract) -> None:
    semantics = row.family_semantics
    if not isinstance(semantics, AttributionSemantics) or row.shape_id.local_shape_id not in (
        "joint",
        "hierarchy",
    ):
        raise attribution_error("exact Attribution row semantics", "invalid family contract")
    if (
        semantics.numeric_type not in ("unknown", "int64", "float64", "decimal")
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
