"""J1: revenue total, regional breakdown, then east by order channel."""

from __future__ import annotations

import sys
from pathlib import Path

from common import emit, open_project, rows

import marivo.analysis as mv
import marivo.semantic as ms


def main(root: Path) -> None:
    session = open_project(root, "s4-p3-j1")
    customers = session.members(ms.ref.entity("sales.customer"))
    revenue = ms.ref.metric("sales.revenue")
    buyer = ms.ref.relationship("sales.order_buyer")
    region = ms.ref.dimension("sales.customer.region")
    channel = ms.ref.dimension("sales.order.channel")
    august = mv.time_scope(start="2026-08-01", end="2026-09-01")

    observed = customers.observe(revenue, during=august, via=buyer, coordinates=(channel,))
    total = observed.rollup().execute()
    regions = customers.group_by(region).observe(revenue, during=august, via=buyer).execute()
    read_region = customers.read(region)
    east = read_region.where(read_region.value.eq("east")).members()
    east_channels = (
        east.observe(revenue, during=august, via=buyer, coordinates=(channel,))
        .group_by(channel)
        .execute()
    )
    fixed = observed.execute()
    emit(
        {
            "journey": "j1",
            "session_id": session.id,
            "artifacts": {"fixed": fixed.state.artifact_ref.ref},
            "total": rows(total.to_pandas()),
            "regions": rows(regions.to_pandas()),
            "east_channels": rows(east_channels.to_pandas()),
            "fixed_rollup": rows(fixed.rollup().execute().to_pandas()),
        }
    )


if __name__ == "__main__":
    main(Path(sys.argv[1]))
