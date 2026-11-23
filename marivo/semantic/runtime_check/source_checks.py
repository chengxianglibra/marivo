"""Public source-check builders and typed check arguments."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal, TypeAlias

from marivo._definition.types import DefinitionGraphError, InputContext, merge_input_contexts
from marivo._kernel.refs import (
    DimensionKind,
    FieldKind,
    Ref,
    RelationshipKind,
    SemanticKind,
    SemanticKindTag,
    TimeDimensionKind,
)
from marivo._kernel.refs import ref as ref_factory
from marivo._kernel.symbolic import (
    DimensionSymbol,
    MeasureSymbol,
    RelationshipSymbol,
    TimeDimensionSymbol,
    native_symbol_ref,
    symbol_factory,
)
from marivo.semantic.catalog_model.objects import (
    Dimension,
    Measure,
    Relationship,
    TimeDimension,
)
from marivo.semantic.errors import ErrorKind, SemanticRuntimeError, _raise, repair

ValidationStatus = Literal["current", "failed", "unavailable", "unknown"]

ValidationCheckKind = Literal[
    "connectivity",
    "schema",
    "not_null",
    "allowed_values",
    "unique",
    "freshness",
    "relationship_matches",
    "relationship_cardinality",
]

SourceScalar: TypeAlias = str | int | float | bool

RelationshipSide = Literal["from", "both"]

RelationshipCardinality = Literal[
    "one_to_one",
    "many_to_one",
    "one_to_many",
    "many_to_many",
]

_FIELD_KINDS = frozenset(
    {SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION, SemanticKind.MEASURE}
)


def _require_ref(
    value: object,
    *,
    parameter: str,
    allowed: frozenset[SemanticKind],
) -> Ref[SemanticKindTag]:

    reference = (
        value._ref
        if isinstance(value, Dimension | TimeDimension | Measure | Relationship)
        else native_symbol_ref(value)
    )
    if reference is None or reference.kind not in allowed:
        expected = " | ".join(sorted(kind.value for kind in allowed))
        _raise(
            ErrorKind.INVALID_REF,
            f"{parameter} requires a native named field/relationship or an explicit authoring selector.",
            cls=SemanticRuntimeError,
            expected=f"a native named object or authoring selector with kind [{expected}]",
            received=type(value).__name__,
            details={"parameter": parameter, "query_executed": False},
            repair_value=repair(
                kind="inspect",
                canonical_id="source_check",
                action="Select the original named field or relationship; anonymous and routed expressions cannot be source checks.",
            ),
        )
    return reference


FieldSymbol: TypeAlias = DimensionSymbol | TimeDimensionSymbol | MeasureSymbol

FieldInput: TypeAlias = "FieldSymbol | Dimension | TimeDimension | Measure"

CategoricalInput: TypeAlias = "DimensionSymbol | TimeDimensionSymbol | Dimension | TimeDimension"

TimeInput: TypeAlias = "TimeDimensionSymbol | TimeDimension"

RelationshipInput: TypeAlias = "RelationshipSymbol | Relationship"


def _source_input_context(value: object) -> InputContext:

    if isinstance(value, Dimension | TimeDimension | Measure | Relationship):
        return InputContext(project_root=value._catalog.workspace_dir.resolve())
    return InputContext()


def _source_contexts(values: Sequence[FieldInput]) -> InputContext:
    try:
        return merge_input_contexts(*(_source_input_context(value) for value in values))
    except DefinitionGraphError as exc:
        _raise(
            ErrorKind.INVALID_REF,
            str(exc),
            cls=SemanticRuntimeError,
            expected="fields from one project",
            received="fields from different projects",
            details={"parameter": "fields", "query_executed": False},
            repair_value=repair(
                kind="inspect",
                canonical_id="source_check",
                action="Select all check fields from the receiving project's Catalog.",
            ),
        )


def _field_key(
    value: FieldInput, *, allowed: frozenset[SemanticKind] = _FIELD_KINDS
) -> Ref[FieldKind]:
    reference = _require_ref(value, parameter="field", allowed=allowed)
    if reference.kind is SemanticKind.DIMENSION:
        return ref_factory.dimension(reference.path)
    if reference.kind is SemanticKind.TIME_DIMENSION:
        return ref_factory.time_dimension(reference.path)
    return ref_factory.measure(reference.path)


def _field_symbol(reference: Ref[FieldKind]) -> FieldSymbol:
    if reference.kind is SemanticKind.DIMENSION:
        return symbol_factory.dimension(reference.path)
    if reference.kind is SemanticKind.TIME_DIMENSION:
        return symbol_factory.time_dimension(reference.path)
    return symbol_factory.measure(reference.path)


@dataclass(frozen=True, slots=True, init=False)
class NotNullSourceCheck:
    _context: InputContext
    _field: Ref[FieldKind]
    kind: Literal["not_null"] = "not_null"

    def __init__(self, field: FieldInput) -> None:
        object.__setattr__(self, "_context", _source_input_context(field))
        object.__setattr__(self, "_field", _field_key(field))
        object.__setattr__(self, "kind", "not_null")

    @property
    def field(self) -> FieldSymbol:
        return _field_symbol(self._field)


@dataclass(frozen=True, slots=True, init=False)
class AllowedValuesSourceCheck:
    _context: InputContext
    _field: Ref[DimensionKind | TimeDimensionKind]
    values: tuple[SourceScalar, ...]
    kind: Literal["allowed_values"] = "allowed_values"

    def __init__(self, field: CategoricalInput, values: tuple[SourceScalar, ...]) -> None:
        reference = _require_ref(
            field,
            parameter="field",
            allowed=frozenset({SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION}),
        )
        key = (
            ref_factory.dimension(reference.path)
            if reference.kind is SemanticKind.DIMENSION
            else ref_factory.time_dimension(reference.path)
        )
        object.__setattr__(self, "_context", _source_input_context(field))
        object.__setattr__(self, "_field", key)
        object.__setattr__(self, "values", values)
        object.__setattr__(self, "kind", "allowed_values")
        if not self.values:
            raise ValueError("allowed_values requires at least one value.")
        if len(set(self.values)) != len(self.values):
            raise ValueError("allowed_values values must be unique.")
        if not all(isinstance(value, str | int | float | bool) for value in self.values):
            raise TypeError("allowed_values values must be str, int, float, or bool.")

    @property
    def field(self) -> DimensionSymbol | TimeDimensionSymbol:
        return (
            symbol_factory.dimension(self._field.path)
            if self._field.kind is SemanticKind.DIMENSION
            else symbol_factory.time_dimension(self._field.path)
        )


@dataclass(frozen=True, slots=True, init=False)
class UniqueSourceCheck:
    _context: InputContext
    _fields: tuple[Ref[FieldKind], ...]
    kind: Literal["unique"] = "unique"

    def __init__(self, fields: tuple[FieldInput, ...]) -> None:
        keys = tuple(_field_key(field) for field in fields)
        if not keys:
            raise ValueError("unique requires at least one field.")
        if len(set(keys)) != len(keys):
            raise ValueError("unique fields must not repeat.")
        object.__setattr__(self, "_context", _source_contexts(fields))
        object.__setattr__(self, "_fields", keys)
        object.__setattr__(self, "kind", "unique")

    @property
    def fields(self) -> tuple[FieldSymbol, ...]:
        return tuple(_field_symbol(reference) for reference in self._fields)


@dataclass(frozen=True, slots=True, init=False)
class FreshnessSourceCheck:
    _context: InputContext
    _field: Ref[TimeDimensionKind]
    max_age: timedelta
    kind: Literal["freshness"] = "freshness"

    def __init__(self, field: TimeInput, max_age: timedelta) -> None:
        reference = _require_ref(
            field, parameter="field", allowed=frozenset({SemanticKind.TIME_DIMENSION})
        )
        object.__setattr__(self, "_context", _source_input_context(field))
        object.__setattr__(self, "_field", ref_factory.time_dimension(reference.path))
        object.__setattr__(self, "max_age", max_age)
        object.__setattr__(self, "kind", "freshness")
        received: object = self.max_age
        if not isinstance(received, timedelta) or received <= timedelta(0):
            raise ValueError("freshness max_age must be a positive timedelta.")

    @property
    def field(self) -> TimeDimensionSymbol:
        return symbol_factory.time_dimension(self._field.path)


@dataclass(frozen=True, slots=True, init=False)
class RelationshipMatchesSourceCheck:
    _context: InputContext
    _relationship: Ref[RelationshipKind]
    side: RelationshipSide
    kind: Literal["relationship_matches"] = "relationship_matches"

    def __init__(self, relationship: RelationshipInput, side: RelationshipSide) -> None:
        reference = _require_ref(
            relationship, parameter="relationship", allowed=frozenset({SemanticKind.RELATIONSHIP})
        )
        object.__setattr__(self, "_context", _source_input_context(relationship))
        object.__setattr__(self, "_relationship", ref_factory.relationship(reference.path))
        object.__setattr__(self, "side", side)
        object.__setattr__(self, "kind", "relationship_matches")
        if self.side not in {"from", "both"}:
            raise ValueError("relationship_matches side must be 'from' or 'both'.")

    @property
    def relationship(self) -> RelationshipSymbol:
        return symbol_factory.relationship(self._relationship.path)


@dataclass(frozen=True, slots=True, init=False)
class RelationshipCardinalitySourceCheck:
    _context: InputContext
    _relationship: Ref[RelationshipKind]
    expected: RelationshipCardinality
    kind: Literal["relationship_cardinality"] = "relationship_cardinality"

    def __init__(self, relationship: RelationshipInput, expected: RelationshipCardinality) -> None:
        reference = _require_ref(
            relationship, parameter="relationship", allowed=frozenset({SemanticKind.RELATIONSHIP})
        )
        object.__setattr__(self, "_context", _source_input_context(relationship))
        object.__setattr__(self, "_relationship", ref_factory.relationship(reference.path))
        object.__setattr__(self, "expected", expected)
        object.__setattr__(self, "kind", "relationship_cardinality")
        if self.expected not in {"one_to_one", "many_to_one", "one_to_many", "many_to_many"}:
            raise ValueError("relationship_cardinality expected value is invalid.")

    @property
    def relationship(self) -> RelationshipSymbol:
        return symbol_factory.relationship(self._relationship.path)


SourceCheck: TypeAlias = (
    NotNullSourceCheck
    | AllowedValuesSourceCheck
    | UniqueSourceCheck
    | FreshnessSourceCheck
    | RelationshipMatchesSourceCheck
    | RelationshipCardinalitySourceCheck
)

FieldSourceCheck: TypeAlias = (
    NotNullSourceCheck | AllowedValuesSourceCheck | UniqueSourceCheck | FreshnessSourceCheck
)


class SourceCheckNamespace:
    """Closed constructors for explicit online validation expectations."""

    def not_null(self, field: FieldInput, /) -> NotNullSourceCheck:
        """Build an explicit no-NULL expectation for one field.

        Args:
            field: Native named field object or an explicit ``ms.ref`` authoring selector.
        Returns:
            A check for ``catalog.validate(..., checks=[...], scope=...)``.
        Example:
            ``ms.source_check.not_null(ms.ref.measure('sales.orders.amount'))``.
        Constraints:
            Anonymous and routed field expressions are rejected. Construction reads no data.
        """
        return NotNullSourceCheck(field=field)

    def allowed_values(
        self,
        field: CategoricalInput,
        /,
        *,
        values: Sequence[SourceScalar],
    ) -> AllowedValuesSourceCheck:
        """Build an explicit allowed-values expectation for a categorical or time field.

        Args:
            field: Native named field object or an explicit ``ms.ref`` authoring selector.
            values: Nonempty sequence of unique str, int, float or bool values.
        Returns:
            A check for ``catalog.validate(..., checks=[...], scope=...)``.
        Example:
            ``ms.source_check.allowed_values(ms.ref.dimension('sales.orders.region'), values=('US', 'EU'))``.
        Constraints:
            Anonymous and routed field expressions are rejected. Construction reads no data.
        """
        return AllowedValuesSourceCheck(field=field, values=tuple(values))

    def unique(self, *, fields: Sequence[FieldInput]) -> UniqueSourceCheck:
        """Build an explicit uniqueness expectation for a field tuple.

        Args:
            fields: Nonempty sequence of distinct native field lookup symbols.
                Use named field objects or explicit ``ms.ref`` selectors.
        Returns:
            A check for ``catalog.validate(..., checks=[...], scope=...)``.
        Example:
            ``ms.source_check.unique(fields=[ms.ref.dimension('sales.orders.order_id')])``.
        Constraints:
            Fields must share one Entity at validation. Anonymous and routed expressions are rejected. Construction reads no data.
        """
        return UniqueSourceCheck(fields=tuple(fields))

    def freshness(
        self,
        field: TimeInput,
        /,
        *,
        max_age: timedelta,
    ) -> FreshnessSourceCheck:
        """Require a time field's maximum value to be within an explicit age.

        Args:
            field: Native TimeDimension object or an explicit ``ms.ref`` authoring selector.
            max_age: Strictly positive ``datetime.timedelta``.
        Returns:
            A check for ``catalog.validate(..., checks=[...], scope=...)``.
        Example:
            ``from datetime import timedelta``;
            ``ms.source_check.freshness(time_field, max_age=timedelta(days=1))``.
        Constraints:
            Anonymous and routed field expressions are rejected. Construction reads no data;
            validation tests only the selected bounded source scope.
        """
        return FreshnessSourceCheck(field=field, max_age=max_age)

    def relationship_matches(
        self,
        relationship: RelationshipInput,
        /,
        *,
        side: RelationshipSide,
    ) -> RelationshipMatchesSourceCheck:
        """Build a bounded check for unmatched relationship keys.

        Args:
            relationship: Native Relationship object or an explicit ``ms.ref`` authoring selector.
            side: ``"from"`` checks source keys for target matches; ``"both"`` also
                checks target keys for source matches in the selected bounded scopes.

        Returns:
            A check consumed by ``catalog.validate(..., checks=..., scope=...)``.

        Example:
            ms.source_check.relationship_matches(
                ms.ref.relationship("sales.orders_to_customers"), side="from"
            )

        Constraints:
            The result describes only the explicitly selected source scopes. It
            neither declares global match completeness nor changes readiness.
        """
        return RelationshipMatchesSourceCheck(relationship=relationship, side=side)

    def relationship_cardinality(
        self,
        relationship: RelationshipInput,
        /,
        *,
        expected: RelationshipCardinality,
    ) -> RelationshipCardinalitySourceCheck:
        """Build an explicit directed relationship multiplicity expectation.

        Args:
            relationship: Native Relationship object or an explicit ``ms.ref`` authoring selector.
            expected: ``"one_to_one"``, ``"many_to_one"``, ``"one_to_many"`` or
                ``"many_to_many"`` in the declared from_entity to to_entity direction.
        Returns:
            A check for ``catalog.validate(..., checks=[...], scope=...)``.
        Example:
            ``ms.source_check.relationship_cardinality(ms.ref.relationship('sales.orders_to_customers'), expected='many_to_one')``.
        Constraints:
            Anonymous and routed field expressions are rejected. Construction reads no data;
            validation tests multiplicity only within the selected bounded scopes.
        """
        return RelationshipCardinalitySourceCheck(
            relationship=relationship,
            expected=expected,
        )


source_check = SourceCheckNamespace()
