"""Period coverage uses frozen aligned keys, retaining both original coordinates."""

from __future__ import annotations

from typing import Literal

import pyarrow as pa
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.core.model import CorrespondencePart
from marivo.analysis.errors import AnalysisError
from tests.shared_fixtures import DslCaseFactory, export_dsl_parquet_models


def period(
    members: mv.LogicalAnalysisDomain,
    month: int,
    length: int,
    *,
    metric: str = "order_count",
    grouped: bool = False,
) -> tuple[mv.TimeGrid, mv.LogicalNumericRelation]:
    grid = mv.time_grid(
        during=mv.time_scope(
            start=f"2026-{month:02d}-01", end=f"2026-{month:02d}-{length + 1:02d}"
        ),
        grain=mv.grain("day"),
        timezone="UTC",
    )
    result = members.observe(
        ms.ref.metric("sales." + metric),
        during=grid,
        via=ms.ref.relationship("sales.order_buyer"),
        by=(mv.member(), *((ms.ref.dimension("sales.customer.region"),) if grouped else ())),
    )
    assert isinstance(result, mv.LogicalNumericRelation)
    return grid, result


@pytest.mark.parametrize("lengths", [(3, 2), (2, 3)])
@pytest.mark.parametrize("parquet", [False, True])
def test_period_outer_keeps_time_missing_and_original_keys(
    analysis_dsl_case_factory: DslCaseFactory,
    lengths: tuple[int, int],
    parquet: bool,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = analysis_dsl_case_factory("j2")
    if parquet:
        export_dsl_parquet_models(case, case.root)
        ms.load(workspace_dir=case.root)
    members = case.session.members(ms.ref.entity("sales.customer"))
    _, current = period(members, 8, lengths[0])
    _, baseline = period(members, 7, lengths[1])
    alignment = mv.outer(time=mv.ordinal())
    results = [current.compare(baseline, align=alignment).execute()]
    results.append(current.execute().compare(baseline.execute(), align=alignment).execute())
    for result in results:
        frame = result.to_pandas()
        assert set(frame.coord_0) == {"ordinal:1", "ordinal:2", "ordinal:3"}
        assert len(frame) == 12
        missing = frame[frame.coord_0 == "ordinal:3"]
        for field in ("absolute", "relative"):
            assert set(missing[field + "_cell_tag"]) == {"undefined"}
            assert set(missing[field + "_cell_reason"]) == {"missing_side"}
        assert result._dataset is not None
        exchange = result._dataset.verified()
        assert exchange.contract.signature.domain.time_grid is None
        assert exchange.contract.signature.domain.instance_key[1].role == "aligned"
        assert exchange.contract.schema.field("key_1").type == pa.string()
        mapping = next(p.table for p in exchange.parts if p.role == "correspondence")
        mapping_schema = next(
            p.schema for p in exchange.contract.parts if p.role == "correspondence"
        )
        declaration = next(
            p for p in exchange.contract.signature.parts if isinstance(p, CorrespondencePart)
        )
        assert declaration.time_keys is not None
        for side, length, table in zip(
            ("current", "baseline"),
            lengths,
            (declaration.time_keys.current, declaration.time_keys.baseline),
            strict=True,
        ):
            original = {aligned: bucket for bucket, aligned in table}
            assert mapping_schema.field(f"correspondence__{side}_key_1").type == pa.string()
            for row in mapping.to_dict(orient="records"):
                assert row[f"correspondence__{side}_present"] == (row["key_1"] in original)
                assert row[f"correspondence__{side}_key_1"] == original.get(row["key_1"])
                if row["key_1"] not in original:
                    assert row[f"correspondence__{side}_key_0"] is None
        result.show()
        text = capsys.readouterr().out
        assert "aligned" in text and "current_time" in text and "baseline_time" in text
        selected = result.absolute.where(result.absolute.value.is_defined())
        assert len(selected.members().execute().to_pandas()) == 4
    assert results[0].to_pandas().equals(results[1].to_pandas())
    first, second = (result._dataset for result in results)
    assert first is not None and second is not None
    left_parts = {part.role: part.table for part in first.verified().parts}
    right_parts = {part.role: part.table for part in second.verified().parts}
    assert left_parts.keys() == right_parts.keys()
    assert all(table.equals(right_parts[role]) for role, table in left_parts.items())


def test_period_exact_and_empty_finish_reject_time_missing_at_construction(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    members = case.session.members(ms.ref.entity("sales.customer"))
    _, current = period(members, 8, 3)
    _, baseline = period(members, 7, 2)
    with pytest.raises(AnalysisError, match=r"equal.*aligned|equal-length"):
        current.compare(baseline, align=mv.exact(time=mv.ordinal()))
    with pytest.raises(AnalysisError, match=r"unobserved|outside.*window") as failure:
        current.compare(baseline, align=mv.outer(missing="empty_value", time=mv.ordinal()))
    assert "keep" in str(failure.value) and "window" in str(failure.value)


def test_aligned_views_reject_real_time_operations_after_selection(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    members = case.session.members(ms.ref.entity("sales.customer"))
    grid, current = period(members, 8, 3)
    _, baseline = period(members, 7, 3)
    logical = current.compare(baseline, align=mv.exact(time=mv.ordinal()))
    fixed = logical.execute()
    for view in (logical.absolute, logical.relative, fixed.absolute, fixed.relative):
        for selected in (view, view.where(view.value.is_defined())):
            if isinstance(
                selected,
                (
                    mv.LogicalNumericRelation,
                    mv.MaterializedNumericRelation,
                    mv.LogicalSelectedNumericRelation,
                ),
            ):
                with pytest.raises(AnalysisError, match="aligned"):
                    selected.group_by(grid)
                with pytest.raises(AnalysisError, match="aligned"):
                    selected.group_by(mv.grain("month"))
        with pytest.raises(AnalysisError, match="aligned"):
            view.runs(where=view.value.gt(0))
        with pytest.raises(AnalysisError, match="aligned"):
            view.forecast(horizon=mv.periods(1))
        with pytest.raises(AnalysisError, match="aligned"):
            view.correlate(logical.relative, lag_range=range(1))
        assert all(
            "forecast(" not in a.call and "runs(" not in a.call for a in view.contract().actions
        )

    # Without lag, association admission uses only the shared paired domain.
    for absolute, relative in (
        (logical.absolute, logical.relative),
        (fixed.absolute, fixed.relative),
    ):
        association = absolute.correlate(relative)
        assert association._node.root.signature.domain.time_grid is None

    from marivo.analysis.public_dsl import _TOKEN

    # Retained-row statistics keep aligned coordinates after dropping comparison Parts.
    for view in (logical.absolute, fixed.absolute):
        node = view._node.summarize(
            "count", coordinates=view._node.root.signature.domain.instance_key
        )
        statistic = mv.LogicalStatisticRelation(_TOKEN, node, view._runtime, inputs=(view,))
        for receiver in (statistic, statistic.execute()):
            assert any(
                c.role == "aligned" for c in receiver._node.root.signature.domain.instance_key
            )
            with pytest.raises(AnalysisError, match="aligned"):
                receiver._group_nodes((grid,))
            with pytest.raises(AnalysisError, match="aligned"):
                receiver.runs(where=receiver.value.gt(0))
            with pytest.raises(AnalysisError, match="aligned"):
                receiver.forecast(horizon=mv.periods(1))
            with pytest.raises(AnalysisError, match="aligned"):
                receiver.correlate(receiver, lag_range=range(1))


@pytest.mark.parametrize("metric", ["order_count", "revenue_zero", "revenue"])
def test_grouped_grid_includes_empty_groups_and_propagates_both_cells(
    analysis_dsl_case_factory: DslCaseFactory, metric: str
) -> None:
    case = analysis_dsl_case_factory("j2")
    if metric == "revenue_zero":
        model = case.root / "models/semantic/sales/models.py"
        model.write_text(
            model.read_text()
            + "\nrevenue_zero = ms.aggregate(name='revenue_zero', measure=amount, agg='sum', time=ordered_at, empty=ms.empty.zero())\n"
        )
        ms.load(workspace_dir=case.root)
    members = case.session.members(ms.ref.entity("sales.customer"))
    current_grid, current = period(members, 8, 3, metric=metric, grouped=True)
    baseline_grid, baseline = period(members, 7, 3, metric=metric, grouped=True)
    region = ms.ref.dimension("sales.customer.region")
    current_grouped = current.group_by(region, current_grid).rollup()
    baseline_grouped = baseline.group_by(region, baseline_grid).rollup()
    observed = current_grouped.execute().to_pandas()
    assert len(observed) == 9
    empty = observed[observed.group.isin(("south", "west"))]
    assert len(empty) == 6
    if metric == "revenue":
        assert set(empty.cell_tag) == {"null"}
        assert set(empty.cell_reason) == {"empty_contribution"}
    else:
        assert set(empty.cell_tag) == {"defined"}
        assert set(empty.value) == {0}
    for alignment in (
        mv.exact(time=mv.ordinal()),
        mv.outer(time=mv.ordinal()),
        mv.outer(missing="empty_value", time=mv.ordinal()),
    ):
        result = current_grouped.compare(baseline_grouped, align=alignment).execute()
        frame = result.to_pandas()
        assert len(frame) == 9
        if metric == "revenue":
            for field in ("absolute", "relative"):
                assert set(frame[field + "_cell_tag"]) == {"null"}
                assert set(frame[field + "_cell_reason"]) == {"empty_contribution"}
        else:
            assert set(frame.absolute_cell_tag) == {"defined"}
            assert set(frame.relative_cell_reason) == {"zero_baseline"}


def test_exact_period_retains_current_and_baseline_original_allocation_states(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    members = case.session.members(ms.ref.entity("sales.customer"))
    current_grid, current = period(members, 8, 3, grouped=True)
    baseline_grid, baseline = period(members, 7, 3, grouped=True)
    region = ms.ref.dimension("sales.customer.region")
    current_rollup = current.group_by(current_grid).rollup()
    baseline_rollup = baseline.group_by(baseline_grid).rollup()
    for left, right in (
        (current_rollup, baseline_rollup),
        (current_rollup.execute(), baseline_rollup.execute()),
    ):
        comparison = left.compare(right, align=mv.exact(time=mv.ordinal())).execute()
        allocation = comparison.absolute.attribute(axes=(region,)).execute()
        assert allocation.contribution.to_pandas().value.sum() == 1


def test_equal_period_empty_finish_allows_missing_non_time_groups(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    import duckdb

    case = analysis_dsl_case_factory("j2")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute("UPDATE \"order\" SET channel='mobile' WHERE order_id='j2_aa'")
    members = case.session.members(ms.ref.entity("sales.customer"))

    def observe(month: int, start: int) -> mv.LogicalNumericRelation:
        grid = mv.time_grid(
            during=mv.time_scope(
                start=f"2026-{month:02d}-{start:02d}",
                end=f"2026-{month:02d}-{start + 3:02d}",
            ),
            grain=mv.grain("day"),
            timezone="UTC",
        )
        result = members.observe(
            ms.ref.metric("sales.order_count"),
            during=grid,
            via=ms.ref.relationship("sales.order_buyer"),
            by=(ms.ref.dimension("sales.order.channel"),),
        )
        assert isinstance(result, mv.LogicalNumericRelation)
        return result

    current, baseline = observe(8, 1), observe(7, 9)
    alignment = mv.outer(missing="empty_value", time=mv.ordinal())
    for left, right in ((current, baseline), (current.execute(), baseline.execute())):
        result = left.compare(right, align=alignment).execute()
        frame = result.to_pandas()
        assert len(frame) == 6 and set(frame.group) == {"mobile", "web"}
        assert set(frame.absolute_cell_tag) == {"defined"}
        assert sorted(frame.absolute.tolist()) == [-4, 0, 0, 0, 0, 1]
        assert "missing_side" not in set(frame.relative_cell_reason)
        assert result._dataset is not None
        correspondence = next(
            p.table for p in result._dataset.verified().parts if p.role == "correspondence"
        )
        assert correspondence["correspondence__current_present"].tolist().count(False) == 3
        assert correspondence["correspondence__baseline_present"].tolist().count(False) == 3


@pytest.mark.slow
@pytest.mark.parametrize(
    "kind,timestamp",
    [
        ("ordinal", False),
        ("holiday_then_weekday", False),
        ("holiday", True),
        ("holiday_then_weekday", True),
    ],
)
def test_period_independent_offline_fixed_and_cold(
    analysis_dsl_case_factory: DslCaseFactory,
    kind: Literal["ordinal", "holiday", "holiday_then_weekday"],
    timestamp: bool,
) -> None:
    import os
    import subprocess
    import sys

    from tests.analysis.materialization.domain_recovery_worker import snapshot
    from tests.support.json import Json, encode, read
    from tests.support.paths import PROJECT_ROOT

    case = analysis_dsl_case_factory("j2")
    members = case.session.members(ms.ref.entity("sales.customer"))
    _, current = period(members, 8, 2)
    _, baseline = period(members, 7, 3)
    from tests.analysis.numeric.test_time_alignment import time_key

    source = current.compare(
        baseline, align=mv.outer(time=time_key(kind, timestamp=timestamp))
    ).execute()
    state: dict[str, Json] = {
        "session": case.session.id,
        "kind": kind,
        "timestamp": timestamp,
        "inputs": {
            "current": snapshot(current.execute()),
            "baseline": snapshot(baseline.execute()),
        },
        "source": snapshot(source),
    }
    (case.root / "period-alignment.json").write_bytes(encode(state))
    case.database_path.rename(case.database_path.with_suffix(".offline"))
    (case.root / "models").rename(case.root / "models.offline")
    pids = {os.getpid()}
    for phase in ("fixed", "cold"):
        output = case.root / (phase + ".json")
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.analysis.materialization.period_alignment_worker",
                str(case.root),
                phase,
                str(output),
            ],
            cwd=PROJECT_ROOT,
            env=dict(os.environ, PYTHONPATH=str(PROJECT_ROOT), MARIVO_TELEMETRY="off"),
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        pid = read(output)["pid"]
        assert isinstance(pid, int)
        pids.add(pid)
    assert len(pids) == 3


def test_period_exchange_checks_original_projections_presence_and_both_views(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    from dataclasses import replace

    from marivo.analysis.materialization.errors import MaterializationError
    from marivo.analysis.materialization.graph_exchange import from_pandas

    case = analysis_dsl_case_factory("j2")
    members = case.session.members(ms.ref.entity("sales.customer"))
    _, current = period(members, 8, 2)
    _, baseline = period(members, 7, 3)
    saved = current.compare(baseline, align=mv.outer(time=mv.ordinal())).execute()
    assert saved._dataset is not None
    checked = saved._dataset.verified()
    primary = checked.primary
    contract = checked.contract
    from_pandas(primary, contract, parts=checked.parts, completed_checks=checked.completed_checks)
    correspondence = next(p for p in checked.parts if p.role == "correspondence")
    table = correspondence.table
    for field, values in (
        ("correspondence__current_key_1", ["forged"] * len(table)),
        ("correspondence__baseline_key_1", [1] * len(table)),
        ("correspondence__current_present", [True] * len(table)),
    ):
        changed = table.copy()
        changed[field] = values
        parts = tuple(
            replace(p, table=changed) if p.role == "correspondence" else p for p in checked.parts
        )
        with pytest.raises(MaterializationError):
            from_pandas(primary, contract, parts=parts, completed_checks=checked.completed_checks)
    for field in ("absolute", "relative"):
        column = field + "__cell_reason"
        changed = primary.copy()
        changed[column] = ["forged"] * len(primary)
        with pytest.raises(MaterializationError):
            from_pandas(
                changed, contract, parts=checked.parts, completed_checks=checked.completed_checks
            )


def test_frozen_time_tables_are_injective_and_match_each_original_grid(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    from dataclasses import replace

    from marivo.analysis.core.graph import MethodNode, method_node
    from marivo.analysis.core.model import TimeKeys
    from marivo.analysis.core.rules import CellCompare
    from marivo.analysis.core.time_alignment import OrdinalTimeMapping

    for table in (
        (("a", "ordinal:1"), ("b", "ordinal:1")),
        (("a", "ordinal:1"), ("a", "ordinal:2")),
        (("a", ""),),
    ):
        with pytest.raises(AnalysisError, match="injective"):
            TimeKeys(table, (("c", "ordinal:1"),), OrdinalTimeMapping())
    case = analysis_dsl_case_factory("j2")
    members = case.session.members(ms.ref.entity("sales.customer"))
    _, current = period(members, 8, 2)
    _, baseline = period(members, 7, 2)
    comparison = current.compare(baseline, align=mv.exact(time=mv.ordinal()))
    node = comparison._node.root
    assert isinstance(node, MethodNode) and isinstance(node.parameters, CellCompare)
    original = node.parameters.time_keys
    assert original is not None
    for changed in (
        TimeKeys(original.current[:-1], original.baseline, original.mapping),
        TimeKeys(
            (("forged", "ordinal:1"), original.current[1]), original.baseline, original.mapping
        ),
        TimeKeys(
            tuple((bucket, f"ordinal:{i}") for i, (bucket, _) in enumerate(original.current, 2)),
            original.baseline,
            original.mapping,
        ),
    ):
        with pytest.raises(AnalysisError, match="complete frozen time-key"):
            method_node(
                node.inputs, replace(node.parameters, time_keys=changed), value_type=node.value_type
            )


@pytest.mark.parametrize("lengths", [(3, 2), (2, 3)])
def test_period_sql_lowering_matches_dual_local_finish(
    analysis_dsl_case_factory: DslCaseFactory, lengths: tuple[int, int]
) -> None:
    case = analysis_dsl_case_factory("j2")
    members = case.session.members(ms.ref.entity("sales.customer"))
    _, current = period(members, 8, lengths[0])
    _, baseline = period(members, 7, lengths[1])
    comparison = current.compare(baseline, align=mv.outer(time=mv.ordinal()))
    expected = comparison.execute().absolute.to_pandas()
    assert single_sql_difference(comparison).to_pandas().equals(expected)


def single_sql_difference(
    comparison: mv.LogicalComparisonResult,
) -> mv.MaterializedDifferenceRelation:
    """Exercise the single-quantity native SQL boundary with the same frozen pairing."""
    from marivo.analysis.core.graph import MethodNode, method_node
    from marivo.analysis.core.rules import CellCompare, CellDerive
    from marivo.analysis.public_dsl import _TOKEN

    root = comparison._node.root
    assert isinstance(root, MethodNode) and isinstance(root.parameters, CellCompare)
    params = root.parameters
    single = CellDerive(
        params.method,
        params.definition_id,
        params.value_policy,
        params.unit,
        params.time_scope,
        params.pairing_check_id,
        params.numeric_check_id,
        params.design,
        params.pairing,
        params.empty_rules,
        params.time_index,
        params.time_keys,
        params.relationship,
        params.verification,
    )
    node = method_node(root.inputs, single, value_type=root.value_type)
    relation = comparison._node._with(node)
    return mv.LogicalDifferenceRelation(_TOKEN, relation, comparison._runtime).execute()
