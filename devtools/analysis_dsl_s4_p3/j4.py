"""J4: Spearman over two governed customer observations, then fixed selection."""

from __future__ import annotations

import sys
from pathlib import Path

from common import emit, open_project, rows

import marivo.analysis as mv
import marivo.semantic as ms


def main(root: Path) -> None:
    session = open_project(root, "s4-p3-j4")
    customers = session.members(ms.ref.entity("sales.customer"))
    buyer = ms.ref.relationship("sales.order_buyer")
    august = mv.time_scope(start="2026-08-01", end="2026-09-01")
    revenue = customers.observe(
        ms.ref.metric("sales.revenue"),
        during=august,
        via=buyer,
        by=(ms.ref.entity("sales.customer"),),
    )
    count = customers.observe(
        ms.ref.metric("sales.order_count"),
        during=august,
        via=buyer,
        by=(ms.ref.entity("sales.customer"),),
    )
    association = revenue.correlate(count, method="spearman").execute()
    coefficient = association.coefficient
    negative = coefficient.where(coefficient.value.lt(0)).execute()
    emit(
        {
            "journey": "j4",
            "session_id": session.id,
            "artifacts": {"fixed": association.state.artifact_ref.ref},
            "association": rows(association.to_pandas()),
            "negative": rows(negative.to_pandas()),
            "coefficient_actions": [action.call for action in coefficient.contract().actions],
        }
    )


if __name__ == "__main__":
    main(Path(sys.argv[1]))
