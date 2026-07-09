"""Catalog-details to runner adapter types and catalog access helpers.

Internal to ``marivo.analysis.intents`` — extracted from ``observe``.
"""

from __future__ import annotations

from typing import Any, cast

from marivo.analysis.errors import MetricNotFoundError, SemanticKindMismatchError
from marivo.refs import SemanticRef
from marivo.semantic.catalog import (
    DimensionDetails,
    EntityDetails,
    SemanticKind,
    TimeDimensionDetails,
)
from marivo.semantic.ir import HourPrefixParse


class _TimeFieldMetaAdapter:
    """Adapter that mimics the old TimeFieldMeta for runner.py."""

    def __init__(
        self,
        data_type: str,
        granularity: str,
        format: str | None = None,
        required_prefix: str | None = None,
        timezone: str | None = None,
        parse_kind: str | None = None,
        semantic_id: str | None = None,
        name: str | None = None,
    ) -> None:
        self.data_type = data_type
        self.granularity = granularity
        self.format = format
        self.required_prefix = required_prefix
        self.timezone = timezone
        self.parse_kind = parse_kind
        self.semantic_id = semantic_id
        self.name = name


class _DimensionIRAdapter:
    """Adapter that mimics the old DimensionIR for runner.py."""

    def __init__(
        self,
        semantic_id: str,
        name: str,
        dataset_name: str,
        fn: Any,
        *,
        is_time: bool = False,
        is_default: bool = False,
        time_meta: _TimeFieldMetaAdapter | None = None,
        sample_interval: Any | None = None,
    ) -> None:
        self.semantic_id = semantic_id
        self.name = name
        self.dataset_name = dataset_name
        self.fn = fn
        self.is_time = is_time
        self.is_default = is_default
        self.time_meta = time_meta
        self.sample_interval = sample_interval


class _EntityIRAdapter:
    """Adapter that mimics the old EntityIR shape for runner.py window helpers."""

    def __init__(
        self,
        name: str,
        fn: Any,
        datasource_name: str,
        fields: dict[str, _DimensionIRAdapter],
    ) -> None:
        self.name = name
        self.fn = fn
        self.datasource_name = datasource_name
        self.fields = fields


def _catalog_id(ref: str, kind: SemanticKind) -> str:
    return f"{kind.value}.{ref}"


def _catalog_kind(catalog: Any, ref: str) -> SemanticKind | None:
    return cast("SemanticKind | None", catalog._resolve_kind_of(ref, catalog._require_ready()))


def _catalog_object(catalog: Any, ref: str, kind: SemanticKind) -> Any:
    return catalog.get(_catalog_id(ref, kind))


def _entity_details(catalog: Any, ref: str) -> EntityDetails:
    details = _catalog_object(catalog, ref, SemanticKind.ENTITY).details()
    if not isinstance(details, EntityDetails):
        raise MetricNotFoundError(message=f"entity {ref!r} not found", details={"entity": ref})
    return details


def _field_details(catalog: Any, ref: str) -> DimensionDetails | TimeDimensionDetails:
    kind = _catalog_kind(catalog, ref)
    if kind not in {SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION}:
        raise SemanticKindMismatchError(
            message=f"field {ref!r} is not a dimension or time dimension",
            details={"ref": ref, "actual_kind": str(kind) if kind is not None else None},
        )
    details = _catalog_object(catalog, ref, kind).details()
    if not isinstance(details, (DimensionDetails, TimeDimensionDetails)):
        raise SemanticKindMismatchError(
            message=f"field {ref!r} is not a dimension or time dimension",
            details={"ref": ref, "actual_kind": getattr(details, "kind", None)},
        )
    return details


def _fields_for_entity(
    catalog: Any, entity_ref: str
) -> list[DimensionDetails | TimeDimensionDetails]:
    fields: list[DimensionDetails | TimeDimensionDetails] = []
    for kind in (SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION):
        for obj in catalog.list(str(kind), scope=f"entity.{entity_ref}"):
            details = obj.details()
            if isinstance(details, (DimensionDetails, TimeDimensionDetails)):
                fields.append(details)
    return fields


def _build_entity_adapter(
    catalog: Any,
    resolver: Any,
    entity: EntityDetails,
) -> _EntityIRAdapter:
    def _source_fn(_backend: Any, *, _ref: SemanticRef = entity.ref) -> Any:
        return resolver.table(_ref)

    field_adapters: dict[str, _DimensionIRAdapter] = {}
    for field in _fields_for_entity(catalog, entity.ref.id):
        field_ref = field.ref

        def _field_fn(table_arg: Any, *, _ref: SemanticRef = field_ref) -> Any:
            return resolver.dimension_on(_ref, table_arg)

        if isinstance(field, TimeDimensionDetails):
            is_time = True
            # For hour_prefix fields, look up the companion field name
            # from the catalog's internal IR registry.
            required_prefix: str | None = None
            if field.parse_kind == "hour_prefix":
                reg = catalog._reg
                dim_ir = reg.dimensions.get(field.ref.id) if reg else None
                if dim_ir is not None and isinstance(dim_ir.parse, HourPrefixParse):
                    required_prefix = dim_ir.parse.prefix
            # Resolve data_type: when the IR no longer carries data_type on
            # strptime/hour_prefix, infer from parse_kind so the adapter has a
            # usable value before the runner's _ensure_resolved_data_type runs.
            if field.data_type is not None:
                effective_data_type = field.data_type
            elif field.parse_kind in ("strptime", "hour_prefix"):
                effective_data_type = "string"
            elif field.parse_kind is not None:
                effective_data_type = field.parse_kind  # date/datetime/timestamp
            else:
                effective_data_type = "date"  # deferred — resolved later by runner
            time_meta = _TimeFieldMetaAdapter(
                data_type=effective_data_type,
                granularity=field.granularity or "day",
                format=field.format,
                required_prefix=required_prefix,
                timezone=field.timezone,
                parse_kind=field.parse_kind,
                semantic_id=field.ref.id,
                name=field.name,
            )
        else:
            is_time = False
            time_meta = None
        adapter = _DimensionIRAdapter(
            semantic_id=field.ref.id,
            name=field.name,
            dataset_name=entity.name,
            fn=_field_fn,
            is_time=is_time,
            is_default=getattr(field, "is_default", False),
            time_meta=time_meta,
            sample_interval=getattr(field, "sample_interval", None),
        )
        field_adapters[field.name] = adapter
    return _EntityIRAdapter(
        name=entity.name,
        fn=_source_fn,
        datasource_name=entity.datasource.id,
        fields=field_adapters,
    )
