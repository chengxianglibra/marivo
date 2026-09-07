"""Exact selector values over the current immutable Dataset schema."""

from __future__ import annotations

from typing import TYPE_CHECKING, SupportsIndex, final

from marivo._compat import Never, Self
from marivo.analysis.datasets.descriptors import (
    DatasetField,
    DatasetFieldId,
    _CatalogFieldIdentity,
    _field_binding_fingerprint,
    _RuntimeMetricFieldIdentity,
)
from marivo.analysis.datasets.errors import DatasetFieldSelectionError, DatasetOwnershipError
from marivo.refs import DimensionKind, MetricKind, Ref, SemanticKind, TimeDimensionKind

if TYPE_CHECKING:
    from marivo.analysis.datasets.base import Dataset
    from marivo.semantic.catalog import DimensionEntry, MetricEntry, TimeDimensionEntry
    from marivo.semantic.runtime_metric import RuntimeMetricExpr


@final
class DatasetFieldRef:
    """One immutable Session-owned selector, created by ``dataset.fields``."""

    __slots__ = ("field_binding_fingerprint", "field_id", "owning_session_id")

    owning_session_id: str
    field_id: DatasetFieldId
    field_binding_fingerprint: str

    def __new__(cls, *args: object, **kwargs: object) -> Self:
        del cls, args, kwargs
        raise TypeError("DatasetFieldRef has no public constructor; use dataset.fields.get(...).")

    def __init__(self, _sealed: Never, /) -> None:
        raise AssertionError("DatasetFieldRef initialization is unreachable")

    def __init_subclass__(cls, **kwargs: object) -> Never:
        del cls, kwargs
        raise TypeError("DatasetFieldRef is sealed and cannot be subclassed.")

    def __setattr__(self, name: str, value: object) -> Never:
        del name, value
        raise AttributeError("DatasetFieldRef instances are immutable")

    def __delattr__(self, name: str) -> Never:
        del name
        raise AttributeError("DatasetFieldRef instances are immutable")

    def __eq__(self, other: object) -> Never:
        del other
        raise TypeError("DatasetFieldRef is selector-only; pass it to a typed Dataset consumer.")

    def __ne__(self, other: object) -> Never:
        del other
        raise TypeError("DatasetFieldRef is selector-only; pass it to a typed Dataset consumer.")

    def __bool__(self) -> Never:
        raise TypeError("DatasetFieldRef has no truth value; pass it to a typed Dataset consumer.")

    def __reduce_ex__(self, protocol: SupportsIndex) -> Never:
        del protocol
        raise TypeError("DatasetFieldRef cannot be pickled; reacquire it from dataset.fields.")

    def __repr__(self) -> str:
        return (
            f"<DatasetFieldRef field={self.field_id.value[:64]} "
            f"session={self.owning_session_id[:48]} binding={self.field_binding_fingerprint[:16]}>"
        )


@final
class DatasetFields:
    """Resolve exact current field bindings without reading or computing values."""

    __slots__ = ("_dataset",)

    _dataset: Dataset

    def __new__(cls, *args: object, **kwargs: object) -> Self:
        del cls, args, kwargs
        raise TypeError("DatasetFields has no public constructor; use dataset.fields.")

    def __init__(self, _sealed: Never, /) -> None:
        raise AssertionError("DatasetFields initialization is unreachable")

    def __init_subclass__(cls, **kwargs: object) -> Never:
        del cls, kwargs
        raise TypeError("DatasetFields is sealed and cannot be subclassed.")

    def __setattr__(self, name: str, value: object) -> Never:
        del name, value
        raise AttributeError("DatasetFields instances are immutable")

    def __delattr__(self, name: str) -> Never:
        del name
        raise AttributeError("DatasetFields instances are immutable")

    def __reduce_ex__(self, protocol: SupportsIndex) -> Never:
        del protocol
        raise TypeError("DatasetFields cannot be pickled; use dataset.fields.")

    def metric(self, metric: Ref[MetricKind] | MetricEntry | RuntimeMetricExpr) -> DatasetFieldRef:
        """Select a retained Metric binding without reading it.

        Args:
            metric: Exact retained Metric ref, owning-catalog entry, or retained
                in-process runtime Metric expression.
        Returns:
            A selector owned by this Dataset's execution Session.
        Example:
            ``selector = dataset.fields.metric(revenue)``
        Constraints:
            Identity must resolve uniquely; entries require the owning catalog.
        """
        from marivo.semantic.catalog import MetricEntry
        from marivo.semantic.runtime_metric import RuntimeMetricExpr

        if isinstance(metric, Ref):
            if metric.kind is not SemanticKind.METRIC:
                raise _selection_error(self._dataset, "a Metric ref", metric.kind.value)
            return self._by_identity(metric.key, ("metric",))
        if isinstance(metric, MetricEntry):
            self._check_catalog(metric._catalog)
            return self._by_identity(metric.ref.key, ("metric",))
        if isinstance(metric, RuntimeMetricExpr):
            field_ids = tuple(
                field_id
                for expression, field_id in self._dataset._owner.runtime_metric_bindings
                if expression is metric
            )
            candidates = tuple(
                field
                for field in self._dataset.schema.columns
                if field.field_id.value in field_ids
                and isinstance(field.identity, _RuntimeMetricFieldIdentity)
            )
            return self._resolve(candidates, "retained runtime Metric identity", ("metric",))
        raise _selection_error(
            self._dataset, "a Metric ref, entry, or runtime expression", type(metric).__name__
        )

    def dimension(
        self,
        dimension: Ref[DimensionKind]
        | DimensionEntry
        | Ref[TimeDimensionKind]
        | TimeDimensionEntry,
    ) -> DatasetFieldRef:
        """Select an exact retained Dimension or TimeDimension binding.

        Args:
            dimension: A retained dimension ref or entry from the owning catalog.
        Returns:
            A selector owned by this Dataset's execution Session.
        Example:
            ``selector = dataset.fields.dimension(region)``
        Constraints:
            This matches semantic identity, never a display name or semantic path.
        """
        from marivo.semantic.catalog import DimensionEntry, TimeDimensionEntry

        if isinstance(dimension, Ref):
            if dimension.kind not in {SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION}:
                raise _selection_error(
                    self._dataset, "a Dimension or TimeDimension ref", dimension.kind.value
                )
            return self._by_identity(dimension.key, ("dimension", "time_dimension"))
        if isinstance(dimension, DimensionEntry | TimeDimensionEntry):
            self._check_catalog(dimension._catalog)
            return self._by_identity(dimension.ref.key, ("dimension", "time_dimension"))
        raise _selection_error(
            self._dataset, "a Dimension or TimeDimension ref or entry", type(dimension).__name__
        )

    def get(self, key: DatasetFieldId | str) -> DatasetFieldRef:
        """Select one exact field ID or current public field name.

        Args:
            key: A schema field ID or exact current public name.
        Returns:
            An immutable selector for the single matching current binding.
        Example:
            ``selector = dataset.fields.get("item_id")``
        Constraints:
            Strings are names only; lookup never parses semantic paths or keys.
        """
        if isinstance(key, DatasetFieldId):
            matches = tuple(
                field for field in self._dataset.schema.columns if field.field_id == key
            )
            received = key.value
        elif type(key) is str:
            matches = tuple(field for field in self._dataset.schema.columns if field.name == key)
            received = key[:128]
        else:
            raise _selection_error(
                self._dataset, "a DatasetFieldId or exact public field name", type(key).__name__
            )
        return self._resolve(matches, received)

    def _check_catalog(self, catalog: object) -> None:
        if catalog is not self._dataset._owner.catalog_identity:
            raise DatasetOwnershipError(
                expected="an entry from the Dataset-owning Session catalog",
                received="an entry owned by a different catalog",
                repair="Use the owning catalog entry, a retained semantic ref, or dataset.fields.get(...).",
                location="dataset.fields",
            )

    def _by_identity(self, identity_id: str, roles: tuple[str, ...]) -> DatasetFieldRef:
        matches = tuple(
            field
            for field in self._dataset.schema.columns
            if isinstance(field.identity, _CatalogFieldIdentity)
            and field.identity.identity_id == identity_id
        )
        return self._resolve(matches, identity_id, roles)

    def _resolve(
        self,
        matches: tuple[DatasetField, ...],
        received: str,
        roles: tuple[str, ...] = (),
    ) -> DatasetFieldRef:
        if len(matches) != 1:
            raise _selection_error(
                self._dataset,
                "one exact current field binding",
                f"{received}: {len(matches)} matches",
            )
        field = matches[0]
        if roles and field.role_id not in roles:
            raise _selection_error(self._dataset, f"field role in {roles}", field.role_id)
        return _make_field_ref(self._dataset, field)

    def __repr__(self) -> str:
        return f"<DatasetFields family={self._dataset.kind[:64]}; use .get(...), .metric(...), .dimension(...)>"


def _selection_error(dataset: Dataset, expected: str, received: str) -> DatasetFieldSelectionError:
    present = ", ".join(
        f"{field.name} ({field.field_id.value})" for field in dataset.schema.columns[:8]
    )
    omitted = len(dataset.schema.columns) - 8
    if omitted > 0:
        present += f", +{omitted} more"
    return DatasetFieldSelectionError(
        expected=expected,
        received=received,
        repair=f"Inspect dataset.schema.columns and select one current binding with dataset.fields.get(...). Present: {present or '(none)'}.",
        location="dataset.fields",
    )


def _make_field_ref(dataset: Dataset, field: DatasetField) -> DatasetFieldRef:
    selector = object.__new__(DatasetFieldRef)
    object.__setattr__(selector, "owning_session_id", dataset._owner.session_id)
    object.__setattr__(selector, "field_id", field.field_id)
    object.__setattr__(selector, "field_binding_fingerprint", _field_binding_fingerprint(field))
    return selector


def make_fields(dataset: Dataset) -> DatasetFields:
    resolver = object.__new__(DatasetFields)
    object.__setattr__(resolver, "_dataset", dataset)
    return resolver


def validate_field_ref(
    dataset: Dataset,
    selector: DatasetFieldRef,
    allowed_roles: tuple[str, ...] = (),
) -> DatasetField:
    """Admit one selector against its corresponding input, Session first."""
    if not isinstance(selector, DatasetFieldRef):
        raise _selection_error(
            dataset, "a DatasetFieldRef from dataset.fields", type(selector).__name__
        )
    if selector.owning_session_id != dataset._owner.session_id:
        raise DatasetOwnershipError(
            expected=f"execution Session {dataset._owner.session_id}",
            received=f"execution Session {selector.owning_session_id}",
            repair="Reacquire the selector from the corresponding input Dataset's fields resolver.",
            location="dataset.fields",
        )
    matches = tuple(
        field for field in dataset.schema.columns if field.field_id == selector.field_id
    )
    if len(matches) != 1:
        raise _selection_error(
            dataset, "one current field with the selector's exact ID", selector.field_id.value
        )
    field = matches[0]
    if _field_binding_fingerprint(field) != selector.field_binding_fingerprint:
        raise _selection_error(
            dataset,
            "the exact retained field binding",
            f"stale binding for {selector.field_id.value}",
        )
    if allowed_roles and field.role_id not in allowed_roles:
        raise _selection_error(dataset, f"field role in {allowed_roles}", field.role_id)
    return field
