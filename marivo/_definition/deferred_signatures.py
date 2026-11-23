"""Argument shapes for captured field, Metric and Population calls."""

from __future__ import annotations


def method_arity(
    symbol: str, positional_count: int, *, population: bool
) -> tuple[int, int, set[str]] | None:
    """Look up the admitted arity of one known deferred operation."""
    reducers = {
        "sum",
        "mean",
        "min",
        "max",
        "median",
        "count",
        "count_distinct",
        "first",
        "last",
        "approx_median",
        "approx_nunique",
        "weighted_mean",
        "quantile",
        "approx_quantile",
    }
    signatures = {
        "via": (1, 1, set()),
        "at": (1, 1, set()),
        "within": (1, 1, set()),
        "where": (1, 1, set()),
        "per": (0, 1, {"by", "grain"}),
        "window": (1, 1, set()),
        "abs": (0, 0, set()),
        "sqrt": (0, 0, set()),
        "exp": (0, 0, set()),
        "isnull": (0, 0, set()),
        "notnull": (0, 0, set()),
        "cast": (1, 1, set()),
        "ifelse": (2, 2, set()),
        "nullif": (1, 1, set()),
        "round": (0, 1, {"digits"}),
        "name": (1, 1, set()),
        "isin": (1, 1, set()),
        "clip": (0, 2, {"lower", "upper"}),
        "log": (0, 1, {"base"}),
        "coalesce": (0, positional_count, set()),
        "greatest": (0, positional_count, set()),
        "least": (0, positional_count, set()),
        "cases": (1, max(1, positional_count), {"else_"}),
    }
    if population:
        signatures = {
            "count": (0, 0, set()),
            "project": (0, 0, {"by"}),
            "classify": (0, 0, {"by", "on"}),
            "identities": (0, 0, {"on"}),
            "union": (1, 1, set()),
            "intersection": (1, 1, set()),
            "difference": (1, 1, set()),
            "image": (0, 0, {"via", "on"}),
            "preimage": (1, 1, {"via", "on"}),
            "where": (1, 1, set()),
        }
    elif symbol in reducers:
        keywords = {"time", "nulls", "where"}
        if symbol in {"quantile", "approx_quantile"}:
            keywords.add("q")
        if symbol in {"first", "last"}:
            keywords.update({"order_by", "include_null"})
        if symbol == "weighted_mean":
            keywords.add("weight")
        signatures[symbol] = (
            0,
            1 if symbol in {"quantile", "approx_quantile"} else 0,
            keywords,
        )
    return signatures.get(symbol)
