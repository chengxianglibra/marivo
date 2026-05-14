"""Pure computation functions for metric resolution and metric query compilation.

Extracted from service.py — these functions accept all needed data as
parameters and perform no I/O.  The caller (service.py / CoreEngine proxy) is
responsible for fetching any required data before invoking these functions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

# ── Shared helper ────────────────────────────────────────────────────


def _optional_str(value: Any) -> str | None:
    """Normalize a value to a stripped non-empty string or None."""
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    return None


def _metric_name_from_ref(metric_ref: str) -> str:
    return metric_ref.removeprefix("metric.")


def _coerce_metric_ref(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("'metric' is required")
    if normalized.startswith("metric."):
        return normalized
    return f"metric.{normalized}"


# ── Data classes ─────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class MetricExecutionContext:
    metric_ref: str
    table_name: str
    binding_ref: str
    carrier_binding_key: str | None = None
    source_object_ref: str | None = None
    carrier_locator: dict[str, Any] | None = None
    authority_locator: dict[str, Any] | None = None
    mapping_id: str | None = None
    execution_locator: dict[str, Any] | None = None
    routing_detail: dict[str, Any] | None = None
    additive_dimensions: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class MetricBindingResolution:
    metric_ref: str
    binding_ref: str
    carrier_binding_key: str | None
    source_object_ref: str | None
    carrier_locator: dict[str, Any] | None
    authority_locator: dict[str, Any] | None
    mapping_id: str | None
    execution_locator: dict[str, Any] | None
    routing_detail: dict[str, Any] | None
    table_name: str | None


@dataclass(frozen=True, slots=True)
class MetricCarrierRoutePreflight:
    table_name: str | None
    mapping_id: str | None
    execution_locator: dict[str, Any] | None
    routing_detail: dict[str, Any]
    readiness_blockers: list[dict[str, Any]]


# ── Metric query mode contracts ──────────────────────────────────────

METRIC_QUERY_MODE_CONTRACTS: dict[str, Any] = {
    "compare": {
        "payload_fields": {
            "current_value": "current_value",
            "baseline_value": "baseline_value",
            "delta_pct": "delta_pct",
            "current_sessions": "current_sessions",
            "baseline_sessions": "baseline_sessions",
        },
        "required_payload_keys": (
            "current_value",
            "baseline_value",
            "delta_pct",
            "current_sessions",
            "baseline_sessions",
        ),
    },
    "single_window": {
        "payload_fields": {
            "current_value": "current_value",
            "current_sessions": "current_sessions",
        },
        "required_payload_keys": (
            "current_value",
            "current_sessions",
        ),
    },
}


def metric_query_mode_contract(mode: str) -> dict[str, Any]:
    """Resolve a metric_query mode string to its contract specification.

    Returns a dict with keys ``mode``, ``payload_fields``,
    ``required_payload_keys``, and ``required_row_fields``.
    """
    normalized = str(mode).strip().lower()
    contract = METRIC_QUERY_MODE_CONTRACTS.get(normalized)
    if contract is None:
        raise ValueError(f"Unsupported metric_query mode: {mode}")
    payload_fields = dict(contract["payload_fields"])
    required_payload_keys = tuple(contract["required_payload_keys"])
    return {
        "mode": normalized,
        "payload_fields": payload_fields,
        "required_payload_keys": required_payload_keys,
        "required_row_fields": tuple(payload_fields[key] for key in required_payload_keys),
    }


def build_metric_query_extractor_context(
    *,
    mode: str,
    metric_name: str,
    observation_type: str,
    dimensions: list[str],
    quality_builder: Any,
) -> dict[str, Any]:
    """Build the extractor context dict for metric query row extraction."""
    contract = metric_query_mode_contract(mode)
    return {
        "metric": metric_name,
        "observation_type": observation_type,
        "dimensions": dimensions,
        "payload_fields": contract["payload_fields"],
        "required_payload_keys": contract["required_payload_keys"],
        "quality_builder": quality_builder,
    }


def metric_query_quality_builder(mode: str) -> Any:
    """Return a quality-check lambda for the given metric query mode."""
    normalized = metric_query_mode_contract(mode)["mode"]
    if normalized == "compare":
        return lambda row: {
            "freshness_ok": True,
            "sample_size_ok": min(row["current_sessions"] or 0, row["baseline_sessions"] or 0)
            >= 150,
        }
    return lambda row: {
        "freshness_ok": True,
        "sample_size_ok": (row.get("current_sessions") or 0) >= 150,
    }


# ── Row normalization ────────────────────────────────────────────────


def normalize_metric_rows(
    rows: list[dict[str, Any]],
    *,
    mode: str,
) -> list[dict[str, Any]]:
    """Validate and normalize metric query rows against the mode contract."""
    contract = metric_query_mode_contract(mode)
    normalized: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        row_dict = dict(row)
        missing = [field for field in contract["required_row_fields"] if field not in row_dict]
        if missing:
            missing_str = ", ".join(missing)
            raise ValueError(
                f"metric_query rows missing required columns at row {index}: {missing_str}"
            )
        normalized.append(row_dict)
    return normalized


# ── Slice label / debug / summary ────────────────────────────────────


def comparison_slice_label(row: dict[str, Any], dimensions: list[str]) -> str:
    """Build a human-readable slice label from a row and dimension names."""
    if not dimensions:
        return "overall"
    parts = [
        f"{dimension}={row[dimension]}"
        for dimension in dimensions
        if row.get(dimension) is not None
    ]
    return ", ".join(parts) if parts else "overall"


def metric_query_debug_payload(
    *,
    current_start: str,
    current_end: str,
    baseline_start: str | None = None,
    baseline_end: str | None = None,
    scope_mode: str = "single_window",
    all_rows: list[dict[str, Any]],
    window_length_match: bool | None = None,
) -> dict[str, Any]:
    """Build the debug payload dict for a metric query result."""
    debug: dict[str, Any] = {
        "current_window": [current_start, current_end],
        "current_has_data": any(row.get("current_sessions") for row in all_rows),
    }
    if scope_mode == "single_window":
        return debug
    if baseline_start is None or baseline_end is None:
        raise ValueError("metric_query debug payload requires baseline window")
    debug.update(
        {
            "baseline_window": [baseline_start, baseline_end],
            "baseline_has_data": any(row.get("baseline_sessions") for row in all_rows),
            "window_length_match": bool(window_length_match),
        }
    )
    return debug


def metric_query_summary(
    metric_name: str,
    rows: list[dict[str, Any]],
    *,
    mode: str,
    debug: dict[str, Any],
    dimensions: list[str],
    grain: str,
    current_len: int | None = None,
    baseline_len: int | None = None,
) -> str:
    """Generate a human-readable summary for a metric query result."""
    if mode == "single_window":
        if rows:
            top = rows[0]
            slice_label = comparison_slice_label(top, dimensions)
            return (
                f"Metric '{metric_name}' current window observation: highest value is "
                f"{top['current_value']} for {slice_label} "
                f"(current_sessions={top['current_sessions']})."
            )
        if debug["current_has_data"]:
            return (
                f"Metric '{metric_name}' current window observation returned no retained rows. "
                f"current_window={debug['current_window']}."
            )
        return (
            f"Metric '{metric_name}' current window has no data. "
            f"current_window={debug['current_window']}."
        )

    # compare mode
    if rows:
        top = rows[0]
        direction = "decline" if (top.get("delta_pct") or 0) < 0 else "increase"
        slice_label = comparison_slice_label(top, dimensions)
        summary = (
            f"Metric '{metric_name}' comparison: top {direction} is "
            f"{top['delta_pct']}% for {slice_label} "
            f"(current_value={top['current_value']}, baseline_value={top['baseline_value']})."
        )
        if not debug["window_length_match"]:
            if current_len is None or baseline_len is None:
                raise ValueError("metric_query compare summary requires both window lengths")
            unit = "h" if grain == "hour" else "d"
            summary += (
                f" Window size mismatch: current={current_len}{unit}, "
                f"baseline={baseline_len}{unit}; count/sum metrics may not be comparable."
            )
        return summary

    if debug["current_has_data"] or debug["baseline_has_data"]:
        missing = []
        if not debug["current_has_data"]:
            missing.append("current")
        if not debug["baseline_has_data"]:
            missing.append("baseline")
        missing_str = " and ".join(missing) if missing else "one"
        return (
            f"Metric '{metric_name}' comparison: {missing_str} window has no data. "
            f"current_window={debug['current_window']}, baseline_window={debug['baseline_window']}."
        )

    return (
        f"Metric '{metric_name}' comparison returned no results. "
        f"current_window={debug['current_window']}, baseline_window={debug['baseline_window']}."
    )


# ── Order normalization ──────────────────────────────────────────────


def normalize_metric_query_order(order: str | None, *, mode: str) -> str | None:
    """Normalize a metric query ORDER BY clause for the given mode."""
    normalized_mode = metric_query_mode_contract(mode)["mode"]
    if order is None:
        return "CURRENT_VALUE DESC" if normalized_mode == "single_window" else None
    normalized = order.strip().upper()
    if normalized_mode == "compare":
        if normalized in {"ASC", "DESC"}:
            return f"DELTA_PCT {normalized}"
        if normalized in {"DELTA_PCT ASC", "DELTA_PCT DESC"}:
            return normalized
        raise ValueError("metric_query compare mode supports only delta_pct ASC/DESC")
    if normalized in {
        "CURRENT_VALUE ASC",
        "CURRENT_VALUE DESC",
        "CURRENT_SESSIONS ASC",
        "CURRENT_SESSIONS DESC",
    }:
        return normalized
    raise ValueError(
        "metric_query single_window mode supports only current_value ASC/DESC or current_sessions ASC/DESC"
    )


# ── Window length calculation ────────────────────────────────────────


def window_length(
    *,
    window_start: str,
    window_end: str,
    grain: str,
) -> int:
    """Compute the length of a time window in hours or days.

    Parameters
    ----------
    window_start, window_end:
        ISO-format datetime strings.
    grain:
        ``"hour"`` for hours; anything else for days.
    """
    if grain == "hour":
        start_dt = datetime.fromisoformat(window_start)
        end_dt = datetime.fromisoformat(window_end)
        return int((end_dt - start_dt).total_seconds() // 3600)
    start_day = date.fromisoformat(window_start)
    end_day = date.fromisoformat(window_end)
    return (end_day - start_day).days
