"""Native aggregate transport and independent retained numeric components."""

import json
import os
from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError
from marivo.datasource.adapters import SourceSession
from marivo.datasource.ir import TableSourceIR
from tests.analysis.materialization.domain_recovery_worker import forbidden
from tests.analysis.materialization.publication_fixtures import publication_counts
from tests.datasource.source_cases import SourceData, source_case


@pytest.mark.runtime
@pytest.mark.parametrize("parquet", [False, True])
@pytest.mark.parametrize(
    "value_type,weight_type",
    [
        ("DECIMAL(12,2)", "BIGINT"),
        ("DOUBLE", "BIGINT"),
        ("INTEGER", "SMALLINT"),
        ("REAL", "INTEGER"),
        ("DECIMAL(12,2)", "DOUBLE"),
        ("DECIMAL(12,2)", "DECIMAL(8,3)"),
    ],
)
def test_native_weighted_and_mean(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    value_type: str,
    weight_type: str,
    parquet: bool,
) -> None:
    monkeypatch.chdir(tmp_path)
    _model(tmp_path)
    with duckdb.connect(str(tmp_path / "source.duckdb")) as con:
        con.execute(
            f"CREATE TABLE facts(id BIGINT, day DATE, value {value_type}, weight {weight_type}, category VARCHAR)"
        )
        con.execute(
            "INSERT INTO facts VALUES (1,'2026-07-01',1.01,1,'a'),(2,'2026-07-01',1.02,2,'a'),(3,'2026-07-01',NULL,100,'b'),(4,'2026-07-01',5,NULL,'b')"
        )
        expected_row = con.execute(
            "SELECT SUM(value*weight)/SUM(CASE WHEN value IS NOT NULL AND weight IS NOT NULL THEN weight END) FROM facts"
        ).fetchone()
        assert expected_row is not None
        expected = expected_row[0]
        expected_mean = con.execute(
            f"SELECT CAST(AVG(value) AS {value_type if value_type.startswith('DECIMAL') else 'DOUBLE'}) FROM facts GROUP BY category ORDER BY category"
        ).fetchall()
        if parquet:
            pq.write_table(
                con.execute("SELECT * FROM facts").to_arrow_table(), tmp_path / "facts.parquet"
            )
    if parquet:
        model = tmp_path / "models/semantic/sales/facts.py"
        model.write_text(
            model.read_text().replace("md.table('facts',", "md.parquet('facts.parquet',")
        )
    session = mv.session.get_or_create("native", report_timezone="UTC")
    members = session.members(ms.ref.entity("sales.facts"))
    scope = mv.time_scope(start="2026-07-01", end="2026-07-02")
    observed = members.observe(ms.ref.metric("sales.weighted"), during=scope, by=(mv.member(),))
    assert observed.rollup().execute().to_pandas().value.tolist() == pytest.approx([expected])
    saved = observed.execute()
    assert saved.rollup().execute().to_pandas().value.tolist() == pytest.approx([expected])
    coordinate = ms.ref.dimension("sales.facts.category")
    with_coordinates = members.observe(
        ms.ref.metric("sales.weighted"),
        during=scope,
        by=(
            mv.member(),
            coordinate,
        ),
    )
    for relation in (with_coordinates, with_coordinates.execute()):
        rows = relation.group_by(coordinate).rollup().execute().to_pandas()
        assert rows.value.dropna().tolist() == pytest.approx([expected])
    grouped = (
        members.observe(
            ms.ref.metric("sales.mean"),
            during=scope,
            by=(
                mv.member(),
                coordinate,
            ),
        )
        .group_by(coordinate)
        .rollup()
    )
    grouped_mean = grouped.execute()
    assert grouped_mean.to_pandas().value.tolist() == [row[0] for row in expected_mean]
    mean = members.observe(ms.ref.metric("sales.mean"), during=scope, by=(mv.member(),))
    assert float(mean.rollup().execute().to_pandas().value.iloc[0]) == pytest.approx(2.34, abs=0.01)
    assert float(mean.execute().rollup().execute().to_pandas().value.iloc[0]) == pytest.approx(
        2.34, abs=0.01
    )


def _model(tmp_path: Path) -> None:
    (tmp_path / "marivo.toml").write_text('[project]\nname="native"\n')
    models = tmp_path / "models/semantic/sales"
    models.mkdir(parents=True)
    datasource = tmp_path / "models/datasources"
    datasource.mkdir()
    (datasource / "warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path='source.duckdb')\n"
    )
    (models / "_domain.py").write_text(
        "import marivo.semantic as ms\nms.domain(name='sales', owner='Data', default=True)\n"
    )
    (models / "facts.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        "facts=ms.entity(name='facts', datasource=ms.ref.datasource('warehouse'), source=md.table('facts', columns={k:k for k in ['id','day','value','weight','category']}), primary_key=['id'])\n"
        "day=ms.time_dimension_column(name='day', entity=facts, column='day', granularity='day', is_default=True)\n"
        "category=ms.dimension_column(name='category', entity=facts, column='category')\n"
        "value=ms.measure_column(name='value', entity=facts, column='value', additivity=ms.additive_all())\n"
        "weight=ms.measure_column(name='weight', entity=facts, column='weight', additivity=ms.additive_all())\n"
        "weighted=ms.weighted_mean(name='weighted', value=value, weight=weight)\n"
        "total=ms.aggregate(name='total', measure=value, agg='sum')\n"
        "weights=ms.aggregate(name='weights', measure=weight, agg='sum')\n"
        "mean=ms.aggregate(name='mean', measure=value, agg='mean')\n"
    )


@pytest.mark.runtime
@pytest.mark.parametrize("backend", ["postgres", "mysql", "trino", "clickhouse", "sqlite"])
def test_native_remote_weighted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: str
) -> None:
    _remote_weighted(tmp_path, monkeypatch, backend, overflow=False)


@pytest.mark.runtime
@pytest.mark.parametrize("fixed", [False, True], ids=["source", "fixed"])
def test_clickhouse_weight_state_overflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fixed: bool
) -> None:
    _remote_weighted(tmp_path, monkeypatch, "clickhouse", overflow=True, fixed=fixed)


def _remote_weighted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
    *,
    overflow: bool,
    fixed: bool = False,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    _model(tmp_path)
    profile = {
        "trino": "iceberg",
        "clickhouse": "mergetree",
        "mysql": "innodb-table",
        "sqlite": "main-table",
    }.get(backend, "table")
    typ = "DOUBLE" if backend == "sqlite" else "DECIMAL(12,2)"
    rows = (
        "(1,'2026-07-01',1.01,9223372036854775807,'a'),(2,'2026-07-01',1.02,9223372036854775807,'a')"
        if overflow
        else "(1,'2026-07-01',1.01,1,'a'),(2,'2026-07-01',1.02,2,'a'),(3,'2026-07-01',NULL,100,'b'),(4,'2026-07-01',5,NULL,'b')"
    )
    data = SourceData(
        f"id BIGINT, day DATE, value {typ}, weight BIGINT, category VARCHAR(10)",
        rows.replace("'2026-07-01'", "DATE '2026-07-01'") if backend == "trino" else rows,
        "id Int64, day Date, value Nullable(Decimal(12,2)), weight Nullable(Int64), category String",
        [
            {
                "id": i,
                "day": date(2026, 7, 1),
                "value": value,
                "weight": 9223372036854775807 if overflow else weight,
                "category": category,
            }
            for i, value, weight, category in [
                (1, Decimal("1.01"), 1, "a"),
                (2, Decimal("1.02"), 2, "a"),
                (3, None, 100, "b"),
                (4, Decimal("5"), None, "b"),
            ]
            if not overflow or i < 3
        ],
        {"BIGINT": "int64", "DOUBLE": "float64", "DATE": "date"} if backend == "sqlite" else None,
    )
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        assert isinstance(case.source, TableSourceIR)
        arguments = {
            **case.session.datasource.fields,
            **{key + "_env": value for key, value in case.session.datasource.env_refs.items()},
        }
        user = arguments.pop("user", None)
        if user is not None:
            monkeypatch.setenv("MARIVO_NATIVE_READER", str(user))
            arguments["user_env"] = "MARIVO_NATIVE_READER"
        (tmp_path / "models/datasources/warehouse.py").write_text(
            "import marivo.datasource as md\n"
            + f"md.{backend}(name='warehouse',"
            + ",".join(f"{key}={value!r}" for key, value in arguments.items())
            + ")\n"
        )
        model = tmp_path / "models/semantic/sales/facts.py"
        model.write_text(
            model.read_text().replace("md.table('facts',", f"md.table({case.source.table!r},")
        )
        session = mv.session.get_or_create("native_remote", report_timezone="UTC")
        members = session.members(ms.ref.entity("sales.facts"))
        scope = mv.time_scope(start="2026-07-01", end="2026-07-02")
        observed = members.observe(ms.ref.metric("sales.weighted"), during=scope, by=(mv.member(),))
        if overflow:
            from marivo.analysis.materialization.errors import MaterializationError

            saved = observed.execute() if fixed else None
            previous = saved.to_pandas() if saved is not None else None
            if saved is not None:
                assert saved._dataset is not None
                state = next(
                    part.table
                    for part in saved._dataset.verified().parts
                    if part.role == "original_state"
                )
                assert state.column("original_state__weight_sum").to_pylist() == [2**63 - 1] * 2
            before = publication_counts(session)
            with monkeypatch.context() as offline:
                if saved is not None:
                    offline.setattr(SourceSession, "batches", forbidden)
                with pytest.raises(MaterializationError) as raised:
                    (saved if saved is not None else observed).rollup().execute()
            cause = str(raised.value.__cause__)
            assert (
                "retained numeric sum within declared carrier" in str(raised.value)
                or "int64 state overflow" in str(raised.value)
                or "DECIMAL_OVERFLOW" in cause
                or ("CANNOT_PARSE_TEXT" in cause and "numeric carrier overflow" in cause)
            )
            assert raised.value.expected and raised.value.received and raised.value.repair
            assert publication_counts(session) == before
            assert session._runtime.store.resources(session.id) == ()
            if saved is not None:
                assert previous is not None
                assert saved.to_pandas().equals(previous)
            if directory := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
                Path(
                    directory,
                    "native-clickhouse-weight-overflow-"
                    + ("fixed" if fixed else "source")
                    + ".json",
                ).write_text(
                    json.dumps(
                        {
                            "columns": data.columns,
                            "values": data.values,
                            "raw_rows": data.rows,
                            "mathematical_weight_sum": 2 * (2**63 - 1),
                            "weight_carrier": "int64",
                            "route": "fixed_python" if fixed else "source_ibis",
                            "expected": "overflow_refusal",
                            "error": str(raised.value),
                            "publication_counts_before": before,
                            "publication_counts_after": publication_counts(session),
                            "resources": 0,
                        },
                        default=str,
                        sort_keys=True,
                    )
                )
            return
        assert observed.rollup().execute().to_pandas().value.tolist() == pytest.approx([3.05 / 3])
        assert observed.execute().rollup().execute().to_pandas().value.tolist() == pytest.approx(
            [3.05 / 3]
        )
        total = ms.ref.metric("sales.total")
        weights = ms.ref.metric("sales.weights")
        for metric, expected, tolerance in (
            (ms.ref.metric("sales.mean"), 7.03 / 3, 0.005),
            (mv.runtime_metric.ratio(total, weights, label="rate"), 7.03 / 103, 1e-12),
            (mv.runtime_metric.linear(add=[total], subtract=[weights], label="net"), -95.97, 1e-9),
        ):
            logical = members.observe(metric, during=scope, by=(mv.member(),))
            for relation in (logical, logical.execute()):
                value = relation.rollup().execute().to_pandas().value.iloc[0]
                assert float(value) == pytest.approx(expected, rel=0, abs=tolerance)


@pytest.mark.runtime
def test_native_numeric_recovers_without_sources(tmp_path: Path) -> None:
    import os
    import subprocess
    import sys

    from tests.support.paths import PROJECT_ROOT

    for phase in ("produce", "continue", "recover"):
        if phase == "continue":
            (tmp_path / "models").rename(tmp_path / "models.offline")
            (tmp_path / "facts.parquet").rename(tmp_path / "facts.offline")
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.analysis.numeric.native_numeric_worker",
                str(tmp_path),
                phase,
            ],
            env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
            capture_output=True,
            text=True,
            timeout=90,
        )
        assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.runtime
def test_mixed_linear_coordinates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _model(tmp_path)
    with duckdb.connect(str(tmp_path / "source.duckdb")) as con:
        con.execute(
            "CREATE TABLE facts(id BIGINT, day DATE, value DECIMAL(12,2), weight DOUBLE, category VARCHAR)"
        )
        con.execute(
            "INSERT INTO facts VALUES (1,'2026-07-01',1.25,0.5,'a'),(2,'2026-07-01',2.75,1.5,'a')"
        )
    session = mv.session.get_or_create("native", report_timezone="UTC")
    coordinate = ms.ref.dimension("sales.facts.category")
    metric = mv.runtime_metric.linear(
        add=[ms.ref.metric("sales.total")], subtract=[ms.ref.metric("sales.weights")], label="net"
    )
    observed = session.members(ms.ref.entity("sales.facts")).observe(
        metric,
        during=mv.time_scope(start="2026-07-01", end="2026-07-02"),
        by=(
            mv.member(),
            coordinate,
        ),
    )
    for relation in (observed, observed.execute()):
        result = relation.group_by(coordinate).rollup().execute()
        assert result.to_pandas().value.tolist() == [2.0]


def _component_model(tmp_path: Path, *, parquet: bool = True) -> None:
    _model(tmp_path)
    with duckdb.connect(str(tmp_path / "source.duckdb")) as con:
        con.execute("CREATE TABLE people(id BIGINT)")
        con.execute("INSERT INTO people VALUES (1)")
        if parquet:
            for name in ("facts", "people"):
                pq.write_table(
                    con.execute(f"SELECT * FROM {name}").to_arrow_table(),
                    tmp_path / f"{name}.parquet",
                )
    facts = "md.parquet('facts.parquet'," if parquet else "md.table('facts',"
    people = "md.parquet('people.parquet'," if parquet else "md.table('people',"
    (tmp_path / "models/semantic/sales/facts.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        "facts=ms.entity(name='facts', datasource=ms.ref.datasource('warehouse'), source="
        + facts
        + " columns={k:k for k in ['id','person','day','value','weight','cancel','category']}), primary_key=['id'])\n"
        "day=ms.time_dimension_column(name='day', entity=facts, column='day', granularity='day', is_default=True)\n"
        "category=ms.dimension_column(name='category', entity=facts, column='category')\n"
        "value=ms.measure_column(name='value', entity=facts, column='value', additivity=ms.additive_all())\n"
        "weight=ms.measure_column(name='weight', entity=facts, column='weight', additivity=ms.additive_all())\n"
        "cancel=ms.measure_column(name='cancel', entity=facts, column='cancel', additivity=ms.additive_all())\n"
        "total=ms.aggregate(name='total', measure=value, agg='sum')\n"
        "weights=ms.aggregate(name='weights', measure=weight, agg='sum')\n"
        "cancel_total=ms.aggregate(name='cancel_total', measure=cancel, agg='sum')\n"
        "people=ms.entity(name='people', datasource=ms.ref.datasource('warehouse'), source="
        + people
        + " columns={'id':'id'}), primary_key=['id'])\n"
        "person=ms.dimension_column(name='person', entity=facts, column='person')\n"
        "person_id=ms.dimension_column(name='person_id', entity=people, column='id')\n"
        "link=ms.relationship(name='link', from_entity=facts, to_entity=people, keys=[ms.join_on(person,person_id)])\n"
    )


@pytest.mark.runtime
@pytest.mark.parametrize(
    "method,value_type,weight_type,weight",
    [
        ("weighted", "DOUBLE", "BIGINT", 1.0),
        ("weighted", "DECIMAL(12,2)", "BIGINT", 1.0),
        ("ratio", "DOUBLE", "BIGINT", 1.0),
        ("ratio", "BIGINT", "DOUBLE", 1.0),
        ("ratio", "DECIMAL(12,2)", "DOUBLE", 1.0),
        ("ratio", "BIGINT", "DOUBLE", 1e-14),
        ("ratio", "DECIMAL(12,2)", "DOUBLE", 1e-14),
    ],
)
def test_mixed_component_attribution_source_and_fixed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    value_type: str,
    weight_type: str,
    weight: float,
) -> None:
    monkeypatch.chdir(tmp_path)
    with duckdb.connect(str(tmp_path / "source.duckdb")) as con:
        con.execute(
            f"CREATE TABLE facts(id BIGINT, person BIGINT, day DATE, value {value_type}, weight {weight_type}, cancel BIGINT, category VARCHAR)"
        )
        con.execute(
            "INSERT INTO facts VALUES (1,1,'2026-07-01',1,?,0,'a'),(2,1,'2026-08-01',2,?,0,'a')",
            [weight, weight],
        )
    _component_model(tmp_path)
    session = mv.session.get_or_create("mixed_attribution", report_timezone="UTC")
    members = session.members(ms.ref.entity("sales.people"))
    axis = ms.ref.dimension("sales.facts.category")
    metric = (
        mv.runtime_metric.weighted_mean(
            ms.ref.measure("sales.facts.value"), ms.ref.measure("sales.facts.weight"), label="mean"
        )
        if method == "weighted"
        else mv.runtime_metric.ratio(
            ms.ref.metric("sales.total"), ms.ref.metric("sales.weights"), label="rate"
        )
    )
    current = members.observe(
        metric,
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.link"),
        by=(
            mv.member(),
            axis,
        ),
    ).rollup()
    baseline = members.observe(
        metric,
        during=mv.time_scope(start="2026-07-01", end="2026-08-01"),
        via=ms.ref.relationship("sales.link"),
        by=(
            mv.member(),
            axis,
        ),
    ).rollup()
    change = current.compare(baseline)
    for relation in (change, change.execute()):
        if weight < 1:
            with pytest.raises(AnalysisError, match="denominator"):
                relation.attribute(axes=(axis,)).execute()
            continue
        rows = relation.attribute(axes=(axis,)).execute().to_pandas()
        assert rows.value.tolist() == [1.0]
        assert rows.cell_tag.tolist() == ["defined"]


@pytest.mark.runtime
@pytest.mark.parametrize(
    "family", ["duration", "float_negation", "float_prefix", "decimal_negation"]
)
def test_linear_promotes_before_signed_intermediate_arithmetic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, family: str
) -> None:
    monkeypatch.chdir(tmp_path)
    large = 2**63 - 1
    value_type, weight_type, cancel_type = {
        "duration": ("BIGINT", "BIGINT", "BIGINT"),
        "float_negation": ("DOUBLE", "BIGINT", "BIGINT"),
        "float_prefix": ("BIGINT", "BIGINT", "DOUBLE"),
        "decimal_negation": ("DECIMAL(12,2)", "BIGINT", "BIGINT"),
    }[family]
    negation = family.endswith("negation")
    values = (0, -(2**63), 0) if negation else (large, 1, 0 if family == "float_prefix" else large)
    with duckdb.connect(str(tmp_path / "source.duckdb")) as con:
        con.execute(
            f"CREATE TABLE facts(id BIGINT, person BIGINT, day DATE, value {value_type}, weight {weight_type}, cancel {cancel_type}, category VARCHAR)"
        )
        con.execute("INSERT INTO facts VALUES (1,1,'2026-08-01',?,?,?,'a')", values)
    _component_model(tmp_path, parquet=family == "duration")
    if family == "duration":
        path = tmp_path / "facts.parquet"
        table = pq.read_table(path)
        for name in ("value", "weight", "cancel"):
            table = table.set_column(
                table.schema.get_field_index(name), name, table[name].cast(pa.duration("ns"))
            )
        pq.write_table(table, path)
    session = mv.session.get_or_create("linear_intermediate", report_timezone="UTC")
    leaves = tuple(ms.ref.metric("sales." + name) for name in ("total", "weights", "cancel_total"))
    metric = mv.runtime_metric.linear(
        add=leaves[:1] if negation else leaves[:2],
        subtract=leaves[1:2] if negation else leaves[2:],
        label="result",
    )
    logical = session.members(ms.ref.entity("sales.facts")).observe(
        metric,
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        by=(mv.member(),),
    )
    fixed = logical.execute()
    for result in (fixed, logical.rollup().execute(), fixed.rollup().execute()):
        rows = result.to_pandas()
        assert rows.cell_tag.tolist() == ["defined"]
        if family == "duration":
            assert pa.array(rows.value).cast(pa.duration("ns")).cast(pa.int64()).to_pylist() == [1]
        elif family == "decimal_negation":
            assert rows.value.tolist() == [Decimal("9223372036854775808.00")]
        else:
            assert rows.value.tolist() == [float(2**63)]


@pytest.mark.runtime
@pytest.mark.parametrize("value_type,scale", [("DECIMAL(12,2)", 2), ("DECIMAL(30,6)", 6)])
def test_narrow_decimal_mean_difference_preserves_fixed_attribution_carrier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value_type: str, scale: int
) -> None:
    monkeypatch.chdir(tmp_path)
    with duckdb.connect(str(tmp_path / "source.duckdb")) as con:
        con.execute(
            f"CREATE TABLE facts(id BIGINT, person BIGINT, day DATE, value {value_type}, weight BIGINT, cancel BIGINT, category VARCHAR)"
        )
        con.execute(
            "INSERT INTO facts VALUES (1,1,'2026-07-01',1.01,1,0,'a'),(2,1,'2026-08-01',2.02,1,0,'a')"
        )
    _component_model(tmp_path)
    session = mv.session.get_or_create("decimal_attribution", report_timezone="UTC")
    members = session.members(ms.ref.entity("sales.people"))
    axis = ms.ref.dimension("sales.facts.category")
    metric = mv.runtime_metric.aggregate(
        ms.ref.measure("sales.facts.value"), agg="mean", label="mean"
    )
    current = members.observe(
        metric,
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.link"),
        by=(
            mv.member(),
            axis,
        ),
    )
    baseline = members.observe(
        metric,
        during=mv.time_scope(start="2026-07-01", end="2026-08-01"),
        via=ms.ref.relationship("sales.link"),
        by=(
            mv.member(),
            axis,
        ),
    )
    change = current.compare(baseline)
    fixed = change.execute()
    assert fixed._dataset is not None
    assert fixed._dataset.verified().primary.schema.field("value").type == pa.decimal128(38, scale)
    for relation in (change, fixed):
        rows = relation.attribute(axes=(axis,)).execute().to_pandas()
        assert rows.value.tolist() == [Decimal("1.01")]
        assert rows.cell_tag.tolist() == ["defined"]


@pytest.mark.runtime
@pytest.mark.parametrize("duration", [False, True])
def test_linear_still_rejects_final_carrier_overflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, duration: bool
) -> None:
    monkeypatch.chdir(tmp_path)
    with duckdb.connect(str(tmp_path / "source.duckdb")) as con:
        con.execute(
            "CREATE TABLE facts(id BIGINT, person BIGINT, day DATE, value BIGINT, weight BIGINT, cancel BIGINT, category VARCHAR)"
        )
        con.execute(
            "INSERT INTO facts VALUES (1,1,'2026-08-01',9223372036854775807,0,0,'a'),(2,1,'2026-08-01',0,1,0,'a')"
        )
    _component_model(tmp_path)
    if duration:
        path = tmp_path / "facts.parquet"
        table = pq.read_table(path)
        for name in ("value", "weight", "cancel"):
            table = table.set_column(
                table.schema.get_field_index(name), name, table[name].cast(pa.duration("ns"))
            )
        pq.write_table(table, path)
    session = mv.session.get_or_create("linear_overflow", report_timezone="UTC")
    metric = mv.runtime_metric.linear(
        add=[ms.ref.metric("sales.total"), ms.ref.metric("sales.weights")], label="total"
    )
    logical = session.members(ms.ref.entity("sales.facts")).observe(
        metric,
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        by=(mv.member(),),
    )
    fixed = logical.execute()
    for relation in (logical, fixed):
        with pytest.raises(AnalysisError):
            relation.rollup().execute()


@pytest.mark.runtime
def test_native_linear_comparison_uses_represented_cells_in_both_routes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    with duckdb.connect(str(tmp_path / "source.duckdb")) as con:
        con.execute(
            "CREATE TABLE facts(id BIGINT, person BIGINT, day DATE, value DOUBLE, weight DOUBLE, cancel DOUBLE, category VARCHAR)"
        )
        con.execute(
            "INSERT INTO facts VALUES (1,1,'2026-07-01',1e-14,1e-14,0,'a'),(2,1,'2026-08-01',2e-14,2e-14,0,'a')"
        )
    _component_model(tmp_path)
    session = mv.session.get_or_create("linear_comparison", report_timezone="UTC")
    members = session.members(ms.ref.entity("sales.people"))
    metric = mv.runtime_metric.linear(
        add=[ms.ref.metric("sales.total"), ms.ref.metric("sales.weights")], label="total"
    )
    current = members.observe(
        metric,
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.link"),
        by=(mv.member(),),
    )
    baseline = members.observe(
        metric,
        during=mv.time_scope(start="2026-07-01", end="2026-08-01"),
        via=ms.ref.relationship("sales.link"),
        by=(mv.member(),),
    )
    for change in (
        current.compare(baseline, value="relative_change"),
        current.execute().compare(baseline.execute(), value="relative_change"),
    ):
        result = change.execute()
        rows = result.to_pandas()
        assert rows.value.tolist() == [1.0]
        assert rows.cell_tag.tolist() == ["defined"]
        assert result._dataset is not None
        correspondence = next(
            part.table for part in result._dataset.verified().parts if part.role == "correspondence"
        )
        for side in ("current", "baseline"):
            assert correspondence[f"correspondence__{side}_error_bound"].to_pylist() == [0.0]
