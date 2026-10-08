"""Independent components, public funnel and atomic Finding regressions."""

from datetime import UTC, datetime, timedelta
from fractions import Fraction

import pytest

from marivo.analysis.core.domain_captures import DomainPreparationError
from marivo.analysis.core.model import Defined, Undefined
from marivo.analysis.event import FirstPerSubject
from marivo.analysis.methods.funnel import Counts, FunnelState, components, count, rate
from marivo.analysis.methods.journey_matching import OrderedOccurrence, match
from tests.support.paths import PROJECT_ROOT

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


@pytest.mark.runtime
def test_public_funnel_counts_owned_read_and_fixed(funnel_public):
    session, journeys, pattern, _ = funnel_public
    logical = journeys().funnel()
    fixed = logical.execute()
    frame = fixed.to_pandas()
    assert frame["cohort_count"].tolist() == [2, 2]
    assert frame["lost_count"].tolist() == [0, 1]
    loss = fixed.read(
        __import__("marivo.analysis", fromlist=["funnel_loss_rate"]).funnel_loss_rate(
            step=pattern.steps[1]
        )
    ).execute()
    assert loss.to_pandas()["value"].tolist() == [0.5]
    assert session.artifact(fixed.state.artifact_ref).to_pandas().equals(frame)
    assert fixed.evidence_digest().finding_count == 0


@pytest.mark.runtime
@pytest.mark.parametrize("funnel_public", ["table", "parquet"], indirect=True)
def test_public_compare_nonempty_findings(funnel_public):
    session, journeys, _, _ = funnel_public
    result = journeys().funnel().compare(journeys(START - timedelta(days=3)).funnel()).execute()
    assert result.evidence_digest().finding_count == 1
    page = result.findings()
    assert len(page.items) == 1
    assert result.finding(page.items[0].finding_id) == page.items[0]
    assert page.items[0].value.loss_rate_delta == 0.0
    summary = next(
        item
        for item in session.graph(artifact_ref=result.state.artifact_ref).artifacts
        if item.artifact_ref == result.state.artifact_ref
    )
    assert summary.evidence.finding_count == 1
    assert summary.evidence.finding_set_digest == result.evidence_digest().finding_set_digest


@pytest.mark.runtime
@pytest.mark.parametrize("funnel_public", ["table", "parquet"], indirect=True)
def test_grouped_axis_expansion_allocation_and_fixed(funnel_public):
    import marivo.analysis as mv
    from marivo.refs import ref

    session, journeys, pattern, _ = funnel_public
    axes = (ref.dimension("sales.customers.region"),)
    change = journeys().funnel().compare(journeys(START - timedelta(days=3)).funnel())
    allocation = change.attribute(
        target=mv.funnel_loss_rate(step=pattern.steps[1]), axes=axes
    ).execute()
    assert dict(allocation.contract()._facts)["entry_axes"] == axes[0].path
    frame = allocation.to_pandas()
    assert frame["contribution"].sum() == 0.0
    assert allocation.evidence_digest().finding_count == 4
    assert allocation.current.to_pandas()["value"].sum() == 0.5
    assert allocation.baseline.to_pandas()["value"].sum() == 0.5
    assert (
        session.artifact(allocation.state.artifact_ref).findings().items
        == allocation.findings().items
    )
    current = journeys().funnel(axes=axes).execute()
    baseline = journeys(START - timedelta(days=3)).funnel(axes=axes).execute()
    fixed = current.compare(baseline).execute()
    assert fixed.findings().items
    continuation = fixed.attribute(target=mv.funnel_loss_rate(step=pattern.steps[1]), axes=axes)
    result = continuation.execute()
    assert result.to_pandas()["contribution"].sum() == 0.0
    assert result.state.artifact_ref == continuation.execute().state.artifact_ref


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


@pytest.mark.parametrize("mode", ["joint", "hierarchy"])
@pytest.mark.parametrize("top_k", [None, 1])
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


@pytest.mark.runtime
def test_allocation_selection_rank_table_and_repair(funnel_public):
    import marivo.analysis as mv
    from marivo.analysis.datasets.errors import DatasetConstructionError
    from marivo.refs import ref

    _, journeys, pattern, _ = funnel_public
    current = journeys().funnel()
    foreign = journeys().funnel()
    with pytest.raises(DatasetConstructionError):
        current.read(foreign.lost_count)
    with pytest.raises(DatasetConstructionError):
        current.read(mv.funnel_loss_rate(step=pattern.steps[0]))
    change = current.compare(journeys(START - timedelta(days=3)).funnel())
    parts = change.attribute(
        target=mv.funnel_loss_rate(step=pattern.steps[1]),
        axes=(ref.dimension("sales.customers.region"),),
        top_k=1,
    ).execute()
    selected = parts.where(parts.contribution.value.gt(0)).execute()
    declaration = next(
        p
        for p in selected._node.root.signature.parts
        if isinstance(
            p,
            __import__(
                "marivo.analysis.core.model", fromlist=["FunnelAllocationPart"]
            ).FunnelAllocationPart,
        )
    )
    assert not declaration.complete
    assert selected.to_pandas()["value"].gt(0).all()
    ranking = parts.contribution.rank(order="descending", ties="ordinal").execute()
    table = mv.table(contribution=ranking.values, rank=ranking.ranks).execute()
    assert sorted(table.to_pandas()["rank"].tolist()) == list(range(1, len(table.to_pandas()) + 1))


@pytest.mark.runtime
@pytest.mark.parametrize(
    "point",
    ["insert_artifact", "insert_evidence", "insert_findings", "insert_terminal", "before_commit"],
)
def test_findings_transaction_failure_has_no_partial_publication(funnel_public, point):
    from marivo.analysis.materialization.errors import MaterializationError

    session, journeys, _, _ = funnel_public

    def inject(actual):
        if actual == point:
            raise RuntimeError("injected atomic Finding failure")

    session._runtime._hook = inject
    with pytest.raises(MaterializationError):
        journeys().funnel().compare(journeys(START - timedelta(days=3)).funnel()).execute()
    with session._runtime.store._read() as connection:
        assert connection.execute("SELECT COUNT(*) FROM dataset_artifacts").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM dataset_evidence").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM findings").fetchone()[0] == 0
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM analysis_action_run_terminals WHERE outcome='succeeded'"
            ).fetchone()[0]
            == 0
        )
        assert connection.execute("SELECT COUNT(*) FROM action_resource_journal").fetchone()[0] == 0


@pytest.mark.runtime
@pytest.mark.parametrize("form", ["table", "parquet"])
@pytest.mark.parametrize("history", ["none", "snapshot", "validity"])
def test_independent_producer_continue_recover_nonempty_findings(tmp_path, form, history):
    import subprocess
    import sys

    for phase in ("produce", "continue", "recover"):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.analysis.journey.funnel_worker",
                str(tmp_path),
                phase,
                form,
                history,
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert '"accepted": true' in result.stdout


@pytest.mark.runtime
@pytest.mark.parametrize(
    "funnel_public",
    [
        ("table", "snapshot"),
        ("table", "validity"),
        ("parquet", "snapshot"),
        ("parquet", "validity"),
    ],
    indirect=True,
)
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


@pytest.mark.runtime
def test_finding_cap_retains_eligible_emitted_truncated_authority(funnel_public, tmp_path):
    import json
    import subprocess
    import sys

    import duckdb

    from marivo.analysis.materialization.graph_findings import POLICY
    from marivo.analysis.materialization.graph_snapshot import freeze_graph
    from marivo.refs import ref

    session, journeys, _, database = funnel_public
    with duckdb.connect(str(database)) as connection:
        for table in ("customers", "started_rows", "finished_rows"):
            connection.execute(f"DELETE FROM {table}")
        connection.executemany(
            "INSERT INTO customers (id, region) VALUES (?, ?)",
            [(i, f"g{i:04d}") for i in range(1003)],
        )
        starts = [(i * 2, i, START) for i in range(1003)] + [
            (i * 2 + 1, i, START - timedelta(days=3)) for i in range(1003)
        ]
        connection.executemany("INSERT INTO started_rows VALUES (?, ?, ?)", starts)
    axes = (ref.dimension("sales.customers.region"),)
    current = journeys().funnel(axes=axes).execute()
    baseline = journeys(START - timedelta(days=3)).funnel(axes=axes).execute()
    logical = current.compare(baseline)
    result = logical.execute()
    digest = result.evidence_digest()
    assert digest.finding_count == 1000
    retained = next(p.table for p in result._dataset.verified().parts if p.role == "finding_policy")
    policy = POLICY.validate_json(retained["finding_policy__retained"][0].as_py())
    assert (policy.eligible, policy.emitted, policy.truncated) == (1003, 1000, 3)
    page = result.findings(limit=100)
    assert len(page.items) == 100 and page.has_more
    assert all(item.value.loss_rate_delta == 0.0 for item in page.items)
    assert session.artifact(result.state.artifact_ref).evidence_digest() == digest
    (tmp_path / "funnel-recovery.json").write_text(
        json.dumps(
            {
                "session": session.id,
                "comparison": result.state.artifact_ref.ref,
                "graph": freeze_graph(logical._node.root),
            }
        )
    )
    for phase in ("cap-continue", "cap-recover"):
        recovered = subprocess.run(
            [sys.executable, "-m", "tests.analysis.journey.funnel_worker", str(tmp_path), phase],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert recovered.returncode == 0, recovered.stdout + recovered.stderr


@pytest.mark.runtime
@pytest.mark.parametrize("fault", ["extractor", "cancel", "deadline"])
def test_extractor_cancel_and_deadline_are_atomic(funnel_public, monkeypatch, fault):
    from marivo.analysis.materialization import graph_findings
    from marivo.analysis.materialization.errors import MaterializationError
    from marivo.analysis.materialization.execute_deadline import CURRENT, ExecuteDeadline

    session, journeys, _, _ = funnel_public
    original = graph_findings.extract

    def extract(*args, **kwargs):
        raise RuntimeError("injected extractor failure")

    def inject(point):
        if point == "insert_findings":
            if fault == "cancel":
                raise KeyboardInterrupt("injected cancellation")
            CURRENT.set(ExecuteDeadline(0, clock=lambda: 601))

    if fault == "extractor":
        monkeypatch.setattr(graph_findings, "extract", extract)
    else:
        session._runtime._hook = inject
    with pytest.raises((MaterializationError, KeyboardInterrupt, DomainPreparationError)):
        journeys().funnel().compare(journeys(START - timedelta(days=3)).funnel()).execute()
    monkeypatch.setattr(graph_findings, "extract", original)
    with session._runtime.store._read() as connection:
        assert connection.execute("SELECT COUNT(*) FROM dataset_artifacts").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM findings").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM dataset_evidence").fetchone()[0] == 0


@pytest.mark.runtime
@pytest.mark.parametrize("funnel_public", ["table", "parquet"], indirect=True)
def test_source_reevaluation_fixed_preservation_and_stage_trace(funnel_public, monkeypatch):
    import duckdb
    import pyarrow.parquet as pq

    from marivo.analysis.materialization import funnel_execution, journey_execution
    from marivo.datasource.adapters import SourceSession

    session, journeys, _, database = funnel_public
    trace = []
    original_read = SourceSession.batches
    original_journey = journey_execution.execute
    original_funnel = funnel_execution.execute

    def read(*args, **kwargs):
        trace.append("source")
        return original_read(*args, **kwargs)

    def journey(*args, **kwargs):
        trace.append("assignment")
        return original_journey(*args, **kwargs)

    def funnel(*args, **kwargs):
        trace.append("funnel")
        return original_funnel(*args, **kwargs)

    monkeypatch.setattr(SourceSession, "batches", read)
    monkeypatch.setattr(journey_execution, "execute", journey)
    monkeypatch.setattr(funnel_execution, "execute", funnel)
    logical = journeys().funnel()
    fixed = logical.execute()
    assert fixed.to_pandas().lost_count.tolist() == [0, 1]
    assert (
        max(i for i, stage in enumerate(trace) if stage == "source")
        < trace.index("assignment")
        < trace.index("funnel")
    )
    with duckdb.connect(str(database)) as connection:
        connection.execute("DELETE FROM finished_rows")
        path = database.parent / "finished_rows.parquet"
        if path.exists():
            pq.write_table(connection.table("finished_rows").to_arrow_table(), path)
    trace.clear()
    changed = logical.execute()
    assert changed.to_pandas().lost_count.tolist() == [0, 2]
    assert changed.state.artifact_ref != fixed.state.artifact_ref

    def forbidden(*args, **kwargs):
        raise AssertionError("fixed continuation opened a source or rematched")

    monkeypatch.setattr(SourceSession, "__init__", forbidden)
    monkeypatch.setattr(duckdb, "connect", forbidden)
    monkeypatch.setattr(journey_execution, "execute", forbidden)
    assert session.artifact(fixed.state.artifact_ref).to_pandas().lost_count.tolist() == [0, 1]
    assert fixed.read(fixed.lost_count).execute().to_pandas().value.tolist() == [0, 1]


@pytest.mark.runtime
def test_funnel_exact_period_and_matching_rejections(funnel_public):
    from dataclasses import replace

    import marivo.analysis as mv
    from marivo.analysis.core.funnel_rules import compatible
    from marivo.analysis.core.model import FunnelPart
    from marivo.analysis.errors import AnalysisError
    from marivo.refs import ref

    session, journeys, pattern, _ = funnel_public
    current = journeys().funnel()
    part = next(p for p in current._node.root.signature.parts if isinstance(p, FunnelPart))
    for wrong in (
        replace(part, population_id="foreign"),
        replace(part, complete=False),
        replace(
            part,
            journey=replace(
                part.journey, completion_through=(THROUGH + timedelta(days=1)).isoformat()
            ),
        ),
        replace(part, journey=replace(part.journey, steps=("start", "different"))),
    ):
        with pytest.raises(AnalysisError):
            compatible(part, wrong)
    original = journeys()
    # The public receiver rejects every_start before execution.
    from marivo.analysis.core.graph import method_node

    root = original._node.root
    repeated = replace(
        original._node,
        root=method_node(
            root.inputs, replace(root.parameters, policy="shared"), value_type=root.value_type
        ),
    )
    with pytest.raises(AnalysisError):
        mv.LogicalJourneyResult(
            __import__("marivo.analysis.public_dsl", fromlist=["_TOKEN"])._TOKEN,
            repeated,
            session._runtime,
        ).funnel()
    fixed = current.execute()
    change = fixed.compare(journeys(START - timedelta(days=3)).funnel().execute()).execute()
    with pytest.raises(AnalysisError, match="retained"):
        change.attribute(
            target=mv.funnel_loss_rate(step=pattern.steps[1]),
            axes=(ref.dimension("sales.customers.region"),),
        )


@pytest.mark.runtime
def test_owned_read_exact_step_and_rank_projection(funnel_public):
    from dataclasses import replace

    import marivo.analysis as mv
    from marivo.analysis.errors import AnalysisError
    from marivo.analysis.materialization.graph_exchange import from_arrow

    _, journeys, pattern, _ = funnel_public
    fixed = journeys().funnel().execute()
    all_loss = fixed.read(fixed.loss_rate_from_previous).execute()._dataset.verified()
    selected = fixed.read(mv.funnel_loss_rate(step=pattern.steps[1])).execute()._dataset.verified()
    with pytest.raises(AnalysisError):
        from_arrow(
            all_loss.primary,
            replace(selected.contract, schema=all_loss.primary.schema),
            parts=selected.parts,
            method_state=None,
        )
    with pytest.raises(AnalysisError):
        from_arrow(
            selected.primary.slice(0, 0), selected.contract, parts=selected.parts, method_state=None
        )
    ranked = fixed.read(fixed.lost_count).rank(order="descending", ties="ordinal").execute()
    table = mv.table(lost=ranked.values, rank=ranked.ranks).execute().to_pandas()
    assert dict(zip(table.lost, table["rank"], strict=True)) == {1: 1, 0: 2}


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


@pytest.mark.runtime
def test_latest_funnel_example_executes(funnel_public):

    import marivo.analysis as mv
    import marivo.semantic as ms

    _, journeys, pattern, _ = funnel_public
    from tests.support.documentation import _example

    examples = [_example(locale, "funnel-attribution") for locale in ("en", "zh")]
    assert len(set(examples)) == 1
    namespace = {
        "mv": mv,
        "ms": ms,
        "journeys": journeys(),
        "baseline_journeys": journeys(START - timedelta(days=3)),
        "finish_step": pattern.steps[1],
    }
    exec(compile(examples[0], "latest-funnel-example", "exec"), namespace)
    assert namespace["allocation"].evidence_digest().finding_count == 4


@pytest.mark.runtime
def test_zero_policy_rejects_an_injected_nonempty_collection(funnel_public):
    from marivo.analysis.materialization.errors import MaterializationError

    session, journeys, _, _ = funnel_public
    zero = journeys().funnel().execute()
    compared = journeys().funnel().compare(journeys(START - timedelta(days=3)).funnel()).execute()
    with session._runtime.store._write() as connection:
        connection.execute(
            "UPDATE findings SET artifact_ref=? WHERE artifact_ref=?",
            (zero.state.artifact_ref.ref, compared.state.artifact_ref.ref),
        )
    with pytest.raises(MaterializationError):
        session.artifact(zero.state.artifact_ref)
    with pytest.raises(MaterializationError):
        zero.findings()


@pytest.mark.runtime
def test_no_eligible_rows_preserves_frozen_extractor_policy(funnel_public):
    import duckdb

    from marivo.analysis.materialization.graph_findings import POLICY

    session, journeys, _, database = funnel_public
    with duckdb.connect(str(database)) as connection:
        connection.execute("DELETE FROM started_rows")
    result = journeys().funnel().compare(journeys(START - timedelta(days=3)).funnel()).execute()
    digest = result.evidence_digest()
    assert digest.finding_count == 0
    assert digest.extractor_contract_versions == ("graph.funnel_delta_findings@v1",)
    retained = next(
        part.table for part in result._dataset.verified().parts if part.role == "finding_policy"
    )
    policy = POLICY.validate_json(retained["finding_policy__retained"][0].as_py())
    assert (policy.eligible, policy.emitted, policy.truncated) == (0, 0, 0)
    assert not result.findings().items
    assert session.artifact(result.state.artifact_ref).evidence_digest() == digest
