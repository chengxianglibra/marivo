"""Source totals and local row sums retain exact full-fact answers."""

from pathlib import Path

import pytest

from tests.analysis.graph.physical_workloads import workload
from tests.analysis.numeric.pandas_coordinates import ordinary_columns
from tests.shared_fixtures import terminal_runs
from tests.support.source_trace import SourceTrace


def test_duckdb_cost_routes_keep_the_original_exact_sum(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source_trace: SourceTrace
) -> None:
    with workload("duckdb", "ordinary-table", 64, "baseline", tmp_path, monkeypatch) as work:
        assert [row["amount"] for row in work.rows[:6]] == [None, 2, 3, 4, 5, 6]
        assert [row["id"] for row in work.rows[:2]] == [9007199254740992, 9007199254740993]
        values = work.values()
        queries = (work.total(), values.reduce({"value": values["value"].sum()}))
        references = []
        for query in queries:
            first = query.persist()
            assert work.validate(first)["passed"] is True
            references.append(first.artifact.id)
            before = len(terminal_runs(work.session))
            second = query.persist()
            assert work.validate(second)["passed"] is True
            assert second.artifact.id != first.artifact.id
            assert len(terminal_runs(work.session)) == before + 1
            source_trace.record(first)
            submitted = len(source_trace.native_sql)
            assert (
                work.session.persisted_frame(first.artifact.id)
                .to_pandas()
                .equals(first.to_pandas())
            )
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
            by=(session.time_scope(start="2026-08-01", end="2026-08-02"), axis),
        )
        frame = grouped.persist().to_pandas()
        assert (
            dict(zip(frame[ordinary_columns(frame)[0]], frame[("value", "value")], strict=True))
            == independent_totals
        )
        numeric_values = frame[("value", "value")].astype("float64")
        scores = (numeric_values - numeric_values.mean()) / numeric_values.std(ddof=0)
        assert dict(zip(frame[ordinary_columns(frame)[0]], scores, strict=True)) == {
            "started": -1.0,
            "finished": 1.0,
        }
        # Current-row reduction sums the explicitly observed group values.
        result = grouped.reduce({"value": grouped["value"].sum()}).persist()
        assert work.validate(result)["passed"] is True
        source_trace.record(result)
        assert source_trace.qualifications
        assert any(
            "SUM(" in sql.upper() and "GROUP BY" in sql.upper() for sql in source_trace.native_sql
        )
        assert work.session._runtime.store.resources(work.session.id) == ()
