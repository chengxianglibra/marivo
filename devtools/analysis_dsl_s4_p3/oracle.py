"""Independent SQL and arithmetic checks over the seeded source facts."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import duckdb


def _ranks(values: list[float]) -> list[float]:
    ranked = sorted(enumerate(values), key=lambda item: item[1])
    result = [0.0] * len(values)
    index = 0
    while index < len(ranked):
        end = index + 1
        while end < len(ranked) and ranked[end][1] == ranked[index][1]:
            end += 1
        average_rank = (index + 1 + end) / 2
        for position in range(index, end):
            result[ranked[position][0]] = average_rank
        index = end
    return result


def _spearman(left: list[float], right: list[float]) -> float:
    x = _ranks(left)
    y = _ranks(right)
    mean_x = sum(x) / len(x)
    mean_y = sum(y) / len(y)
    numerator = sum((a - mean_x) * (b - mean_y) for a, b in zip(x, y, strict=True))
    denominator = math.sqrt(sum((a - mean_x) ** 2 for a in x) * sum((b - mean_y) ** 2 for b in y))
    return numerator / denominator


def expected(root: Path, journey: str) -> dict[str, Any]:
    """Compute expectations without importing Marivo or its test fixtures."""
    connection = duckdb.connect(str(root / "warehouse.duckdb"), read_only=True)
    try:
        if journey == "j1":
            totals = connection.execute(
                "SELECT c.region, SUM(o.amount) FROM customer c "
                'LEFT JOIN "order" o ON o.customer_id=c.customer_id '
                "AND o.ordered_at >= TIMESTAMPTZ '2026-08-01 00:00:00+00:00' "
                "AND o.ordered_at < TIMESTAMPTZ '2026-09-01 00:00:00+00:00' "
                "GROUP BY c.region ORDER BY c.region"
            ).fetchall()
            channels = connection.execute(
                "SELECT o.channel, SUM(o.amount) FROM customer c "
                'JOIN "order" o ON o.customer_id=c.customer_id '
                "WHERE c.region='east' "
                "AND o.ordered_at >= TIMESTAMPTZ '2026-08-01 00:00:00+00:00' "
                "AND o.ordered_at < TIMESTAMPTZ '2026-09-01 00:00:00+00:00' "
                "GROUP BY o.channel ORDER BY o.channel"
            ).fetchall()
            return {
                "total": sum(value for _, value in totals if value is not None),
                "regions": dict(totals),
                "east_channels": dict(channels),
            }
        if journey == "j2":
            changes = connection.execute(
                "SELECT c.customer_id, "
                "SUM(CASE WHEN o.ordered_at >= TIMESTAMPTZ '2026-08-01 00:00:00+00:00' "
                "AND o.ordered_at < TIMESTAMPTZ '2026-09-01 00:00:00+00:00' "
                "THEN o.amount ELSE 0 END) - "
                "SUM(CASE WHEN o.ordered_at >= TIMESTAMPTZ '2026-07-01 00:00:00+00:00' "
                "AND o.ordered_at < TIMESTAMPTZ '2026-08-01 00:00:00+00:00' "
                "THEN o.amount ELSE 0 END) AS change "
                'FROM customer c LEFT JOIN "order" o ON o.customer_id=c.customer_id '
                "GROUP BY c.customer_id ORDER BY c.customer_id"
            ).fetchall()
            decliners = [customer for customer, change in changes if change < 0]
            september = connection.execute(
                'SELECT customer_id, SUM(amount) FROM "order" '
                "WHERE ordered_at >= TIMESTAMPTZ '2026-09-01 00:00:00+00:00' "
                "AND ordered_at < TIMESTAMPTZ '2026-10-01 00:00:00+00:00' "
                "GROUP BY customer_id"
            ).fetchall()
            september_by_customer = dict(september)
            return {
                "changes": dict(changes),
                "decliners": decliners,
                "september_mean": sum(september_by_customer.get(key, 0) for key in decliners)
                / len(decliners),
            }
        if journey == "j3":
            counts = {
                (customer, channel): count
                for customer, channel, count in connection.execute(
                    'SELECT customer_id, channel, COUNT(*) FROM "order" '
                    "WHERE ordered_at >= TIMESTAMPTZ '2026-08-01 00:00:00+00:00' "
                    "AND ordered_at < TIMESTAMPTZ '2026-09-01 00:00:00+00:00' "
                    "GROUP BY customer_id, channel"
                ).fetchall()
            }
            numerators = {
                (customer, channel): amount
                for customer, channel, amount in connection.execute(
                    "SELECT o.customer_id, o.channel, SUM(l.line_amount) "
                    'FROM order_line l JOIN "order" o ON l.order_id=o.order_id '
                    "WHERE o.ordered_at >= TIMESTAMPTZ '2026-08-01 00:00:00+00:00' "
                    "AND o.ordered_at < TIMESTAMPTZ '2026-09-01 00:00:00+00:00' "
                    "GROUP BY o.customer_id, o.channel"
                ).fetchall()
            }
            by_channel: dict[str, float] = {}
            for channel in sorted({channel for _, channel in counts}):
                numerator = sum(numerators.get(key, 0) for key in counts if key[1] == channel)
                denominator = sum(count for key, count in counts.items() if key[1] == channel)
                by_channel[channel] = numerator / denominator
            return {
                "overall": sum(numerators.values()) / sum(counts.values()),
                "current_mean": sum(numerators.get(key, 0) / count for key, count in counts.items())
                / len(counts),
                "by_channel": by_channel,
                "component_rows": len(counts),
            }
        if journey == "j4":
            observations = connection.execute(
                "SELECT c.customer_id, SUM(o.amount), COUNT(o.order_id) "
                'FROM customer c LEFT JOIN "order" o ON o.customer_id=c.customer_id '
                "AND o.ordered_at >= TIMESTAMPTZ '2026-08-01 00:00:00+00:00' "
                "AND o.ordered_at < TIMESTAMPTZ '2026-09-01 00:00:00+00:00' "
                "GROUP BY c.customer_id ORDER BY c.customer_id"
            ).fetchall()
            complete = [(amount, count) for _, amount, count in observations if amount is not None]
            return {
                "coefficient": _spearman(
                    [float(amount) for amount, _ in complete],
                    [float(count) for _, count in complete],
                ),
                "input_observation_count": len(observations),
                "matched_observation_count": len(observations),
                "complete_pair_count": len(complete),
                "null_pair_count": len(observations) - len(complete),
                "status": "valid",
            }
        raise ValueError(journey)
    finally:
        connection.close()


def _near(actual: object, target: object) -> bool:
    if target is None:
        return actual is None
    return (
        isinstance(actual, (int, float))
        and isinstance(target, (int, float))
        and math.isclose(float(actual), float(target), rel_tol=1e-9, abs_tol=1e-9)
    )


def verify(result: dict[str, Any], expected_values: dict[str, Any]) -> None:
    """Check the public script's values, domain and state against independent facts."""
    journey = result["journey"]
    if journey == "j1":
        total = result["total"][0]
        assert _near(total["value"], expected_values["total"])
        assert total["cell_tag"] == "defined"
        regions = {row["group"]: row for row in result["regions"]}
        assert set(regions) == set(expected_values["regions"])
        for group, value in expected_values["regions"].items():
            assert _near(regions[group]["value"], value)
            assert regions[group]["cell_tag"] == ("null" if value is None else "defined")
        channels = {row["group"]: row for row in result["east_channels"]}
        assert channels.keys() == expected_values["east_channels"].keys()
        assert all(
            _near(channels[group]["value"], value) and channels[group]["cell_tag"] == "defined"
            for group, value in expected_values["east_channels"].items()
        )
        assert _near(result["fixed_rollup"][0]["value"], expected_values["total"])
    elif journey == "j2":
        changes = {row["member"]: row["value"] for row in result["changes"]}
        assert changes == expected_values["changes"]
        assert all(row["cell_tag"] == "defined" for row in result["changes"])
        assert sorted(row["member"] for row in result["decliners"]) == expected_values["decliners"]
        assert _near(result["september_mean"][0]["value"], expected_values["september_mean"])
        assert result["september_mean"][0]["cell_tag"] == "defined"
    elif journey == "j3":
        assert _near(result["overall"][0]["value"], expected_values["overall"])
        assert _near(result["fixed_rollup"][0]["value"], expected_values["overall"])
        assert _near(result["current_mean"][0]["value"], expected_values["current_mean"])
        assert all(
            rows[0]["cell_tag"] == "defined"
            for rows in (result["overall"], result["fixed_rollup"], result["current_mean"])
        )
        by_channel = {row["group"]: row for row in result["by_channel"]}
        assert by_channel.keys() == expected_values["by_channel"].keys()
        assert all(
            _near(by_channel[group]["value"], value) and by_channel[group]["cell_tag"] == "defined"
            for group, value in expected_values["by_channel"].items()
        )
        assert expected_values["component_rows"] == 3
        assert len(result["retained_parts"]) == 5
    elif journey == "j4":
        association = result["association"]
        assert len(association) == 1
        assert len(result["negative"]) == 1
        for row in (association[0], result["negative"][0]):
            for key, value in expected_values.items():
                assert _near(row[key], value) if key == "coefficient" else row[key] == value
            assert row["metric_key_a"] == "metric:sales.revenue"
            assert row["metric_key_b"] == "metric:sales.order_count"
    else:
        raise ValueError(journey)
