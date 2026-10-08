"""Independent deterministic display and public source/fixed acceptance."""

from decimal import Decimal
from typing import Literal

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.materialization.graph_display import ranked
from tests.shared_fixtures import DslCaseFactory, analysis_dsl_rows, export_dsl_parquet_models
from tests.support.paths import PROJECT_ROOT


@pytest.mark.runtime
@pytest.mark.parametrize("parquet", (False, True))
def test_current_row_mean_of_integer_counts(
    analysis_dsl_case_factory: DslCaseFactory, parquet: bool
) -> None:
    case = analysis_dsl_case_factory("j2")
    if parquet:
        export_dsl_parquet_models(case, case.root)
        ms.load(workspace_dir=case.root)
    members = case.session.members(ms.ref.entity("sales.customer"))
    counts = members.observe(
        ms.ref.metric("sales.order_count"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.order_buyer"),
        by=(ms.ref.entity("sales.customer"),),
    )
    facts = analysis_dsl_rows("j2")
    expected = sum(str(order[4]).startswith("2026-08") for order in facts.orders) / len(
        facts.customers
    )
    mean = counts.summarize(mv.mean()).execute()
    assert mean.to_pandas().value.tolist() == [expected]


@pytest.mark.runtime
def test_business_display_reads_saved_rows_and_preserves_state_summary(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import duckdb
    import pandas as pd

    from marivo.datasource.adapters import SourceSession

    case = analysis_dsl_case_factory("j2")
    with duckdb.connect(str(case.database_path)) as db:
        db.execute('UPDATE "order" SET channel = ?', ["retained" * 200])
    members = case.session.members(ms.ref.entity("sales.order"))
    logical = members.read(ms.ref.measure("sales.order.amount"))
    values = logical.execute()
    ratio = logical.ratio(logical).execute()
    table = mv.table(amount=values).execute()
    category = members.read(ms.ref.dimension("sales.order.channel")).execute()
    runs = len(case.session.runs().items)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("show must not query sources or convert rows to pandas")

    monkeypatch.setattr(SourceSession, "__enter__", forbidden)
    monkeypatch.setattr(pd.DataFrame, "__init__", forbidden)
    for result in (values, table):
        result.show()
        text = capsys.readouterr().out
        assert "11 total; 11 shown" in text
        assert text.count("<identity>") == 11
        assert "j2_" not in text
        assert "required_parts" not in text and "call=" not in text
        assert "available:" not in text
        result.show(n=2)
        text = capsys.readouterr().out
        assert "11 total; 2 shown" in text
        assert "Omitted: 9 rows; reason=row_limit" in text
        assert text.count("<identity>") == 2
        assert not hasattr(result, "render")
    category.show()
    text = capsys.readouterr().out
    assert len(text.encode()) <= 8192
    assert "reason=output_budget" in text
    category.show(max_output_bytes=None)
    text = capsys.readouterr().out
    assert len(text.encode()) > 8192
    assert "11 total; 11 shown" in text
    ratio.show(n=0)
    text = capsys.readouterr().out
    assert "11 total; 0 shown" in text
    assert "defined=7, null=0, undefined=4, unknown=0" in text
    assert "undefined(zero_denominator)=4" in text
    assert "all result rows" in text
    assert len(case.session.runs().items) == runs


@pytest.mark.runtime
def test_business_display_identifies_current_and_baseline_windows(
    analysis_dsl_case_factory: DslCaseFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    case = analysis_dsl_case_factory("j2")
    customers = case.session.members(ms.ref.entity("sales.customer"))
    current = customers.observe(
        ms.ref.metric("sales.revenue"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.order_buyer"),
        by=(ms.ref.entity("sales.customer"),),
    )
    baseline = customers.observe(
        ms.ref.metric("sales.revenue"),
        during=mv.time_scope(start="2026-07-01", end="2026-08-01"),
        via=ms.ref.relationship("sales.order_buyer"),
        by=(ms.ref.entity("sales.customer"),),
    )
    current.compare(baseline).execute().show(n=0)
    text = capsys.readouterr().out
    assert "current.metric: sales.revenue" in text
    assert "baseline.metric: sales.revenue" in text
    assert "current.observation_window: [2026-08-01" in text
    assert "baseline.observation_window: [2026-07-01" in text
    assert "unit: CNY" in text
    assert "current.timezone: UTC" in text


@pytest.mark.parametrize(
    "ties,expected",
    [("ordinal", [1, 2, 3]), ("dense", [1, 1, 2]), ("min", [1, 1, 3]), ("max", [2, 2, 3])],
)
def test_exact_ties_and_nondefined_tail(ties: str, expected: list[int]) -> None:
    rows = [
        {"key_0": 2, "value": Decimal("2.000000"), "cell_tag": "defined"},
        {"key_0": 1, "value": Decimal("2.000000"), "cell_tag": "defined"},
        {"key_0": 3, "value": Decimal("1.999999"), "cell_tag": "defined"},
        *(
            {"key_0": key, "value": None, "cell_tag": tag}
            for key, tag in ((6, "null"), (4, "unknown"), (5, "undefined"))
        ),
    ]
    result = ranked(rows, ("key_0",), "descending", ties, ())
    assert [result[(key,)][0] for key in (1, 2, 3)] == expected
    assert [result[(key,)][1] for key in (4, 5, 6)] == [4, 5, 6]
    assert ranked(list(reversed(rows)), ("key_0",), "descending", ties, ()) == result


@pytest.mark.runtime
@pytest.mark.parametrize("parquet", [False, True])
@pytest.mark.parametrize("ties", ["ordinal", "dense", "min", "max"])
@pytest.mark.parametrize("order", ["ascending", "descending"])
def test_public_rank_table_source_and_fixed(
    analysis_dsl_case_factory: DslCaseFactory,
    parquet: bool,
    ties: Literal["ordinal", "dense", "min", "max"],
    order: Literal["ascending", "descending"],
) -> None:
    case = analysis_dsl_case_factory("j2")
    if parquet:
        export_dsl_parquet_models(case, case.root)
        ms.load(workspace_dir=case.root)
    members = case.session.members(ms.ref.entity("sales.customer"))
    region = members.read(ms.ref.dimension("sales.customer.region"))
    values = members.observe(
        ms.ref.metric("sales.order_count"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship(f"sales.{case.names.buyer}"),
        by=(ms.ref.entity("sales.customer"),),
    )
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
    expected = {}
    distinct = sorted(set(counts.values()), reverse=order == "descending")
    for index, key in enumerate(ordered):
        occupied = [i + 1 for i, other in enumerate(ordered) if counts[other] == counts[key]]
        expected[key] = (
            index + 1
            if ties == "ordinal"
            else distinct.index(counts[key]) + 1
            if ties == "dense"
            else min(occupied)
            if ties == "min"
            else max(occupied)
        )
    for current, category in ((values, region), (values.execute(), region.execute())):
        ranking = current.rank(order=order, ties=ties).execute()
        frame = ranking.ranks.to_pandas()
        assert frame.member.tolist() == ordered
        assert dict(zip(frame.member, frame.value, strict=True)) == expected
        limited = ranking.limit(2).execute()
        assert limited.values.to_pandas().member.tolist() == ordered[:2]
        assert dict(
            zip(limited.ranks.to_pandas().member, limited.ranks.to_pandas().value, strict=True)
        ) == {key: expected[key] for key in ordered[:2]}
        table = mv.table(count=ranking.values, rank=ranking.ranks).execute()
        exported = table.to_pandas()
        assert exported.columns.tolist() == ["member", "count", "rank"]
        assert dict(zip(exported.member, exported["count"], strict=True)) == counts
        assert dict(zip(exported.member, exported["rank"], strict=True)) == expected
        assert not hasattr(table, "contract") and not hasattr(table, "where")
        recovered = case.session.artifact(table.artifact_ref)
        assert isinstance(recovered, mv.MaterializedTable)
        assert recovered.to_pandas().equals(exported)
        partitioned = current.rank(order=order, ties=ties, partition_by=(category,)).execute()
        partition_order = []
        partition_ranks = {}
        for label in sorted({label for _, label in facts.customers}):
            group = [key for key in ordered if (key, label) in facts.customers]
            partition_order.extend(group)
            scores = sorted({counts[key] for key in group}, reverse=order == "descending")
            for index, key in enumerate(group):
                occupied = [i + 1 for i, other in enumerate(group) if counts[other] == counts[key]]
                partition_ranks[key] = (
                    index + 1
                    if ties == "ordinal"
                    else scores.index(counts[key]) + 1
                    if ties == "dense"
                    else min(occupied)
                    if ties == "min"
                    else max(occupied)
                )
        partition_frame = partitioned.ranks.to_pandas()
        assert partition_frame.member.tolist() == partition_order
        assert (
            dict(zip(partition_frame.member, partition_frame.value, strict=True)) == partition_ranks
        )
    source = values.rank(order=order, ties=ties)
    table = mv.table(count=source.values, rank=source.ranks).execute()
    assert dict(zip(table.to_pandas().member, table.to_pandas()["rank"], strict=True)) == expected


@pytest.mark.runtime
@pytest.mark.parametrize(
    "physical,parquet",
    [
        (physical, parquet)
        for physical in ("BIGINT", "DOUBLE", "DECIMAL(30,6)", "duration")
        for parquet in (False, True)
    ]
    + [("duration_" + unit, True) for unit in ("s", "ms", "ns")],
)
def test_display_precision_matrix(
    analysis_dsl_case_factory: DslCaseFactory,
    physical: str,
    parquet: bool,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import duckdb
    import pandas as pd
    import pyarrow as pa
    import pyarrow.parquet as pq

    case = analysis_dsl_case_factory("j2")
    with duckdb.connect(str(case.database_path)) as db:
        if physical.startswith("duration") and not parquet:
            db.execute(
                'ALTER TABLE "order" ALTER amount TYPE INTERVAL USING to_microseconds(amount)'
            )
        elif not physical.startswith("duration"):
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
                    raw["amount"].cast(pa.duration(physical.partition("_")[2] or "us")),
                ),
                path,
            )
    ms.load(workspace_dir=case.root)
    members = case.session.members(ms.ref.entity("sales.customer"))
    values = members.observe(
        ms.ref.metric("sales.revenue"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.order_buyer"),
        by=(ms.ref.entity("sales.customer"),),
    )
    expected = {"A": 9007199254740993 if physical == "BIGINT" else 60, "B": 120, "C": 0, "D": 0}
    if physical.startswith("DECIMAL"):
        expected = {
            key: Decimal(value).quantize(Decimal("0.000001")) for key, value in expected.items()
        }
    elif physical.startswith("duration"):
        expected = {
            key: pd.Timedelta(value, unit=physical.partition("_")[2] or "us")
            for key, value in expected.items()
        }
    for current in (values, values.execute()):
        ranking = current.rank(order="descending", ties="min").execute()
        frame = ranking.values.to_pandas()
        assert dict(zip(frame.member, frame.value, strict=True)) == expected
        terminal = mv.table(amount=ranking.values, rank=ranking.ranks).execute()
        exported = terminal.to_pandas()
        assert dict(zip(exported.member, exported.amount, strict=True)) == expected
        assert terminal.to_pandas().equals(case.session.artifact(terminal.artifact_ref).to_pandas())
        terminal.show(max_output_bytes=None)
        display = capsys.readouterr().out
        assert "4 total; 4 shown" in display
        for value in expected.values():
            if isinstance(value, pd.Timedelta):
                unit = physical.partition("_")[2] or "us"
                assert f"{value // pd.Timedelta(1, unit=unit)} {unit}" in display
            else:
                assert str(value) in display
        exported.iloc[0, 1] = None
        assert terminal.to_pandas().amount.isna().sum() == 0


@pytest.mark.runtime
@pytest.mark.parametrize("parquet", [False, True])
def test_top_k_original_rank_share_and_empty(
    analysis_dsl_case_factory: DslCaseFactory, parquet: bool
) -> None:
    case = analysis_dsl_case_factory("j2")
    if parquet:
        export_dsl_parquet_models(case, case.root)
        ms.load(workspace_dir=case.root)
    members = case.session.members(ms.ref.entity("sales.customer"))
    category = members.read(ms.ref.dimension("sales.customer.region"))
    values = members.observe(
        ms.ref.metric("sales.order_count"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.order_buyer"),
        by=(ms.ref.entity("sales.customer"),),
    )
    for current, partitions in ((values, category), (values.execute(), category.execute())):
        shares = (
            current.share_of(current.rollup()).execute()
            if isinstance(current, mv.MaterializedNumericRelation)
            else current.share_of(current.rollup())
        )
        grouping = (
            partitions.execute()
            if isinstance(shares, mv.MaterializedNumericRelation)
            and isinstance(partitions, mv.LogicalCategoryRelation)
            else partitions
        )
        ranking = shares.rank(order="descending", ties="min", partition_by=(grouping,)).execute()
        prefix = ranking.limit(1).execute()
        defined = ranking.where(ranking.ranks.value.is_defined())
        top = defined.where(defined.ranks.value.lte(1)).execute()
        assert len(prefix.values.to_pandas()) == 1
        # Both east ties plus the singleton south and west groups survive.
        assert top.values.to_pandas().member.tolist() == ["A", "B", "C", "D"]
        assert top.ranks.to_pandas().value.tolist() == [1, 1, 1, 1]
        assert prefix.values.to_pandas().value.tolist() == [0.25]
        assert (
            dict(prefix.values.contract()._facts)["reference"]
            == dict(ranking.values.contract()._facts)["reference"]
        )
        assert (
            prefix.values._node.root.signature.quantity
            == ranking.values._node.root.signature.quantity
        )
        empty = ranking.where(ranking.ranks.value.gt(99)).execute()
        assert empty.values.to_pandas().empty and empty.ranks.to_pandas().empty
        assert mv.table(a=empty.values, b=empty.ranks).execute().to_pandas().empty
        selected = ranking.where(ranking.values.value.gt(0)).limit(3).execute()
        assert (
            selected.values.to_pandas().member.tolist()
            == selected.ranks.to_pandas().member.tolist()
        )
    flat = values.rank(order="descending", ties="ordinal").execute()
    selected = flat.where(flat.ranks.value.gt(2)).execute()
    assert selected.ranks.to_pandas().value.tolist() == [3, 4]
    assert selected.limit(1).execute().ranks.to_pandas().value.tolist() == [3]


@pytest.mark.runtime
def test_static_display_errors_are_zero_read(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.analysis.errors import AnalysisError
    from marivo.datasource.adapters import SourceSession

    case = analysis_dsl_case_factory("j2")
    members = case.session.members(ms.ref.entity("sales.customer"))
    category = members.read(ms.ref.dimension("sales.customer.region"))
    values = members.observe(
        ms.ref.metric("sales.order_count"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.order_buyer"),
        by=(ms.ref.entity("sales.customer"),),
    )
    fixed = values.execute()
    ranking = values.rank(order="descending", ties="dense")
    other = analysis_dsl_case_factory("j2")
    foreign = other.session.members(ms.ref.entity("sales.customer")).read(
        ms.ref.dimension("sales.customer.region")
    )
    run = values._runtime.last_run_ref

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("static display admission read business data")

    monkeypatch.setattr(SourceSession, "batches", forbidden)
    for bad in (True, False, 0, -1, 100001, 1.5, "1"):
        with pytest.raises(AnalysisError):
            ranking.limit(bad)
    for order, ties in (("up", "dense"), ("ascending", "random")):
        with pytest.raises(AnalysisError):
            values.rank(order=order, ties=ties)
    for columns in (
        {},
        {"member": values},
        {"": values},
        {"a": values, "b": fixed},
        {"a": values, "b": foreign},
        {"a": ranking},
        {"a": members},
    ):
        with pytest.raises(AnalysisError):
            mv.table(**columns)
    with pytest.raises(AnalysisError):
        values.rank(order="ascending", ties="min", partition_by=(values,))
    july = members.observe(
        ms.ref.metric("sales.order_count"),
        during=mv.time_scope(start="2026-07-01", end="2026-08-01"),
        via=ms.ref.relationship("sales.order_buyer"),
        by=(ms.ref.entity("sales.customer"),),
    )
    with pytest.raises(AnalysisError):
        mv.table(a=values, b=july)
    assert values._runtime.last_run_ref == run
    assert isinstance(mv.table(a=values, b=category), mv.LogicalTable)


@pytest.mark.runtime
@pytest.mark.parametrize("parquet", [False, True])
def test_cold_display_continuations_and_corruption(
    analysis_dsl_case_factory: DslCaseFactory, parquet: bool
) -> None:
    import os
    import subprocess
    import sys
    from dataclasses import replace

    import pyarrow as pa

    from marivo.analysis.errors import AnalysisError
    from marivo.analysis.materialization.graph_exchange import ExchangePart, from_arrow

    case = analysis_dsl_case_factory("j4_ties")
    if parquet:
        export_dsl_parquet_models(case, case.root)
        ms.load(workspace_dir=case.root)
    members = case.session.members(ms.ref.entity("sales.customer"))
    values = members.observe(
        ms.ref.metric("sales.order_count"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.order_buyer"),
        by=(ms.ref.entity("sales.customer"),),
    )
    ranking = values.rank(order="descending", ties="dense").execute()
    terminal = mv.table(amount=ranking.values, rank=ranking.ranks).execute()
    for result in (ranking, terminal):
        checked = result._dataset.verified()
        descriptor = result._dataset.artifact.descriptor
        assert descriptor.method_state.kind in ("ranking", "table")
        assert descriptor.method_state.contract_version == 1
        with pytest.raises(AnalysisError):
            replace(descriptor.method_state, contract_version=2)
        for part in checked.parts:
            with pytest.raises(AnalysisError):
                from_arrow(
                    checked.primary,
                    checked.contract,
                    parts=tuple(p for p in checked.parts if p is not part),
                    method_state=checked.method_state,
                    completed_checks=checked.completed_checks,
                )
            if part.role not in ("ranks", "ranking_domain", "ordering", "column_bindings"):
                continue
            name = next(c for c in part.table.column_names if c not in checked.contract.key_fields)
            dtype = part.table.schema.field(name).type
            bad = part.table.set_column(
                part.table.schema.get_field_index(name),
                name,
                pa.array(
                    [99 if pa.types.is_integer(dtype) else "wrong-binding"] * part.table.num_rows,
                    type=dtype,
                ),
            )
            with pytest.raises(AnalysisError):
                from_arrow(
                    checked.primary,
                    checked.contract,
                    parts=tuple(
                        ExchangePart(p.role, bad) if p is part else p for p in checked.parts
                    ),
                    method_state=checked.method_state,
                    completed_checks=checked.completed_checks,
                )
    source_path = case.root / "source_files" if parquet else case.database_path
    offline = source_path.with_name(source_path.name + ".offline")
    source_path.rename(offline)
    script = """
import sys
import duckdb
import marivo.analysis as mv
import marivo.semantic as ms
def unavailable(*args, **kwargs):
    raise AssertionError('fixed display recovery cannot open Semantic or DuckDB')
duckdb.connect = unavailable
sys.modules['duckdb'] = None
ms.load = unavailable
session = mv.session.resume(sys.argv[1], by='id')
ranking = session.artifact(sys.argv[2])
assert isinstance(ranking, mv.MaterializedRankingResult)
ranking.show(n=2)
assert not hasattr(ranking, 'render')
selected = ranking.where(ranking.ranks.value.is_defined())
selected = selected.where(selected.ranks.value.lte(2)).limit(2).execute()
assert selected.values.to_pandas().member.tolist() == ['C','D']
assert selected.ranks.to_pandas().value.tolist() == [1,2]
assert session.artifact(selected.state.artifact_ref).ranks.to_pandas().equals(selected.ranks.to_pandas())
table = session.artifact(sys.argv[3])
assert isinstance(table, mv.MaterializedTable)
table.show(max_output_bytes=None)
assert table.to_pandas().columns.tolist() == ['member','amount','rank']
assert table.to_pandas().amount.tolist() == [1,1,3,2]
assert mv.table(a=selected.values,b=selected.ranks).execute().to_pandas().a.tolist() == [3,2]
"""
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                script,
                case.session.id,
                ranking.state.artifact_ref.ref,
                terminal.artifact_ref.ref,
            ],
            cwd=case.root,
            env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    finally:
        offline.rename(source_path)
    assert completed.returncode == 0, completed.stderr
    continuation = ranking.limit(2)
    continuation.execute()
    receipt = ranking._dataset.artifact.descriptor.parts[0].local
    path = case.root / receipt.project_relative_path / receipt.file_manifest[0].relative_path
    contents = path.read_bytes()
    try:
        path.write_bytes(b"invalid display part")
        with pytest.raises(AnalysisError):
            ranking.show(n=0)
        with pytest.raises(AnalysisError):
            continuation.execute()
    finally:
        path.write_bytes(contents)


@pytest.mark.runtime
@pytest.mark.parametrize(
    "point", ["graph_part_written", "graph_files_published", "graph_receipts_verified"]
)
@pytest.mark.parametrize("family", ["ranking", "table"])
def test_display_publication_is_atomic(
    analysis_dsl_case_factory: DslCaseFactory, point: str, family: str
) -> None:
    from marivo.analysis.errors import AnalysisError

    case = analysis_dsl_case_factory("j2")
    members = case.session.members(ms.ref.entity("sales.customer"))
    values = members.observe(
        ms.ref.metric("sales.order_count"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.order_buyer"),
        by=(ms.ref.entity("sales.customer"),),
    )
    fixed = values.execute()
    before = set((case.root / ".marivo").rglob("*.parquet"))

    def fail(event: str) -> None:
        if event == point:
            raise RuntimeError("injected display publication failure")

    values._runtime._hook = fail
    try:
        with pytest.raises(AnalysisError):
            result = (
                values.rank(order="ascending", ties="ordinal")
                if family == "ranking"
                else mv.table(a=values)
            )
            result.execute()
    finally:
        values._runtime._hook = None
    assert set((case.root / ".marivo").rglob("*.parquet")) == before
    assert fixed.to_pandas().value.tolist() == [1, 1, 1, 1]


@pytest.mark.runtime
def test_shared_rank_fresh_source_fixed_and_no_fallback(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    import duckdb

    from marivo.analysis.errors import AnalysisError
    from marivo.analysis.materialization import graph_display

    case = analysis_dsl_case_factory("j2")
    members = case.session.members(ms.ref.entity("sales.customer"))
    values = members.observe(
        ms.ref.metric("sales.order_count"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.order_buyer"),
        by=(ms.ref.entity("sales.customer"),),
    )
    ranking = values.rank(order="descending", ties="dense")
    calls: list[str] = []
    finish = graph_display.finish

    def counted(node, data):
        if node.method.name == "display.rank":
            calls.append(node.identity)
        return finish(node, data)

    monkeypatch.setattr(graph_display, "finish", counted)
    mv.table(a=ranking.values, b=ranking.ranks).execute()
    assert calls == [ranking._node.root.identity]
    calls.clear()
    independent = values.rank(order="descending", ties="dense")
    mv.table(a=ranking.values, b=independent.ranks).execute()
    assert set(calls) == {ranking._node.root.identity, independent._node.root.identity}
    captured = ranking.execute()
    with duckdb.connect(str(case.database_path)) as db:
        db.execute(
            'INSERT INTO "order" VALUES (?, ?, ?, ?, ?, ?)',
            ["extra", "B", "web", "paid", "2026-08-15", 1],
        )
    assert ranking.execute().ranks.to_pandas().member.tolist()[0] == "B"
    assert captured.limit(1).execute().values.to_pandas().member.tolist() == ["A"]
    offline = case.database_path.with_suffix(".offline")
    case.database_path.rename(offline)
    try:
        with pytest.raises(AnalysisError):
            ranking.execute()
        assert captured.limit(1).execute().values.to_pandas().member.tolist() == ["A"]
    finally:
        offline.rename(case.database_path)


@pytest.mark.runtime
@pytest.mark.parametrize("parquet", [False, True])
def test_complete_composite_keys_scalar_columns_and_nulls(
    analysis_dsl_case_factory: DslCaseFactory, parquet: bool, capsys: pytest.CaptureFixture[str]
) -> None:
    import duckdb
    import ibis
    import pyarrow.parquet as pq

    from marivo.analysis.errors import AnalysisError

    case = analysis_dsl_case_factory("j2")
    with duckdb.connect(str(case.database_path)) as db:
        db.execute(
            "CREATE TABLE r65 (tenant BIGINT, id VARCHAR, amount BIGINT, region VARCHAR, enabled BOOLEAN, moment TIMESTAMPTZ)"
        )
        db.execute(
            "INSERT INTO r65 VALUES (2,'A',NULL,'west',true,'2026-08-01 00:00:00+00'), (1,'B',9007199254740993,'east',false,'2026-08-02 00:00:00+00'), (1,'A',9007199254740993,'east',true,'2026-08-03 00:00:00+00'), (2,'B',9007199254740994,NULL,NULL,NULL)"
        )
    with duckdb.connect(str(case.database_path)) as db:
        db.execute("ALTER TABLE r65 ADD COLUMN day DATE")
        db.execute("UPDATE r65 SET day = CAST(moment AS DATE)")
    source = "md.table('r65')"
    if parquet:
        backend = ibis.duckdb.connect(case.database_path)
        try:
            path = case.root / "r65.parquet"
            pq.write_table(backend.table("r65").to_pyarrow(), path)
        finally:
            backend.disconnect()
        source = f"md.parquet({str(path)!r})"
    model = case.root / "models/semantic/sales/r65.py"
    model.write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        + f"display = ms.entity(name='display', datasource=ms.ref.datasource('warehouse'), source={source}, primary_key=['tenant','id'])\n"
        + "amount = ms.measure_column(name='amount', entity=display, column='amount', additivity=ms.additive_all())\nregion = ms.dimension_column(name='region', entity=display, column='region')\nenabled = ms.dimension_column(name='enabled', entity=display, column='enabled')\nmoment = ms.time_dimension_column(name='moment', entity=display, column='moment', granularity='second', parse=ms.timestamp(timezone='UTC'))\nday = ms.time_dimension_column(name='day', entity=display, column='day', granularity='day')\n"
    )
    ms.load(workspace_dir=case.root)
    members = case.session.members(ms.ref.entity("sales.display"))
    numeric = members.read(ms.ref.measure("sales.display.amount"))
    category = members.read(ms.ref.dimension("sales.display.region"))
    boolean = members.read(ms.ref.dimension("sales.display.enabled"))
    temporal = members.read(ms.ref.time_dimension("sales.display.moment"))
    day_value = members.read(ms.ref.time_dimension("sales.display.day"))
    for columns in (
        (numeric, category, boolean, temporal, day_value),
        tuple(c.execute() for c in (numeric, category, boolean, temporal, day_value)),
    ):
        amount, region, enabled, moment, day = columns
        terminal = mv.table(
            moment=moment, day=day, region=region, amount=amount, enabled=enabled
        ).execute()
        exported = terminal.to_pandas()
        assert exported.columns.tolist() == [
            "member",
            "coord_0",
            "moment",
            "day",
            "region",
            "amount",
            "enabled",
        ]
        assert list(zip(exported.member, exported.coord_0, strict=True)) == [
            (1, "A"),
            (1, "B"),
            (2, "A"),
            (2, "B"),
        ]
        from datetime import date

        assert exported.day.tolist()[:3] == [date(2026, 8, 3), date(2026, 8, 2), date(2026, 8, 1)]
        assert exported.day.isna().tolist() == [False, False, False, True]
        recovered = case.session.artifact(terminal.artifact_ref)
        assert recovered.to_pandas().equals(exported)
        assert exported.amount.tolist()[:2] == [9007199254740993, 9007199254740993]
        assert exported.amount.isna().tolist() == [False, False, True, False]
        terminal.show()
        assert "Null(" in capsys.readouterr().out
        assert not hasattr(terminal, "moment")
        assert not hasattr(terminal, "contract")
        ranking = amount.rank(order="descending", ties="ordinal", partition_by=(region,)).execute()
        frame = ranking.ranks.to_pandas()
        assert list(zip(frame.member, frame.coord_0, strict=True)) == [
            (2, "B"),
            (1, "A"),
            (1, "B"),
            (2, "A"),
        ]
        assert frame.cell_tag.tolist() == ["defined", "defined", "defined", "null"]
        defined = ranking.where(ranking.ranks.value.is_defined())
        top = defined.where(defined.ranks.value.lte(1)).execute()
        assert len(top.ranks.to_pandas()) == 2
        shorter = region.where(region.value.is_defined())
        with pytest.raises(AnalysisError):
            mv.table(a=amount, b=shorter).execute()
        with pytest.raises(AnalysisError):
            amount.rank(order="descending", ties="dense", partition_by=(shorter,)).execute()
    with pytest.raises(AnalysisError):
        mv.table(coord_0=numeric)
    # Declared identity is trusted; necessary rank index insertion rejects conflicts.
    if not parquet:
        with duckdb.connect(str(case.database_path)) as db:
            db.execute("INSERT INTO r65 SELECT * FROM r65 WHERE tenant=1 AND id='A'")
        with pytest.raises(AnalysisError, match="duplicate"):
            numeric.rank(order="ascending", ties="ordinal").execute()


@pytest.mark.parametrize("ties", ["ordinal", "dense", "min", "max"])
def test_closed_display_exchange_preserves_four_cells(
    ties: Literal["ordinal", "dense", "min", "max"],
) -> None:
    import pandas as pd
    import pyarrow as pa

    from marivo.analysis.core.graph import Edge, FixedLeaf, method_node
    from marivo.analysis.core.model import (
        Binding,
        Coordinate,
        DerivedQuantity,
        DomainSignature,
        Signature,
    )
    from marivo.analysis.core.rules import DisplayRank, DisplayTable
    from marivo.analysis.materialization.graph_dataset import _public_table
    from marivo.analysis.materialization.graph_display import fixed, project
    from marivo.analysis.materialization.graph_exchange import ExchangeContract, from_arrow
    from marivo.analysis.methods.physical import FixedShape, NoTime, ScalarType
    from marivo.analysis.methods.semantics import MethodKey
    from marivo.analysis.refs import ArtifactRef

    binding = Binding("r65", "display", "cells", "whole")
    key = (Coordinate(ms.ref.entity("sales.customer"), "customer_id", "identity"),)
    domain = DomainSignature(binding, "entity", key, key, "four-cells")
    signature = Signature(
        domain, DerivedQuantity("input", "test@v1", ("raw",), None, "none", "input_owned")
    )
    primary = pa.table(
        {
            "key_0": [4, 2, 1, 3],
            "value": pa.array([None, None, 2**53 + 1, None], type=pa.int64()),
            "cell_tag": ["null", "unknown", "defined", "undefined"],
            "cell_reason": ["source_null", "coverage", None, "zero_denominator"],
        }
    )
    reasons = (
        ("null", ("source_null",)),
        ("unknown", ("coverage",)),
        ("undefined", ("zero_denominator",)),
    )
    contract = ExchangeContract(
        signature,
        MethodKey("parts_transport"),
        "four-cells",
        primary.schema,
        ("key_0",),
        cell_reasons=reasons,
    )
    source = from_arrow(primary, contract)
    leaf = FixedLeaf(
        ArtifactRef(ref="cells"), "input", signature, ScalarType("int64"), FixedShape(NoTime())
    )
    ranking_node = method_node(
        (Edge("quantity", leaf),),
        DisplayRank("int64", "descending", ties),
        value_type=ScalarType("int64"),
    )
    ranking = fixed(ranking_node, (source,), "four-cells")
    ranks = project(ranking, "ranks")
    values = project(ranking, "values")
    assert _public_table(ranks)["member"].to_pylist() == [1, 2, 3, 4]
    assert ranks.primary["cell_tag"].to_pylist() == primary["cell_tag"].to_pylist()
    assert ranks.primary["cell_reason"].to_pylist() == primary["cell_reason"].to_pylist()
    inputs = tuple(
        FixedLeaf(
            ArtifactRef(ref=name),
            name,
            item.contract.signature,
            ScalarType("int64"),
            FixedShape(NoTime()),
        )
        for name, item in (("values", values), ("ranks", ranks))
    )
    table_node = method_node(
        tuple(Edge("quantity", item) for item in inputs),
        DisplayTable(("amount", "rank"), ("int64", "int64"), ("values", "ranks")),
        value_type=ScalarType("int64"),
    )
    table = fixed(table_node, (values, ranks), "four-cells")
    assert table.contract.column_reasons == (reasons, reasons)
    exported = _public_table(table).to_pandas(types_mapper=pd.ArrowDtype)
    assert exported.column_0__value.tolist()[0] == 2**53 + 1
    assert exported.column_0__value.isna().tolist() == [False, True, True, True]
    for i in range(2):
        assert table.primary[f"column_{i}__cell_tag"].to_pylist() == primary["cell_tag"].to_pylist()


def test_represented_float_order_has_no_epsilon_ties() -> None:
    rows = [
        {"key_0": key, "value": value, "cell_tag": "defined"}
        for key, value in ((1, 1.0), (2, 1.0 + 2**-52), (3, -0.0), (4, 0.0))
    ]
    result = ranked(rows, ("key_0",), "ascending", "dense", ())
    assert [result[(key,)][0] for key in (1, 2, 3, 4)] == [2, 3, 1, 1]


@pytest.mark.runtime
@pytest.mark.parametrize("parquet", [False, True])
def test_empty_original_domain_and_numeric_view_continuations(
    analysis_dsl_case_factory: DslCaseFactory, parquet: bool
) -> None:
    for scenario in ("empty_domain", "j4_ties"):
        case = analysis_dsl_case_factory(scenario)
        if parquet:
            export_dsl_parquet_models(case, case.root)
            ms.load(workspace_dir=case.root)
        members = case.session.members(ms.ref.entity("sales.customer"))
        values = members.observe(
            ms.ref.metric("sales.order_count"),
            during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
            via=ms.ref.relationship("sales.order_buyer"),
            by=(ms.ref.entity("sales.customer"),),
        )
        for current in (values, values.execute()):
            ranking = current.rank(order="descending", ties="ordinal").execute()
            selected = ranking.ranks.where(ranking.ranks.value.gt(1)).execute()
            assert isinstance(
                selected, (mv.MaterializedNumericRelation, mv.MaterializedSelectedNumericRelation)
            )
            assert not isinstance(selected, mv.MaterializedRankingResult)
            assert (
                selected._node.root.signature.quantity
                == ranking.ranks._node.root.signature.quantity
            )
            selected_values = ranking.values.where(ranking.values.value.gt(1)).execute()
            assert isinstance(
                selected_values,
                (mv.MaterializedNumericRelation, mv.MaterializedSelectedNumericRelation),
            )
            if scenario == "empty_domain":
                assert ranking.limit(1).execute().ranks.to_pandas().empty
                assert mv.table(a=ranking.values, b=ranking.ranks).execute().to_pandas().empty
            else:
                assert "relation.rollup()" in {a.call for a in ranking.values.contract().actions}
                assert "relation.rollup()" not in {a.call for a in ranking.ranks.contract().actions}
                assert ranking.values.rollup().execute().to_pandas().value.tolist() == [7]
                assert selected_values.rollup().execute().to_pandas().value.tolist() == [5]
                assert mv.table(
                    total=ranking.values.rollup()
                ).execute().to_pandas().columns.tolist() == ["total"]
        source_prefix = values.rank(order="descending", ties="ordinal").limit(2).execute()
        assert len(source_prefix.values.to_pandas()) == (0 if scenario == "empty_domain" else 2)


@pytest.mark.runtime
def test_empty_singleton_display_views(analysis_dsl_case_factory: DslCaseFactory) -> None:
    case = analysis_dsl_case_factory("j2")
    members = case.session.members(ms.ref.entity("sales.customer"))
    values = members.observe(
        ms.ref.metric("sales.order_count"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.order_buyer"),
        by=(ms.ref.entity("sales.customer"),),
    )
    total = values.rollup()
    ranked_total = total.rank(order="descending", ties="dense")
    empty = ranked_total.where(ranked_total.ranks.value.gt(99))
    for ranking in (empty, empty.execute()):
        assert ranking.limit(1).execute().ranks.to_pandas().empty
        fixed = ranking.execute() if isinstance(ranking, mv.LogicalRankingResult) else ranking
        assert fixed.ranks.where(fixed.ranks.value.is_defined()).execute().to_pandas().empty
        assert mv.table(a=ranking.values, b=ranking.ranks).execute().to_pandas().empty


@pytest.mark.runtime
@pytest.mark.parametrize("parquet", [False, True])
def test_group_and_entity_time_display_domains(
    analysis_dsl_case_factory: DslCaseFactory, parquet: bool
) -> None:
    case = analysis_dsl_case_factory("j2")
    if parquet:
        export_dsl_parquet_models(case, case.root)
        ms.load(workspace_dir=case.root)
    members = case.session.members(ms.ref.entity("sales.customer"))
    region = members.read(ms.ref.dimension("sales.customer.region"))
    count = members.observe(
        ms.ref.metric("sales.order_count"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.order_buyer"),
        by=(ms.ref.entity("sales.customer"),),
    )
    groups = count.group_by(region).rollup()
    for current in (groups, groups.execute()):
        ranking = current.rank(order="descending", ties="dense").execute()
        assert ranking.ranks.to_pandas().value.tolist() == [1, 2, 2]
        terminal = mv.table(count=ranking.values, rank=ranking.ranks).execute().to_pandas()
        assert terminal.columns.tolist() == ["group", "count", "rank"]
        assert terminal["count"].tolist() == [2, 1, 1]
    grid = mv.time_grid(
        during=mv.time_scope(start="2026-07-01", end="2026-10-01"), grain=mv.grain("month")
    )
    product = members.each(grid)
    values = product.observe(
        ms.ref.metric("sales.order_count"),
        during=grid.window,
        via=ms.ref.relationship("sales.order_buyer"),
        by=(ms.ref.entity("sales.customer"),),
    )
    categories = product.read(ms.ref.dimension("sales.customer.region"))
    facts = analysis_dsl_rows("j2")
    for current, category in ((values, categories), (values.execute(), categories.execute())):
        ranking = current.rank(
            order="descending", ties="ordinal", partition_by=(category,)
        ).execute()
        frame = ranking.values.to_pandas()
        assert len(frame) == 12
        assert frame[["member", "coord_0"]].equals(ranking.ranks.to_pandas()[["member", "coord_0"]])
        expected_counts = {
            member: [
                sum(
                    buyer == member and str(day).startswith(month)
                    for _, buyer, _, _, day, _ in facts.orders
                )
                for month in ("2026-07", "2026-08", "2026-09")
            ]
            for member, _ in facts.customers
        }
        assert {
            member: sorted(frame.loc[frame.member == member, "value"].tolist())
            for member in expected_counts
        } == {member: sorted(values) for member, values in expected_counts.items()}
        assert mv.table(amount=ranking.values, rank=ranking.ranks).execute().to_pandas().shape == (
            12,
            4,
        )


@pytest.mark.runtime
@pytest.mark.parametrize("parquet", [False, True])
def test_public_undefined_ranks_and_strict_two_step_filter(
    analysis_dsl_case_factory: DslCaseFactory, parquet: bool, capsys: pytest.CaptureFixture[str]
) -> None:
    from marivo.analysis.errors import AnalysisError

    case = analysis_dsl_case_factory("j2")
    if parquet:
        export_dsl_parquet_models(case, case.root)
        ms.load(workspace_dir=case.root)
    members = case.session.members(ms.ref.entity("sales.customer"))
    values = members.observe(
        ms.ref.metric("sales.revenue"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.order_buyer"),
        by=(ms.ref.entity("sales.customer"),),
    )
    ratio = values.ratio(values)
    for current in (ratio, ratio.execute()):
        ranking = current.rank(order="descending", ties="dense").execute()
        assert ranking.ranks.to_pandas().cell_tag.tolist() == [
            "defined",
            "defined",
            "undefined",
            "undefined",
        ]
        with pytest.raises(AnalysisError):
            ranking.where(
                mv.all_of(ranking.ranks.value.is_defined(), ranking.ranks.value.lte(1))
            ).execute()
        defined = ranking.where(ranking.ranks.value.is_defined())
        top = defined.where(defined.ranks.value.lte(1)).execute()
        assert top.ranks.to_pandas().value.tolist() == [1, 1]
        table = mv.table(membership=ranking.values, rank=ranking.ranks).execute()
        assert table.to_pandas().membership.isna().tolist() == [False, False, True, True]
        table.show()
        card = capsys.readouterr().out
        assert "Undefined(zero_denominator)" in card
        assert "1.0" in card


@pytest.mark.runtime
@pytest.mark.parametrize("fixed", [False, True])
@pytest.mark.parametrize("view", ["values", "ranks"])
def test_ranking_views_can_be_ranked_again(
    analysis_dsl_case_factory: DslCaseFactory, fixed: bool, view: str
) -> None:
    case = analysis_dsl_case_factory("j2")
    export_dsl_parquet_models(case, case.root)
    ms.load(workspace_dir=case.root)
    members = case.session.members(ms.ref.entity("sales.customer"))
    values = members.observe(
        ms.ref.metric("sales.order_count"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship(f"sales.{case.names.buyer}"),
        by=(ms.ref.entity("sales.customer"),),
    )
    ranking = values.rank(order="descending", ties="ordinal")
    current = ranking.execute() if fixed else ranking
    numeric = current.values if view == "values" else current.ranks
    result = numeric.rank(order="ascending", ties="ordinal").execute()
    facts = analysis_dsl_rows("j2")
    counts = {
        member: sum(
            buyer == member and str(day).startswith("2026-08")
            for _, buyer, _, _, day, _ in facts.orders
        )
        for member, *_ in facts.customers
    }
    scores = (
        counts
        if view == "values"
        else {
            key: index + 1
            for index, key in enumerate(sorted(counts, key=lambda key: (-counts[key], key)))
        }
    )
    ordered = sorted(scores, key=lambda key: (scores[key], key))
    frame = result.ranks.to_pandas()
    assert frame.member.tolist() == ordered
    assert frame.value.tolist() == list(range(1, len(ordered) + 1))
    values_frame = result.values.to_pandas()
    assert values_frame.member.tolist() == ordered
    assert values_frame.value.tolist() == [scores[key] for key in ordered]


@pytest.mark.parametrize("domain", ("anchor", "journey"))
def test_fixed_rank_instance_routes_do_not_grant_live_source_domains(domain: str) -> None:
    from marivo.analysis.methods.builtin import implementations
    from marivo.analysis.methods.physical import FixedShape, Qualified, SourceShape
    from marivo.analysis.methods.semantics import MethodKey

    ranked = tuple(
        item
        for item in implementations(MethodKey("display.rank"))
        if item.key.input_domains == (domain,)
    )
    assert ranked
    assert all(isinstance(item.key.shape, FixedShape) for item in ranked)
    assert all(isinstance(item.qualification, Qualified) for item in ranked)
    assert not any(isinstance(item.key.shape, SourceShape) for item in ranked)
