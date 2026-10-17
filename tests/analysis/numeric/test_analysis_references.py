"""Independent reference kernels and Query exports for explicit pandas recipes."""

from decimal import Decimal
from fractions import Fraction

import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
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
        else:
            names.append(f"key_{key}")
            key += 1
    frame.columns = names
    return frame


def _monthly(case: DslCase, metric: str) -> mv.Query:
    session = case.session
    return session.observe(
        {"value": session.catalog.metrics.get("sales." + metric)},
        during=session.time_scope(start="2026-08-01", end="2026-09-01"),
        by=session.members(ms.ref.entity("sales.customer")),
    )


def _grouped(case: DslCase, metric: str) -> mv.Query:
    session = case.session
    members = session.members(ms.ref.entity("sales.customer"))
    return session.observe(
        {"value": session.catalog.metrics.get("sales." + metric)},
        during=session.time_scope(start="2026-08-01", end="2026-09-01"),
        by=members.classify(by=(session.catalog.fields.get("sales.customer.region"),)),
    )


def _cold(case: DslCase, results: tuple[mv.Artifact, ...], assertions: str) -> None:
    import os
    import subprocess
    import sys

    source = case.root / "source_files"
    if not source.exists():
        source = case.database_path
    offline = source.with_name(source.name + ".offline")
    source.rename(offline)
    script = (
        """
import sys
from fractions import Fraction
from unittest.mock import patch
import duckdb
import marivo.analysis as mv
import marivo.semantic as ms
from marivo.datasource.adapters import SourceSession
from tests.analysis.numeric.test_analysis_references import _plain
from tests.shared_fixtures import run_ids

def unavailable(*args, **kwargs):
    raise AssertionError('cold reference export reopened source or Semantic')
with patch.object(duckdb, 'connect', unavailable), patch.object(ms, 'load', unavailable), patch.object(SourceSession, '__init__', unavailable):
    session = mv.session.resume(sys.argv[1], by='id')
    before = run_ids(session)
    saved = tuple(session.artifact(ref) for ref in sys.argv[2:])
    assert all(isinstance(item, mv.Artifact) for item in saved)
    assert all(not hasattr(item, name) for item in saved for name in ('standardize', 'where', 'rollup', 'contract', '_node', '_dataset'))
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


def test_public_references_source_and_fixed(
    analysis_dsl_case_factory: DslCaseFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    case = analysis_dsl_case_factory("j2")
    values = _monthly(case, "order_count")
    facts = analysis_dsl_rows("j2")
    counts = {
        member: sum(
            buyer == member and str(date).startswith("2026-08")
            for _, buyer, _, _, date, _ in facts.orders
        )
        for member, *_ in facts.customers
    }
    expected_shares = {
        key: float(Fraction(value, sum(counts.values()))) for key, value in counts.items()
    }
    share_query = values.share("value")
    saved = share_query.execute()
    frame = _plain(saved).set_index("key_0")
    assert frame["value.share"].to_dict() == expected_shares
    selected = share_query.where(share_query["value.share"] > 1).execute()
    assert selected.to_pandas().empty
    assert not hasattr(saved, "contract") and not hasattr(saved, "where")
    saved.show()
    assert "value.share" in capsys.readouterr().out
    # Penetration and standardization consume exported complete identities in pandas.
    selected_members = frame.index[frame.value > 0]
    assert len(selected_members.unique()) / len(frame.index.unique()) == float(
        Fraction(sum(value > 0 for value in counts.values()), len(counts))
    )
    groups = _grouped(case, "order_count")
    weighted = groups.share("value").execute()
    table = _plain(weighted).set_index("key_0")
    represented = sum(
        (
            Fraction(value) * Fraction(weight)
            for value, weight in zip(table["value"], table["value.share"], strict=True)
        ),
        Fraction(),
    )
    grouped_counts = dict.fromkeys(dict(facts.customers).values(), 0)
    for member, count in counts.items():
        grouped_counts[dict(facts.customers)[member]] += count
    expected = sum(
        (Fraction(value**2, sum(counts.values())) for value in grouped_counts.values()), Fraction()
    )
    assert float(represented) == float(expected)
    pd.testing.assert_frame_equal(saved.to_pandas(), case.session.artifact(saved.id).to_pandas())
    fixed_values = _plain(values.execute()).set_index("key_0")
    pandas_shares = fixed_values.value / fixed_values.value.sum()
    assert pandas_shares.to_dict() == expected_shares


@pytest.mark.parametrize("physical", ["BIGINT", "DECIMAL(30,6)", "duration_ns"])
def test_public_share_numeric_matrix(
    analysis_dsl_case_factory: DslCaseFactory, physical: str
) -> None:
    import duckdb
    import pyarrow as pa
    import pyarrow.parquet as pq

    case = analysis_dsl_case_factory("j2")
    with duckdb.connect(str(case.database_path)) as db:
        if not physical.startswith("duration"):
            db.execute(f'ALTER TABLE "order" ALTER amount TYPE {physical}')
    export_dsl_parquet_models(case, case.root)
    if physical.startswith("duration"):
        path = case.root / "source_files/order.parquet"
        table = pq.read_table(path)
        pq.write_table(
            table.set_column(
                table.schema.get_field_index("amount"),
                "amount",
                table["amount"].cast(pa.duration("ns")),
            ),
            path,
        )
    ms.load(workspace_dir=case.root)
    values = _monthly(case, "revenue")
    shares = _plain(values.share("value").execute()).set_index("key_0")
    raw = analysis_dsl_rows("j2")
    totals = {
        member: sum(
            amount
            for _, buyer, _, _, date, amount in raw.orders
            if buyer == member and str(date).startswith("2026-08")
        )
        for member, _ in raw.customers
    }
    expected = {
        member: Fraction(total) / Fraction(sum(totals.values())) for member, total in totals.items()
    }
    assert shares["value.share"].to_dict() == {key: float(value) for key, value in expected.items()}
    assert pa.array(shares["value.share"]).type == pa.float64()
    if physical.startswith("DECIMAL"):
        assert all(isinstance(value, Decimal) for value in shares.value)
        assert shares.value.to_dict() == {key: Decimal(total) for key, total in totals.items()}
    saved = values.share("value").execute()
    pd.testing.assert_frame_equal(saved.to_pandas(), case.session.artifact(saved.id).to_pandas())
    if physical.startswith("duration"):
        assert pa.array(shares.value).type == pa.duration("ns")
        # Duration remains duration data; pandas uses numeric count weights explicitly.
        weights = _plain(_grouped(case, "order_count").share("value").execute())
        assert sum(map(Fraction, weights["value.share"])) == 1
        assert all(value >= 0 for value in weights["value.share"])


@pytest.mark.slow
@pytest.mark.parametrize("physical", ["DOUBLE", "DECIMAL(30,6)"])
def test_original_metric_standardization_matrix(
    analysis_dsl_case_factory: DslCaseFactory, physical: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import duckdb

    from tests.support.source_trace import capture_source

    case = analysis_dsl_case_factory("j2")
    with duckdb.connect(str(case.database_path)) as db:
        db.execute(f'ALTER TABLE "order" ALTER amount TYPE {physical}')
    trace = capture_source(monkeypatch)
    session = case.session
    members = session.members(ms.ref.entity("sales.customer"))
    region = session.catalog.fields.get("sales.customer.region")
    base = session.catalog.metrics.get("sales.revenue")
    groups = session.observe(
        {"sum": base, "ratio": base / base},
        during=session.time_scope(start="2026-08-01", end="2026-09-01"),
        by=members.classify(by=(region,)),
    )
    weights = _grouped(case, "revenue").share("value")
    saved_groups, saved_weights = groups.execute(), weights.execute()
    table = _plain(saved_groups).set_index("key_0")
    reference = _plain(saved_weights).set_index("key_0")
    assert table.index.is_unique and reference.index.is_unique
    assert set(table.index) == set(reference.index)
    raw = analysis_dsl_rows("j2")
    totals = dict.fromkeys(dict(raw.customers).values(), Fraction())
    for _, buyer, _, _, date, amount in raw.orders:
        if str(date).startswith("2026-08"):
            totals[dict(raw.customers)[buyer]] += Fraction(amount)
    for kind in ("sum", "ratio"):
        positive = reference["value.share"] > 0
        values = table.loc[positive, kind]
        assert values.notna().all()
        actual = sum(
            (
                Fraction(value) * Fraction(weight)
                for value, weight in zip(
                    values, reference.loc[positive, "value.share"], strict=True
                )
            ),
            Fraction(),
        )
        expected = sum(
            (
                (total if kind == "sum" else Fraction(1)) * total / sum(totals.values())
                for total in totals.values()
                if total > 0
            ),
            Fraction(),
        )
        if physical.startswith("DECIMAL") and kind == "sum":
            assert (Decimal(actual.numerator) / Decimal(actual.denominator)).quantize(
                Decimal("0.000001")
            ) == (Decimal(expected.numerator) / Decimal(expected.denominator)).quantize(
                Decimal("0.000001")
            )
            assert all(isinstance(value, Decimal) for value in values)
        else:
            assert float(actual) == float(expected)
    assert trace.native_sql and trace.owners and all(owner._closed for owner in trace.owners)
    _cold(
        case,
        (saved_groups, saved_weights),
        """groups = _plain(saved[0]).set_index('key_0')
weights = _plain(saved[1]).set_index('key_0')['value.share']
positive = weights > 0
assert set(groups.index) == set(weights.index)
assert float(sum(Fraction(v) * Fraction(w) for v, w in zip(groups.loc[positive, 'sum'], weights[positive], strict=True))) == 180.0
assert float(sum(Fraction(v) * Fraction(w) for v, w in zip(groups.loc[positive, 'ratio'], weights[positive], strict=True))) == 1.0""",
    )


def test_public_exact_integer_reference_weights(analysis_dsl_case_factory: DslCaseFactory) -> None:
    import duckdb
    import pyarrow as pa

    case = analysis_dsl_case_factory("j2")
    kept = next(row for row in analysis_dsl_rows("j2").orders if str(row[4]).startswith("2026-08"))
    with duckdb.connect(str(case.database_path)) as db:
        db.execute('DELETE FROM "order" WHERE order_id != ?', [kept[0]])
    session = case.session
    members = session.members(ms.ref.entity("sales.customer"))
    region = session.catalog.fields.get("sales.customer.region")
    amount, time = (
        session.catalog.fields.get("sales.order." + name) for name in ("amount", "ordered_at")
    )
    groups = session.observe(
        {"count": session.catalog.metrics.get("sales.order_count"), "mean": amount.mean(time=time)},
        during=session.time_scope(start="2026-08-01", end="2026-09-01"),
        by=members.classify(by=(region,)),
    )
    saved = groups.execute()
    table = _plain(saved).set_index("key_0")
    assert pa.array(table["count"]).type == pa.int64()
    assert table["mean"].isna().sum() == 2
    weights = table["count"]
    assert weights.sum() == 1
    assert (
        sum(
            Fraction(value) * Fraction(weight)
            for value, weight in zip(
                table.loc[weights > 0, "count"], weights[weights > 0], strict=True
            )
        )
        == 1
    )
    assert sum(
        Fraction(value) * Fraction(weight)
        for value, weight in zip(table.loc[weights > 0, "mean"], weights[weights > 0], strict=True)
    ) == Fraction(kept[5])
    pd.testing.assert_frame_equal(saved.to_pandas(), session.artifact(saved.id).to_pandas())


@pytest.mark.slow
def test_standardized_cold_selection_preserves_reference(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    export_dsl_parquet_models(case, case.root)
    ms.load(workspace_dir=case.root)
    groups = _grouped(case, "order_count").share("value")
    selected = groups.where(groups["value"] > 1)
    saved, subset = groups.execute(), selected.execute()
    _cold(
        case,
        (saved, subset),
        """full = _plain(saved[0]).set_index('key_0')
selected = _plain(saved[1]).set_index('key_0')
assert full['value.share'].to_dict() == {'east': 0.5, 'south': 0.25, 'west': 0.25}
assert selected['value.share'].to_dict() == {'east': 0.5}
assert sum(Fraction(value) * Fraction(weight) for value, weight in zip(full.value, full['value.share'], strict=True)) == Fraction(3, 2)
assert sum(Fraction(value) * Fraction(weight) for value, weight in zip(selected.value, selected['value.share'], strict=True)) == 1""",
    )


def test_static_reference_rejections_are_zero_read(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.analysis.errors import AnalysisError
    from marivo.datasource.adapters import SourceSession
    from tests.shared_fixtures import run_ids

    case = analysis_dsl_case_factory("j2")
    values = _monthly(case, "order_count")
    total = values.rollup()
    fixed = values.execute()
    other = analysis_dsl_case_factory("j2")
    foreign = _monthly(other, "order_count").rollup()
    before = run_ids(case.session)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("reference construction cannot read business rows")

    monkeypatch.setattr(SourceSession, "execute", forbidden)
    values.share("value")
    values.select({"ratio": values["value"] / mv.broadcast(total["value"], to=values)})
    with pytest.raises(AnalysisError):
        mv.broadcast(foreign["value"], to=values)
    with pytest.raises(AnalysisError):
        mv.broadcast(fixed, to=values)  # type: ignore[arg-type]  # Probe the terminal boundary.
    ordinary = values.select({"value": values["value"] / values["value"]})
    with pytest.raises(AnalysisError):
        ordinary.share("value")
    with pytest.raises(AnalysisError):
        values.share("absent")
    assert run_ids(case.session) == before


def test_composite_penetration_overlap_and_empty(analysis_dsl_case_factory: DslCaseFactory) -> None:
    import duckdb

    case = analysis_dsl_case_factory("j2")
    with duckdb.connect(str(case.database_path)) as db:
        db.execute("ALTER TABLE customer ADD tenant BIGINT DEFAULT 7")
        db.execute("INSERT INTO customer VALUES ('A', 'north', 8)")
    model = case.root / "models/semantic/sales/models.py"
    model.write_text(
        model.read_text().replace(
            "primary_key=['customer_id']", "primary_key=['tenant', 'customer_id']"
        )
    )
    ms.load(workspace_dir=case.root)
    session = case.session
    members = session.members(ms.ref.entity("sales.customer"))
    read = session.read(members, {"region": session.catalog.fields.get("sales.customer.region")})
    saved = read.execute()
    table = _plain(saved).set_index("key_0")
    assert set(table.index) == {(7, "A"), (7, "B"), (7, "C"), (7, "D"), (8, "A")}
    first = table.index[table.region != "west"]
    second = table.index[table.region != "south"]
    assert len(first.unique()) / len(table.index.unique()) == float(Fraction(4, 5))
    assert len(second.unique()) / len(table.index.unique()) == float(Fraction(4, 5))
    assert len(first) + len(second) > len(table)
    empty = table.loc[table.region == "absent"]
    penetration = pd.NA if empty.empty else len(empty) / len(empty)
    assert pd.isna(penetration)
    pd.testing.assert_frame_equal(saved.to_pandas(), session.artifact(saved.id).to_pandas())


def test_zero_and_signed_share_support(analysis_dsl_case_factory: DslCaseFactory) -> None:
    import duckdb

    from marivo.analysis.errors import AnalysisError

    case = analysis_dsl_case_factory("j2")
    with duckdb.connect(str(case.database_path)) as db:
        db.execute('UPDATE "order" SET amount=0')
    values = _monthly(case, "revenue")
    shares = values.share("value")
    assert _plain(shares.execute())["value.share"].isna().all()
    with pytest.raises(AnalysisError):
        values.share("value", zero_denominator="error").execute()
    with duckdb.connect(str(case.database_path)) as db:
        db.execute(
            "UPDATE \"order\" SET amount=CASE customer_id WHEN 'A' THEN 60 WHEN 'B' THEN 120 WHEN 'C' THEN -30 ELSE 0 END"
        )
    frame = _plain(shares.execute()).set_index("key_0")
    assert frame["value.share"].to_dict() == {"A": 0.4, "B": 0.8, "C": -0.2, "D": 0.0}
    limited = shares.where(shares["value"] > 0)
    assert _plain(limited.execute())["value.share"].sum() == pytest.approx(1.2)
    # Selecting the input first creates a different, explicitly smaller denominator.
    positive = values.where(values["value"] > 0)
    assert _plain(positive.share("value").execute())["value.share"].sum() == 1.0


def test_shared_reference_source_reexecution_and_fixed_inputs(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    import duckdb

    from marivo.analysis.errors import AnalysisError
    from tests.support.source_trace import capture_source

    case = analysis_dsl_case_factory("j2")
    values = _monthly(case, "order_count")
    share = values.share("value")
    trace = capture_source(monkeypatch)
    ratio = share.select({"value": share["value.share"] / share["value.share"]})
    assert _plain(ratio.execute()).value.tolist() == [1.0] * 4
    assert trace.native_sql and all(owner._closed for owner in trace.owners)
    assert sum('"order_id"' in sql for sql in trace.native_sql) == 1
    saved = share.execute()
    fixed = _plain(saved).set_index("key_0")
    assert fixed["value.share"].tolist() == [0.25] * 4
    with duckdb.connect(str(case.database_path)) as db:
        db.execute(
            'INSERT INTO "order" VALUES (?, ?, ?, ?, ?, ?)',
            ["extra", "A", "web", "paid", "2026-08-15", 1],
        )
    current = _plain(share.execute()).set_index("key_0")
    assert current["value.share"].to_dict() == {"A": 0.4, "B": 0.2, "C": 0.2, "D": 0.2}
    assert _plain(saved)["value.share"].tolist() == [0.25] * 4
    offline = case.database_path.with_suffix(".offline")
    case.database_path.rename(offline)
    try:
        with pytest.raises(AnalysisError):
            share.execute()
        assert _plain(saved)["value.share"].tolist() == [0.25] * 4
    finally:
        offline.rename(case.database_path)


def test_invalid_public_standardization_rejects_without_normalization(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    import duckdb

    case = analysis_dsl_case_factory("j2")
    for missing in (True, False):
        if not missing:
            with duckdb.connect(str(case.database_path)) as db:
                db.execute('UPDATE "order" SET amount=-30 WHERE customer_id=?', ["C"])
        exported = _plain(_grouped(case, "revenue").share("value").execute()).set_index("key_0")
        weights = exported["value.share"]
        if missing:
            weights = weights[weights > 0]
            assert set(weights.index) != set(exported.index)
            assert len(exported.index.difference(weights.index)) == 2
        else:
            assert (weights < 0).any()
            assert weights.to_dict() == {"east": 1.2, "south": -0.2, "west": 0.0}
        # Standardization is explicitly external: invalid references are diagnosed,
        # never silently completed, dropped, or normalized into valid weights.
        assert set(weights.index) != set(exported.index) or (weights < 0).any()


def test_fixed_selected_original_can_use_retained_full_reference(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    values = _monthly(case, "revenue")
    shares = values.share("value")
    selected = shares.where(shares["value"] > 0)
    saved, full = selected.execute(), values.execute()
    assert _plain(saved).set_index("key_0")["value.share"].to_dict() == {
        "A": float(Fraction(1, 3)),
        "B": float(Fraction(2, 3)),
    }
    assert _plain(full).value.sum() == 180
    external = _plain(full).set_index("key_0")
    assert (external.value[external.value > 0] / external.value.sum()).to_dict() == _plain(
        saved
    ).set_index("key_0")["value.share"].to_dict()


def test_reference_preparation_captures_independent_source_origins(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.support.source_trace import capture_source

    case = analysis_dsl_case_factory("j3")
    session = case.session
    members = session.members(ms.ref.entity("sales.customer"))
    region = session.catalog.fields.get("sales.customer.region")
    counts = session.catalog.metrics.get("sales.order_count")
    lines = session.catalog.metrics.get("sales.line_revenue")
    revenue = session.catalog.metrics.get("sales.revenue")
    q = session.observe(
        {"count": counts, "lines": lines, "average": lines / counts, "linear": revenue + lines},
        during=session.time_scope(start="2026-08-01", end="2026-09-01"),
        by=members.classify(by=(region,)),
    )
    trace = capture_source(monkeypatch)
    saved = q.execute()
    table = _plain(saved).set_index("key_0")
    assert table.index.tolist() == ["east"]
    raw = analysis_dsl_rows("j3")
    assert table["count"].tolist() == [len(raw.orders)]
    assert table["lines"].tolist() == [sum(amount for _, _, amount in raw.lines)]
    assert table["average"].tolist() == [
        float(sum((Fraction(amount) for _, _, amount in raw.lines), Fraction()) / len(raw.orders))
    ]
    assert table["linear"].tolist() == [
        sum(amount for *_, amount in raw.orders) + sum(amount for _, _, amount in raw.lines)
    ]
    assert trace.native_sql and trace.owners and all(owner._closed for owner in trace.owners)
    # Line-derived weights remain an explicit pandas reference; no statistical
    # unit proof or hidden origin payload is inferred from matching output keys.
    weights = table.lines / table.lines.sum()
    assert sum(
        Fraction(value) * Fraction(weight)
        for value, weight in zip(table["count"], weights, strict=True)
    ) == len(raw.orders)
    pd.testing.assert_frame_equal(saved.to_pandas(), session.artifact(saved.id).to_pandas())
