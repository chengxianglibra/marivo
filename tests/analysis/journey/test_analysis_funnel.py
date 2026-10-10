"""Independent Funnel component and allocation oracles with public source behavior."""

from datetime import UTC, datetime, timedelta
from fractions import Fraction

import pytest

from marivo.analysis.core.domain_captures import DomainPreparationError
from marivo.analysis.core.model import Defined, Undefined
from marivo.analysis.event import FirstPerSubject
from marivo.analysis.methods.funnel import Counts, FunnelState, components, count, rate
from marivo.analysis.methods.journey_matching import OrderedOccurrence, match

START = datetime(2026, 2, 1, tzinfo=UTC)
END = START + timedelta(days=1)
THROUGH = END + timedelta(days=1)


def test_independent_counts_unknown_and_empty() -> None:
    rows = tuple(
        OrderedOccurrence(event, (index,), (subject,), START + timedelta(seconds=index), index)
        for index, (event, subject) in enumerate((("a", 1), ("a", 2), ("b", 1)))
    )
    assignments = match(
        rows,
        events=("a", "b"),
        policy=FirstPerSubject(),
        cohort_start=START,
        cohort_end=END,
        completion_through=THROUGH,
        coverage=(),
    )
    result = components(FunnelState(assignments, (), False, "capture", "[]"), 2, 0)
    assert result[0].counts == Counts(2, 2, 2, 2, 2, 0, 0)
    assert result[1].counts == Counts(2, 1, 2, 1, 1, 0, 1)
    assert rate(result[1], "conversion_from_previous") == Defined(1.0)
    assert rate(result[0], "loss_rate_from_previous") == Undefined("initial_step")
    empty = components(FunnelState((), (), True, "capture", "[]"), 2, 0)
    assert len(empty) == 2
    assert rate(empty[1], "loss_rate_from_previous") == Undefined("zero_denominator")
    with pytest.raises(DomainPreparationError, match="funnel_count"):
        count(2**63)


@pytest.fixture
def funnel_public(tmp_path, request):
    from tests.analysis.journey.funnel_fixtures import build_funnel_public

    options = getattr(request, "param", "table")
    return (
        build_funnel_public(tmp_path, form=options[0], history=options[1])
        if isinstance(options, tuple)
        else build_funnel_public(tmp_path, form=options)
    )


def test_public_funnel_counts_owned_read_and_fixed(funnel_public):
    import marivo.analysis as mv

    _, journeys, pattern, _ = funnel_public
    logical = journeys().funnel()
    fixed = logical.execute()
    frame = fixed.to_pandas()
    assert frame["cohort_count"].tolist() == [2, 2]
    assert frame["lost_count"].tolist() == [0, 1]
    loss = fixed.read(mv.funnel_loss_rate(step=pattern.steps[1])).execute()
    assert loss.to_pandas()["value"].tolist() == [0.5]
    assert fixed.evidence_digest().finding_count == 0

    result = logical.compare(journeys(START - timedelta(days=3)).funnel()).execute()
    assert result.evidence_digest().finding_count == 1
    page = result.findings()
    assert len(page.items) == 1
    assert page.items[0].value.loss_rate_delta == 0.0


def component_state(groups, *, offset=0):
    from marivo.analysis.methods.funnel import EntryAxisRow
    from marivo.analysis.methods.journey_matching import JourneyAssignment

    assignments, axes = [], []
    ordinal = offset
    for coordinate, entered, lost in groups:
        for index in range(entered):
            start = OrderedOccurrence("a", (ordinal,), (ordinal,), START, ordinal)
            finish = (
                None
                if index < lost
                else OrderedOccurrence(
                    "b",
                    (ordinal + 10000,),
                    (ordinal,),
                    START + timedelta(seconds=1),
                    ordinal + 10000,
                )
            )
            assignments.append(
                JourneyAssignment(
                    (ordinal,),
                    start,
                    (start, finish),
                    ("reached", "unreachable" if finish is None else "reached"),
                )
            )
            axes.append(EntryAxisRow("a", (ordinal,), coordinate))
            ordinal += 1
    return FunnelState(tuple(assignments), tuple(axes), True, "capture", "[]")


@pytest.mark.parametrize("mode,top_k", [("joint", None), ("hierarchy", 1)])
def test_fraction_oracle_hierarchy_common_topk_and_typed_other(mode, top_k):
    from marivo.analysis.methods.funnel import AllocationState, ComparisonState, allocate

    current = component_state(((("EU", None), 10, 5), (("US", "Other"), 7, 2)))
    baseline = component_state(((("EU", None), 5, 3), (("Other", "z"), 15, 5)), offset=100)
    original = ComparisonState(current, baseline)
    result = allocate(
        AllocationState(original, original),
        steps=2,
        axes=2,
        target_step=1,
        mode=mode,
        top_k=top_k,
        original_axes=2,
    )
    target = Fraction(7, 17) - Fraction(8, 20)
    assert target == Fraction(1, 85)
    for resolution in {r.resolution for r in result}:
        rows = [r for r in result if r.resolution == resolution]
        assert sum(r.contribution for r in rows) == pytest.approx(float(target), abs=1e-15)
        assert sum(r.current for r in rows) == pytest.approx(7 / 17)
        assert sum(r.baseline for r in rows) == pytest.approx(8 / 20)
        assert sorted(r.rank for r in rows) == list(range(1, len(rows) + 1))
        assert all(r.target == float(target) for r in rows)
    if top_k is None:
        eu = next(
            r
            for r in result
            if r.resolution == 2 and r.coordinates == ("EU", None) and r.kind == "loss"
        )
        mix = next(
            r
            for r in result
            if r.resolution == 2 and r.coordinates == ("EU", None) and r.kind == "denominator_mix"
        )
        assert eu.contribution == float(Fraction(2, 17))
        assert mix.contribution == float(Fraction(9, 340))
        assert mix.current == 0.0 and mix.baseline == -mix.contribution
    else:
        assert any(any(r.other_mask) for r in result)
        assert any(r.coordinates[0] == "EU" and not r.other_mask[0] for r in result)


def test_outer_null_empty_and_zero_delta_shares():
    from marivo.analysis.methods.funnel import AllocationState, ComparisonState, allocate, compare

    current = component_state((((None,), 2, 1),))
    baseline = component_state(((("Other",), 3, 1),), offset=100)
    paired = compare(ComparisonState(current, baseline), 2, 1)
    assert {(r.coordinates, r.presence) for r in paired} == {
        ((None,), "current_only"),
        (("Other",), "baseline_only"),
    }
    assert all(not isinstance(r.delta, Defined) for r in paired)
    state = ComparisonState(current, current)
    zero = allocate(
        AllocationState(state, state),
        steps=2,
        axes=1,
        target_step=1,
        mode="joint",
        top_k=None,
        original_axes=1,
    )
    assert all(
        r.share_total is None and r.share_positive is None and r.share_negative is None
        for r in zero
    )
    with pytest.raises(DomainPreparationError, match="funnel_coverage"):
        compare(
            __import__("dataclasses").replace(
                state, current=__import__("dataclasses").replace(current, complete=False)
            ),
            2,
            1,
        )


@pytest.mark.parametrize("funnel_public", [("table", "snapshot")], indirect=True)
def test_historical_entry_axes_and_real_null(funnel_public):
    from marivo.refs import ref

    session, journeys, _, _ = funnel_public
    registry = journeys()._node.binding.graph.registry
    entity = (
        "sales.snapshots" if "sales.snapshots.region" in registry.dimensions else "sales.validity"
    )
    axis = ref.dimension(entity + ".region")
    current = journeys().funnel(axes=(axis,)).execute().to_pandas()
    baseline = journeys(START - timedelta(days=3)).funnel(axes=(axis,)).execute().to_pandas()
    assert set(current[axis.path].dropna()) == {"new"}
    assert current[axis.path].isna().sum() == 2
    assert set(baseline[axis.path]) == {"old", "Other"}
    assert current["cohort_count"].tolist() == [1, 1, 1, 1]


def test_entry_mappings_require_complete_occurrence_identity() -> None:
    import pyarrow as pa

    from marivo.analysis.core.domain_captures import DomainPreparationError
    from marivo.analysis.materialization.funnel_execution import assemble_axes

    large = 2**53 + 1
    base = pa.table({"key_0": ["start", "start"], "key_1": [1, 2], "key_2": [7, 7]})
    mapping = pa.table(
        {
            "key_0": ["start", "start"],
            "key_1": [2, 1],
            "key_2": [7, 7],
            "axis_0": pa.array([None, large], type=pa.int64()),
        }
    )
    assert assemble_axes(base, (mapping,))["axis_0"].to_pylist() == [large, None]
    invalid_mappings = (
        mapping.slice(0, 1),
        pa.concat_tables([mapping, mapping.slice(0, 1)]),
        mapping.set_column(1, "key_1", pa.array([2, 3])),
    )
    for invalid in invalid_mappings:
        with pytest.raises(DomainPreparationError, match="path mapping"):
            assemble_axes(base, (invalid,))


def test_source_reevaluation_preserves_fixed_result(funnel_public, monkeypatch):
    import duckdb

    from marivo.datasource.adapters import SourceSession

    session, journeys, _, database = funnel_public
    logical = journeys().funnel()
    fixed = logical.execute()
    assert fixed.to_pandas().lost_count.tolist() == [0, 1]
    with duckdb.connect(str(database)) as connection:
        connection.execute("DELETE FROM finished_rows")
    changed = logical.execute()
    assert changed.to_pandas().lost_count.tolist() == [0, 2]

    def forbidden(*args, **kwargs):
        raise AssertionError("fixed result accessed the source")

    monkeypatch.setattr(SourceSession, "__init__", forbidden)
    monkeypatch.setattr(duckdb, "connect", forbidden)
    assert session.artifact(fixed.state.artifact_ref).to_pandas().lost_count.tolist() == [0, 1]
    assert fixed.read(fixed.lost_count).execute().to_pandas().value.tolist() == [0, 1]


def test_large_exact_count_ratio_and_numeric_axis_order():
    from marivo.analysis.methods.funnel import FunnelRow, axis_key

    large = 2**53 + 3
    row = FunnelRow(1, (), Counts(large, large, large, large, large - 7, 7, 0))
    assert rate(row, "loss_rate_from_previous") == Defined(float(Fraction(7, large)))
    assert sorted(((10,), (2,), (None,), ("Other",)), key=axis_key) == [
        (None,),
        (2,),
        (10,),
        ("Other",),
    ]
