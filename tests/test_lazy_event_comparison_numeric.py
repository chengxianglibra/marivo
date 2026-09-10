"""Independent native/pandas Event arithmetic over exact compact components."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import ibis
import pandas as pd
import pyarrow as pa
import pytest

from marivo.analysis.compiler.event_attribution import lower_attribute
from marivo.analysis.compiler.event_comparison import lower_compare
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.domains.event_attribution import FunnelAttributePayload
from marivo.analysis.domains.event_attribution_values import execute_attribute
from marivo.analysis.domains.event_comparison import FunnelComparePayload, FunnelCompareSpec
from marivo.analysis.domains.event_comparison_values import execute_compare
from marivo.analysis.funnel import funnel_loss_rate
from marivo.analysis.operators.row import ordered
from marivo.analysis.session._lazy_sources import LazySources, make_lazy_sources
from marivo.refs import ref
from tests.lazy_event_fixtures import make_event_registry, make_event_sources
from tests.lazy_event_runtime_fixtures import journey
from tests.lazy_observation_fixtures import NoIoActionPort


def two_axis_sources() -> LazySources:
    registry, sidecar = make_event_registry(Path("/nonexistent/event-numeric.duckdb"))
    source = ref.dimension("sales.customers.region")
    target = ref.dimension("sales.customers.category")
    member = replace(
        registry.dimensions[source.path],
        semantic_id=target.path,
        name="category",
        python_symbol="category",
    )
    registry = replace(registry, dimensions={**registry.dimensions, target.path: member})
    registry.freeze()
    sidecar = replace(
        sidecar,
        bodies={**sidecar.bodies, target: sidecar.bodies[source]},
        field_owners={**sidecar.field_owners, target: sidecar.field_owners[source]},
        catalog_refs=frozenset((*sidecar.catalog_refs, target)),
    )
    return make_lazy_sources(
        semantic_registry=registry,
        sidecar=sidecar,
        action_port=NoIoActionPort(),
        session_id="two-axes",
        store_id="two-axes",
    )


def cells(groups: tuple[tuple[str | None, int, int], ...], *, grouped: bool) -> pd.DataFrame:
    records = []
    for axis, count, reached in groups:
        for step, found in (("start", count), ("finish", reached)):
            records.append(
                {
                    **({"region": axis} if grouped else {}),
                    "step_key": step,
                    "cohort_count": count,
                    "resolved_cohort_count": count,
                    "entry_count": count,
                    "resolved_entry_count": count,
                    "reached_count": found,
                    "lost_count": count - found,
                    "conversion_from_first": found / count if count else None,
                    "conversion_from_previous": found / count
                    if count and step == "finish"
                    else None,
                    "loss_rate_from_previous": (count - found) / count
                    if count and step == "finish"
                    else None,
                    "coverage_censored_count": 0,
                }
            )
    schema = pa.schema(
        [
            *([pa.field("region", pa.string())] if grouped else []),
            pa.field("step_key", pa.string()),
            *(
                pa.field(n, pa.int64())
                for n in (
                    "cohort_count",
                    "resolved_cohort_count",
                    "entry_count",
                    "resolved_entry_count",
                    "reached_count",
                    "lost_count",
                )
            ),
            *(
                pa.field(n, pa.float64())
                for n in (
                    "conversion_from_first",
                    "conversion_from_previous",
                    "loss_rate_from_previous",
                )
            ),
            pa.field("coverage_censored_count", pa.int64()),
        ]
    )
    result: object = pa.Table.from_pylist(records, schema=schema).to_pandas(
        types_mapper=pd.ArrowDtype
    )
    assert isinstance(result, pd.DataFrame)
    return result


def comparison_spec(*, grouped: bool = True) -> FunnelCompareSpec:
    j = journey(make_event_sources())
    f = j.funnel(axes=[ref.dimension("sales.customers.region")] if grouped else [])
    result = f.compare(f)
    assert isinstance(result._root, LogicalRootHandle) and isinstance(
        result._root.payload, FunnelComparePayload
    )
    return result._root.payload.spec


@pytest.mark.parametrize("groups", [(("a", 10, 7), (None, 4, 1)), (("a", 0, 0),), ()])
def test_compare_independent_parity(groups: tuple[tuple[str | None, int, int], ...]) -> None:
    spec = comparison_spec()
    a, b = cells(groups, grouped=True), cells((("b", 8, 5), (None, 3, 1)), grouped=True)
    expected = execute_compare(a, b, spec)
    initial = expected.loc[
        (expected.step_key == "start") & (expected.coordinate_presence == "matched")
    ]
    assert set(initial.calculation_status) <= {"zero_denominator"}
    assert initial.current_loss_rate_from_previous.isna().all()
    assert initial.baseline_loss_rate_from_previous.isna().all()
    assert initial.loss_rate_delta.isna().all()
    backend = ibis.duckdb.connect()
    try:
        left, right = (
            backend.create_table("current", pa.Table.from_pandas(a)),
            backend.create_table("baseline", pa.Table.from_pandas(b)),
        )
        expression, checks = lower_compare(left, right, spec)
        assert all(int(backend.execute(check.expression).iloc[0, 0]) == 0 for check in checks)
        actual = backend.to_pyarrow(
            expression.select(*(f.name for f in spec.output_row.schema.columns))
        ).to_pandas(types_mapper=pd.ArrowDtype)
    finally:
        backend.disconnect()
    pd.testing.assert_frame_equal(
        ordered(actual, spec.output_row, spec.output_rows), expected, check_dtype=False
    )


@pytest.mark.parametrize("axes_count,mode", [(1, "joint"), (2, "joint"), (2, "hierarchy")])
@pytest.mark.parametrize("top_k", [None, 1, 2])
def test_ratio_mix_reconciles_independent_paths(
    mode: str, top_k: int | None, axes_count: int
) -> None:
    j = journey(two_axis_sources())
    from marivo.analysis.domains.contracts import EventJourneySemantics

    meaning = j.row_contract.family_semantics
    assert isinstance(meaning, EventJourneySemantics)
    delta = j.funnel().compare(j.funnel())
    assert isinstance(delta._root, LogicalRootHandle) and isinstance(
        delta._root.payload, FunnelComparePayload
    )
    result = delta.attribute(
        target=funnel_loss_rate(step=meaning.pattern.steps[1]),
        axes=(ref.dimension("sales.customers.region"),)
        + ((ref.dimension("sales.customers.category"),) if axes_count == 2 else ()),
        mode="joint" if mode == "joint" else "hierarchy",
        top_k=top_k,
    )
    assert isinstance(result._root, LogicalRootHandle) and isinstance(
        result._root.payload, FunnelAttributePayload
    )
    spec = result._root.payload.spec
    a, b = (
        cells((("a", 10, 6), (None, 5, 4), ("c", 2, 0)), grouped=True),
        cells((("a", 4, 1), (None, 10, 6), ("b", 6, 5)), grouped=True),
    )
    if axes_count == 2:
        for frame, members in ((a, ("x", "y", "x")), (b, ("z", "x", "y"))):
            frame.insert(
                1,
                "category",
                pd.Series(
                    [v for member in members for v in (member, member)],
                    dtype=pd.ArrowDtype(pa.string()),
                ),
            )
    original = execute_compare(
        cells(((None, 17, 10),), grouped=False),
        cells(((None, 20, 12),), grouped=False),
        delta._root.payload.spec,
    )
    expected = execute_attribute(original, a, b, spec)
    backend = ibis.duckdb.connect()
    try:
        tables = tuple(
            backend.create_table(name, pa.Table.from_pandas(frame))
            for name, frame in (("original", original), ("current", a), ("baseline", b))
        )
        expression, checks = lower_attribute(tables[0], tables[1], tables[2], spec)
        assert all(int(backend.execute(check.expression).iloc[0, 0]) == 0 for check in checks)
        actual = backend.to_pyarrow(
            expression.select(*(f.name for f in spec.output_row.schema.columns))
        ).to_pandas(types_mapper=pd.ArrowDtype)
    finally:
        backend.disconnect()
    pd.testing.assert_frame_equal(
        ordered(actual, spec.output_row, spec.output_rows), expected, check_dtype=False, atol=1e-12
    )
    assert expected.contribution.sum() == pytest.approx(
        (7 / 17 - 8 / 20) * (axes_count if mode == "hierarchy" else 1)
    )
    assert set(expected.contribution_kind) == {"loss", "denominator_mix"}
    for _, resolution in expected.groupby(expected.active_axis_mask.map(tuple), sort=False):
        assert resolution.current_value.sum() == pytest.approx(7 / 17)
        assert resolution.baseline_value.sum() == pytest.approx(8 / 20)
        assert resolution.contribution.sum() == pytest.approx(7 / 17 - 8 / 20)
        assert sorted(resolution.contribution_rank) == list(range(1, len(resolution) + 1))
        for name in ("share_of_positive_pool", "share_of_negative_pool", "share_of_total_delta"):
            assert resolution[name].sum() == pytest.approx(1)
    if axes_count == 1 and top_k is None:
        # Independent closed-form oracle; no production folding or allocation helper.
        losses = {"a": (4, 3), None: (1, 4), "c": (2, 0), "b": (0, 1)}
        for record in expected.to_dict("records"):
            key = None if pd.isna(record["region"]) else record["region"]
            current_loss, baseline_loss = losses[key]
            oracle = (
                (current_loss - baseline_loss) / 17
                if record["contribution_kind"] == "loss"
                else baseline_loss * (1 / 17 - 1 / 20)
            )
            assert record["contribution"] == pytest.approx(oracle)


def test_censoring_is_not_hidden_by_filter() -> None:
    from marivo.analysis.observation.predicates import eq

    j = journey(make_event_sources())
    f = j.funnel()
    delta = f.where(eq(f.fields.get("step_key"), "start")).compare(f)
    assert isinstance(delta._root, LogicalRootHandle) and isinstance(
        delta._root.payload, FunnelComparePayload
    )
    a = cells(((None, 2, 1),), grouped=False)
    a.loc[1, "coverage_censored_count"] = 1
    with pytest.raises(Exception, match="censored"):
        execute_compare(a, a, delta._root.payload.spec)


def test_missing_side_preserves_the_available_rate_and_exact_large_counts() -> None:
    spec = comparison_spec()
    count = 2**53 + 3
    current = cells((("large", count, count - 7),), grouped=True)
    baseline = cells((("other", 5, 1),), grouped=True)
    result = execute_compare(current, baseline, spec)
    row = result[(result.region == "large") & (result.step_key == "finish")].iloc[0]
    assert row.current_cohort_count == count
    assert row.current_loss_rate_from_previous == float(7) / float(count)
    assert pd.isna(row.baseline_loss_rate_from_previous)
    assert pd.isna(row.loss_rate_delta)
    assert row.calculation_status == "missing_side"


@pytest.mark.parametrize("duplicate_side", ["current", "baseline"])
@pytest.mark.parametrize("axis", ["a", None])
def test_duplicate_coordinates_rejected_by_both_paths(
    duplicate_side: str, axis: str | None
) -> None:
    from marivo.analysis.datasets.errors import DatasetConstructionError

    spec = comparison_spec()
    normal = cells(((axis, 5, 2),), grouped=True)
    repeated = pd.concat([normal, normal], ignore_index=True)
    current, baseline = (repeated, normal) if duplicate_side == "current" else (normal, repeated)
    with pytest.raises(DatasetConstructionError, match="duplicate funnel cells"):
        execute_compare(current, baseline, spec)
    backend = ibis.duckdb.connect()
    try:
        left = backend.create_table("current", pa.Table.from_pandas(current))
        right = backend.create_table("baseline", pa.Table.from_pandas(baseline))
        _, checks = lower_compare(left, right, spec)
        counts = [int(backend.execute(check.expression).iloc[0, 0]) for check in checks]
        assert sum(counts) == 2
    finally:
        backend.disconnect()
