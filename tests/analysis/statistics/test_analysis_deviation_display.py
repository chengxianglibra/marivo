"""Fitted display authority, owned ranks and isolated offline recovery."""

import subprocess
import sys
from decimal import Decimal
from typing import Literal

import pyarrow as pa
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.core.model import TableFitsPart
from marivo.analysis.errors import AnalysisError, StatisticalRelationError
from marivo.analysis.materialization.graph_exchange import ExchangePart, from_arrow
from marivo.analysis.methods.deviation_numeric import DeviationMethod
from tests.analysis.statistics.deviation_oracle import expected
from tests.shared_fixtures import DslCaseFactory, analysis_dsl_rows, export_dsl_parquet_models
from tests.support.paths import PROJECT_ROOT


@pytest.mark.runtime
def test_local_display_pairing_rejects_different_current_keys(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    members = case.session.members(ms.ref.entity("sales.order"))
    raw = members.read(ms.ref.measure("sales.order.amount"))
    score = raw.deviation(method="zscore").score
    selected = score.rank(order="descending", ties="ordinal").limit(3).values
    with pytest.raises(AnalysisError, match="display input complete keys differ"):
        mv.table(score=selected, original=raw).execute()


@pytest.mark.runtime
@pytest.mark.parametrize("method", ("zscore", "mad"))
@pytest.mark.parametrize("mode", ("category", "original", "other_fit", "ranks"))
@pytest.mark.parametrize("reverse", (False, True))
def test_mixed_table_preserves_each_current_value_and_original_fit(
    analysis_dsl_case_factory: DslCaseFactory,
    method: DeviationMethod,
    mode: Literal["category", "original", "other_fit", "ranks"],
    reverse: bool,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = analysis_dsl_case_factory("j2")
    members = case.session.members(ms.ref.entity("sales.order"))
    raw = members.read(ms.ref.measure("sales.order.amount"))
    fit = raw.deviation(method=method)
    score = fit.score
    other: mv.LogicalNumericRelation | mv.LogicalCategoryRelation
    if mode == "category":
        category = members.read(ms.ref.dimension("sales.order.channel"))
        assert isinstance(category, mv.LogicalCategoryRelation)
        other = category
    elif mode == "original":
        other = raw
    elif mode == "other_fit":
        other = raw.deviation(method="mad" if method == "zscore" else "zscore").score
    else:
        ranking = score.rank(order="descending", ties="dense").limit(3)
        score, other = ranking.values, ranking.ranks
    columns = {"other": other, "score": score} if reverse else {"score": score, "other": other}
    result = mv.table(**columns).execute()
    result.show(n=0)
    shown = capsys.readouterr().out
    assert f"score.fit: deviation.{method}; original fit retained" in shown
    assert "selection does not refit" in shown
    facts = analysis_dsl_rows("j2").orders
    oracle = expected(tuple(row[-1] for row in facts), method)[3]
    wanted = dict(zip((row[0] for row in facts), oracle, strict=True))
    frame = result.to_pandas()
    assert dict(zip(frame.member, frame.score, strict=True)) == {
        key: wanted[key] for key in frame.member
    }
    if mode == "ranks":
        assert sorted(frame.other) == [1, 2, 3]
    restored = case.session.artifact(result.artifact_ref)
    assert isinstance(restored, mv.MaterializedTable)
    assert restored.to_pandas().equals(frame)
    assert result._dataset is not None
    checked = result._dataset.verified()
    declaration = next(p for p in checked.contract.signature.parts if isinstance(p, TableFitsPart))
    score_index = 1 if reverse else 0
    assert tuple(c.index for c in declaration.columns) == (
        (0, 1) if mode in ("ranks", "other_fit") else (score_index,)
    )
    from_arrow(
        checked.primary, checked.contract, parts=checked.parts, method_state=checked.method_state
    )
    name = f"column_{score_index}__value"
    bad = pa.array([1.0] * checked.primary.num_rows, type=pa.float64())
    primary = checked.primary.set_column(checked.primary.schema.get_field_index(name), name, bad)
    parts = tuple(
        ExchangePart(
            p.role,
            p.table.set_column(
                p.table.schema.get_field_index("columns__" + name), "columns__" + name, bad
            ),
        )
        if p.role == "columns"
        else p
        for p in checked.parts
    )
    with pytest.raises(StatisticalRelationError) as failure:
        from_arrow(primary, checked.contract, parts=parts, method_state=checked.method_state)
    assert failure.value.code == "r8.retained_part"


@pytest.mark.runtime
@pytest.mark.parametrize("method", ("zscore", "mad"))
def test_rank_projection_disclosure_and_fixed_table_keep_fit_scope(
    analysis_dsl_case_factory: DslCaseFactory, method: DeviationMethod
) -> None:
    case = analysis_dsl_case_factory("j2")
    raw = case.session.members(ms.ref.entity("sales.order")).read(
        ms.ref.measure("sales.order.amount")
    )
    logical = raw.deviation(method=method).score.rank(order="descending", ties="dense").limit(3)
    logical_ranks = logical.ranks.execute()
    ranking = logical.execute()
    for ranks in (logical_ranks, ranking.ranks):
        frame = ranks.to_pandas()
        assert frame.value.tolist() == [1, 2, 3]
        facts = dict(ranks.contract()._facts)
        assert facts["original_count"] == "11"
        assert facts["method"] == "display.ranks"
        ranks.show()
        assert ranks._dataset is not None
        assert ranks.contract().retained_parts == tuple(
            p.role for p in ranks._dataset.verified().parts
        )
        logical_table = mv.table(rank=ranks)
        table = logical_table.execute()
        assert sorted(table.to_pandas()["rank"]) == [1, 2, 3]
        runs = case.session.runs().items
        assert logical_table.execute().artifact_ref == table.artifact_ref
        assert case.session.runs().items == runs


@pytest.mark.runtime
@pytest.mark.parametrize("fault", ("missing", "corrupt"))
def test_table_fit_receipt_is_required_for_recovery_and_exact_hit(
    analysis_dsl_case_factory: DslCaseFactory, fault: Literal["missing", "corrupt"]
) -> None:
    case = analysis_dsl_case_factory("j2")
    members = case.session.members(ms.ref.entity("sales.order"))
    raw = members.read(ms.ref.measure("sales.order.amount")).execute()
    fit = raw.deviation(method="mad").execute()
    logical = mv.table(original=raw, score=fit.score)
    table = logical.execute()
    case.session.artifact(table.artifact_ref).to_pandas()
    assert table._dataset is not None
    receipt = next(
        p.local for p in table._dataset.artifact.descriptor.parts if p.role == "table_fits"
    )
    path = case.root / receipt.project_relative_path / receipt.file_manifest[0].relative_path
    if fault == "missing":
        path.unlink()
    else:
        payload = path.read_bytes()
        path.write_bytes(payload[:-1] + bytes((payload[-1] ^ 1,)))
    runs = case.session.runs().items
    with pytest.raises(AnalysisError):
        case.session.artifact(table.artifact_ref)
    with pytest.raises(AnalysisError):
        logical.execute()
    assert case.session.runs().items == runs
    assert case.session._runtime.store.resources(case.session.id) == ()


@pytest.mark.runtime
@pytest.mark.parametrize("method", ("zscore", "mad"))
def test_source_fixed_and_cold_display_in_independent_processes(
    analysis_dsl_case_factory: DslCaseFactory, method: DeviationMethod
) -> None:
    case = analysis_dsl_case_factory("j2")
    export_dsl_parquet_models(case, case.root)
    for phase in ("produce", "fixed", "cold"):
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.analysis.statistics.deviation_display_worker",
                str(case.root),
                phase,
                method,
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr


@pytest.mark.runtime
@pytest.mark.parametrize("method", ("zscore", "mad"))
def test_decimal_grid_tables_retain_current_keys_and_full_fit(
    analysis_dsl_case_factory: DslCaseFactory, method: DeviationMethod
) -> None:
    import duckdb

    case = analysis_dsl_case_factory("j2")
    with duckdb.connect(str(case.database_path)) as db:
        db.execute('ALTER TABLE "order" ALTER amount TYPE DECIMAL(30,6)')
    ms.load(workspace_dir=case.root)
    grid = mv.time_grid(
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"), grain=mv.grain("day")
    )
    raw = (
        case.session.members(ms.ref.entity("sales.customer"))
        .each(grid)
        .observe(
            ms.ref.metric("sales.revenue"),
            during=grid.window,
            via=ms.ref.relationship("sales." + case.names.buyer),
            by=(ms.ref.entity("sales.customer"),),
        )
    )
    original = raw.execute()
    input_frame = original.to_pandas()
    assert len(input_frame) == 124
    defined = input_frame.loc[input_frame.cell_tag == "defined"]
    assert input_frame.cell_tag.eq("null").any()
    assert all(isinstance(value, Decimal) for value in defined.value)
    inputs = tuple(value for value in defined.value if isinstance(value, Decimal))
    scores = expected(inputs, method)[3]
    wanted = dict(zip(zip(defined.member, defined.coord_0, strict=True), scores, strict=True))
    logical = raw.deviation(method=method)
    fixed = logical.execute()
    for receiver, observed in ((logical, raw), (fixed, original)):
        mixed = mv.table(original=observed, score=receiver.score).execute()
        frame = mixed.to_pandas()
        assert frame.original.isna().tolist() == input_frame.value.isna().tolist()
        assert frame.original.dropna().tolist() == defined.value.tolist()
        current = frame.loc[frame.score.notna()]
        assert (
            dict(zip(zip(current.member, current.coord_0, strict=True), current.score, strict=True))
            == wanted
        )
        ranking = receiver.score.rank(order="descending", ties="dense").limit(7).execute()
        assert dict(ranking.ranks.contract()._facts)["original_count"] == "124"
        ranked = mv.table(rank=ranking.ranks, score=ranking.values).execute()
        ranked_frame = ranked.to_pandas()
        assert len(ranked_frame) == 7
        current = ranked_frame.loc[ranked_frame.score.notna()]
        assert dict(
            zip(
                zip(current.member, current.coord_0, strict=True),
                current.score,
                strict=True,
            )
        ) == {
            (member, time): wanted[(member, time)]
            for member, time in zip(current.member, current.coord_0, strict=True)
        }
        for table, exported in ((mixed, frame), (ranked, ranked_frame)):
            restored = case.session.artifact(table.artifact_ref)
            assert isinstance(restored, mv.MaterializedTable)
            assert restored.to_pandas().equals(exported)
