"""Independent native numeric publication and source-free terminal reads."""

from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError
from marivo.datasource.adapters import SourceSession
from marivo.semantic.errors import SemanticExpressionError
from marivo.semantic.reader import SemanticProject
from tests.analysis.materialization.domain_recovery_worker import forbidden, snapshot
from tests.shared_fixtures import catalog_value, terminal_runs
from tests.support.json import Json, obj, read


def run_process(project: str, session_id: str, phase: str) -> None:
    root = Path(project)
    os.environ["MARIVO_PROJECT_ROOT"] = project
    os.chdir(root)
    manifest = root / "numeric-source.json"
    session = mv.session.resume(session_id)
    if phase == "produce":
        members = session.members(session.catalog.get("entity:sales.customer"))
        region = catalog_value(session.catalog, "sales.customer.region", kind="field")
        scope = session.time_scope(start="2026-08-01", end="2026-09-01")
        observed = session.observe(
            {"value": session.catalog.get("metric:sales.revenue")},
            by=(scope, members.axes[0], region),
            where=mv.in_population(members),
        )
        results = {
            "sum": observed.reduce(
                {"value": observed["value"].sum()}, by=(observed.axes[1],)
            ).persist(),
            "mean": observed.reduce(
                {"value": observed["value"].mean()}, by=(observed.axes[1],)
            ).persist(),
            "count": observed.reduce({"value": mv.row_count()}, by=(observed.axes[1],)).persist(),
            "count_defined": observed.where(~observed["value"].is_null())
            .reduce({"value": mv.row_count()})
            .persist(),
        }
        sums = results["sum"].to_pandas()["value"].tolist()
        assert sums == [Decimal("120"), Decimal("20"), Decimal("7")], sums
        assert all(isinstance(value, Decimal) for value in sums)
        assert results["mean"].to_pandas()["value"].tolist() == [120, 20, 7]
        assert results["count"].to_pandas()["value"].tolist() == [1, 1, 1]
        raw = observed.persist().to_pandas()["value"]
        assert raw.min() == 7 and raw.max() == 120
        for name in ("distinct", "approx_distinct", "quantile"):
            results[name] = session.observe(
                {"value": catalog_value(session.catalog, "sales." + name, kind="metric")},
                by=mv.product(scope, members),
            ).persist()
        assert results["distinct"].to_pandas()["value"].tolist() == [2, 1, 1]
        assert results["quantile"].to_pandas()["value"].tolist() == [60, 20, 7]
        results["read"] = session.read(
            session.members(session.catalog.get("entity:sales.order")),
            {"value": catalog_value(session.catalog, "sales.order.amount", kind="field")},
        ).persist()
        results["category"] = session.read(members, {"value": region}).persist()
        before = len(terminal_runs(session))
        try:
            session.observe(
                {"value": session.catalog.get("metric:sales.approx_quantile")}, by=(scope,)
            ).persist()
        except (AnalysisError, SemanticExpressionError) as error:
            assert error.expected and error.received and error.repair
        else:
            raise AssertionError("An unqualified approximate quantile route executed")
        assert len(terminal_runs(session)) == before
        output: dict[str, Json] = {
            name: {"artifact": result.artifact_id, "snapshot": snapshot(result)}
            for name, result in results.items()
        }
        manifest.write_text(json.dumps(output, sort_keys=True))
    else:
        output = obj(read(manifest))
        with (
            patch.object(ms, "load", forbidden),
            patch.object(SemanticProject, "load", forbidden),
            patch.object(SourceSession, "__enter__", forbidden),
            patch.object(SourceSession, "execute", forbidden),
        ):
            before = len(terminal_runs(session))
            for value in output.values():
                saved = obj(value)
                artifact = saved["artifact"]
                assert isinstance(artifact, str)
                result = session.persisted_frame(artifact)
                assert snapshot(result) == saved["snapshot"]
            assert len(terminal_runs(session)) == before
    assert session._runtime.store.resources(session.id) == ()
    print(json.dumps({"pid": os.getpid(), "results": sorted(output), "phase": phase}))
