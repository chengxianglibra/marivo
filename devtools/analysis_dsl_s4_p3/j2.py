"""J2: select customers whose revenue fell, then observe September."""

from __future__ import annotations

import sys
from pathlib import Path

from common import emit, open_project, rows

import marivo.analysis as mv
import marivo.semantic as ms


def main(root: Path) -> None:
    session = open_project(root, "s4-p3-j2")
    customers = session.members(ms.ref.entity("sales.customer"))
    revenue = ms.ref.metric("sales.revenue")
    buyer = ms.ref.relationship("sales.order_buyer")
    july = mv.time_scope(start="2026-07-01", end="2026-08-01")
    august = mv.time_scope(start="2026-08-01", end="2026-09-01")
    september = mv.time_scope(start="2026-09-01", end="2026-10-01")

    change = customers.observe(revenue, during=august, via=buyer).compare(
        customers.observe(revenue, during=july, via=buyer)
    )
    selected = change.where(change.value.lt(0)).members()
    september_mean = (
        selected.observe(revenue, during=september, via=buyer).summarize(mv.mean()).execute()
    )
    fixed_change = change.execute()
    fixed_selected = fixed_change.where(fixed_change.value.lt(0)).execute()
    emit(
        {
            "journey": "j2",
            "session_id": session.id,
            "artifacts": {"fixed": fixed_change.state.artifact_ref.ref},
            "changes": rows(fixed_change.to_pandas()),
            "decliners": rows(fixed_selected.members().execute().to_pandas()),
            "september_mean": rows(september_mean.to_pandas()),
        }
    )


if __name__ == "__main__":
    main(Path(sys.argv[1]))
