"""Source Frame comparisons with raw-fact oracles and stored cold reads."""

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
from marivo.analysis.core.frame_data import MemoryFrameBacking, StoredFrameBacking
from marivo.analysis.errors import AnalysisError
from tests.analysis.numeric.pandas_coordinates import plain
from tests.analysis.numeric.test_period_alignment import comparison_tables
from tests.shared_fixtures import (
    DslCase,
    DslCaseFactory,
    analysis_dsl_rows,
    export_dsl_parquet_models,
)
from tests.support.paths import PROJECT_ROOT


def _plain(result: mv.AnalysisFrame) -> pd.DataFrame:
    return plain(result)


def _result_id(frame: mv.AnalysisFrame) -> str:
    assert isinstance(frame._backing, (MemoryFrameBacking, StoredFrameBacking))
    return frame._backing.metadata.result_id


def _monthly(
    case: DslCase, month: int, metric: str = "revenue", *, overall: bool = False
) -> mv.AnalysisFrame:
    session = case.session
    scope = session.time_scope(start=f"2026-{month:02}-01", end=f"2026-{month + 1:02}-01")
    return session.observe(
        {"value": session.catalog.metrics.get("sales." + metric)},
        by=(scope,)
        if overall
        else mv.product(scope, session.members(session.catalog.entities.get("sales.customer"))),
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


def _cold(case: DslCase, results: tuple[mv.AnalysisFrame, ...], assertions: str) -> None:
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
    session = mv.session.resume(sys.argv[1])
    before = run_ids(session)
    saved = tuple(session.persisted_frame(ref) for ref in sys.argv[2:])
    assert all(isinstance(item, mv.AnalysisFrame) for item in saved)
    assert all(not hasattr(item, name) for item in saved for name in ('rollup', 'contract', '_node', '_dataset'))
"""
        + "".join("    " + line + "\n" for line in assertions.splitlines())
        + "    assert run_ids(session) == before\n"
    )
    try:
        process = subprocess.run(
            [
                sys.executable,
                "-c",
                script,
                case.session.id,
                *(result.artifact.id for result in results),
            ],
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
    comparison = current.compare(
        {"reference": baseline},
        on={"reference": tuple(zip(current.axes, baseline.axes, strict=True))},
        match={"reference": mv.ordinal()},
    )
    saved = comparison.persist()
    source = _plain(saved).set_index("key_0")
    recovered = _plain(case.session.persisted_frame(saved.artifact.id)).set_index("key_0")
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
        absolute = frame["reference.absolute.value"]
        if physical.startswith("duration"):
            absolute = pd.Series(
                pa.array(absolute).cast(pa.duration("us")).cast(pa.int64()).to_pylist(),
                index=absolute.index,
            )
        assert absolute.to_dict() == expected
        assert frame[["presence.current.present", "presence.reference.present"]].all().all()
        for member in frame.index:
            denominator = totals[member, "2026-07"]
            if denominator == 0:
                assert pd.isna(frame.loc[member, "reference.relative.value"])
            else:
                assert frame.loc[member, "reference.relative.value"] == float(
                    expected[member] / abs(denominator)
                )
    if physical.startswith("DECIMAL"):
        assert all(type(value) is Decimal for value in recovered["reference.absolute.value"])
    if physical.startswith("duration"):
        session = case.session
        amount = session.catalog.fields.get("sales.order.amount")
        time = session.catalog.fields.get("sales.order.ordered_at")
        nullable = session.observe(
            {"value": amount.sum(time=time)},
            by=mv.product(
                session.time_scope(start="2026-09-01", end="2026-10-01"),
                session.members(session.catalog.entities.get("sales.customer")),
            ),
        )
        # A missing Duration expression remains NULL, unlike an empty sum.
        nullable = nullable.select({"value": nullable["value"] / 0})
        null_saved = nullable.compare(
            {"reference": current},
            on={"reference": tuple(zip(nullable.axes, current.axes, strict=True))},
            match={"reference": mv.ordinal()},
        ).persist()
        nulls = _plain(null_saved).set_index("key_0")
        assert pd.isna(nulls.loc["D", "value"])
        assert pd.isna(nulls.loc["D", "reference.absolute.value"])
        assert pd.isna(nulls.loc["D", "reference.relative.value"])
        assert (
            nulls.loc["D", "presence.current.present"]
            and nulls.loc["D", "presence.reference.present"]
        )
        assert pa.array(nulls["reference.absolute.value"]).type == pa.duration("us")
        pd.testing.assert_frame_equal(
            null_saved.to_pandas(), session.persisted_frame(null_saved.artifact.id).to_pandas()
        )
    # Both saved endpoints are ordinary data; their independent pandas comparison agrees.
    first, second = _plain(current.compute()), _plain(baseline.compute())
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
    comparison = current.compare(
        {"reference": baseline},
        on={"reference": tuple(zip(current.axes, baseline.axes, strict=True))},
        match={"reference": mv.ordinal()},
    )
    selected = comparison.where(~comparison["reference.relative.value"].is_null())
    saved, filtered = comparison.persist(), selected.persist()
    backing = saved._backing
    assert isinstance(backing, StoredFrameBacking)
    data = backing.data
    assert len(data.schema.presence) == 2
    with pytest.raises(AnalysisError):
        replace(data.schema, presence=(replace(data.schema.presence[0], nullable=True),))
    with pytest.raises(AnalysisError):
        replace(
            data.schema,
            values=(replace(data.schema.values[0], name="forged"), *data.schema.values[1:]),
        )
    _cold(
        case,
        (saved, filtered),
        """frame = _plain(saved[0]).set_index('key_0')
assert frame.loc['D', 'value'] == 0
assert frame.loc['D', 'reference.absolute.value'] == 0
assert pd.isna(frame.loc['D', 'reference.relative.value'])
assert frame[['presence.current.present', 'presence.reference.present']].all().all()
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
    first = august.compare(
        {"reference": july_first},
        on={"reference": tuple(zip(august.axes, july_first.axes, strict=True))},
        match={"reference": mv.ordinal()},
    )
    first = first.reduce({"value": first["reference.absolute.value"].sum()}, by=(first.axes[1],))
    second = july_second.compare(
        {"reference": june},
        on={"reference": tuple(zip(july_second.axes, june.axes, strict=True))},
        match={"reference": mv.ordinal()},
    )
    second = second.reduce(
        {"value": second["reference.absolute.value"].sum()}, by=(second.axes[1],)
    )
    nested = first.compare(
        {"reference": second}, on={"reference": tuple(zip(first.axes, second.axes, strict=True))}
    )
    saved = nested.persist()
    expected = {"A": -140, "B": -80, "C": -100, "D": 0}
    assert _plain(saved).set_index("key_0")["reference.absolute.value"].to_dict() == expected
    # Each endpoint is reevaluated from its Query, with no retained result graph.
    assert _result_id(saved) != _result_id(nested.compute())
    _cold(
        case,
        (saved,),
        """frame = _plain(saved[0]).set_index('key_0')
assert frame['reference.absolute.value'].to_dict() == {'A': -140, 'B': -80, 'C': -100, 'D': 0}
assert frame[['presence.current.present', 'presence.reference.present']].all().all()""",
    )


def test_ordinary_ratio_and_cohort_singleton(analysis_dsl_case_factory: DslCaseFactory) -> None:
    case = analysis_dsl_case_factory("j2")
    _numeric_source(case, "BIGINT", True)
    current = _monthly(case, 8)
    ratio = current.select({"value": current["value"] / current["value"]})
    saved = ratio.compute()
    frame = _plain(saved).set_index("key_0")
    assert frame.loc[["A", "B"], "value"].tolist() == [1.0, 1.0]
    assert frame.loc[["C", "D"], "value"].isna().all()
    assert not hasattr(saved, "rollup") and not hasattr(saved, "contract")
    first, second = _monthly(case, 8, overall=True), _monthly(case, 8, overall=True)
    assert _plain(
        first.compare(
            {"reference": second},
            on={"reference": tuple(zip(first.axes, second.axes, strict=True))},
        ).compute()
    )["reference.absolute.value"].tolist() == [0]


def test_union_keep_distinguishes_missing_coordinate_and_rejects_filtered_empty(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    _numeric_source(case, "BIGINT", True)
    current, baseline = _monthly(case, 8), _monthly(case, 7)
    left, right = current.where(current["value"] > 0), baseline.where(baseline["value"] > 0)
    saved = left.compare(
        {"reference": right},
        on={"reference": tuple(zip(left.axes, right.axes, strict=True))},
        match={"reference": mv.ordinal()},
    ).persist()
    frame = _plain(saved).set_index("key_0")
    assert frame.loc["A", "reference.absolute.value"] == -40
    assert frame.loc["B", "reference.absolute.value"] == 20
    assert pd.isna(frame.loc["C", "reference.absolute.value"])
    assert not frame.loc["C", "presence.current.present"]
    assert frame.loc["C", "presence.reference.present"]
    pd.testing.assert_frame_equal(
        saved.to_pandas(), case.session.persisted_frame(saved.artifact.id).to_pandas()
    )


def test_cohort_group_metric_empty_preserves_metric_null_policy(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    _numeric_source(case, "BIGINT", True)
    session = case.session
    members = session.members(session.catalog.entities.get("sales.customer"))
    region = session.catalog.fields.get("sales.customer.region")
    categories = session.read(members, {"region": region})
    selection_values = categories.where(categories["region"] == "east")
    eastern = session.population_from(
        selection_values,
        on=((selection_values.axes[0], session.catalog.entities.get("sales.customer")),),
    )
    amount, time = (
        session.catalog.fields.get("sales.order." + name) for name in ("amount", "ordered_at")
    )
    metrics = {
        "revenue": amount.sum(time=time),
        "order_count": session.catalog.metrics.get("sales.order_count"),
    }
    metrics["count_ratio"] = metrics["order_count"] / metrics["order_count"]
    for name, metric in metrics.items():

        def observe(population: mv.Population, metric: Deferred = metric) -> mv.AnalysisFrame:
            return session.observe(
                {"value": metric},
                by=mv.product(
                    session.time_scope(start="2026-07-01", end="2026-08-01"),
                    population.classify(by=(region,)),
                ),
                where=mv.in_population(population),
            )

        first, second = observe(members), observe(eastern)
        result = _plain(
            first.compare(
                {"reference": second},
                on={"reference": tuple(zip(first.axes, second.axes, strict=True))},
            ).compute()
        ).set_index("key_0")
        assert result.index.tolist() == ["east", "south", "west"]
        assert result.loc["east", "reference.absolute.value"] == 0
        assert result.loc[["south", "west"], "reference.absolute.value"].isna().all()
        assert not result.loc[["south", "west"], "presence.reference.present"].any()
        if name == "count_ratio":
            assert result["value"].tolist() == [1.0, 1.0, 1.0]
        elif name == "order_count":
            assert result.loc[["south", "west"], "value"].tolist() == [1, 1]


def test_period_change_retains_original_buckets_and_rejects_renumbering(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    _numeric_source(case, "BIGINT", True)
    session = case.session

    def observe(month: int, day: int, length: int) -> mv.AnalysisFrame:
        axis = session.time_scope(
            start=f"2026-{month:02}-{day:02}", end=f"2026-{month:02}-{day + length:02}"
        ).by(mv.grain("day"))
        return session.observe(
            {"value": session.catalog.metrics.get("sales.order_count")},
            by=mv.product(axis, session.members(session.catalog.entities.get("sales.customer"))),
        )

    current, baseline = observe(8, 14, 3), observe(7, 9, 3)
    shorter = observe(7, 9, 2)
    saved = current.compare(
        {"reference": baseline},
        match={"reference": mv.ordinal()},
        on={"reference": tuple(zip(current.axes, baseline.axes, strict=True))},
    ).compute()
    frame = _plain(saved)
    assert len(frame) == 12
    assert sorted(frame["reference.absolute.value"].tolist()) == [-1] + [0] * 11
    mapping, left_directory, right_directory = comparison_tables(saved, saved.to_pandas())
    assert set(left_directory["nominal_start"]) != set(right_directory["nominal_start"])
    selected = current.where(current["value"] > 0)
    outer = _plain(
        selected.compare(
            {"reference": baseline},
            match={"reference": mv.ordinal()},
            on={"reference": tuple(zip(selected.axes, baseline.axes, strict=True))},
        ).compute()
    )
    assert outer.time_0.nunique() == 3


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
        session.members(session.catalog.entities.get("sales." + name))
        for name in ("customer", "account")
    )
    scope = (
        session.time_scope(start="2026-07-09", end="2026-07-12")
        if calendar
        else session.time_scope(start="2026-07-01", end="2026-08-01")
    )
    during = scope.by(mv.grain("day")) if calendar else scope
    metric = session.catalog.metrics.get("sales.revenue")
    left = session.observe({"value": metric}, by=mv.product(during, customer))
    right = session.observe({"value": metric}, by=mv.product(during, account))
    # Relationship routing is explicit at the source; current data aligns only
    # the complete compatible keys deliberately supplied by this test.
    aligned = left.align(
        right, on=tuple(zip(left.axes, right.axes, strict=True)), match=mv.ordinal()
    )
    ratio = aligned.select({"value": aligned["left.value"] / aligned["right.value"]})
    result = _plain(ratio.compute())
    assert result.value.dropna().tolist() == [1.0, 1.0, 1.0]
    assert len(result) == (12 if calendar else 4)
    with pytest.raises(AnalysisError):
        left.align(right, on=())
    with pytest.raises(AnalysisError):
        left.align(right, on=((left.axes[0], left.axes[0]),))
    if calendar:
        assert result.time_0.nunique() == 3
        assert ratio.reduce({"count": mv.row_count()}).compute().to_pandas()[
            ("value", "count")
        ].tolist() == [12]


@pytest.mark.slow
def test_relative_and_ratio_cold_fixed_composition(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    _numeric_source(case, "BIGINT", True)
    current, baseline = _monthly(case, 8), _monthly(case, 7)
    current_total, baseline_total = _monthly(case, 8, overall=True), _monthly(case, 7, overall=True)
    totals = current_total.compare(
        {"reference": baseline_total},
        on={"reference": tuple(zip(current_total.axes, baseline_total.axes, strict=True))},
        match={"reference": mv.ordinal()},
    )
    positive = current.where(current["value"] > 0)
    ratio = positive.select({"value": positive["value"] / positive["value"]})
    individual = current.compare(
        {"reference": baseline},
        on={"reference": tuple(zip(current.axes, baseline.axes, strict=True))},
        match={"reference": mv.ordinal()},
    )
    _cold(
        case,
        (totals.persist(), ratio.persist(), individual.persist()),
        """assert _plain(saved[0])['reference.relative.value'].tolist() == [-0.28]
assert _plain(saved[1]).value.tolist() == [1.0, 1.0]
frame = _plain(saved[2]).set_index('key_0')
assert frame.loc['A', 'reference.relative.value'] == -0.4
assert frame.loc['B', 'reference.relative.value'] == 0.2
assert pd.isna(frame.loc['D', 'reference.relative.value'])""",
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
        session.members(session.catalog.entities.get("sales.customer")),
        {"id": session.catalog.fields.get("sales.customer.customer_id")},
    )
    selection_values = identities.where(identities["id"] == "A")
    members = session.population_from(
        selection_values,
        on=((selection_values.axes[0], session.catalog.entities.get("sales.customer")),),
    )
    amount, time = (
        session.catalog.fields.get("sales.order." + name) for name in ("amount", "ordered_at")
    )
    for metric, denominator in (
        (amount.sum(time=time), 1.0 + (-1.0 + 1e-13)),
        (amount.mean(time=time), (1.0 + (-1.0 + 1e-13)) / 2),
    ):

        def observe(month: int, metric: Deferred = metric) -> mv.AnalysisFrame:
            return session.observe(
                {"value": metric},
                by=mv.product(
                    session.time_scope(start=f"2026-{month:02}-01", end=f"2026-{month + 1:02}-01"),
                    members,
                ),
            )

        left, right = observe(8), observe(7)
        frame = _plain(
            left.compare(
                {"reference": right},
                on={"reference": tuple(zip(left.axes, right.axes, strict=True))},
                match={"reference": mv.ordinal()},
            ).compute()
        )
        assert frame["reference.baseline.value"].tolist() == [denominator]
        assert frame["reference.relative.value"].tolist() == pytest.approx(
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
    comparison = observed.compare(
        {"reference": independent},
        on={"reference": tuple(zip(observed.axes, independent.axes, strict=True))},
        match={"reference": mv.ordinal()},
    )
    assert not trace.native_sql and not trace.owners
    first = ratio.compute()
    first_reads = len(trace.native_sql)
    assert first_reads > 0 and all(owner._closed for owner in trace.owners)
    assert sum('"amount"' in sql for sql in trace.native_sql) == 1
    second = ratio.compute()
    assert len(trace.native_sql) == first_reads * 2
    assert _result_id(first) != _result_id(second)
    pd.testing.assert_frame_equal(first.to_pandas(), second.to_pandas())
    assert _plain(comparison.compute())["reference.absolute.value"].tolist() == [-40, 20, -50, 0]


@pytest.mark.parametrize("kind", ["ratio", "linear", "weighted"])
def test_comparison_of_float_original_expression_operands(
    analysis_dsl_case_factory: DslCaseFactory, kind: str
) -> None:
    case = analysis_dsl_case_factory("j2")
    _numeric_source(case, "DOUBLE", False)
    session = case.session
    identities = session.read(
        session.members(session.catalog.entities.get("sales.customer")),
        {"id": session.catalog.fields.get("sales.customer.customer_id")},
    )
    selection_values = identities.where(identities["id"] == "A")
    members = session.population_from(
        selection_values,
        on=((selection_values.axes[0], session.catalog.entities.get("sales.customer")),),
    )
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

    def observe(month: int, metric: Deferred = metric) -> mv.AnalysisFrame:
        return session.observe(
            {"value": metric},
            where=mv.in_population(members),
            by=mv.product(
                session.time_scope(start=f"2026-{month:02}-01", end=f"2026-{month + 1:02}-01"),
                members,
            ),
        )

    left, right = observe(8), observe(7)
    result = left.compare(
        {"reference": right},
        on={"reference": tuple(zip(left.axes, right.axes, strict=True))},
        match={"reference": mv.ordinal()},
    ).persist()
    assert _plain(result)["reference.absolute.value"].tolist() == [
        {"ratio": 0.0, "linear": -80.0, "weighted": -40.0}[kind]
    ]
    pd.testing.assert_frame_equal(
        result.to_pandas(), session.persisted_frame(result.artifact.id).to_pandas()
    )


def test_union_keeps_present_nondefined_cell_separate_from_absence(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    session = case.session
    members = session.members(session.catalog.entities.get("sales.customer"))
    region = session.catalog.fields.get("sales.customer.region")
    categories = session.read(members, {"region": region})
    selection_values = categories.where(categories["region"] == "east")
    eastern = session.population_from(
        selection_values,
        on=((selection_values.axes[0], session.catalog.entities.get("sales.customer")),),
    )
    amount, time = (
        session.catalog.fields.get("sales.order." + name) for name in ("amount", "ordered_at")
    )
    for metric, empty_value in (
        (amount.sum(time=time), 0),
        (session.catalog.metrics.get("sales.order_count") / amount.sum(time=time), None),
    ):

        def observe(population: mv.Population, metric: Deferred = metric) -> mv.AnalysisFrame:
            return session.observe(
                {"value": metric},
                by=mv.product(
                    session.time_scope(start="2026-08-01", end="2026-09-01"),
                    population.classify(by=(region,)),
                ),
                where=mv.in_population(population),
            )

        left, right = observe(eastern), observe(members)
        result = left.compare(
            {"reference": right},
            on={"reference": tuple(zip(left.axes, right.axes, strict=True))},
            match={"reference": mv.ordinal()},
        ).persist()
        frame = _plain(result).set_index("key_0")
        assert frame.loc[["south", "west"], "reference.absolute.value"].isna().all()
        assert not frame.loc[["south", "west"], "presence.current.present"].any()
        assert frame.loc[["south", "west"], "presence.reference.present"].all()
        baseline_value = frame.loc["west", "reference.baseline.value"]
        assert pd.isna(baseline_value) if empty_value is None else baseline_value == empty_value
        pd.testing.assert_frame_equal(
            result.to_pandas(), session.persisted_frame(result.artifact.id).to_pandas()
        )


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
    members = session.members(session.catalog.entities.get("sales.customer"))
    time = session.catalog.fields.get("sales.order.ordered_at")
    denominator = session.catalog.fields.get("sales.order.denominator").sum(time=time)
    q = session.observe(
        {"numerator": session.catalog.metrics.get("sales.revenue"), "denominator": denominator},
        by=mv.product(session.time_scope(start="2026-08-01", end="2026-09-01"), members),
    )
    result = q.select({"value": q["numerator"] / q["denominator"]}).persist()
    values = _plain(result).set_index("key_0").value.to_dict()
    assert values == {
        member: float(Fraction(Decimal("0.000001") if member == "A" else Decimal("0.000003")) / 2)
        for member in "ABCD"
    }
    assert pa.array(result.to_pandas()[("value", "value")]).type == pa.float64()
    pd.testing.assert_frame_equal(
        result.to_pandas(), session.persisted_frame(result.artifact.id).to_pandas()
    )


def test_ordinary_ratio_over_untimed_numeric_read(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    session = case.session
    values = session.read(
        session.members(session.catalog.entities.get("sales.order")),
        {"value": session.catalog.fields.get("sales.order.amount")},
    )
    saved = values.select({"value": values["value"] / values["value"]}).persist()
    result = _plain(saved)
    assert result.value.notna().sum() > 0 and result.value.dropna().eq(1).all()
    assert result.value.isna().sum() == sum(
        amount == 0 for *_, amount in analysis_dsl_rows("j2").orders
    )
    pd.testing.assert_frame_equal(
        saved.to_pandas(), session.persisted_frame(saved.artifact.id).to_pandas()
    )


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
        by=mv.product(
            session.time_scope(start="2026-08-01", end="2026-08-03"),
            session.members(session.catalog.entities.get("sales.customer")),
        ),
    )
    comparison = observed.compare(
        {"reference": observed},
        on={"reference": tuple(zip(observed.axes, observed.axes, strict=True))},
        match={"reference": mv.ordinal()},
    )
    result = _plain(comparison.compute())
    assert result["reference.absolute.value"].dropna().eq(0).all()
    assert result["reference.relative.value"].dropna().eq(0).all()


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
        value = _plain(reduced.compute()).value.iloc[0]
        assert value == (2.0 if name == "sum" else 0.5)
        compared = _plain(
            reduced.compare(
                {"reference": reduced},
                on={"reference": tuple(zip(reduced.axes, reduced.axes, strict=True))},
            ).compute()
        )
        assert compared["reference.absolute.value"].tolist() == [0.0]
        assert compared["reference.relative.value"].tolist() == [0.0]


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
        assert _plain(reduced.compute()).value.tolist() == [expected]
        result = reduced.select({"value": reduced["value"] / reduced["value"]}).compute()
        assert _plain(result).value.tolist() == [1.0]
    # Further statistics are an explicit pandas handoff from the same terminal values.
    values = _plain(floating.compute()).value
    assert values.min() == values.max() == 1.0


def test_period_change_after_bucket_rollup(analysis_dsl_case_factory: DslCaseFactory) -> None:
    case = analysis_dsl_case_factory("j2")
    _numeric_source(case, "BIGINT", True)
    session = case.session

    def grouped(month: int, day: int) -> mv.AnalysisFrame:
        axis = session.time_scope(
            start=f"2026-{month:02}-{day:02}", end=f"2026-{month:02}-{day + 3:02}"
        ).by(mv.grain("day"))
        observed = session.observe(
            {"value": session.catalog.metrics.get("sales.order_count")},
            by=mv.product(axis, session.members(session.catalog.entities.get("sales.customer"))),
        )
        return session.observe(
            {"value": session.catalog.metrics.get("sales.order_count")},
            by=(axis,),
            where=mv.in_population(session.members(session.catalog.entities.get("sales.customer"))),
        )

    left, right = grouped(8, 14), grouped(7, 9)
    saved = left.compare({"reference": right}, match={"reference": mv.ordinal()}).compute()
    assert sorted(_plain(saved)["reference.absolute.value"].tolist()) == [-1, 0, 0]
