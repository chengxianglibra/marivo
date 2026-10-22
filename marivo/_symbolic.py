"""Dependency-neutral native lookup leaves for semantic authoring."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, NewType, NoReturn, TypeAlias

from ibis.common.bases import FrozenSlotted
from ibis.common.deferred import Deferred, Resolver

from marivo.refs import Ref, SemanticKind, SemanticKindTag
from marivo.refs import ref as ref_factory

DomainSymbol = NewType("DomainSymbol", Deferred)
DatasourceSymbol = NewType("DatasourceSymbol", Deferred)
EntitySymbol = NewType("EntitySymbol", Deferred)
DimensionSymbol = NewType("DimensionSymbol", Deferred)
TimeDimensionSymbol = NewType("TimeDimensionSymbol", Deferred)
MeasureSymbol = NewType("MeasureSymbol", Deferred)
MetricSymbol = NewType("MetricSymbol", Deferred)
RelationshipSymbol = NewType("RelationshipSymbol", Deferred)
PeriodCalendarSymbol = NewType("PeriodCalendarSymbol", Deferred)
TemporalSetSymbol = NewType("TemporalSetSymbol", Deferred)

LookupSymbol: TypeAlias = (
    DomainSymbol
    | DatasourceSymbol
    | EntitySymbol
    | DimensionSymbol
    | TimeDimensionSymbol
    | MeasureSymbol
    | MetricSymbol
    | RelationshipSymbol
    | PeriodCalendarSymbol
    | TemporalSetSymbol
)
_SYMBOL_MARKERS: dict[SemanticKind, Callable[[Deferred], LookupSymbol]] = {
    SemanticKind.DOMAIN: DomainSymbol,
    SemanticKind.DATASOURCE: DatasourceSymbol,
    SemanticKind.ENTITY: EntitySymbol,
    SemanticKind.DIMENSION: DimensionSymbol,
    SemanticKind.TIME_DIMENSION: TimeDimensionSymbol,
    SemanticKind.MEASURE: MeasureSymbol,
    SemanticKind.METRIC: MetricSymbol,
    SemanticKind.RELATIONSHIP: RelationshipSymbol,
    SemanticKind.PERIOD_CALENDAR: PeriodCalendarSymbol,
    SemanticKind.TEMPORAL_SET: TemporalSetSymbol,
}

if TYPE_CHECKING:

    class _FrozenResolver(Resolver):
        def __init__(self, **values: object) -> None: ...
else:

    class _FrozenResolver(FrozenSlotted, Resolver):
        __slots__ = ()


class SymbolResolver(_FrozenResolver):
    """One immutable lookup key; it never consults a live model or source."""

    __slots__ = ("reference",)
    reference: Ref[SemanticKindTag]

    def __init__(self, reference: Ref[SemanticKindTag]) -> None:
        super().__init__(reference=reference)

    def resolve(self, context: dict[str, object]) -> NoReturn:
        raise TypeError("An unbound semantic symbol requires loader binding before analysis.")


def symbol_from_ref(reference: Ref[SemanticKindTag]) -> LookupSymbol:
    """Build a native lookup leaf from a private identity key."""
    return _SYMBOL_MARKERS[reference.kind](Deferred(SymbolResolver(reference)))


def native_symbol_ref(value: object) -> Ref[SemanticKindTag] | None:
    """Extract only a native lookup leaf, never a composite expression."""
    if not isinstance(value, Deferred):
        return None
    resolver = object.__getattribute__(value, "_resolver")
    return resolver.reference if type(resolver) is SymbolResolver else None


class _SymbolFactory:
    __slots__ = ()

    def domain(self, path: str) -> DomainSymbol:
        """Capture one native Domain lookup symbol without looking it up.

        Args:
            path: Exact domain path, such as 'sales'.
        Returns:
            A native Deferred marked as DomainSymbol for domain= identity parameters; it cannot compute values.
        Example:
            ``ms.ref.domain('sales')``.
        Constraints:
            Path shape is checked immediately; existence and semantic facts bind
            in the loader. This does not register a definition or read a source.
            Use the symbol only in matching identity parameters or supported
            Catalog metadata operations. Equality builds a recipe; bool, hashing
            and identity attributes are unsupported.
        """
        return DomainSymbol(symbol_from_ref(ref_factory.domain(path)))

    def datasource(self, path: str) -> DatasourceSymbol:
        """Capture one native Datasource lookup symbol without looking it up.

        Args:
            path: Exact datasource path, such as 'warehouse'.
        Returns:
            A native Deferred marked as DatasourceSymbol for datasource= identity parameters and datasource metadata operations; it cannot compute values.
        Example:
            ``ms.ref.datasource('warehouse')``.
        Constraints:
            Path shape is checked immediately; existence and semantic facts bind
            in the loader. This does not register a definition or read a source.
            Use the symbol only in matching identity parameters or supported
            Catalog metadata operations. Equality builds a recipe; bool, hashing
            and identity attributes are unsupported.
        """
        return DatasourceSymbol(symbol_from_ref(ref_factory.datasource(path)))

    def entity(self, path: str) -> EntitySymbol:
        """Capture one native Entity lookup symbol without looking it up.

        Args:
            path: Exact entity path, such as 'sales.orders'.
        Returns:
            A native Deferred marked as EntitySymbol for Entity identity parameters and count(); it is not a numeric field.
        Example:
            ``ms.ref.entity('sales.orders')``.
        Constraints:
            Path shape is checked immediately; existence and semantic facts bind
            in the loader. This does not register a definition or read a source.
            Obtain closed analysis inputs through the matching Catalog collection's
            .get(path). Equality builds a recipe; bool, hashing and identity
            attributes are unsupported.
        """
        return EntitySymbol(symbol_from_ref(ref_factory.entity(path)))

    def dimension(self, path: str) -> DimensionSymbol:
        """Capture one native Dimension lookup symbol without looking it up.

        Args:
            path: Exact dimension path, such as 'sales.orders.status'.
        Returns:
            A native Deferred marked as DimensionSymbol for categorical field predicates, coordinate parameters, and supported count reductions.
        Example:
            ``ms.ref.dimension('sales.orders.status')``.
        Constraints:
            Path shape is checked immediately; existence and semantic facts bind
            in the loader. This does not register a definition or read a source.
            Obtain closed analysis inputs through the matching Catalog collection's
            .get(path). Equality builds a recipe; bool, hashing and identity
            attributes are unsupported.
        """
        return DimensionSymbol(symbol_from_ref(ref_factory.dimension(path)))

    def time_dimension(self, path: str) -> TimeDimensionSymbol:
        """Capture one native TimeDimension lookup symbol without looking it up.

        Args:
            path: Exact time_dimension path, such as 'sales.orders.paid_at'.
        Returns:
            A native Deferred marked as TimeDimensionSymbol for time fields, time= parameters, predicates, and supported field paths.
        Example:
            ``ms.ref.time_dimension('sales.orders.paid_at')``.
        Constraints:
            Path shape is checked immediately; existence and semantic facts bind
            in the loader. This does not register a definition or read a source.
            Obtain closed analysis inputs through the matching Catalog collection's
            .get(path). Equality builds a recipe; bool, hashing and identity
            attributes are unsupported.
        """
        return TimeDimensionSymbol(symbol_from_ref(ref_factory.time_dimension(path)))

    def measure(self, path: str) -> MeasureSymbol:
        """Capture one native Measure lookup symbol without looking it up.

        Args:
            path: Exact measure path, such as 'sales.orders.amount'.
        Returns:
            A native Deferred marked as MeasureSymbol for numeric row expressions and business-authorized reductions.
        Example:
            ``ms.ref.measure('sales.orders.amount')``.
        Constraints:
            Path shape is checked immediately; existence and semantic facts bind
            in the loader. This does not register a definition or read a source.
            Obtain closed analysis inputs through the matching Catalog collection's
            .get(path). Equality builds a recipe; bool, hashing and identity
            attributes are unsupported.
        """
        return MeasureSymbol(symbol_from_ref(ref_factory.measure(path)))

    def metric(self, path: str) -> MetricSymbol:
        """Capture one native Metric lookup symbol without looking it up.

        Args:
            path: Exact metric path, such as 'sales.revenue'.
        Returns:
            A native Deferred marked as MetricSymbol for completed Metric composition and Metric metadata selection.
        Example:
            ``ms.ref.metric('sales.revenue')``.
        Constraints:
            Path shape is checked immediately; existence and semantic facts bind
            in the loader. This does not register a definition or read a source.
            Obtain closed analysis inputs through the matching Catalog collection's
            .get(path). Equality builds a recipe; bool, hashing and identity
            attributes are unsupported.
        """
        return MetricSymbol(symbol_from_ref(ref_factory.metric(path)))

    def relationship(self, path: str) -> RelationshipSymbol:
        """Capture one native Relationship lookup symbol without looking it up.

        Args:
            path: Exact relationship path, such as 'sales.buyer'.
        Returns:
            A native Deferred marked as RelationshipSymbol for directed relationship path parameters; it cannot compute values.
        Example:
            ``ms.ref.relationship('sales.buyer')``.
        Constraints:
            Path shape is checked immediately; existence and semantic facts bind
            in the loader. This does not register a definition or read a source.
            Use the symbol only in matching identity parameters or supported
            Catalog metadata operations. Equality builds a recipe; bool, hashing
            and identity attributes are unsupported.
        """
        return RelationshipSymbol(symbol_from_ref(ref_factory.relationship(path)))

    def period_calendar(self, path: str) -> PeriodCalendarSymbol:
        """Capture one native PeriodCalendar lookup symbol without looking it up.

        Args:
            path: Exact period_calendar path, such as 'sales.fiscal'.
        Returns:
            A native Deferred marked as PeriodCalendarSymbol for calendar= identity parameters and governed-calendar metadata selection; it cannot compute values.
        Example:
            ``ms.ref.period_calendar('sales.fiscal')``.
        Constraints:
            Path shape is checked immediately; existence and semantic facts bind
            in the loader. This does not register a definition or read a source.
            Use the symbol only in matching identity parameters or supported
            Catalog metadata operations. Equality builds a recipe; bool, hashing
            and identity attributes are unsupported.
        """
        return PeriodCalendarSymbol(symbol_from_ref(ref_factory.period_calendar(path)))

    def temporal_set(self, path: str) -> TemporalSetSymbol:
        """Capture one native TemporalSet lookup symbol without looking it up.

        Args:
            path: Exact temporal_set path, such as 'sales.campaigns'.
        Returns:
            A native Deferred marked as TemporalSetSymbol for temporal-set identity parameters and governed-set metadata selection; it cannot compute values.
        Example:
            ``ms.ref.temporal_set('sales.campaigns')``.
        Constraints:
            Path shape is checked immediately; existence and semantic facts bind
            in the loader. This does not register a definition or read a source.
            Use the symbol only in matching identity parameters or supported
            Catalog metadata operations. Equality builds a recipe; bool, hashing
            and identity attributes are unsupported.
        """
        return TemporalSetSymbol(symbol_from_ref(ref_factory.temporal_set(path)))


symbol_factory = _SymbolFactory()
