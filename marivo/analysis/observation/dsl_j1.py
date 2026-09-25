"""Private, source-free J1 construction over the existing Dataset logical roots."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Literal, overload

from marivo._temporal import TimeScope
from marivo.analysis.datasets.descriptors import (
    AnalysisDomain,
    DatasetField,
    DatasetFieldId,
    DatasetRowContract,
    DatasetRowSetContract,
    QuantityState,
    _complete_from_schema,
    _deferred_type,
    _difference_quantity,
    _entity_domain,
    _generated_identity,
    _group_domain,
    _keyed_cardinality,
    _make_field,
    _make_field_id,
    _make_row_contract,
    _make_row_set_contract,
    _make_schema,
    _make_shape_id,
    _observed_quantity,
    _row_contract_fingerprint,
    _row_set_contract_fingerprint,
    _row_statistic_quantity,
    _selected_entity_domain,
    _singleton_cardinality,
    _singleton_domain,
    _StableIdRegistry,
    _unknown_row_bound,
    _unordered_ordering,
)
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.datasets.handles import (
    DefinitionInput,
    LogicalInputToken,
    LogicalRootHandle,
    _make_logical_root,
)
from marivo.analysis.observation.contracts import MetricComponentPlan, derive_metric_components
from marivo.analysis.observation.coordinates import path_is_functional
from marivo.refs import DimensionKind, EntityKind, MetricKind, Ref, RelationshipKind, SemanticKind
from marivo.semantic._dsl_authoring import AdditiveAllV1
from marivo.semantic._expression_binding import CompiledExpressionSidecar
from marivo.semantic.ir import RatioComposition, TargetDimensionContract
from marivo.semantic.metric_graph import AggregateNodeV1, RatioNodeV1, component_node
from marivo.semantic.metric_graph_lowering import normalize_target_metric
from marivo.semantic.validator import Registry, normalize_target_dimension, normalize_target_entity

_KINDS = (
    "members",
    "read",
    "where",
    "group",
    "observe",
    "rollup",
    "summarize",
    "compare",
    "ratio_observe",
    "ratio_rollup",
    "correlate",
    "correlate_where",
    "correlate_summarize",
)
_IDS = _StableIdRegistry(
    families=frozenset({"dsl_j1"}),
    shapes=frozenset(("dsl_j1", kind, 1) for kind in _KINDS),
    roles=frozenset(
        {"member", "group", "value", "cell", "metric_identity", "status", "effect_value"}
    ),
    logical_types=frozenset({"int64", "float64", "string", "unknown"}),
    physical_types=frozenset({"int64", "float64", "string", "unknown"}),
    admitted_types=frozenset({"int64", "float64", "string", "unknown"}),
)
J1_SUM_PARTS = ("value.sum", "value.non_null_count", "value.row_count")
J1_COUNT_PARTS = ("value.count", "value.row_count")
J1_COMPARE_PARTS = ("current_endpoint", "baseline_endpoint")
J1_COMPARE_CHECKS = ("complete_pairing", "strict_numeric_cell")
J3_RATIO_COLUMNS = (
    "numerator_sum",
    "numerator_non_null_count",
    "numerator_row_count",
    "denominator_count",
    "denominator_row_count",
)
J3_RATIO_PARTS = (
    "value.numerator.sum",
    "value.numerator.non_null_count",
    "value.numerator.row_count",
    "value.denominator.count",
    "value.denominator.row_count",
)
J3_RATIO_CHECKS = ("complete_coverage", "contribution_partition", "component_binding")


def numeric_threshold_is_lossless(value_type: str, threshold: object) -> bool:
    """Admit a finite threshold without changing the Cell's numeric type."""
    return (value_type == "int64" and type(threshold) is int and -(2**63) <= threshold < 2**63) or (
        value_type == "float64"
        and (
            (type(threshold) is int and abs(threshold) <= 2**53)
            or (type(threshold) is float and math.isfinite(threshold))
        )
    )


def _reject(expected: str, received: str, *, repair: str) -> DatasetConstructionError:
    return DatasetConstructionError(
        expected=expected,
        received=received,
        repair=repair,
        location="dsl.j1",
    )


def _j1_contracts(
    context: J1Context,
    kind: str,
    entity_path: str,
    input_root: LogicalRootHandle | None,
    parameters: tuple[object, ...],
) -> tuple[DatasetRowContract, DatasetRowSetContract]:
    if kind in ("read", "group", "summarize") and not parameters:
        raise _reject(
            "canonical J1 node parameters",
            f"empty {kind} parameters",
            repair="Rebuild the J1 node from its admitted constructor.",
        )
    if (
        kind == "observe"
        and input_root is not None
        and input_root.shape_id.local_shape_id == "group"
        and (type(input_root.parameters) is not tuple or not input_root.parameters)
    ):
        raise _reject(
            "canonical J1 group input parameters",
            "empty group parameters",
            repair="Rebuild the J1 group input from its admitted constructor.",
        )
    entity = normalize_target_entity(context.registry, entity_path)
    member_type = entity.identity_signature[0][1]
    if member_type not in ("unknown", "string", "int64"):
        raise _reject(
            "string or int64 member identity",
            member_type,
            repair="Use a first-round single-column Entity identity.",
        )
    if kind in ("ratio_observe", "ratio_rollup"):
        paths: tuple[str, ...] = ()
        if kind == "ratio_observe":
            if len(parameters) != 5 or type(parameters[4]) is not tuple:
                raise _reject(
                    "bound ratio coordinates",
                    "invalid parameters",
                    repair="Rebuild the ratio observation.",
                )
            paths = parameters[4]
        group_path = parameters[2] if kind == "ratio_rollup" and len(parameters) == 3 else None
        keys: list[tuple[str, DatasetFieldId, str]] = []
        if kind == "ratio_observe":
            keys.append(
                (
                    "member",
                    _make_field_id("member." + entity_path + "." + entity.primary_key[0]),
                    member_type,
                )
            )
            for index, path in enumerate(paths):
                if not isinstance(path, str):
                    raise _reject(
                        "coordinate path", repr(path), repair="Use declared Dimension refs."
                    )
                coordinate = normalize_target_dimension(context.registry, path)
                keys.append(
                    (
                        f"coord_{index}",
                        _make_field_id("contribution." + path),
                        coordinate.logical_type,
                    )
                )
        elif isinstance(group_path, str):
            coordinate = normalize_target_dimension(context.registry, group_path)
            keys.append(
                ("group", _make_field_id("dimension." + group_path), coordinate.logical_type)
            )

        def ratio_field(
            name: str, identifier: DatasetFieldId, role: str, logical: str, nullable: bool
        ) -> DatasetField:
            return _make_field(
                field_id=identifier,
                name=name,
                role_id=role,
                identity=_generated_identity(identifier),
                derivation_identity="dsl.j1.ratio." + identifier.value,
                logical_type_id=logical,
                physical_type_state=_deferred_type(logical, ids=_IDS),
                nullable=nullable,
                ids=_IDS,
            )

        columns = [
            ratio_field(name, identifier, "member" if name == "member" else "group", logical, False)
            for name, identifier, logical in keys
        ]
        columns.extend(
            (
                ratio_field("value", _make_field_id("j1.value"), "value", "float64", True),
                ratio_field("cell_tag", _make_field_id("j1.cell_tag"), "cell", "string", False),
                ratio_field(
                    "cell_reason", _make_field_id("j1.cell_reason"), "cell", "string", True
                ),
            )
        )
        identifiers = tuple(identifier for _, identifier, _ in keys)
        row = _make_row_contract(
            schema_version=1,
            shape_id=_make_shape_id("dsl_j1", kind, 1, ids=_IDS),
            schema=_make_schema(tuple(columns)),
            coordinate_field_ids=identifiers,
            key_field_ids=identifiers,
            family_semantics=_complete_from_schema(),
        )
        rows = _make_row_set_contract(
            schema_version=1,
            cardinality=_keyed_cardinality(_unknown_row_bound())
            if keys
            else _singleton_cardinality(),
            ordering=_unordered_ordering(),
        )
        return row, rows
    group_type = "string"
    if kind == "group" or (
        kind == "observe"
        and input_root is not None
        and input_root.shape_id.local_shape_id == "group"
    ):
        group_path = (
            parameters[0]
            if kind == "group"
            else input_root.parameters[0]
            if input_root is not None and type(input_root.parameters) is tuple
            else None
        )
        if isinstance(group_path, str) and group_path in context.registry.dimensions:
            group_type = normalize_target_dimension(context.registry, group_path).logical_type
        if group_type not in ("unknown", "string"):
            raise _reject(
                "string categorical group",
                group_type,
                repair="Use a first-round string Dimension for grouping.",
            )
    if kind in ("members", "where", "read", "compare"):
        key = "member"
        key_id = _make_field_id("member." + entity_path + "." + entity.primary_key[0])
        key_type = member_type
    elif kind == "group" or (
        kind == "observe"
        and input_root is not None
        and input_root.shape_id.local_shape_id == "group"
    ):
        key = "group"
        group_key_path: object = (
            parameters[0]
            if kind == "group"
            else input_root.parameters[0]
            if input_root is not None and type(input_root.parameters) is tuple
            else None
        )
        key_id = _make_field_id(
            (
                "contribution."
                if kind == "group" and len(parameters) > 1 and parameters[1] == "contribution"
                else "dimension."
            )
            + str(group_key_path)
        )
        key_type = group_type
    elif kind == "observe":
        key = "member"
        key_id = _make_field_id("member." + entity_path + "." + entity.primary_key[0])
        key_type = member_type
    else:
        key = None
        key_id = None
        key_type = None

    def field(name: str, field_id: str, role: str, logical: str, nullable: bool) -> DatasetField:
        identifier = _make_field_id(field_id)
        return _make_field(
            field_id=identifier,
            name=name,
            role_id=role,
            identity=_generated_identity(identifier),
            derivation_identity="dsl.j1." + field_id,
            logical_type_id=logical,
            physical_type_state=_deferred_type(logical, ids=_IDS),
            nullable=nullable,
            ids=_IDS,
        )

    if kind == "correlate_summarize":
        if (
            input_root is None
            or input_root.shape_id.local_shape_id not in ("correlate", "correlate_where")
            or len(parameters) != 1
            or parameters[0] not in ("sum", "count", "mean")
        ):
            raise _reject(
                "bound coefficient row statistic",
                "invalid parameters",
                repair="Rebuild the statistic.",
            )
        value_type = "int64" if parameters[0] == "count" else "float64"
        statistic_columns = tuple(
            field(name, "j4.statistic." + name, role, logical, nullable)
            for name, role, logical, nullable in (
                ("value", "value", value_type, True),
                ("cell_tag", "cell", "string", False),
                ("cell_reason", "cell", "string", True),
            )
        )
        return (
            _make_row_contract(
                schema_version=1,
                shape_id=_make_shape_id("dsl_j1", kind, 1, ids=_IDS),
                schema=_make_schema(statistic_columns),
                coordinate_field_ids=(),
                key_field_ids=(),
                family_semantics=_complete_from_schema(),
            ),
            _make_row_set_contract(
                schema_version=1,
                cardinality=_singleton_cardinality(),
                ordering=_unordered_ordering(),
            ),
        )
    if kind in ("correlate", "correlate_where"):
        if kind == "correlate_where":
            if (
                input_root is None
                or input_root.shape_id.local_shape_id != "correlate"
                or len(parameters) != 2
                or parameters[0] not in ("lt", "lte", "gt", "gte", "eq")
                or not numeric_threshold_is_lossless("float64", parameters[1])
            ):
                raise _reject(
                    "bound coefficient predicate",
                    "invalid parameters",
                    repair="Rebuild the selection.",
                )
            if type(input_root.parameters) is not tuple:
                raise _reject(
                    "bound Association parameters",
                    "invalid parent",
                    repair="Rebuild the association.",
                )
            parameters = input_root.parameters
        if (
            len(parameters) != 4
            or not all(isinstance(value, str) for value in parameters)
            or parameters[2] != "spearman"
        ):
            raise _reject(
                "bound no-lag Spearman pair",
                "invalid parameters",
                repair="Rebuild the association.",
            )
        association_columns = tuple(
            field(name, "j4." + name, role, logical, nullable)
            for name, role, logical, nullable in (
                ("metric_key_a", "metric_identity", "string", False),
                ("metric_key_b", "metric_identity", "string", False),
                ("status", "status", "string", False),
                ("coefficient", "effect_value", "float64", True),
                ("input_observation_count", "effect_value", "int64", False),
                ("matched_observation_count", "effect_value", "int64", False),
                ("null_pair_count", "effect_value", "int64", False),
                ("complete_pair_count", "effect_value", "int64", False),
            )
        )
        association_keys = (association_columns[0].field_id, association_columns[1].field_id)
        return (
            _make_row_contract(
                schema_version=1,
                shape_id=_make_shape_id("dsl_j1", kind, 1, ids=_IDS),
                schema=_make_schema(association_columns),
                coordinate_field_ids=association_keys,
                key_field_ids=association_keys,
                family_semantics=_complete_from_schema(),
            ),
            _make_row_set_contract(
                schema_version=1,
                cardinality=_keyed_cardinality(_unknown_row_bound()),
                ordering=_unordered_ordering(),
            ),
        )
    columns = []
    if key is not None and key_id is not None and key_type is not None:
        columns.append(field(key, key_id.value, key, key_type, False))
    if (
        kind in ("read", "observe", "rollup", "summarize", "compare")
        or (kind == "where" and parameters and parameters[0] == "numeric")
        or (
            kind == "group"
            and input_root is not None
            and input_root.shape_id.local_shape_id == "observe"
        )
    ):
        if kind == "read":
            value_type = normalize_target_dimension(
                context.registry, str(parameters[0])
            ).logical_type
        elif kind == "summarize" and parameters[0] == "count":
            value_type = "int64"
        elif kind == "summarize" and parameters[0] == "mean":
            value_type = "float64"
        else:
            value_type = "unknown"
        columns.extend(
            (
                field("value", "j1.value", "value", value_type, True),
                field("cell_tag", "j1.cell_tag", "cell", "string", False),
                field("cell_reason", "j1.cell_reason", "cell", "string", True),
            )
        )
    row = _make_row_contract(
        schema_version=1,
        shape_id=_make_shape_id("dsl_j1", kind, 1, ids=_IDS),
        schema=_make_schema(tuple(columns)),
        coordinate_field_ids=(key_id,) if key_id is not None else (),
        key_field_ids=(key_id,) if key_id is not None else (),
        family_semantics=_complete_from_schema(),
    )
    rows = _make_row_set_contract(
        schema_version=1,
        cardinality=_keyed_cardinality(_unknown_row_bound())
        if key_id is not None
        else _singleton_cardinality(),
        ordering=_unordered_ordering(),
    )
    return row, rows


def j1_row_contracts(
    context: J1Context, root: LogicalRootHandle
) -> tuple[DatasetRowContract, DatasetRowSetContract]:
    """Recover the complete row contract bound into an exact J1 root."""
    kind = root.shape_id.local_shape_id
    parameters = root.parameters
    if type(parameters) is not tuple:
        raise _reject("canonical J1 parameters", "invalid root", repair="Rebuild the J1 node.")
    first = root
    while first.inputs:
        input_root = first.inputs[0].root
        if not isinstance(input_root, LogicalRootHandle):
            raise _reject(
                "J1 logical ancestry", "materialized leaf", repair="Use the exact J1 input."
            )
        first = input_root
    member_path = (
        first.parameters[0] if type(first.parameters) is tuple and first.parameters else None
    )
    if not isinstance(member_path, str):
        raise _reject("bound member Entity", "invalid root", repair="Rebuild the J1 node.")
    parent = root.inputs[0].root if root.inputs else None
    return _j1_contracts(
        context,
        kind,
        member_path,
        parent if isinstance(parent, LogicalRootHandle) else None,
        parameters,
    )


@dataclass(frozen=True, slots=True, eq=False)
class J1Context:
    """One invocation's declared semantic facts and logical-root authority."""

    registry: Registry
    sidecar: CompiledExpressionSidecar
    session_id: str
    store_id: str

    def _node(
        self,
        kind: str,
        *,
        entity: str,
        input_root: LogicalRootHandle | None = None,
        baseline_root: LogicalRootHandle | None = None,
        parameters: tuple[str | int | float | None | tuple[str, ...], ...] = (),
        dependency: str = "",
        requirements: tuple[str, ...] = (),
    ) -> LogicalRootHandle:
        shape = _make_shape_id("dsl_j1", kind, 1, ids=_IDS)
        inputs: tuple[DefinitionInput, ...] = (
            (
                DefinitionInput(
                    "input", LogicalInputToken(input_root.definition_fingerprint), input_root
                ),
            )
            if input_root is not None
            else ()
        )
        if baseline_root is not None:
            if input_root is None:
                raise _reject("current input", "missing", repair="Bind both comparison endpoints.")
            inputs = (
                DefinitionInput(
                    "left" if kind == "correlate" else "current",
                    LogicalInputToken(input_root.definition_fingerprint),
                    input_root,
                ),
                DefinitionInput(
                    "right" if kind == "correlate" else "baseline",
                    LogicalInputToken(baseline_root.definition_fingerprint),
                    baseline_root,
                ),
            )
        row, rows = _j1_contracts(self, kind, entity, input_root, parameters)
        return _make_logical_root(
            session_id=self.session_id,
            store_id=self.store_id,
            shape_id=shape,
            row_contract_fingerprint=_row_contract_fingerprint(row),
            row_set_contract_fingerprint=_row_set_contract_fingerprint(rows),
            operator_id=f"dsl.j1.{kind}",
            inputs=inputs,
            parameters=parameters,
            dependency_facts=(dependency,) if dependency else (),
            contract_versions=(("dsl.j1", "v1"),),
            requirements=requirements,
        )

    def members(self, entity: Ref[EntityKind]) -> J1Members:
        if type(entity) is not Ref or entity.kind is not SemanticKind.ENTITY:
            raise _reject("Entity Ref", type(entity).__name__, repair="Pass ms.ref.entity(...).")
        contract = normalize_target_entity(self.registry, entity.path)
        if contract.version is not None or len(contract.identity_signature) != 1:
            raise _reject(
                "non-versioned, single-column Entity identity",
                entity.path,
                repair="Use a first-round Entity with one declared identity column.",
            )
        identity = _make_field_id("member." + entity.path + "." + contract.primary_key[0])
        domain = _entity_domain(entity, identity)
        root = self._node(
            "members",
            entity=entity.path,
            parameters=(entity.path, contract.dependency_fingerprint),
            dependency="entity:" + entity.path,
        )
        return J1Members(self, entity, domain, root)


@dataclass(frozen=True, slots=True)
class J3Route:
    root: Ref[EntityKind]
    through: tuple[Ref[RelationshipKind], ...]


@dataclass(frozen=True, slots=True)
class J3Routes:
    routes: tuple[J3Route, ...]


def j3_route(root: Ref[EntityKind], *, through: tuple[Ref[RelationshipKind], ...]) -> J3Route:
    """Bind one private contribution root to an ordered governed path."""
    if (
        type(root) is not Ref
        or root.kind is not SemanticKind.ENTITY
        or type(through) is not tuple
        or any(
            type(item) is not Ref or item.kind is not SemanticKind.RELATIONSHIP for item in through
        )
    ):
        raise _reject(
            "Entity and Relationship refs",
            "invalid route",
            repair="Bind a declared contribution root and path.",
        )
    return J3Route(root, through)


def j3_routes(*routes: J3Route) -> J3Routes:
    """Bind each private ratio contribution root exactly once."""
    if (
        len(routes) != 2
        or any(type(item) is not J3Route for item in routes)
        or len({item.root.path for item in routes}) != 2
    ):
        raise _reject(
            "two distinct component routes",
            "invalid routes",
            repair="Name each component root exactly once.",
        )
    return J3Routes(tuple(routes))


def _member_dimension(members: J1Members, dimension: Ref[DimensionKind]) -> TargetDimensionContract:
    if type(dimension) is not Ref or dimension.kind is not SemanticKind.DIMENSION:
        raise _reject(
            "Dimension Ref", type(dimension).__name__, repair="Pass ms.ref.dimension(...)."
        )
    bound = normalize_target_dimension(members.context.registry, dimension.path)
    if bound.entity_ref.path != members.entity.path:
        raise _reject(
            "single-valued Dimension on the member Entity",
            dimension.path,
            repair="Read or group by an attribute owned by this Entity; contribution coordinates belong to observe().",
        )
    return bound


@dataclass(frozen=True, slots=True, eq=False)
class J1Members:
    context: J1Context
    entity: Ref[EntityKind]
    domain: AnalysisDomain
    root: LogicalRootHandle

    def read(self, dimension: Ref[DimensionKind]) -> J1Read:
        bound = _member_dimension(self, dimension)
        root = self.context._node(
            "read",
            entity=self.entity.path,
            input_root=self.root,
            parameters=(dimension.path, bound.source_column),
        )
        return J1Read(self, bound, root)

    def group_by(self, dimension: Ref[DimensionKind]) -> J1Group:
        bound = _member_dimension(self, dimension)
        field = _make_field_id("dimension." + dimension.path)
        root = self.context._node(
            "group",
            entity=self.entity.path,
            input_root=self.root,
            parameters=(dimension.path, bound.source_column),
        )
        return J1Group(self, bound, _group_domain(self.domain, (field,)), root)

    @overload
    def observe(
        self,
        metric: Ref[MetricKind],
        *,
        during: TimeScope,
        via: Ref[RelationshipKind],
        coordinates: tuple[Ref[DimensionKind], ...] = (),
    ) -> J1Observed: ...

    @overload
    def observe(
        self,
        metric: Ref[MetricKind],
        *,
        during: TimeScope,
        via: J3Routes,
        coordinates: tuple[Ref[DimensionKind], ...] = (),
    ) -> J3Observed: ...

    def observe(
        self,
        metric: Ref[MetricKind],
        *,
        during: TimeScope,
        via: Ref[RelationshipKind] | J3Routes,
        coordinates: tuple[Ref[DimensionKind], ...] = (),
    ) -> J1Observed | J3Observed:
        if isinstance(via, J3Routes):
            return _observe_ratio(self, metric, during, via, coordinates)
        return _observe(self, self.domain, self.root, metric, during, via, coordinates)


@dataclass(frozen=True, slots=True, eq=False)
class J1CategoryField:
    root: LogicalRootHandle
    dimension: TargetDimensionContract

    def eq(self, value: str) -> J1Predicate:
        if type(value) is not str:
            raise _reject(
                "string category value",
                type(value).__name__,
                repair="Use a declared category value.",
            )
        return J1Predicate(self.root, self.dimension, value)


@dataclass(frozen=True, slots=True, eq=False)
class J1Predicate:
    root: LogicalRootHandle
    dimension: TargetDimensionContract
    value: str


@dataclass(frozen=True, slots=True, eq=False)
class J1Read:
    members_input: J1Members
    dimension: TargetDimensionContract
    root: LogicalRootHandle

    @property
    def value(self) -> J1CategoryField:
        return J1CategoryField(self.root, self.dimension)

    def where(self, predicate: J1Predicate) -> J1SelectedCategory:
        if type(predicate) is not J1Predicate or predicate.root is not self.root:
            raise _reject(
                "predicate on this exact read node",
                "foreign predicate",
                repair="Build the predicate from this read.value handle.",
            )
        root = self.members_input.context._node(
            "where",
            entity=self.members_input.entity.path,
            input_root=self.root,
            parameters=(self.dimension.ref.path, predicate.value),
            requirements=("strict_category_cell@v1",),
        )
        return J1SelectedCategory(self.members_input, root)

    def group_by(self) -> J1Group:
        field = _make_field_id("dimension." + self.dimension.ref.path)
        root = self.members_input.context._node(
            "group",
            entity=self.members_input.entity.path,
            input_root=self.root,
            parameters=(self.dimension.ref.path, self.dimension.source_column),
        )
        return J1Group(
            self.members_input,
            self.dimension,
            _group_domain(self.members_input.domain, (field,)),
            root,
        )


@dataclass(frozen=True, slots=True, eq=False)
class J1SelectedCategory:
    members_input: J1Members
    root: LogicalRootHandle

    def members(self) -> J1Members:
        original = self.members_input
        return J1Members(
            original.context,
            original.entity,
            _selected_entity_domain(original.domain, self.root.definition_fingerprint),
            self.root,
        )


@dataclass(frozen=True, slots=True, eq=False)
class J1Group:
    members_input: J1Members
    dimension: TargetDimensionContract
    domain: AnalysisDomain
    root: LogicalRootHandle

    def observe(
        self,
        metric: Ref[MetricKind],
        *,
        during: TimeScope,
        via: Ref[RelationshipKind],
    ) -> J1Observed:
        return _observe(self.members_input, self.domain, self.root, metric, during, via, ())


@dataclass(frozen=True, slots=True, eq=False)
class J1Observed:
    context: J1Context
    entity: Ref[EntityKind]
    domain: AnalysisDomain
    quantity: QuantityState
    root: LogicalRootHandle
    metric: Ref[MetricKind]
    plan: MetricComponentPlan
    coordinates: tuple[Ref[DimensionKind], ...]

    def correlate(
        self, other: J1Observed, *, method: Literal["spearman"] = "spearman"
    ) -> J4Association:
        left_facts = self.root.parameters
        right_facts = other.root.parameters if isinstance(other, J1Observed) else ()
        if (
            not isinstance(other, J1Observed)
            or method != "spearman"
            or self.context is not other.context
            or self.domain.kind != "entity"
            or self.domain != other.domain
            or self.coordinates
            or other.coordinates
            or self.metric == other.metric
            or type(left_facts) is not tuple
            or type(right_facts) is not tuple
            or len(left_facts) != 5
            or len(right_facts) != 5
            or not isinstance(left_facts[3], str)
            or left_facts[3] != right_facts[3]
            or self.root.inputs[0].root is not other.root.inputs[0].root
        ):
            raise _reject(
                "two distinct Numeric Metrics on one explicit Entity member realization and time scope",
                "incompatible Spearman endpoints",
                repair="Observe two different Metrics from the same members object and time scope.",
            )
        root = self.context._node(
            "correlate",
            entity=self.entity.path,
            input_root=self.root,
            baseline_root=other.root,
            parameters=(self.metric.path, other.metric.path, method, left_facts[3]),
            requirements=("complete_pairing@v1", "spearman_pairs@v1"),
        )
        return J4Association(self.context, root, self, other)

    def compare(self, baseline: J1Observed) -> J1Difference:
        if not isinstance(baseline, J1Observed):
            raise _reject(
                "Observed baseline", type(baseline).__name__, repair="Compare two observations."
            )
        current_facts = self.root.parameters
        baseline_facts = baseline.root.parameters
        if (
            self.context is not baseline.context
            or self.domain.kind != "entity"
            or self.domain != baseline.domain
            or self.plan.method != "sum"
            or baseline.plan.method != "sum"
            or self.metric != baseline.metric
            or self.plan != baseline.plan
            or self.coordinates != baseline.coordinates
            or type(current_facts) is not tuple
            or type(baseline_facts) is not tuple
            or len(current_facts) != 5
            or len(baseline_facts) != 5
            or current_facts[:3] != baseline_facts[:3]
            or current_facts[3] == baseline_facts[3]
            or current_facts[4] != baseline_facts[4]
            or self.root.inputs[0].root is not baseline.root.inputs[0].root
        ):
            raise _reject(
                "two distinct time-scoped observations of one Metric and one explicit member node",
                "incompatible comparison endpoints",
                repair="Observe both periods from the same members object with matching Metric, route and coordinates.",
            )
        root = self.context._node(
            "compare",
            entity=self.entity.path,
            input_root=self.root,
            baseline_root=baseline.root,
            parameters=("time_change", "exact_keys", "difference"),
            requirements=tuple(f"{check}@v1" for check in J1_COMPARE_CHECKS),
        )
        quantity = _difference_quantity(
            self.domain,
            self.root.definition_fingerprint,
            baseline.root.definition_fingerprint,
            "metric:" + self.metric.path,
            J1_COMPARE_PARTS,
        )
        return J1Difference(self.context, self.entity, self.domain, quantity, root, self, baseline)

    def group_by(self, dimension: Ref[DimensionKind]) -> J1Observed:
        if type(dimension) is not Ref or dimension not in self.coordinates:
            raise _reject(
                "an observed contribution coordinate",
                repr(dimension),
                repair="Name this Dimension in observe(coordinates=(...,)) first.",
            )
        field = _make_field_id("contribution." + dimension.path)
        domain = _group_domain(self.domain, (field,))
        root = self.context._node(
            "group",
            entity=self.entity.path,
            input_root=self.root,
            parameters=(dimension.path, "contribution"),
        )
        quantity = _observed_quantity(
            domain, "metric:" + self.metric.path, (field,), self.plan.required_parts
        )
        return J1Observed(
            self.context,
            self.entity,
            domain,
            quantity,
            root,
            self.metric,
            self.plan,
            self.coordinates,
        )

    def rollup(self) -> J1Observed:
        if self.plan.method != "sum" or self.plan.required_parts != J1_SUM_PARTS:
            raise _reject(
                "sum observation with original retained state",
                self.plan.method,
                repair="Observe a builder-backed sum with complete state.",
            )
        domain = self.domain if self.domain.kind == "group" else _singleton_domain(self.domain)
        root = self.context._node(
            "rollup",
            entity=self.entity.path,
            input_root=self.root,
            parameters=(self.metric.path, self.plan.required_parts),
            requirements=("contribution_partition@v1", "complete_coverage@v1"),
        )
        quantity = _observed_quantity(
            domain, "metric:" + self.metric.path, (), self.plan.required_parts
        )
        return J1Observed(
            self.context, self.entity, domain, quantity, root, self.metric, self.plan, ()
        )

    def summarize(self, method: Literal["sum", "count", "mean"]) -> J1Statistic:
        """Construct a new current-row statistic, distinct from Metric state."""
        if self.plan.method != "sum":
            raise _reject(
                "sum observation with admitted current-row continuation",
                self.plan.method,
                repair="Use the count observation only as a private Spearman input.",
            )
        return _summarize_numeric(self.context, self.entity, self.domain, self.root, method)


@dataclass(frozen=True, slots=True, eq=False)
class J3Observed:
    context: J1Context
    entity: Ref[EntityKind]
    domain: AnalysisDomain
    quantity: QuantityState
    root: LogicalRootHandle
    metric: Ref[MetricKind]
    plan: MetricComponentPlan
    coordinates: tuple[Ref[DimensionKind], ...]

    def group_by(self, dimension: Ref[DimensionKind]) -> J3Grouped:
        if type(dimension) is not Ref or dimension not in self.coordinates:
            raise _reject(
                "one retained ratio coordinate",
                repr(dimension),
                repair="Group by a coordinate declared by observe().",
            )
        return J3Grouped(self, dimension)

    def rollup(self) -> J3Observed:
        if self.root.operator_id != "dsl.j1.ratio_observe":
            raise _reject(
                "original ratio observation",
                self.root.operator_id,
                repair="Roll up the retained original component state once.",
            )
        domain = _singleton_domain(self.domain)
        root = self.context._node(
            "ratio_rollup",
            entity=self.entity.path,
            input_root=self.root,
            parameters=(self.metric.path, "singleton", None),
            requirements=tuple(f"{check}@v1" for check in J3_RATIO_CHECKS),
        )
        quantity = _observed_quantity(
            domain, "metric:" + self.metric.path, (), self.plan.required_parts
        )
        return J3Observed(
            self.context, self.entity, domain, quantity, root, self.metric, self.plan, ()
        )

    def summarize(self, method: Literal["sum", "count", "mean"]) -> J1Statistic:
        return _summarize_numeric(self.context, self.entity, self.domain, self.root, method)


@dataclass(frozen=True, slots=True, eq=False)
class J3Grouped:
    observed: J3Observed
    dimension: Ref[DimensionKind]

    def rollup(self) -> J3Observed:
        observed = self.observed
        domain = _group_domain(
            observed.domain, (_make_field_id("dimension." + self.dimension.path),)
        )
        root = observed.context._node(
            "ratio_rollup",
            entity=observed.entity.path,
            input_root=observed.root,
            parameters=(observed.metric.path, "group", self.dimension.path),
            requirements=tuple(f"{check}@v1" for check in J3_RATIO_CHECKS),
        )
        quantity = _observed_quantity(
            domain, "metric:" + observed.metric.path, (), observed.plan.required_parts
        )
        return J3Observed(
            observed.context,
            observed.entity,
            domain,
            quantity,
            root,
            observed.metric,
            observed.plan,
            (),
        )


def _summarize_numeric(
    context: J1Context,
    entity: Ref[EntityKind],
    domain: AnalysisDomain,
    input_root: LogicalRootHandle,
    method: Literal["sum", "count", "mean"],
) -> J1Statistic:
    if method not in ("sum", "count", "mean"):
        raise _reject(
            "current-row sum, count or mean",
            method,
            repair="Choose one registered current-row method.",
        )
    parts = {"sum": ("sum",), "count": ("count",), "mean": ("sum", "count")}[method]
    target = _singleton_domain(domain)
    quantity = _row_statistic_quantity(
        domain, target, input_root.definition_fingerprint, method, parts
    )
    root = context._node(
        "summarize",
        entity=entity.path,
        input_root=input_root,
        parameters=(method, input_root.definition_fingerprint),
        requirements=("strict_current_row_cell@v1",) if method != "count" else (),
    )
    return J1Statistic(context, target, quantity, root, method)


@dataclass(frozen=True, slots=True, eq=False)
class J1Statistic:
    context: J1Context
    domain: AnalysisDomain
    quantity: QuantityState
    root: LogicalRootHandle
    method: str


@dataclass(frozen=True, slots=True, eq=False)
class J1Difference:
    context: J1Context
    entity: Ref[EntityKind]
    domain: AnalysisDomain
    quantity: QuantityState
    root: LogicalRootHandle
    current: J1Observed
    baseline: J1Observed

    @property
    def value(self) -> J1NumericField:
        return J1NumericField(self.root)

    def where(self, predicate: J1NumericPredicate) -> J1SelectedDifference:
        return _select_difference(self, predicate)

    def summarize(self, method: Literal["sum", "count", "mean"]) -> J1Statistic:
        return _summarize_numeric(self.context, self.entity, self.domain, self.root, method)


@dataclass(frozen=True, slots=True, eq=False)
class J4Association:
    context: J1Context
    root: LogicalRootHandle
    left: J1Observed
    right: J1Observed

    @property
    def coefficient(self) -> J4Coefficient:
        return J4Coefficient(self)


@dataclass(frozen=True, slots=True, eq=False)
class J4Coefficient:
    association: J4Association

    @property
    def value(self) -> J1NumericField:
        return J1NumericField(self.association.root)

    def where(self, predicate: J1NumericPredicate) -> J4CoefficientSelection:
        association = self.association
        if predicate.root is not association.root:
            raise _reject(
                "predicate on this coefficient",
                "foreign predicate",
                repair="Build the predicate from this coefficient view.",
            )
        root = association.context._node(
            "correlate_where",
            entity=association.left.entity.path,
            input_root=association.root,
            parameters=(predicate.operation, predicate.threshold),
            requirements=("complete_pairing@v1", "spearman_pairs@v1"),
        )
        return J4CoefficientSelection(association, root)

    def summarize(self, method: Literal["sum", "count", "mean"]) -> J4CoefficientStatistic:
        return _j4_statistic(self.association, self.association.root, method)


@dataclass(frozen=True, slots=True, eq=False)
class J4CoefficientSelection:
    association: J4Association
    root: LogicalRootHandle

    def summarize(self, method: Literal["sum", "count", "mean"]) -> J4CoefficientStatistic:
        return _j4_statistic(self, self.root, method)


@dataclass(frozen=True, slots=True, eq=False)
class J4CoefficientStatistic:
    predecessor: J4Association | J4CoefficientSelection
    root: LogicalRootHandle


def _j4_statistic(
    predecessor: J4Association | J4CoefficientSelection,
    input_root: LogicalRootHandle,
    method: Literal["sum", "count", "mean"],
) -> J4CoefficientStatistic:
    if method not in ("sum", "count", "mean"):
        raise _reject(
            "sum, count or mean", str(method), repair="Choose a registered current row statistic."
        )
    association = (
        predecessor.association if isinstance(predecessor, J4CoefficientSelection) else predecessor
    )
    root = association.context._node(
        "correlate_summarize",
        entity=association.left.entity.path,
        input_root=input_root,
        parameters=(method,),
    )
    return J4CoefficientStatistic(predecessor, root)


@dataclass(frozen=True, slots=True, eq=False)
class J1NumericPredicate:
    root: LogicalRootHandle
    operation: Literal["lt", "lte", "gt", "gte", "eq"]
    threshold: int | float


@dataclass(frozen=True, slots=True, eq=False)
class J1NumericField:
    root: LogicalRootHandle

    def _predicate(
        self, operation: Literal["lt", "lte", "gt", "gte", "eq"], threshold: int | float
    ) -> J1NumericPredicate:
        if (type(threshold) is int and -(2**63) <= threshold < 2**63) or (
            type(threshold) is float and math.isfinite(threshold)
        ):
            return J1NumericPredicate(self.root, operation, threshold)
        raise _reject(
            "finite int64 or float64 threshold",
            type(threshold).__name__,
            repair="Use a finite numeric threshold of the observed unit.",
        )

    def lt(self, threshold: int | float) -> J1NumericPredicate:
        return self._predicate("lt", threshold)

    def lte(self, threshold: int | float) -> J1NumericPredicate:
        return self._predicate("lte", threshold)

    def gt(self, threshold: int | float) -> J1NumericPredicate:
        return self._predicate("gt", threshold)

    def gte(self, threshold: int | float) -> J1NumericPredicate:
        return self._predicate("gte", threshold)

    def eq(self, threshold: int | float) -> J1NumericPredicate:
        return self._predicate("eq", threshold)


@dataclass(frozen=True, slots=True, eq=False)
class J1SelectedDifference:
    context: J1Context
    entity: Ref[EntityKind]
    domain: AnalysisDomain
    quantity: QuantityState
    root: LogicalRootHandle
    current: J1Observed
    baseline: J1Observed

    def members(self) -> J1Members:
        root = self.context._node(
            "members",
            entity=self.entity.path,
            input_root=self.root,
            parameters=(self.entity.path, self.root.definition_fingerprint),
        )
        return J1Members(self.context, self.entity, self.domain, root)

    def summarize(self, method: Literal["sum", "count", "mean"]) -> J1Statistic:
        return _summarize_numeric(self.context, self.entity, self.domain, self.root, method)


def _select_difference(
    relation: J1Difference, predicate: J1NumericPredicate
) -> J1SelectedDifference:
    if type(predicate) is not J1NumericPredicate or predicate.root is not relation.root:
        raise _reject(
            "predicate on this exact Difference node",
            "foreign predicate",
            repair="Build the predicate from this relation.value handle.",
        )
    root = relation.context._node(
        "where",
        entity=relation.entity.path,
        input_root=relation.root,
        parameters=("numeric", predicate.operation, predicate.threshold),
        requirements=tuple(f"{check}@v1" for check in J1_COMPARE_CHECKS),
    )
    domain = _selected_entity_domain(relation.domain, root.definition_fingerprint)
    quantity = _difference_quantity(
        domain,
        relation.current.root.definition_fingerprint,
        relation.baseline.root.definition_fingerprint,
        "metric:" + relation.current.metric.path,
        J1_COMPARE_PARTS,
    )
    return J1SelectedDifference(
        relation.context,
        relation.entity,
        domain,
        quantity,
        root,
        relation.current,
        relation.baseline,
    )


def _observe(
    members: J1Members,
    domain: AnalysisDomain,
    input_root: LogicalRootHandle,
    metric: Ref[MetricKind],
    during: TimeScope,
    via: Ref[RelationshipKind],
    coordinates: tuple[Ref[DimensionKind], ...],
) -> J1Observed:
    if type(metric) is not Ref or metric.kind is not SemanticKind.METRIC:
        raise _reject("Metric Ref", type(metric).__name__, repair="Pass ms.ref.metric(...).")
    if not isinstance(during, TimeScope):
        raise _reject(
            "fixed TimeScope",
            type(during).__name__,
            repair="Use mv.time_scope(start=..., end=...).",
        )
    if type(via) is not Ref or via.kind is not SemanticKind.RELATIONSHIP:
        raise _reject(
            "Relationship Ref", type(via).__name__, repair="Pass ms.ref.relationship(...)."
        )
    relation = members.context.registry.relationships.get(via.path)
    if relation is None or relation.to_entity != members.entity.path:
        raise _reject(
            "declared contribution-to-member Relationship",
            via.path,
            repair="Choose the exact directed Relationship from the Metric root to this Entity.",
        )
    declaration = members.context.registry.metrics.get(metric.path)
    if declaration is None or declaration.aggregation not in ("sum", "count"):
        raise _reject(
            "builder-backed sum or count with retained component state",
            metric.path,
            repair="Declare this Metric with ms.aggregate(..., agg='sum') or ms.count(...).",
        )
    normalized = normalize_target_metric(
        members.context.registry, metric.path, sidecar=members.context.sidecar
    )
    plan = derive_metric_components(normalized)
    if (
        plan.method not in ("sum", "count")
        or plan.required_parts != (J1_SUM_PARTS if plan.method == "sum" else J1_COUNT_PARTS)
        or len(normalized.computation_roots) != 1
    ):
        raise _reject(
            "builder-backed single-root sum or count",
            plan.method,
            repair="Use an admitted J1 Metric.",
        )
    root_entity = normalized.computation_roots[0].path
    if relation.from_entity != root_entity or not path_is_functional(
        members.context.registry, root_entity, (via.path,)
    ):
        raise _reject(
            "single-valued declared path from contribution to member",
            via.path,
            repair="Use a many-to-one Relationship with declared compatible keys.",
        )
    if (
        normalized.event_time_dimension is None
        or normalized.event_time_dimension.path not in members.context.registry.dimensions
    ):
        raise _reject(
            "sum builder with declared event time",
            metric.path,
            repair="Declare ms.aggregate(..., agg='sum', time=EventTime).",
        )
    if (
        type(normalized.authoring_additivity) is not AdditiveAllV1
        or normalized.authoring_additivity.exceptions
    ):
        raise _reject(
            "versioned additive-all Metric support",
            metric.path,
            repair="Declare the supported contribution additivity on the Measure.",
        )
    if (
        plan.method == "sum"
        and normalized.null_policy is not None
        and normalized.null_policy.kind != "ignore"
    ):
        raise _reject(
            "authored ignore-Null input policy",
            normalized.null_policy.kind,
            repair="Declare nulls=ms.nulls.ignore() for this J1 sum.",
        )
    if (
        plan.method == "sum"
        and normalized.empty_policy is not None
        and normalized.empty_policy.kind != "null"
    ):
        raise _reject(
            "authored empty-Null contribution policy",
            normalized.empty_policy.kind,
            repair="Declare empty=ms.empty.null() for this J1 sum.",
        )
    if plan.method == "sum" and (
        plan.null_rule != "ignore_null_inputs" or plan.empty_rule != "null"
    ):
        raise _reject(
            "sum graph compatible with ignore-Null and empty-Null policy",
            metric.path,
            repair="Use a sum graph whose structural value rules match the declared J1 policy.",
        )
    if plan.method == "count" and plan.empty_rule != "zero":
        raise _reject(
            "count graph with valid empty-zero policy",
            metric.path,
            repair="Use an admitted count Metric with explicit zero empty contribution.",
        )
    if plan.method == "count" and (
        domain.kind != "entity" or input_root is not members.root or coordinates
    ):
        raise _reject(
            "Entity-level count observation for the private Spearman pair",
            "unsupported count observation shape",
            repair="Observe the count Metric on the ungrouped members without coordinates.",
        )
    if type(coordinates) is not tuple or len(set(coordinates)) != len(coordinates):
        raise _reject(
            "unique coordinate refs",
            "invalid coordinates",
            repair="Pass a tuple of unique Dimensions.",
        )
    coordinate_fields: list[DatasetFieldId] = []
    for coordinate in coordinates:
        if type(coordinate) is not Ref or coordinate.kind is not SemanticKind.DIMENSION:
            raise _reject(
                "Dimension coordinate",
                type(coordinate).__name__,
                repair="Use a categorical Dimension Ref.",
            )
        bound = normalize_target_dimension(members.context.registry, coordinate.path)
        if bound.entity_ref.path != root_entity:
            raise _reject(
                "coordinate on the contribution root",
                coordinate.path,
                repair="Choose a Dimension on the Metric computation root.",
            )
        coordinate_fields.append(_make_field_id("contribution." + coordinate.path))
    quantity = _observed_quantity(
        domain, "metric:" + metric.path, tuple(coordinate_fields), plan.required_parts
    )
    root = members.context._node(
        "observe",
        entity=members.entity.path,
        input_root=input_root,
        parameters=(
            metric.path,
            normalized.dependency_fingerprint,
            via.path,
            during.model_dump_json(),
            tuple(item.path for item in coordinates),
        ),
        dependency="metric:" + metric.path,
        requirements=(
            "complete_coverage@v1",
            "contribution_partition@v1",
            *(("count_observation@v1",) if plan.method == "count" else ()),
        ),
    )
    return J1Observed(
        members.context, members.entity, domain, quantity, root, metric, plan, coordinates
    )


def _observe_ratio(
    members: J1Members,
    metric: Ref[MetricKind],
    during: TimeScope,
    via: J3Routes,
    coordinates: tuple[Ref[DimensionKind], ...],
) -> J3Observed:
    if (
        type(metric) is not Ref
        or metric.kind is not SemanticKind.METRIC
        or not isinstance(during, TimeScope)
    ):
        raise _reject(
            "Metric Ref and fixed TimeScope",
            "invalid ratio observation",
            repair="Use a declared Metric and mv.time_scope().",
        )
    if (
        type(coordinates) is not tuple
        or len(set(coordinates)) != len(coordinates)
        or len(coordinates) > 2
    ):
        raise _reject(
            "up to two unique coordinate refs",
            "invalid coordinates",
            repair="Use distinct declared categorical Dimensions.",
        )
    normalized = normalize_target_metric(
        members.context.registry, metric.path, sidecar=members.context.sidecar
    )
    plan = derive_metric_components(normalized)
    nodes = {record.node_id: record.node for record in normalized.graph.nodes}
    node = nodes.get(normalized.graph.roots[0]) if normalized.graph.roots else None
    if (
        plan.method != "ratio"
        or plan.required_parts != J3_RATIO_PARTS
        or not isinstance(node, RatioNodeV1)
        or node.zero_division != "undefined"
        or len(normalized.components) != 2
        or normalized.logical_type not in ("unknown", "float64")
    ):
        raise _reject(
            "explicit sum/count ratio with a declared zero policy",
            metric.path,
            repair="Declare an admitted ms.ratio over sum and count components.",
        )
    numerator, denominator = normalized.components
    left = component_node(normalized.graph, numerator.node_id)
    right = component_node(normalized.graph, denominator.node_id)
    declaration = members.context.registry.metrics.get(metric.path)
    composition = declaration.composition if declaration is not None else None
    numerator_decl = (
        members.context.registry.metrics.get(composition.numerator)
        if isinstance(composition, RatioComposition)
        else None
    )
    if (
        not isinstance(left, AggregateNodeV1)
        or left.agg != "sum"
        or left.filter
        or not isinstance(right, AggregateNodeV1)
        or right.agg != "count"
        or right.filter
        or numerator_decl is None
        or numerator_decl.null_policy is None
        or numerator_decl.null_policy.kind != "ignore"
        or numerator_decl.empty_policy is None
        or numerator_decl.empty_policy.kind != "zero"
        or numerator.empty_rule != "zero"
        or denominator.empty_rule != "zero"
    ):
        raise _reject(
            "direct-column sum/count and explicit zero-empty numerator",
            metric.path,
            repair="Declare the first-round component policies and direct aggregates.",
        )
    supplied = {item.root.path: item for item in via.routes}
    roots = {component.computation_root.path for component in normalized.components}
    if len(supplied) != 2 or set(supplied) != roots:
        raise _reject(
            "one route for each ratio root",
            repr(tuple(supplied)),
            repair="Bind both exact computation roots.",
        )
    registry = members.context.registry
    for component in normalized.components:
        root_path = component.computation_root.path
        route = supplied[root_path]
        path = tuple(item.path for item in route.through)
        if not path_is_functional(registry, root_path, path):
            raise _reject(
                "functional component path",
                root_path,
                repair="Use declared to-one Relationship steps.",
            )
        visited = [root_path]
        for step in path:
            relationship = registry.relationships.get(step)
            if relationship is None or relationship.from_entity != visited[-1]:
                raise _reject(
                    "continuous forward path",
                    step,
                    repair="Order the component Relationship refs from root to member.",
                )
            visited.append(relationship.to_entity)
        if visited[-1] != members.entity.path:
            raise _reject(
                "route ending at member Entity",
                root_path,
                repair="Complete each path to the selected member Entity.",
            )
        time_ref = component.event_time_dimension
        time_path = tuple(item.path for item in component.event_time_path)
        if (
            time_ref is None
            or path[: len(time_path)] != time_path
            or registry.dimensions[time_ref.path].entity != visited[len(time_path)]
        ):
            raise _reject(
                "declared component time on its route",
                root_path,
                repair="Bind time= and time_via= to this contribution path.",
            )
        for coordinate in coordinates:
            if type(coordinate) is not Ref or coordinate.kind is not SemanticKind.DIMENSION:
                raise _reject(
                    "categorical Dimension Ref",
                    repr(coordinate),
                    repair="Use declared coordinate Dimensions.",
                )
            dimension = normalize_target_dimension(registry, coordinate.path)
            if (
                dimension.logical_type not in ("unknown", "string")
                or dimension.entity_ref.path not in visited
            ):
                raise _reject(
                    "categorical coordinate on each component path",
                    coordinate.path,
                    repair="Choose an admitted coordinate reachable on both component paths.",
                )
    route_payload = tuple(
        (root_path, tuple(item.path for item in supplied[root_path].through))
        for root_path in sorted(supplied)
    )
    coordinate_paths = tuple(item.path for item in coordinates)
    root = members.context._node(
        "ratio_observe",
        entity=members.entity.path,
        input_root=members.root,
        parameters=(
            metric.path,
            normalized.dependency_fingerprint,
            json.dumps(route_payload),
            during.model_dump_json(),
            coordinate_paths,
        ),
        dependency="metric:" + metric.path,
        requirements=tuple(f"{check}@v1" for check in J3_RATIO_CHECKS),
    )
    quantity = _observed_quantity(
        members.domain,
        "metric:" + metric.path,
        tuple(_make_field_id("contribution." + item.path) for item in coordinates),
        plan.required_parts,
    )
    return J3Observed(
        members.context, members.entity, members.domain, quantity, root, metric, plan, coordinates
    )
