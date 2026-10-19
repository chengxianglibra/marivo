"""Native aggregate transport and independent retained numeric components."""

import json
import os
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError
from marivo.datasource.ir import TableSourceIR
from tests.analysis.materialization.publication_fixtures import publication_counts
from tests.datasource.source_cases import SourceData, source_case


def _value(result: mv.AnalysisFrame) -> pd.Series:
    return result.to_pandas()[("value", "value")]


@pytest.mark.parametrize(
    "value_type,weight_type",
    [
        ("DECIMAL(12,2)", "BIGINT"),
        ("DOUBLE", "BIGINT"),
        ("INTEGER", "SMALLINT"),
        ("DECIMAL(12,2)", "DECIMAL(8,3)"),
    ],
)
def test_native_weighted_and_mean(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    value_type: str,
    weight_type: str,
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
        pq.write_table(
            con.execute("SELECT * FROM facts").to_arrow_table(), tmp_path / "facts.parquet"
        )
    model = tmp_path / "models/semantic/sales/facts.py"
    model.write_text(model.read_text().replace("md.table('facts',", "md.parquet('facts.parquet',"))
    session = mv.session.get_or_create("native", report_timezone="UTC")
    members = session.members(ms.ref.entity("sales.facts"))
    scope = session.time_scope(start="2026-07-01", end="2026-07-02")
    observed = session.observe(
        {"value": session.catalog.metrics.get("sales.weighted")}, during=scope, by=members
    )
    total = session.observe(
        {"value": session.catalog.metrics.get("sales.weighted")}, during=scope, members=members
    ).compute()
    assert _value(total).tolist() == pytest.approx([expected])
    saved = observed.persist()
    raw = (
        session.read(
            members,
            {
                "value": session.catalog.fields.get("sales.facts.value"),
                "weight": session.catalog.fields.get("sales.facts.weight"),
            },
        )
        .compute()
        .to_pandas()
    )
    pair = raw[("value", "value")].notna() & raw[("value", "weight")].notna()
    assert float(
        (raw[("value", "value")].loc[pair] * raw[("value", "weight")].loc[pair]).sum()
        / raw[("value", "weight")].loc[pair].sum()
    ) == pytest.approx(expected)
    assert saved.to_pandas().equals(session.persisted_frame(saved.artifact.id).to_pandas())
    coordinate = session.catalog.fields.get("sales.facts.category")
    with_coordinates = session.observe(
        {"value": session.catalog.metrics.get("sales.weighted")},
        during=scope,
        by=(mv.member(), coordinate),
        members=members,
    )
    result = session.observe(
        {"value": session.catalog.metrics.get("sales.weighted")},
        during=scope,
        by=(coordinate,),
        members=members,
    ).compute()
    assert _value(result).dropna().tolist() == pytest.approx([expected])
    grouped = session.observe(
        {"value": session.catalog.metrics.get("sales.mean")},
        during=scope,
        by=(coordinate,),
        members=members,
    )
    grouped_mean = grouped.compute()
    # Direct source means follow the independently executed native SQL carrier.
    assert _value(grouped_mean).tolist() == [row[0] for row in expected_mean]
    if value_type.startswith("DECIMAL"):
        assert all(isinstance(value, Decimal) for value in raw[("value", "value")].dropna())
        expected_a = ((Decimal("1.01") + Decimal("1.02")) / 2).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_EVEN
        )
        # Current rows have their own HalfEven arithmetic. This is not source
        # mean reconstruction: each admitted fact is one represented row here.
        represented = session.observe(
            {"value": session.catalog.metrics.get("sales.mean")},
            during=scope,
            by=(mv.member(), coordinate),
            members=members,
        ).compute()
        row_means = represented.reduce(
            {"value": represented["value"].mean()}, by=(represented.axes[1],)
        ).compute()
        assert _value(row_means).tolist() == [expected_a, Decimal("5.00")]
    mean = session.observe(
        {"value": session.catalog.metrics.get("sales.mean")}, during=scope, by=members
    )
    assert float(
        _value(
            session.observe(
                {"value": session.catalog.metrics.get("sales.mean")}, during=scope, members=members
            ).compute()
        ).iloc[0]
    ) == pytest.approx(2.34, abs=0.01)


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
        "frozen=ms.finalize()\n"
        "value_row=frozen.resolve(value)\nweight_row=frozen.resolve(weight)\ntime=frozen.resolve(day)\n"
        "@ms.metric()\ndef weighted():\n    return value_row.weighted_mean(weight=weight_row,time=time)\n"
        "@ms.metric()\ndef total():\n    return value_row.sum(time=time)\n"
        "@ms.metric()\ndef weights():\n    return weight_row.sum(time=time)\n"
        "@ms.metric()\ndef mean():\n    return value_row.mean(time=time)\n"
    )


@pytest.mark.runtime
@pytest.mark.parametrize("fixed", [False, True], ids=["source", "fixed"])
def test_clickhouse_weight_state_overflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fixed: bool
) -> None:
    _clickhouse_weight_state_overflow(tmp_path, monkeypatch, fixed=fixed)


def _clickhouse_weight_state_overflow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    fixed: bool,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    _model(tmp_path)
    rows = "(1,'2026-07-01',1.01,9223372036854775807,'a'),(2,'2026-07-01',1.02,9223372036854775807,'a')"
    data = SourceData(
        "id BIGINT, day DATE, value DECIMAL(12,2), weight BIGINT, category VARCHAR(10)",
        rows,
        "id Int64, day Date, value Nullable(Decimal(12,2)), weight Nullable(Int64), category String",
        [
            {
                "id": i,
                "day": date(2026, 7, 1),
                "value": value,
                "weight": 9223372036854775807,
                "category": category,
            }
            for i, value, category in [
                (1, Decimal("1.01"), "a"),
                (2, Decimal("1.02"), "a"),
            ]
        ],
    )
    with source_case("clickhouse", "mergetree", tmp_path, monkeypatch, data) as case:
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
            + "md.clickhouse(name='warehouse',"
            + ",".join(f"{key}={value!r}" for key, value in arguments.items())
            + ")\n"
        )
        model = tmp_path / "models/semantic/sales/facts.py"
        model.write_text(
            model.read_text().replace("md.table('facts',", f"md.table({case.source.table!r},")
        )
        session = mv.session.get_or_create("native_remote", report_timezone="UTC")
        members = session.members(ms.ref.entity("sales.facts"))
        scope = session.time_scope(start="2026-07-01", end="2026-07-02")
        from marivo.analysis.materialization.errors import MaterializationError

        saved = (
            session.read(
                members, {"value": session.catalog.fields.get("sales.facts.value")}
            ).persist()
            if fixed
            else None
        )
        previous = saved.to_pandas() if saved is not None else None
        if saved is not None:
            weights = session.read(
                members, {"weight": session.catalog.fields.get("sales.facts.weight")}
            ).compute()
            assert weights.to_pandas()[("value", "weight")].tolist() == [2**63 - 1] * 2
        before = publication_counts(session)
        with pytest.raises(MaterializationError) as raised:
            session.observe(
                {"value": session.catalog.metrics.get("sales.weighted")},
                during=scope,
                members=members,
            ).compute()
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
                "native-clickhouse-weight-overflow-" + ("fixed" if fixed else "source") + ".json",
            ).write_text(
                json.dumps(
                    {
                        "columns": data.columns,
                        "values": data.values,
                        "raw_rows": data.rows,
                        "mathematical_weight_sum": 2 * (2**63 - 1),
                        "weight_carrier": "int64",
                        "route": "source_original_after_capture" if fixed else "source_ibis",
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
    coordinate = session.catalog.fields.get("sales.facts.category")
    metric = session.catalog.metrics.get("sales.total") - session.catalog.metrics.get(
        "sales.weights"
    )
    observed = session.observe(
        {"value": metric},
        during=session.time_scope(start="2026-07-01", end="2026-07-02"),
        by=(coordinate,),
    )
    result = observed.persist()
    assert _value(result).tolist() == [2.0]
    assert session.persisted_frame(result.artifact.id).to_pandas().equals(result.to_pandas())


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
        "people=ms.entity(name='people', datasource=ms.ref.datasource('warehouse'), source="
        + people
        + " columns={'id':'id'}), primary_key=['id'])\n"
        "person=ms.dimension_column(name='person', entity=facts, column='person')\n"
        "person_id=ms.dimension_column(name='person_id', entity=people, column='id')\n"
        "link=ms.relationship(name='link', from_entity=facts, to_entity=people, keys=[ms.join_on(person,person_id)])\n"
        "frozen=ms.finalize()\n"
        "value_row=frozen.resolve(value)\nweight_row=frozen.resolve(weight)\ncancel_row=frozen.resolve(cancel)\ntime=frozen.resolve(day)\n"
        "@ms.metric()\ndef total():\n    return value_row.sum(time=time)\n"
        "@ms.metric()\ndef weights():\n    return weight_row.sum(time=time)\n"
        "@ms.metric()\ndef cancel_total():\n    return cancel_row.sum(time=time)\n"
        "@ms.metric()\ndef mean():\n    return value_row.mean(time=time)\n"
        "@ms.metric()\ndef weighted():\n    return value_row.weighted_mean(weight=weight_row,time=time)\n"
    )


@pytest.mark.parametrize(
    "method,value_type,weight_type,weight",
    [
        ("weighted", "DOUBLE", "BIGINT", 1.0),
        ("weighted", "DECIMAL(12,2)", "BIGINT", 1.0),
        ("ratio", "DOUBLE", "BIGINT", 1.0),
        ("ratio", "BIGINT", "DOUBLE", 1.0),
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
    axis = session.catalog.fields.get("sales.facts.category")
    metric = (
        session.catalog.metrics.get("sales.weighted")
        if method == "weighted"
        else session.catalog.metrics.get("sales.total")
        / session.catalog.metrics.get("sales.weights")
    )
    current = session.observe(
        {"value": metric},
        during=session.time_scope(start="2026-08-01", end="2026-09-01"),
        by=(axis,),
        members=members,
    )
    baseline = session.observe(
        {"value": metric},
        during=session.time_scope(start="2026-07-01", end="2026-08-01"),
        by=(axis,),
        members=members,
    )
    changed = current.compare(
        baseline, on=tuple(zip(current.axes, baseline.axes, strict=True))
    ).persist()
    expected = 1.0 if method == "weighted" else 1.0 / weight
    assert changed.to_pandas()[("value", "absolute.value")].tolist() == pytest.approx([expected])
    assert session.persisted_frame(changed.artifact.id).to_pandas().equals(changed.to_pandas())


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
    metrics = session.catalog.metrics
    total, weights, cancel = (
        metrics.get("sales." + name) for name in ("total", "weights", "cancel_total")
    )
    metric = total - weights if negation else (total + weights) - cancel
    scope = session.time_scope(start="2026-08-01", end="2026-09-01")
    members = session.members(ms.ref.entity("sales.facts"))
    logical = session.observe({"value": metric}, during=scope, by=members)
    if family in {"duration", "float_prefix"}:
        with pytest.raises(AnalysisError):
            logical.compute()
        metric = (total - cancel) + weights if family == "duration" else total + (weights - cancel)
        logical = session.observe({"value": metric}, during=scope, by=members)
    fixed = logical.persist()
    for result in (
        fixed,
        session.observe({"value": metric}, during=scope, members=members).compute(),
        session.persisted_frame(fixed.artifact.id),
    ):
        result_values = _value(result)
        if family == "duration":
            assert pa.array(result_values).cast(pa.duration("ns")).cast(pa.int64()).to_pylist() == [
                1
            ]
        elif family == "decimal_negation":
            assert result_values.tolist() == [Decimal("9223372036854775808.00")]
        else:
            assert result_values.tolist() == [float(2**63)]


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
    axis = session.catalog.fields.get("sales.facts.category")
    metric = session.catalog.metrics.get("sales.mean")
    current = session.observe(
        {"value": metric},
        during=session.time_scope(start="2026-08-01", end="2026-09-01"),
        by=(axis,),
        members=members,
    )
    baseline = session.observe(
        {"value": metric},
        during=session.time_scope(start="2026-07-01", end="2026-08-01"),
        by=(axis,),
        members=members,
    )
    fixed = current.compare(
        baseline, on=tuple(zip(current.axes, baseline.axes, strict=True))
    ).persist()
    for result in (fixed, session.persisted_frame(fixed.artifact.id)):
        values = result.to_pandas()[("value", "absolute.value")]
        assert values.tolist() == [Decimal("1.01")]
        dtype = values.dtype
        precision = int(value_type.split("(")[1].split(",")[0])
        assert isinstance(dtype, pd.ArrowDtype) and dtype.pyarrow_dtype == pa.decimal128(
            precision, scale
        )


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
    metric = session.catalog.metrics.get("sales.total") + session.catalog.metrics.get(
        "sales.weights"
    )
    logical = session.observe(
        {"value": metric},
        during=session.time_scope(start="2026-08-01", end="2026-09-01"),
        by=session.members(ms.ref.entity("sales.facts")),
    )
    fixed = logical.persist()
    previous = fixed.to_pandas()
    with pytest.raises(AnalysisError):
        session.observe(
            {"value": metric},
            during=session.time_scope(start="2026-08-01", end="2026-09-01"),
            members=session.members(ms.ref.entity("sales.facts")),
        ).compute()
    assert fixed.to_pandas().equals(previous)
    assert session.persisted_frame(fixed.artifact.id).to_pandas().equals(previous)


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
    metric = session.catalog.metrics.get("sales.total") + session.catalog.metrics.get(
        "sales.weights"
    )
    current = session.observe(
        {"value": metric},
        during=session.time_scope(start="2026-08-01", end="2026-09-01"),
        by=members,
    )
    baseline = session.observe(
        {"value": metric},
        during=session.time_scope(start="2026-07-01", end="2026-08-01"),
        by=members,
    )
    result = current.compare(
        baseline, on=tuple(zip(current.axes, baseline.axes, strict=True))
    ).persist()
    assert result.to_pandas()[("value", "relative.value")].tolist() == [1.0]
    current_values, baseline_values = _value(current.compute()), _value(baseline.compute())
    assert ((current_values - baseline_values) / baseline_values.abs()).tolist() == [1.0]
    assert session.persisted_frame(result.artifact.id).to_pandas().equals(result.to_pandas())
