"""Independent source attribution arithmetic, mapping and selected-coordinate expansion."""

from dataclasses import replace
from pathlib import Path
from typing import Literal

import ibis
import ibis.expr.types as ir
import pandas as pd
import pyarrow as pa
import pytest

from marivo.analysis import grain, time_scope
from marivo.analysis.compiler import compile_dataset
from marivo.analysis.compiler.attribution import lower_attribute
from marivo.analysis.datasets.base import LogicalDataset
from marivo.analysis.observation.predicates import eq, gt
from marivo.analysis.operators.attribute_values import execute_attribute
from marivo.analysis.operators.attribution_contracts import AttributeSpecV1
from marivo.analysis.operators.row import PartFrame
from marivo.analysis.operators.row_values import frame_keys, row_key_names
from marivo.refs import ref
from tests.lazy_attribute_fixtures import CHANNEL, REGION, inputs, signed_basis_inputs
from tests.lazy_execution_fixtures import execution_fixture


def _source(
    frame: pd.DataFrame, spec: AttributeSpecV1, parts: tuple[PartFrame, ...]
) -> pd.DataFrame:
    wide = frame.copy()
    keys = row_key_names(spec.input_row)
    expected = frame_keys(frame, keys)
    for part in parts:
        positions = dict(zip(frame_keys(part.frame, keys), range(len(part.frame)), strict=True))
        for name in part.frame.columns:
            if name not in keys:
                wide[name] = (
                    part.frame[name]
                    .iloc[pd.Index([positions[key] for key in expected])]
                    .reset_index(drop=True)
                )
    backend = ibis.duckdb.connect()
    try:
        table = backend.create_table(
            "complete_delta", pa.Table.from_pandas(wide, preserve_index=False)
        )
        expression, validations = lower_attribute(table, spec)
        for validation in validations:
            assert backend.to_pyarrow(validation.expression)["violations"][0].as_py() == 0, (
                validation.name
            )
        result = backend.to_pyarrow(expression).to_pandas(types_mapper=pd.ArrowDtype)
        assert isinstance(result, pd.DataFrame)
        return result
    finally:
        backend.disconnect()


def _same(source: pd.DataFrame, local: pd.DataFrame, spec: AttributeSpecV1) -> None:
    keys = row_key_names(spec.output_row)
    left = dict(zip(frame_keys(source, keys), source.to_dict(orient="records"), strict=True))
    right = dict(zip(frame_keys(local, keys), local.to_dict(orient="records"), strict=True))
    assert left.keys() == right.keys()
    for key in left:
        for name, value in left[key].items():
            expected = right[key][name]
            if isinstance(value, (list, tuple)):
                assert tuple(value) == tuple(expected)
            elif value is None or value is pd.NA:
                assert expected is None or expected is pd.NA
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                assert value == pytest.approx(expected)
            else:
                assert value == expected


@pytest.mark.parametrize(
    "name", ["revenue", "order_count", "mean_amount", "weighted_amount", "conversion_rate"]
)
@pytest.mark.parametrize("mode", ["joint", "hierarchy"])
def test_complete_source_mapping_matches_local_and_independent_endpoint(
    name: str, mode: Literal["joint", "hierarchy"]
) -> None:
    frame, spec, parts = inputs(
        name,
        [("a", "x"), ("a", "y"), ("b", "x"), (None, "x")],
        [(12, 2), (18, 3), (4, 1), (6, 1)],
        [(6, 1), (8, 4), (3, 1), (4, 1)],
        mode=mode,
        top_k=1,
    )
    source = _source(frame, spec, parts)
    _same(source, execute_attribute(frame, spec, parts), spec)
    expected = 19 if name in ("revenue", "order_count") else 40 / 7 - 21 / 7
    for active in {tuple(value) for value in source.active_axis_mask}:
        rows = source[[tuple(value) == active for value in source.active_axis_mask]]
        assert rows.contribution.sum() == pytest.approx(expected)


def test_source_top_k_scores_folded_members_before_absolute_value() -> None:
    frame, spec, parts = inputs(
        "revenue",
        [("a", "x"), ("a", "y"), ("b", "x")],
        [(100, 1), (-99, 1), (5, 1)],
        [(100, 1), (-99, 1), (3, 1)],
        mode="hierarchy",
        top_k=1,
    )
    source = _source(frame, spec, parts)
    _same(source, execute_attribute(frame, spec, parts), spec)
    assert source[source.region.notna()].region.tolist() == ["b", "b"]


@pytest.mark.parametrize("name", ["order_count", "revenue"])
def test_source_top_k_respects_one_sided_empty_result_authority(name: str) -> None:
    frame, spec, parts = inputs(
        name,
        [("a",), ("b",)],
        [(99, 1), (5, 1)],
        [(50, 1), (10, 1)],
        top_k=1,
        current_absent=(0,),
    )
    if name == "revenue":
        with pytest.raises(AssertionError, match=r"current\.raw_partition_finite"):
            _source(frame, spec, parts)
        return
    source = _source(frame, spec, parts)
    _same(source, execute_attribute(frame, spec, parts), spec)
    assert source[source.region.notna()].region.tolist() == ["a"]
    assert source.current_value.tolist() == [0, 5]
    assert source.baseline_value.tolist() == [50, 10]
    assert source.contribution.tolist() == [-50, -5]
    assert source.overall_delta.tolist() == [-55, -55]


def test_source_top_k_uses_absolute_signed_basis_and_exact_side_terms() -> None:
    frame, spec, parts = signed_basis_inputs()
    source = _source(frame, spec, parts)
    _same(source, execute_attribute(frame, spec, parts), spec)
    assert source[source.region.notna()].region.tolist() == ["b"]
    assert source.current_value.tolist() == [6.0, 5.0]
    assert source.baseline_value.tolist() == [2.0, 1.0]
    assert source.contribution.tolist() == [4.0, 4.0]
    assert source.overall_delta.tolist() == [8.0, 8.0]


def test_source_rejects_raw_zero_basis_before_other_can_hide_it() -> None:
    frame, spec, parts = inputs(
        "weighted_amount", [("a",), ("b",)], [(0, 0), (10, 2)], [(0, 0), (4, 1)], top_k=1
    )
    corrupt = parts[0].frame.copy()
    name = next(name for name in corrupt.columns if name.endswith("weighted_numerator"))
    corrupt.loc[0, name] = 1
    with pytest.raises(AssertionError, match="zero_basis"):
        _source(frame, spec, (replace(parts[0], frame=corrupt), parts[1]))


def test_source_scope_and_zero_total_shares_match_local() -> None:
    frame, spec, parts = inputs(
        "revenue",
        [("a", "x"), ("b", "x"), ("a", "y"), ("b", "y")],
        [(6, 1), (1, 1), (8, 1), (2, 1)],
        [(2, 1), (5, 1), (3, 1), (2, 1)],
        decompose="first",
    )
    source = _source(frame, spec, parts)
    _same(source, execute_attribute(frame, spec, parts), spec)
    assert source[source.channel == "x"].share_of_total_delta.isna().all()
    assert source.groupby("channel").contribution.sum().to_dict() == {"x": 0, "y": 5}


def test_source_rejects_overflowing_signed_pools_even_when_total_reconciles() -> None:
    frame, spec, parts = inputs(
        "revenue",
        [("a",), ("b",), ("c",), ("d",)],
        [(1e308, 1), (-1e308, 1), (1e308, 1), (-1e308, 1)],
        [(0, 1)] * 4,
    )
    with pytest.raises(AssertionError, match="positive_pool_finite"):
        _source(frame, spec, parts)


def test_source_structural_empty_mean_is_zero_term() -> None:
    frame, spec, parts = inputs(
        "mean_amount", [("a",), ("b",)], [(0, 0), (12, 2)], [(0, 0), (4, 1)]
    )
    changed: list[PartFrame] = []
    for part in parts:
        state = part.frame.copy()
        column = next(name for name in state.columns if name.endswith("_sum"))
        state.loc[0, column] = None
        changed.append(replace(part, frame=state))
    source = _source(frame, spec, tuple(changed))
    _same(source, execute_attribute(frame, spec, tuple(changed)), spec)
    assert source[source.region == "a"].contribution.tolist() == [0.0]


def test_source_rejects_presence_corruption_before_other_can_hide_state() -> None:
    frame, spec, parts = inputs(
        "revenue",
        [("a",), ("b",), ("c",)],
        [(3, 1), (100, 1), (4, 1)],
        [(1, 1), (2, 1), (1, 1)],
        top_k=1,
    )
    corrupt = parts[0].frame.copy()
    for name in corrupt.columns:
        if name.startswith("__mv_current_"):
            corrupt.loc[0, name] = False if name == "__mv_current_present" else None
    with pytest.raises(AssertionError, match="presence"):
        _source(frame, spec, (replace(parts[0], frame=corrupt), parts[1]))


@pytest.mark.parametrize("name", ["order_count", "mean_amount", "revenue"])
@pytest.mark.parametrize("scoped", [False, True])
def test_empty_inputs_require_defined_global_endpoints_but_do_not_invent_scopes(
    name: str, scoped: bool
) -> None:
    frame, spec, parts = inputs(
        name,
        [("a", "x")] if scoped else [("a",)],
        [(1, 1)],
        [(1, 1)],
        decompose="first" if scoped else "all",
    )
    frame = frame.iloc[:0]
    parts = tuple(replace(part, frame=part.frame.iloc[:0]) for part in parts)
    if not scoped and name != "order_count":
        with pytest.raises(AssertionError, match="endpoint_finite"):
            _source(frame, spec, parts)
        from marivo.analysis.operators.errors import AttributionError

        with pytest.raises(AttributionError):
            execute_attribute(frame, spec, parts)
    else:
        assert _source(frame, spec, parts).empty
        assert execute_attribute(frame, spec, parts).empty


def _compiled_rows(dataset: LogicalDataset, tables: dict[str, ir.Table]) -> list[dict[str, object]]:
    compiled = compile_dataset(dataset, tables)
    for validation in compiled.validations:
        assert validation.expression.to_pyarrow()["violations"][0].as_py() == 0, validation.name
    table = compiled.expression.select(*compiled.primary_columns).to_pyarrow()
    return [
        {name: table[name][index].as_py() for name in table.column_names}
        for index in range(len(table))
    ]


def test_logical_expansion_preserves_original_reduced_selection(tmp_path: Path) -> None:
    with execution_fixture(tmp_path) as fixture:
        metric = (
            fixture.sources.observe(ref.metric("sales.revenue")).with_dimensions(REGION).aggregate()
        )
        selected = metric.where(gt(ref.metric("sales.revenue"), 50))
        delta = selected.compare(selected)
        expanded = delta.attribute(axes=(CHANNEL,))
        rows = _compiled_rows(expanded, fixture.tables(expanded))
        assert len(rows) == 1 and rows[0]["region"] is None
        assert rows[0]["current_value"] == 100


def test_logical_expansion_uses_original_selected_time_ordinal(tmp_path: Path) -> None:
    with execution_fixture(tmp_path) as fixture:
        fixture.backend.raw_sql("DELETE FROM orders")
        fixture.backend.raw_sql(
            "INSERT INTO orders (id,customer_id,amount,weight,day,channel) VALUES (1,1,10,1,'2026-02-01','x'),(2,1,20,1,'2026-02-02','y'),(3,1,4,1,'2026-02-03','y'),(4,1,8,1,'2026-02-04','x')"
        )
        current = (
            fixture.sources.observe(
                ref.metric("sales.mean_amount"),
                time_scope=time_scope(start="2026-02-01", end="2026-02-03"),
            )
            .with_time_axis(ref.time_dimension("sales.orders.order_time"), grain=grain("day"))
            .aggregate()
        )
        baseline = (
            fixture.sources.observe(
                ref.metric("sales.mean_amount"),
                time_scope=time_scope(start="2026-02-03", end="2026-02-05"),
            )
            .with_time_axis(ref.time_dimension("sales.orders.order_time"), grain=grain("day"))
            .aggregate()
        )
        delta = current.compare(baseline)
        selected = delta.where(eq(delta.fields.get("comparison_ordinal"), 1))
        attribution = selected.attribute(axes=(CHANNEL,))
        rows = _compiled_rows(attribution, fixture.tables(attribution))
        assert {row["comparison_ordinal"] for row in rows} == {1}
        assert {str(row["current_time"]) for row in rows} == {"2026-02-02"}
        assert {str(row["baseline_time"]) for row in rows} == {"2026-02-04"}
        assert sum(float(str(row["contribution"])) for row in rows) == 12
