"""Remote sample and window originals transport grouped leaves, not facts."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

import marivo.analysis as mv
from marivo.analysis.errors import AnalysisError
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import CompiledRead, SourceSession
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.reader import SemanticProject
from tests.analysis.materialization.test_source_original_multiroot_remote import _baseline
from tests.analysis.numeric.test_remote_numeric_query import _datasource_model
from tests.datasource.environment import clickhouse_analysis, trino_analysis
from tests.datasource.source_cases import SourceData, source_case, source_params
from tests.support.source_trace import capture_source

CASES = [("duckdb", "table"), ("trino", "iceberg"), ("clickhouse", "mergetree")]


@contextmanager
def _session(
    backend: str,
    profile: str,
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    factory: Callable[[dict[str, str]], SemanticProject],
    *,
    window: bool = False,
    zero: bool = False,
    change_after_parent: bool = False,
) -> Iterator[Session]:
    rows = [
        {
            "id": index,
            "customer": ("A" if index % 2 else "B") if window else "A",
            "channel": "web" if index <= 500 else "store",
            "amount": 1
            if window
            else 100
            if index == 1 and not zero
            else 300
            if index == 501
            else 0,
            "day": date(2026, 9, 20) if window and index <= 500 else date(2026, 10, 7),
        }
        for index in range(1, 1001)
    ]
    values = ",".join(
        f"({row['id']},'{row['customer']}','{row['channel']}',{row['amount']},DATE '{row['day']}')"
        for row in rows
    )
    data = SourceData(
        "id BIGINT NOT NULL, customer VARCHAR(16) NOT NULL, channel VARCHAR(16) NOT NULL, amount BIGINT NOT NULL, day DATE",
        values,
        "id Int64, customer String, channel String, amount Int64, day Date",
        rows,
    )
    monkeypatch.chdir(root)
    with source_case(backend, profile, root, monkeypatch, data) as case:
        assert isinstance(case.source, TableSourceIR)
        factory(
            {
                "datasources/warehouse.py": _datasource_model(backend, case, monkeypatch),
                "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales',owner='Analytics',default=True)\n",
                "sales/facts.py": "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                + f"facts=ms.entity(name='facts',datasource=ms.ref.datasource('warehouse'),source=md.table({case.source.table!r},database={case.source.database!r}),primary_key=['id'])\n"
                + "customer=ms.dimension_column(name='customer',entity=facts,column='customer')\n"
                + "channel=ms.dimension_column(name='channel',entity=facts,column='channel')\n"
                + "day=ms.time_dimension_column(name='day',entity=facts,column='day',granularity='day',is_default=True)\n"
                + "amount=ms.measure_column(name='amount',entity=facts,column='amount',additivity=ms.additive_all())\n",
            }
        )
        if change_after_parent:
            previous = SourceSession.execute
            changed = False

            def executed(owner: SourceSession, read: CompiledRead) -> pd.DataFrame:
                nonlocal changed
                frame = previous(owner, read)
                if read.purpose.startswith("query.") and not changed:
                    assert isinstance(case.source, TableSourceIR)
                    if backend == "trino":
                        with trino_analysis.connection(admin=True) as connection:
                            cursor = connection.cursor()
                            try:
                                cursor.execute(
                                    f"INSERT INTO iceberg.analysis.{case.source.table} VALUES "
                                    "(1001,'A','web',1000000,DATE '2026-10-07'),"
                                    "(1002,'A','store',7,DATE '2026-10-07')"
                                ).fetchall()
                            finally:
                                cursor.close()
                    else:
                        with clickhouse_analysis.connection(admin=True) as connection:
                            connection.insert(
                                "qualification." + case.source.table,
                                [
                                    [1001, "A", "web", 1000000, date(2026, 10, 7)],
                                    [1002, "A", "store", 7, date(2026, 10, 7)],
                                ],
                                column_names=["id", "customer", "channel", "amount", "day"],
                            )
                    changed = True
                return frame

            monkeypatch.setattr(SourceSession, "execute", executed)
        yield mv.session.get_or_create("remote-samples", report_timezone="UTC")


@pytest.mark.parametrize("backend,profile", source_params(CASES))
@pytest.mark.parametrize("empty", ("zero", "null"))
def test_remote_per_rollup_combines_shared_sample_before_empty_domain_mean(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
    empty: str,
) -> None:
    with _session(backend, profile, tmp_path, monkeypatch, semantic_project_factory) as session:
        amount = session.catalog.fields.get("sales.facts.amount")
        customer = session.catalog.fields.get("sales.facts.customer")
        channel = session.catalog.fields.get("sales.facts.channel")
        samples = mv.coordinates(by=(customer,), keys=(("A",), ("B",), ("C",)))
        metric = amount.sum(empty=empty).per(samples).mean()
        query = session.observe({"value": metric}, by=(channel,)).rollup()
        expected = _baseline(query, monkeypatch)
        trace = capture_source(monkeypatch)
        frame = query.execute().to_pandas()
        pd.testing.assert_frame_equal(frame, expected, check_exact=True)
        assert frame[("value", "value")].tolist() == [400 / 3 if empty == "zero" else 400]
        assert trace.returned_rows == [3]
        assert trace.uploaded_key_rows == []
        assert not any(
            "__original_value" in name for names in trace.returned_columns for name in names
        )


@pytest.mark.parametrize("backend,profile", source_params(CASES))
@pytest.mark.parametrize("method", ("sum", "mean", "ratio"))
def test_remote_window_spatial_rollup_retains_lookback_and_each_endpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
    method: str,
) -> None:
    with _session(
        backend, profile, tmp_path, monkeypatch, semantic_project_factory, window=True
    ) as session:
        amount = session.catalog.fields.get("sales.facts.amount")
        customer = session.catalog.fields.get("sales.facts.customer")
        day = session.catalog.fields.get("sales.facts.day")
        calendar = session.time_scope(start="2026-10-07", end="2026-10-09").by(mv.grain("day"))
        total = amount.sum(time=day)
        metric = (
            total / amount.count(time=day)
            if method == "ratio"
            else amount.mean(time=day)
            if method == "mean"
            else total
        ).window(mv.all_history())
        query = session.observe({"value": metric}, during=calendar, by=(customer,)).rollup(
            by=(calendar,)
        )
        expected = _baseline(query, monkeypatch)
        trace = capture_source(monkeypatch)
        frame = query.execute().to_pandas()
        pd.testing.assert_frame_equal(frame, expected, check_exact=True)
        assert frame[("value", "value")].tolist() == (
            [1000, 1000] if method == "sum" else [1.0, 1.0]
        )
        assert trace.returned_rows == [12 if method == "ratio" else 6]
        assert trace.uploaded_key_rows == []


@pytest.mark.parametrize("backend,profile", source_params(CASES))
def test_remote_sample_parent_zero_denominator_is_not_hidden_by_valid_rollup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
) -> None:
    with _session(
        backend, profile, tmp_path, monkeypatch, semantic_project_factory, zero=True
    ) as session:
        amount = session.catalog.fields.get("sales.facts.amount")
        customer = session.catalog.fields.get("sales.facts.customer")
        channel = session.catalog.fields.get("sales.facts.channel")
        metric = amount.count().divide(amount.sum(), zero_denominator="error").per(customer).mean()
        trace = capture_source(monkeypatch)
        with pytest.raises(AnalysisError, match="zero denominator"):
            session.observe({"value": metric}, by=(channel,)).rollup().execute()
        assert trace.returned_rows == [6]


@pytest.mark.parametrize("backend,profile", source_params(CASES))
@pytest.mark.parametrize("selection", ("top_two", "where", "empty"))
def test_remote_selected_per_rollup_recomputes_complete_shared_sample_union(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
    selection: str,
) -> None:
    with _session(backend, profile, tmp_path, monkeypatch, semantic_project_factory) as session:
        amount = session.catalog.fields.get("sales.facts.amount")
        customer = session.catalog.fields.get("sales.facts.customer")
        channel = session.catalog.fields.get("sales.facts.channel")
        samples = mv.coordinates(by=(customer,), keys=(("A",), ("B",), ("C",)))
        fine = session.observe(
            {"value": amount.sum(empty="zero").per(samples).mean()}, by=(channel,)
        )
        selected = (
            fine.where(fine["value"] > 0)
            if selection == "where"
            else fine.top(0 if selection == "empty" else 2, by=fine["value"])
        )
        query = selected.rollup()
        expected = _baseline(query, monkeypatch)
        trace = capture_source(monkeypatch)
        frame = query.execute().to_pandas()
        pd.testing.assert_frame_equal(frame, expected, check_exact=True)
        single_batch = backend == "duckdb" and selection == "empty"
        assert trace.returned_rows == ([4] if single_batch else [2, 1])
        assert trace.uploaded_key_rows == (
            [] if single_batch else [0 if selection == "empty" else 2]
        )


@pytest.mark.parametrize("backend,profile", source_params(CASES))
def test_remote_selected_window_rollup_keeps_correlated_endpoint_and_parent_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
) -> None:
    with _session(
        backend, profile, tmp_path, monkeypatch, semantic_project_factory, window=True
    ) as session:
        amount = session.catalog.fields.get("sales.facts.amount")
        customer = session.catalog.fields.get("sales.facts.customer")
        day = session.catalog.fields.get("sales.facts.day")
        calendar = session.time_scope(start="2026-10-07", end="2026-10-09").by(mv.grain("day"))
        metric = amount.sum(time=day).window(mv.all_history())
        fine = session.observe({"value": metric}, during=calendar, by=(customer,))
        query = fine.top(3, by=fine["value"]).rollup(by=(calendar,))
        expected = _baseline(query, monkeypatch)
        trace = capture_source(monkeypatch)
        frame = query.execute().to_pandas()
        pd.testing.assert_frame_equal(frame, expected, check_exact=True)
        assert frame[("value", "value")].tolist() == [1000, 500]
        assert trace.returned_rows == [4, 4]
        assert trace.uploaded_key_rows == [3]


@pytest.mark.parametrize("backend,profile", source_params(CASES))
def test_remote_sample_top_zero_finishes_fine_checks_before_target_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
) -> None:
    with _session(
        backend, profile, tmp_path, monkeypatch, semantic_project_factory, zero=True
    ) as session:
        amount = session.catalog.fields.get("sales.facts.amount")
        customer = session.catalog.fields.get("sales.facts.customer")
        channel = session.catalog.fields.get("sales.facts.channel")
        metric = amount.count().divide(amount.sum(), zero_denominator="error").per(customer).mean()
        fine = session.observe({"value": metric}, by=(channel,))
        trace = capture_source(monkeypatch)
        with pytest.raises(AnalysisError, match="zero denominator"):
            fine.top(0, by=fine["value"]).rollup().execute()
        assert trace.returned_rows == ([8] if backend == "duckdb" else [4])
        assert trace.uploaded_key_rows == []


@pytest.mark.parametrize("backend,profile", source_params(CASES[1:]))
def test_remote_selected_sample_target_uses_current_contributions_without_reranking(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
) -> None:
    with _session(
        backend,
        profile,
        tmp_path,
        monkeypatch,
        semantic_project_factory,
        change_after_parent=True,
    ) as session:
        amount = session.catalog.fields.get("sales.facts.amount")
        customer = session.catalog.fields.get("sales.facts.customer")
        channel = session.catalog.fields.get("sales.facts.channel")
        samples = mv.coordinates(by=(customer,), keys=(("A",), ("B",), ("C",)))
        fine = session.observe(
            {"value": amount.sum(empty="zero").per(samples).mean()}, by=(channel,)
        )
        trace = capture_source(monkeypatch)
        result = fine.top(1, by=fine["value"]).rollup().execute().to_pandas()
        assert result[("value", "value")].tolist() == [307 / 3]
        assert trace.returned_rows == [2, 1]
        assert trace.uploaded_key_rows == [1]
