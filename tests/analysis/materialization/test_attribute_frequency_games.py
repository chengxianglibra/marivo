"""Public frequency games freeze source transport before local permutations."""

from collections.abc import Callable
from pathlib import Path

import duckdb
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.session.core import Session
from marivo.semantic.reader import SemanticProject
from tests.shared_fixtures import catalog_value
from tests.support.source_trace import capture_source


def _field(session: Session, path: str) -> ms.Dimension | ms.TimeDimension | ms.Measure:
    value = catalog_value(session.catalog, path, kind="field")
    assert isinstance(value, (ms.Dimension, ms.TimeDimension, ms.Measure))
    return value


@pytest.fixture
def frequency_session(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> Session:
    database = tmp_path / "frequency.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute(
            "CREATE TABLE facts(id BIGINT, batch BIGINT, tenant VARCHAR, player VARCHAR, amount DECIMAL(22,2), member BIGINT)"
        )
        connection.execute(
            "INSERT INTO facts VALUES (1,0,'t','A',5,1),(2,0,'t','B',15,1),(3,0,'t','B',25,2),(4,1,'t','A',10,2),(5,1,'t','A',20,3),(6,1,'t','B',30,2)"
        )
        csv = tmp_path / "facts.csv"
        if getattr(request, "param", None) == "csv":
            connection.execute(f"COPY facts TO '{csv}' (HEADER, DELIMITER ',')")
    source = (
        f"md.csv({str(csv)!r})" if getattr(request, "param", None) == "csv" else "md.table('facts')"
    )
    semantic_project_factory(
        {
            "datasources/warehouse.py": f"import marivo.datasource as md\nmd.duckdb(name='warehouse',path={str(database)!r})\n",
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales',owner='Analytics',default=True)\n",
            "sales/model.py": f"""import marivo.datasource as md
import marivo.semantic as ms
facts=ms.entity(name='facts',datasource=ms.ref.datasource('warehouse'),source={source},primary_key=['id'])
batch=ms.dimension_column(name='batch',entity=facts,column='batch')
tenant=ms.dimension_column(name='tenant',entity=facts,column='tenant')
player=ms.dimension_column(name='player',entity=facts,column='player')
amount=ms.measure_column(name='amount',entity=facts,column='amount',unit='USD',additivity=ms.additive_all())
member=ms.measure_column(name='member',entity=facts,column='member',unit='1',additivity=ms.additive_all())
""",
        },
        load=False,
    )
    monkeypatch.chdir(tmp_path)
    return mv.session.get_or_create("frequency", timezone="UTC")


@pytest.mark.parametrize("distinct", [False, True], ids=["minimum", "distinct"])
def test_public_frequency_game_keeps_source_capture_fixed_as_permutations_grow(
    frequency_session: Session,
    distinct: bool,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session = frequency_session
    if not distinct:
        database = tmp_path / "frequency.duckdb"
        with duckdb.connect(str(database)) as connection:
            connection.execute("ALTER TABLE facts ALTER COLUMN amount SET DATA TYPE DOUBLE")
    amount = _field(session, "sales.facts.amount")
    member = _field(session, "sales.facts.member")
    batch = _field(session, "sales.facts.batch")
    tenant = _field(session, "sales.facts.tenant")
    player = _field(session, "sales.facts.player")
    metric = member.count_distinct() if distinct else amount.quantile(0.0)
    current = session.observe({"q": metric}, by=(tenant,), where=batch == 1)
    baseline = session.observe({"q": metric}, by=(tenant,), where=batch == 0)
    compared = current.compare(
        {"b": mv.baseline(baseline, on=tuple(zip(current.axes, baseline.axes, strict=True)))}
    )
    target = compared.select({"change": compared["b.absolute.q"]})
    trace = capture_source(monkeypatch, measure_cost=True)
    costs = []
    for permutations in (2, 64):
        start = len(trace.native_sql)
        report = target.attribute(by=(player,), permutations=permutations, seed=42).compute()
        rows = report.to_pandas()
        assert rows["total.change"].tolist() == [0 if distinct else 5] * 2
        assert sum(rows["contribution.change"]) == pytest.approx(0 if distinct else 5)
        if not distinct:
            assert rows["contribution.change"].tolist() == [5.0, 0.0]
        elif permutations == 64:
            by_player = dict(zip(rows.iloc[:, 1], rows["contribution.change"], strict=True))
            assert 0 < by_player["A"] < 1 and by_player["B"] == -by_player["A"]
        method = report.method_facts[0].parents[0].metrics[0].method
        assert method.kind == "shapley"
        assert method.unique_coalitions <= 4
        assert method.permutations == permutations
        assert method.captures
        assert sum(capture.source_reads for capture in method.captures) == 2
        costs.append(
            (
                len(trace.native_sql) - start,
                tuple(trace.returned_rows[start:]),
                tuple(trace.returned_arrow_bytes[start:]),
                tuple(
                    (
                        capture.state_entries,
                        capture.state_bytes,
                        capture.returned_rows,
                        capture.returned_bytes,
                        capture.source_reads,
                    )
                    for capture in method.captures
                ),
            )
        )
        sql = "\n".join(trace.native_sql[start:]).lower()
        assert "__attribute_frequency_value" in sql
        assert "coalition" not in sql
    assert costs[0] == costs[1]
    import json
    import os

    destination = os.environ.get("MARIVO_SHAPLEY_STATE_EVIDENCE_DIR")
    if destination:
        path = Path(destination) / (
            "distinct-fixed-capture.json" if distinct else "minimum-fixed-capture.json"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "backend": "duckdb",
                    "version": duckdb.__version__,
                    "permutations": [2, 64],
                    "costs": costs,
                    "cost_fields": [
                        "actual_source_submissions",
                        "returned_rows",
                        "decoded_arrow_bytes",
                        "capture_facts",
                    ],
                    "capture_fields": [
                        "state_entries",
                        "state_bytes",
                        "returned_rows",
                        "decoded_arrow_bytes",
                        "source_reads",
                    ],
                    "backend_scan_rows": None,
                    "backend_scan_bytes": None,
                    "wire_bytes": None,
                },
                indent=2,
            )
        )


@pytest.mark.parametrize("frequency_session", ["csv"], indirect=True)
def test_frequency_game_rejects_non_table_sources_before_business_reads(
    frequency_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.analysis.errors import AnalysisStageUnavailableError

    session = frequency_session
    amount = _field(session, "sales.facts.amount")
    batch = _field(session, "sales.facts.batch")
    player = _field(session, "sales.facts.player")
    current = session.observe({"q": amount.quantile(0.5)}, where=batch == 1)
    baseline = session.observe({"q": amount.quantile(0.5)}, where=batch == 0)
    compared = current.compare({"b": mv.baseline(baseline, on=())})
    target = compared.select({"change": compared["b.absolute.q"]})
    trace = capture_source(monkeypatch)
    with pytest.raises(AnalysisStageUnavailableError, match="native physical table source"):
        target.attribute(by=(player,), permutations=2).compute()
    assert not trace.native_sql


def test_native_duckdb_interpolation_uses_original_queries_and_observe_still_works(
    frequency_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = frequency_session
    member = _field(session, "sales.facts.member")
    batch = _field(session, "sales.facts.batch")
    player = _field(session, "sales.facts.player")
    current = session.observe({"q": member.quantile(0.5)}, where=batch == 1)
    baseline = session.observe({"q": member.quantile(0.5)}, where=batch == 0)
    compared = current.compare({"b": mv.baseline(baseline, on=())})
    target = compared.select({"change": compared["b.absolute.q"]})
    trace = capture_source(monkeypatch)
    report = target.attribute(by=(player,), permutations=2).compute()
    rows = report.to_pandas()
    assert rows["total.change"].tolist() == [1, 1]
    assert sum(rows["contribution.change"]) == pytest.approx(1)
    method = report.method_facts[0].parents[0].metrics[0].method
    assert method.state_route == "native_queries/v1"
    assert (
        len([sql for sql in trace.native_sql if "UNION ALL" in sql.upper()])
        == method.unique_coalitions
    )
    assert all(
        not any(word in sql.upper() for word in ("CREATE ", "INSERT ", "DROP "))
        for sql in trace.native_sql
    )
    ordinary = current.compute().to_pandas()
    assert ordinary["q"].tolist() == [2.0]


def test_native_decimal_quantiles_preserve_each_finisher_and_query_receipts(
    frequency_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from decimal import Decimal
    from fractions import Fraction

    session = frequency_session
    amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
    batch = catalog_value(session.catalog, "sales.facts.batch", kind="field")
    player = catalog_value(session.catalog, "sales.facts.player", kind="field")
    quantiles = {
        "low": 0.0,
        "quarter": 0.25,
        "mid": 0.5,
        "upper": 0.75,
        "high": 1.0,
        "mid_again": 0.5,
    }
    metrics = {name: amount.quantile(q) for name, q in quantiles.items()}
    current = session.observe(metrics, where=batch == 1)
    baseline = session.observe(metrics, where=batch == 0)
    compared = current.compare({"b": mv.baseline(baseline, on=())}).compute()
    target = compared.select({name: compared[f"b.absolute.{name}"] for name in metrics})
    raw_baseline = {"A": [Decimal(5)], "B": [Decimal(15), Decimal(25)]}
    raw_current = {"A": [Decimal(10), Decimal(20)], "B": [Decimal(30)]}

    def raw_quantile(groups: dict[str, list[Decimal]], q: float) -> Fraction:
        values = [value for group in groups.values() for value in group]
        with duckdb.connect(":memory:") as connection:
            rows = ",".join(f"({value})" for value in values)
            value = connection.execute(
                f"SELECT quantile_cont(CAST(x AS DECIMAL(22,2)), {q!r}) FROM (VALUES {rows}) t(x)"
            ).fetchone()
        assert value is not None
        return Fraction(value[0])

    trace = capture_source(monkeypatch, measure_cost=True)
    costs = []
    for count in (2, 32):
        start = len(trace.native_sql)
        result = target.attribute(by=(player,), permutations=count, seed=7).compute()
        rows = result.to_pandas()
        for name, q in quantiles.items():
            expected = {"A": Fraction(0), "B": Fraction(0)}
            for order in (("A", "B"), ("B", "A")):
                groups = dict(raw_baseline)
                previous = raw_quantile(groups, q)
                for member in order:
                    groups[member] = raw_current[member]
                    following = raw_quantile(groups, q)
                    expected[member] += (following - previous) / 2
                    previous = following
            assert rows[f"contribution.{name}"].tolist() == [
                float(expected["A"]),
                float(expected["B"]),
            ]
            assert rows[f"total.{name}"].tolist() == [Decimal(5), Decimal(5)]
        methods = tuple(
            metric
            for facts in result.method_facts
            for parent in facts.parents
            for metric in parent.metrics
        )
        assert len(methods) == len(quantiles)
        for metric in methods:
            assert metric.method.kind == "shapley"
            assert metric.method.state_route == "native_queries/v1"
            assert metric.method.captured_source_reads == sum(
                c.source_reads for c in metric.method.captures
            )
        costs.append(
            (
                len(trace.native_sql) - start,
                tuple(trace.returned_rows[start:]),
                tuple(trace.returned_arrow_bytes[start:]),
            )
        )
    assert all(cost[0] > 2 for cost in costs)
