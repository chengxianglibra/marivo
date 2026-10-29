"""Online physical contract validation and explicit bounded data checks."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from contextlib import nullcontext
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal, TypeAlias, cast
from zoneinfo import ZoneInfo

import ibis.expr.types as ir
import pandas as pd

from marivo._authoring.model import AuthoringRepair
from marivo._authoring.render import _repair_summary
from marivo._compat import UTC
from marivo._definition.types import DefinitionGraphError, InputContext, merge_input_contexts
from marivo._symbolic import (
    DatasourceSymbol,
    DimensionSymbol,
    EntitySymbol,
    LookupSymbol,
    MeasureSymbol,
    RelationshipSymbol,
    TimeDimensionSymbol,
    native_symbol_ref,
    symbol_factory,
    symbol_from_ref,
)
from marivo.datasource import credentials as cr
from marivo.datasource.engines import require_profile_for_backend_type
from marivo.datasource.errors import (
    DatasourceAuthoringError,
    DatasourceCredentialError,
    DatasourceCredentialScopeError,
    _backend_failure_summary,
)
from marivo.datasource.inspection import SourceInspection, _ibis_table_type, _inspect_in_project
from marivo.datasource.ir import CsvSourceIR, EntitySourceIR, JsonSourceIR
from marivo.datasource.source import AuthoringScope
from marivo.refs import (
    DatasourceKind,
    DimensionKind,
    EntityKind,
    FieldKind,
    MeasureKind,
    Ref,
    RelationshipKind,
    SemanticKind,
    SemanticKindTag,
    TimeDimensionKind,
)
from marivo.refs import ref as ref_factory
from marivo.render import Card, RenderableResult
from marivo.semantic._expression_binding import evaluate_expression_body
from marivo.semantic._physical_contracts import validate_bound_field
from marivo.semantic.errors import ErrorKind, SemanticError, SemanticRuntimeError, _raise, repair
from marivo.semantic.preview_scope import PreviewScope, _normalize_scopes

if TYPE_CHECKING:
    from marivo.semantic.catalog import (
        Dimension,
        Measure,
        Relationship,
        SemanticCatalog,
        TimeDimension,
    )
    from marivo.semantic.resolver import SemanticResolver
    from marivo.semantic.validator import Registry

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
    from marivo.semantic.catalog import Dimension, Measure, Relationship, TimeDimension

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
    from marivo.semantic.catalog import Dimension, Measure, Relationship, TimeDimension

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
        if not isinstance(self.max_age, timedelta) or self.max_age <= timedelta(0):
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


def _scope_dict(scope: AuthoringScope) -> dict[str, object]:
    payload: dict[str, object] = {
        "kind": "unpruned",
        "max_rows": scope.max_rows,
        "timeout_seconds": scope.timeout_seconds,
    }
    time_range = getattr(scope, "_time_range", None)
    if time_range is not None:
        payload.update(
            {
                "kind": "time_range",
                "column": time_range.column,
                "start": time_range.start.isoformat(),
                "end": time_range.end.isoformat(),
            }
        )
    elif hasattr(scope, "values"):
        payload.update({"kind": "partition", "values": dict(scope.values)})
    return payload


def _repair_dict(value: AuthoringRepair | None) -> dict[str, object] | None:
    if value is None:
        return None
    return {
        "kind": value.kind,
        "help_target": f"{value.help_target.surface}.{value.help_target.canonical_id}",
        "action": value.action,
        "snippet": value.snippet,
        "candidates": list(value.candidates),
    }


@dataclass(frozen=True, repr=False, slots=True)
class ValidationCheckResult(RenderableResult):
    kind: ValidationCheckKind
    status: ValidationStatus
    _datasource: Ref[DatasourceKind]
    source: EntitySourceIR
    _target_refs: tuple[Ref[SemanticKindTag], ...]
    _affected_refs: tuple[Ref[SemanticKindTag], ...]
    checked_at: str
    observed_schema_fingerprint: str | None
    observed_capability_fingerprint: str | None
    observed: Mapping[str, object]
    repair: AuthoringRepair | None
    user_data_queried: bool
    _scopes: tuple[tuple[Ref[EntityKind], AuthoringScope], ...] = ()

    @property
    def datasource(self) -> DatasourceSymbol:
        return symbol_factory.datasource(self._datasource.path)

    @property
    def target_refs(self) -> tuple[LookupSymbol, ...]:
        return tuple(symbol_from_ref(ref) for ref in self._target_refs)

    @property
    def affected_refs(self) -> tuple[LookupSymbol, ...]:
        return tuple(symbol_from_ref(ref) for ref in self._affected_refs)

    @property
    def scopes(self) -> tuple[tuple[EntitySymbol, AuthoringScope], ...]:
        return tuple((symbol_factory.entity(ref.path), scope) for ref, scope in self._scopes)

    def _repr_identity(self) -> str:
        return (
            f"ValidationCheckResult kind={self.kind} status={self.status} "
            f"targets={len(self._target_refs)}"
        )

    def _card(self) -> Card:
        card = Card(
            identity=self._repr_identity(),
            available=(
                ".observed",
                ".affected_refs",
                ".scopes",
                ".repair",
                ".show()",
                ".to_dict()",
            ),
        ).status(
            f"user_data_queried={self.user_data_queried} "
            f"datasource={self._datasource.key} source={self.source.kind}"
        )
        if self._affected_refs:
            card = card.listing("affected refs", (ref.key for ref in self._affected_refs))
        if self._scopes:
            card = card.listing(
                "scopes",
                (
                    f"{entity_ref.key}: {json.dumps(_scope_dict(scope), sort_keys=True, default=str)}"
                    for entity_ref, scope in self._scopes
                ),
            )
        if self.observed:
            card = card.field(
                "observed", json.dumps(dict(self.observed), sort_keys=True, default=str)
            )
        if self.repair is not None:
            card = card.field("repair", _repair_summary(self.repair))
        return card.field("checked_at", self.checked_at)

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "status": self.status,
            "datasource": self._datasource.key,
            "source": self.source.to_dict(),
            "target_refs": [ref.key for ref in self._target_refs],
            "affected_refs": [ref.key for ref in self._affected_refs],
            "checked_at": self.checked_at,
            "observed_schema_fingerprint": self.observed_schema_fingerprint,
            "observed_capability_fingerprint": self.observed_capability_fingerprint,
            "observed": dict(self.observed),
            "repair": _repair_dict(self.repair),
            "user_data_queried": self.user_data_queried,
            "scopes": [
                {"entity": entity_ref.key, "scope": _scope_dict(scope)}
                for entity_ref, scope in self._scopes
            ],
        }


@dataclass(frozen=True, repr=False, slots=True)
class ValidationReport(RenderableResult):
    status: ValidationStatus
    checks: tuple[ValidationCheckResult, ...]
    checked_at: str
    catalog_definition_fingerprint: str

    @property
    def _affected_refs(self) -> tuple[Ref[SemanticKindTag], ...]:
        return tuple(dict.fromkeys(ref for check in self.checks for ref in check._affected_refs))

    @property
    def affected_refs(self) -> tuple[LookupSymbol, ...]:
        return tuple(symbol_from_ref(ref) for ref in self._affected_refs)

    def _repr_identity(self) -> str:
        return f"ValidationReport status={self.status} checks={len(self.checks)}"

    def _card(self) -> Card:
        card = Card(
            identity=self._repr_identity(),
            available=(".checks", ".affected_refs", ".show()", ".to_dict()"),
        )
        failures = tuple(check for check in self.checks if check.status != "current")
        if failures:
            card = card.listing(
                "non-successful checks",
                (
                    f"{check.kind}: {check.status}; affected refs: "
                    f"{', '.join(ref.key for ref in check._affected_refs) or 'none'}"
                    + (f" -> {_repair_summary(check.repair)}" if check.repair is not None else "")
                    for check in failures
                ),
            )
        card = card.table(
            columns=("check", "status", "data query", "affected refs"),
            rows=(
                (
                    check.kind,
                    check.status,
                    "yes" if check.user_data_queried else "no",
                    str(len(check._affected_refs)),
                )
                for check in self.checks
            ),
            row_count=len(self.checks),
            label="online validation",
            show_omission_counts=True,
        )
        return card.field("checked_at", self.checked_at)

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "checks": [check.to_dict() for check in self.checks],
            "affected_refs": [ref.key for ref in self._affected_refs],
            "checked_at": self.checked_at,
            "catalog_definition_fingerprint": self.catalog_definition_fingerprint,
        }


def _overall_status(checks: Sequence[ValidationCheckResult]) -> ValidationStatus:
    statuses = {check.status for check in checks}
    for status in ("failed", "unavailable", "unknown"):
        if status in statuses:
            return cast("ValidationStatus", status)
    return "current"


def _fingerprint(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _reverse_affected(
    catalog: SemanticCatalog,
    roots: Sequence[Ref[SemanticKindTag]],
) -> tuple[Ref[SemanticKindTag], ...]:
    affected = set(roots)
    changed = True
    while changed:
        changed = False
        for ref, dependencies in catalog._state.dependencies.items():
            if ref not in affected and any(dependency in affected for dependency in dependencies):
                affected.add(ref)
                changed = True
    return tuple(sorted(affected, key=lambda ref: ref.key))


def _field_owner(registry: Registry, ref: Ref[SemanticKindTag]) -> str:
    if ref.kind in {SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION}:
        return registry.dimensions[ref.path].entity
    if ref.kind is SemanticKind.MEASURE:
        return registry.measures[ref.path].entity
    raise AssertionError(f"unsupported online validation field kind: {ref.kind}")


def _check_entities(check: SourceCheck, registry: Registry) -> tuple[str, ...]:
    if isinstance(check, UniqueSourceCheck):
        return tuple(dict.fromkeys(_field_owner(registry, field) for field in check._fields))
    if isinstance(check, NotNullSourceCheck | AllowedValuesSourceCheck | FreshnessSourceCheck):
        return (_field_owner(registry, check._field),)
    relationship = registry.relationships[check._relationship.path]
    return tuple(dict.fromkeys((relationship.from_entity, relationship.to_entity)))


def _normalize_checks(
    checks: Sequence[SourceCheck],
    *,
    registry: Registry,
    selected_entities: frozenset[str],
    catalog: SemanticCatalog,
) -> tuple[SourceCheck, ...]:
    if not isinstance(checks, Sequence) or isinstance(checks, str | bytes):
        _raise(
            ErrorKind.INVALID_REF,
            "checks must be a sequence of source checks.",
            cls=SemanticRuntimeError,
            details={"parameter": "checks", "query_executed": False},
        )
    normalized = tuple(checks)
    for check in normalized:
        if not isinstance(
            check,
            NotNullSourceCheck
            | AllowedValuesSourceCheck
            | UniqueSourceCheck
            | FreshnessSourceCheck
            | RelationshipMatchesSourceCheck
            | RelationshipCardinalitySourceCheck,
        ):
            _raise(
                ErrorKind.INVALID_REF,
                "catalog.validate(checks=...) requires values from ms.source_check.",
                cls=SemanticRuntimeError,
                details={"query_executed": False, "received_type": type(check).__name__},
            )
        if (
            check._context.project_root is not None
            and check._context.project_root != catalog.workspace_dir.resolve()
        ):
            _raise(
                ErrorKind.INVALID_REF,
                "The source check belongs to another project.",
                cls=SemanticRuntimeError,
                expected=f"a source check from {catalog.workspace_dir}",
                received=str(check._context.project_root),
                details={"parameter": "checks", "query_executed": False},
                repair_value=repair(
                    kind="inspect",
                    canonical_id="validate",
                    action="Rebuild the complete check with named objects from the receiving project's Catalog.",
                ),
            )
        target_refs = (
            check._fields
            if isinstance(check, UniqueSourceCheck)
            else (
                (check._field,)
                if isinstance(
                    check,
                    NotNullSourceCheck | AllowedValuesSourceCheck | FreshnessSourceCheck,
                )
                else (check._relationship,)
            )
        )
        for target in target_refs:
            if target not in registry_ref_set(registry):
                _raise(
                    ErrorKind.NOT_FOUND,
                    f"Online validation target {target.key!r} is absent from the loaded catalog.",
                    cls=SemanticRuntimeError,
                    refs=(target.key,),
                    details={"query_executed": False},
                )
        entities = _check_entities(check, registry)
        if isinstance(check, UniqueSourceCheck) and len(entities) != 1:
            _raise(
                ErrorKind.INVALID_REF,
                "ms.source_check.unique(fields=...) requires fields from one Entity.",
                cls=SemanticRuntimeError,
                refs=tuple(field.key for field in check._fields),
                details={"query_executed": False},
            )
        if not set(entities).issubset(selected_entities):
            _raise(
                ErrorKind.INVALID_REF,
                "Online validation checks must be grounded in the selected refs' Entity sources.",
                cls=SemanticRuntimeError,
                details={"query_executed": False, "unselected_entities": entities},
            )
    return normalized


def registry_ref_set(registry: Registry) -> frozenset[Ref[SemanticKindTag]]:
    refs: set[Ref[SemanticKindTag]] = set()
    refs.update(ref_factory.entity(value) for value in registry.entities)
    refs.update(
        (
            ref_factory.time_dimension(value)
            if field.is_time_dimension
            else ref_factory.dimension(value)
        )
        for value, field in registry.dimensions.items()
    )
    refs.update(ref_factory.measure(value) for value in registry.measures)
    refs.update(ref_factory.relationship(value) for value in registry.relationships)
    return frozenset(refs)


def _result(
    *,
    kind: ValidationCheckKind,
    status: ValidationStatus,
    datasource: Ref[DatasourceKind],
    source: EntitySourceIR,
    target_refs: Sequence[Ref[SemanticKindTag]],
    affected_refs: Sequence[Ref[SemanticKindTag]],
    checked_at: str,
    inspection: SourceInspection | None,
    observed: Mapping[str, object],
    repair_value: AuthoringRepair | None,
    user_data_queried: bool,
    scopes: Sequence[tuple[Ref[EntityKind], AuthoringScope]] = (),
) -> ValidationCheckResult:
    schema_fingerprint = None
    capability_fingerprint = None
    if inspection is not None:
        schema_fingerprint = _fingerprint(
            [(column.name, column.type, column.nullable) for column in inspection.schema]
        )
        capability_fingerprint = _fingerprint(asdict(inspection.execution_capabilities))
    return ValidationCheckResult(
        kind=kind,
        status=status,
        _datasource=datasource,
        source=source,
        _target_refs=tuple(target_refs),
        _affected_refs=tuple(affected_refs),
        checked_at=checked_at,
        observed_schema_fingerprint=schema_fingerprint,
        observed_capability_fingerprint=capability_fingerprint,
        observed=MappingProxyType(dict(observed)),
        repair=repair_value,
        user_data_queried=user_data_queried,
        _scopes=tuple(scopes),
    )


def _unavailable_observed(exc: Exception) -> dict[str, object]:
    failure = _backend_failure_summary(exc)
    message = f"{failure.identity} {failure.message}".lower()
    if "permission" in message or "denied" in message or "not authorized" in message:
        code = "permission_denied"
    elif any(
        marker in message
        for marker in (
            "not found",
            "missing",
            "does not exist",
            "tablenotfound",
            "failed to resolve datasource table",
        )
    ):
        code = "source_unavailable"
    else:
        code = "connection_unavailable"
    return {
        "code": code,
        "failure_identity": failure.identity,
        "message": failure.message,
    }


def _authoring_failure_observed(exc: DatasourceAuthoringError) -> dict[str, object]:
    failure = _backend_failure_summary(exc)
    return {
        "code": exc.code,
        "stage": exc.stage,
        "failure_identity": failure.identity,
        "message": failure.message,
    }


def _materialized_field(
    resolver: SemanticResolver,
    ref: Ref[FieldKind],
    table: ir.Table,
) -> ir.Value:
    if ref.kind in {SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION}:
        return resolver.dimension_on(ref, table)
    return resolver.measure_on(cast("Ref[MeasureKind]", ref), table)


def _input_column_names(inspection: SourceInspection) -> set[str]:
    """Return the physical input column names from one inspection."""
    return {column.name for column in inspection.schema}


def _schema_repair(
    *,
    schema_status: ValidationStatus,
    has_expression_body: bool,
    output_columns: tuple[str, ...] | None,
    output_reason: str | None,
    missing_fields: tuple[Ref[SemanticKindTag], ...],
    missing_keys: tuple[str, ...],
) -> AuthoringRepair | None:
    """Return the schema-check repair for one entity, or None when current.

    Repair text follows the check classification: direct entities repair
    against physical input columns; expression entities repair against the
    established output, the failed expression build, or the undetermined
    output schema.
    """
    if schema_status == "current":
        return None
    if schema_status == "unknown":
        return repair(
            kind="rescope",
            canonical_id="validate",
            action="Run only the explicit scoped data checks needed to prove the declared-only source contract.",
        )
    if missing_keys:
        return repair(
            kind="reauthor",
            canonical_id="validate",
            action=(
                "Expose the missing primary_key column(s) "
                f"{', '.join(repr(key) for key in missing_keys)} in the Entity body "
                "output, or update primary_key to columns that identify the "
                "transformed grain."
            ),
        )
    if not has_expression_body:
        return repair(
            kind="reauthor",
            canonical_id="validate",
            action="Restore the missing physical columns or update the affected semantic field bindings.",
        )
    if missing_fields:
        return repair(
            kind="reauthor",
            canonical_id="validate",
            action=(
                "Expose the missing column(s) in the Entity body output, or update the "
                "affected semantic field bindings to columns the Entity output provides."
            ),
        )
    if output_reason is not None:
        return repair(
            kind="reauthor",
            canonical_id="validate",
            action=(
                "The Entity output schema could not be established from the inspected "
                "input, so output-field presence is undetermined. Correct the Entity "
                "expression or the Source schema, then rerun online validation."
            ),
        )
    return repair(
        kind="reauthor",
        canonical_id="validate",
        action="Restore the missing physical columns or update the affected semantic field bindings.",
    )


def _schema_missing_fields(
    catalog: SemanticCatalog,
    *,
    entity_id: str,
    available_columns: set[str],
) -> tuple[Ref[SemanticKindTag], ...]:
    missing: list[Ref[SemanticKindTag]] = []
    for field_ref, owner_ref in catalog._state.sidecar.field_owners.items():
        if owner_ref.path != entity_id:
            continue
        body = catalog._state.sidecar.bodies.get(field_ref)
        if body is not None and any(
            column not in available_columns for column in body.source_columns
        ):
            missing.append(cast("Ref[SemanticKindTag]", field_ref))
    return tuple(missing)


def _inspected_entity_output(
    catalog: SemanticCatalog,
    *,
    entity_id: str,
    inspection: SourceInspection,
) -> tuple[ir.Table | None, str | None, dict[str, str]]:
    """Bind the Entity output to inspected physical types without reading rows."""
    import ibis

    entity_ref = ref_factory.entity(entity_id)
    body = catalog._state.sidecar.bodies.get(entity_ref)
    engine = catalog._reg.datasources[catalog._reg.entities[entity_id].datasource].backend_type
    input_types: dict[str, str] = {}
    unavailable: dict[str, str] = {}
    for column in inspection.schema:
        normalized = _ibis_table_type(
            column.type,
            backend_type=engine,
        )
        if normalized is None:
            unavailable[column.name] = column.type
        else:
            input_types[column.name] = normalized
    reason = "declared_input_types_unusable" if unavailable else None
    if unavailable and body is not None:
        return None, reason, unavailable
    try:
        unbound = ibis.table(input_types, name=f"marivo_unbound_{entity_id}")
    except Exception:
        return None, "declared_input_types_unusable", unavailable
    if body is None:
        return unbound, reason, unavailable
    try:
        result = evaluate_expression_body(
            catalog_definition_fingerprint=catalog.definition_fingerprint,
            expression_sidecar=catalog._state.sidecar,
            owning_ref=entity_ref,
            body=body,
            entity_refs=(entity_ref,),
            aliases=(unbound,),
            table_result=True,
        )
    except Exception:
        return None, "entity_expression_invalid", unavailable
    if not isinstance(result, ibis.Table):
        return None, "entity_expression_invalid", unavailable
    return result, None, unavailable


def _field_contracts(
    catalog: SemanticCatalog,
    *,
    entity_id: str,
    table: ir.Table,
    unavailable_fields: Sequence[Ref[SemanticKindTag]],
) -> tuple[dict[str, str], tuple[SemanticError, ...]]:
    """Validate every field on the selected Entity's real output schema."""
    field_types: dict[str, str] = {}
    errors: list[SemanticError] = []
    for field_ref, owner in sorted(
        catalog._state.sidecar.field_owners.items(), key=lambda item: item[0].key
    ):
        if owner.path != entity_id:
            continue
        body = catalog._state.sidecar.bodies[field_ref]
        if field_ref in unavailable_fields:
            continue
        try:
            value = evaluate_expression_body(
                catalog_definition_fingerprint=catalog.definition_fingerprint,
                expression_sidecar=catalog._state.sidecar,
                owning_ref=field_ref,
                body=body,
                entity_refs=(owner,),
                aliases=(table,),
            )
            field_types[field_ref.key] = str(value.type())
            validate_bound_field(value, ref=field_ref, registry=catalog._reg)
        except SemanticError as exc:
            errors.append(exc)
    return field_types, tuple(errors)


def _dependency_entities(
    catalog: SemanticCatalog, refs: Sequence[Ref[SemanticKindTag]]
) -> tuple[str, ...]:
    pending = list(refs)
    checked: set[Ref[SemanticKindTag]] = set()
    while pending:
        ref = pending.pop()
        if ref in checked:
            continue
        checked.add(ref)
        pending.extend(catalog._entry_dependencies(ref))
    return tuple(sorted(ref.path for ref in checked if ref.kind is SemanticKind.ENTITY))


def _field_frame(
    resolver: SemanticResolver,
    registry: Registry,
    fields: Sequence[Ref[FieldKind]],
    scope: AuthoringScope,
) -> pd.DataFrame:
    entity_id = _field_owner(registry, fields[0])
    table = resolver.entity(ref_factory.entity(entity_id)).limit(scope.max_rows)
    values = [
        _materialized_field(resolver, field, table).name(f"value_{index}")
        for index, field in enumerate(fields)
    ]
    return cast(
        "pd.DataFrame",
        resolver.connections.collect_source(
            registry.entities[entity_id].datasource,
            table.select(*values),
            purpose="semantic.validate_field",
            max_rows=scope.max_rows,
        ),
    )


def _freshness_observation(
    value: object,
    *,
    declared_timezone: str | None,
) -> tuple[datetime | None, str | None]:
    if value is None or value is pd.NaT or value is pd.NA:
        return None, "no_non_null_values"
    if isinstance(value, pd.Timestamp):
        value = value.to_pydatetime()
    if type(value) is date:
        value = datetime.combine(value, datetime.min.time(), tzinfo=UTC)
    if not isinstance(value, datetime):
        return None, "unsupported_observed_type"
    if value.tzinfo is None or value.utcoffset() is None:
        if declared_timezone is None:
            return None, "naive_observed_time"
        value = value.replace(tzinfo=ZoneInfo(declared_timezone))
    return value.astimezone(UTC), None


def _execute_field_check(
    *,
    catalog: SemanticCatalog,
    check: FieldSourceCheck,
    scopes: Mapping[str, AuthoringScope],
    inspections: Mapping[str, SourceInspection | None],
    resolver: SemanticResolver,
    checked_at: str,
) -> ValidationCheckResult:
    registry = catalog._reg
    fields = check._fields if isinstance(check, UniqueSourceCheck) else (check._field,)
    entity_id = _field_owner(registry, fields[0])
    entity = registry.entities[entity_id]
    datasource = ref_factory.datasource(entity.datasource)
    source = entity.source
    scope = scopes[entity_id]
    target_refs = tuple(fields)
    affected = _reverse_affected(catalog, target_refs)
    query_attempted = False
    try:
        profile = require_profile_for_backend_type(
            registry.datasources[entity.datasource].backend_type
        )
        timeout = profile.native_query_timeout
        with (
            resolver.connections.terminal_scope(scope.timeout_seconds)
            if timeout is not None
            else nullcontext()
        ):
            backend = resolver.connections.session_backend(entity.datasource)
            with timeout(backend, scope.timeout_seconds) if timeout is not None else nullcontext():
                query_attempted = True
                frame = _field_frame(resolver, registry, fields, scope)
    except Exception as exc:
        return _result(
            kind=check.kind,
            status="unavailable",
            datasource=datasource,
            source=source,
            target_refs=target_refs,
            affected_refs=affected,
            checked_at=checked_at,
            inspection=inspections.get(entity_id),
            observed=_unavailable_observed(exc),
            repair_value=repair(
                kind="reconnect",
                canonical_id="validate",
                action="Restore source access or repair the affected semantic field, then rerun online validation.",
            ),
            user_data_queried=query_attempted,
            scopes=((ref_factory.entity(entity_id), scope),),
        )

    status: ValidationStatus
    observed: dict[str, object]
    if isinstance(check, NotNullSourceCheck):
        null_count = int(frame.iloc[:, 0].isna().sum())
        status = "current" if null_count == 0 else "failed"
        observed = {"rows_observed": len(frame), "null_count": null_count}
    elif isinstance(check, AllowedValuesSourceCheck):
        values = frame.iloc[:, 0]
        invalid = values[values.notna() & ~values.isin(check.values)]
        status = "current" if invalid.empty else "failed"
        observed = {
            "rows_observed": len(frame),
            "invalid_count": len(invalid),
            "allowed_values": list(check.values),
        }
    elif isinstance(check, UniqueSourceCheck):
        duplicate_count = int(frame.duplicated(keep=False).sum())
        status = "current" if duplicate_count == 0 else "failed"
        observed = {
            "rows_observed": len(frame),
            "duplicate_row_count": duplicate_count,
        }
    elif isinstance(check, FreshnessSourceCheck):
        maximum = frame.iloc[:, 0].max() if not frame.empty else None
        parse = registry.dimensions[check._field.path].parse
        normalized, issue = _freshness_observation(
            maximum,
            declared_timezone=getattr(parse, "timezone", None),
        )
        if issue is not None:
            status = "unknown"
            observed = {"rows_observed": len(frame), "reason": issue}
        else:
            assert normalized is not None
            age = datetime.fromisoformat(checked_at) - normalized
            status = "current" if age <= check.max_age else "failed"
            observed = {
                "rows_observed": len(frame),
                "maximum": normalized.isoformat(),
                "age_seconds": age.total_seconds(),
                "max_age_seconds": check.max_age.total_seconds(),
            }
    else:
        raise AssertionError(f"unexpected field source check: {type(check).__name__}")
    return _result(
        kind=check.kind,
        status=status,
        datasource=datasource,
        source=source,
        target_refs=target_refs,
        affected_refs=affected,
        checked_at=checked_at,
        inspection=inspections.get(entity_id),
        observed=observed,
        repair_value=(
            None
            if status == "current"
            else repair(
                kind="reauthor",
                canonical_id="validate",
                action="Repair the scoped source data or revise the explicit expectation, then rerun online validation.",
            )
        ),
        user_data_queried=True,
        scopes=((ref_factory.entity(entity_id), scope),),
    )


def _relationship_frames(
    resolver: SemanticResolver,
    registry: Registry,
    relationship_ref: Ref[SemanticKindTag],
    scopes: Mapping[str, AuthoringScope],
) -> tuple[pd.DataFrame, pd.DataFrame, str, str]:
    relationship = registry.relationships[relationship_ref.path]
    left_id = relationship.from_entity
    right_id = relationship.to_entity
    left = resolver.entity(ref_factory.entity(left_id)).limit(scopes[left_id].max_rows)
    right = resolver.entity(ref_factory.entity(right_id)).limit(scopes[right_id].max_rows)
    left_values = []
    right_values = []
    for index, key in enumerate(relationship.keys):
        from_key, to_key = key.from_key, key.to_key
        from_ir = registry.dimensions[from_key]
        to_ir = registry.dimensions[to_key]
        from_ref = (
            ref_factory.time_dimension(from_key)
            if from_ir.is_time_dimension
            else ref_factory.dimension(from_key)
        )
        to_ref = (
            ref_factory.time_dimension(to_key)
            if to_ir.is_time_dimension
            else ref_factory.dimension(to_key)
        )
        left_values.append(
            resolver.dimension_on(cast("Ref[FieldKind]", from_ref), left).name(f"key_{index}")
        )
        right_values.append(
            resolver.dimension_on(cast("Ref[FieldKind]", to_ref), right).name(f"key_{index}")
        )
    return (
        resolver.connections.collect_source(
            registry.entities[left_id].datasource,
            left.select(*left_values),
            purpose="semantic.validate_relationship",
            max_rows=scopes[left_id].max_rows,
        ),
        resolver.connections.collect_source(
            registry.entities[right_id].datasource,
            right.select(*right_values),
            purpose="semantic.validate_relationship",
            max_rows=scopes[right_id].max_rows,
        ),
        left_id,
        right_id,
    )


def _execute_relationship_check(
    *,
    catalog: SemanticCatalog,
    check: RelationshipMatchesSourceCheck | RelationshipCardinalitySourceCheck,
    scopes: Mapping[str, AuthoringScope],
    inspections: Mapping[str, SourceInspection | None],
    resolver: SemanticResolver,
    checked_at: str,
) -> ValidationCheckResult:
    registry = catalog._reg
    relationship = registry.relationships[check._relationship.path]
    left_entity = registry.entities[relationship.from_entity]
    right_entity = registry.entities[relationship.to_entity]
    datasource = ref_factory.datasource(left_entity.datasource)
    target_refs = (check._relationship,)
    affected = _reverse_affected(catalog, target_refs)
    bound_scopes = (
        (ref_factory.entity(relationship.from_entity), scopes[relationship.from_entity]),
        (ref_factory.entity(relationship.to_entity), scopes[relationship.to_entity]),
    )
    if left_entity.datasource != right_entity.datasource:
        return _result(
            kind=check.kind,
            status="unknown",
            datasource=datasource,
            source=left_entity.source,
            target_refs=target_refs,
            affected_refs=affected,
            checked_at=checked_at,
            inspection=inspections.get(relationship.from_entity),
            observed={"reason": "cross_datasource_relationship"},
            repair_value=repair(
                kind="reauthor",
                canonical_id="validate",
                action="Run the relationship expectation in an accountable external system or colocate the source contract.",
            ),
            user_data_queried=False,
            scopes=bound_scopes,
        )
    query_attempted = False
    try:
        profile = require_profile_for_backend_type(
            registry.datasources[left_entity.datasource].backend_type
        )
        timeout = profile.native_query_timeout
        timeout_seconds = min(scope.timeout_seconds for _ref, scope in bound_scopes)
        with (
            resolver.connections.terminal_scope(timeout_seconds)
            if timeout is not None
            else nullcontext()
        ):
            backend = resolver.connections.session_backend(left_entity.datasource)
            with timeout(backend, timeout_seconds) if timeout is not None else nullcontext():
                query_attempted = True
                left, right, _left_id, _right_id = _relationship_frames(
                    resolver,
                    registry,
                    check._relationship,
                    scopes,
                )
    except Exception as exc:
        return _result(
            kind=check.kind,
            status="unavailable",
            datasource=datasource,
            source=left_entity.source,
            target_refs=target_refs,
            affected_refs=affected,
            checked_at=checked_at,
            inspection=inspections.get(relationship.from_entity),
            observed=_unavailable_observed(exc),
            repair_value=repair(
                kind="reconnect",
                canonical_id="validate",
                action="Restore both relationship sources and permissions, then rerun online validation.",
            ),
            user_data_queried=query_attempted,
            scopes=bound_scopes,
        )
    key_columns = list(left.columns)
    observed: dict[str, object]
    if isinstance(check, RelationshipMatchesSourceCheck):
        non_null_left = left.dropna(subset=key_columns)
        non_null_right = right.dropna(subset=key_columns)
        left_unmatched = len(left) - len(non_null_left)
        right_unmatched = len(right) - len(non_null_right)
        right_keys = set(map(tuple, non_null_right[key_columns].itertuples(index=False, name=None)))
        left_keys = set(map(tuple, non_null_left[key_columns].itertuples(index=False, name=None)))
        left_unmatched += sum(
            tuple(row) not in right_keys
            for row in non_null_left[key_columns].itertuples(index=False, name=None)
        )
        right_unmatched += sum(
            tuple(row) not in left_keys
            for row in non_null_right[key_columns].itertuples(index=False, name=None)
        )
        failed = left_unmatched > 0 or (check.side == "both" and right_unmatched > 0)
        status: ValidationStatus = "failed" if failed else "current"
        observed = {
            "from_rows_observed": len(left),
            "to_rows_observed": len(right),
            "from_unmatched_count": left_unmatched,
            "to_unmatched_count": right_unmatched,
            "required_side": check.side,
        }
    else:
        left_non_null = left.dropna(subset=key_columns)
        right_non_null = right.dropna(subset=key_columns)
        if left_non_null.empty or right_non_null.empty:
            status = "unknown"
            observed = {
                "from_rows_observed": len(left),
                "to_rows_observed": len(right),
                "reason": "insufficient_non_null_keys",
                "expected_cardinality": check.expected,
            }
        else:
            left_unique = not left_non_null.duplicated(subset=key_columns, keep=False).any()
            right_unique = not right_non_null.duplicated(subset=key_columns, keep=False).any()
            if left_unique and right_unique:
                observed_cardinality: RelationshipCardinality = "one_to_one"
            elif left_unique:
                observed_cardinality = "one_to_many"
            elif right_unique:
                observed_cardinality = "many_to_one"
            else:
                observed_cardinality = "many_to_many"
            status = "current" if observed_cardinality == check.expected else "failed"
            observed = {
                "from_rows_observed": len(left),
                "to_rows_observed": len(right),
                "observed_cardinality": observed_cardinality,
                "expected_cardinality": check.expected,
            }
    return _result(
        kind=check.kind,
        status=status,
        datasource=datasource,
        source=left_entity.source,
        target_refs=target_refs,
        affected_refs=affected,
        checked_at=checked_at,
        inspection=inspections.get(relationship.from_entity),
        observed=observed,
        repair_value=(
            None
            if status == "current"
            else (
                repair(
                    kind="rescope",
                    canonical_id="validate",
                    action="Choose a scope with non-null relationship keys, then rerun online validation.",
                )
                if status == "unknown"
                else repair(
                    kind="reauthor",
                    canonical_id="validate",
                    action="Repair the scoped relationship data or revise the explicit expectation, then rerun online validation.",
                )
            )
        ),
        user_data_queried=True,
        scopes=bound_scopes,
    )


def run_validation(
    catalog: SemanticCatalog,
    *,
    refs: Sequence[Ref[SemanticKindTag]],
    checks: Sequence[SourceCheck],
    scope: PreviewScope | None,
) -> ValidationReport:
    """Run metadata checks and explicitly requested bounded data checks."""
    registry = catalog._reg
    selected_entities = _dependency_entities(catalog, refs)
    if not selected_entities:
        _raise(
            ErrorKind.INVALID_REF,
            "catalog.validate(refs=...) requires refs grounded in at least one Entity.",
            cls=SemanticRuntimeError,
            refs=tuple(ref.key for ref in refs),
            details={"query_executed": False},
        )
    normalized_checks = _normalize_checks(
        checks,
        registry=registry,
        selected_entities=frozenset(selected_entities),
        catalog=catalog,
    )
    checked_entities = tuple(
        dict.fromkeys(
            entity_id
            for check in normalized_checks
            for entity_id in _check_entities(check, registry)
        )
    )
    if normalized_checks and scope is None:
        _raise(
            ErrorKind.MATERIALIZE_FAILED,
            "catalog.validate(..., checks=...) requires one explicit AuthoringScope or exact Entity mapping.",
            cls=SemanticRuntimeError,
            refs=tuple(ref.key for ref in refs),
            details={"query_executed": False},
        )
    if not normalized_checks and scope is not None:
        _raise(
            ErrorKind.MATERIALIZE_FAILED,
            "catalog.validate(..., scope=...) requires at least one explicit data check.",
            cls=SemanticRuntimeError,
            refs=tuple(ref.key for ref in refs),
            details={"query_executed": False},
        )
    normalized_scopes = (
        dict(
            _normalize_scopes(
                checked_entities,
                scope,
                preview_ref=refs[0].path,
                operation="catalog.validate",
                catalog=catalog,
            )
        )
        if scope is not None
        else {}
    )
    with catalog._project._connection_operation() as connections:
        checked_at = datetime.now(tz=UTC).isoformat()
        results: list[ValidationCheckResult] = []
        inspections: dict[str, SourceInspection | None] = {}
        connectivity: dict[str, tuple[ValidationStatus, Mapping[str, object]]] = {}
        for entity_id in selected_entities:
            entity = registry.entities[entity_id]
            exact_datasource_ref = ref_factory.datasource(entity.datasource)
            if entity.datasource not in connectivity:
                try:
                    backend = connections.session_backend(entity.datasource)
                    with cr.backend_errors(backend):
                        from marivo.datasource.adapters import provider_for

                        provider_for(registry.datasources[entity.datasource].backend_type).probe(
                            backend
                        )
                except (DatasourceCredentialError, DatasourceCredentialScopeError):
                    raise
                except Exception as exc:
                    connectivity[entity.datasource] = (
                        "unavailable",
                        _unavailable_observed(exc),
                    )
                else:
                    connectivity[entity.datasource] = ("current", {"roundtrip": "ibis_literal"})
            connection_status, connection_observed = connectivity[entity.datasource]
            entity_ref = cast("Ref[SemanticKindTag]", ref_factory.entity(entity_id))
            affected = _reverse_affected(catalog, (entity_ref,))
            results.append(
                _result(
                    kind="connectivity",
                    status=connection_status,
                    datasource=exact_datasource_ref,
                    source=entity.source,
                    target_refs=(entity_ref,),
                    affected_refs=affected,
                    checked_at=checked_at,
                    inspection=None,
                    observed=connection_observed,
                    repair_value=(
                        None
                        if connection_status == "current"
                        else repair(
                            kind="reconnect",
                            canonical_id="validate",
                            action="Restore datasource connectivity or permissions, then rerun online validation.",
                        )
                    ),
                    user_data_queried=False,
                )
            )
            try:
                with connections.resolution_context():
                    inspection = _inspect_in_project(
                        exact_datasource_ref,
                        entity.source,
                        project_root=catalog._project.workspace_dir,
                        connections=connections,
                    )
            except (DatasourceCredentialError, DatasourceCredentialScopeError):
                raise
            except DatasourceAuthoringError as exc:
                inspections[entity_id] = None
                authoring_status: ValidationStatus = (
                    "unavailable" if exc.code == "datasource_missing" else "failed"
                )
                results.append(
                    _result(
                        kind="schema",
                        status=authoring_status,
                        datasource=exact_datasource_ref,
                        source=entity.source,
                        target_refs=(entity_ref,),
                        affected_refs=affected,
                        checked_at=checked_at,
                        inspection=None,
                        observed=_authoring_failure_observed(exc),
                        repair_value=exc.repair,
                        user_data_queried=False,
                    )
                )
                continue
            except Exception as exc:
                inspections[entity_id] = None
                results.append(
                    _result(
                        kind="schema",
                        status="unavailable",
                        datasource=exact_datasource_ref,
                        source=entity.source,
                        target_refs=(entity_ref,),
                        affected_refs=affected,
                        checked_at=checked_at,
                        inspection=None,
                        observed=_unavailable_observed(exc),
                        repair_value=repair(
                            kind="inspect",
                            canonical_id="validate",
                            action="Restore metadata access or the physical source, then rerun online validation.",
                        ),
                        user_data_queried=False,
                    )
                )
                continue
            inspections[entity_id] = inspection
            has_expression_body = catalog._state.sidecar.bodies.get(entity_ref) is not None
            output, output_reason, unavailable_columns = _inspected_entity_output(
                catalog, entity_id=entity_id, inspection=inspection
            )
            output_columns = tuple(output.columns) if output is not None else None
            if not has_expression_body:
                # Direct entities: the Source schema is the output schema, so
                # field bindings keep comparing against physical input.
                missing_fields = _schema_missing_fields(
                    catalog,
                    entity_id=entity_id,
                    available_columns=_input_column_names(inspection),
                )
            elif output_columns is not None:
                missing_fields = _schema_missing_fields(
                    catalog,
                    entity_id=entity_id,
                    available_columns=set(output_columns),
                )
            else:
                # The expression Entity output schema is not established;
                # neither input columns nor an empty relation can decide
                # downstream output-field presence, so no field is reported
                # missing.
                missing_fields = ()
            if output_columns is not None:
                output_set = (
                    _input_column_names(inspection)
                    if not has_expression_body
                    else set(output_columns)
                )
                missing_keys = tuple(key for key in entity.primary_key if key not in output_set)
            else:
                missing_keys = ()
            unavailable_roots = tuple(
                field_ref
                for field_ref, owner in catalog._state.sidecar.field_owners.items()
                if owner.path == entity_id
                and unavailable_columns
                and (
                    output is None
                    or any(
                        column in unavailable_columns
                        for column in catalog._state.sidecar.bodies[field_ref].source_columns
                    )
                )
            )
            unavailable_dependents = (
                set(_reverse_affected(catalog, unavailable_roots)) if unavailable_roots else set()
            )
            unavailable_fields = tuple(
                field_ref
                for field_ref, owner in sorted(
                    catalog._state.sidecar.field_owners.items(), key=lambda item: item[0].key
                )
                if owner.path == entity_id and field_ref in unavailable_dependents
            )
            field_types, contract_errors = (
                _field_contracts(
                    catalog,
                    entity_id=entity_id,
                    table=output,
                    unavailable_fields=unavailable_fields,
                )
                if output is not None
                else ({}, ())
            )
            declared_only = isinstance(entity.source, CsvSourceIR | JsonSourceIR) or any(
                "declared" in warning.lower() or "metadata_unavailable" in warning.lower()
                for warning in inspection.warnings
            )
            schema_status: ValidationStatus = (
                "failed"
                if missing_fields or output_reason is not None or missing_keys or contract_errors
                else "unknown"
                if declared_only
                else "current"
            )
            schema_affected = (
                _reverse_affected(
                    catalog,
                    (*missing_fields, entity_ref),
                )
                if missing_fields or missing_keys
                else affected
            )
            observed: dict[str, object] = {
                "column_count": len(inspection.schema),
                "missing_field_refs": [ref.key for ref in missing_fields],
                "metadata_authority": "declared" if declared_only else "authoritative",
                "metadata_warnings": list(inspection.warnings),
                "execution_capabilities": asdict(inspection.execution_capabilities),
                "field_types": field_types,
                "contract_violations": [
                    {
                        "refs": list(error.semantic_refs),
                        "expected": error.expected,
                        "received": error.received,
                        "location": (
                            {"file": error.location.file, "line": error.location.line}
                            if error.location is not None
                            else None
                        ),
                        "message": str(error),
                    }
                    for error in contract_errors
                ],
            }
            if unavailable_columns:
                observed["unavailable_columns"] = dict(sorted(unavailable_columns.items()))
                observed["unavailable_field_refs"] = [ref.key for ref in unavailable_fields]
            if missing_keys:
                observed["missing_output_primary_key"] = list(missing_keys)
            if output_reason is not None:
                observed["reason"] = output_reason
            if has_expression_body:
                observed["entity_output_established"] = output_reason is None
                if output_columns is not None:
                    observed["entity_output_columns"] = list(output_columns)
                else:
                    observed["reason"] = output_reason
            results.append(
                _result(
                    kind="schema",
                    status=schema_status,
                    datasource=exact_datasource_ref,
                    source=entity.source,
                    target_refs=(entity_ref,),
                    affected_refs=schema_affected,
                    checked_at=checked_at,
                    inspection=inspection,
                    observed=observed,
                    repair_value=(contract_errors[0].repair if contract_errors else None)
                    or (
                        repair(
                            kind="reauthor",
                            canonical_id="validate",
                            action=(
                                "Physical types are unavailable for "
                                + ", ".join(
                                    f"{name!r} ({kind})"
                                    for name, kind in sorted(unavailable_columns.items())
                                )
                                + ". Expose supported physical types or explicitly project the needed "
                                "supported columns with md.table(columns=...), then rerun catalog.validate()."
                            ),
                        )
                        if unavailable_columns
                        else None
                    )
                    or _schema_repair(
                        schema_status=schema_status,
                        has_expression_body=has_expression_body,
                        output_columns=output_columns,
                        output_reason=output_reason,
                        missing_fields=missing_fields,
                        missing_keys=missing_keys,
                    ),
                    user_data_queried=False,
                )
            )
        if normalized_checks:
            resolver = catalog._semantic_resolver(
                connections=connections,
                entity_scopes=normalized_scopes,
            )
            for check in normalized_checks:
                if isinstance(
                    check, RelationshipMatchesSourceCheck | RelationshipCardinalitySourceCheck
                ):
                    results.append(
                        _execute_relationship_check(
                            catalog=catalog,
                            check=check,
                            scopes=normalized_scopes,
                            inspections=inspections,
                            resolver=resolver,
                            checked_at=checked_at,
                        )
                    )
                else:
                    results.append(
                        _execute_field_check(
                            catalog=catalog,
                            check=check,
                            scopes=normalized_scopes,
                            inspections=inspections,
                            resolver=resolver,
                            checked_at=checked_at,
                        )
                    )
        return ValidationReport(
            status=_overall_status(results),
            checks=tuple(results),
            checked_at=checked_at,
            catalog_definition_fingerprint=catalog.definition_fingerprint,
        )
