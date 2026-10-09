"""J3: original-component order value versus current customer-channel mean."""

from __future__ import annotations

import sys
from pathlib import Path

from common import emit, open_project, rows

import marivo.analysis as mv
import marivo.semantic as ms


def main(root: Path) -> None:
    session = open_project(root, "s4-p3-j3")
    customers = session.members(ms.ref.entity("sales.customer"))
    channel = ms.ref.dimension("sales.order.channel")
    order = ms.ref.entity("sales.order")
    line = ms.ref.entity("sales.order_line")
    buyer = ms.ref.relationship("sales.order_buyer")
    line_order = ms.ref.relationship("sales.line_order")
    august = mv.time_scope(start="2026-08-01", end="2026-09-01")

    metric = ms.ref.metric("sales.aov_from_lines")
    routes = mv.routes(
        mv.route(line, through=(line_order, buyer)),
        mv.route(order, through=(buyer,)),
    )
    observed = customers.observe(
        metric,
        during=august,
        via=routes,
        by=(
            ms.ref.entity("sales.customer"),
            channel,
        ),
    )
    overall = customers.observe(metric, during=august, via=routes).execute()
    current_mean = observed.aggregate(mv.mean()).execute()
    by_channel = customers.observe(metric, during=august, via=routes, by=(channel,)).execute()
    fixed = observed.execute()
    emit(
        {
            "journey": "j3",
            "session_id": session.id,
            "artifacts": {"fixed": fixed.state.artifact_ref.ref},
            "overall": rows(overall.to_pandas()),
            "current_mean": rows(current_mean.to_pandas()),
            "by_channel": rows(by_channel.to_pandas()),
            "fixed_rollup": rows(fixed.rollup().execute().to_pandas()),
            "retained_parts": list(fixed.contract().retained_parts),
        }
    )


if __name__ == "__main__":
    main(Path(sys.argv[1]))
