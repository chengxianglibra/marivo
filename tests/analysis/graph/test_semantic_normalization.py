"""Private lazy semantics derive from authored declarations without source access."""

from collections import UserList
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone

import pytest

from marivo.datasource.ir import (
    AiContextIR,
    DatasourceIR,
    DatasourceSourceLocation,
    JsonSourceIR,
    SourceParamIR,
    TableSourceIR,
)
from marivo.semantic._expression_binding import ExpressionBody
from marivo.semantic.errors import SemanticLoadError
from marivo.semantic.ir import (
    DateParse,
    DimensionIR,
    DimensionKind,
    DomainIR,
    EntityIR,
    EntityVersioningIR,
    MeasureIR,
    SnapshotVersioningIR,
    SourceLocation,
    TargetSnapshotSelection,
    TargetValiditySelection,
    ValidityVersioningIR,
)
from marivo.semantic.validator import (
    Registry,
    normalize_target_dimension,
    normalize_target_entity,
    normalize_target_version_selection,
)

_LOCATION = SourceLocation("declared.py", 1)


def _entity(
    name: str,
    *,
    key: tuple[str, ...] = ("id",),
    version: EntityVersioningIR | None = None,
) -> EntityIR:
    return EntityIR(
        semantic_id=f"sales.{name}",
        domain="sales",
        name=name,
        datasource="warehouse",
        source=JsonSourceIR(
            path="https://example.test/facts",
            columns=(
                ("id", "id"),
                ("tenant", "tenant"),
                ("amount", "amount"),
                ("weight", "weight"),
                ("day", "day"),
                ("start", "start"),
                ("end", "end"),
            ),
            query_params=(("region", SourceParamIR("region")),),
        ),
        primary_key=key,
        ai_context=AiContextIR(),
        python_symbol=name,
        location=_LOCATION,
        versioning=version,
    )


def _registry() -> Registry:
    registry = Registry()
    registry.domains["sales"] = DomainIR("sales", "sales", True, AiContextIR(), _LOCATION)
    registry.datasources["warehouse"] = DatasourceIR(
        "warehouse",
        "warehouse",
        "duckdb",
        {},
        {"http_bearer_token": "API_TOKEN"},
        AiContextIR(),
        "warehouse",
        DatasourceSourceLocation("declared.py", 1),
    )
    for name in ("orders", "customers", "visits"):
        entity = _entity(name)
        registry.entities[entity.semantic_id] = entity
        for column in ("day", "start", "end"):
            path = f"{entity.semantic_id}.{column}"
            registry.dimensions[path] = DimensionIR(
                path,
                "sales",
                entity.semantic_id,
                column,
                AiContextIR(),
                True,
                DimensionKind.TIME,
                column,
                _LOCATION,
                granularity="day",
                parse=DateParse(),
                is_default=column == "day",
                source_column=column,
                body_ast_hash=ExpressionBody.for_column(column).body_ast_hash,
            )
        for column in ("amount", "weight"):
            path = f"{entity.semantic_id}.{column}"
            registry.measures[path] = MeasureIR(
                path,
                "sales",
                entity.semantic_id,
                column,
                AiContextIR(),
                "additive",
                "USD" if column == "amount" else None,
                column,
                _LOCATION,
                body_ast_hash=ExpressionBody.for_column(column).body_ast_hash,
            )
    return registry


def test_normalized_entity_defers_unknown_source_columns_until_physical_binding() -> None:
    registry = _registry()
    entity = registry.entities["sales.orders"]
    assert isinstance(entity.source, JsonSourceIR)

    normalized = normalize_target_entity(registry, entity.semantic_id)

    assert dict(normalized.columns)["amount"] == "unknown"


def test_normalized_time_dimension_preserves_declared_type_and_role() -> None:
    registry = _registry()

    normalized = normalize_target_dimension(registry, "sales.orders.day")

    assert normalized.logical_type == "date"
    assert normalized.is_default
    assert normalized.granularity == "day"


def test_entity_identity_is_ordered_and_independent_of_version_row_key() -> None:
    registry = _registry()
    registry.entities["sales.orders"] = _entity("orders", key=("tenant", "id"))
    normalized = normalize_target_entity(registry, "sales.orders")
    assert normalized.primary_key == ("tenant", "id")
    assert normalized.identity_signature == (("tenant", "unknown"), ("id", "unknown"))
    assert normalized.version_row_key == ("tenant", "id")
    assert normalized.credential_slots == ("http_bearer_token",)
    assert normalized.datasource_ref.path == "warehouse"
    assert normalize_target_entity(registry, "sales.customers").identity_signature == (
        ("id", "unknown"),
    )
    with pytest.raises(FrozenInstanceError):
        normalized.__setattr__("primary_key", ())
    registry.entities["sales.orders"] = _entity("orders", key=())
    unkeyed = normalize_target_entity(registry, "sales.orders")
    assert unkeyed.primary_key == ()
    assert unkeyed.identity_signature == ()


def test_normalized_source_detaches_authored_mutable_query_sequences() -> None:
    registry = _registry()
    values: UserList[str | int | float | bool | SourceParamIR] = UserList([SourceParamIR("region")])
    entity = registry.entities["sales.orders"]
    assert isinstance(entity.source, JsonSourceIR)
    registry.entities[entity.semantic_id] = replace(
        entity,
        source=replace(entity.source, query_params=(("region", values),)),
    )
    normalized = normalize_target_entity(registry, entity.semantic_id)
    assert isinstance(normalized.source, JsonSourceIR)
    values.append("later")
    assert normalized.source.query_params == (("region", (SourceParamIR("region"),)),)


def test_normalized_entity_preserves_datasource_identity() -> None:
    registry = _registry()
    first = normalize_target_entity(registry, "sales.orders")
    registry.entities["sales.orders"] = replace(
        registry.entities["sales.orders"], datasource="other"
    )
    registry.datasources["other"] = replace(
        registry.datasources["warehouse"], semantic_id="other", name="other"
    )
    second = normalize_target_entity(registry, "sales.orders")
    assert first != second
    assert first.datasource_ref.path == "warehouse" and second.datasource_ref.path == "other"
    assert first.dependency_fingerprint != second.dependency_fingerprint
    fingerprint_before_change = second.dependency_fingerprint
    registry.datasources["other"].fields["path"] = "changed.duckdb"
    changed = normalize_target_entity(registry, "sales.orders")
    assert changed.dependency_fingerprint != fingerprint_before_change
    assert second.dependency_fingerprint == fingerprint_before_change


def test_snapshot_normalization_preserves_exact_left_limit() -> None:
    registry = _registry()
    registry.entities["sales.orders"] = _entity(
        "orders",
        version=SnapshotVersioningIR("snapshot", "sales.orders.day", "day", "Asia/Shanghai"),
    )
    normalized = normalize_target_entity(registry, "sales.orders")
    assert normalized.primary_key == ("id",)
    assert normalized.version_row_key == ("id", "day")
    boundary = datetime(2026, 2, 1, 16, tzinfo=timezone.utc)
    instant = normalize_target_version_selection(
        normalized, boundary=boundary, interpretation="instant"
    )
    previous = normalize_target_version_selection(
        normalized, boundary=boundary, interpretation="before_endpoint"
    )
    assert isinstance(instant, TargetSnapshotSelection) and instant.period == "2026-02-02"
    assert isinstance(previous, TargetSnapshotSelection) and previous.period == "2026-02-01"
    within_day = normalize_target_version_selection(
        normalized,
        boundary=boundary.replace(minute=1),
        interpretation="before_endpoint",
    )
    assert isinstance(within_day, TargetSnapshotSelection) and within_day.period == "2026-02-02"


@pytest.mark.parametrize("interval", ["closed_open", "closed_closed"])
def test_validity_exact_endpoint_comparisons(interval: str) -> None:
    registry = _registry()
    assert interval in {"closed_open", "closed_closed"}
    version = ValidityVersioningIR(
        "validity",
        "sales.orders.start",
        "sales.orders.end",
        "closed_open" if interval == "closed_open" else "closed_closed",
        (None, "9999-12-31"),
        "UTC",
    )
    registry.entities["sales.orders"] = _entity("orders", version=version)
    normalized = normalize_target_entity(registry, "sales.orders")
    assert normalized.version_row_key == ("id", "start")
    boundary = datetime(2026, 2, 1, tzinfo=timezone.utc)
    instant = normalize_target_version_selection(
        normalized, boundary=boundary, interpretation="instant"
    )
    before = normalize_target_version_selection(
        normalized, boundary=boundary, interpretation="before_endpoint"
    )
    assert isinstance(instant, TargetValiditySelection) and isinstance(
        before, TargetValiditySelection
    )
    assert instant.boundary == before.boundary == boundary.isoformat()
    assert (before.start_operator, before.end_operator) == ("lt", "ge")
    assert (instant.start_operator, instant.end_operator) == (
        "le",
        "gt" if interval == "closed_open" else "ge",
    )


def test_invalid_identity_and_version_axes_fail_without_guessing_columns() -> None:
    registry = _registry()
    missing_projection = replace(
        _entity("orders", key=("unknown",)),
        source=TableSourceIR("orders", columns=(("other", "other"),)),
    )
    registry.entities["sales.orders"] = missing_projection
    with pytest.raises(SemanticLoadError) as caught:
        normalize_target_entity(registry, "sales.orders")
    assert caught.value.kind == "missing_identity_key_column"
    assert "unknown" in (caught.value.received or "")

    registry.entities["sales.orders"] = _entity("orders", key=("id", "id"))
    with pytest.raises(SemanticLoadError) as caught:
        normalize_target_entity(registry, "sales.orders")
    assert caught.value.kind == "duplicate_identity_key"
    assert "id" in (caught.value.received or "")
    registry.entities["sales.orders"] = _entity(
        "orders",
        version=SnapshotVersioningIR("snapshot", "sales.customers.day", "day"),
    )
    with pytest.raises(SemanticLoadError, match="same Entity"):
        normalize_target_entity(registry, "sales.orders")
    registry.entities["sales.orders"] = _entity(
        "orders",
        key=("id", "day"),
        version=SnapshotVersioningIR("snapshot", "sales.orders.day", "day"),
    )
    with pytest.raises(SemanticLoadError, match="separate from snapshot") as caught:
        normalize_target_entity(registry, "sales.orders")
    assert caught.value.kind == "identity_version_overlap"


def test_identity_diagnostics_prioritize_duplicates_projection_then_snapshot_overlap() -> None:
    registry = _registry()
    version = SnapshotVersioningIR("snapshot", "sales.orders.day", "day")
    untyped = TableSourceIR("orders")
    entity = replace(_entity("orders", key=("id", "id", "day"), version=version), source=untyped)
    registry.entities[entity.semantic_id] = entity

    with pytest.raises(SemanticLoadError) as caught:
        normalize_target_entity(registry, entity.semantic_id)
    assert caught.value.kind == "duplicate_identity_key"
    assert caught.value.received == "duplicate identity keys ('id',)"

    registry.entities[entity.semantic_id] = replace(
        entity,
        primary_key=("id", "day"),
        source=TableSourceIR("orders", columns=(("id", "id"),)),
    )
    with pytest.raises(SemanticLoadError) as caught:
        normalize_target_entity(registry, entity.semantic_id)
    assert caught.value.kind == "missing_identity_key_column"
    assert "day" in (caught.value.received or "")
    assert "Add these identity columns to columns" in (caught.value.hint or "")

    registry.entities[entity.semantic_id] = replace(
        entity,
        primary_key=("id", "day"),
        source=TableSourceIR("orders", columns=(("id", "id"), ("day", "day"))),
    )
    with pytest.raises(SemanticLoadError) as caught:
        normalize_target_entity(registry, entity.semantic_id)
    assert caught.value.kind == "identity_version_overlap"
    assert "day" in (caught.value.received or "")
