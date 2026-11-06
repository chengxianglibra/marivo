"""Public time predicates freeze report boundaries before native source lowering."""

import operator
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
import sqlglot
import sqlglot.expressions as sge

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.reader import SemanticProject
from tests.analysis.numeric.test_remote_numeric_query import _datasource_model
from tests.datasource.source_cases import SourceData, source_case, source_params
from tests.shared_fixtures import catalog_value
from tests.support.source_trace import SourceTrace


def _entity(catalog: ms.SemanticCatalog, path: str) -> ms.Entity:
    result = catalog.get(path)
    assert result.kind == "entity"
    return result


def _metric(catalog: ms.SemanticCatalog, path: str) -> ms.Metric:
    result = catalog.get(path)
    assert result.kind == "metric"
    return result


@contextmanager
def _session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    factory: Callable[[dict[str, str]], SemanticProject],
    *,
    backend: str = "duckdb",
    profile: str = "table",
    representation: str = "date",
    source_zone: str = "Asia/Shanghai",
    report_zone: str = "Asia/Shanghai",
) -> Iterator[tuple[mv.Session, ms.Entity, ms.TimeDimension, ms.Measure, ms.Dimension]]:
    physical = {"date": "DATE", "naive": "TIMESTAMP", "aware": "TIMESTAMPTZ", "text": "VARCHAR"}[
        representation
    ]
    parse = {
        "date": f"ms.date(timezone={source_zone!r})",
        "naive": f"ms.timestamp(timezone={source_zone!r})",
        "aware": "ms.timestamp()",
        "text": f"ms.strptime('%Y-%m-%d %H:%M:%S', timezone={source_zone!r})",
    }[representation]
    rows: list[dict[str, object]] = []
    values = []
    for day in (27, 28, 29, 30, 31):
        text = f"2026-10-{day}"
        point_literal_value = text if representation == "date" else f"{text} 00:00:00"
        if representation == "aware":
            point_literal_value += "+08:00"
        rows.append({"id": day, "point": date(2026, 10, day), "label": text, "amount": day})
        point_literal = (
            f"DATE '{point_literal_value}'"
            if representation == "date"
            else f"'{point_literal_value}'"
        )
        values.append(f"({day},{point_literal},'{text}',{day})")
    rows.append({"id": 99, "point": None, "label": "not-a-date", "amount": 99})
    values.append("(99,NULL,'not-a-date',99)")
    data = SourceData(
        f"id BIGINT, point {physical}, label VARCHAR(40), amount BIGINT",
        ",".join(values),
        "id Int64, point Nullable(Date), label String, amount Int64",
        rows,
    )
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        assert isinstance(case.source, TableSourceIR)
        factory(
            {
                "datasources/warehouse.py": _datasource_model(backend, case, monkeypatch),
                "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='Tests', default=True)\n",
                "sales/facts.py": "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                + f"facts=ms.entity(name='facts', datasource=ms.ref.datasource('warehouse'), source=md.table({case.source.table!r}, database={case.source.database!r}), primary_key=['id'])\n"
                + f"point=ms.time_dimension_column(name='point', entity=facts, column='point', parse={parse})\n"
                + "label=ms.dimension_column(name='label', entity=facts, column='label')\n"
                + "amount=ms.measure_column(name='amount', entity=facts, column='amount', additivity=ms.additive_all())\n"
                + "@ms.metric()\ndef filtered():\n    return amount.sum().where((point >= '2026-10-28') & (point < '2026-10-30'))\n",
            }
        )
        session = mv.session.get_or_create("boundaries", timezone=report_zone)
        entity = _entity(session.catalog, "entity:sales.facts")
        point = catalog_value(session.catalog, "sales.facts.point", kind="time_dimension")
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="measure")
        label = catalog_value(session.catalog, "sales.facts.label", kind="dimension")
        assert isinstance(entity, ms.Entity)
        assert isinstance(point, ms.TimeDimension)
        assert isinstance(amount, ms.Measure)
        assert isinstance(label, ms.Dimension)
        yield session, entity, point, amount, label


@pytest.mark.parametrize("representation", ["date", "naive", "aware", "text"])
def test_all_source_predicate_entries_use_report_midnight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    representation: str,
) -> None:
    with _session(
        tmp_path, monkeypatch, semantic_project_factory, representation=representation
    ) as (session, entity, point, amount, label):
        condition = (point >= "2026-10-28") & (point < "2026-10-30")
        metrics = {
            "metric": amount.sum().where(condition),
            "reduction": amount.sum(where=condition),
            "declared": _metric(session.catalog, "metric:sales.filtered"),
        }
        assert session.observe(metrics).compute().to_pandas().iloc[0].tolist() == [57, 57, 57]
        grouped = session.observe({"value": amount.sum()}, by=(label,), where=condition).compute()
        data = grouped.to_pandas(axis_names={grouped.axes[0].token: "label"})
        assert data.label.tolist() == ["2026-10-28", "2026-10-29"]
        assert data.value.tolist() == [28, 29]
        members = session.frame(session.members(entity, where=condition)).compute()
        assert members.to_pandas(axis_names={members.axes[0].token: "id"}).id.tolist() == [28, 29]


@pytest.mark.parametrize("form", ["date", "civil", "aware", "offset", "anchor"])
def test_literal_forms_and_comparisons_have_exact_instant_semantics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    form: str,
) -> None:
    with _session(tmp_path, monkeypatch, semantic_project_factory) as (
        session,
        entity,
        point,
        amount,
        label,
    ):
        boundaries: dict[str, str | date | datetime] = {
            "date": date(2026, 10, 28),
            "civil": "2026-10-28T00:00:00",
            "aware": datetime(2026, 10, 28, tzinfo=timezone(timedelta(hours=8))),
            "offset": "2026-10-27T16:00:00Z",
            "anchor": session.instant("2026-10-27T16:00:00Z"),
        }
        boundary = boundaries[form]
        for compare, expected in (
            (operator.eq, 28),
            (operator.ne, 117),
            (operator.lt, 27),
            (operator.le, 55),
            (operator.gt, 90),
            (operator.ge, 118),
        ):
            condition = compare(point, boundary)
            assert session.observe(
                {"value": amount.sum()}, where=condition
            ).compute().to_pandas().value.tolist() == [expected]
        reverse = operator.le("2026-10-28", point) & ~((point >= "2026-10-30") | point.isnull())
        assert session.observe(
            {"value": amount.sum()}, where=reverse
        ).compute().to_pandas().value.tolist() == [57]
        fallback = point.coalesce(date(2026, 10, 28))
        assert session.observe(
            {"value": amount.sum()}, where=(fallback >= boundary) & (fallback < "2026-10-30")
        ).compute().to_pandas().value.tolist() == [156]
        assert session.observe(
            {"value": amount.sum()}, where=label == "not-a-date"
        ).compute().to_pandas().value.tolist() == [99]


def test_temporal_branches_preserve_conditions_and_normalize_only_time_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    with _session(tmp_path, monkeypatch, semantic_project_factory) as (
        session,
        entity,
        point,
        amount,
        label,
    ):
        fallback = date(2026, 10, 28)
        for expression, expected in (
            (point.nullif(fallback), 29),
            ((label == "not-a-date").ifelse(fallback, point), 156),
            (label.cases(("not-a-date", fallback), else_=point), 156),
        ):
            condition = (expression >= "2026-10-28") & (expression < "2026-10-30")
            output = session.observe({"value": amount.sum()}, where=condition).compute()
            assert output.to_pandas().value.tolist() == [expected]
        raw = session.observe(
            {"value": amount.sum()},
            by=(point,),
            where=(point >= "2026-10-28") & (point < "2026-10-30"),
        ).compute()
        assert raw.to_pandas(axis_names={raw.axes[0].token: "point"}).point.tolist() == [
            date(2026, 10, 28),
            date(2026, 10, 29),
        ]
        with pytest.raises(AnalysisError, match="matching value kinds"):
            _ = raw.coord(raw.axes[0]) >= "2026-10-28"


def test_session_timezone_is_frozen_independently_of_output_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    with _session(tmp_path, monkeypatch, semantic_project_factory) as (
        session,
        entity,
        point,
        amount,
        label,
    ):
        condition = (point >= "2026-10-28") & (point < "2026-10-30")
        before = repr(condition)
        scope = session.time_scope(start="2026-10-27", end="2026-10-31", timezone="UTC")
        result = session.observe(
            {"value": amount.sum(time=point)}, by=(scope,), where=condition
        ).compute()
        assert result.to_pandas().value.tolist() == [57]
        explicit = session.time_scope(start="2026-10-28", end="2026-10-30", timezone="UTC")
        assert session.observe(
            {"value": amount.sum()}, where=point.within(explicit)
        ).compute().to_pandas().value.tolist() == [59]
        utc = mv.session.get_or_create("utc-boundaries", timezone="UTC")
        assert utc.observe(
            {"value": amount.sum()}, where=condition
        ).compute().to_pandas().value.tolist() == [59]
        assert repr(condition) == before
        saved = result.persist()
        monkeypatch.setenv("TZ", "Pacific/Honolulu")
        assert session.persisted_frame(saved.artifact_id).to_pandas().value.tolist() == [57]


@pytest.mark.parametrize(
    "source_zone,report_zone,expected",
    [
        ("UTC", "Asia/Shanghai", 57),
        ("Asia/Tokyo", "UTC", 59),
        ("Asia/Shanghai", "America/Los_Angeles", 59),
    ],
)
def test_date_comparisons_assign_declared_day_start_across_report_zones(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    source_zone: str,
    report_zone: str,
    expected: int,
) -> None:
    with _session(
        tmp_path,
        monkeypatch,
        semantic_project_factory,
        source_zone=source_zone,
        report_zone=report_zone,
    ) as (session, entity, point, amount, label):
        condition = (point >= date(2026, 10, 28)) & (point < date(2026, 10, 30))
        assert session.observe(
            {"value": amount.sum()}, where=condition
        ).compute().to_pandas().value.tolist() == [expected]


@pytest.mark.parametrize(
    "boundary", [datetime(2026, 10, 28), "not-a-date", "2026-03-08T02:30:00", "2026-11-01T01:30:00"]
)
def test_invalid_boundaries_fail_at_capture_without_source_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    source_trace: SourceTrace,
    boundary: str | datetime,
) -> None:
    with _session(
        tmp_path, monkeypatch, semantic_project_factory, report_zone="America/New_York"
    ) as (session, entity, point, amount, label):
        first = len(source_trace.native_sql)
        for entry in (
            lambda: session.observe({"value": amount.sum()}, where=point >= boundary),
            lambda: session.members(entity, where=point >= boundary),
        ):
            with pytest.raises(AnalysisError) as caught:
                entry()
            assert caught.value.location is not None and "where" in caught.value.location
            assert repr(boundary) in (caught.value.received or "")
        assert len(source_trace.native_sql) == first


@pytest.mark.parametrize(
    "backend,profile",
    source_params(
        [
            ("postgres", "table"),
            ("mysql", "innodb-table"),
            ("trino", "iceberg"),
            ("clickhouse", "mergetree"),
        ]
    ),
)
def test_native_date_predicates_use_typed_source_cutoffs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    source_trace: SourceTrace,
    backend: str,
    profile: str,
) -> None:
    with _session(
        tmp_path, monkeypatch, semantic_project_factory, backend=backend, profile=profile
    ) as (session, entity, point, amount, label):
        first = len(source_trace.native_sql)
        condition = (point >= "2026-10-28") & (point < "2026-10-30")
        output = session.observe({"value": amount.sum()}, by=(label,), where=condition).compute()
        data = output.to_pandas(axis_names={output.axes[0].token: "label"})
        assert data.label.tolist() == ["2026-10-28", "2026-10-29"]
        assert data.value.tolist() == [28, 29]
        dialect = "postgres" if backend == "postgres" else backend
        comparisons = [
            node
            for sql in source_trace.native_sql[first:]
            for node in sqlglot.parse_one(sql, read=dialect).find_all(sge.GTE)
        ]
        assert any(
            isinstance(node.this, sge.Column)
            and node.this.name == "point"
            and [literal.this for literal in node.expression.find_all(sge.Literal)]
            in (["2026-10-28"], ["2026", "10", "28"])
            for node in comparisons
        ), source_trace.native_sql[first:]
