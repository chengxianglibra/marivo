"""Public C-family source counterexamples; invocations alone grant no qualification."""

from collections.abc import Callable
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Literal, NoReturn
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError
from marivo.datasource.adapters import SourceSession
from marivo.datasource.capabilities import provider_statement_log
from marivo.datasource.ir import DatasourceIR
from marivo.datasource.runtime import DatasourceConnectionService
from marivo.semantic.reader import SemanticProject
from tests.analysis.graph.source_fixtures import author_source_project
from tests.analysis.numeric.pandas_coordinates import ordinary_columns
from tests.datasource.source_cases import SourceData, source_case, source_params
from tests.shared_fixtures import terminal_runs
from tests.support.source_trace import SourceTrace


def _value(result: mv.AnalysisFrame) -> pd.Series:
    return result.to_pandas()["value", "value"]


def _full(
    session: mv.Session,
    metric: str = "sales.total",
    *,
    during: mv.TimeScope | mv.CalendarAxis | None = None,
) -> mv.AnalysisFrame:
    return session.observe(
        {"value": session.catalog.metrics.get(metric)},
        by=mv.product(during, session.members(session.catalog.entities.get("sales.facts")))
        if during is not None
        else session.members(session.catalog.entities.get("sales.facts")),
    )


@pytest.mark.parametrize(
    ("backend", "time_case"),
    source_params(
        [
            (backend, time_case)
            for backend in ("duckdb", "postgres", "mysql", "trino", "clickhouse")
            for time_case in ("utc", "dst")
        ]
    ),
)
def test_declared_string_time_uses_owned_execution_functions(
    backend: str,
    time_case: Literal["utc", "dst"],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    source_trace: SourceTrace,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    facts = [
        (9007199254740992, 1, "a", 2, "2026-08-01 00:00:00"),
        (9007199254740993, 2, "a", None, "2026-08-01 00:00:00"),
        (9007199254740994, 1, "a", 99, "2026-08-02 00:00:00"),
        (9007199254740995, 1, "a", 77, "2026-07-31 23:59:59"),
    ]
    zone = ZoneInfo("America/New_York") if time_case == "dst" else ZoneInfo("UTC")
    start = (
        datetime(2026, 11, 1, tzinfo=zone)
        if time_case == "dst"
        else datetime(2026, 8, 1, tzinfo=zone)
    )
    end = (
        datetime(2026, 11, 2, tzinfo=zone)
        if time_case == "dst"
        else datetime(2026, 8, 2, tzinfo=zone)
    )
    if time_case == "dst":
        facts = [
            (9007199254740992, 1, "a", 2, "2026-11-01 04:00:00"),
            (9007199254740993, 2, "a", None, "2026-11-01 04:00:00"),
            (9007199254740994, 1, "a", 99, "2026-11-02 05:00:00"),
            (9007199254740995, 1, "a", 77, "2026-11-01 03:59:59"),
            (9007199254740996, 1, "a", 5, "2026-11-02 04:30:00"),
        ]
    data = SourceData(
        "id BIGINT, revision BIGINT, tenant VARCHAR(10), amount BIGINT, happened VARCHAR(32)",
        ",".join(
            (
                f"({identity},{revision},'{tenant}',{(amount if amount is not None else 'NULL')},'{instant}')"
                for identity, revision, tenant, amount, instant in facts
            )
        ),
        "id Int64, revision Int64, tenant String, amount Nullable(Int64), happened String",
        [
            {
                "id": identity,
                "revision": revision,
                "tenant": tenant,
                "amount": amount,
                "happened": instant,
            }
            for identity, revision, tenant, amount, instant in facts
        ],
    )
    profile = (
        "iceberg" if backend == "trino" else "mergetree" if backend == "clickhouse" else "table"
    )
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        author_source_project(
            backend, case, monkeypatch, semantic_project_factory, time_parse="string"
        )
        session = mv.session.create("r93-string-time", timezone=str(zone))
        values = session.observe(
            {"value": session.catalog.metrics.get("sales.total")},
            by=mv.product(
                session.time_scope(start=start, end=end),
                session.members(session.catalog.entities.get("sales.facts")),
            ),
        )
        assert isinstance(values, mv.AnalysisFrame)
        result = values.persist()
        frame = result.to_pandas()
        frame = frame.set_index(ordinary_columns(frame)[0])
        expected_values = {
            ("a", 9007199254740992, 1): 2,
            ("a", 9007199254740993, 2): None,
            ("a", 9007199254740994, 1): 0,
            ("a", 9007199254740995, 1): 0,
        }
        if time_case == "dst":
            expected_values["a", 9007199254740996, 1] = 5
        assert frame["value", "value"].to_dict() == expected_values
        assert frame["value", "value"].isna().sum() == 1
        assert session._runtime.store.resources(session._runtime.session_ref) == ()
        source_trace.save(
            f"c06-source-{backend}-{('dst' if time_case == 'dst' else 'string-time')}",
            {**case.environment, "profile": profile},
            {"columns": data.columns, "values": data.values},
            {
                "values": [2, 0, 0, 0, 5] if time_case == "dst" else [2, 0, 0, 0],
                "report_timezone": str(zone),
                "window_seconds": int(
                    (end.astimezone(timezone.utc) - start.astimezone(timezone.utc)).total_seconds()
                ),
                "last_hour_included": time_case == "dst",
                "declared_format": "%Y-%m-%d %H:%M:%S",
                "start_inclusive": True,
                "end_exclusive": True,
                "before_start_excluded": True,
                "resources": 0,
            },
            result,
            (case.session,),
        )


@pytest.mark.parametrize("numeric_type", ["int64", "float64"])
@pytest.mark.parametrize("backend", ["duckdb"])
@pytest.mark.slow
def test_source_coordinates_statistics_and_original_state(
    backend: str,
    numeric_type: Literal["int64", "float64"],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    source_trace: SourceTrace,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    instants = datetime(2026, 8, 1, tzinfo=timezone.utc)
    data = SourceData(
        "id BIGINT, revision BIGINT, tenant VARCHAR(10), amount "
        + ("BIGINT" if numeric_type == "int64" else "DOUBLE PRECISION")
        + ", happened TIMESTAMP",
        ",".join(
            (
                f"({identity},{revision},'{tenant}',{amount},"
                + ("TIMESTAMP " if backend == "trino" else "")
                + "'2026-08-01 00:00:00')"
                for identity, revision, tenant, amount in (
                    (9007199254740992, 1, "a", "2"),
                    (9007199254740993, 2, "a", "NULL"),
                    (9007199254740993, 1, "b", "4" if numeric_type == "int64" else "4.5"),
                )
            )
        ),
        "id Int64, revision Int64, tenant String, amount Nullable("
        + ("Int64" if numeric_type == "int64" else "Float64")
        + "), happened DateTime64(6, 'UTC')",
        [
            {
                "id": identity,
                "revision": revision,
                "tenant": tenant,
                "amount": float(amount)
                if numeric_type == "float64" and amount is not None
                else amount,
                "happened": instants,
            }
            for identity, revision, tenant, amount in (
                (9007199254740992, 1, "a", 2),
                (9007199254740993, 2, "a", None),
                (9007199254740993, 1, "b", 4 if numeric_type == "int64" else 4.5),
            )
        ],
    )
    profile = (
        "iceberg" if backend == "trino" else "mergetree" if backend == "clickhouse" else "table"
    )
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        remote = author_source_project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.create("r93-c05", timezone="UTC")
        members = session.members(session.catalog.entities.get("sales.facts"))
        bucket = session.catalog.fields.get("sales.facts.bucket")
        revision = session.catalog.fields.get("sales.facts.revision")
        read = session.read(members, {"value": bucket})
        chosen = read.where(read["value"] == "a")
        selected = session.population_from(
            chosen, on=((chosen.axes[0], session.catalog.entities.get("sales.facts")),)
        )
        values = session.observe(
            {"value": session.catalog.metrics.get("sales.total")},
            by=(selected.axes[0], bucket, revision),
            where=mv.in_population(selected),
        )
        counted = values.reduce({"value": mv.row_count()}, by=(values.axes[1],)).persist()
        assert _value(counted).tolist() == [2]
        tuples = values.reduce(
            {"value": mv.row_count()}, by=(values.axes[1], values.axes[2])
        ).persist()
        frame = tuples.to_pandas()
        assert list(frame.iloc[:, 0]) == ["a", "a"]
        assert list(frame.iloc[:, 1]) == [1, 2]
        assert frame[("value", "value")].tolist() == [1, 1]
        summed = values.reduce({"value": values["value"].sum()}, by=(values.axes[1],)).persist()
        expected_sum = Decimal("2") if numeric_type == "int64" else 2.0
        assert _value(summed).tolist() == [expected_sum]
        defined_values = values.where(~values["value"].is_null())
        defined = defined_values.reduce(
            {"value": mv.row_count()}, by=(defined_values.axes[1],)
        ).persist()
        assert _value(defined).tolist() == [1]
        mean = values.reduce({"value": values["value"].mean()}, by=(values.axes[1],)).persist()
        expected_mean = Decimal("2") if numeric_type == "int64" else 2.0
        assert _value(mean).tolist() == [expected_mean]
        original = session.observe(
            {"value": session.catalog.metrics.get("sales.total")},
            by=(bucket,),
            where=mv.in_population(selected),
        ).persist()
        assert _value(original).tolist() == [2]
        assert _value(
            session.observe(
                {"value": session.catalog.metrics.get("sales.total")},
                where=mv.in_population(selected),
            ).persist()
        ).tolist() == [2]
        full = _full(session)
        if numeric_type == "int64":
            current = _full(
                session, during=session.time_scope(start="2026-08-01", end="2026-08-02")
            )
            baseline = _full(
                session, during=session.time_scope(start="2026-08-02", end="2026-08-03")
            )
            difference = (
                current.compare(
                    {"reference": baseline},
                    on={"reference": tuple(zip(current.axes, baseline.axes, strict=True))},
                    match={"reference": mv.ordinal()},
                )
                .persist()
                .to_pandas()
            )
            differences = difference["value", "reference.absolute.value"].tolist()
            assert dict(
                zip(
                    difference.iloc[:, 1],
                    [None if pd.isna(value) else value for value in differences],
                    strict=True,
                )
            ) == {
                ("a", 9007199254740992, 1): 2,
                ("a", 9007199254740993, 2): None,
                ("b", 9007199254740993, 1): 4,
            }
        full_sum = 6 if numeric_type == "int64" else 6.5
        assert _value(
            session.observe({"value": session.catalog.metrics.get("sales.total")}).persist()
        ).tolist() == [full_sum]
        full_mean = full.reduce({"value": full["value"].mean()}).persist()
        expected_full_mean = Decimal("3") if numeric_type == "int64" else 3.25
        if numeric_type == "int64":
            assert _value(full_mean).tolist() == [expected_full_mean]
        else:
            assert _value(full_mean).tolist() == pytest.approx(
                [expected_full_mean], abs=1e-12, rel=0
            )
        average = _full(session, "sales.average")
        assert _value(average.reduce({"value": mv.row_count()}).persist()).tolist() == [3]
        supported = average.where(~average["value"].is_null())
        assert _value(supported.reduce({"value": mv.row_count()}).persist()).tolist() == [2]
        assert _value(average.reduce({"value": average["value"].sum()}).persist()).tolist() == [
            full_sum
        ]
        assert _value(average.reduce({"value": average["value"].mean()}).persist()).tolist() == [
            full_sum / 2
        ]
        before = set(tmp_path.rglob("*.parquet"))
        with pytest.raises(AnalysisError):
            full.select({"value": full["value"].divide(0, zero_denominator="error")}).persist()
        assert set(tmp_path.rglob("*.parquet")) == before
        assert _value(original).tolist() == [2]
        absent = read.where(read["value"] == "missing")
        empty_members = session.population_from(
            absent, on=((absent.axes[0], session.catalog.entities.get("sales.facts")),)
        )
        empty = session.observe(
            {"value": session.catalog.metrics.get("sales.total")}, by=empty_members
        )
        assert _value(empty.reduce({"value": mv.row_count()}).persist()).tolist() == [0]
        assert _value(empty.reduce({"value": empty["value"].mean()}).persist()).isna().all()
        for result in (counted, tuples, summed, defined, mean, original):
            source_trace.record(result)
        assert session._runtime.store.resources(session.id) == ()
        source_trace.save(
            f"c05-source-{backend}-{numeric_type}",
            {**case.environment, "profile": profile},
            {"columns": data.columns, "values": data.values},
            {
                "counts_a": 2,
                "sum_a": 2,
                "mean_a": 1.0,
                "full_sum": full_sum,
                "full_mean": full_sum / 3,
                "defined_source_mean_count": 2,
                "empty_count": 0,
            },
            None,
            (case.session,),
        )


@pytest.mark.parametrize("identity_type", ["int64", "string"])
@pytest.mark.parametrize("backend", ["duckdb"])
def test_original_mean_preserves_component_weights(
    backend: str,
    identity_type: Literal["int64", "string"],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    source_trace: SourceTrace,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    facts = [(0, "a", 1), (1, "a", 1), (2, "b", 100)]
    data = SourceData(
        "id BIGINT, revision BIGINT, tenant VARCHAR(10), amount BIGINT, happened TIMESTAMP",
        ",".join(
            (
                f"({9007199254740992 + i},1,'{tenant}',{amount},"
                + ("TIMESTAMP " if backend == "trino" else "")
                + "'2026-08-01 00:00:00')"
                for i, tenant, amount in facts
            )
        ),
        "id Int64, revision Int64, tenant String, amount Int64, happened DateTime64(6, 'UTC')",
        [
            {
                "id": 9007199254740992 + i,
                "revision": 1,
                "tenant": tenant,
                "amount": amount,
                "happened": datetime(2026, 8, 1, tzinfo=timezone.utc),
            }
            for i, tenant, amount in facts
        ],
    )
    profile = (
        "iceberg" if backend == "trino" else "mergetree" if backend == "clickhouse" else "table"
    )
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        remote = author_source_project(
            backend, case, monkeypatch, semantic_project_factory, identity_type
        )
        session = mv.session.create("r93-c05-mean", timezone="UTC")
        bucket = session.catalog.fields.get("sales.facts.bucket")
        amount = session.catalog.fields.get("sales.facts.amount")
        time = session.catalog.fields.get("sales.facts.happened")
        grouped = session.observe(
            {
                "value": session.catalog.metrics.get("sales.average"),
                "sum": session.catalog.metrics.get("sales.total"),
                "count": amount.count(time=time),
            },
            by=(bucket,),
        )
        current = grouped.reduce({"value": grouped["value"].mean()}).persist()
        original = session.observe(
            {"value": session.catalog.metrics.get("sales.average")}
        ).persist()
        assert _value(current).tolist() == [50.5]
        assert _value(original).tolist() == [34.0]
        fixed = grouped.persist()
        exported = fixed.to_pandas()
        frame = exported.set_index(exported.columns[0])
        assert frame["value", "value"].to_dict() == {"a": 1.0, "b": 100.0}
        assert dict(zip(frame["value", "sum"], frame["value", "count"], strict=True)) == {
            2: 2,
            100: 1,
        }

        def forbid_source(*args: object, **kwargs: object) -> NoReturn:
            pytest.fail("Terminal mean calculation read the source")

        submissions_before = len(source_trace.native_sql)
        with monkeypatch.context() as isolated:
            isolated.setattr(type(case.session), "execute", forbid_source)
            saved = fixed.to_pandas()
            assert saved["value", "value"].mean() == 50.5
            assert saved["value", "sum"].sum() / saved["value", "count"].sum() == 34.0
        assert len(source_trace.native_sql) == submissions_before
        for result in (current, original, fixed):
            source_trace.record(result)
        source_trace.save(
            f"c05-mean-{backend}-{identity_type}",
            {**case.environment, "profile": profile},
            {"columns": data.columns, "values": data.values},
            {
                "current": 50.5,
                "original": 34.0,
                "rows": 3,
                "components": {"a": [2, 2], "b": [100, 1]},
            },
            fixed,
            (case.session,),
        )


@pytest.mark.parametrize("backend", ["duckdb"])
@pytest.mark.parametrize(
    ("anchor", "expected", "overlap"),
    [
        ("mv.all_history()", [10, 28], True),
        ("mv.to_date(grain=mv.grain('month'))", [5, 18], False),
        ("mv.trailing_duration(duration=timedelta(days=31))", [5, 21], True),
    ],
    ids=["all-history", "month-reset", "trailing-overlap"],
)
def test_cumulative_keeps_anchor_and_overlap(
    backend: str,
    anchor: str,
    expected: list[int],
    overlap: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    source_trace: SourceTrace,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    facts = [
        (1, 5, "2026-07-31 00:00:00"),
        (2, 2, "2026-08-01 00:00:00"),
        (3, 3, "2026-08-31 00:00:00"),
        (4, 7, "2026-09-01 00:00:00"),
        (5, 11, "2026-09-30 00:00:00"),
        (6, 99, "2026-10-01 00:00:00"),
    ]
    data = SourceData(
        "id BIGINT, revision BIGINT, tenant VARCHAR(10), amount BIGINT, happened VARCHAR(32)",
        ",".join(
            (f"({identity},1,'a',{amount},'{instant}')" for identity, amount, instant in facts)
        ),
        "id Int64, revision Int64, tenant String, amount Int64, happened String",
        [
            {"id": identity, "revision": 1, "tenant": "a", "amount": amount, "happened": instant}
            for identity, amount, instant in facts
        ],
    )
    profile = (
        "iceberg" if backend == "trino" else "mergetree" if backend == "clickhouse" else "table"
    )
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        author_source_project(
            backend,
            case,
            monkeypatch,
            semantic_project_factory,
            time_parse="string",
            cumulative_anchor=anchor,
        )
        session = mv.session.create("r93-cumulative", timezone="UTC")
        grid = session.time_scope(start="2026-08-15", end="2026-10-01").by(mv.grain("month"))
        logical = session.observe(
            {"value": session.catalog.metrics.get("sales.running")},
            by=mv.product(grid, session.members(session.catalog.entities.get("sales.facts"))),
        )
        fixed = logical.persist()
        source_trace.record(fixed)
        grouped = session.observe(
            {"value": session.catalog.metrics.get("sales.running")}, by=(grid,)
        ).persist()
        assert _value(grouped).tolist() == expected
        assert not hasattr(fixed, "rollup")
        if not overlap:
            assert _value(grouped).sum() == 23
        assert session._runtime.store.resources(session._runtime.session_ref) == ()
        name = (
            "all-history"
            if anchor == "mv.all_history()"
            else "trailing-overlap"
            if overlap
            else "month-reset"
        )
        source_trace.save(
            f"c06-source-{backend}-{name}",
            {**case.environment, "profile": profile},
            {"columns": data.columns, "values": data.values},
            {
                "values": expected,
                "anchor": anchor,
                "overlap": overlap,
                "display_start_does_not_clip_anchor": True,
                "end_exclusive": True,
                "resources": 0,
            },
            grouped,
            (case.session,),
        )


@pytest.mark.parametrize(
    "backend", source_params(["duckdb", "postgres", "mysql", "trino", "clickhouse"])
)
@pytest.mark.parametrize("transition", ["spring", "fall"])
def test_dst_grid_keeps_instant_edges_and_fixed_coordinate(
    backend: str,
    transition: Literal["spring", "fall"],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    source_trace: SourceTrace,
) -> None:
    from marivo._temporal_grid import bind_grid

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    start, end = (
        ("2026-03-08", "2026-03-10") if transition == "spring" else ("2026-11-01", "2026-11-03")
    )
    instants = (
        ["2026-03-08 05:00:00", "2026-03-09 03:30:00", "2026-03-09 04:00:00", "2026-03-10 04:00:00"]
        if transition == "spring"
        else [
            "2026-11-01 04:00:00",
            "2026-11-02 04:30:00",
            "2026-11-02 05:00:00",
            "2026-11-03 05:00:00",
        ]
    )
    facts = list(zip([1, 2, 3, 4], [2, 5, 11, 99], instants, strict=True))
    data = SourceData(
        "id BIGINT, revision BIGINT, tenant VARCHAR(10), amount BIGINT, happened VARCHAR(32)",
        ",".join(
            (f"({identity},1,'a',{amount},'{instant}')" for identity, amount, instant in facts)
        ),
        "id Int64, revision Int64, tenant String, amount Int64, happened String",
        [
            {"id": identity, "revision": 1, "tenant": "a", "amount": amount, "happened": instant}
            for identity, amount, instant in facts
        ],
    )
    profile = (
        "iceberg" if backend == "trino" else "mergetree" if backend == "clickhouse" else "table"
    )
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        author_source_project(
            backend, case, monkeypatch, semantic_project_factory, time_parse="string"
        )
        session = mv.session.create("r93-dst-grid", timezone="Asia/Tokyo")
        zone = ZoneInfo("America/New_York")
        scope = session.time_scope(
            start=datetime.fromisoformat(start).replace(tzinfo=zone),
            end=datetime.fromisoformat(end).replace(tzinfo=zone),
            timezone=str(zone),
        )
        grain = mv.grain("day")
        grid = scope.by(grain)
        bound = bind_grid(
            scope, grain, report_timezone="Asia/Tokyo", explicit_timezone="America/New_York"
        )
        hours = [23, 24] if transition == "spring" else [25, 24]
        assert [(c.end - c.start).total_seconds() / 3600 for c in bound.cells] == hours
        assert [c.start.strftime("%Y-%m-%d %H:%M:%S") for c in bound.cells] == [
            instants[0],
            instants[2],
        ]
        assert bound.cells[-1].end.strftime("%Y-%m-%d %H:%M:%S") == instants[3]
        logical = session.observe(
            {"value": session.catalog.metrics.get("sales.total")},
            by=mv.product(grid, session.members(session.catalog.entities.get("sales.facts"))),
        )
        fixed = logical.persist()
        frame = fixed.to_pandas()
        assert len(frame) == 8
        assert not frame["value", "value"].isna().any()
        source_trace.record(fixed)
        grouped = session.observe(
            {"value": session.catalog.metrics.get("sales.total")}, by=(grid,)
        ).persist()
        assert _value(grouped).tolist() == [7, 11]
        assert session._runtime.store.resources(session._runtime.session_ref) == ()
        source_trace.save(
            f"c06-source-{backend}-{transition}-grid",
            {**case.environment, "profile": profile},
            {"columns": data.columns, "values": data.values},
            {
                "values": [7, 11],
                "cell_hours": hours,
                "product_rows": 8,
                "source_timezone": "UTC",
                "report_timezone": "Asia/Tokyo",
                "grid_timezone": "America/New_York",
                "end_exclusive": True,
                "last_half_hour_included": True,
                "resources": 0,
            },
            grouped,
            (case.session,),
        )


@pytest.mark.parametrize(
    "backend", source_params(["duckdb", "postgres", "mysql", "trino", "clickhouse"])
)
def test_certified_unequal_calendar_owns_native_boundaries(
    backend: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    source_trace: SourceTrace,
) -> None:
    from datetime import date, timedelta

    import marivo.datasource as md

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    facts = [
        (
            i + 1,
            amount,
            date(2026, 8, 1) + timedelta(days=i),
            (datetime(2026, 7, 31, 16) + timedelta(days=i)).strftime("%Y-%m-%d %H:%M:%S"),
            "P1" if i < 2 else "P2",
        )
        for i, amount in enumerate([2, 5, 11, 13, 17])
    ]
    data = SourceData(
        "id BIGINT, revision BIGINT, tenant VARCHAR(10), amount BIGINT, happened VARCHAR(32), calendar_day DATE, period VARCHAR(10)",
        ",".join(
            (
                f"({identity},1,'a',{amount},'{instant}',{('DATE ' if backend == 'trino' else '')}'{day}','{period}')"
                for identity, amount, day, instant, period in facts
            )
        ),
        "id Int64, revision Int64, tenant String, amount Int64, happened String, calendar_day Date, period String",
        [
            {
                "id": identity,
                "revision": 1,
                "tenant": "a",
                "amount": amount,
                "happened": instant,
                "calendar_day": day,
                "period": period,
            }
            for identity, amount, day, instant, period in facts
        ],
    )
    profile = (
        "iceberg" if backend == "trino" else "mergetree" if backend == "clickhouse" else "table"
    )
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        author_source_project(
            backend,
            case,
            monkeypatch,
            semantic_project_factory,
            time_parse="string",
            certified_calendar=True,
        )
        catalog = ms.load(workspace_dir=tmp_path)
        calendar = ms.ref.period_calendar("sales.fiscal")
        source_session = DatasourceConnectionService.source_session
        certification_owners: list[SourceSession] = []

        def capture_certification_owner(
            connections: DatasourceConnectionService, name: str, datasource: DatasourceIR
        ) -> SourceSession:
            owner = source_session(connections, name, datasource)
            if owner not in certification_owners:
                certification_owners.append(owner)
            return owner

        monkeypatch.setattr(
            DatasourceConnectionService, "source_session", capture_certification_owner
        )
        catalog.preview(calendar, scope=md.unpruned(max_rows=5, timeout_seconds=30))
        assert any(
            submission.purpose == "semantic.certified_preview"
            for owner in certification_owners
            for submission in owner.submissions
        )
        for owner in certification_owners:
            if owner not in source_trace.owners:
                source_trace.owners.append(owner)
        assert all(owner._closed for owner in certification_owners)
        deadline_controls = [
            {
                "statement_id": item.statement_id,
                "purpose": item.purpose,
                "sql": item.sql,
                "state": item.state,
            }
            for owner in certification_owners
            for item in provider_statement_log(owner._backend)
            if item.statement_id.startswith("mysql.authoring.")
        ]
        if backend == "mysql":
            assert [item["statement_id"] for item in deadline_controls] == [
                "mysql.authoring.install_select_deadline",
                "mysql.authoring.read_select_deadline",
            ]
            assert all(
                item["state"] == "succeeded"
                and item["purpose"] == "semantic.certified_preview.deadline"
                for item in deadline_controls
            )
        entry = catalog.period_calendars.get(calendar.path)
        assert entry.details().snapshot_status == "current"
        grain = entry.grain("period")
        session = mv.session.create("r93-certified-calendar", timezone="UTC")
        scope = session.time_scope(
            start=datetime(2026, 7, 31, 16, tzinfo=timezone.utc),
            end=datetime(2026, 8, 5, 16, tzinfo=timezone.utc),
            timezone="Asia/Shanghai",
        )
        grid = scope.by(grain)
        members = session.members(session.catalog.entities.get("sales.facts"))
        with pytest.raises(ValueError) as conflict:
            session.time_scope(start=scope.start, end=scope.end, timezone="UTC").by(grain)
        assert str(conflict.value) == (
            "Expected compatible scope and calendar timezones; construct the scope "
            "in the certified calendar boundary timezone."
        )
        logical = session.observe(
            {"value": session.catalog.metrics.get("sales.total")}, by=mv.product(grid, members)
        )
        fixed = logical.persist()
        assert len(fixed.to_pandas()) == 10
        source_trace.record(fixed)
        grouped = session.observe(
            {"value": session.catalog.metrics.get("sales.total")}, by=(grid,)
        ).persist()
        assert _value(grouped).tolist() == [7, 41]
        assert session._runtime.store.resources(session._runtime.session_ref) == ()
        source_trace.save(
            f"c06-source-{backend}-certified-calendar",
            {**case.environment, "profile": profile},
            {"columns": data.columns, "values": data.values},
            {
                "values": [7, 41],
                "period_days": [2, 3],
                "product_rows": 10,
                "calendar_timezone": "Asia/Shanghai",
                "report_timezone": "UTC",
                "snapshot_current": True,
                "deadline_controls": deadline_controls,
                "conflicting_zone_rejected": True,
                "resources": 0,
            },
            grouped,
            (case.session,),
        )


@pytest.mark.parametrize(
    "backend", source_params(["duckdb", "postgres", "mysql", "trino", "clickhouse"])
)
def test_civil_date_grid_keeps_date_keys_on_foreign_zone(
    backend: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    source_trace: SourceTrace,
) -> None:
    from datetime import date

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    facts = [
        (1, 2, date(2026, 8, 1)),
        (2, 5, date(2026, 8, 2)),
        (3, 99, date(2026, 8, 3)),
        (4, 77, date(2026, 7, 31)),
    ]
    data = SourceData(
        "id BIGINT, revision BIGINT, tenant VARCHAR(10), amount BIGINT, happened DATE",
        ",".join(
            (
                f"({identity},1,'a',{amount},{('DATE ' if backend == 'trino' else '')}'{day}')"
                for identity, amount, day in facts
            )
        ),
        "id Int64, revision Int64, tenant String, amount Int64, happened Date",
        [
            {"id": identity, "revision": 1, "tenant": "a", "amount": amount, "happened": day}
            for identity, amount, day in facts
        ],
    )
    profile = (
        "iceberg" if backend == "trino" else "mergetree" if backend == "clickhouse" else "table"
    )
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        author_source_project(
            backend, case, monkeypatch, semantic_project_factory, time_parse="date"
        )
        session = mv.session.create("r93-civil-date", timezone="UTC")
        zone = ZoneInfo("America/New_York")
        grid = session.time_scope(
            start=datetime(2026, 8, 1, tzinfo=zone),
            end=datetime(2026, 8, 3, tzinfo=zone),
            timezone=str(zone),
        ).by(mv.grain("day"))
        logical = session.observe(
            {"value": session.catalog.metrics.get("sales.total")},
            by=mv.product(grid, session.members(session.catalog.entities.get("sales.facts"))),
        )
        fixed = logical.persist()
        frame = fixed.to_pandas()
        assert len(frame) == 8 and (not frame["value", "value"].isna().any())
        source_trace.record(fixed)
        grouped = session.observe(
            {"value": session.catalog.metrics.get("sales.total")}, by=(grid,)
        ).persist()
        assert _value(grouped).tolist() == [2, 5]
        assert session._runtime.store.resources(session._runtime.session_ref) == ()
        source_trace.save(
            f"c06-source-{backend}-civil-date-grid",
            {**case.environment, "profile": profile},
            {"columns": data.columns, "values": data.values},
            {
                "values": [2, 5],
                "product_rows": 8,
                "source_role": "civil_date",
                "report_timezone": "UTC",
                "grid_timezone": str(zone),
                "end_exclusive": True,
                "before_start_excluded": True,
                "resources": 0,
            },
            grouped,
            (case.session,),
        )


@pytest.mark.parametrize(
    "backend", source_params(["duckdb", "postgres", "mysql", "trino", "clickhouse"])
)
def test_native_timestamp_retains_reader_and_grid_authority(
    backend: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    source_trace: SourceTrace,
) -> None:
    import pyarrow as pa

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("TZ", "UTC")
    instants = [
        "2026-08-01 03:59:59.999999",
        "2026-08-01 04:00:00.000000",
        "2026-08-02 03:59:59.999999",
        "2026-08-02 04:00:00.000000",
        "2026-08-03 04:00:00.000000",
    ]
    facts = list(
        zip(range(9007199254740992, 9007199254740997), [77, 2, 5, 11, 99], instants, strict=True)
    )
    physical = (
        "DATETIME(6)"
        if backend == "mysql"
        else "TIMESTAMP(6)"
        if backend == "trino"
        else "TIMESTAMP"
    )
    data = SourceData(
        f"id BIGINT, revision BIGINT, tenant VARCHAR(10), amount BIGINT, happened {physical}",
        ",".join(
            (
                f"({identity},1,'a',{amount},{('TIMESTAMP ' if backend == 'trino' else '')}'{instant}')"
                for identity, amount, instant in facts
            )
        ),
        "id Int64, revision Int64, tenant String, amount Int64, happened DateTime64(6, 'UTC')",
        [
            {
                "id": identity,
                "revision": 1,
                "tenant": "a",
                "amount": amount,
                "happened": datetime.fromisoformat(instant),
            }
            for identity, amount, instant in facts
        ],
    )
    profile = (
        "iceberg" if backend == "trino" else "mergetree" if backend == "clickhouse" else "table"
    )
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        native = case.session.bind(case.source, source_identity="c06.native-time")
        native_type = native.facts.schema.field("happened").type
        assert pa.types.is_timestamp(native_type)
        assert native_type.unit == "us"
        author_source_project(
            backend, case, monkeypatch, semantic_project_factory, time_parse="native_resolved"
        )
        session = mv.session.create("r93-native-time", timezone="Asia/Tokyo")
        zone = ZoneInfo("America/New_York")
        scope = session.time_scope(
            start=datetime(2026, 8, 1, tzinfo=zone),
            end=datetime(2026, 8, 3, tzinfo=zone),
            timezone=str(zone),
        )
        grid = scope.by(mv.grain("day"))
        members = session.members(session.catalog.entities.get("sales.facts"))
        from marivo.datasource.timezone import probe_engine_timezone

        reader = probe_engine_timezone(case.session._backend)
        reader_zone = reader.engine_timezone_name if reader is not None else None
        logical = session.observe(
            {"value": session.catalog.metrics.get("sales.total")}, by=mv.product(grid, members)
        )
        fixed = logical.persist()
        frame = fixed.to_pandas()
        assert len(frame) == 10 and (not frame["value", "value"].isna().any())
        source_trace.record(fixed)
        grouped = session.observe(
            {"value": session.catalog.metrics.get("sales.total")}, by=(grid,)
        ).persist()
        assert _value(grouped).tolist() == [7, 11]
        assert session._runtime.store.resources(session._runtime.session_ref) == ()
        source_trace.save(
            f"c06-source-{backend}-native-time",
            {**case.environment, "profile": profile},
            {"columns": data.columns, "values": data.values},
            {
                "values": [7, 11],
                "product_rows": 10,
                "native_time_type": str(native_type),
                "reader_timezone": reader_zone,
                "parser": None,
                "report_timezone": "Asia/Tokyo",
                "grid_timezone": str(zone),
                "last_microsecond_included": True,
                "exact_start_included": True,
                "exact_end_excluded": True,
                "resources": 0,
            },
            grouped,
            (case.session,),
        )


@pytest.mark.parametrize("backend", source_params(["duckdb", "postgres"]))
def test_aware_native_repeated_hour_keeps_distinct_instants(
    backend: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    source_trace: SourceTrace,
) -> None:
    import pyarrow as pa

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    facts = [
        (9007199254740992, 2, "2026-11-01 01:30:00-04:00"),
        (9007199254740993, 5, "2026-11-01 01:30:00-05:00"),
        (9007199254740994, 11, "2026-11-01 00:00:00-04:00"),
        (9007199254740995, 99, "2026-11-02 00:00:00-05:00"),
    ]
    data = SourceData(
        "id BIGINT, revision BIGINT, tenant VARCHAR(10), amount BIGINT, happened TIMESTAMPTZ",
        ",".join(
            (f"({identity},1,'a',{amount},'{instant}')" for identity, amount, instant in facts)
        ),
        "",
        [],
    )
    with source_case(backend, "table", tmp_path, monkeypatch, data) as case:
        native = case.session.bind(case.source, source_identity="c06.aware-time")
        native_type = native.facts.schema.field("happened").type
        assert pa.types.is_timestamp(native_type) and native_type.tz == "UTC"
        author_source_project(
            backend, case, monkeypatch, semantic_project_factory, time_parse="native_resolved"
        )
        session = mv.session.create("r93-aware-time", timezone="Asia/Tokyo")
        zone = ZoneInfo("America/New_York")
        scope = session.time_scope(
            start=datetime(2026, 11, 1, tzinfo=zone),
            end=datetime(2026, 11, 2, tzinfo=zone),
            timezone=str(zone),
        )
        grid = scope.by(mv.grain("day"))
        logical = session.observe(
            {"value": session.catalog.metrics.get("sales.total")},
            by=mv.product(grid, session.members(session.catalog.entities.get("sales.facts"))),
        )
        fixed = logical.persist()
        frame = fixed.to_pandas()
        assert len(frame) == 4 and (not frame["value", "value"].isna().any())
        assert sorted(frame["value", "value"].tolist()) == [0, 2, 5, 11]
        source_trace.record(fixed)
        grouped = session.observe(
            {"value": session.catalog.metrics.get("sales.total")}, by=(grid,)
        ).persist()
        assert _value(grouped).tolist() == [18]
        assert session._runtime.store.resources(session._runtime.session_ref) == ()
        source_trace.save(
            f"c06-source-{backend}-aware-repeated-hour",
            {**case.environment, "profile": "table"},
            {"columns": data.columns, "values": data.values},
            {
                "values": [18],
                "product_rows": 4,
                "native_time_type": str(native_type),
                "parser": None,
                "report_timezone": "Asia/Tokyo",
                "grid_timezone": str(zone),
                "distinct_repeated_hour_values": [2, 5],
                "exact_start_included": True,
                "exact_end_excluded": True,
                "resources": 0,
            },
            grouped,
            (case.session,),
        )


@pytest.mark.parametrize(
    ("backend", "wall", "start", "end"),
    source_params(
        [
            ("duckdb", "2026-03-08 02:30:00", "2026-03-09", "2026-03-10"),
            ("postgres", "2026-11-01 01:30:00", "2026-11-02", "2026-11-03"),
        ]
    ),
)
def test_native_observation_does_not_audit_wall_times_outside_its_scope(
    backend: str,
    wall: str,
    start: str,
    end: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    source_trace: SourceTrace,
) -> None:
    from tests.datasource.source_receipts import receipt

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    data = SourceData(
        "id BIGINT, revision BIGINT, tenant VARCHAR(10), amount BIGINT, happened TIMESTAMP",
        f"(1,1,'a',2,'{wall}'),(2,1,'a',2,'{start} 12:00:00')",
        "",
        [],
    )
    with source_case(backend, "table", tmp_path, monkeypatch, data) as case:
        author_source_project(
            backend, case, monkeypatch, semantic_project_factory, read_timezone="America/New_York"
        )
        session = mv.session.create("r93-native-wall-scope", timezone="Asia/Tokyo")
        zone = ZoneInfo("America/New_York")
        scope = session.time_scope(
            start=datetime.fromisoformat(start).replace(tzinfo=zone),
            end=datetime.fromisoformat(end).replace(tzinfo=zone),
            timezone=str(zone),
        )
        grid = scope.by(mv.grain("day"))
        logical = session.observe(
            {"value": session.catalog.metrics.get("sales.total")},
            by=mv.product(grid, session.members(session.catalog.entities.get("sales.facts"))),
        )
        result = logical.persist()
        assert sorted(_value(result).tolist()) == [0, 2]
        run = terminal_runs(session)[-1]
        assert run.lifecycle == "succeeded"
        with session._runtime.store._read() as connection:
            assert connection.execute("SELECT count(*) FROM dataset_artifacts").fetchone()[0] == 1
        assert session._runtime.store.resources(session._runtime.session_ref) == ()
        product_owners = [owner for owner in source_trace.owners if owner is not case.session]
        assert product_owners and all(owner._closed for owner in product_owners)
        assert any(owner.submissions for owner in product_owners)
        assert all(
            submission.connection_disconnected
            for owner in product_owners
            for submission in owner.submissions
        )
        receipt(
            f"c06-{backend}-native-wall-scope",
            {
                "backend": backend,
                "wall": wall,
                "read_timezone": str(zone),
                "report_timezone": "Asia/Tokyo",
                "grid_timezone": str(zone),
                "outside_scope_wall": wall,
                "run_lifecycle": run.lifecycle,
                "published_artifacts": 1,
                "resources": 0,
            },
        )
