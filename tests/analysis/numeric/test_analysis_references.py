"""Independent reference kernels and Frame exports for explicit pandas recipes."""

from decimal import Decimal
from fractions import Fraction

import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.datasource.errors import DatasourceConnectionError
from tests.analysis.numeric.attribution_workflows import _number
from tests.analysis.numeric.pandas_coordinates import plain
from tests.shared_fixtures import (
    DslCase,
    DslCaseFactory,
    analysis_dsl_rows,
    export_dsl_parquet_models,
)
from tests.support.paths import PROJECT_ROOT


def _plain(result: mv.AnalysisFrame) -> pd.DataFrame:
    return plain(result)


def _proportions(frame: pd.DataFrame) -> pd.DataFrame:
    """Compute explicit represented-value proportions, with no contribution claim."""
    result = frame.copy(deep=True)
    numbers = [_number(value) for value in frame["value"]]
    denominator = sum((value for value in numbers if value is not None), Fraction())
    result["value.share"] = pd.Series(
        [
            pd.NA if value is None or not denominator else float(value / denominator)
            for value in numbers
        ],
        index=frame.index,
        dtype="Float64",
    )
    return result


def _monthly(case: DslCase, metric: str) -> mv.AnalysisFrame:
    session = case.session
    return session.observe(
        {"value": session.catalog.metrics.get("sales." + metric)},
        by=mv.product(
            session.time_scope(start="2026-08-01", end="2026-09-01"),
            session.members(session.catalog.entities.get("sales.customer")),
        ),
    )


def _grouped(case: DslCase, metric: str) -> mv.AnalysisFrame:
    session = case.session
    members = session.members(session.catalog.entities.get("sales.customer"))
    return session.observe(
        {"value": session.catalog.metrics.get("sales." + metric)},
        by=mv.product(
            session.time_scope(start="2026-08-01", end="2026-09-01"),
            members.classify(by=(session.catalog.fields.get("sales.customer.region"),)),
        ),
    )


def _cold(case: DslCase, results: tuple[mv.AnalysisFrame, ...], assertions: str) -> None:
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
from tests.analysis.numeric.test_analysis_references import _plain, _proportions
from tests.shared_fixtures import run_ids

def unavailable(*args, **kwargs):
    raise AssertionError('cold reference export reopened source or Semantic')
with patch.object(duckdb, 'connect', unavailable), patch.object(ms, 'load', unavailable), patch.object(SourceSession, '__init__', unavailable):
    session = mv.session.resume(sys.argv[1])
    before = run_ids(session)
    saved = tuple(session.persisted_frame(ref) for ref in sys.argv[2:])
    assert all(isinstance(item, mv.AnalysisFrame) for item in saved)
    assert all(not hasattr(item, name) for item in saved for name in ('standardize', 'rollup', 'contract', '_node', '_dataset'))
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
    saved = values.persist()
    frame = _proportions(_plain(saved)).set_index("key_0")
    assert frame["value.share"].to_dict() == expected_shares
    selected = frame.loc[frame["value.share"] > 1]
    assert selected.empty
    assert not hasattr(saved, "contract") and not hasattr(saved, "share")
    saved.show()
    assert "value" in capsys.readouterr().out
    # Penetration and standardization consume exported complete identities in pandas.
    selected_members = frame.index[frame.value > 0]
    assert len(selected_members.unique()) / len(frame.index.unique()) == float(
        Fraction(sum(value > 0 for value in counts.values()), len(counts))
    )
    groups = _grouped(case, "order_count")
    weighted = groups.compute()
    table = _proportions(_plain(weighted)).set_index("key_0")
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
    pd.testing.assert_frame_equal(
        saved.to_pandas(), case.session.persisted_frame(saved.artifact.id).to_pandas()
    )
    fixed_values = _plain(values.compute()).set_index("key_0")
    pandas_shares = fixed_values.value.astype("float64") / float(fixed_values.value.sum())
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
    shares = _proportions(_plain(values.compute())).set_index("key_0")
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
    saved = values.persist()
    pd.testing.assert_frame_equal(
        saved.to_pandas(), case.session.persisted_frame(saved.artifact.id).to_pandas()
    )
    if physical.startswith("duration"):
        assert pa.array(shares.value).type == pa.duration("ns")
        # Duration remains duration data; pandas uses numeric count weights explicitly.
        weights = _proportions(_plain(_grouped(case, "order_count").compute()))
        assert sum(map(Fraction, weights["value.share"])) == 1
        assert all(value >= 0 for value in weights["value.share"])


@pytest.mark.release
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
    members = session.members(session.catalog.entities.get("sales.customer"))
    region = session.catalog.fields.get("sales.customer.region")
    base = session.catalog.metrics.get("sales.revenue")
    groups = session.observe(
        {"sum": base, "ratio": base / base},
        by=mv.product(
            session.time_scope(start="2026-08-01", end="2026-09-01"), members.classify(by=(region,))
        ),
    )
    weights = _grouped(case, "revenue")
    saved_groups, saved_weights = groups.persist(), weights.persist()
    table = _plain(saved_groups).set_index("key_0")
    reference = _proportions(_plain(saved_weights)).set_index("key_0")
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
weights = _proportions(_plain(saved[1])).set_index('key_0')['value.share']
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
    members = session.members(session.catalog.entities.get("sales.customer"))
    region = session.catalog.fields.get("sales.customer.region")
    amount, time = (
        session.catalog.fields.get("sales.order." + name) for name in ("amount", "ordered_at")
    )
    groups = session.observe(
        {"count": session.catalog.metrics.get("sales.order_count"), "mean": amount.mean(time=time)},
        by=mv.product(
            session.time_scope(start="2026-08-01", end="2026-09-01"), members.classify(by=(region,))
        ),
    )
    saved = groups.persist()
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
    pd.testing.assert_frame_equal(
        saved.to_pandas(), session.persisted_frame(saved.artifact.id).to_pandas()
    )


@pytest.mark.release
@pytest.mark.slow
def test_standardized_cold_selection_preserves_reference(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    export_dsl_parquet_models(case, case.root)
    ms.load(workspace_dir=case.root)
    groups = _grouped(case, "order_count")
    selected = groups.where(groups["value"] > 1)
    saved, subset = groups.persist(), selected.persist()
    _cold(
        case,
        (saved, subset),
        """full = _proportions(_plain(saved[0])).set_index('key_0')
selected_keys = _plain(saved[1]).set_index('key_0').index
selected = full.loc[full.index.isin(selected_keys)]
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
    fixed = values.compute()
    other = analysis_dsl_case_factory("j2")
    foreign = _monthly(other, "order_count")
    before = run_ids(case.session)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("reference construction cannot read business rows")

    monkeypatch.setattr(SourceSession, "execute", forbidden)
    with pytest.raises(AnalysisError):
        values.select({"ratio": values["value"] / foreign["value"]})
    with pytest.raises(AnalysisError):
        values.select({"ratio": values["value"] / fixed["value"]})
    assert not hasattr(values, "share") and not hasattr(mv, "broadcast")
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
    members = session.members(session.catalog.entities.get("sales.customer"))
    read = session.read(members, {"region": session.catalog.fields.get("sales.customer.region")})
    saved = read.persist()
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
    pd.testing.assert_frame_equal(
        saved.to_pandas(), session.persisted_frame(saved.artifact.id).to_pandas()
    )


def test_zero_and_signed_share_support(analysis_dsl_case_factory: DslCaseFactory) -> None:
    import duckdb

    case = analysis_dsl_case_factory("j2")
    with duckdb.connect(str(case.database_path)) as db:
        db.execute('UPDATE "order" SET amount=0')
    values = _monthly(case, "revenue")
    assert _proportions(_plain(values.compute()))["value.share"].isna().all()
    with duckdb.connect(str(case.database_path)) as db:
        db.execute(
            "UPDATE \"order\" SET amount=CASE customer_id WHEN 'A' THEN 60 WHEN 'B' THEN 120 WHEN 'C' THEN -30 ELSE 0 END"
        )
    frame = _proportions(_plain(values.compute())).set_index("key_0")
    assert frame["value.share"].to_dict() == {"A": 0.4, "B": 0.8, "C": -0.2, "D": 0.0}
    limited = frame.loc[frame["value"] > 0]
    assert limited["value.share"].sum() == pytest.approx(1.2)
    # Selecting the input first creates a different, explicitly smaller denominator.
    positive = values.where(values["value"] > 0)
    assert _proportions(_plain(positive.compute()))["value.share"].sum() == 1.0


def test_shared_reference_source_reexecution_and_fixed_inputs(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    import duckdb

    from tests.support.source_trace import capture_source

    case = analysis_dsl_case_factory("j2")
    values = _monthly(case, "order_count")
    trace = capture_source(monkeypatch)
    ratio = values.select({"value": values["value"] / values["value"]})
    assert _plain(ratio.compute()).value.tolist() == [1.0] * 4
    assert trace.native_sql and all(owner._closed for owner in trace.owners)
    assert sum('"order_id"' in sql for sql in trace.native_sql) == 1
    saved = values.compute()
    fixed = _proportions(_plain(saved)).set_index("key_0")
    assert fixed["value.share"].tolist() == [0.25] * 4
    with duckdb.connect(str(case.database_path)) as db:
        db.execute(
            'INSERT INTO "order" VALUES (?, ?, ?, ?, ?, ?)',
            ["extra", "A", "web", "paid", "2026-08-15", 1],
        )
    current = _proportions(_plain(values.compute())).set_index("key_0")
    assert current["value.share"].to_dict() == {"A": 0.4, "B": 0.2, "C": 0.2, "D": 0.2}
    assert _proportions(_plain(saved))["value.share"].tolist() == [0.25] * 4
    offline = case.database_path.with_suffix(".offline")
    case.database_path.rename(offline)
    try:
        with pytest.raises(DatasourceConnectionError):
            values.compute()
        assert _proportions(_plain(saved))["value.share"].tolist() == [0.25] * 4
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
        exported = _proportions(_plain(_grouped(case, "revenue").compute())).set_index("key_0")
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
    full = values.persist()
    external = _plain(full).set_index("key_0")
    proportions = _proportions(external)
    selected = proportions.loc[proportions["value"] > 0]
    assert selected["value.share"].to_dict() == {
        "A": float(Fraction(1, 3)),
        "B": float(Fraction(2, 3)),
    }
    assert external.value.sum() == 180
    assert (
        external.value[external.value > 0].astype("float64") / float(external.value.sum())
    ).to_dict() == selected["value.share"].to_dict()
    pd.testing.assert_frame_equal(
        full.to_pandas(), case.session.persisted_frame(full.artifact.id).to_pandas()
    )


def test_reference_preparation_captures_independent_source_origins(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.support.source_trace import capture_source

    case = analysis_dsl_case_factory("j3")
    session = case.session
    members = session.members(session.catalog.entities.get("sales.customer"))
    region = session.catalog.fields.get("sales.customer.region")
    counts = session.catalog.metrics.get("sales.order_count")
    lines = session.catalog.metrics.get("sales.line_revenue")
    revenue = session.catalog.metrics.get("sales.revenue")
    q = session.observe(
        {"count": counts, "lines": lines, "average": lines / counts, "linear": revenue + lines},
        by=mv.product(
            session.time_scope(start="2026-08-01", end="2026-09-01"), members.classify(by=(region,))
        ),
    )
    trace = capture_source(monkeypatch)
    saved = q.persist()
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
    weights = table.lines.astype(object) / sum(table.lines)
    assert sum(
        Fraction(value) * Fraction(weight)
        for value, weight in zip(table["count"], weights, strict=True)
    ) == len(raw.orders)
    pd.testing.assert_frame_equal(
        saved.to_pandas(), session.persisted_frame(saved.artifact.id).to_pandas()
    )
