"""Independent producer and offline recovery for numeric row state."""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Literal

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization.graph_protocol import SIGNATURE, encode
from marivo.analysis.public_dsl import PublicMaterialized


def _snapshot(result: PublicMaterialized) -> dict[str, object]:
    dataset = result._run()
    verified = dataset.verified()
    return {
        "artifact": result.state.artifact_ref.ref,
        "run": result.state.producing_run_ref,
        "rows": result.to_pandas().to_dict(orient="records"),
        "signature": encode(dataset.artifact.descriptor.signature, SIGNATURE),
        "K": asdict(result.contract()),
        "parts": {part.role: part.table.to_pylist() for part in verified.parts},
    }


def run_process(
    project: str, session_id: str, phase: Literal["produce", "continue", "recover"]
) -> None:
    root = Path(project)
    os.environ["MARIVO_PROJECT_ROOT"] = project
    os.chdir(root)
    manifest = root / "numeric-recovery.json"
    if phase != "produce":
        import duckdb
        import ibis

        from marivo.datasource.adapters import SourceSession
        from marivo.datasource.runtime import DatasourceConnectionService
        from marivo.semantic import catalog

        def forbidden(*args: object, **kwargs: object) -> None:
            raise AssertionError("fixed recovery touched a source, model or DuckDB")

        patch = pytest.MonkeyPatch()
        for owner, name in (
            (ms, "load"),
            (catalog, "load"),
            (duckdb, "connect"),
            (ibis.duckdb, "connect"),
            (SourceSession, "bind"),
            (DatasourceConnectionService, "use_backend"),
        ):
            patch.setattr(owner, name, forbidden)
    session = mv.session.resume(session_id, by="id")
    if phase == "produce":
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
    else:
        saved = json.loads(manifest.read_text())
        output = {}
        expected = {"sum": 147, "min": 7, "max": 120, "mean": 49, "count": 3, "count_defined": 3}
        for name, original in saved.items():
            fixed = session.artifact(original["artifact"])
            assert isinstance(
                fixed,
                (
                    mv.MaterializedStatisticRelation,
                    mv.MaterializedNumericRelation,
                    mv.MaterializedCategoryRelation,
                ),
            )
            assert json.loads(json.dumps(_snapshot(fixed), default=str)) == original
            continuation: (
                mv.LogicalStatisticRelation
                | mv.LogicalAnalysisDomain
                | mv.LogicalFixedAnalysisDomain
            )
            if name in expected:
                assert isinstance(fixed, mv.MaterializedStatisticRelation)
                continuation = fixed.rollup()
            elif name == "read":
                assert isinstance(fixed, mv.MaterializedNumericRelation)
                continuation = fixed.where(fixed.value.gt(10)).members()
            elif name == "category":
                assert isinstance(fixed, mv.MaterializedCategoryRelation)
                continuation = fixed.members()
            else:
                assert isinstance(fixed, mv.MaterializedNumericRelation)
                assert not any(
                    action.call == "relation.rollup()" for action in fixed.contract().actions
                )
                with pytest.raises(AnalysisError):
                    fixed.rollup()
                continuation = fixed.aggregate(mv.count())
            result = continuation.execute()
            if name in expected:
                assert result.to_pandas()["value"].tolist() == [expected[name]]
            elif name in ("read", "category"):
                assert len(result.to_pandas()) == 3
            else:
                assert result.to_pandas()["value"].tolist() == [3]
            again = continuation.execute()
            assert again.state.artifact_ref == result.state.artifact_ref
            output[name] = _snapshot(result)
    print(json.dumps({"pid": os.getpid(), "results": output}, default=str, sort_keys=True))
