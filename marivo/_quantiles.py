"""Shared source algorithm policy for explicit exact and approximate quantiles."""

from typing import Literal


def source_quantile_algorithm(
    backend: str, *, approximate: bool
) -> Literal["native_exact", "rank_bounds", "native_approximate"] | None:
    """Return only routes preserving the requested accuracy contract."""
    if approximate:
        return "native_approximate" if backend in ("duckdb", "trino", "clickhouse") else None
    if backend in ("duckdb", "postgres"):
        return "native_exact"
    return "rank_bounds" if backend in ("mysql", "trino", "clickhouse") else None
