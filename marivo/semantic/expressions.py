"""Frozen physical metadata and native Deferred resolution for semantic authoring."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, is_dataclass
from dataclasses import fields as dataclass_fields
from dataclasses import replace as dataclass_replace
from typing import TYPE_CHECKING, Literal, NoReturn, TypeAlias, TypeVar, cast, overload

from ibis.common.deferred import Deferred

from marivo._definition.deferred import RelationshipParameter
from marivo._definition.expressions import (
    DeclaredMetricRef,
    FieldPredicate,
    MetricNode,
)
from marivo._definition.graph import (
    DefinitionClosure,
    DefinitionNode,
    direct_refs,
    walk,
)
from marivo._definition.types import (
    DefinitionGraphError,
    ExactRef,
    FieldRead,
    NullPolicy,
    SumAll,
    SumAllExcept,
    SumAxes,
    SumNone,
    SumPermission,
    ZeroDenominator,
)
from marivo._definition.types import (
    SourceLocation as DefinitionSourceLocation,
)
from marivo._temporal import InstantAnchor
from marivo.refs import (
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
from marivo.refs import ref as ref_factory
from marivo.render import Card, RenderableResult
from marivo.semantic._compiled_state import CompiledSemanticState
from marivo.semantic._dsl_authoring import (
    AdditiveAllV1,
    AdditiveOverV1,
    AdditivityPolicy,
    NonAdditiveV1,
)
from marivo.semantic.errors import ErrorKind, SemanticExpressionError
from marivo.semantic.ir import (
    DimensionIR,
    EntityIR,
    RelationshipIR,
    SemiAdditive,
    SourceLocation,
    TargetRelationshipContract,
)
from marivo.semantic.typing import AiContextValue
from marivo.semantic.unit_algebra import (
    divide_unit_states,
    linear_unit,
    linear_units_conflict,
    multiply_unit_states,
    render_unit,
    unit_state,
)
from marivo.semantic.validator import Registry, normalize_target_relationship

if TYPE_CHECKING:
    from marivo.datasource.ir import AiContextIR, DatasourceIR
    from marivo.semantic.catalog import SemanticCatalog
    from marivo.semantic.loader import LoaderContext


Cardinality: TypeAlias = Literal["one_to_one", "many_to_one", "one_to_many", "many_to_many"]


RowOperator: TypeAlias = Literal["add", "subtract", "multiply", "divide"]


_SYMBOLS: dict[RowOperator, str] = {"add": "+", "subtract": "-", "multiply": "*", "divide": "/"}


def _fail(kind: ErrorKind, message: str, *, expected: str, received: str, action: str) -> NoReturn:
    raise SemanticExpressionError(
        kind=kind, message=message, expected=expected, received=received, action=action
    )


def _received(value: object) -> str:
    if isinstance(value, int) and value.bit_length() > 192:
        # Describe huge ints by size; decimal conversion is slow and can exceed the str limit.
        return f"{type(value).__name__} with {value.bit_length()} bits"
    return f"{type(value).__name__} {repr(value)[:60]}"


@dataclass(frozen=True, slots=True)
class DefinitionFacts:
    """Declared metadata of one resolved definition; it never enters a computation."""

    name: str
    ai_context: AiContextIR | AiContextValue | None
    location: str
    reads: tuple[Ref[SemanticKindTag], ...]
    details: tuple[tuple[str, str], ...] = ()


ExpressionStage: TypeAlias = Literal[
    "entity",
    "dimension",
    "time_field",
    "measure",
    "row_expression",
    "declared_measure",
    "metric",
    "declared_metric",
    "published_metric",
    "metric_samples",
    "predicate",
    "relationship",
]


@dataclass(frozen=True, repr=False)
class ExpressionContract(RenderableResult):
    """Bounded definition contract of one expression value.

    It discloses the stage, identity, contribution roots, unit, sum permission,
    default time, computation fingerprint, the semantic refs it reads, declared
    metadata and the operations valid at this stage. Reading it never touches
    a source, executes a query, or enumerates a domain.
    """

    stage: ExpressionStage
    ref: ExactRef | None
    roots: tuple[ExactRef, ...]
    unit: str | None
    sum_permission: SumPermission | None
    default_time: ExactRef | None
    computation: str | None
    reads: tuple[Ref[SemanticKindTag], ...]
    name: str | None
    business_definition: str | None
    guardrails: tuple[str, ...]
    location: str | None
    facts: tuple[tuple[str, str], ...]
    operations: tuple[str, ...]

    def _repr_identity(self) -> str:
        identity = self.ref.ref.key if self.ref is not None else (self.name or "anonymous")
        return f"ExpressionContract {self.stage} {identity}"

    def _card(self) -> Card:
        card = Card(identity=self._repr_identity(), available=(".show()",))
        card = card.field("stage", self.stage)
        if self.ref is not None:
            card = card.field("ref", f"{self.ref.ref.key}@{self.ref.version[:19]}")
        if self.name is not None:
            card = card.field("name", self.name)
        if self.roots:
            card = card.field("roots", _bounded_refs(root.ref for root in self.roots))
        if self.unit is not None:
            card = card.field("unit", self.unit)
        if self.sum_permission is not None:
            card = card.field("sum_permission", _permission_text(self.sum_permission))
        if self.default_time is not None:
            card = card.field("default_time", self.default_time.ref.key)
        if self.computation is not None:
            card = card.field("computation", self.computation[:26])
        card = card.field("reads", _bounded_refs(self.reads) or "(none)")
        for label, value in self.facts[:8]:
            card = card.field(label, value[:160])
        if self.business_definition is not None:
            card = card.field("business_definition", self.business_definition[:200])
        if self.guardrails:
            card = card.field("guardrails", "; ".join(self.guardrails[:3])[:200])
        if self.location is not None:
            card = card.field("declared_at", self.location)
        if self.operations:
            card = card.field("operations", ", ".join(self.operations))
        return card.field(
            "boundary", "definition only; no source read, query, or domain enumeration"
        )


def _bounded_refs(refs: Iterable[Ref[SemanticKindTag]], *, limit: int = 6) -> str:
    keys = [ref.key for ref in refs]
    extra = f" (+{len(keys) - limit} more)" if len(keys) > limit else ""
    return ", ".join(keys[:limit]) + extra


def _permission_text(permission: SumPermission) -> str:
    match permission:
        case SumAll():
            return "sum over every axis"
        case SumNone():
            return "no sum"
        case SumAxes(axes):
            return "sum over " + _bounded_refs(axis.ref for axis in axes)
        case SumAllExcept(axes):
            return "sum over every axis except " + _bounded_refs(axis.ref for axis in axes)


class _ExpressionDisplay:
    """Bounded metadata-only inspection shared by constructed expression values."""

    __slots__ = ()

    def contract(self) -> ExpressionContract:
        """Return this value's bounded definition contract.

        Returns: An ExpressionContract with stage, identity, roots, unit, sum
            permission, default time, computation fingerprint, refs read,
            declared metadata and the operations valid at this stage.
        Example: ``amount.contract().show()``.
        Constraints: Definition facts only; never reads a source or executes.
        """
        return _expression_contract(self)

    def show(self) -> None:
        """Print this value's bounded definition contract without reading source data.

        Returns: None.
        Example: ``amount.show()``.
        Constraints: Definition facts only, not execution evidence.
        """
        self.contract().show()


@dataclass(frozen=True, slots=True)
class Entity(_ExpressionDisplay):
    """Resolved Entity: one contribution root with its complete primary key."""

    ref: ExactRef
    primary_key: tuple[str, ...]
    default_time: ExactRef | None
    _facts: DefinitionFacts | None = field(default=None, compare=False, repr=False, kw_only=True)

    def count(self, *, time: Deferred | None = None) -> Deferred:
        """Capture a count of complete contribution rows without reading a source."""
        from marivo._definition.deferred import SemanticInput, semantic_input

        value = semantic_input(SemanticInput(self.ref, self.ref, "1", SumNone(), self.default_time))
        return cast("Deferred", value.count(time=time))

    def __repr__(self) -> str:
        return f"Entity {self.ref.ref.path}@{self.ref.version[:19]} (use .show())"


@dataclass(frozen=True, slots=True)
class Relationship(RelationshipParameter, _ExpressionDisplay):
    """Resolved directed relationship between two Entities."""

    _facts: DefinitionFacts | None = field(default=None, compare=False, repr=False, kw_only=True)

    def __repr__(self) -> str:
        return f"Relationship {self.ref.ref.path}@{self.ref.version[:19]} (use .show())"


class _FieldMetadata(_ExpressionDisplay):
    """Private physical facts; author-facing inputs use native Deferred."""

    path: tuple[Relationship, ...]
    anchor: InstantAnchor | None

    def _require_native(self, *, operation: str) -> None:
        if self.path or self.anchor is not None:
            _fail(
                ErrorKind.INVALID_TIME_PATH,
                "A bound field cannot be used in physical authoring.",
                expected="an unbound native field",
                received=operation,
                action="Use the native captured field Ref in physical declarations.",
            )


@dataclass(frozen=True, slots=True, eq=False)
class Dimension(_FieldMetadata):
    ref: ExactRef
    root: ExactRef
    default_time: ExactRef | None
    anchor: InstantAnchor | None = field(default=None, kw_only=True)
    _versioned: bool = field(default=False, compare=False, repr=False, kw_only=True)
    path: tuple[Relationship, ...] = field(default=(), kw_only=True)
    _facts: DefinitionFacts | None = field(default=None, compare=False, repr=False, kw_only=True)


@dataclass(frozen=True, slots=True, eq=False)
class TimeField(_FieldMetadata):
    ref: ExactRef
    root: ExactRef
    default_time: ExactRef | None
    path: tuple[Relationship, ...] = ()
    anchor: InstantAnchor | None = field(default=None, kw_only=True)
    _versioned: bool = field(default=False, compare=False, repr=False, kw_only=True)
    _facts: DefinitionFacts | None = field(default=None, compare=False, repr=False, kw_only=True)


@dataclass(frozen=True, slots=True, eq=False)
class Measure(_FieldMetadata):
    ref: ExactRef
    root: ExactRef
    unit: str | None
    permission: SumPermission
    default_time: ExactRef | None
    coordinates: tuple[ExactRef, ...] = ()
    anchor: InstantAnchor | None = field(default=None, kw_only=True)
    _versioned: bool = field(default=False, compare=False, repr=False, kw_only=True)
    path: tuple[Relationship, ...] = field(default=(), kw_only=True)
    _facts: DefinitionFacts | None = field(default=None, compare=False, repr=False, kw_only=True)


Field: TypeAlias = Dimension | TimeField | Measure
ResolvedValue: TypeAlias = Entity | Field | Relationship | Deferred


def declare_measure(
    row: Deferred,
    *,
    name: str,
    unit: str | None,
    additivity: AdditivityPolicy | None,
    ai_context: AiContextValue | None,
    location: SourceLocation,
) -> Deferred:
    """Attach local metadata and only genuinely new, compatible business assertions."""
    from marivo._definition.deferred import DeclarationRecord, declare, inspect_deferred
    from marivo.semantic._deferred_semantics import consume_row

    value = consume_row(row)
    declared_unit = render_unit(unit_state(unit)) if unit is not None else value.unit
    if unit is not None and value.unit is not None:
        if linear_units_conflict((unit, value.unit)):
            _fail(
                ErrorKind.INCOMMENSURABLE_LINEAR_UNITS,
                f"Measure {name!r} cannot relabel its inferred unit.",
                expected=f"a unit compatible with {value.unit}",
                received=unit,
                action=f"Keep unit={value.unit!r}, or change the row expression explicitly.",
            )
        declared_unit = value.unit
    permission = value.permission
    if additivity is not None:
        axes = {axis.ref.path: axis for axis in value.coordinates}
        paths = (
            additivity.coordinates
            if isinstance(additivity, AdditiveOverV1)
            else additivity.exceptions
            if isinstance(additivity, AdditiveAllV1)
            else ()
        )
        missing = tuple(path for path in paths if path not in axes)
        if missing:
            _fail(
                ErrorKind.INVALID_EXPRESSION_VALUE,
                f"Measure {name!r} grants permission on unknown or foreign axes.",
                expected=f"declared coordinates on {value.root.ref.path}",
                received=", ".join(missing),
                action=f"Choose existing coordinates: {', '.join(sorted(axes)) or '(none)'}.",
            )
        if isinstance(additivity, AdditiveOverV1):
            permission = SumAxes(tuple(axes[path] for path in paths))
        elif isinstance(additivity, AdditiveAllV1):
            permission = SumAllExcept(tuple(axes[path] for path in paths)) if paths else SumAll()
        elif isinstance(additivity, NonAdditiveV1):
            permission = SumNone()
        else:
            _fail(
                ErrorKind.INVALID_EXPRESSION_VALUE,
                "Measure additivity requires a closed additivity policy.",
                expected="ms.additive(...), ms.additive_all(...), or ms.non_additive()",
                received=type(additivity).__name__,
                action="Construct additivity with the public policy helper before declaring the Measure.",
            )
        # Arithmetic can lose sum permission, but cannot lose a leaf's fixed time axis.
        fixed = tuple(
            dict.fromkeys(
                axis
                for use in inspect_deferred(row).uses
                if isinstance(use.input.permission, SumAllExcept)
                for axis in use.input.permission.axes
                if axis.ref.kind is SemanticKind.TIME_DIMENSION
            )
        )
        erased = tuple(
            axis
            for axis in fixed
            if (
                isinstance(permission, SumAll)
                or (
                    isinstance(permission, SumAxes)
                    and axis.ref in {item.ref for item in permission.axes}
                )
                or (
                    isinstance(permission, SumAllExcept)
                    and axis.ref not in {item.ref for item in permission.axes}
                )
            )
        )
        if erased:
            _fail(
                ErrorKind.INVALID_EXPRESSION_VALUE,
                f"Measure {name!r} cannot erase fixed time constraints.",
                expected="sum permission that keeps the dependency's time axes fixed",
                received=", ".join(axis.ref.path for axis in erased),
                action="Keep these time axes in additive_all(except_=...), or use non_additive().",
            )
    asserted_unit = declared_unit if declared_unit != value.unit else None
    if (
        isinstance(permission, (SumAxes, SumAllExcept))
        and type(permission) is type(value.permission)
        and isinstance(value.permission, (SumAxes, SumAllExcept))
        and frozenset(permission.axes) == frozenset(value.permission.axes)
    ):
        permission = value.permission
    asserted_permission = permission if permission != value.permission else None
    return declare(
        row,
        DeclarationRecord(
            kind="measure",
            name=name,
            unit=asserted_unit,
            permission=asserted_permission,
            ai_context=ai_context,
            location=DefinitionSourceLocation(location.file, location.line),
        ),
    )


def _zero_denominator(value: object) -> ZeroDenominator:
    if value == "null":
        return "null"
    if value == "error":
        return "error"
    _fail(
        ErrorKind.INVALID_EXPRESSION_VALUE,
        "divide received an unsupported zero_denominator policy.",
        expected='zero_denominator="null" or zero_denominator="error"',
        received=_received(value),
        action='Use "null" (default) or "error" for this division.',
    )


def _intersect(left: SumPermission, right: SumPermission) -> SumPermission:
    """Sum permission granted by both operands; never wider than either input."""
    if isinstance(left, SumNone) or isinstance(right, SumNone):
        return SumNone()
    if isinstance(left, SumAll):
        return right
    if isinstance(right, SumAll):
        return left
    left_refs = {axis.ref for axis in left.axes}
    right_refs = {axis.ref for axis in right.axes}
    if isinstance(left, SumAllExcept) and isinstance(right, SumAllExcept):
        return SumAllExcept(
            (*left.axes, *(axis for axis in right.axes if axis.ref not in left_refs))
        )
    if isinstance(left, SumAxes) and isinstance(right, SumAxes):
        axes = tuple(axis for axis in left.axes if axis.ref in right_refs)
    elif isinstance(left, SumAxes) and isinstance(right, SumAllExcept):
        axes = tuple(axis for axis in left.axes if axis.ref not in right_refs)
    else:
        assert isinstance(left, SumAllExcept) and isinstance(right, SumAxes)
        axes = tuple(axis for axis in right.axes if axis.ref not in left_refs)
    return SumAxes(axes) if axes else SumNone()


def _arith_unit(
    op: RowOperator,
    left: str | None,
    right: str | None,
    *,
    constant: Literal["left", "right"] | None,
    stage: Literal["Row", "Metric"],
) -> str | None:
    """Unit of one arithmetic use; a bare constant takes the other operand's unit for
    addition and subtraction and is dimensionless for multiplication and division."""
    if constant == "left":
        if op == "divide":
            return render_unit(divide_unit_states(unit_state("1"), unit_state(right)))
        return right
    if constant == "right":
        return left
    if op in ("add", "subtract"):
        if linear_units_conflict((left, right)):
            symbol = _SYMBOLS[op]
            _fail(
                ErrorKind.INCOMMENSURABLE_LINEAR_UNITS,
                f"{stage} {symbol} combines incompatible units {left} and {right}.",
                expected="operands with one commensurable unit",
                received=f"{left} {symbol} {right}",
                action=(
                    "Convert one operand to the other unit explicitly, then combine again; "
                    "units are never converted implicitly."
                ),
            )
        return linear_unit((left, right))
    states = (unit_state(left), unit_state(right))
    if op == "multiply":
        return render_unit(multiply_unit_states(*states))
    return render_unit(divide_unit_states(*states))


def _nulls(value: object, *, operation: str) -> NullPolicy:
    if value == "ignore":
        return "ignore"
    if value == "error":
        return "error"
    _fail(
        ErrorKind.INVALID_EXPRESSION_VALUE,
        f"{operation} received an unsupported nulls policy.",
        expected='nulls="ignore" or nulls="error"',
        received=_received(value),
        action='Use "ignore" (default) or "error" for this reduction.',
    )


def _quantile(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(
            ErrorKind.INVALID_EXPRESSION_VALUE,
            "quantile received a q that is not a number.",
            expected="a finite float q with 0 <= q <= 1",
            received=_received(value),
            action="Pass q such as 0.95.",
        )
    if not 0 <= value <= 1:
        # Range-check integers before float conversion, which overflows for huge values.
        _fail(
            ErrorKind.INVALID_EXPRESSION_VALUE,
            "quantile received a q outside [0, 1].",
            expected="a finite float q with 0 <= q <= 1",
            received=_received(value),
            action="Pass q such as 0.95.",
        )
    return float(value)


_SUPPORTED_KINDS = (
    SemanticKind.ENTITY,
    SemanticKind.DIMENSION,
    SemanticKind.TIME_DIMENSION,
    SemanticKind.MEASURE,
    SemanticKind.METRIC,
    SemanticKind.RELATIONSHIP,
)


@dataclass(frozen=True, slots=True)
class FinalizedDeclarations:
    """Validated, immutable physical definitions; resolving never reads source data."""

    _state: CompiledSemanticState = field(repr=False)

    def __repr__(self) -> str:
        return f"FinalizedDeclarations {self._state.definition_fingerprint[:26]} (use .show())"

    def show(self) -> None:
        """Print the frozen definition count and available operation.

        Returns: None.
        Example: ``finalized.show()``.
        Constraints: No Catalog Metric publication or execution is implied.
        """
        print(repr(self))
        print(f"  Physical definitions: {len(self._state.definitions)}")
        print("  Resolve: finalized.resolve(captured_ref)")

    @overload
    def resolve(self, target: Ref[EntityKind]) -> Entity: ...
    @overload
    def resolve(self, target: Ref[DimensionKind]) -> Deferred: ...
    @overload
    def resolve(self, target: Ref[TimeDimensionKind]) -> Deferred: ...
    @overload
    def resolve(self, target: Ref[MeasureKind]) -> Deferred: ...
    @overload
    def resolve(self, target: Ref[RelationshipKind]) -> Relationship: ...
    @overload
    def resolve(self, target: Ref[SemanticKindTag]) -> ResolvedValue: ...
    def resolve(self, target: Ref[SemanticKindTag]) -> ResolvedValue:
        """Resolve a captured lookup key into a closed immutable expression value.

        Args: target: An Entity, Dimension, TimeField, Measure or Relationship Ref.
        Returns: The corresponding immutable value with complete scope facts.
        Example: ``amount = finalized.resolve(amount_ref)``.
        Constraints: Only this frozen scope is read; bare Refs cannot be composed.
        """
        return resolve(self, target)

    def require(self, target: Ref[SemanticKindTag]) -> None:
        if target not in self._state.definitions:
            raise SemanticExpressionError(
                kind=ErrorKind.NOT_FOUND,
                message=f"Ref {target.key!r} is not among the finalized declarations.",
                expected=f"a captured {target.kind.value} Ref in this finalized scope",
                received=target.key,
                action="Capture this declaration and its dependencies before ms.finalize(), then resolve its Ref from that result.",
                refs=(target.key,),
            )


def declaration_state(
    contexts: Sequence[LoaderContext], *, datasource_irs: tuple[DatasourceIR, ...]
) -> FinalizedDeclarations:
    """Validate and close a physical capture scope using the existing loader owners."""
    from marivo.semantic._compiled_state import build_compiled_state
    from marivo.semantic.loader import _build_registry, _semantic_duplicate_errors
    from marivo.semantic.validator import assembly_validate

    registry, sidecar, build_errors = _build_registry(list(contexts), datasource_irs=datasource_irs)
    validation_errors, _ = assembly_validate(registry, sidecar)
    errors = (*build_errors, *_semantic_duplicate_errors(contexts), *validation_errors)
    if errors:
        error = errors[0]
        raise SemanticExpressionError(
            kind=ErrorKind(error.kind),
            message="Physical declarations cannot be finalized: " + error.message,
            expected=error.expected or "a complete, valid physical dependency closure",
            received=error.received
            or f"{error.kind}: {error.message}; refs={error.semantic_refs!r}",
            action=(error.repair.action if error.repair is not None else str(error))
            + " Complete or repair the referenced declarations before calling ms.finalize() again.",
            refs=error.semantic_refs,
        ) from error
    result = FinalizedDeclarations(
        build_compiled_state(
            registry=registry,
            sidecar=sidecar,
            selected_root_roles=(),
            filtered_domains=(),
        )
    )
    for ctx in contexts:
        if ctx.finalized is None:
            ctx.finalized = result
    return result


def finalize() -> FinalizedDeclarations:
    """Validate and freeze all physical declarations captured in this loader scope.

    Returns: A FinalizedDeclarations result whose resolve(ref) yields exact values.
    Example:
        >>> amount_ref = ms.measure_column(name="amount", entity=orders_ref,
        ...     column="amount", additivity=ms.additive_all())
        >>> finalized = ms.finalize()
        >>> amount = finalized.resolve(amount_ref)
    Constraints: Capture every physical dependency first. Finalization performs no
        source reads, closes capture, and does not publish local Measure/Metric graphs.
    """
    from marivo.semantic._authoring_context import _require_ctx

    ctx = _require_ctx()
    if ctx.finalized is not None:
        return ctx.finalized
    return declaration_state((*ctx.prior_contexts, ctx), datasource_irs=ctx.datasource_irs)


class _Resolver:
    """Definition identity over one compiled or declared semantic state."""

    def __init__(self, state: CompiledSemanticState) -> None:
        self._state = state
        self._registry: Registry = state.registry
        self._versions: dict[Ref[SemanticKindTag], ExactRef] = {}

    def exact(self, target: Ref[SemanticKindTag]) -> ExactRef:
        from marivo.semantic._definition_identity import scoped_definition_fingerprint

        cached = self._versions.get(target)
        if cached is None:
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

    def default_time(self, entity_path: str) -> ExactRef | None:
        """The Entity's unique declared default time field; none when absent."""
        defaults = sorted(
            key
            for key, field in self._registry.dimensions.items()
            if field.entity == entity_path and field.is_time_dimension and field.is_default
        )
        return self.coordinate(defaults[0]) if len(defaults) == 1 else None

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
        elif isinstance(definition, DimensionIR):
            if definition.granularity is not None:
                details.append(("granularity", definition.granularity))
            if definition.parse is not None:
                details.append(("parse", repr(definition.parse)))
            if definition.is_default:
                details.append(("default_time", "true"))
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

    def entity(self, path: str) -> Entity:
        target = ref_factory.entity(path)
        return Entity(
            ref=self.entity_ref(path),
            primary_key=self._registry.entities[path].primary_key,
            default_time=self.default_time(path),
            _facts=self.facts(target),
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
            default_time=self.default_time(measure.entity),
            coordinates=tuple(
                self.coordinate(path)
                for path, field in sorted(self._registry.dimensions.items())
                if field.entity == measure.entity
            ),
            _facts=self.facts(ref_factory.measure(path)),
            _versioned=self._registry.entities[measure.entity].versioning is not None,
        )

    def field(self, path: str) -> Dimension | TimeField:
        field = self._registry.dimensions[path]
        root = self.entity_ref(field.entity)
        default = self.default_time(field.entity)
        ref = self.coordinate(path)
        facts = self.facts(ref.ref)
        versioned = self._registry.entities[field.entity].versioning is not None
        if field.is_time_dimension:
            return TimeField(
                ref=ref, root=root, default_time=default, _facts=facts, _versioned=versioned
            )
        return Dimension(
            ref=ref, root=root, default_time=default, _facts=facts, _versioned=versioned
        )

    def metric(self, path: str) -> Deferred:
        """Return a qualified declaration view without changing its captured source."""
        from marivo._definition.deferred import DeclarationResolver, resolver_of

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

    def relationship(self, path: str) -> Relationship:
        contract: TargetRelationshipContract = normalize_target_relationship(self._registry, path)
        return Relationship(
            ref=self.exact(ref_factory.relationship(path)),
            from_entity=self.entity_ref(contract.from_entity_ref.path),
            to_entity=self.entity_ref(contract.to_entity_ref.path),
            cardinality=contract.cardinality,
            _facts=self.facts(ref_factory.relationship(path)),
        )


@overload
def resolve(
    catalog: SemanticCatalog | FinalizedDeclarations, target: Ref[EntityKind]
) -> Entity: ...


@overload
def resolve(
    catalog: SemanticCatalog | FinalizedDeclarations, target: Ref[DimensionKind]
) -> Deferred: ...


@overload
def resolve(
    catalog: SemanticCatalog | FinalizedDeclarations, target: Ref[TimeDimensionKind]
) -> Deferred: ...


@overload
def resolve(
    catalog: SemanticCatalog | FinalizedDeclarations, target: Ref[MeasureKind]
) -> Deferred: ...


@overload
def resolve(
    catalog: SemanticCatalog | FinalizedDeclarations, target: Ref[MetricKind]
) -> Deferred: ...


@overload
def resolve(
    catalog: SemanticCatalog | FinalizedDeclarations, target: Ref[RelationshipKind]
) -> Relationship: ...


@overload
def resolve(
    catalog: SemanticCatalog | FinalizedDeclarations, target: Ref[SemanticKindTag]
) -> ResolvedValue: ...


def resolve(
    catalog: SemanticCatalog | FinalizedDeclarations, target: Ref[SemanticKindTag]
) -> ResolvedValue:
    """Resolve one exact ref in ``catalog`` into its immutable expression value.

    ``catalog`` is a loaded Catalog or finalized declarations; both share one
    exact-version function. Unsupported kinds fail with a structured error;
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
    from marivo._definition.deferred import SemanticInput, semantic_input

    if isinstance(value, (Dimension, TimeField, Measure)):
        return semantic_input(
            SemanticInput(
                reference=value.ref,
                root=value.root,
                unit=value.unit if isinstance(value, Measure) else None,
                permission=value.permission if isinstance(value, Measure) else SumNone(),
                default_time=value.default_time,
                coordinates=value.coordinates if isinstance(value, Measure) else (),
                versioned=value._versioned,
                field_read=FieldRead(
                    value.ref, value.root, tuple(step.ref for step in value.path), value.anchor
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


def catalog_metric_definition(
    catalog: SemanticCatalog | FinalizedDeclarations, target: ExactRef
) -> MetricNode | None:
    """The stored body of the published Metric at ``target``'s path; None when absent.

    Lookup is by kind and path in this scope: a reference built against an
    earlier same-path definition reaches the current one.
    """
    declared = catalog._state.registry.declared_metrics.get(target.ref.path)
    if declared is None or target.ref.kind is not SemanticKind.METRIC:
        return None
    from marivo.semantic._deferred_semantics import consume_metric

    return consume_metric(declared).node


_RebindT = TypeVar("_RebindT")


def _rebind_declared(
    value: _RebindT,
    current: Callable[[DeclaredMetricRef], DeclaredMetricRef],
    memo: dict[int, object],
) -> _RebindT:
    """Rebuild ``value`` with every declared Metric reference rebound by ``current``.

    Unchanged subgraphs are returned as the same objects, so shared nodes stay
    shared; the input graph is never mutated.
    """
    if id(value) in memo:
        return cast("_RebindT", memo[id(value)])  # memo stores the rebuilt same-type value
    result: object
    if isinstance(value, DeclaredMetricRef):
        result = current(value)
    elif isinstance(value, tuple):
        items = tuple(_rebind_declared(item, current, memo) for item in value)
        result = items if any(a is not b for a, b in zip(items, value, strict=True)) else value
    elif is_dataclass(value) and not isinstance(value, (type, ExactRef)):
        changes = {
            item.name: rebuilt
            for item in dataclass_fields(value)
            if (rebuilt := _rebind_declared(getattr(value, item.name), current, memo))
            is not getattr(value, item.name)
        }
        result = dataclass_replace(value, **changes) if changes else value
    else:
        result = value
    memo[id(value)] = result
    return cast("_RebindT", result)  # rebinding preserves each value's type


def catalog_closure(
    catalog: SemanticCatalog | FinalizedDeclarations, value: Deferred
) -> DefinitionClosure:
    """Expand ``value`` through the published Metrics of ``catalog`` into one graph.

    Declared Metric references resolve by kind and path in this scope; a
    reference built against an earlier same-path definition is rebound to the
    current definition and version, so the closure describes what is actually
    consumed. ``value`` itself is unchanged. Each ``where`` over a published
    Metric is checked again against its current body. Cycles, unpublished
    Metrics and filters over a now-composite Metric fail with a structured
    error. No source is read.
    """
    state = catalog._state

    def expand(target: ExactRef) -> MetricNode | None:
        return catalog_metric_definition(catalog, target)

    from marivo.semantic._deferred_semantics import consume_metric_in_state

    root = consume_metric_in_state(value, state).node
    try:
        closure = walk((root,), resolve=expand)
    except DefinitionGraphError as error:
        raise SemanticExpressionError.from_definition(error, operation="Metric closure") from error
    return closure


def _node_reads(node: DefinitionNode | FieldPredicate) -> tuple[Ref[SemanticKindTag], ...]:
    return tuple(sorted({exact.ref for exact in direct_refs(node)}, key=lambda ref: ref.key))


def _declaration_location(location: DefinitionSourceLocation | None) -> str | None:
    return f"{location.path}:{location.line}" if location is not None else None


def _expression_contract(value: object) -> ExpressionContract:
    """Bounded metadata disclosure; numerical definitions belong to Catalog/Query."""
    if not isinstance(value, (Entity, Relationship, Dimension, TimeField, Measure)):
        raise TypeError(f"no physical definition contract for {type(value).__name__}")
    facts = value._facts
    if isinstance(value, Entity):
        stage: ExpressionStage = "entity"
        roots: tuple[ExactRef, ...] = (value.ref,)
        operations: tuple[str, ...] = ("count",)
    elif isinstance(value, Relationship):
        stage, roots, operations = "relationship", (value.from_entity, value.to_entity), ("via",)
    else:
        stage = (
            "measure"
            if isinstance(value, Measure)
            else "time_field"
            if isinstance(value, TimeField)
            else "dimension"
        )
        roots, operations = (value.root,), ()
    details = facts.details if facts else ()
    if isinstance(value, Relationship):
        details = (
            ("from", value.from_entity.ref.key),
            ("to", value.to_entity.ref.key),
            ("cardinality", value.cardinality),
            *details,
        )
    return ExpressionContract(
        stage=stage,
        ref=value.ref,
        roots=roots,
        unit=value.unit if isinstance(value, Measure) else None,
        sum_permission=value.permission if isinstance(value, Measure) else None,
        default_time=value.default_time if not isinstance(value, Relationship) else None,
        computation=None,
        reads=facts.reads if facts else (),
        name=facts.name if facts else value.ref.ref.name,
        business_definition=getattr(facts.ai_context, "business_definition", None)
        if facts
        else None,
        guardrails=tuple(getattr(facts.ai_context, "guardrails", ()) or ()) if facts else (),
        location=facts.location if facts else None,
        facts=details,
        operations=operations,
    )
