"""Pure computation functions for scope resolution.

Extracted from service.py — these functions accept all needed data as
parameters and perform no I/O.  The caller (service.py / CoreEngine proxy) is
responsible for fetching any required data before invoking these functions.
"""

from __future__ import annotations

import json
from typing import Any

# ── Shared helpers ───────────────────────────────────────────────────


def _optional_str(value: Any) -> str | None:
    """Normalize a value to a stripped non-empty string or None."""
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    return None


def _carrier_locator_dict(value: Any) -> dict[str, Any] | None:
    """Parse a carrier locator from dict, JSON string, or dot-separated string."""
    if isinstance(value, dict):
        return {
            "catalog": _optional_str(value.get("catalog")),
            "schema": _optional_str(value.get("schema")) or _optional_str(value.get("schema_name")),
            "table": _optional_str(value.get("table")),
        }
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return None
        try:
            payload = json.loads(normalized)
        except json.JSONDecodeError:
            parts = [part.strip() for part in normalized.split(".") if part.strip()]
            if len(parts) >= 3:
                return {"catalog": parts[-3], "schema": parts[-2], "table": parts[-1]}
            if len(parts) == 2:
                return {"catalog": None, "schema": parts[0], "table": parts[1]}
            if len(parts) == 1:
                return {"catalog": None, "schema": None, "table": parts[0]}
            return None
        if isinstance(payload, dict):
            return _carrier_locator_dict(payload)
        if isinstance(payload, str):
            return _carrier_locator_dict(payload)
    return None


# ── Filter merging ───────────────────────────────────────────────────


def merge_filters(*filters: str | None) -> str | None:
    """AND-merge multiple filter expressions, ignoring None values."""
    parts = [f for f in filters if f]
    if not parts:
        return None
    return " AND ".join(f"({p})" for p in parts)


# ── Constraint resolution ───────────────────────────────────────────


def resolve_scope_constraint_column(
    constraint_key: str,
    *,
    dimension_sources: dict[str, set[str]] | None = None,
) -> str:
    """Resolve a scope constraint key to a physical column name.

    * If the key has no dot, it is returned as-is (already a physical column).
    * If the key starts with ``dimension.``, it is resolved via *dimension_sources*.
    * Otherwise a ``ValueError`` is raised.

    Parameters
    ----------
    constraint_key:
        The constraint key from the request scope.
    dimension_sources:
        Mapping from canonical dimension ref (e.g. ``"dimension.cluster"``)
        to the set of physical column names it resolves to.  Must be supplied
        when *constraint_key* contains a ``.``.
    """
    if "." not in constraint_key:
        return constraint_key
    if not constraint_key.startswith("dimension."):
        raise ValueError(
            f"scope.constraints key '{constraint_key}' must be a physical column or "
            "a canonical dimension ref like 'dimension.cluster'"
        )
    if dimension_sources is None:
        raise ValueError(
            f"scope.constraints key '{constraint_key}' requires a semantic metric scope"
        )
    physical_names = sorted(dimension_sources.get(constraint_key) or [])
    if not physical_names:
        raise ValueError(
            f"scope.constraints key '{constraint_key}' is not available in metric semantic scope"
        )
    if len(physical_names) > 1:
        raise ValueError(
            f"scope.constraints key '{constraint_key}' does not resolve to a unique physical column"
        )
    return physical_names[0]


def constraints_dict_to_filter(
    constraints: dict[str, Any],
    *,
    resolve_semantic_refs: bool = False,
    dimension_sources: dict[str, set[str]] | None = None,
) -> str | None:
    """Convert a constraint dict to a SQL filter expression.

    Parameters
    ----------
    constraints:
        Mapping of constraint keys to scalar values.
    resolve_semantic_refs:
        If True, resolve ``dimension.*`` keys via *dimension_sources*.
    dimension_sources:
        Required when *resolve_semantic_refs* is True.
    """
    parts: list[str] = []
    for key, value in constraints.items():
        if isinstance(value, (dict, list)):
            continue
        column_name = key
        if resolve_semantic_refs:
            column_name = resolve_scope_constraint_column(
                key,
                dimension_sources=dimension_sources,
            )
        parts.append(f"{column_name} = '{value}'")
    return " AND ".join(parts) if parts else None


# ── Predicate expression → SQL ───────────────────────────────────────


def resolve_predicate_target_column(
    target_ref: str,
    *,
    dimension_sources: dict[str, set[str]] | None = None,
) -> str:
    """Resolve a predicate target ref to a physical column name.

    * Plain names (no dot) are returned as-is.
    * ``dimension.*`` refs are resolved via *dimension_sources*.
    * Other dotted refs are stripped to the last segment.
    """
    if "." not in target_ref:
        return target_ref
    if target_ref.startswith("dimension."):
        return resolve_scope_constraint_column(
            target_ref,
            dimension_sources=dimension_sources,
        )
    # Fall back: strip prefix and use as column hint (entity.user -> user).
    return target_ref.split(".", 1)[-1].replace(".", "_")


def predicate_expression_to_sql(
    expr: dict[str, Any],
    *,
    dimension_sources: dict[str, set[str]] | None = None,
) -> str:
    """Convert a structured predicate expression dict to a SQL WHERE fragment.

    The expression format follows the canonical predicate contract:
    ``{"op": "<operator>", "target_ref": "<ref>", "value": <value>}``,
    with ``"op": "and"`` for conjunction over ``"items"``.
    """
    op = expr.get("op")
    if op == "and":
        items = expr.get("items") or []
        parts = [
            predicate_expression_to_sql(item, dimension_sources=dimension_sources) for item in items
        ]
        return " AND ".join(parts)
    target_ref = expr.get("target_ref", "")
    column = resolve_predicate_target_column(
        target_ref,
        dimension_sources=dimension_sources,
    )
    value: Any = expr.get("value")
    if op in ("is_null", "is_not_null"):
        return f"{column} IS NULL" if op == "is_null" else f"{column} IS NOT NULL"
    if op == "between":
        lo, hi = value[0], value[1]
        return f"{column} BETWEEN '{lo}' AND '{hi}'"
    if op in ("in", "not_in"):
        vals = ", ".join(f"'{v}'" for v in value)
        sql_in = f"{column} IN ({vals})"
        return sql_in if op == "in" else f"NOT {sql_in}"
    if value is not None:
        return f"{column} {op} '{value}'"
    return f"{column} {op}"


# ── Table name ↔ locator matching ────────────────────────────────────


def table_name_matches_locator(table_name: str, locator: dict[str, Any] | str | None) -> bool:
    """Check whether *table_name* matches a carrier locator.

    Handles both fully-qualified dot notation and partial matches.
    """
    normalized_table = table_name.strip()
    locator_dict = _carrier_locator_dict(locator)
    normalized_locator = ""
    if locator_dict is not None:
        normalized_locator = ".".join(
            value
            for value in [
                _optional_str(locator_dict.get("catalog")),
                _optional_str(locator_dict.get("schema")),
                _optional_str(locator_dict.get("table")),
            ]
            if value is not None
        )
    else:
        normalized_locator = str(locator or "").strip()
    if not normalized_table or not normalized_locator:
        return False
    if normalized_table == normalized_locator:
        return True
    return normalized_locator.endswith(f".{normalized_table}") or normalized_table.endswith(
        f".{normalized_locator}"
    )


# ── Dataset source → authority locator ───────────────────────────────


def dataset_source_to_authority_locator(source: str) -> dict[str, Any]:
    """Parse a dot-separated dataset source into a locator dict.

    Three parts → ``catalog.schema.table``.
    Two parts → ``schema.table`` (catalog=None).
    One part → ``table`` only.
    """
    parts = [part for part in source.split(".") if part]
    if len(parts) >= 3:
        return {"catalog": parts[-3], "schema": parts[-2], "table": parts[-1]}
    if len(parts) == 2:
        return {"catalog": None, "schema": parts[0], "table": parts[1]}
    return {"catalog": None, "schema": None, "table": source}


# ── Metric scope dimension sources (pure part) ──────────────────────


def compute_metric_scope_dimension_sources(
    *,
    payload: dict[str, Any],
    table_name: str,
    dataset_source: str | None,
) -> dict[str, set[str]]:
    """Compute the dimension→physical-column mapping from metric payload data.

    This is the pure computation extracted from
    ``SemanticLayerService._metric_scope_dimension_sources``.  The caller
    must fetch the metric payload and dataset_source string and verify
    table-to-source matching before calling this function.

    Parameters
    ----------
    payload:
        The metric semantic object payload dict.
    table_name:
        The table name from the query request.
    dataset_source:
        The dataset_source from the metric payload (or None).
    """
    if dataset_source is not None and not table_name_matches_locator(table_name, dataset_source):
        return {}
    fields = payload.get("dataset_fields")
    available = set(fields) if isinstance(fields, dict) else set()
    dimensions = [str(item) for item in list(payload.get("dimensions") or [])]
    result: dict[str, set[str]] = {}
    for dimension in dimensions:
        if dimension == "event_date":
            continue
        physical_name = dimension.removeprefix("dimension.")
        if not available or physical_name in available:
            result.setdefault(dimension, set()).add(physical_name)
    return result
