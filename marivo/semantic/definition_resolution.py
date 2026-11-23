"""Frozen physical metadata and native Deferred resolution for semantic authoring."""

from __future__ import annotations

from dataclasses import replace as dataclass_replace
from functools import cached_property
from typing import Protocol, TypeAlias, overload

from ibis.common.deferred import Deferred, Resolver

from marivo._definition.deferred import (
    DeclarationResolver,
    NamedResolver,
    RelationshipParameter,
    SemanticInput,
    resolver_of,
    semantic_input,
)
from marivo._definition.types import (
    ExactRef,
    FieldRead,
    SumAll,
    SumAllExcept,
    SumAxes,
    SumNone,
    SumPermission,
)
from marivo._kernel.refs import (
    DimensionKind,
    EntityKind,
    MeasureKind,
    MetricKind,
    Ref,
    RelationshipKind,
    SemanticKind,
    SemanticKindTag,
    TimeDimensionKind,
)
from marivo._kernel.refs import ref as ref_factory
from marivo.semantic._compiled_state import CompilationGraph
from marivo.semantic._definition_identity import scoped_definition_fingerprint
from marivo.semantic.compiled_types import CompiledSemanticState
from marivo.semantic.errors import ErrorKind, SemanticExpressionError
from marivo.semantic.expressions.objects import (
    DefinitionFacts,
    Dimension,
    Measure,
    TimeField,
    _Entity,
    _fail,
    _Relationship,
)
from marivo.semantic.ir import (
    DimensionIR,
    EntityIR,
    RelationshipIR,
    SemiAdditive,
    TargetRelationshipContract,
)
from marivo.semantic.policies import (
    AdditiveAllV1,
    AdditiveOverV1,
    NonAdditiveV1,
)
from marivo.semantic.static_check.registry import Registry
from marivo.semantic.static_check.targets import normalize_target_relationship

Field: TypeAlias = Dimension | TimeField | Measure

ResolvedValue: TypeAlias = _Entity | Field | _Relationship | Deferred

_SUPPORTED_KINDS = (
    SemanticKind.ENTITY,
    SemanticKind.DIMENSION,
    SemanticKind.TIME_DIMENSION,
    SemanticKind.MEASURE,
    SemanticKind.METRIC,
    SemanticKind.RELATIONSHIP,
)


class _Resolver:
    """Definition identity over one compiled or declared semantic state."""

    def __init__(self, state: CompiledSemanticState | CompilationGraph) -> None:
        self._state = state
        self._registry: Registry = state.registry
        self._versions: dict[Ref[SemanticKindTag], ExactRef] = {}

    def exact(self, target: Ref[SemanticKindTag]) -> ExactRef:

        cached = self._versions.get(target)
        if cached is None:
            if isinstance(self._state, CompiledSemanticState):
                version = self._state._definition_version(target)
            else:
                version = scoped_definition_fingerprint(
                    root=target,
                    definitions=self._state.definitions,
                    dependencies=self._state.dependencies,
                    sidecar=self._state.sidecar,
                )
            cached = self._versions[target] = ExactRef(target, version)
        return cached

    def entity_ref(self, path: str) -> ExactRef:
        return self.exact(ref_factory.entity(path))

    def default_time(self, entity_path: str) -> ExactRef | None:
        path = self._registry.entities[entity_path].default_time
        return self.exact(ref_factory.time_dimension(path)) if path is not None else None

    def coordinate(self, path: str) -> ExactRef:
        field = self._registry.dimensions.get(path)
        if field is None:
            _fail(
                ErrorKind.INVALID_EXPRESSION_VALUE,
                "The expression references an undeclared coordinate.",
                expected="an existing Dimension or TimeField declared before its consumer",
                received=path,
                action="Declare the coordinate first; available coordinates: "
                + ", ".join(sorted(self._registry.dimensions)[:5]),
            )
        if field.is_time_dimension:
            return self.exact(ref_factory.time_dimension(path))
        return self.exact(ref_factory.dimension(path))

    @cached_property
    def _dimension_paths_by_entity(self) -> dict[str, list[str]]:
        paths: dict[str, list[str]] = {}
        for path, dimension in sorted(self._registry.dimensions.items()):
            paths.setdefault(dimension.entity, []).append(path)
        return paths

    def facts(self, target: Ref[SemanticKindTag]) -> DefinitionFacts:
        """Declared metadata and physical binding facts; never part of a computation."""
        definition = self._state.definitions[target]
        details: list[tuple[str, str]] = []
        body = self._state.sidecar.bodies.get(target)
        if body is not None and body.source_column is not None:
            details.append(("column", body.source_column))
        elif body is not None and body.description.display is not None:
            details.append(("expression", body.description.display.text))
        if isinstance(definition, EntityIR):
            details.append(("definition_form", "expression" if body is not None else "direct"))
            details.append(("source", str(definition.source.to_dict())[:160]))
            if definition.versioning is not None:
                details.append(("versioning", repr(definition.versioning)))
            if definition.default_time is not None:
                details.append(("default_time", f"time_dimension:{definition.default_time}"))
        elif isinstance(definition, DimensionIR):
            if definition.sample_interval is not None:
                details.append(("sample_interval", definition.sample_interval.to_token()))
            if definition.parse is not None:
                details.append(("parse", repr(definition.parse)))
        elif isinstance(definition, RelationshipIR):
            details.append(
                ("join_keys", ", ".join(f"{key.from_key}={key.to_key}" for key in definition.keys))
            )
        location = getattr(definition, "location", None)
        return DefinitionFacts(
            name=getattr(definition, "name", target.name),
            ai_context=getattr(definition, "ai_context", None),
            location=f"{location.file}:{location.line}" if location is not None else "<unknown>",
            reads=self._state.dependencies.get(target, ()),
            details=tuple(details),
        )

    def entity(self, path: str) -> _Entity:
        target = ref_factory.entity(path)
        return _Entity(
            _exact_ref=self.entity_ref(path),
            primary_key=self._registry.entities[path].primary_key,
            _facts=self.facts(target),
            _default_time=self.default_time(path),
        )

    def permission(self, path: str) -> SumPermission:
        measure = self._registry.measures[path]
        policy = measure.dsl_additivity

        def native_axis(path: str) -> ExactRef:
            coordinate = self.coordinate(path)
            owner = self._registry.dimensions[path].entity
            if owner != measure.entity:
                _fail(
                    ErrorKind.INVALID_EXPRESSION_VALUE,
                    "Measure sum permission references another contribution root.",
                    expected=f"coordinates on {measure.entity}",
                    received=f"{path} on {owner}",
                    action="Choose an existing coordinate on the Measure's Entity; do not infer a cross-Entity permission.",
                )
            return coordinate

        if isinstance(policy, AdditiveOverV1):
            return SumAxes(tuple(native_axis(axis) for axis in policy.coordinates))
        if isinstance(policy, AdditiveAllV1):
            if not policy.exceptions:
                return SumAll()
            return SumAllExcept(tuple(native_axis(axis) for axis in policy.exceptions))
        if isinstance(policy, NonAdditiveV1):
            return SumNone()
        additivity = measure.additivity
        if isinstance(additivity, SemiAdditive):
            return SumAllExcept((native_axis(additivity.over),))
        return SumAll() if additivity == "additive" else SumNone()

    def measure(self, path: str) -> Measure:
        measure = self._registry.measures[path]
        return Measure(
            ref=self.exact(ref_factory.measure(path)),
            root=self.entity_ref(measure.entity),
            unit=measure.unit,
            permission=self.permission(path),
            coordinates=tuple(
                self.coordinate(path)
                for path in self._dimension_paths_by_entity.get(measure.entity, ())
            ),
            _facts=self.facts(ref_factory.measure(path)),
            _versioned=self._registry.entities[measure.entity].versioning is not None,
            _default_time=self.default_time(measure.entity),
        )

    def field(self, path: str) -> Dimension | TimeField:
        field = self._registry.dimensions[path]
        root = self.entity_ref(field.entity)
        ref = self.coordinate(path)
        facts = self.facts(ref.ref)
        versioned = self._registry.entities[field.entity].versioning is not None
        default = self.default_time(field.entity)
        if field.is_time_dimension:
            return TimeField(
                ref=ref, root=root, _facts=facts, _versioned=versioned, _default_time=default
            )
        return Dimension(
            ref=ref, root=root, _facts=facts, _versioned=versioned, _default_time=default
        )

    def metric(self, path: str) -> Deferred:
        """Return a qualified declaration view without changing its captured source."""

        value = self._registry.declared_metrics[path]
        captured = resolver_of(value)
        assert isinstance(captured, DeclarationResolver)
        declaration = dataclass_replace(
            captured.declaration,
            domain=ref_factory.domain(path.rsplit(".", 1)[0]),
            reference=self.exact(ref_factory.metric(path)),
        )
        return Deferred(DeclarationResolver(captured.recipe, declaration))

    def value(self, target: Ref[SemanticKindTag]) -> ResolvedValue:
        """The expression value of one defined ref, dispatched by kind."""
        path = target.path
        match target.kind:
            case SemanticKind.ENTITY:
                return self.entity(path)
            case SemanticKind.RELATIONSHIP:
                return self.relationship(path)
            case SemanticKind.METRIC:
                return self.metric(path)
            case SemanticKind.MEASURE if path in self._registry.declared_measures:
                return self._registry.declared_measures[path]
            case SemanticKind.MEASURE:
                return self.measure(path)
            case _:
                return self.field(path)

    def relationship(self, path: str) -> _Relationship:
        contract: TargetRelationshipContract = normalize_target_relationship(self._registry, path)
        return _Relationship(
            _parameter=RelationshipParameter(
                ref=self.exact(ref_factory.relationship(path)),
                from_entity=self.entity_ref(contract.from_entity_ref.path),
                to_entity=self.entity_ref(contract.to_entity_ref.path),
                cardinality=contract.cardinality,
                _from_default_time=self.default_time(contract.from_entity_ref.path),
                from_versioned=self._registry.entities[contract.from_entity_ref.path].versioning
                is not None,
                to_versioned=self._registry.entities[contract.to_entity_ref.path].versioning
                is not None,
                target_keys=tuple(self._registry.entities[contract.to_entity_ref.path].primary_key),
                target_keys_covered=set(
                    self._registry.entities[contract.to_entity_ref.path].primary_key
                ).issubset(right for _, right in contract.keys),
            ),
            _facts=self.facts(ref_factory.relationship(path)),
        )


@overload
def resolve(catalog: CompiledCatalog, target: Ref[EntityKind]) -> _Entity: ...


@overload
def resolve(catalog: CompiledCatalog, target: Ref[DimensionKind]) -> Deferred: ...


@overload
def resolve(catalog: CompiledCatalog, target: Ref[TimeDimensionKind]) -> Deferred: ...


@overload
def resolve(catalog: CompiledCatalog, target: Ref[MeasureKind]) -> Deferred: ...


@overload
def resolve(catalog: CompiledCatalog, target: Ref[MetricKind]) -> Deferred: ...


@overload
def resolve(catalog: CompiledCatalog, target: Ref[RelationshipKind]) -> _Relationship: ...


@overload
def resolve(catalog: CompiledCatalog, target: Ref[SemanticKindTag]) -> ResolvedValue: ...


def resolve(catalog: CompiledCatalog, target: Ref[SemanticKindTag]) -> ResolvedValue:
    """Resolve one exact ref in ``catalog`` into its immutable expression value.

    ``catalog`` is a loaded Catalog with one exact-version identity function. Unsupported kinds fail with a structured error;
    absent refs fail through the source's own lookup error. No datasource is read.
    """
    if type(target) is not Ref:
        raise SemanticExpressionError(
            kind=ErrorKind.INVALID_EXPRESSION_VALUE,
            message="Expression resolution requires a semantic Ref lookup key.",
            expected="Ref from ms.ref.entity/dimension/time_dimension/measure/relationship",
            received=type(target).__name__,
            action="Pass a typed ref such as ms.ref.measure('sales.orders.amount').",
        )
    if target.kind not in _SUPPORTED_KINDS:
        raise SemanticExpressionError(
            kind=ErrorKind.INVALID_EXPRESSION_STAGE,
            message=f"Ref {target.key!r} cannot be resolved into an expression value here.",
            expected="entity, dimension, time_dimension, measure, metric, or relationship ref",
            received=target.kind.value,
            action="Resolve a field, Entity, Relationship or published Metric ref.",
            refs=(target.key,),
        )
    state = catalog._state
    if target.kind is SemanticKind.METRIC and target.path not in state.registry.declared_metrics:
        raise SemanticExpressionError(
            kind=ErrorKind.METRIC_NOT_FOUND,
            message=f"Metric {target.key!r} is not published in this scope.",
            expected="a Metric declared at module level in a loaded model",
            received=target.key,
            action="Choose one of: "
            + (", ".join(sorted(state.registry.declared_metrics)[:8]) or "(no published Metrics)"),
            refs=(target.key,),
        )
    if target not in state.definitions:
        candidates = sorted(ref.key for ref in state.definitions if ref.kind is target.kind)[:8]
        raise SemanticExpressionError(
            kind=ErrorKind.NOT_FOUND,
            message=f"Ref {target.key!r} is not defined in this scope.",
            expected=f"a {target.kind.value} defined in this scope",
            received=target.key,
            action="Choose one of: " + (", ".join(candidates) or "(none of this kind)"),
            refs=(target.key,),
        )
    return author_value(_Resolver(state).value(target))


def author_value(value: ResolvedValue) -> ResolvedValue:
    """Expose numerical/condition inputs as native Deferred, preserving metadata internally."""

    if isinstance(value, (Dimension, TimeField, Measure)):
        return semantic_input(
            SemanticInput(
                reference=value.ref,
                root=value.root,
                unit=value.unit if isinstance(value, Measure) else None,
                permission=value.permission if isinstance(value, Measure) else SumNone(),
                coordinates=value.coordinates if isinstance(value, Measure) else (),
                versioned=value._versioned,
                default_time=value._default_time,
                field_read=FieldRead(
                    value.ref,
                    value.root,
                    tuple(step._parameter.ref for step in value.path),
                    value.anchor,
                    path_facts=tuple(step._parameter for step in value.path),
                    versioned=value._versioned,
                    unit=value.unit if isinstance(value, Measure) else None,
                ),
            )
        )
    return value


class CatalogValues:
    """Lazily resolved expression values of one compiled state.

    ``refs`` lists every Entity, field, published Metric and Relationship in
    key order. Values resolve on first access through one shared version cache;
    no datasource is read.
    """

    def __init__(self, state: CompiledSemanticState) -> None:
        self._resolver = _Resolver(state)
        self._values: dict[Ref[SemanticKindTag], ResolvedValue] = {}
        self.refs: tuple[Ref[SemanticKindTag], ...] = tuple(
            sorted(
                (
                    ref
                    for ref in state.definitions
                    if ref.kind in _SUPPORTED_KINDS
                    and (
                        ref.kind is not SemanticKind.METRIC
                        or ref.path in state.registry.declared_metrics
                    )
                ),
                key=lambda item: item.key,
            )
        )

    def get(self, target: Ref[SemanticKindTag]) -> ResolvedValue:
        value = self._values.get(target)
        if value is None:
            value = self._values[target] = self._resolver.value(target)
        return value

    def named_recipe(self, target: Ref[SemanticKindTag]) -> Resolver:
        value = author_value(self.get(target))
        assert isinstance(value, Deferred)
        return NamedResolver(self._resolver.exact(target), resolver_of(value))


class CompiledCatalog(Protocol):
    @property
    def _state(self) -> CompiledSemanticState: ...
