"""DuckDB-only SQL lowerings for native Runtime proofs and fences."""

from __future__ import annotations

from collections.abc import Mapping

from sqlglot import expressions as sge

from marivo.analysis.datasets.descriptors import DatasetFieldId
from marivo.analysis.materialization.duckdb_execution import quote


def attribution_summary_sql(
    source_sql: str,
    names: Mapping[DatasetFieldId, str],
    scope_ids: tuple[DatasetFieldId, ...],
    key_ids: tuple[DatasetFieldId, ...],
) -> str:
    scope = tuple(quote(names[key]) for key in scope_ids)
    keys = tuple(quote(names[key]) for key in key_ids)
    resolution = (*scope, quote("active_axis_mask"))
    identity = "struct_pack(" + ", ".join(f"{name} := {name}" for name in keys) + ")"
    grouping = ", ".join(resolution)
    scoped = (
        "struct_pack(" + ", ".join(f"{name} := {name}" for name in scope) + ")" if scope else "1"
    )
    sql = (
        f"WITH attributed AS ({source_sql}), reconciled AS ("
        f"SELECT sum(contribution) AS total, max(overall_delta) AS delta FROM attributed GROUP BY {grouping}) "
        f"SELECT count(DISTINCT {scoped}), (SELECT count(*) FROM reconciled), "
        "count(*) FILTER (WHERE status = 'ok'), count(*) FILTER (WHERE status = 'zero_total_delta'), "
        "CAST(coalesce((SELECT max(abs(total - delta)) FROM reconciled), 0) AS DOUBLE), "
        f"sha256(coalesce(string_agg(sha256(to_json({identity})), '' ORDER BY {', '.join(keys)}), '')) FROM attributed"
    )
    return sql


def membership_integrity_sql(
    member_sql: str,
    primary_sql: str,
    keys: tuple[str, ...],
    endpoint: str,
    signature: tuple[tuple[str, str], ...],
) -> str:
    from marivo.analysis.observation.distinct_contracts import DISTINCT_KEY_COLUMN

    member = quote(DISTINCT_KEY_COLUMN)
    null_member = " OR ".join(
        [f"{member} IS NULL"]
        + [
            f"struct_extract({member}, {sge.Literal.string(name).sql(dialect='duckdb')}) IS NULL"
            for name, _ in signature
        ]
    )
    coordinates = ", ".join(quote(name) for name in keys)
    equality = (
        " AND ".join(f"m.{quote(name)} IS NOT DISTINCT FROM p.{quote(name)}" for name in keys)
        or "TRUE"
    )
    grouped = f"{coordinates}, " if coordinates else ""
    grouping = f" GROUP BY {coordinates}" if coordinates else ""
    sql = (
        f"WITH membership AS ({member_sql}), primary_rows AS ({primary_sql}), "
        f"counts AS (SELECT {grouped}count(*) AS __mv_members FROM membership{grouping}) "
        "SELECT "
        f"(SELECT count(*) FROM membership WHERE {null_member}) + "
        f"(SELECT count(*) FROM (SELECT {grouped}{member} FROM membership "
        f"GROUP BY {grouped}{member} HAVING count(*) <> 1)) + "
        f"(SELECT count(*) FROM membership m WHERE NOT EXISTS (SELECT 1 FROM primary_rows p WHERE {equality})) + "
        f"(SELECT count(*) FROM primary_rows p LEFT JOIN counts m ON {equality} "
        f"WHERE p.{quote(endpoint)} IS NULL OR p.{quote(endpoint)} <> coalesce(m.__mv_members, 0))"
    )
    return sql
