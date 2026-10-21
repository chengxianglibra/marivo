"""Public cross-owner temporal originals have a concrete compacting target."""

from collections.abc import Callable
from datetime import date
from pathlib import Path

import duckdb
import pytest

import marivo.analysis as mv
from marivo.analysis.errors import AnalysisError
from marivo.analysis.session.core import Session
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.reader import SemanticProject
from tests.analysis.numeric.test_remote_numeric_query import _datasource_model
from tests.datasource.source_cases import SourceData, source_case, source_params
from tests.support.source_trace import capture_source


def _session(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    factory: Callable[[dict[str, str]], SemanticProject],
    *,
    same_owner: bool = False,
    boundary: str = "ordinary",
) -> Session:
    files = {
        "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales',owner='Analytics',default=True)\n"
    }
    model = "import marivo.datasource as md\nimport marivo.semantic as ms\n"
    for name, amount in (("one", 1), ("two", 2)):
        datasource = "warehouse" if same_owner else name
        path = root / f"{datasource}.duckdb"
        table = "facts_" + name
        with duckdb.connect(str(path)) as connection:
            connection.execute(f"CREATE TABLE {table}(id BIGINT NOT NULL, day DATE, amount BIGINT)")
            values = (
                "CASE WHEN i=0 THEN ? WHEN i=1 THEN -? ELSE 0 END"
                if boundary == "overflow"
                else f"CASE WHEN i%2={0 if name == 'one' else 1} THEN ? ELSE 0 END"
                if boundary == "disjoint"
                else "?"
            )
            parameters = (
                ([2**63 - 1, 2**63 - 1] if name == "one" else [1, 1])
                if boundary == "overflow"
                else [amount]
            )
            connection.execute(
                f"INSERT INTO {table} SELECT i, CASE WHEN i%2=0 THEN DATE '2026-10-01' ELSE DATE '2026-10-02' END, {values} FROM range(120) t(i)",
                parameters,
            )
        files[f"datasources/{datasource}.py"] = (
            f"import marivo.datasource as md\nmd.duckdb(name={datasource!r},path={str(path)!r})\n"
        )
        model += (
            f"{name}=ms.entity(name={name!r},datasource=ms.ref.datasource({datasource!r}),source=md.table({table!r}),primary_key=['id'])\n"
            f"{name}_amount=ms.measure_column(name='amount',entity={name},column='amount',additivity=ms.additive_all())\n"
            f"{name}_day=ms.time_dimension_column(name='day',entity={name},column='day',is_default=True)\n"
        )
    files["sales/facts.py"] = model
    factory(files)
    monkeypatch.chdir(root)
    return mv.session.create("cross-owner-calendar", timezone="UTC")


@pytest.mark.parametrize("same_owner", (False, True))
def test_calendar_rollup_reduces_each_root_before_host_formula(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    same_owner: bool,
) -> None:
    session = _session(tmp_path, monkeypatch, semantic_project_factory, same_owner=same_owner)
    one = session.catalog.fields.get("sales.one.amount")
    two = session.catalog.fields.get("sales.two.amount")
    one_day = session.catalog.fields.get("sales.one.day")
    two_day = session.catalog.fields.get("sales.two.day")
    days = session.time_scope(start="2026-10-01", end="2026-10-03").by(mv.grain("day"))
    metric = one.sum(time=one_day) - two.sum(time=two_day)
    trace = capture_source(monkeypatch)
    result = session.observe({"net": metric}, by=(days.scope,)).compute().to_pandas()
    assert result["value", "net"].tolist() == [-120]
    assert trace.uploaded_key_rows == []


@pytest.mark.parametrize("same_owner", (False, True))
def test_calendar_parent_formula_failure_precedes_valid_target_totals(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    same_owner: bool,
) -> None:
    session = _session(
        tmp_path, monkeypatch, semantic_project_factory, same_owner=same_owner, boundary="overflow"
    )
    one = session.catalog.fields.get("sales.one.amount")
    two = session.catalog.fields.get("sales.two.amount")
    one_day = session.catalog.fields.get("sales.one.day")
    two_day = session.catalog.fields.get("sales.two.day")
    days = session.time_scope(start="2026-10-01", end="2026-10-03").by(mv.grain("day"))
    metric = one.sum(time=one_day) + two.sum(time=two_day)
    query = session.observe({"net": metric}, by=(days,))
    trace = capture_source(monkeypatch)
    with pytest.raises(AnalysisError, match="overflow"):
        query.compute()
    assert trace.uploaded_key_rows == []


def test_cross_owner_calendar_missing_days_use_additive_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    session = _session(tmp_path, monkeypatch, semantic_project_factory, boundary="disjoint")
    one = session.catalog.fields.get("sales.one.amount")
    two = session.catalog.fields.get("sales.two.amount")
    one_day = session.catalog.fields.get("sales.one.day")
    two_day = session.catalog.fields.get("sales.two.day")
    days = session.time_scope(start="2026-10-01", end="2026-10-03").by(mv.grain("day"))
    metric = one.sum(time=one_day).where(one > 0) - two.sum(time=two_day).where(two > 0)
    query = session.observe({"net": metric}, by=(days.scope,))
    daily = session.observe({"net": metric}, by=(days,)).compute().to_pandas()
    assert len(daily) == 2
    assert daily["value", "net"].tolist() == [60, -120]
    trace = capture_source(monkeypatch)
    result = query.compute().to_pandas()
    assert result["value", "net"].tolist() == [-60]


@pytest.mark.parametrize("partial", (False, True))
def test_plain_calendar_coarsening_recomputes_with_original_clipped_period(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    partial: bool,
) -> None:
    session = _session(tmp_path, monkeypatch, semantic_project_factory, same_owner=True)
    if partial:
        with duckdb.connect(str(tmp_path / "warehouse.duckdb")) as connection:
            connection.execute("ALTER TABLE facts_one ALTER COLUMN day TYPE TIMESTAMP")
    amount = session.catalog.fields.get("sales.one.amount")
    day = session.catalog.fields.get("sales.one.day")
    scope = session.time_scope(
        start="2026-10-01T12:00:00" if partial else "2026-10-01",
        end="2026-10-02T12:00:00" if partial else "2026-11-01",
    )
    days, month = (scope.by(mv.grain("day")), scope.by(mv.grain("month")))
    query = session.observe({"total": amount.sum(time=day)}, by=(month,))
    trace = capture_source(monkeypatch)
    result = query.compute().to_pandas()
    assert result["value", "total"].tolist() == [60 if partial else 120]
    assert trace.uploaded_key_rows == []


def test_plain_calendar_coarsening_checks_daily_formula_before_legal_month_total(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    session = _session(
        tmp_path, monkeypatch, semantic_project_factory, same_owner=True, boundary="overflow"
    )
    amount = session.catalog.fields.get("sales.one.amount")
    day = session.catalog.fields.get("sales.one.day")
    scope = session.time_scope(start="2026-10-01", end="2026-11-01")
    days, month = (scope.by(mv.grain("day")), scope.by(mv.grain("month")))
    query = session.observe({"value": amount.sum(time=day) + 1}, by=(days,))
    trace = capture_source(monkeypatch)
    with pytest.raises(AnalysisError, match="overflow"):
        query.compute()


@pytest.mark.parametrize(
    "backend,profile,boundary",
    source_params(
        (
            (backend, profile, boundary)
            for backend, profile in (
                ("trino", "iceberg"),
                ("clickhouse", "mergetree"),
                ("postgres", "table"),
                ("mysql", "innodb-table"),
            )
            for boundary in (
                ("ordinary", "overflow", "coarsen", "partial")
                if backend in ("trino", "clickhouse")
                else ("coarsen", "overflow")
            )
        )
    ),
)
def test_remote_cross_owner_calendar_compacts_before_global_host_formula(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
    boundary: str,
) -> None:
    rows = [
        {
            "id": index,
            "day": date(2026, 10, 1 if index % 2 == 0 else 2),
            "amount": (4 * 10**18 if index == 0 else -(4 * 10**18) if index == 1 else 0)
            if boundary == "overflow"
            else 1,
        }
        for index in range(120)
    ]
    data = SourceData(
        "id BIGINT NOT NULL, day DATE, amount BIGINT NOT NULL",
        ",".join(f"({row['id']},DATE '{row['day']}',{row['amount']})" for row in rows),
        "id Int64, day Date, amount Int64",
        rows,
    )
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        assert isinstance(case.source, TableSourceIR)
        datasource = _datasource_model(backend, case, monkeypatch)
        files = {
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales',owner='Analytics',default=True)\n"
        }
        model = "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        for name in ("one", "two"):
            files[f"datasources/{name}.py"] = datasource.replace(
                "name='warehouse'", f"name={name!r}"
            )
            model += f"{name}=ms.entity(name={name!r},datasource=ms.ref.datasource({name!r}),source=md.table({case.source.table!r},database={case.source.database!r}),primary_key=['id'])\n{name}_amount=ms.measure_column(name='amount',entity={name},column='amount',additivity=ms.additive_all())\n{name}_day=ms.time_dimension_column(name='day',entity={name},column='day',is_default=True)\n"
        files["sales/facts.py"] = model
        semantic_project_factory(files)
        session = mv.session.create("remote-cross-owner", timezone="UTC")
        one = session.catalog.fields.get("sales.one.amount")
        two = session.catalog.fields.get("sales.two.amount")
        one_day = session.catalog.fields.get("sales.one.day")
        two_day = session.catalog.fields.get("sales.two.day")
        scope = session.time_scope(
            start="2026-10-01T12:00:00" if boundary == "partial" else "2026-10-01",
            end="2026-10-02T12:00:00"
            if boundary == "partial"
            else "2026-11-01"
            if boundary == "coarsen"
            else "2026-10-03",
        )
        days = scope.by(mv.grain("day"))
        one_total, two_total = (one.sum(time=one_day), two.sum(time=two_day) * 2)
        metric = one_total + two_total if boundary == "overflow" else one_total - two_total
        query = session.observe(
            {"net": metric},
            by=(
                days
                if boundary == "overflow"
                else scope.by(mv.grain("month"))
                if boundary in ("coarsen", "partial")
                else scope,
            ),
        )
        trace = capture_source(monkeypatch)
        if boundary == "overflow":
            with pytest.raises(AnalysisError, match="overflow"):
                query.compute()
        else:
            result = query.compute().to_pandas()
            assert result["value", "net"].tolist() == [-60 if boundary == "partial" else -120]
        assert trace.returned_rows
        assert trace.uploaded_key_rows == []
