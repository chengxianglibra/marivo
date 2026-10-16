"""Independent producer and offline recovery for numeric row state."""

from __future__ import annotations

import json
import os
from pathlib import Path

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.materialization.graph_protocol import SIGNATURE, encode
from marivo.analysis.public_dsl import PublicMaterialized


def _snapshot(result: PublicMaterialized) -> dict[str, object]:
    dataset = result._dataset
    assert dataset is not None
    verified = dataset.verified()
    return {
        "artifact": result.state.artifact_ref.ref,
        "run": result.state.producing_run_ref,
        "rows": result.to_pandas().to_dict(orient="records"),
        "signature": encode(dataset.artifact.descriptor.signature, SIGNATURE),
        "parts": {part.role: part.table.to_dict("records") for part in verified.parts},
    }


def run_process(project: str, session_id: str) -> None:
    root = Path(project)
    os.environ["MARIVO_PROJECT_ROOT"] = project
    os.chdir(root)
    manifest = root / "numeric-source.json"
    session = mv.session.resume(session_id, by="id")
    members = session.members(ms.ref.entity("sales.customer"))
    observed = members.observe(
        ms.ref.metric("sales.revenue"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.order_buyer"),
        by=(mv.member(),),
    )
    region = members.read(ms.ref.dimension("sales.customer.region"))
    assert isinstance(region, mv.LogicalCategoryRelation)
    results: dict[str, PublicMaterialized] = {}
    for name, method in (
        ("sum", mv.sum()),
        ("min", mv.min()),
        ("max", mv.max()),
        ("mean", mv.mean()),
        ("count", mv.count()),
        ("count_defined", mv.count_defined()),
    ):
        results[name] = observed.group_by(region).aggregate(method).execute()
    for name in ("distinct", "approx_distinct", "quantile", "approx_quantile"):
        results[name] = members.observe(
            ms.ref.metric(f"sales.{name}"),
            during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
            via=ms.ref.relationship("sales.order_buyer"),
            by=(mv.member(),),
        ).execute()
    orders = session.members(ms.ref.entity("sales.order"))
    results["read"] = orders.read(ms.ref.measure("sales.order.amount")).execute()
    results["category"] = region.execute()
    output = {name: _snapshot(result) for name, result in results.items()}
    manifest.write_text(json.dumps(output, default=str, sort_keys=True))
    print(json.dumps({"pid": os.getpid(), "results": output}, default=str, sort_keys=True))
