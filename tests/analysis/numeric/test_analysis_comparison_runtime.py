"""Source Query comparisons with raw-fact oracles and terminal cold reads."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import replace
from decimal import Decimal
from fractions import Fraction

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from ibis.common.deferred import Deferred

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError
from tests.shared_fixtures import (
    DslCase,
    DslCaseFactory,
    analysis_dsl_rows,
    export_dsl_parquet_models,
)
from tests.support.paths import PROJECT_ROOT


def _plain(result: mv.Artifact) -> pd.DataFrame:
    frame = result.to_pandas()
    names: list[str] = []
    key = 0
    for label in frame.columns:
        assert isinstance(label, tuple) and len(label) == 2
        kind, name = label
        if kind == "value":
            assert isinstance(name, str)
            names.append(name)
        elif kind == "presence":
            assert isinstance(name, tuple)
            names.append("presence." + ".".join(name))
        else:
            names.append(f"key_{key}")
            key += 1
    frame.columns = names
    return frame


def _monthly(case: DslCase, month: int, metric: str = "revenue") -> mv.Query:
    session = case.session
    return session.observe(
        {"value": session.catalog.metrics.get("sales." + metric)},
        during=session.time_scope(start=f"2026-{month:02}-01", end=f"2026-{month + 1:02}-01"),
        by=session.members(ms.ref.entity("sales.customer")),
    )


def _numeric_source(case: DslCase, physical: str, parquet: bool) -> None:
    with duckdb.connect(str(case.database_path)) as connection:
        if not physical.startswith("duration"):
            connection.execute(f'ALTER TABLE "order" ALTER amount TYPE {physical}')
    if parquet:
        export_dsl_parquet_models(case, case.root)
        if physical.startswith("duration"):
            path = case.root / "source_files/order.parquet"
            data = pq.read_table(path)
            data = data.set_column(
                data.schema.get_field_index("amount"),
                "amount",
                data["amount"].cast(pa.duration(physical.partition("_")[2] or "us")),
            )
            pq.write_table(data, path)
    ms.load(workspace_dir=case.root)


def _cold(case: DslCase, results: tuple[mv.Artifact, ...], assertions: str) -> None:
    source = case.root / "source_files"
    if not source.exists():
        source = case.database_path
    offline = source.with_name(source.name + ".offline")
    source.rename(offline)
    script = (
        """
import sys
from unittest.mock import patch
import duckdb
import pandas as pd
import marivo.analysis as mv
import marivo.semantic as ms
from marivo.datasource.adapters import SourceSession
from tests.analysis.numeric.test_analysis_comparison_runtime import _plain
from tests.shared_fixtures import run_ids

def unavailable(*args, **kwargs):
    raise AssertionError('terminal comparison reopened source or Semantic')
with patch.object(duckdb, 'connect', unavailable), patch.object(ms, 'load', unavailable), patch.object(SourceSession, '__init__', unavailable):
    session = mv.session.resume(sys.argv[1], by='id')
    before = run_ids(session)
    saved = tuple(session.artifact(ref) for ref in sys.argv[2:])
    assert all(isinstance(item, mv.Artifact) for item in saved)
    assert all(not hasattr(item, name) for item in saved for name in ('compare', 'where', 'rollup', 'contract', '_node', '_dataset'))
"""
        + "".join("    " + line + "\n" for line in assertions.splitlines())
        + "    assert run_ids(session) == before\n"
    )
    try:
        process = subprocess.run(
            [sys.executable, "-c", script, case.session.id, *(result.id for result in results)],
            cwd=case.root,
            env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT), "MARIVO_TELEMETRY": "off"},
            capture_output=True,
            text=True,
            check=False,
            timeout=90,
        )
    finally:
        offline.rename(source)
    assert process.returncode == 0, process.stdout + process.stderr


@pytest.mark.parametrize("physical", ["BIGINT", "DOUBLE", "DECIMAL(30,6)", "duration"])
def test_public_numeric_difference_source_and_fixed(
    analysis_dsl_case_factory: DslCaseFactory, physical: str
) -> None:
    case = analysis_dsl_case_factory("j2")
    with duckdb.connect(str(case.database_path)) as connection:
        raw = connection.execute('SELECT customer_id, ordered_at, amount FROM "order"').fetchall()
    _numeric_source(case, physical, True)
    current, baseline = _monthly(case, 8), _monthly(case, 7)
    comparison = current.compare(baseline)
    saved = comparison.execute()
    source = _plain(saved).set_index("key_0")
    recovered = _plain(case.session.artifact(saved.id)).set_index("key_0")
    expected: dict[str, Fraction] = {}
    totals: dict[tuple[str, str], Fraction] = {}
    for member, occurred, amount in raw:
        month = str(occurred)[:7]
        if month in ("2026-07", "2026-08"):
            totals[member, month] = totals.get((member, month), Fraction()) + Fraction(amount)
            expected[member] = expected.get(member, Fraction()) + Fraction(amount) * (
                1 if month == "2026-08" else -1
            )
    for frame in (source, recovered):
        absolute = frame["value.absolute"]
        if physical.startswith("duration"):
            absolute = pd.Series(
                pa.array(absolute).cast(pa.duration("us")).cast(pa.int64()).to_pylist(),
                index=absolute.index,
            )
        assert absolute.to_dict() == expected
        assert frame[["presence.current", "presence.baseline"]].all().all()
        for member in frame.index:
            denominator = totals[member, "2026-07"]
            if denominator == 0:
                assert pd.isna(frame.loc[member, "value.relative"])
            else:
                assert frame.loc[member, "value.relative"] == float(
                    expected[member] / abs(denominator)
                )
    if physical.startswith("DECIMAL"):
        assert all(type(value) is Decimal for value in recovered["value.absolute"])
    with pytest.raises(AnalysisError, match="nonzero denominator"):
        current.compare(baseline, zero_denominator="error").execute()
    if physical.startswith("duration"):
        session = case.session
        amount = session.catalog.fields.get("sales.order.amount")
        time = session.catalog.fields.get("sales.order.ordered_at")
        nullable = session.observe(
            {"value": amount.sum(time=time, empty="null")},
            during=session.time_scope(start="2026-09-01", end="2026-10-01"),
            by=session.members(ms.ref.entity("sales.customer")),
        )
        null_saved = nullable.compare(current).execute()
        nulls = _plain(null_saved).set_index("key_0")
        assert pd.isna(nulls.loc["D", "value.current"])
        assert pd.isna(nulls.loc["D", "value.absolute"])
        assert pd.isna(nulls.loc["D", "value.relative"])
        assert nulls.loc["D", "presence.current"] and nulls.loc["D", "presence.baseline"]
        assert pa.array(nulls["value.absolute"]).type == pa.duration("us")
        pd.testing.assert_frame_equal(
            null_saved.to_pandas(), session.artifact(null_saved.id).to_pandas()
        )
    # Both saved endpoints are ordinary data; their independent pandas comparison agrees.
    first, second = _plain(current.execute()), _plain(baseline.execute())
    assert first.key_0.tolist() == second.key_0.tolist()
    if not physical.startswith("duration"):
        assert (
            dict(
                zip(
                    first.key_0,
                    (
                        Fraction(left) - Fraction(right)
                        for left, right in zip(first.value, second.value, strict=True)
                    ),
                    strict=True,
                )
            )
            == expected
        )


@pytest.mark.slow
def test_comparison_v2_cold_continuation_and_correspondence_integrity(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    _numeric_source(case, "BIGINT", True)
    current, baseline = _monthly(case, 9), _monthly(case, 8)
    comparison = current.compare(baseline)
    selected = comparison.where(~comparison["value.relative"].is_null())
    saved, filtered = comparison.execute(), selected.execute()
    data = saved._manifest.data
    assert len(data.schema.presence) == 2
    with pytest.raises(AnalysisError):
        replace(data.schema, presence=(replace(data.schema.presence[0], nullable=True),))
    with pytest.raises(AnalysisError):
        replace(
            data,
            coordinate_projections=(replace(data.coordinate_projections[0], columns=("forged",)),),
        )
    _cold(
        case,
        (saved, filtered),
        """frame = _plain(saved[0]).set_index('key_0')
assert frame.loc['D', 'value.current'] == 0
assert frame.loc['D', 'value.absolute'] == 0
assert pd.isna(frame.loc['D', 'value.relative'])
assert frame[['presence.current', 'presence.baseline']].all().all()
assert _plain(saved[1]).key_0.tolist() == ['A', 'B']""",
    )


@pytest.mark.slow
def test_nested_difference_keeps_independent_captures(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.executemany(
            'INSERT INTO "order" VALUES (?, ?, ?, ?, ?, ?)',
            [(f"june_{member}", member, "web", "paid", "2026-06-10", 0) for member in "ABCD"],
        )
    _numeric_source(case, "BIGINT", True)
    august, july_first, july_second, june = (_monthly(case, month) for month in (8, 7, 7, 6))
    first = august.compare(july_first)
    first = first.select({"value": first["value.absolute"]})
    second = july_second.compare(june)
    second = second.select({"value": second["value.absolute"]})
    nested = first.compare(second)
    saved = nested.execute()
    expected = {"A": -140, "B": -80, "C": -100, "D": 0}
    assert _plain(saved).set_index("key_0")["value.absolute"].to_dict() == expected
    # Each endpoint is reevaluated from its Query, with no retained result graph.
    assert saved.id != nested.execute().id
    _cold(
        case,
        (saved,),
        """frame = _plain(saved[0]).set_index('key_0')
assert frame['value.absolute'].to_dict() == {'A': -140, 'B': -80, 'C': -100, 'D': 0}
assert frame[['presence.current', 'presence.baseline']].all().all()""",
    )


def test_ordinary_ratio_and_cohort_singleton(analysis_dsl_case_factory: DslCaseFactory) -> None:
    case = analysis_dsl_case_factory("j2")
    _numeric_source(case, "BIGINT", True)
    current = _monthly(case, 8)
    ratio = current.select({"value": current["value"] / current["value"]})
    saved = ratio.execute()
    frame = _plain(saved).set_index("key_0")
    assert frame.loc[["A", "B"], "value"].tolist() == [1.0, 1.0]
    assert frame.loc[["C", "D"], "value"].isna().all()
    assert not hasattr(saved, "rollup") and not hasattr(saved, "contract")
    first, second = current.rollup(), _monthly(case, 8).rollup()
    assert _plain(first.compare(second).execute())["value.absolute"].tolist() == [0]


def test_union_keep_distinguishes_missing_coordinate_and_rejects_filtered_empty(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    _numeric_source(case, "BIGINT", True)
    current, baseline = _monthly(case, 8), _monthly(case, 7)
    left, right = current.where(current["value"] > 0), baseline.where(baseline["value"] > 0)
    with pytest.raises(AnalysisError, match="equal complete endpoint key images"):
        left.compare(right).execute()
    saved = left.compare(right, how="outer").execute()
    frame = _plain(saved).set_index("key_0")
    assert frame.loc["A", "value.absolute"] == -40
    assert frame.loc["B", "value.absolute"] == 20
    assert pd.isna(frame.loc["C", "value.absolute"])
    assert not frame.loc["C", "presence.current"]
    assert frame.loc["C", "presence.baseline"]
    pd.testing.assert_frame_equal(saved.to_pandas(), case.session.artifact(saved.id).to_pandas())


def test_cohort_group_metric_empty_preserves_metric_null_policy(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    _numeric_source(case, "BIGINT", True)
    session = case.session
    members = session.members(ms.ref.entity("sales.customer"))
    region = session.catalog.fields.get("sales.customer.region")
    categories = session.read(members, {"region": region})
    eastern = categories.where(categories["region"] == "east").population()
    amount, time = (
        session.catalog.fields.get("sales.order." + name) for name in ("amount", "ordered_at")
    )
    metrics = {
        "revenue": amount.sum(time=time, empty="null"),
        "order_count": session.catalog.metrics.get("sales.order_count"),
    }
    metrics["count_ratio"] = metrics["order_count"] / metrics["order_count"]
    for name, metric in metrics.items():

        def observe(population: mv.Population, metric: Deferred = metric) -> mv.Query:
            return session.observe(
                {"value": metric},
                during=session.time_scope(start="2026-07-01", end="2026-08-01"),
                by=population.classify(by=(region,)),
                members=population,
            )

        first, second = observe(members), observe(eastern)
        result = _plain(first.compare(second, how="outer").execute()).set_index("key_0")
        assert result.index.tolist() == ["east", "south", "west"]
        assert result.loc["east", "value.absolute"] == 0
        assert result.loc[["south", "west"], "value.absolute"].isna().all()
        assert not result.loc[["south", "west"], "presence.baseline"].any()
        if name == "count_ratio":
            assert result["value.current"].tolist() == [1.0, 1.0, 1.0]
        elif name == "order_count":
            assert result.loc[["south", "west"], "value.current"].tolist() == [1, 1]


def test_period_change_retains_original_buckets_and_rejects_renumbering(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    _numeric_source(case, "BIGINT", True)
    session = case.session

    def observe(month: int, day: int, length: int) -> mv.Query:
        axis = session.time_scope(
            start=f"2026-{month:02}-{day:02}", end=f"2026-{month:02}-{day + length:02}"
        ).by(mv.grain("day"))
        return session.observe(
            {"value": session.catalog.metrics.get("sales.order_count")},
            during=axis,
            by=session.members(ms.ref.entity("sales.customer")),
        )

    current, baseline = observe(8, 14, 3), observe(7, 9, 3)
    with pytest.raises(AnalysisError, match="equal complete calendar bucket counts"):
        current.compare(observe(7, 9, 2), match=mv.ordinal()).execute()
    saved = current.compare(baseline, match=mv.ordinal()).execute()
    frame = _plain(saved)
    assert len(frame) == 12
    assert sorted(frame["value.absolute"].tolist()) == [-1] + [0] * 11
    axes = saved.to_pandas().attrs["marivo"]["axes"]
    projection = axes[0]["projections"]
    assert projection[0]["bounds"] != projection[1]["bounds"]
    selected = current.where(current["value"] > 0)
    with pytest.raises(AnalysisError, match="equal complete endpoint key images"):
        selected.compare(baseline, match=mv.ordinal()).execute()
    outer = _plain(selected.compare(baseline, match=mv.ordinal(), how="outer").execute())
    assert set(outer.key_0) == {"(0, 0)", "(1, 1)", "(2, 2)"}


@pytest.mark.parametrize("calendar", [False, True])
def test_one_to_one_binds_exact_ordered_nodes_and_retained_relationship(
    analysis_dsl_case_factory: DslCaseFactory, calendar: bool
) -> None:
    case = analysis_dsl_case_factory("j2")
    model = case.root / "models/semantic/sales/models.py"
    model.write_text(
        model.read_text().replace(
            "finalized = ms.finalize()",
            """
account = ms.entity(name='account', datasource=warehouse, source=md.table('customer'), primary_key=['customer_id'])
account_id = ms.dimension_column(name='customer_id', entity=account, column='customer_id')
customer_account = ms.relationship(name='customer_account', from_entity=customer, to_entity=account, keys=[ms.join_on(customer_id, account_id)])
finalized = ms.finalize()
""",
            1,
        )
    )
    ms.load(workspace_dir=case.root)
    session = case.session
    customer, account = (
        session.members(ms.ref.entity("sales." + name)) for name in ("customer", "account")
    )
    scope = (
        session.time_scope(start="2026-07-09", end="2026-07-12")
        if calendar
        else session.time_scope(start="2026-07-01", end="2026-08-01")
    )
    during = scope.by(mv.grain("day")) if calendar else scope
    metric = session.catalog.metrics.get("sales.revenue")
    left = session.observe({"value": metric}, during=during, by=customer)
    right = session.observe({"value": metric}, during=during, by=account)
    relation = session.catalog.relationships.get("sales.customer_account")
    pairing = mv.corresponding(via=(relation,))
    aligned = left.align(right, match=pairing)
    ratio = aligned.select({"value": aligned["left.value"] / aligned["right.value"]})
    result = _plain(ratio.execute())
    assert result.value.dropna().tolist() == [1.0, 1.0, 1.0]
    assert len(result) == (12 if calendar else 4)
    with pytest.raises(AnalysisError):
        right.align(left, match=pairing).execute()
    invalid = mv.corresponding(via=(session.catalog.relationships.get("sales.order_buyer"),))
    with pytest.raises(AnalysisError):
        left.align(right, match=invalid).execute()
    if calendar:
        assert result.key_0.nunique() == 3
        assert ratio.reduce({"count": mv.row_count()}).execute().to_pandas()[
            ("value", "count")
        ].tolist() == [12]


@pytest.mark.slow
def test_relative_and_ratio_cold_fixed_composition(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    _numeric_source(case, "BIGINT", True)
    current, baseline = _monthly(case, 8), _monthly(case, 7)
    totals = current.rollup().compare(baseline.rollup())
    positive = current.where(current["value"] > 0)
    ratio = positive.select({"value": positive["value"] / positive["value"]})
    individual = current.compare(baseline)
    _cold(
        case,
        (totals.execute(), ratio.execute(), individual.execute()),
        """assert _plain(saved[0])['value.relative'].tolist() == [-0.28]
assert _plain(saved[1]).value.tolist() == [1.0, 1.0]
frame = _plain(saved[2]).set_index('key_0')
assert frame.loc['A', 'value.relative'] == -0.4
assert frame.loc['B', 'value.relative'] == 0.2
assert pd.isna(frame.loc['D', 'value.relative'])""",
    )


def test_float_sum_and_native_mean_comparison_values(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute('ALTER TABLE "order" ALTER amount TYPE DOUBLE')
        connection.execute('DELETE FROM "order"')
        connection.executemany(
            'INSERT INTO "order" VALUES (?, ?, ?, ?, ?, ?)',
            [
                ("old-1", "A", "web", "paid", "2026-07-10", 1.0),
                ("old-2", "A", "web", "paid", "2026-07-10", -1.0 + 1e-13),
                ("new", "A", "web", "paid", "2026-08-10", 2.0),
            ],
        )
    session = case.session
    identities = session.read(
        session.members(ms.ref.entity("sales.customer")),
        {"id": session.catalog.fields.get("sales.customer.customer_id")},
    )
    members = identities.where(identities["id"] == "A").population()
    amount, time = (
        session.catalog.fields.get("sales.order." + name) for name in ("amount", "ordered_at")
    )
    for metric, denominator in (
        (amount.sum(time=time), 1.0 + (-1.0 + 1e-13)),
        (amount.mean(time=time), (1.0 + (-1.0 + 1e-13)) / 2),
    ):

        def observe(month: int, metric: Deferred = metric) -> mv.Query:
            return session.observe(
                {"value": metric},
                during=session.time_scope(
                    start=f"2026-{month:02}-01", end=f"2026-{month + 1:02}-01"
                ),
                by=members,
                members=members,
            )

        frame = _plain(observe(8).compare(observe(7)).execute())
        assert frame["value.baseline"].tolist() == [denominator]
        assert frame["value.relative"].tolist() == pytest.approx(
            [(2.0 - denominator) / abs(denominator)]
        )


def test_comparison_construction_is_lazy_and_shared_source_runs_once(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.support.source_trace import capture_source

    case = analysis_dsl_case_factory("j2")
    observed, independent = _monthly(case, 8), _monthly(case, 7)
    trace = capture_source(monkeypatch)
    ratio = observed.select({"value": observed["value"] / observed["value"]})
    comparison = observed.compare(independent)
    assert not trace.native_sql and not trace.owners
    first = ratio.execute()
    first_reads = len(trace.native_sql)
    assert first_reads > 0 and all(owner._closed for owner in trace.owners)
    assert sum('"amount"' in sql for sql in trace.native_sql) == 1
    second = ratio.execute()
    assert len(trace.native_sql) == first_reads * 2
    assert first.id != second.id
    pd.testing.assert_frame_equal(first.to_pandas(), second.to_pandas())
    assert _plain(comparison.execute())["value.absolute"].tolist() == [-40, 20, -50, 0]


@pytest.mark.parametrize("kind", ["ratio", "linear", "weighted"])
def test_comparison_of_float_original_expression_operands(
    analysis_dsl_case_factory: DslCaseFactory, kind: str
) -> None:
    case = analysis_dsl_case_factory("j2")
    _numeric_source(case, "DOUBLE", False)
    session = case.session
    identities = session.read(
        session.members(ms.ref.entity("sales.customer")),
        {"id": session.catalog.fields.get("sales.customer.customer_id")},
    )
    members = identities.where(identities["id"] == "A").population()
    amount, time = (
        session.catalog.fields.get("sales.order." + name) for name in ("amount", "ordered_at")
    )
    base = session.catalog.metrics.get("sales.revenue")
    metric = (
        base / base
        if kind == "ratio"
        else base + base
        if kind == "linear"
        else amount.weighted_mean(weight=amount, time=time)
    )

    def observe(month: int, metric: Deferred = metric) -> mv.Query:
        return session.observe(
            {"value": metric},
            during=session.time_scope(start=f"2026-{month:02}-01", end=f"2026-{month + 1:02}-01"),
            by=members,
            members=members,
        )

    result = observe(8).compare(observe(7)).execute()
    assert _plain(result)["value.absolute"].tolist() == [
        {"ratio": 0.0, "linear": -80.0, "weighted": -40.0}[kind]
    ]
    pd.testing.assert_frame_equal(result.to_pandas(), session.artifact(result.id).to_pandas())


def test_union_keeps_present_nondefined_cell_separate_from_absence(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    session = case.session
    members = session.members(ms.ref.entity("sales.customer"))
    region = session.catalog.fields.get("sales.customer.region")
    categories = session.read(members, {"region": region})
    eastern = categories.where(categories["region"] == "east").population()
    amount, time = (
        session.catalog.fields.get("sales.order." + name) for name in ("amount", "ordered_at")
    )
    for metric in (
        amount.sum(time=time, empty="null"),
        session.catalog.metrics.get("sales.order_count") / amount.sum(time=time),
    ):

        def observe(population: mv.Population, metric: Deferred = metric) -> mv.Query:
            return session.observe(
                {"value": metric},
                during=session.time_scope(start="2026-08-01", end="2026-09-01"),
                by=population.classify(by=(region,)),
                members=population,
            )

        result = observe(eastern).compare(observe(members), how="outer").execute()
        frame = _plain(result).set_index("key_0")
        assert frame.loc[["south", "west"], "value.absolute"].isna().all()
        assert not frame.loc[["south", "west"], "presence.current"].any()
        assert frame.loc[["south", "west"], "presence.baseline"].all()
        assert pd.isna(frame.loc["west", "value.baseline"])
        pd.testing.assert_frame_equal(result.to_pandas(), session.artifact(result.id).to_pandas())


def test_public_decimal_ratio_uses_declared_float_carrier(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute('ALTER TABLE "order" ALTER amount TYPE DECIMAL(30,6)')
        connection.execute('ALTER TABLE "order" ADD denominator DECIMAL(30,6) DEFAULT 2')
        connection.execute(
            "UPDATE \"order\" SET amount = CASE WHEN customer_id = 'A' THEN 0.000001 ELSE 0.000003 END"
        )
    model = case.root / "models/semantic/sales/models.py"
    model.write_text(
        model.read_text().replace(
            "finalized = ms.finalize()",
            "denominator_value = ms.measure_column(name='denominator', entity=orders, column='denominator', additivity=ms.additive_all(), unit='CNY')\nfinalized = ms.finalize()",
            1,
        )
    )
    ms.load(workspace_dir=case.root)
    session = case.session
    members = session.members(ms.ref.entity("sales.customer"))
    time = session.catalog.fields.get("sales.order.ordered_at")
    denominator = session.catalog.fields.get("sales.order.denominator").sum(time=time)
    q = session.observe(
        {"numerator": session.catalog.metrics.get("sales.revenue"), "denominator": denominator},
        during=session.time_scope(start="2026-08-01", end="2026-09-01"),
        by=members,
        members=members,
    )
    result = q.select({"value": q["numerator"] / q["denominator"]}).execute()
    values = _plain(result).set_index("key_0").value.to_dict()
    assert values == {
        member: float(Fraction(Decimal("0.000001") if member == "A" else Decimal("0.000003")) / 2)
        for member in "ABCD"
    }
    assert pa.array(result.to_pandas()[("value", "value")]).type == pa.float64()
    pd.testing.assert_frame_equal(result.to_pandas(), session.artifact(result.id).to_pandas())


def test_ordinary_ratio_over_untimed_numeric_read(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    session = case.session
    values = session.read(
        session.members(ms.ref.entity("sales.order")),
        {"value": session.catalog.fields.get("sales.order.amount")},
    )
    saved = values.select({"value": values["value"] / values["value"]}).execute()
    result = _plain(saved)
    assert result.value.notna().sum() > 0 and result.value.dropna().eq(1).all()
    assert result.value.isna().sum() == sum(
        amount == 0 for *_, amount in analysis_dsl_rows("j2").orders
    )
    pd.testing.assert_frame_equal(saved.to_pandas(), session.artifact(saved.id).to_pandas())


def test_float_status_fold_comparison_uses_native_values(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    _numeric_source(case, "DOUBLE", False)
    path = case.root / "models/semantic/sales/models.py"
    path.write_text(
        path.read_text().replace(
            "finalized = ms.finalize()",
            "status_amount = ms.measure_column(name='status_amount', entity=orders, column='amount', additivity=ms.additive_all(except_=(ordered_at,)), status_time_dimension=ordered_at, unit='CNY')\nfinalized = ms.finalize()",
            1,
        )
    )
    ms.load(workspace_dir=case.root)
    session = case.session
    observed = session.observe(
        {
            "value": session.catalog.fields.get("sales.order.status_amount")
            .sum(time=session.catalog.fields.get("sales.order.ordered_at"))
            .per(session.catalog.fields.get("sales.order.ordered_at"))
            .mean()
        },
        during=session.time_scope(start="2026-08-01", end="2026-08-03"),
        by=session.members(ms.ref.entity("sales.customer")),
    )
    comparison = observed.compare(observed)
    result = _plain(comparison.execute())
    assert result["value.absolute"].dropna().eq(0).all()
    assert result["value.relative"].dropna().eq(0).all()


def test_float_row_statistic_comparison_uses_stable_value_reduction(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute('ALTER TABLE "order" ALTER amount TYPE DOUBLE')
        connection.execute('DELETE FROM "order"')
        connection.executemany(
            'INSERT INTO "order" VALUES (?, ?, ?, ?, ?, ?)',
            [
                (f"order-{i}", customer, "web", "paid", "2026-08-10", amount)
                for i, (customer, amount) in enumerate(
                    zip("ABCD", [1e16, -1e16, 1.0, 1.0], strict=True)
                )
            ],
        )
    _numeric_source(case, "DOUBLE", True)
    observed = _monthly(case, 8)
    for name, statistic in (("sum", observed["value"].sum()), ("mean", observed["value"].mean())):
        reduced = observed.reduce({"value": statistic})
        value = _plain(reduced.execute()).value.iloc[0]
        assert value == (2.0 if name == "sum" else 0.5)
        compared = _plain(reduced.compare(reduced).execute())
        assert compared["value.absolute"].tolist() == [0.0]
        assert compared["value.relative"].tolist() == [0.0]


def test_stable_float_row_statistics_and_pandas_handoff(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    _numeric_source(case, "DOUBLE", True)
    observed = _monthly(case, 8, "order_count")
    positive = observed.where(observed["value"] > 0)
    floating = positive.select({"value": positive["value"] / positive["value"]})
    for statistic, expected in ((floating["value"].sum(), 4.0), (floating["value"].mean(), 1.0)):
        reduced = floating.reduce({"value": statistic})
        assert _plain(reduced.execute()).value.tolist() == [expected]
        result = reduced.select({"value": reduced["value"] / reduced["value"]}).execute()
        assert _plain(result).value.tolist() == [1.0]
    # Further statistics are an explicit pandas handoff from the same terminal values.
    values = _plain(floating.execute()).value
    assert values.min() == values.max() == 1.0


def test_period_change_after_bucket_rollup(analysis_dsl_case_factory: DslCaseFactory) -> None:
    case = analysis_dsl_case_factory("j2")
    _numeric_source(case, "BIGINT", True)
    session = case.session

    def grouped(month: int, day: int) -> mv.Query:
        axis = session.time_scope(
            start=f"2026-{month:02}-{day:02}", end=f"2026-{month:02}-{day + 3:02}"
        ).by(mv.grain("day"))
        observed = session.observe(
            {"value": session.catalog.metrics.get("sales.order_count")},
            during=axis,
            by=session.members(ms.ref.entity("sales.customer")),
        )
        return observed.rollup(by=(axis,))

    saved = grouped(8, 14).compare(grouped(7, 9), match=mv.ordinal()).execute()
    assert sorted(_plain(saved)["value.absolute"].tolist()) == [-1, 0, 0]
