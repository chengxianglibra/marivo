"""Remote sample and window originals transport grouped leaves, not facts."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

import marivo.analysis as mv
from marivo.analysis.core.data_schema import CalendarDataAxis
from marivo.analysis.core.frame_data import MemoryFrameBacking
from marivo.analysis.errors import AnalysisError
from marivo.analysis.session.core import Session
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.reader import SemanticProject
from tests.analysis.numeric.test_remote_numeric_query import _datasource_model
from tests.datasource.environment import clickhouse_analysis, trino_analysis
from tests.datasource.source_cases import Case, SourceData, source_case, source_params
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
) -> Iterator[tuple[Session, Case]]:
    rows: list[dict[str, object]] = [
        {
            "id": index,
            "customer": ("A" if index % 2 else "B") if window else "A",
            "channel": "web" if index <= 500 else "store",
            "amount": 1
            if window
            else 100
            if index == 1 and (not zero)
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
                + "day=ms.time_dimension_column(name='day',entity=facts,column='day',is_default=True)\n"
                + "amount=ms.measure_column(name='amount',entity=facts,column='amount',additivity=ms.additive_all())\n",
            }
        )
        yield (mv.session.create("remote-samples", timezone="UTC"), case)


@pytest.mark.parametrize("backend,profile", source_params(CASES))
def test_remote_per_rollup_combines_shared_sample_before_empty_domain_mean(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
) -> None:
    with _session(backend, profile, tmp_path, monkeypatch, semantic_project_factory) as (
        session,
        case,
    ):
        amount = session.catalog.fields.get("sales.facts.amount")
        customer = session.catalog.fields.get("sales.facts.customer")
        channel = session.catalog.fields.get("sales.facts.channel")
        samples = mv.coordinates(by=(customer,), keys=(("A",), ("B",), ("C",)))
        metric = amount.sum().per(samples).mean()
        query = session.observe({"value": metric})
        trace = capture_source(monkeypatch)
        frame = query.compute().to_pandas()
        assert frame["value", "value"].tolist() == [400 / 3]
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
    ) as (session, case):
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
        query = session.observe({"value": metric}, by=(calendar,))
        trace = capture_source(monkeypatch)
        frame = query.compute().to_pandas()
        assert frame["value", "value"].tolist() == ([1000, 1000] if method == "sum" else [1.0, 1.0])


@pytest.mark.parametrize("backend,profile", source_params(CASES))
def test_remote_sample_parent_zero_denominator_is_not_hidden_by_valid_rollup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
) -> None:
    with _session(backend, profile, tmp_path, monkeypatch, semantic_project_factory, zero=True) as (
        session,
        case,
    ):
        amount = session.catalog.fields.get("sales.facts.amount")
        customer = session.catalog.fields.get("sales.facts.customer")
        channel = session.catalog.fields.get("sales.facts.channel")
        metric = amount.count().divide(amount.sum(), zero_denominator="error").per(customer).mean()
        trace = capture_source(monkeypatch)
        with pytest.raises(AnalysisError, match="zero denominator"):
            session.observe({"value": metric}, by=(channel,)).compute()


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
    with _session(backend, profile, tmp_path, monkeypatch, semantic_project_factory) as (
        session,
        case,
    ):
        amount = session.catalog.fields.get("sales.facts.amount")
        customer = session.catalog.fields.get("sales.facts.customer")
        channel = session.catalog.fields.get("sales.facts.channel")
        samples = mv.coordinates(by=(customer,), keys=(("A",), ("B",), ("C",)))
        fine = session.observe({"value": amount.sum().per(samples).mean()}, by=(channel,))
        selected = (
            fine.where(fine["value"] > 0)
            if selection == "where"
            else fine.top(0 if selection == "empty" else 2, by=fine["value"])
        )
        fixed = selected.compute()
        keys = session.population_from(fixed, on=((fixed.axes[0], channel),))
        trace = capture_source(monkeypatch)
        frame = (
            session.observe(
                {"value": amount.sum().per(samples).mean()},
                where=mv.in_population(keys),
            )
            .compute()
            .to_pandas()
        )
        assert frame["value", "value"].tolist() == [0.0 if selection == "empty" else 400 / 3]
        assert trace.returned_rows


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
    ) as (session, case):
        amount = session.catalog.fields.get("sales.facts.amount")
        customer = session.catalog.fields.get("sales.facts.customer")
        day = session.catalog.fields.get("sales.facts.day")
        calendar = session.time_scope(start="2026-10-07", end="2026-10-09").by(mv.grain("day"))
        metric = amount.sum(time=day).window(mv.all_history())
        fine = session.observe({"value": metric}, by=(calendar, customer))
        fixed = fine.top(3, by=fine["value"]).compute()
        frame = fixed.to_pandas()
        assert frame["value", "value"].tolist() == [500, 500, 500]
        assert frame.iloc[:, 1].tolist() == ["A", "B", "A"]
        assert len(frame) == 3
        assert list(
            zip(frame.iloc[:, 0], frame.iloc[:, 1], frame[("value", "value")], strict=True)
        ) == [(0, "A", 500), (0, "B", 500), (1, "A", 500)]
        backing = fixed._backing
        assert isinstance(backing, MemoryFrameBacking)
        calendar_axis = backing.result.data.schema.axes[0]
        assert isinstance(calendar_axis, CalendarDataAxis)
        assert calendar_axis.token == fixed.axes[0].token
        assert calendar_axis.frozen is not None
        (directory,) = backing.result.data.copy_attachments()
        assert directory.name == calendar_axis.frozen.directory_name
        assert directory.schema.axes[0].columns == calendar_axis.columns
        bounds = directory.rows
        assert list(
            zip(
                bounds[calendar_axis.columns[0].slot_id],
                bounds["ordinal"],
                bounds["nominal_start"],
                bounds["nominal_end"],
                bounds["actual_start"],
                bounds["actual_end"],
                strict=True,
            )
        ) == [
            (
                0,
                0,
                datetime(2026, 10, 7, tzinfo=timezone.utc),
                datetime(2026, 10, 8, tzinfo=timezone.utc),
                datetime(2026, 10, 7, tzinfo=timezone.utc),
                datetime(2026, 10, 8, tzinfo=timezone.utc),
            ),
            (
                1,
                1,
                datetime(2026, 10, 8, tzinfo=timezone.utc),
                datetime(2026, 10, 9, tzinfo=timezone.utc),
                datetime(2026, 10, 8, tzinfo=timezone.utc),
                datetime(2026, 10, 9, tzinfo=timezone.utc),
            ),
        ]


@pytest.mark.parametrize("backend,profile", source_params(CASES))
def test_remote_sample_top_zero_finishes_fine_checks_before_target_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
) -> None:
    with _session(backend, profile, tmp_path, monkeypatch, semantic_project_factory, zero=True) as (
        session,
        case,
    ):
        amount = session.catalog.fields.get("sales.facts.amount")
        customer = session.catalog.fields.get("sales.facts.customer")
        channel = session.catalog.fields.get("sales.facts.channel")
        metric = amount.count().divide(amount.sum(), zero_denominator="error").per(customer).mean()
        fine = session.observe({"value": metric}, by=(channel,))
        trace = capture_source(monkeypatch)
        with pytest.raises(AnalysisError, match="zero denominator"):
            fine.top(0, by=fine["value"]).compute()


@pytest.mark.parametrize("backend,profile", source_params(CASES[1:]))
def test_remote_selected_sample_target_uses_current_contributions_without_reranking(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
) -> None:
    with _session(backend, profile, tmp_path, monkeypatch, semantic_project_factory) as (
        session,
        case,
    ):
        amount = session.catalog.fields.get("sales.facts.amount")
        customer = session.catalog.fields.get("sales.facts.customer")
        channel = session.catalog.fields.get("sales.facts.channel")
        samples = mv.coordinates(by=(customer,), keys=(("A",), ("B",), ("C",)))
        fine = session.observe({"value": amount.sum().per(samples).mean()}, by=(channel,))
        fixed = fine.top(1, by=fine["value"]).compute()
        assert fixed.to_pandas().iloc[:, 0].tolist() == ["store"]
        assert isinstance(case.source, TableSourceIR)
        if backend == "trino":
            with trino_analysis.connection(admin=True) as connection:
                cursor = connection.cursor()
                try:
                    cursor.execute(
                        f"INSERT INTO iceberg.analysis.{case.source.table} VALUES (1001,'A','web',1000000,DATE '2026-10-07'),(1002,'A','store',7,DATE '2026-10-07')"
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
        keys = session.population_from(fixed, on=((fixed.axes[0], channel),))
        trace = capture_source(monkeypatch)
        result = (
            session.observe(
                {"value": amount.sum().per(samples).mean()},
                where=mv.in_population(keys),
            )
            .compute()
            .to_pandas()
        )
        assert result["value", "value"].tolist() == [307 / 3]
        assert fixed.to_pandas().iloc[:, 0].tolist() == ["store"]
        assert trace.returned_rows
