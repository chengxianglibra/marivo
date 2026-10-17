"""Source and local original sums retain exact full-fact answers."""

from pathlib import Path

import pytest

from marivo.analysis.methods.builtin import implementations
from marivo.analysis.methods.physical import Qualified, SourceShape, TimeShape
from marivo.analysis.methods.semantics import MethodKey
from tests.analysis.graph.physical_workloads import workload
from tests.support.source_trace import SourceTrace


def test_duckdb_cost_routes_keep_the_original_exact_sum(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source_trace: SourceTrace
) -> None:
    with workload("duckdb", "ordinary-table", 64, "baseline", tmp_path, monkeypatch) as work:
        assert [row["amount"] for row in work.rows[:6]] == [None, 2, 3, 4, 5, 6]
        assert [row["id"] for row in work.rows[:2]] == [9007199254740992, 9007199254740993]
        queries = (work.total(), work.values().rollup())
        references = []
        for query in queries:
            first = query.execute()
            assert work.validate(first)["passed"] is True
            references.append(first.id)
            before = len(work.session.runs().items)
            second = query.execute()
            assert work.validate(second)["passed"] is True
            assert second.id != first.id
            assert len(work.session.runs().items) == before + 1
            source_trace.record(first)
            submitted = len(source_trace.native_sql)
            assert work.session.artifact(first.id).to_pandas().equals(first.to_pandas())
            assert len(source_trace.native_sql) == submitted
            assert work.session._runtime.store.resources(work.session.id) == ()
        assert references[0] != references[1]
        assert source_trace.qualifications
        assert any("SUM(" in sql.upper() for sql in source_trace.native_sql)


def test_duckdb_local_original_sum_consumes_group_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source_trace: SourceTrace
) -> None:
    with workload("duckdb", "ordinary-table", 64, "baseline", tmp_path, monkeypatch) as work:
        independent_totals = {"started": 0, "finished": 0}
        for row in work.rows:
            amount = row["amount"]
            if amount is not None:
                assert isinstance(amount, int)
                independent_totals[str(row["kind"])] += amount
        assert independent_totals == {"started": 262, "finished": 267}
        session = work.session
        axis = session.catalog.fields.get("cost.facts.kind")
        grouped = session.observe(
            {"value": session.catalog.metrics.get("cost.facts_total")},
            during=session.time_scope(start="2026-08-01", end="2026-08-02"),
            by=(axis,),
        )
        frame = grouped.execute().to_pandas()
        assert (
            dict(zip(frame.iloc[:, 0], frame[("value", "value")], strict=True))
            == independent_totals
        )
        scores = (frame[("value", "value")] - frame[("value", "value")].mean()) / frame[
            ("value", "value")
        ].std(ddof=0)
        assert dict(zip(frame.iloc[:, 0], scores, strict=True)) == {
            "started": -1.0,
            "finished": 1.0,
        }
        # Original rollup consumes the same selected group input before publication.
        result = grouped.rollup().execute()
        assert work.validate(result)["passed"] is True
        source_trace.record(result)
        assert source_trace.qualifications
        assert any(
            "SUM(" in sql.upper() and "GROUP BY" in sql.upper() for sql in source_trace.native_sql
        )
        assert work.session._runtime.store.resources(work.session.id) == ()


def test_cost_sum_increment_does_not_grant_other_duckdb_local_consumers() -> None:
    for name in ("cell.difference", "attribution.additive_difference"):
        assert not any(
            isinstance(item.key.shape, SourceShape)
            and item.key.shape.backend == "duckdb"
            and item.key.shape.time == TimeShape("instant", "us", "UTC")
            and item.key.route == "ibis_python"
            and isinstance(item.qualification, Qualified)
            and item.qualification.implementation_id.startswith("r93.c09.local.duckdb.")
            for item in implementations(MethodKey(name))
        )
