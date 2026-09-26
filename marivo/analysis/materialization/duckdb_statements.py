"""DuckDB-only SQL lowerings for native Runtime proofs and fences."""

from __future__ import annotations

from collections.abc import Mapping

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
