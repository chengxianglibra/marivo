"""Bounded terminal display and explicit pandas ranking workflows."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization.graph_display import ranked
from tests.analysis.statistics.pandas_workflows import plain, receipt_paths
from tests.shared_fixtures import (
    DslCase,
    DslCaseFactory,
    analysis_dsl_rows,
    export_dsl_parquet_models,
)
from tests.support.paths import PROJECT_ROOT

Order = Literal["ascending", "descending"]
Ties = Literal["ordinal", "dense", "min", "max"]


def _values(case: DslCase, metric: str = "order_count", *, region: bool = False) -> mv.Query:
    session = case.session
    members = session.members(ms.ref.entity("sales.customer"))
    return session.observe(
        {"value": session.catalog.metrics.get("sales." + metric)},
        during=session.time_scope(start="2026-08-01", end="2026-09-01"),
        by=(mv.member(), session.catalog.fields.get("sales.customer.region"))
        if region
        else members,
        members=members if region else None,
    )


def _rank(
    frame: pd.DataFrame,
    order: Order = "descending",
    ties: Ties = "ordinal",
    *,
    partition_by: tuple[str, ...] = (),
) -> pd.DataFrame:
    """Rank represented values in pandas; complete keys break ordinal ties."""
    keys = [name for name in frame.columns if name.startswith("key_")]
    result = frame.sort_values(
        [*partition_by, "value", *keys],
        ascending=[*[True] * len(partition_by), order == "ascending", *[True] * len(keys)],
        na_position="last",
    ).copy()
    method: Literal["first", "dense", "min", "max"] = "first" if ties == "ordinal" else ties
    if partition_by:
        result["rank"] = result.groupby(list(partition_by), dropna=False)["value"].rank(
            method=method, ascending=order == "ascending"
        )
    else:
        result["rank"] = result["value"].rank(method=method, ascending=order == "ascending")
    return result.reset_index(drop=True)


def test_business_display_reads_saved_rows_and_preserves_state_summary(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import duckdb

    from marivo.analysis.materialization.graph_reading import (
        ExportRead,
        OutputRead,
        PreviewRead,
        ReadLayout,
    )
    from marivo.datasource.adapters import SourceSession

    case = analysis_dsl_case_factory("j2")
    with duckdb.connect(str(case.database_path)) as db:
        db.execute('UPDATE "order" SET channel = ?', ["retained" * 200])
    session = case.session
    members = session.members(ms.ref.entity("sales.order"))
    logical = session.read(members, {"value": session.catalog.fields.get("sales.order.amount")})
    values = logical.execute()
    ratio = logical.select({"value": logical["value"] / logical["value"]}).execute()
    table = logical.select({"amount": logical["value"]}).execute()
    category = session.read(
        members, {"value": session.catalog.fields.get("sales.order.channel")}
    ).execute()
    assert plain(ratio)["value"].isna().sum() == 4
    assert plain(category)["value"].tolist() == ["retained" * 200] * 11
    runs = len(session.runs().items)
    read = OutputRead.read
    decoded: list[int] = []

    def counted(
        self: OutputRead, layout: ReadLayout, request: PreviewRead | ExportRead
    ) -> pd.DataFrame:
        frame = read(self, layout, request)
        decoded.append(len(frame))
        return frame

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("show queried a source or called the complete pandas export")

    monkeypatch.setattr(OutputRead, "read", counted)
    monkeypatch.setattr(SourceSession, "__enter__", forbidden)
    monkeypatch.setattr(type(values), "to_pandas", forbidden)
    for result in (values, table):
        for n in (20, 2, 0):
            result.show(n=n)
            text = capsys.readouterr().out
            assert "rows: 11" in text and ".to_pandas()" in text
            assert decoded[-1] == min(n, 11)
            assert "required_parts" not in text and "cell_tag" not in text
            assert len(text.encode()) <= 8192
    category.show()
    text = capsys.readouterr().out
    assert len(text.encode()) <= 8192 and "retained" * 200 not in text
    ratio.show(n=0)
    assert "rows: 11" in capsys.readouterr().out
    assert len(session.runs().items) == runs


def test_business_display_identifies_current_and_baseline_windows(
    analysis_dsl_case_factory: DslCaseFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    case = analysis_dsl_case_factory("j2")
    session = case.session
    members = session.members(ms.ref.entity("sales.customer"))
    current_scope = session.time_scope(start="2026-08-01", end="2026-09-01")
    baseline_scope = session.time_scope(start="2026-07-01", end="2026-08-01")
    metric = session.catalog.metrics.get("sales.revenue")
    current = session.observe({"value": metric}, by=members, during=current_scope)
    baseline = session.observe({"value": metric}, by=members, during=baseline_scope)
    for query, start, end in (
        (current, "2026-08-01", "2026-09-01"),
        (baseline, "2026-07-01", "2026-08-01"),
    ):
        period = dict(query.contract()._facts)["period"]
        assert start in period and end in period and "UTC" in period
    output = current.compare(baseline).execute()
    assert {column.name for column in output._manifest.data.schema.values} == {
        "value.current",
        "value.baseline",
        "value.absolute",
        "value.relative",
    }
    assert {
        column.unit
        for column in output._manifest.data.schema.values
        if not column.name.endswith("relative")
    } == {"CNY"}
    output.show(n=0)
    assert "rows: 4" in capsys.readouterr().out


def test_exact_ties_and_nondefined_tail() -> None:
    rows: list[dict[str, object]] = [
        {"key_0": 2, "value": Decimal("2.000000"), "cell_tag": "defined"},
        {"key_0": 1, "value": Decimal("2.000000"), "cell_tag": "defined"},
        {"key_0": 3, "value": Decimal("1.999999"), "cell_tag": "defined"},
    ]
    rows.extend(
        {"key_0": key, "value": None, "cell_tag": tag}
        for key, tag in ((6, "null"), (4, "unknown"), (5, "undefined"))
    )
    for ties, expected in (
        ("ordinal", [1, 2, 3]),
        ("dense", [1, 1, 2]),
        ("min", [1, 1, 3]),
        ("max", [2, 2, 3]),
    ):
        result = ranked(rows, ("key_0",), "descending", ties, ())
        assert [result[(key,)][0] for key in (1, 2, 3)] == expected
        assert [result[(key,)][1] for key in (4, 5, 6)] == [4, 5, 6]
        assert ranked(list(reversed(rows)), ("key_0",), "descending", ties, ()) == result


@pytest.mark.parametrize(("order", "ties"), [("ascending", "ordinal"), ("descending", "dense")])
def test_public_rank_table_source_and_fixed(
    analysis_dsl_case_factory: DslCaseFactory, ties: Ties, order: Order
) -> None:
    case = analysis_dsl_case_factory("j2")
    query = _values(case, region=True)
    artifact = query.execute()
    facts = analysis_dsl_rows("j2")
    counts = {
        member: sum(
            buyer == member and str(day).startswith("2026-08")
            for _, buyer, _, _, day, _ in facts.orders
        )
        for member, *_ in facts.customers
    }
    ordered = sorted(
        counts, key=lambda key: ((-counts[key] if order == "descending" else counts[key]), key)
    )
    distinct = sorted(set(counts.values()), reverse=order == "descending")
    expected = {
        key: index + 1 if ties == "ordinal" else distinct.index(counts[key]) + 1
        for index, key in enumerate(ordered)
    }
    for receiver in (artifact, case.session.artifact(artifact.id)):
        frame = _rank(plain(receiver), order, ties)
        assert frame["key_0"].tolist() == ordered
        assert dict(zip(frame["key_0"], frame["rank"], strict=True)) == expected
        assert dict(zip(frame["key_0"], frame["value"], strict=True)) == counts
        assert frame.head(2)["key_0"].tolist() == ordered[:2]
        partitioned = _rank(plain(receiver), order, ties, partition_by=("key_1",))
        expected_partition = {
            key: 1 if ties == "dense" else i + 1
            for region in sorted({label for _, label in facts.customers if label is not None})
            for i, key in enumerate(key for key in ordered if (key, region) in facts.customers)
        }
        assert (
            dict(zip(partitioned["key_0"], partitioned["rank"], strict=True)) == expected_partition
        )


@pytest.mark.parametrize(
    "physical,parquet",
    [("BIGINT", True), ("DOUBLE", True), ("DECIMAL(30,6)", True), ("duration_ns", True)],
)
def test_display_precision_matrix(
    analysis_dsl_case_factory: DslCaseFactory,
    physical: str,
    parquet: bool,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import duckdb
    import pyarrow as pa
    import pyarrow.parquet as pq

    case = analysis_dsl_case_factory("j2")
    with duckdb.connect(str(case.database_path)) as db:
        if not physical.startswith("duration"):
            db.execute(f'ALTER TABLE "order" ALTER amount TYPE {physical}')
        if physical == "BIGINT":
            db.execute('UPDATE "order" SET amount = 9007199254740993 WHERE order_id = ?', ["j2_aa"])
    if parquet:
        export_dsl_parquet_models(case, case.root)
        if physical.startswith("duration"):
            path = case.root / "source_files/order.parquet"
            raw = pq.read_table(path)
            pq.write_table(
                raw.set_column(
                    raw.schema.get_field_index("amount"),
                    "amount",
                    raw["amount"].cast(pa.duration("ns")),
                ),
                path,
            )
    ms.load(workspace_dir=case.root)
    query = _values(case, "revenue")
    artifact = query.execute()
    raw_expected = {"A": 9007199254740993 if physical == "BIGINT" else 60, "B": 120, "C": 0, "D": 0}
    expected: dict[str, int | Decimal | pd.Timedelta] = dict(raw_expected)
    if physical.startswith("DECIMAL"):
        expected = {
            key: Decimal(value).quantize(Decimal("0.000001")) for key, value in raw_expected.items()
        }
    elif physical.startswith("duration"):
        expected = {key: pd.Timedelta(value, unit="ns") for key, value in raw_expected.items()}
    exported = plain(artifact)
    assert dict(zip(exported["key_0"], exported["value"], strict=True)) == expected
    assert artifact.to_pandas().equals(case.session.artifact(artifact.id).to_pandas())
    ranked_values = _rank(exported, ties="min")
    assert dict(zip(ranked_values["key_0"], ranked_values["value"], strict=True)) == expected
    artifact.show()
    display = capsys.readouterr().out
    assert "rows: 4" in display
    for value in expected.values():
        assert str(value) in display
    exported.iloc[0, 1] = None
    assert plain(artifact)["value"].isna().sum() == 0


def test_top_k_original_rank_share_and_empty(analysis_dsl_case_factory: DslCaseFactory) -> None:
    case = analysis_dsl_case_factory("j2")
    values = _values(case, region=True)
    shared = values.share("value")
    shares = shared.select({"value": shared["value.share"]})
    frame = _rank(plain(shares.execute()), ties="min", partition_by=("key_1",))
    assert frame.head(1)["value"].tolist() == [0.25]
    assert frame.loc[frame["rank"] <= 1, "key_0"].tolist() == ["A", "B", "C", "D"]
    assert frame["rank"].tolist() == [1, 1, 1, 1]
    continued = _rank(plain(values.execute()))
    assert continued.loc[continued["rank"] > 2, "rank"].tolist() == [3, 4]
    region = case.session.catalog.fields.get("sales.customer.region")
    top = shares.top(1, by=shares["value"], partition_by=(region,)).execute()
    assert plain(top)["key_0"].tolist() == ["A", "C", "D"]
    assert plain(top)["value"].tolist() == [0.25] * 3


def test_static_display_errors_are_zero_read(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.datasource.adapters import SourceSession

    case = analysis_dsl_case_factory("j2")
    values = _values(case)
    fixed = values.execute()
    other = analysis_dsl_case_factory("j2")
    foreign = _values(other)
    before = len(case.session.runs().items)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("static display admission read business data")

    monkeypatch.setattr(SourceSession, "execute", forbidden)
    for bad in (True, False, -1, 1.5, "1"):
        with pytest.raises(AnalysisError):
            values.top(bad, by=values["value"])  # type: ignore[arg-type]  # Deliberately invalid public n values.
    for bad in ("up", 1):
        with pytest.raises(AnalysisError):
            values.top(1, by=values["value"], descending=bad)  # type: ignore[arg-type]  # Deliberately invalid closed policy.
    for columns in (
        {},
        {"": values["value"]},
        {"a": fixed},
        {"a": values["value"], "b": foreign["value"]},
    ):
        with pytest.raises(AnalysisError):
            values.select(columns)  # type: ignore[arg-type]  # Deliberately invalid projection/mixed owner.
    with pytest.raises(AnalysisError):
        values.top(1, by=foreign["value"])
    with pytest.raises(AnalysisError):
        values.top(
            1,
            by=values["value"],
            partition_by=(case.session.catalog.fields.get("sales.customer.region"),),
        )
    assert len(case.session.runs().items) == before


@pytest.mark.slow
@pytest.mark.parametrize("parquet", [True])
def test_cold_display_continuations_and_corruption(
    analysis_dsl_case_factory: DslCaseFactory, parquet: bool
) -> None:
    import os
    import shutil
    import subprocess
    import sys

    from tests.analysis.materialization.domain_recovery_worker import snapshot
    from tests.support.json import encode

    case = analysis_dsl_case_factory("j4_ties")
    if parquet:
        export_dsl_parquet_models(case, case.root)
        ms.load(workspace_dir=case.root)
    values = _values(case)
    selected = values.top(2, by=values["value"]).execute()
    terminal = values.execute()
    assert plain(selected)["key_0"].tolist() == ["C", "D"]
    assert plain(terminal)["value"].tolist() == [1, 1, 3, 2]
    (case.root / "display.json").write_bytes(
        encode(
            {
                "session": case.session.id,
                "selected": {"artifact": selected.id, "snapshot": snapshot(selected)},
                "terminal": {"artifact": terminal.id, "snapshot": snapshot(terminal)},
            }
        )
    )
    (case.root / "source_files").rename(case.root / "source_files.offline")
    shutil.rmtree(case.root / "models")
    script = """
import json, sys
from pathlib import Path
import marivo.analysis as mv
import marivo.semantic as ms
from marivo.datasource.adapters import SourceSession
from tests.analysis.materialization.domain_recovery_worker import snapshot
from tests.analysis.statistics.pandas_workflows import plain

def forbidden(*args, **kwargs):
    raise AssertionError('cold display accessed source or Semantic')
ms.load = forbidden
SourceSession.__enter__ = forbidden
SourceSession.execute = forbidden
state = json.loads(Path('display.json').read_text())
session = mv.session.resume(state['session'], by='id')
before = len(session.runs().items)
for name in ('selected', 'terminal'):
    saved = state[name]
    result = session.artifact(saved['artifact'])
    assert snapshot(result) == saved['snapshot']
    result.show()
selected = plain(session.artifact(state['selected']['artifact']))
assert selected['key_0'].tolist() == ['C','D']
assert selected['value'].tolist() == [3,2]
selected['rank'] = selected['value'].rank(method='dense', ascending=False)
assert selected['rank'].tolist() == [1,2]
assert len(session.runs().items) == before
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=case.root,
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    for index, path in enumerate(receipt_paths(case.root, selected)):
        contents = path.read_bytes()
        try:
            path.write_bytes(b"invalid display output")
            with pytest.raises(AnalysisError):
                selected.show(n=1)
            if index == 0:
                with pytest.raises(AnalysisError):
                    selected.to_pandas()
            else:
                assert plain(selected)["value"].tolist() == [3, 2]
        finally:
            path.write_bytes(contents)


def test_shared_rank_fresh_source_fixed_and_no_fallback(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    import duckdb

    from marivo.datasource.adapters import CompiledRead, SourceSession

    case = analysis_dsl_case_factory("j2")
    values = _values(case)
    reads: list[str] = []
    execute = SourceSession.execute

    def counted(owner: SourceSession, read: CompiledRead) -> pd.DataFrame:
        reads.append(read.sql)
        return execute(owner, read)

    monkeypatch.setattr(SourceSession, "execute", counted)
    captured = values.execute()
    before = len(reads)
    frame = _rank(plain(captured), ties="dense")
    table = frame[["key_0", "value", "rank"]]
    assert len(reads) == before and table["key_0"].tolist()[0] == "A"
    with duckdb.connect(str(case.database_path)) as db:
        db.execute(
            'INSERT INTO "order" VALUES (?, ?, ?, ?, ?, ?)',
            ["extra", "B", "web", "paid", "2026-08-15", 1],
        )
    assert _rank(plain(values.execute()), ties="dense")["key_0"].tolist()[0] == "B"
    assert _rank(plain(captured), ties="dense").head(1)["key_0"].tolist() == ["A"]
    case.database_path.rename(case.database_path.with_suffix(".offline"))
    with pytest.raises(AnalysisError):
        values.execute()
    assert _rank(plain(captured), ties="dense").head(1)["key_0"].tolist() == ["A"]


def test_complete_composite_keys_scalar_columns_and_nulls(
    analysis_dsl_case_factory: DslCaseFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    from datetime import date

    import duckdb
    import ibis

    case = analysis_dsl_case_factory("j2")
    with duckdb.connect(str(case.database_path)) as db:
        db.execute(
            "CREATE TABLE r65 (tenant BIGINT, id VARCHAR, amount BIGINT, region VARCHAR, enabled BOOLEAN, moment TIMESTAMPTZ)"
        )
        db.execute(
            "INSERT INTO r65 VALUES (2,'A',NULL,'west',true,'2026-08-01 00:00:00+00'), (1,'B',9007199254740993,'east',false,'2026-08-02 00:00:00+00'), (1,'A',9007199254740993,'east',true,'2026-08-03 00:00:00+00'), (2,'B',9007199254740994,NULL,NULL,NULL)"
        )
        db.execute("ALTER TABLE r65 ADD COLUMN day DATE")
        db.execute("UPDATE r65 SET day = CAST(moment AS DATE)")
    model = case.root / "models/semantic/sales/models.py"
    additions = (
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        "display = ms.entity(name='display', datasource=ms.ref.datasource('warehouse'), source=md.table('r65'), primary_key=['tenant','id'])\n"
        "display_amount = ms.measure_column(name='amount', entity=display, column='amount', additivity=ms.additive_all())\n"
        "display_region = ms.dimension_column(name='region', entity=display, column='region')\n"
        "display_enabled = ms.dimension_column(name='enabled', entity=display, column='enabled')\n"
        "display_moment = ms.time_dimension_column(name='moment', entity=display, column='moment', granularity='second', parse=ms.timestamp(timezone='UTC'))\n"
        "display_day = ms.time_dimension_column(name='day', entity=display, column='day', granularity='day')\n"
    )
    model.write_text(
        model.read_text().replace(
            "finalized = ms.finalize()", additions + "\nfinalized = ms.finalize()"
        )
    )
    ms.load(workspace_dir=case.root)
    session = case.session
    query = session.read(
        session.members(ms.ref.entity("sales.display")),
        {
            name: session.catalog.fields.get("sales.display." + name)
            for name in ("moment", "day", "region", "amount", "enabled")
        },
    )
    terminal = query.execute()
    exported = plain(terminal)
    assert exported.columns.tolist() == ["key_0", "moment", "day", "region", "amount", "enabled"]
    assert exported["key_0"].tolist() == [(1, "A"), (1, "B"), (2, "A"), (2, "B")]
    assert exported["day"].tolist()[:3] == [date(2026, 8, 3), date(2026, 8, 2), date(2026, 8, 1)]
    assert exported["day"].isna().tolist() == [False, False, False, True]
    backend = ibis.duckdb.connect(str(case.database_path))
    try:
        native = backend.execute(backend.table("r65").order_by("tenant", "id"))
    finally:
        backend.disconnect()
    assert exported["amount"].dropna().tolist() == native.amount.dropna().tolist()
    assert str(exported["amount"].dtype) == "double[pyarrow]"
    assert exported["amount"].isna().tolist() == [False, False, True, False]
    assert terminal.to_pandas().equals(session.artifact(terminal.id).to_pandas())
    terminal.show()
    assert "<NA>" in capsys.readouterr().out
    numeric = exported.rename(columns={"amount": "value"})
    ranking = _rank(numeric, partition_by=("region",))
    assert ranking["key_0"].tolist() == [(1, "A"), (1, "B"), (2, "A"), (2, "B")]
    # A missing classification remains a real pandas NA group; its represented
    # numeric value is rankable without losing the complete composite identity.
    assert ranking.loc[ranking["key_0"] == (2, "B"), "rank"].tolist() == [1]
    defined = ranking.loc[ranking["rank"].notna()]
    assert len(defined.loc[defined["rank"] <= 1]) == 2
    shorter = query.where(~query["region"].is_null())
    with pytest.raises(AnalysisError):
        query.align(shorter).execute()
    with duckdb.connect(str(case.database_path)) as db:
        db.execute("INSERT INTO r65 SELECT * FROM r65 WHERE tenant=1 AND id='A'")
    with pytest.raises(AnalysisError) as caught:
        query.execute()
    assert caught.value.expected == "exactly one attribute row for every requested identity"


def test_represented_float_order_has_no_epsilon_ties() -> None:
    rows = [
        {"key_0": key, "value": value, "cell_tag": "defined"}
        for key, value in ((1, 1.0), (2, 1.0 + 2**-52), (3, -0.0), (4, 0.0))
    ]
    result = ranked(rows, ("key_0",), "ascending", "dense", ())
    assert [result[(key,)][0] for key in (1, 2, 3, 4)] == [2, 3, 1, 1]


def test_empty_original_domain_and_numeric_view_continuations(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    for scenario in ("empty_domain", "j4_ties"):
        case = analysis_dsl_case_factory(scenario)
        values = _values(case)
        artifact = values.execute()
        ranking = _rank(plain(artifact))
        selected = ranking.loc[ranking["rank"] > 1]
        if scenario == "empty_domain":
            assert ranking.empty and selected.empty and ranking.head(1).empty
            assert values.top(1, by=values["value"]).execute().to_pandas().empty
        else:
            assert ranking["value"].sum() == 7
            assert ranking.loc[ranking["value"] > 1, "value"].sum() == 5
            assert values.rollup().execute().to_pandas()[("value", "value")].tolist() == [7]
            chosen = values.where(values["value"] > 1)
            assert chosen.rollup().execute().to_pandas()[("value", "value")].tolist() == [5]


def test_empty_singleton_display_views(analysis_dsl_case_factory: DslCaseFactory) -> None:
    case = analysis_dsl_case_factory("j2")
    total = _values(case).rollup()
    empty = total.where(total["value"] > 99)
    assert empty.top(1, by=empty["value"]).execute().to_pandas().empty
    frame = plain(empty.execute())
    assert frame.empty and _rank(frame).empty


def test_group_and_entity_time_display_domains(analysis_dsl_case_factory: DslCaseFactory) -> None:
    case = analysis_dsl_case_factory("j2")
    session = case.session
    region = session.catalog.fields.get("sales.customer.region")
    groups = _values(case, region=True).rollup(by=(region,))
    group_values = plain(groups.execute())
    ranking = _rank(group_values, ties="dense")
    assert ranking["rank"].tolist() == [1, 2, 2]
    assert group_values["value"].tolist() == [2, 1, 1]
    period = session.time_scope(start="2026-07-01", end="2026-10-01").by(mv.grain("month"))
    members = session.members(ms.ref.entity("sales.customer"))
    values = session.observe(
        {"value": session.catalog.metrics.get("sales.order_count")},
        during=period,
        by=(mv.member(), region),
        members=members,
    )
    frame = plain(values.execute())
    axes = values._graph.signature.axes
    member_position = next(
        i
        for i, axis in enumerate(axes)
        if getattr(axis, "field", None) is None
        and getattr(axis, "kind", None) != "calendar"
        and hasattr(axis, "entity")
    )
    frame = frame.rename(columns={f"key_{member_position}": "member"})
    frame = _rank(frame)
    facts = analysis_dsl_rows("j2")
    expected = {
        member: [
            sum(
                buyer == member and str(day).startswith(month)
                for _, buyer, _, _, day, _ in facts.orders
            )
            for month in ("2026-07", "2026-08", "2026-09")
        ]
        for member, _ in facts.customers
    }
    assert len(frame) == 12
    assert {
        member: sorted(frame.loc[frame["member"] == member, "value"].tolist())
        for member in expected
    } == {member: sorted(values) for member, values in expected.items()}
    assert frame.shape == (12, 5)


def test_public_undefined_ranks_and_strict_two_step_filter(
    analysis_dsl_case_factory: DslCaseFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    case = analysis_dsl_case_factory("j2")
    values = _values(case, "revenue")
    ratio = values.select({"value": values["value"] / values["value"]})
    terminal = ratio.execute()
    frame = _rank(plain(terminal), ties="dense")
    assert frame["rank"].isna().tolist() == [False, False, True, True]
    defined = frame.loc[frame["rank"].notna()]
    assert defined.loc[defined["rank"] <= 1, "rank"].tolist() == [1, 1]
    # Nullable predicates follow the current null=false policy; composing the
    # same two filters in one predicate is now legal.
    filtered = ratio.where((~ratio["value"].is_null()) & (ratio["value"] <= 1)).execute()
    assert plain(filtered)["key_0"].tolist() == ["A", "B"]
    terminal.show()
    text = capsys.readouterr().out
    assert "<NA>" in text and "1.0" in text
    assert _rank(plain(case.session.artifact(terminal.id)), ties="dense").equals(frame)


def test_ranking_views_can_be_ranked_again(analysis_dsl_case_factory: DslCaseFactory) -> None:
    case = analysis_dsl_case_factory("j2")
    export_dsl_parquet_models(case, case.root)
    ms.load(workspace_dir=case.root)
    terminal = _values(case).execute()
    frame = _rank(plain(terminal))
    facts = analysis_dsl_rows("j2")
    counts = {
        member: sum(
            buyer == member and str(day).startswith("2026-08")
            for _, buyer, _, _, day, _ in facts.orders
        )
        for member, *_ in facts.customers
    }
    ranked_scores = {
        key: index + 1
        for index, key in enumerate(sorted(counts, key=lambda key: (-counts[key], key)))
    }
    for view in ("value", "rank"):
        scores = counts if view == "value" else ranked_scores
        ordered = sorted(scores, key=lambda key: (scores[key], key))
        numeric = frame[["key_0", view]].rename(columns={view: "value"})
        result = _rank(numeric, order="ascending")
        assert result["key_0"].tolist() == ordered
        assert result["rank"].tolist() == list(range(1, len(ordered) + 1))
        assert result["value"].tolist() == [scores[key] for key in ordered]
