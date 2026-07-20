"""Snapshot and validity versioning resolution for the observe planner.

Internal to ``marivo.analysis.intents`` — extracted from ``observe_planner``.

``_utc_now`` lives here (next to ``_derive_version_mode``, the only caller) so
tests can monkeypatch ``marivo.analysis.intents.observe_planner._observe_planner_versioning._utc_now``.
"""

from __future__ import annotations

import hashlib
import json
import operator
from datetime import date, datetime, timedelta
from functools import reduce
from typing import Any, Literal
from zoneinfo import ZoneInfo

import ibis

from marivo.analysis.executor.runner import apply_slice_to_dataset, execute
from marivo.analysis.intents._observe_planner_catalog import _fields_for_entity
from marivo.analysis.intents._observe_planner_joins import _field_fn
from marivo.analysis.intents._observe_planner_types import PlannerField, _planned_field
from marivo.analysis.intents.observe_errors import raise_observe_planning_error
from marivo.analysis.windows.spec import is_date_only
from marivo.semantic.catalog import SemanticCatalog, TimeDimensionDetails
from marivo.semantic.ir import ValidityVersioningIR


def _anchor_date(resolved_window: Any | None, timezone: str | None) -> date:
    if resolved_window is not None and getattr(resolved_window, "end", None) is not None:
        end = resolved_window.end
        if isinstance(end, datetime):
            return end.astimezone(ZoneInfo(timezone or "UTC")).date()
        if isinstance(end, date):
            return end
        end_str = str(end)
        anchor = datetime.fromisoformat(end_str).date()
        if is_date_only(end_str):
            anchor -= timedelta(days=1)
        return anchor
    return datetime.now(ZoneInfo(timezone or "UTC")).date()


def _utc_now() -> datetime:
    """Indirection so tests can monkeypatch plan-time anchor."""
    return datetime.now(tz=ZoneInfo("UTC"))


def _resolved_target_timezone(target_versioning: Any) -> str:
    return getattr(target_versioning, "timezone", None) or "UTC"


def _derive_version_mode(
    *,
    root_time_dimension: Any | None,
    target_versioning: Any,
    resolved_window: Any | None,
) -> tuple[
    Literal["latest", "as_of_root_time"],
    Literal["timescope_end", "as_of_current_time", "root"],
    date | None,
]:
    qualifying = (
        root_time_dimension is not None
        and getattr(root_time_dimension, "data_type", None)
        in {
            "date",
            "timestamp",
        }
    ) or (
        root_time_dimension is not None
        and getattr(root_time_dimension, "data_type", None) is None
        and getattr(root_time_dimension, "parse_kind", None) is None
    )
    if qualifying:
        return ("as_of_root_time", "root", None)
    target_tz = ZoneInfo(_resolved_target_timezone(target_versioning))
    if resolved_window is not None and getattr(resolved_window, "end", None) is not None:
        end = resolved_window.end
        if isinstance(end, datetime):
            anchor = end.astimezone(target_tz).date()
        elif isinstance(end, date):
            anchor = end
        else:
            end_str = str(end)
            anchor = datetime.fromisoformat(end_str).date()
            if is_date_only(end_str):
                anchor -= timedelta(days=1)
        return ("latest", "timescope_end", anchor)
    return ("latest", "as_of_current_time", _utc_now().astimezone(target_tz).date())


def _format_snapshot_partition(anchor: date, fmt: str | None) -> Any:
    if fmt is None:
        return anchor
    return anchor.strftime(fmt)


def _root_time_dimension(
    catalog: SemanticCatalog, root_entity_id: str, *, explicit_time_dimension: Any | None
) -> PlannerField | None:
    if explicit_time_dimension is not None:
        return _planned_field(explicit_time_dimension)
    candidates = [
        field
        for field in _fields_for_entity(catalog, root_entity_id)
        if isinstance(field, TimeDimensionDetails)
    ]
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    defaults = [tf for tf in candidates if getattr(tf, "is_default", False)]
    if len(defaults) == 1:
        return defaults[0]
    return candidates[0]


def _parse_partition_value(raw: Any, *, fmt: str | None) -> date:
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    # Handle pandas Timestamp (has .date() method but is not a datetime subclass)
    if hasattr(raw, "date") and callable(raw.date):
        result = raw.date()
        if isinstance(result, date):
            return result
        return datetime.fromisoformat(str(result)).date()
    if fmt is not None:
        return datetime.strptime(str(raw), fmt).date()
    return datetime.fromisoformat(str(raw)).date()


def _discover_anchor_dates(
    *,
    root_table: Any,
    time_field_expr: Any,
    datasource_name: str,
    session: Any,
) -> list[date]:
    expr = time_field_expr.cast("timestamp").cast("date").name("anchor_date")
    df = execute(
        root_table.select(expr).distinct(),
        datasource_name=datasource_name,
        cache=session._connection_runtime,
        session_id=session.id,
    ).df
    result: list[date] = []
    for raw in df["anchor_date"].tolist():
        if raw is None:
            continue
        if isinstance(raw, datetime):
            result.append(raw.date())
        elif isinstance(raw, date):
            result.append(raw)
        else:
            # pandas Timestamp or similar
            result.append(_parse_partition_value(raw, fmt=None))
    return sorted(set(result))


def _discover_available_partitions(
    *,
    snapshot_table: Any,
    partition_field_local: str,
    fmt: str | None,
    datasource_name: str,
    session: Any,
) -> list[date]:
    df = execute(
        snapshot_table.select(snapshot_table[partition_field_local].name("p")).distinct(),
        datasource_name=datasource_name,
        cache=session._connection_runtime,
        session_id=session.id,
    ).df
    return sorted({_parse_partition_value(p, fmt=fmt) for p in df["p"].tolist() if p is not None})


def _build_anchor_partition_mapping(
    anchor_dates: list[date],
    available_partitions: list[date],
    *,
    snapshot_dataset_id: str,
) -> dict[date, date]:
    if not available_partitions:
        raise_observe_planning_error(
            code="snapshot-partition-missing",
            message=f"Snapshot dataset {snapshot_dataset_id!r} has no available partitions.",
            candidates={
                "dataset": snapshot_dataset_id,
                "missing_anchors": [str(a) for a in anchor_dates],
                "min_available_partition": None,
                "max_available_partition": None,
            },
            repair=[],
        )
    sorted_partitions = sorted(available_partitions)
    mapping: dict[date, date] = {}
    missing: list[date] = []
    for anchor in anchor_dates:
        eligible = [p for p in sorted_partitions if p <= anchor]
        if not eligible:
            missing.append(anchor)
            continue
        mapping[anchor] = eligible[-1]
    if missing:
        raise_observe_planning_error(
            code="snapshot-partition-missing",
            message=(
                f"No partition <= anchor exists for snapshot {snapshot_dataset_id!r}: "
                f"missing {len(missing)} anchor(s)."
            ),
            candidates={
                "dataset": snapshot_dataset_id,
                "missing_anchors": [str(a) for a in missing],
                "min_available_partition": str(sorted_partitions[0]),
                "max_available_partition": str(sorted_partitions[-1]),
            },
            repair=[],
        )
    return mapping


def _mapping_digest(mapping: dict[date, date]) -> str:
    payload = json.dumps(
        sorted([(str(k), str(v)) for k, v in mapping.items()]),
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()


def _resolve_snapshot_as_of_root_time(
    *,
    catalog: SemanticCatalog,
    session: Any,
    datasource_name: str,
    snapshot_dataset_id: str,
    snapshot_versioning: Any,
    snapshot_table: Any,
    root_table: Any,
    root_time_dimension: Any | None,
    anchor_source: str,
) -> tuple[Any, dict[str, Any], dict[date, date]]:
    if root_time_dimension is None:
        raise_observe_planning_error(
            code="unsupported-as-of-root-time",
            message=(
                f"Snapshot {snapshot_dataset_id!r} as_of_root_time requires a "
                "day-level root time field."
            ),
            candidates={"snapshot_dataset": snapshot_dataset_id},
            repair=[],
        )
    target_tz = _resolved_target_timezone(snapshot_versioning)
    time_field_fn = _field_fn(catalog, root_time_dimension.ref.path)
    time_field_expr = time_field_fn(root_table)
    anchor_dates = _discover_anchor_dates(
        root_table=root_table,
        time_field_expr=time_field_expr,
        datasource_name=datasource_name,
        session=session,
    )
    partition_local = snapshot_versioning.partition_field.rsplit(".", 1)[-1]
    available = _discover_available_partitions(
        snapshot_table=snapshot_table,
        partition_field_local=partition_local,
        fmt=snapshot_versioning.format,
        datasource_name=datasource_name,
        session=session,
    )
    mapping = _build_anchor_partition_mapping(
        anchor_dates, available, snapshot_dataset_id=snapshot_dataset_id
    )
    encoded = {
        anchor: _format_snapshot_partition(part, snapshot_versioning.format)
        for anchor, part in mapping.items()
    }
    schema: dict[str, str] = {
        "anchor_date": "date",
        "partition_value": "string" if snapshot_versioning.format else "date",
    }
    mapping_table = ibis.memtable(
        [{"anchor_date": a, "partition_value": p} for a, p in encoded.items()],
        schema=schema,
    )
    digest = _mapping_digest(mapping)
    annotated_snapshot = snapshot_table.inner_join(
        mapping_table,
        snapshot_table[partition_local] == mapping_table.partition_value,
    ).drop("partition_value")
    meta: dict[str, Any] = {
        "dataset": snapshot_dataset_id,
        "kind": "snapshot",
        "mode": "as_of_root_time",
        "anchor_source": anchor_source,
        "anchor_value": None,
        "resolved_partition": None,
        "resolved_partition_summary": {
            "anchor_count": len(anchor_dates),
            "min_anchor": str(min(anchor_dates)) if anchor_dates else None,
            "max_anchor": str(max(anchor_dates)) if anchor_dates else None,
            "partition_count": len(set(mapping.values())),
        },
        "anchor_to_partition_mapping_digest": digest,
        "resolved_interval_predicate": None,
        "timezone": target_tz,
    }
    return annotated_snapshot, meta, mapping


def _resolve_snapshot_versioning(
    *,
    catalog: SemanticCatalog,
    session: Any,
    datasource_name: str,
    snapshot_dataset_id: str,
    snapshot_versioning: Any,
    snapshot_table: Any,
    snapshot_dataset_ir: Any,
    root_table: Any,
    root_time_dimension: Any,
    resolved_window: Any,
) -> tuple[Any, dict[str, Any], dict[date, date] | None]:
    mode, anchor_source, anchor_value = _derive_version_mode(
        root_time_dimension=root_time_dimension,
        target_versioning=snapshot_versioning,
        resolved_window=resolved_window,
    )
    partition_local = snapshot_versioning.partition_field.rsplit(".", 1)[-1]
    target_tz = _resolved_target_timezone(snapshot_versioning)
    if mode == "latest":
        assert anchor_value is not None, "latest mode always provides an anchor_value"
        partition_value = _format_snapshot_partition(anchor_value, snapshot_versioning.format)
        next_table = apply_slice_to_dataset(
            snapshot_table,
            {partition_local: partition_value},
            dataset_ir=snapshot_dataset_ir,
        )
        meta: dict[str, Any] = {
            "dataset": snapshot_dataset_id,
            "kind": "snapshot",
            "mode": "latest",
            "anchor_source": anchor_source,
            "anchor_value": str(anchor_value),
            "resolved_partition": partition_value,
            "resolved_partition_summary": None,
            "anchor_to_partition_mapping_digest": None,
            "resolved_interval_predicate": None,
            "timezone": target_tz,
        }
        return next_table, meta, None
    return _resolve_snapshot_as_of_root_time(
        catalog=catalog,
        session=session,
        datasource_name=datasource_name,
        snapshot_dataset_id=snapshot_dataset_id,
        snapshot_versioning=snapshot_versioning,
        snapshot_table=snapshot_table,
        root_table=root_table,
        root_time_dimension=root_time_dimension,
        anchor_source=anchor_source,
    )


def _validity_open_end_predicate(table: Any, versioning: ValidityVersioningIR) -> Any:
    """Boolean predicate that selects validity rows currently open (matching any open_end sentinel)."""
    valid_to_local = versioning.valid_to.rsplit(".", 1)[-1]
    column = table[valid_to_local]
    parts: list[Any] = []
    for sentinel in versioning.open_end:
        if sentinel is None:
            parts.append(column.isnull())
        else:
            parts.append(column == sentinel)
    # defense-in-depth: empty open_end is rejected by validity() author-time but reduce() needs an initial value
    return reduce(operator.or_, parts, ibis.literal(False))


def _resolve_validity_as_of_predicate(
    *,
    catalog: SemanticCatalog,
    current_table: Any,
    root_time_dimension: Any | None,
    validity_table: Any,
    validity_versioning: ValidityVersioningIR,
    validity_dataset_id: str,
) -> Any:
    """Return a per-row boolean predicate for as_of_root_time validity joins.

    The predicate checks that the root row's time field falls within the
    validity interval.  Key equalities are handled separately by _join_table.
    """
    # Defense-in-depth: _derive_version_mode only picks as_of_root_time when root_time_dimension is qualifying. This guard is unreachable on the current call path.
    if root_time_dimension is None:
        raise_observe_planning_error(
            code="unsupported-as-of-root-time",
            message=(
                f"Validity {validity_dataset_id!r} as_of_root_time requires a "
                "day-level root time field."
            ),
            candidates={"validity_dataset": validity_dataset_id},
            repair=[],
        )
    valid_from_local = validity_versioning.valid_from.rsplit(".", 1)[-1]
    valid_to_local = validity_versioning.valid_to.rsplit(".", 1)[-1]
    anchor = _field_fn(catalog, root_time_dimension.ref.path)(current_table).cast("date")
    valid_from = validity_table[valid_from_local]
    valid_to_raw = validity_table[valid_to_local]
    open_end = _validity_open_end_predicate(validity_table, validity_versioning)
    if validity_versioning.interval == "closed_open":
        upper = open_end | (valid_to_raw > anchor)
    else:
        upper = open_end | (valid_to_raw >= anchor)
    lower = valid_from <= anchor
    return lower & upper


def _resolve_validity_versioning(
    *,
    root_table: Any,  # root_table is used in the as_of_root_time branch; the latest branch ignores it
    root_time_dimension: Any | None,
    validity_table: Any,
    validity_versioning: ValidityVersioningIR,
    validity_dataset_id: str,
    resolved_window: Any | None,
) -> tuple[Any, dict[str, Any], bool]:
    """Resolve validity versioning for the join.

    Returns (table, version_meta, is_as_of) where is_as_of is True only when the as_of_root_time branch ran.
    The latest branch filters by `_validity_open_end_predicate`; the as_of branch returns the
    unfiltered table for caller-side interval-predicate composition.
    """
    mode, anchor_source, anchor_value = _derive_version_mode(
        root_time_dimension=root_time_dimension,
        target_versioning=validity_versioning,
        resolved_window=resolved_window,
    )
    target_tz = _resolved_target_timezone(validity_versioning)
    if mode == "latest":
        next_table = validity_table.filter(
            _validity_open_end_predicate(validity_table, validity_versioning)
        )
        meta: dict[str, Any] = {
            "dataset": validity_dataset_id,
            "kind": "validity",
            "mode": "latest",
            "anchor_source": anchor_source,
            "anchor_value": str(anchor_value) if anchor_value else None,
            "resolved_partition": None,
            "resolved_partition_summary": None,
            "anchor_to_partition_mapping_digest": None,
            "resolved_interval_predicate": "open_end_only",
            "timezone": target_tz,
        }
        return next_table, meta, False
    # as_of_root_time: return unfiltered table; caller appends the interval predicate
    meta = {
        "dataset": validity_dataset_id,
        "kind": "validity",
        "mode": "as_of_root_time",
        "anchor_source": anchor_source,
        "anchor_value": None,
        "resolved_partition": None,
        "resolved_partition_summary": None,
        "anchor_to_partition_mapping_digest": None,
        "resolved_interval_predicate": validity_versioning.interval,
        "timezone": target_tz,
    }
    return validity_table, meta, True
